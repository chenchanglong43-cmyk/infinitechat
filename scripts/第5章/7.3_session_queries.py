"""会话查询演示.

三个给前端和其他服务用的查询(按接口约定不套统一响应的壳): 会话成员的昵称、消息接收者、
我的会话 ID; 不在会话里的人查不了接收者.
前置条件: 需本地 MySQL, Redis, 变量写在仓库根目录的 .env 里.
"""

import asyncio  # 异步编程

import structlog  # 结构化日志
from infinitechat.contracts.enums import FriendApplicationStatus
from infinitechat.contracts.http import CreateGroupRequest

from infinitechat.foundation.telemetry import configure_logging
from user_service.config import UserServiceSettings
from user_service.contacts import ContactService
from user_service.errors import BusinessError
from user_service.groups import GroupService

from user_service.integrations.minio import AvatarStorage

from user_service.runtime import CoreRuntime, build_core_runtime
from user_service.storage.models import UserModel

from user_service.storage.repositories import UserRepository
from user_service.users import UserProfileService

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

# 走一遍真实流程: 一方申请, 另一方同意
async def make_friends(contacts: ContactService, first: int, second: int) -> None:
    """走一遍真实流程: 一方申请, 另一方同意."""
    await contacts.send_friend_request(first, second, "交个朋友")
    await contacts.modify_application(second, [first], FriendApplicationStatus.ACCEPTED)

# 阿甲和两个好友各有一个单聊
async def main() -> None:
    """阿甲和两个好友各有一个单聊, 再拉一个三人群, 然后查一查."""
    settings = UserServiceSettings()
    core = build_core_runtime(settings)
    contacts = ContactService(
        session_factory=core.session_factory,
        cache=core.cache,
        id_generator=core.ids,
    )
    groups = GroupService(
        session_factory=core.session_factory,
        id_generator=core.ids,
        settings=settings,
    )
    profiles = UserProfileService(
        session_factory=core.session_factory,
        avatar_storage=AvatarStorage(settings),
        allowed_avatar_hosts=frozenset(),
    )
    try:  # 下面这段可能出错, 出错交给 except 处理
        alice = await new_user(core, "阿甲")
        bob = await new_user(core, "阿乙")
        carol = await new_user(core, "阿丙")
        outsider = await new_user(core, "路人")
        await make_friends(contacts, alice, bob)
        await make_friends(contacts, alice, carol)
        created = await groups.create(CreateGroupRequest(creator_id=alice, member_ids=[bob, carol]))
        session_id = int(created.session_id)
        # 1. 会话成员的昵称: 键是用户 ID, 值是昵称
        names = await profiles.nicknames(alice, session_id)
        logger.info("成员昵称", names=sorted(names.values()))  # 打一条 info 日志
        # 2. 消息接收者: 群里三个人都在
        # 打一条 info 日志
        logger.info("接收者", count=len(await profiles.receivers(alice, session_id)))
        # 3. 不在会话里的人查接收者: 被拒
        try:  # 下面这段可能出错, 出错交给 except 处理
            await profiles.receivers(outsider, session_id)
        except BusinessError as error:
            logger.warning(  # 打一条 warning 日志
                "被拒",
                case="非成员查接收者",
                error_code=int(error.code),
                message=error.message,
            )
        # 4. 我的会话 ID: 阿甲有两个单聊和一个群
        logger.info("我的会话", count=len(await profiles.sessions(alice)))  # 打一条 info 日志
    finally:  # 不管成功还是出错, 最后都执行这里
        await core.close()

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    asyncio.run(main())  # 启动事件循环跑这个协程: 阿甲和两个好友各有一个单聊