"""好友申请到期链路演示.

申请事件经发布器发出, 消费处理把它登记进到期队列; 到期后调度器写下过期事件,
再经发布和消费把申请置为已过期; 同一个过期事件投递两次, 只生效一次.
前置条件: 需本地 MySQL, Redis, 变量写在仓库根目录的 .env 里.
"""

import asyncio  # 异步库
import time  # 获取时间戳
from typing import Any

import structlog  # 结构化日志
from infinitechat.contracts.enums import FriendApplicationStatus
# 好友申请状态枚举

from infinitechat.contracts.events import FriendRequestCreated, FriendRequestExpired, KafkaTopic
# 好友申请创建、过期事件，Kafka主题枚举

from infinitechat.foundation.telemetry import configure_logging
# 日志初始化工具
from user_service.config import UserServiceSettings
# 用户服务配置
from user_service.contacts import ContactService
# 联系人业务服务（发好友申请）
from user_service.outbox import OutboxRepository
# 事务发件箱仓库

from user_service.runtime import CoreRuntime, build_core_runtime
# 核心运行时，封装db、redis、id生成器

from user_service.schedulers.friend_requests import FriendRequestExpirationScheduler
# 好友申请过期调度器
from user_service.storage.models import UserModel
# 用户数据库模型

from user_service.storage.repositories import FriendApplicationRepository, UserRepository
# 好友申请、用户仓库

from user_service.workers.friend_requests import FriendRequestHandlers
# 好友申请事件处理器
from user_service.workers.outbox_publisher import OutboxPublisher
# outbox发布器，把发件箱事件推到Kafka

logger = structlog.get_logger(__name__)

# 演示账号不能登录，密码填一个无效占位值
DISABLED_LOGIN = "!demo!"

# 替身生产者：不连真实Kafka，只记录发出的事件
class RecordingProducer:
    """替身生产者: 不连 Kafka, 把发出的事件按顺序记下来."""

    def __init__(self) -> None:
        """还没发出任何事件."""
        self.sent: list[tuple[str, str | None, dict[str, Any]]] = []

    # 记录topic、消息key、消息体
    async def send(
        self,
        *,  # 后面参数必须关键字传参
        topic: str,
        key: str | None,
        payload: dict[str, Any],
        headers: dict[str, Any],
    ) -> None:
        """记下 Topic, 键和消息体."""
        self.sent.append((topic, key, payload))

    # 取出指定topic+key对应的最后一条消息
    def take(self, topic: str, key: str) -> dict[str, Any]:
        """取出发往某个 Topic, 某个键的最后一条消息."""
        return [
            payload
            for sent_topic, sent_key, payload in self.sent
            if (sent_topic, sent_key) == (topic, key)
        ][-1]

# 创建演示用户，每次运行生成新ID
async def new_user(core: CoreRuntime, nickname: str) -> int:
    """建一个正常状态的演示用户, 每次运行都是新 ID."""
    user_id = core.ids.next_id()
    # 开启事务写入用户
    async with core.session_factory() as session, session.begin():
        UserRepository(session).add(
            UserModel(
                user_id=user_id,
                email=f"c6-{user_id}@example.com",
                password=DISABLED_LOGIN,
                nickname=nickname,
            )
        )
    return user_id

# 主流程：发起好友申请，完整跑一遍过期链路
async def main() -> None:
    """发一个好友申请, 把它一路推到已过期."""
    settings = UserServiceSettings()
    core = build_core_runtime(settings)
    producer = RecordingProducer()  # 替身生产者，代替真实Kafka
    contacts = ContactService(
        session_factory=core.session_factory,
        cache=core.cache,
        id_generator=core.ids,
    )
    publisher = OutboxPublisher(session_factory=core.session_factory, producer=producer)
    handlers = FriendRequestHandlers(session_factory=core.session_factory, cache=core.cache)
    scheduler = FriendRequestExpirationScheduler(
        session_factory=core.session_factory,
        cache=core.cache,
    )
    try:
        # 清空历史发件箱占位，避免旧数据干扰
        async with core.session_factory() as session, session.begin():
            await OutboxRepository(session).claim(limit=10_000)
        # 创建两个测试用户
        alice = await new_user(core, "阿甲")
        bob = await new_user(core, "阿乙")
        # 发起好友申请
        await contacts.send_friend_request(alice, bob, "我是阿甲")
        # 等待1秒，保证Redis时间分片生效，可以被认领
        await asyncio.sleep(1)
        # 查询这条好友申请记录
        async with core.session_factory() as session:
            application = await FriendApplicationRepository(session).get_pair(alice, bob)
            assert application is not None
            apply_id = application.apply_friend_id

        # 1. outbox发布器把【好友申请创建事件】发出去
        await publisher.run_once()
        ours = {str(apply_id), str(bob)}
        topics = sorted({topic for topic, key, _ in producer.sent if key in ours})
        logger.info("第一次发布", topics=topics)

        # 2. 消费创建事件，把申请加入Redis过期队列；手动修改过期时间，改成已经过期，不用等24小时
        created = FriendRequestCreated.model_validate(
            producer.take(KafkaTopic.FRIEND_REQUEST_CREATION, str(apply_id))
        )
        await handlers.enqueue(
            created.model_copy(update={"expire_time": time.time_ns() // 1_000_000 - 1})
        )

        # 3. 调度器执行一轮，生成过期事件写入outbox；再由发布器发出过期事件
        await scheduler.run_once()
        await asyncio.sleep(1)
        await publisher.run_once()
        expired = FriendRequestExpired.model_validate(
            producer.take(KafkaTopic.FRIEND_REQUEST_EXPIRATION, str(apply_id))
        )
        logger.info("过期事件", apply_matches=expired.apply_friend_id == apply_id)

        # 4. 消费过期事件：第一次执行状态变更，重复投递返回false，验证幂等
        first = await handlers.expire(expired)
        second = await handlers.expire(expired)
        async with core.session_factory() as session:
            final = await FriendApplicationRepository(session).get_pair(alice, bob)
            assert final is not None
        logger.info(
            "消费过期事件",
            first=first,
            second=second,
            status=FriendApplicationStatus(final.status).name,
        )
    finally:
        await core.close()  # 关闭数据库、Redis连接

if __name__ == "__main__":
    configure_logging(json_output=False)
    asyncio.run(main())