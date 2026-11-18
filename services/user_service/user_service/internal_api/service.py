"""Read-only user, membership and direct-message authorization."""
# 内部只读校验服务，负责用户、群成员、私聊权限查询
from infinitechat.contracts.enums import UserState
# 用户账号状态枚举
from infinitechat.contracts.http import UserStatus

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
# AsyncSession：异步数据库会话，用来写SQL、查数据
# async_sessionmaker：会话工厂，专门用来新建数据库会话
from user_service.storage.repositories import UserRepository
# 用户仓储，封装用户表的查询逻辑

# 数字状态码对应的中文文字，给前端展示用
_STATE_NAMES: dict[int, str] = {
    UserState.NORMAL: "正常",
    UserState.BANNED: "封禁",
    UserState.CANCELLED: "注销",
}

# 内部校验服务，给网关、消息服务调用，做前置鉴权
class InternalValidationService:
    # 注入会话工厂，不提前占数据库连接
    def __init__(
        self,
        *,
        # sync_sessionmaker[AsyncSession]是 **SQLAlchemy 异步版本的会话工厂**
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        # 存好这个工厂，后面查库的时候拿它生成会话
        self._sessions = session_factory

    # 查询用户账号状态，包装成契约模型返回给调用方
    async def user_status(self, user_id: int) -> UserStatus:
        # 用工厂生成一个数据库会话，async with 结束自动释放连接
        async with self._sessions() as session:
            # 实例化用户仓储，把当前会话传给它
            repo = UserRepository(session)
            # 根据用户id去数据库查用户记录
            user = await repo.get_by_id(user_id)
            # 没查到用户，或者用户已经逻辑删除，返回不存在
            if user is None or user.is_delete:
                return UserStatus(user_id=user_id, state=None, state_name="用户不存在")
            # 查到用户，把数据库里存的数字转成枚举，配上中文状态名
            return UserStatus(
                user_id=user_id,
                state=UserState(user.state),
                state_name=_STATE_NAMES.get(user.state, "未知"),
            )