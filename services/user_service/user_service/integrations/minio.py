"""Avatar object storage adapter with server-generated object keys."""

import asyncio  # 异步相关
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from infinitechat.contracts.http import UploadUrlResponse
from minio import Minio

from user_service.config import UserServiceSettings

# 允许的头像后缀，只认这几种图片格式
_ALLOWED_AVATAR_SUFFIXES = frozenset({".gif", ".jpeg", ".jpg", ".png", ".webp"})

# 生成头像在MinIO里的对象路径
def avatar_object_name(file_name: str) -> str:
    # 拿到文件后缀，统一转小写
    # suffix就是**文件后缀名（扩展名）**，比如avatar.png里的 .png。
    suffix = Path(file_name).suffix.lower()
    # 防止路径穿越，同时校验后缀是不是支持的图片
    if Path(file_name).name != file_name or suffix not in _ALLOWED_AVATAR_SUFFIXES:
        # 文件名带路径或者格式不对，直接报错
        raise ValueError("头像文件名或扩展名不受支持")
    # 放到avatars目录下，用uuid避免重名，保留原后缀
    return f"avatars/{uuid4().hex}{suffix}",


# 头像存储操作类，专门把MinIO上传、拿链接的逻辑打包放这里
class AvatarStorage:
    # 初始化方法：读取配置，创建好MinIO连接客户端
    def __init__(self, settings: UserServiceSettings) -> None:
        # 头像要存到哪个桶，从配置里拿
        self._bucket = settings.minio_bucket
        # 预签名链接多久过期，转成timedelta时间对象方便MinIO用
        self._ttl = timedelta(seconds=settings.presigned_url_ttl_seconds)
        # 创建MinIO客户端实例，后面所有操作都靠这个对象
        self._client = Minio(
            # MinIO服务地址
            settings.minio_endpoint,
            # 访问密钥，get_secret_value是把Pydantic SecretStr取出明文
            access_key=settings.minio_access_key.get_secret_value(),
            # 密钥
            secret_key=settings.minio_secret_key.get_secret_value(),
            # 是否开启HTTPS
            secure=settings.minio_secure,
        )

    # 给前端生成预签名上传链接 + 预签名下载链接
    async def create_upload_urls(self, file_name: str) -> UploadUrlResponse:
        # 调用工具函数，生成minio里的对象路径；里面会校验文件后缀、防止路径穿越攻击
        object_name = avatar_object_name(file_name)
        # 重点：MinIO原生的方法是同步阻塞的，直接在异步函数里调用会卡事件循环
        # asyncio.to_thread 作用：把同步函数扔到后台线程去跑，不卡住异步主循环
        # asyncio.gather：并发同时执行两个任务，一起拿上传链接、下载链接，不用等一个跑完再跑另一个
        upload_url, download_url = await asyncio.gather(
            # 生成PUT上传的预签名链接，前端拿着这个url直接PUT文件到MinIO
            asyncio.to_thread(
                self._client.presigned_put_object,
                self._bucket,
                object_name,
                self._ttl,
            ),
            # 生成GET下载的预签名链接，上传成功后可以用这个链接预览头像
            asyncio.to_thread(
                self._client.presigned_get_object,
                self._bucket,
                object_name,
                self._ttl,
            ),
        )
        # 把两个链接打包成响应对象返回给接口，再给到前端
        return UploadUrlResponse(upload_url=upload_url, download_url=download_url)