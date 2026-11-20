"""同意好友申请演示.

好友关系, 单聊会话, 通知事件在同一个事务里写入: 中途出错什么都不留下;
重复同意被拒; 删除好友会连单聊会话和申请一起清掉.
前置条件: 需本地 MySQL, Redis, 变量写在仓库根目录的 .env 里.
"""

import asyncio  # 异步编程

import structlog  # 结构化日志

from infinitechat.contracts.enums import FriendApplicationStatus, UserState

from infinitechat.foundation.telemetry import configure_logging
from sqlalchemy import func, select
from user_service.config import UserServiceSettings
from user_service.contacts import ContactService
from user_service.errors import BusinessError

from user_service.runtime import CoreRuntime, build_core_runtime

from user_service.storage.models import FriendModel, UserModel
from user_service.storage.repositories import (
    FriendApplicationRepository,
    SessionRepository,
    UserRepository,
)

logger = structlog.get_logger(__name__)

# 演示账号不能登录, 密码列放一个不可能匹配的占位值
DISABLED_LOGIN = "!demo!"  # 常量 禁用login: 演示账号不能登录

# 建一个正常状态的演示用户, 每次运行都是新 ID
async def new_user(core: CoreRuntime, nickname: str) -> int:
    """建一个正常状态的演示用户, 每次运行都是新 ID, 脚本可以反复跑."""
    user_id = core.ids.next_id()
    # 开数据库会话 session, 再开启事务(出错自动回滚)
    async with core.session_factory() as session, session.begin():
        UserRepository(session).add(  # 加进去
            UserModel(
                user_id=user_id,
                email=f"c5-{user_id}@example.com",
                password=DISABLED_LOGIN,
                nickname=nickname,
            )
        )
    return user_id

# 改一个用户的账号状态, 模拟管理员封禁和解封
async def set_state(core: CoreRuntime, user_id: int, state: UserState) -> None:
    """改一个用户的账号状态, 模拟管理员封禁和解封."""
    # 开数据库会话 session, 再开启事务(出错自动回滚)
    async with core.session_factory() as session, session.begin():
        user = await UserRepository(session).get_by_id(user_id)
        assert user is not None  # 断言 用户 不为空, 不成立就报错
        user.state = state

# 看一眼这一对用户: 申请状态, 好友关系几行
async def snapshot(core: CoreRuntime, sender: int, receiver: int) -> dict[str, object]:
    """看一眼这一对用户: 申请状态, 好友关系几行, 单聊会话有几个成员."""
    async with core.session_factory() as session:  # 开一个数据库会话, 用完自动关闭
        application = await FriendApplicationRepository(session).get_pair(sender, receiver)
        friend_rows = await session.scalar(
            select(func.count())
            .select_from(FriendModel)  # 从 好友模型 里查
            .where(FriendModel.user_id.in_([sender, receiver]))  # 筛选条件, 再取 用户ID
            .where(FriendModel.friend_id.in_([sender, receiver]))  # 筛选条件, 再取 好友ID
        )
        sessions = SessionRepository(session)
        session_id = await sessions.direct_session(sender, receiver)
        # 成员: 会话ID 时取 长度, 否则取 0
        members = len(await sessions.user_ids(session_id)) if session_id else 0
    # 状态: 申请 时取 创建好友申请状态的名字, 否则取 空
    status = FriendApplicationStatus(application.status).name if application else None
    return {"application": status, "friend_rows": friend_rows, "session_members": members}

# 先让同意在半路失败, 再正常同意一次
async def main() -> None:
    """先让同意在半路失败, 再正常同意一次, 最后删除好友."""
    settings = UserServiceSettings()
    core = build_core_runtime(settings)
    contacts = ContactService(
        session_factory=core.session_factory,
        cache=core.cache,
        id_generator=core.ids,
    )
    try:  # 下面这段可能出错, 出错交给 except 处理
        alice = await new_user(core, "阿甲")
        bob = await new_user(core, "阿乙")
        await contacts.send_friend_request(alice, bob, "我是阿甲")
        # 1. 同意之前, 申请人的账号被封了: 状态已经改了一半, 整个事务回滚
        await set_state(core, alice, UserState.BANNED)
        try:  # 下面这段可能出错, 出错交给 except 处理
            await contacts.modify_application(bob, [alice], FriendApplicationStatus.ACCEPTED)
        except BusinessError as error:
            # 打一条 warning 日志
            logger.warning("同意失败", error_code=int(error.code), message=error.message)
        logger.info("回滚之后", **await snapshot(core, alice, bob))  # 打一条 info 日志
        # 2. 解封后再同意: 两行好友关系, 一个两人的单聊会话
        await set_state(core, alice, UserState.NORMAL)
        created = await contacts.modify_application(bob, [alice], FriendApplicationStatus.ACCEPTED)
        assert not isinstance(created, bool)  # 断言 不是 判断类型, 不成立就报错
        logger.info(  # 打一条 info 日志
            "同意成功",
            session_type=created.session_type.name,
            # 把 等看一眼这一对用户: 申请状态, 好友关系几行
            **await snapshot(core, alice, bob),
        )
        # 3. 同一条申请再同意一次
        try:  # 下面这段可能出错, 出错交给 except 处理
            await contacts.modify_application(bob, [alice], FriendApplicationStatus.ACCEPTED)
        except BusinessError as error:
            # 打一条 warning 日志
            logger.warning("重复同意", error_code=int(error.code), message=error.message)
        # 4. 删除好友: 两行关系, 申请, 单聊会话一起清掉
        await contacts.delete_friend(alice, bob)
        logger.info("删除之后", **await snapshot(core, alice, bob))  # 打一条 info 日志
    finally:  # 不管成功还是出错, 最后都执行这里
        await core.close()

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    asyncio.run(main())  # 启动事件循环跑这个协程: 先让同意在半路失败