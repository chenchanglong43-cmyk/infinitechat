"""群组规则演示.

建群只拉得进好友, 拉不进的人单独返回; 普通成员不能邀请人; 群主邀请时好友进得来、陌生人进不来,
结果里的用户 ID 是字符串(按接口约定).
前置条件: 需本地 MySQL, Redis, 变量写在仓库根目录的 .env 里.
"""

# 导入异步io库，用来跑异步任务
import asyncio
# 导入结构化日志库，方便打印规范日志
import structlog
# 导入好友申请状态枚举
from infinitechat.contracts.enums import FriendApplicationStatus
# 导入建群、邀请入群的请求结构体
from infinitechat.contracts.http import CreateGroupRequest, InviteGroupRequest
# 导入日志链路追踪配置
from infinitechat.foundation.telemetry import configure_logging
# 导入用户服务配置类
from user_service.config import UserServiceSettings
# 导入好友关系服务
from user_service.contacts import ContactService
# 导入自定义业务异常
from user_service.errors import BusinessError
# 导入群聊服务
from user_service.groups import GroupService
# 导入核心运行时、构建运行时的函数
from user_service.runtime import CoreRuntime, build_core_runtime
# 导入用户数据库模型
from user_service.storage.models import UserModel
# 导入用户仓库类，操作用户表
from user_service.storage.repositories import UserRepository

# 获取日志实例
logger = structlog.get_logger(__name__)

# 演示账号的密码占位符，设置成不可能匹配的值，禁止登录
DISABLED_LOGIN = "!demo!"

# 创建一个正常状态的演示用户，每次运行都会生成全新id
async def new_user(core: CoreRuntime, nickname: str) -> int:
    """建一个正常状态的演示用户, 每次运行都是新 ID, 脚本可以反复跑."""
    # 用雪花生成器拿到新用户id
    user_id = core.ids.next_id()
    # 开启数据库会话和事务，出错自动回滚
    async with core.session_factory() as session, session.begin():
        # 实例化用户仓库，添加用户记录
        UserRepository(session).add(
            UserModel(
                user_id=user_id,
                email=f"c5-{user_id}@example.com",
                password=DISABLED_LOGIN,
                nickname=nickname,
            )
        )
    # 返回新建用户id给调用方
    return user_id

# 模拟完整加好友流程：A发申请，B同意，变成双向好友
async def make_friends(contacts: ContactService, first: int, second: int) -> None:
    """走一遍真实流程: 一方申请, 另一方同意."""
    # first给second发送好友申请，附留言
    await contacts.send_friend_request(first, second, "交个朋友")
    # second同意这条好友申请
    await contacts.modify_application(second, [first], FriendApplicationStatus.ACCEPTED)

# 主逻辑：先建群，再测试邀请权限和好友校验
async def main() -> None:
    """群主拉两个好友和一个陌生人建群, 然后试邀请."""
    # 加载服务配置，自动读取.env环境变量
    settings = UserServiceSettings()
    # 初始化整套核心运行时（数据库、redis、id生成器等）
    core = build_core_runtime(settings)
    # 初始化好友服务，注入依赖
    contacts = ContactService(
        session_factory=core.session_factory,
        cache=core.cache,
        id_generator=core.ids,
    )
    # 初始化群聊服务，注入依赖
    groups = GroupService(
        session_factory=core.session_factory,
        id_generator=core.ids,
        settings=settings,
    )
    # try捕获代码块里抛出的业务异常
    try:
        # 创建群主用户，昵称阿甲
        owner = await new_user(core, "阿甲")
        # 创建演示用户阿乙
        first = await new_user(core, "阿乙")
        # 创建演示用户阿丙
        second = await new_user(core, "阿丙")
        # 创建演示用户阿丁，后面用来测试邀请
        late = await new_user(core, "阿丁")
        # 创建陌生人路人，不是群主好友
        stranger = await new_user(core, "路人")
        # 循环，把阿乙、阿丙、阿丁都和群主加为好友
        for friend in (first, second, late):
            await make_friends(contacts, owner, friend)
        # 场景1：群主发起建群，带上两个好友+一个陌生人
        created = await groups.create(
            CreateGroupRequest(creator_id=owner, member_ids=[first, second, stranger])
        )
        # 拿到新建群的会话id，转成整数
        session_id = int(created.session_id)
        # 打印info日志，校验失败成员是不是只有路人
        logger.info(
            "建群",
            name=created.session_name,
            failed=created.failed_member_ids == [str(stranger)],
        )
        # 场景2：普通成员阿乙尝试邀请人，预期会无权限报错
        try:
            await groups.invite(
                InviteGroupRequest(session_id=session_id, inviter_id=first, invitee_ids=[late])
            )
        # 捕获业务异常
        except BusinessError as error:
            # 打印警告日志，记录权限拦截结果
            logger.warning(
                "被拒",
                case="成员邀请",
                error_code=int(error.code),
                message=error.message,
            )
        # 场景3：群主发起邀请，邀请好友阿丁 + 陌生人路人
        invited = await groups.invite(
            InviteGroupRequest(
                session_id=session_id,
                inviter_id=owner,
                invitee_ids=[late, stranger],
            )
        )
        # 打印日志，校验成功、失败列表，同时确认id是字符串类型
        logger.info(
            "群主邀请",
            success=invited.success_ids == [str(late)],
            failed=invited.failed_ids == [str(stranger)],
            id_type=type(invited.success_ids[0]).__name__,
        )
    # 不管上面代码正常跑完还是报错，最后都执行关闭资源
    finally:
        # 关闭核心运行时，释放数据库、redis连接
        await core.close()

# 如果直接运行这个脚本文件，就执行main
if __name__ == "__main__":
    # 配置日志，关闭json格式，用普通文本输出
    configure_logging(json_output=False)
    # 启动异步事件循环，执行main协程
    asyncio.run(main())