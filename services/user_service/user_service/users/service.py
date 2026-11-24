"""Avatar and current user/session query operations."""
# 导入Protocol，用来做接口协议定义，鸭子类型
from typing import Protocol
# 导入url拆分工具，解析头像链接的域名
from urllib.parse import urlsplit
# 导入错误码常量
from infinitechat.contracts import ErrorCode
# 导入上传链接响应、用户信息返回结构体
from infinitechat.contracts.http import (
    UploadUrlResponse,
    UserInfo,
)
# 导入异步数据库会话、会话工厂
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
# 导入自定义业务异常
from user_service.errors import BusinessError
# 导入会话仓库、用户仓库，用来查数据库
from user_service.storage.repositories import SessionRepository, UserRepository

# 上传地址协议，定义获取上传链接的规范
class UploadUrlProvider(Protocol):
    # 生成文件上传地址，返回上传链接响应
    async def create_upload_urls(self, file_name: str) -> UploadUrlResponse: ...

# 用户资料服务，负责头像上传、查询用户信息
class UserProfileService:
    # 构造方法，注入依赖
    def __init__(
        self,
        *,  # 星号，后面参数只能关键字传参，防止传参顺序搞错
        session_factory: async_sessionmaker[AsyncSession],
        avatar_storage: UploadUrlProvider,
        allowed_avatar_hosts: frozenset[str],
    ) -> None:
        self._sessions = session_factory  # 保存数据库会话工厂
        self._avatars = avatar_storage  # 保存头像存储实现（MinIO）
        self._allowed_avatar_hosts = allowed_avatar_hosts  # 头像白名单域名集合


    # 获取会话内所有成员昵称，返回id到昵称的映射字典
    async def nicknames(self, user_id: int, session_id: int) -> dict[int, str]:
        # 开启数据库会话，代码块结束自动释放连接
        async with self._sessions() as session:
            # 实例化会话仓库
            sessions = SessionRepository(session)
            # 校验当前用户是这个会话的成员，不在里面就无权限
            if await sessions.membership(user_id, session_id) is None:
                raise BusinessError(ErrorCode.NO_AUTH_ERROR)
            # 查询这个会话里全部成员id
            user_ids = await sessions.user_ids(session_id)
            # 根据成员id批量查询用户信息
            users = await UserRepository(session).list_by_ids(user_ids)
            # 组装字典：key是用户id，value是昵称，过滤掉查不到的用户
            return {
                target_id: users[target_id].nickname
                for target_id in user_ids
                if target_id in users
            }

    # 获取会话里全部接收人id列表
    async def receivers(self, user_id: int | None, session_id: int) -> list[int]:
        # 开启数据库会话，用完自动关闭
        async with self._sessions() as session:
            # 实例化会话仓库
            sessions = SessionRepository(session)
            # 如果传了用户id，校验该用户属于这个会话
            if user_id is not None and await sessions.membership(user_id, session_id) is None:
                raise BusinessError(ErrorCode.NO_AUTH_ERROR)
            # 返回会话内所有成员id列表
            return await sessions.user_ids(session_id)

    # 查询当前用户加入的所有会话id
    async def sessions(self, user_id: int) -> list[int]:
        # 开启数据库会话，用完自动关闭
        async with self._sessions() as session:
            # 查询该用户关联的全部会话id并返回
            return await SessionRepository(session).session_ids(user_id)

    # 获取头像预签名上传链接
    async def upload_urls(self, file_name: str) -> UploadUrlResponse:
        try:
            # 调用存储模块生成上传、下载地址
            return await self._avatars.create_upload_urls(file_name)
        except ValueError as error:
            # 文件格式不对等参数问题，转成业务异常抛出
            raise BusinessError(ErrorCode.PARAMS_ERROR, str(error)) from error

    # 更新用户头像地址
    async def update_avatar(self, user_id: int, uri: str | None) -> bool:
        if not uri or not self._valid_avatar_uri(uri):
            # 地址为空或者域名不在白名单，判定无效
            raise BusinessError(ErrorCode.PARAMS_ERROR, "头像URI无效")
        # 开启数据库会话和事务，出错自动回滚
        async with self._sessions() as session, session.begin():
            repo = UserRepository(session)
            user = await repo.get_by_id(user_id)
            if user is None:
                # 用户不存在，直接返回false
                return False
            # 给用户头像字段赋值，事务提交后入库
            user.avatar = uri
        return True

    # 批量获取用户简要信息
    async def batch_infos(self, user_ids: list[int]) -> dict[int, UserInfo]:
        # 限制单次最多查100个，防止一次性查太多压垮数据库
        if len(user_ids) > 100:
            raise BusinessError(ErrorCode.PARAMS_ERROR, "单次查询用户数量不能超过100")
        # 开启数据库会话，会话结束自动关闭
        async with self._sessions() as session:
            users = await UserRepository(session).list_by_ids(user_ids)
            # 转成 UserInfo 简短信息返回，只对外暴露昵称、头像、id
            return {
                user_id: UserInfo(
                    user_id=user.user_id,
                    nickname=user.nickname,
                    avatar=user.avatar,
                )
                for user_id, user in users.items()
            }

    # 校验头像链接是否合法，白名单校验
    def _valid_avatar_uri(self, uri: str) -> bool:
        parsed = urlsplit(uri)
        return (
            # 只能是 http / https
            parsed.scheme in {"http", "https"}
            # 域名不能为空
            and bool(parsed.netloc)
            # 域名必须在我们的白名单里面，只允许自己的MinIO地址
            and parsed.hostname in self._allowed_avatar_hosts
            # 链接长度限制，防止超长脏数据
            and len(uri) <= 512
            # 链接里面不能带账号密码，避免泄露
            and parsed.username is None
            and parsed.password is None
        )
