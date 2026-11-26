"""抢红包演示.

20 个群成员同时抢一个 1 元 5 份的拼手气红包: 恰好 5 人抢到, 金额加起来正好 1 元, 红包变成
已领完, 抢到的人余额立刻到账; 来晚的人看到已被领完; 抢到的人再点一次, 告诉他已经领过;
不在群里的人领不了.
前置条件: 需本地 MySQL, Redis, 变量写在仓库根目录的 .env 里.
"""

import asyncio  # 异步编程
import uuid
from decimal import Decimal

import structlog  # 结构化日志
from infinitechat.foundation.settings import read_env  # 先读进程环境变量，再读 .env
from infinitechat.contracts import ErrorCode

from infinitechat.contracts.enums import RedPacketStatus, RedPacketType, SessionType, UserState

from infinitechat.contracts.http import RedPacketBody, RedPacketReceiveRequest, RedPacketSendRequest

from infinitechat.contracts.http.internal import UserInfo
from infinitechat.foundation.ids import SnowflakeGenerator

from infinitechat.foundation.telemetry import configure_logging

from red_packet_service.config import redis_url_for_database
from red_packet_service.errors import RedPacketError
from red_packet_service.redis_store import RedPacketRedis
from red_packet_service.service import RedPacketApplication

from red_packet_service.storage.models import RedPacket, UserBalance
from redis.asyncio import Redis
from sqlalchemy import func, select

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = structlog.get_logger(__name__)

# 替身 user 服务: 人人状态正常
class StubUsers:
    """替身 user 服务: 人人状态正常, 单聊校验一律通过; 群成员由 members 决定, 为空表示谁都算."""

    def __init__(self, members: set[int] | None = None) -> None:  # 构造方法: 创建对象时把依赖存起来
        """记下群成员名单."""
        self.members = members or set()

    # 所有人都是正常状态
    async def user_state(self, user_id: int) -> UserState | None:
        """所有人都是正常状态."""
        return UserState.NORMAL

    # 单聊校验一律通过
    async def validate_direct(
        self,
        *,  # 后面的参数只能按名字传
        sender_id: int,
        receiver_id: int,
        session_id: int,
    ) -> tuple[bool, ErrorCode | None, str | None]:
        """单聊校验一律通过."""
        return True, None, None

    # 名单为空时谁都算群成员
    async def group_membership(self, user_id: int, session_id: int) -> bool:
        """名单为空时谁都算群成员, 否则看是否在名单里."""
        return not self.members or user_id in self.members

    # 群里的人数, 名单为空时按 50 人算
    async def group_member_count(self, session_id: int) -> int:
        """群里的人数, 名单为空时按 50 人算."""
        return len(self.members) or 50

    # 演示用不到昵称和头像
    async def users(self, user_ids: set[int]) -> dict[int, UserInfo]:
        """演示用不到昵称和头像."""
        return {}

# 按红包服务的写法装一个业务对象: MySQL, Redis db0
def build(users: StubUsers) -> tuple[RedPacketApplication, async_sessionmaker[AsyncSession], Redis]:
    """按红包服务的写法装一个业务对象: MySQL, Redis db0, 演示专用的雪花节点 29-29."""
    engine = create_async_engine(read_env("MYSQL_DSN"), pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(
        redis_url_for_database(read_env("REDIS_URL"), 0),
        decode_responses=True,
        protocol=2,
    )
    ids = SnowflakeGenerator(worker_id=29, data_center_id=29)
    app = RedPacketApplication(sessions, RedPacketRedis(redis, ttl_seconds=3600), redis, users, ids)
    return app, sessions, redis

# 演示前给账户充值: 本项目没有充值接口
async def top_up(sessions: async_sessionmaker[AsyncSession], user_id: int, fen: int) -> None:
    """演示前给账户充值: 本项目没有充值接口, 直接写一行余额."""
    # 开数据库会话 session, 再开启事务(出错自动回滚)
    async with sessions() as session, session.begin():
        session.add(UserBalance(user_id=user_id, balance=fen))  # 放进会话, 提交时写库

# 拼一个发红包请求
def red_packet(
    sender: int,
    *,  # 星号后面的参数只能按名字传
    amount: str,
    count: int,
    kind: RedPacketType = RedPacketType.RANDOM,
    session_type: SessionType = SessionType.GROUP,
    receiver: int | None = None,
    client_message_id: str | None = None,
) -> RedPacketSendRequest:
    """拼一个发红包请求, 默认是群里的拼手气红包."""
    return RedPacketSendRequest(
        session_id=1,
        receiver_id=receiver,
        sender_id=sender,
        session_type=session_type,
        body=RedPacketBody(
            red_packet_type=kind,
            total_amount=Decimal(amount),
            total_count=count,
            red_packet_wrapper_text="恭喜发财",
        ),
        client_message_id=client_message_id or uuid.uuid4().hex,
    )

# 查一个人的余额, 按元显示
async def balance_yuan(sessions: async_sessionmaker[AsyncSession], user_id: int) -> str:
    """查一个人的余额, 按元显示."""
    async with sessions() as session:  # 开一个数据库会话, 用完自动关闭
        fen = await session.scalar(
            select(UserBalance.balance).where(UserBalance.user_id == user_id)
        )
    return f"{(fen or 0) / 100:.2f}"

# 一个群, 20 个成员同时抢
async def main() -> None:
    """一个群, 20 个成员同时抢, 再有一个人来晚, 一个外人来抢."""
    ids = SnowflakeGenerator(worker_id=28, data_center_id=28)
    sender = ids.next_id()
    members = [ids.next_id() for _ in range(21)]  # 成员: 生成一串数字里每个算出的 生成下一个 ID
    early, late = members[:20], members[20]  # 拆成 early, late: (成员[切片], 成员[20])
    # 发红包的人自己也得是群成员
    # 拆成 应用, 会话, Redis: 按红包服务的写法装一个业务对象: MySQL
    app, sessions, redis = build(StubUsers({sender, *members}))
    await top_up(sessions, sender, 10_000)
    try:  # 下面这段可能出错, 出错交给 except 处理
        sent = await app.send(red_packet(sender, amount="1.00", count=5))
        # 1. 20 个人同时抢: 每次领取都在 Redis 里原子完成, 结算在 MySQL 里排队
        results = await asyncio.gather(
            *(  # 再乘以 early里每个用户算出的 领取红包
                # 领取红包
                app.receive(RedPacketReceiveRequest(user_id=user, red_packet_id=sent.red_packet_id))
                for user in early
            )
        )
        winners = [
            (user, result)  # (用户, 结果)
            for user, result in zip(early, results, strict=True)
            if result.amount is not None
        ]
        total = sum((result.amount or Decimal(0) for _, result in winners), Decimal(0))
        async with sessions() as session:  # 开一个数据库会话, 用完自动关闭
            packet = await session.get(RedPacket, sent.red_packet_id)
            assert packet is not None  # 断言 包 不为空, 不成立就报错
            credited = await session.scalar(
                select(func.coalesce(func.sum(UserBalance.balance), 0)).where(
                    UserBalance.user_id.in_(early)
                )
            )
        logger.info(  # 打一条 info 日志
            "20 人同时抢",
            winners=len(winners),
            losers=len(results) - len(winners),
            total=str(total),
            status=RedPacketStatus(packet.status).name,
            credited_fen=int(credited or 0),
        )
        # 2. 来晚的人
        late_result = await app.receive(
            RedPacketReceiveRequest(user_id=late, red_packet_id=sent.red_packet_id)
        )
        logger.info("来晚了", message=late_result.message)  # 打一条 info 日志
        # 3. 抢到的人再点一次
        winner, first = winners[0]  # 拆成 winner, 第一: winners[0]
        again = await app.receive(
            RedPacketReceiveRequest(user_id=winner, red_packet_id=sent.red_packet_id)
        )
        # 打一条 info 日志
        logger.info("再点一次", message=again.message, same_amount=again.amount == first.amount)
        # 4. 不在群里的人
        try:  # 下面这段可能出错, 出错交给 except 处理
            await app.receive(
                RedPacketReceiveRequest(user_id=app.ids.next_id(), red_packet_id=sent.red_packet_id)
            )
        except RedPacketError as error:
            # 打一条 warning 日志
            logger.warning("外人来抢", error_code=int(error.code), message=error.message)
    finally:  # 不管成功还是出错, 最后都执行这里
        await redis.aclose()

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    asyncio.run(main())  # 启动事件循环跑这个协程: 一个群, 20 个成员同时抢