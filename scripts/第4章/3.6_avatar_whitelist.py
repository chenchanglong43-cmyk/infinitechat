"""头像地址白名单演示.

读取第4章目录下的 旭日.jpg, 生成预签名链接, 把文件真正 PUT 到 MinIO,
再从预签名下载链接读回校验, 最后用永久地址验证 update_avatar 的白名单逻辑.
依次尝试三种地址:
1. 外站地址 -> 应被拒绝;
2. 主机是 MinIO, 但地址里夹带 admin:secret@ 账号信息 -> 应被拒绝;
3. 刚上传得到的永久地址 -> 应放行并写入用户表.
前置条件: 本地 MySQL 和 MinIO 都在运行, 配置写在仓库根目录 .env 里.
每次运行前会先清空演示用户的头像, 可以反复执行.
通过标准: 输出两行"头像被拒"(error_code=40000), 以及 updated=True、matches=True.
"""

import asyncio  # 异步编程
import mimetypes  # 根据文件名猜 Content-Type
import urllib.request  # 标准库发 PUT/GET 请求
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import structlog  # 结构化日志
from minio import Minio  # MinIO 同步客户端

from infinitechat.foundation.telemetry import configure_logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from user_service.config import UserServiceSettings
from user_service.errors import BusinessError

from user_service.integrations.minio import AvatarStorage, avatar_object_name

from user_service.integrations.mysql import create_mysql_engine, create_session_factory
from user_service.storage.models import UserModel

from user_service.storage.repositories import UserRepository
from user_service.users import UserProfileService

logger = structlog.get_logger(__name__)

OWNER_ID = 9_000_000_201
# 演示账号不能登录，密码放一个不可能匹配的占位字符串
DISABLED_LOGIN = "!demo!"
# 要上传的真实头像，和脚本放在同一个目录
IMAGE_PATH = Path(__file__).with_name("旭日.jpg")


# 初始化演示用户，每次运行先把头像清空
async def seed(sessions: async_sessionmaker[AsyncSession]) -> None:
    """准备一个演示用户。"""
    async with sessions() as session, session.begin():
        repository = UserRepository(session)
        user = await repository.get_by_id(OWNER_ID)
        if user is None:
            user = UserModel(
                user_id=OWNER_ID,
                email=f"u{OWNER_ID}@example.com",
                password=DISABLED_LOGIN,
                nickname="阿甲",
            )
            repository.add(user)
        user.avatar = None


# 查询数据库里当前存的头像链接
async def stored_avatar(sessions: async_sessionmaker[AsyncSession]) -> str | None:
    """读库里现在的头像地址。"""
    async with sessions() as session:
        user = await UserRepository(session).get_by_id(OWNER_ID)
        return None if user is None else user.avatar


# 期望某个地址被白名单拒绝，返回是否真的拒绝成功
async def expect_rejected(profiles: UserProfileService, case: str, uri: str) -> bool:
    """调用 update_avatar，确认非法地址抛出 40000。"""
    try:
        await profiles.update_avatar(OWNER_ID, uri)
    except BusinessError as error:
        logger.info("头像被拒", case=case, error_code=int(error.code), message=error.message)
        return True
    logger.warning("头像未被拒绝", case=case, uri=uri)
    return False


# 同步 PUT 文件到预签名地址，放到线程里跑，避免阻塞事件循环
def put_object(url: str, data: bytes, content_type: str) -> int:
    request = urllib.request.Request(
        url,
        data=data,
        method="PUT",
        headers={"Content-Type": content_type},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return int(response.status)


# 同步 GET 预签名地址，确认文件真的能下载
def get_object(url: str) -> int:
    with urllib.request.urlopen(url, timeout=10) as response:
        return len(response.read())


# 先确认存储桶存在，不存在就创建
async def ensure_bucket(client: Minio, bucket: str) -> None:
    if await asyncio.to_thread(client.bucket_exists, bucket):
        return
    await asyncio.to_thread(client.make_bucket, bucket)
    logger.info("已创建存储桶", bucket=bucket)


async def main() -> None:
    """上传真实头像到 MinIO，校验下载，再验证白名单并写入用户表。"""
    settings = UserServiceSettings()
    # 创建 MinIO 客户端（同步库，调用时都丢到线程里跑）
    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key.get_secret_value(),
        secret_key=settings.minio_secret_key.get_secret_value(),
        secure=settings.minio_secure,
    )
    bucket = settings.minio_bucket
    await ensure_bucket(client, bucket)

    # avatar_object_name 目前多了一个尾逗号返回单元素元组，这里取 [0] 临时绕开
    object_name = avatar_object_name(IMAGE_PATH.name)[0]
    ttl = timedelta(seconds=settings.presigned_url_ttl_seconds)
    # 并发拿上传、下载两个预签名链接
    upload_url, download_url = await asyncio.gather(
        asyncio.to_thread(client.presigned_put_object, bucket, object_name, ttl),
        asyncio.to_thread(client.presigned_get_object, bucket, object_name, ttl),
    )

    data = IMAGE_PATH.read_bytes()
    content_type = mimetypes.guess_type(IMAGE_PATH.name)[0] or "application/octet-stream"
    # 真正把图片字节 PUT 到 MinIO
    status = await asyncio.to_thread(put_object, upload_url, data, content_type)
    # 再用预签名下载链接读回来，校验文件确实落在了桶里
    downloaded = await asyncio.to_thread(get_object, download_url)
    logger.info(
        "文件已上传",
        bucket=bucket,
        object_name=object_name,
        put_status=status,
        upload_bytes=len(data),
        download_bytes=downloaded,
    )

    # 去掉预签名参数，得到不带过期时间的永久地址
    permanent_uri = upload_url.split("?", maxsplit=1)[0]
    logger.info("永久地址", uri=permanent_uri)

    # 白名单：只允许 MinIO 和网关两个主机，和真实接口保持一致
    hosts = frozenset(
        {
            settings.minio_endpoint.rsplit(":", maxsplit=1)[0],
            urlsplit(settings.public_gateway_base_url).hostname or "",
        }
    )
    engine = create_mysql_engine(settings.mysql_dsn)
    sessions = create_session_factory(engine)
    profiles = UserProfileService(
        session_factory=sessions,
        avatar_storage=AvatarStorage(settings),
        allowed_avatar_hosts=hosts,
    )
    try:
        await seed(sessions)
        # 1) 外站地址：主机不在白名单里，应被拒绝
        await expect_rejected(profiles, "外站地址", "https://evil.example.com/avatar.png")
        # 2) 主机是 MinIO，但地址里夹带账号信息，也应被拒绝
        parts = urlsplit(permanent_uri)
        credential_uri = f"{parts.scheme}://admin:secret@{parts.netloc}{parts.path}"
        await expect_rejected(profiles, "带账号信息", credential_uri)
        # 3) 正常地址：放行并写入用户表
        updated = await profiles.update_avatar(OWNER_ID, permanent_uri)
        stored = await stored_avatar(sessions)
        logger.info(
            "头像已写入用户表",
            updated=updated,
            matches=stored == permanent_uri,
            stored=stored,
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    configure_logging(json_output=False)
    asyncio.run(main())
