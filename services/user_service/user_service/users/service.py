"""Avatar and current user/session query operations."""

from typing import Protocol
from urllib.parse import urlsplit

from infinitechat.contracts import ErrorCode
from infinitechat.contracts.http import (
    UploadUrlResponse,
    UserInfo,
)

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from user_service.errors import BusinessError

from user_service.storage.repositories import UserRepository

# 上传链接协议，相当于定一个规矩
# 只要实现了create_upload_urls这个方法，就符合这个协议
# 最大好处：单元测试的时候可以传个假对象，不用真的连MinIO，测起来省事
class UploadUrlProvider(Protocol):
    # 给前端生成头像上传链接 + 预览下载链接
    async def create_upload_urls(self, file_name: str) -> UploadUrlResponse: ...

# 用户资料服务，专门处理头像、个人信息相关的业务逻辑
# 业务逻辑放这里，数据库操作交给仓储，文件存储交给AvatarStorage，各司其职
class UserProfileService:
    # 初始化，所有依赖都从外面传进来，不在里面自己实例化
    def __init__(
        self,
        *,  # 这个星号意思：后面所有参数只能用关键字传参，不能按位置传，避免传参顺序搞错踩坑
        session_factory: async_sessionmaker[AsyncSession],
        avatar_storage: UploadUrlProvider,
        allowed_avatar_hosts: frozenset[str],
    ) -> None:
        self._sessions = session_factory  # 会话工厂，想操作数据库的时候，用它新建一个db会话
        self._avatars = avatar_storage  # 头像存储工具，就是咱们前面写的MinIO存储类，只认协议，不认具体实现
        self._allowed_avatar_hosts = allowed_avatar_hosts  # 头像域名白名单，用来校验头像地址
        # 防止用户随便填别的网站外链头像，防止盗链、恶意跳转之类的安全问题


    # 获取头像预签名上传链接，给前端用来直传MinIO
    async def upload_urls(self, file_name: str) -> UploadUrlResponse:
        try:
            # 调用头像存储模块，一口气拿到上传链接和预览链接
            return await self._avatars.create_upload_urls(file_name)
        except ValueError as error:
            # 走到这里一般是文件名后缀非法、路径穿越这类校验没通过
            # 包装成我们项目统一的业务异常，给前端返回标准错误码
            raise BusinessError(ErrorCode.PARAMS_ERROR, str(error)) from error

    # 更新用户头像地址，把校验好的头像链接存到用户表里
    async def update_avatar(self, user_id: int, uri: str | None) -> bool:
        # 先做校验：链接是空的 或者 域名不在我们的白名单里，直接报错
        if not uri or not self._valid_avatar_uri(uri):
            raise BusinessError(ErrorCode.PARAMS_ERROR, "头像URI无效")
        # 新开一个数据库会话，并且开启事务；中途抛异常会自动回滚，不会脏数据
        async with self._sessions() as session, session.begin():
            # 实例化用户仓储，专门用来操作用户表
            repo = UserRepository(session)
            # 根据用户id查这条用户记录
            user = await repo.get_by_id(user_id)
            if user is None:
                # 找不到这个用户，直接返回False，不继续往下走
                return False
            # 给用户对象的头像字段赋值，只是在session里标记修改
            # 离开这个async with块、事务提交之后，才真正更新到数据库
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
    # 私有方法，专门校验头像链接合不合法，防用户塞恶意外链
    def _valid_avatar_uri(self, uri: str) -> bool:
        # 把链接拆开，拿到协议、域名、路径这些部分
        parsed = urlsplit(uri)
        return (
            # 协议只能是http或者https，拒绝别的奇怪协议
            parsed.scheme in {"http", "https"}
            # 域名不能为空，不能是相对路径那种
            and bool(parsed.netloc)
            # 域名必须在白名单里，只放行咱们自己MinIO的地址
            and parsed.hostname in self._allowed_avatar_hosts
            # 限制链接总长度，防止存超长垃圾数据进库
            and len(uri) <= 512
            # 链接里不能带账号密码，防止把密钥之类的东西存进去泄露
            and parsed.username is None
            and parsed.password is None
        )


