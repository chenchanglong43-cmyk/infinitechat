"""好友申请规则演示.

不能加自己; 申请和两条事件在同一个事务里落库; 重复申请只更新原来那一条;
对方同意以后, 再申请被拒.
前置条件: 需本地 MySQL, Redis, 变量写在仓库根目录的 .env 里.
"""

import asyncio  # 异步编程

import structlog  # 结构化日志
from infinitechat.contracts.enums import FriendApplicationStatus

from infinitechat.foundation.telemetry import configure_logging
from sqlalchemy import func, select
from user_service.config import UserServiceSettings
from user_service.contacts import ContactService
from user_service.errors import BusinessError

from user_service.runtime import CoreRuntime, build_core_runtime

from user_service.storage.models import ApplyFriendModel, OutboxEventModel, UserModel

from user_service.storage.repositories import FriendApplicationRepository, UserRepository

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

# 查这一对用户之间的申请: 一共几条, 当前留言
async def pair_state(core: CoreRuntime, sender: int, receiver: int) -> tuple[int, str, int]:
    """查这一对用户之间的申请: 一共几条, 当前留言, 以及为它写下的事件条数."""
    async with core.session_factory() as session:  # 开一个数据库会话, 用完自动关闭
        applications = await session.scalar(
            select(func.count())
            .select_from(ApplyFriendModel)  # 从 好友申请模型 里查
            # 筛选条件, 再取 发送人ID
            .where(ApplyFriendModel.sender_id == sender, ApplyFriendModel.receiver_id == receiver)
        )
        application = await FriendApplicationRepository(session).get_pair(sender, receiver)
        assert application is not None  # 断言 申请 不为空, 不成立就报错
        events = await session.scalar(
            select(func.count())
            .select_from(OutboxEventModel)  # 从 待发事件事件模型 里查
            .where(  # 筛选条件
                OutboxEventModel.aggregate_id.in_([str(application.apply_friend_id), str(receiver)])
            )
        )
        return int(applications or 0), application.message, int(events or 0)

# 一个人连着申请两次
async def main() -> None:
    """一个人连着申请两次, 对方同意后再申请一次."""
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
        # 1. 不能加自己
        try:  # 下面这段可能出错, 出错交给 except 处理
            await contacts.send_friend_request(alice, alice, "加我自己")
        except BusinessError as error:
            # 打一条 warning 日志
            logger.warning("被拒", case="加自己", error_code=int(error.code), message=error.message)
        # 2. 第一次申请: 一条申请, 两条事件(给对方的通知, 申请创建事件)
        await contacts.send_friend_request(alice, bob, "我是阿甲")
        # 拆成 applications, 消息, 事件: 等查这一对用户之间的申请: 一共几条
        applications, message, events = await pair_state(core, alice, bob)
        # 打一条 info 日志
        logger.info("第一次申请", applications=applications, message=message, events=events)
        # 3. 再申请一次: 还是那一条申请, 留言换成新的, 事件又多两条
        await contacts.send_friend_request(alice, bob, "再加一次")
        # 拆成 applications, 消息, 事件: 等查这一对用户之间的申请: 一共几条
        applications, message, events = await pair_state(core, alice, bob)
        # 打一条 info 日志
        logger.info("重复申请", applications=applications, message=message, events=events)
        # 4. 对方同意以后, 再申请被拒
        await contacts.modify_application(bob, [alice], FriendApplicationStatus.ACCEPTED)
        try:  # 下面这段可能出错, 出错交给 except 处理
            await contacts.send_friend_request(alice, bob, "第三次")
        except BusinessError as error:
            logger.warning(  # 打一条 warning 日志
                "被拒",
                case="已是好友",
                error_code=int(error.code),
                message=error.message,
            )
    finally:  # 不管成功还是出错, 最后都执行这里
        await core.close()

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    asyncio.run(main())  # 启动事件循环跑这个协程: 一个人连着申请两次