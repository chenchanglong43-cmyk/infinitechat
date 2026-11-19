"""Registration, login, refresh and logout application service."""

# Chinese punctuation is part of the client-visible messages.
# ruff: noqa: RUF001

import secrets  # 安全随机数
from datetime import timedelta
from typing import Protocol

from infinitechat.contracts import ErrorCode
from infinitechat.contracts.enums import UserState
from infinitechat.contracts.http import (
    CodeLoginRequest,
    LoginSessionResponse,
    PasswordLoginRequest,
    TokenResponse,
    UserRegisterRequest,
)
from infinitechat.foundation.ids import SnowflakeGenerator

from infinitechat.foundation.security import JwtCodec, TokenInvalidError, TokenType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from user_service.auth.passwords import PasswordManager
from user_service.config import UserServiceSettings
from user_service.errors import BusinessError

from user_service.integrations.redis import UserRedis
from user_service.storage.models import UserModel

from user_service.storage.repositories import UserRepository

# 邮件发送协议
# 邮件发送协议 (协议: 只约定要有哪些方法)
class EmailSender(Protocol):
    # 发送验证码
    async def send_captcha(self, target_email: str, code: str) -> None: ...

# 实时地址定位协议
# 实时地址定位协议 (协议: 只约定要有哪些方法)
class RealtimeEndpointLocator(Protocol):
    # 定位实时地址
    async def locate(self, routing_key: str) -> str: ...


# 认证服务，封装注册、登录、发验证码这类认证相关业务逻辑
class AuthService:
    # 构造方法：一次性注入所有需要的依赖，全部用关键字传参，防止顺序传错
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],  # 数据库会话工厂，用来创建db会话
        cache: UserRedis,                                   # Redis缓存工具，存验证码、限流等
        email_sender: EmailSender,                          # 邮件发送工具，用来发验证码邮件
        realtime_locator: RealtimeEndpointLocator,          # 实时服务定位器（即时通讯相关）
        password_manager: PasswordManager,                  # 密码管理器，argon2/MD5兼容、密码校验
        jwt_codec: JwtCodec,                                # JWT工具，生成、解析登录token
        id_generator: SnowflakeGenerator,                   # 雪花ID生成器，生成用户唯一id
        settings: UserServiceSettings,                     # 项目配置对象
    ) -> None:
        self._sessions = session_factory
        self._cache = cache
        self._email = email_sender
        self._realtime = realtime_locator
        self._passwords = password_manager
        self._jwt = jwt_codec
        self._ids = id_generator
        self._settings = settings

    # 发送邮箱验证码
    async def send_captcha(self, target_email: str) -> str:
        # 邮箱统一转小写，避免大小写当成两个不同邮箱
        target_email = target_email.lower()
        # 随机生成6位数字验证码，不足6位前面补0
        code = f"{secrets.randbelow(1_000_000):06d}"
        # 先在Redis预留验证码（一般用来做限流、防重复发送），预留失败直接抛异常
        if not await self._cache.reserve_captcha(target_email, code):
            raise BusinessError(ErrorCode.LOGIN_ERROR_CODE)
        try:
            # 调用邮件工具，把验证码发给用户邮箱
            await self._email.send_captcha(target_email, code)
        except Exception as error:
            # 发邮件报错了，把刚才Redis里预留的验证码释放掉，避免占用缓存
            await self._cache.release_captcha(target_email, code)
            # 抛出业务异常，from error 保留原始异常链，方便日志排查
            raise BusinessError(ErrorCode.OPERATION_ERROR, "验证码发送失败") from error
        # 一切正常，返回成功提示
        return "发送邮件成功"

    # 用户注册
    async def register(self, request: UserRegisterRequest) -> LoginSessionResponse:
        # 邮箱统一转小写，避免大小写不同被当成两个账号
        email = str(request.email).lower()
        # 校验并消耗验证码，验证码只能用一次；校验失败抛验证码错误
        if not await self._cache.consume_captcha(email, request.code):
            raise BusinessError(ErrorCode.LOGIN_ERROR_CODE)
        # 获取数据库会话 + 开启事务，块内抛出异常就自动回滚，防止脏数据
        async with self._sessions() as session, session.begin():
            # 实例化用户仓储，用来操作用户表
            users = UserRepository(session)
            # 根据邮箱查用户，for_update=True 上排他行锁，防止并发重复注册
            if await users.get_by_email(email, for_update=True) is not None:
                raise BusinessError(ErrorCode.USER_ALREADY_EXISTS)
            # 雪花ID生成器，生成全局唯一用户ID
            user_id = self._ids.next_id()
            # 组装用户ORM模型对象
            user = UserModel(
                user_id=user_id,
                email=email,
                # 密码做Argon2哈希，数据库只存哈希，不存明文
                password=self._passwords.hash_password(request.password),
                # 昵称：前端传了就用，没传就拿邮箱@前面一截当昵称
                nickname=request.nickname or email.split("@", maxsplit=1)[0],
                gender=2,        # 2 = 性别未知
                state=UserState.NORMAL, # 用户状态：正常
                role=0,          # 0 = 普通用户
                is_delete=0,     # 逻辑删除标记：0代表未删除
            )
            users.add(user)  # 把用户对象加入会话缓存，不是立刻入库，事务提交才写入
        # 注册入库完成，生成登录会话，返回登录信息给前端
        return await self._create_login_session(user)

    # 密码登录
    async def login_password(self, request: PasswordLoginRequest) -> LoginSessionResponse:
        # 打开数据库会话并开启事务，里面发生异常会自动回滚
        async with self._sessions() as session, session.begin():
            # 邮箱转小写查询用户，for_update=True 加上行锁，允许更新密码哈希
            user = await UserRepository(session).get_by_email(
                str(request.email).lower(),
                for_update=True,
            )
            # 没有查到这个邮箱的用户
            if user is None:
                # 执行假的密码校验，对齐耗时，防止黑客枚举邮箱
                self._passwords.consume_dummy_verification(request.password)
                raise BusinessError(ErrorCode.LOGIN_ERROR)
            # 检查账号状态，确认账号没有被封禁、没有被删除
            self._ensure_active(user)
            # 校验密码，自动识别是Argon2还是老MD5，同时判断是否需要升级哈希
            verification = self._passwords.verify(user.password, request.password)
            # 密码不对，直接抛出登录错误
            if not verification.valid:
                raise BusinessError(ErrorCode.LOGIN_ERROR)
            # 如果算出了新哈希（老MD5升级或者argon2参数更新），赋值给用户模型，事务提交时更新到库
            if verification.upgraded_hash:
                user.password = verification.upgraded_hash
        # 登录校验全部通过，生成登录会话返回给前端
        return await self._create_login_session(user)

    # 验证码登录
    async def login_code(self, request: CodeLoginRequest) -> LoginSessionResponse:
        # 邮箱统一转小写
        email = str(request.email).lower()
        # 消耗验证码，验证码一次性失效；校验失败抛验证码错误
        if not await self._cache.consume_captcha(email, request.code):
            raise BusinessError(ErrorCode.LOGIN_ERROR_CODE)
        # 打开数据库会话，只做查询，不需要事务和行锁
        async with self._sessions() as session:
            # 根据邮箱查询用户
            user = await UserRepository(session).get_by_email(email)
        # 用户不存在，登录失败
        if user is None:
            raise BusinessError(ErrorCode.LOGIN_ERROR)
        # 检查账号状态是否正常可用
        self._ensure_active(user)
        # 验证码校验通过，生成登录会话返回
        return await self._create_login_session(user)

    # 校验访问令牌
    async def authenticate_access_token(self, token: str | None) -> int:
        try:
            # 解码JWT，校验签名、过期时间
            claims = self._jwt.decode(token)
        except TokenInvalidError as error:
            # 签名不对、过期、格式错 → 令牌非法
            raise BusinessError(ErrorCode.TOKEN_INVALID) from error
        except ValueError as error:
            # token为空/解析失败 → 未登录
            raise BusinessError(ErrorCode.NOT_LOGIN_ERROR) from error
        # 校验令牌类型：必须是 access 令牌，不能拿刷新token当访问token用
        if claims.token_type not in {None, TokenType.ACCESS}:
            raise BusinessError(ErrorCode.TOKEN_INVALID)
        # 从 sub 字段取出 user_id
        user_id = self._subject_user_id(claims.sub)
        # 恒定时间比对，防时序攻击
        # JWT解码合法后，还要和Redis保存的当前有效access_token比对
        # 实现踢下线、单点登录，弥补原生JWT不可撤销的短板
        if not secrets.compare_digest(
                token or "",
                await self._cache.current_access_token(user_id) or "",
        ):
            raise BusinessError(ErrorCode.NOT_LOGIN_ERROR)
        # 校验全部通过，返回用户ID给上层业务
        return user_id

    # 退出登录
    async def logout(self, user_id: int) -> bool:
        # Redis清除该用户绑定的access + refresh令牌
        # 旧token就算没到过期时间，后续缓存比对也会失败，直接失效
        await self._cache.revoke_tokens(user_id)
        return True

    # 刷新令牌：用refresh_token换新access_token，同时换新refresh_token（滚动刷新）
    async def refresh(self, refresh_token: str | None) -> TokenResponse:
        # 先判空，refresh_token不能为空
        if refresh_token is None or not refresh_token.strip():
            raise BusinessError(ErrorCode.PARAMS_ERROR)
        try:
            claims = self._jwt.decode(refresh_token)
        except ValueError as error:
            raise BusinessError(ErrorCode.TOKEN_INVALID, "凭证已失效，请重新登录") from error
        # 校验类型：必须是刷新令牌
        if claims.token_type not in {None, TokenType.REFRESH}:
            raise BusinessError(ErrorCode.TOKEN_INVALID)
        user_id = self._subject_user_id(claims.sub)
        # 从Redis拿到当前生效的refresh_token
        current = await self._cache.current_refresh_token(user_id)
        # 比对不一致：别处登录、已经退出、刷新token已经滚动换新
        if not secrets.compare_digest(refresh_token or "", current or ""):
            raise BusinessError(ErrorCode.TOKEN_INVALID, "凭证已过期或在其他地方登录")
        # 签发一对新令牌：access + 新refresh
        access_token, new_refresh_token = await self._issue_tokens(user_id)
        return TokenResponse(access_token=access_token, refresh_token=new_refresh_token)

    # 刷新实时地址：给IM/消息推送，返回用户当前连接的实时服务节点地址
    async def refresh_uri(self, user_id: int) -> str:
        return await self._realtime.locate(str(user_id))

    # 创建登录会话，拼装登录接口返回给前端的所有数据
    async def _create_login_session(self, user: UserModel) -> LoginSessionResponse:
        # 调用签发令牌方法，拿到access和refresh令牌
        access_token, refresh_token = await self._issue_tokens(user.user_id)
        try:
            # 查一下用户应该连哪个实时消息服务节点
            netty_uri = await self._realtime.locate(str(user.user_id))
        except LookupError:
            # 找不到节点也没关系，不卡住登录，前端就不连实时服务
            netty_uri = None
        return LoginSessionResponse(
            user_id=user.user_id,
            email=user.email,
            nickname=user.nickname,
            avatar=user.avatar,
            gender=user.gender,
            description=user.description,
            access_token=access_token,
            refresh_token=refresh_token,
            netty_uri=netty_uri,
            # 拿到上次离线时间，前端可以用来判断离线消息
            offline_time=await self._cache.take_offline_time(user.user_id),
        )

    # 生成一对令牌，同时丢进Redis存起来
    async def _issue_tokens(self, user_id: int) -> tuple[str, str]:
        # 生成访问令牌，短有效期，用来正常请求接口
        access_token = self._jwt.encode(
            subject=str(user_id),
            # 从配置里读取访问令牌多久过期
            expires_in=timedelta(seconds=self._settings.access_token_ttl_seconds),
            token_type=TokenType.ACCESS,
            # jti 是这个令牌的唯一编号，方便排查日志
            additional_claims={"jti": secrets.token_hex(16)},
        )
        # 生成刷新令牌，有效期更长，用来换新access_token
        refresh_token = self._jwt.encode(
            subject=str(user_id),
            # 刷新令牌过期时间，从配置读取
            expires_in=timedelta(seconds=self._settings.refresh_token_ttl_seconds),
            token_type=TokenType.REFRESH,
            additional_claims={"jti": secrets.token_hex(16)},
        )
        # 把新令牌存Redis，直接覆盖旧的，实现单点登录踢下线
        await self._cache.store_tokens(
            user_id,
            access_token=access_token,
            refresh_token=refresh_token,
            access_ttl_seconds=self._settings.access_token_ttl_seconds,
            refresh_ttl_seconds=self._settings.refresh_token_ttl_seconds,
        )
        return access_token, refresh_token

    @staticmethod
    # 把JWT里sub字段的字符串转回数字user_id
    def _subject_user_id(subject: str) -> int:
        try:
            return int(subject)
        except ValueError as error:
            # 转数字失败，说明token内容不对，直接判定非法
            raise BusinessError(ErrorCode.TOKEN_INVALID) from error

    @staticmethod
    # 检查账号状态，被封禁或者已经删除就不让登录
    def _ensure_active(user: UserModel) -> None:
        if user.state != UserState.NORMAL or user.is_delete != 0:
            raise BusinessError(ErrorCode.FORBIDDEN_ERROR)


