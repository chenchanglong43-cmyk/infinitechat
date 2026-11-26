"""发红包演示.

扣款, 建红包, 记流水, 写三条待发事件, 都在同一个事务里完成; 同一个 clientMessageId 重发,
返回同一个红包, 不会扣第二次钱; 余额不够时什么都不留下; 单聊只能发普通红包.
前置条件: 需本地 MySQL, Redis, 变量写在仓库根目录的 .env 里.
"""

import asyncio  # 异步编程
import uuid
from decimal import Decimal

import structlog  # 结构化日志
from infinitechat.foundation.settings import read_env  # 先读进程环境变量，再读 .env
from infinitechat.contracts import ErrorCode

from infinitechat.contracts.enums import RedPacketType, SessionType, UserState

from infinitechat.contracts.http import RedPacketBody, RedPacketSendRequest

from infinitechat.contracts.http.internal import UserInfo
from infinitechat.foundation.ids import SnowflakeGenerator

from infinitechat.foundation.telemetry import configure_logging

from red_packet_service.config import redis_url_for_database
from red_packet_service.errors import RedPacketError
from red_packet_service.redis_store import RedPacketRedis
from red_packet_service.service import RedPacketApplication

from red_packet_service.storage.models import BalanceLog, OutboxEvent, RedPacket, UserBalance
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

# 查一个人的流水金额(分), 以及他发出的红包个数
async def ledger(sessions: async_sessionmaker[AsyncSession], user_id: int) -> tuple[list[int], int]:
    """查一个人的流水金额(分), 以及他发出的红包个数."""
    async with sessions() as session:  # 开一个数据库会话, 用完自动关闭
        amounts = list(
            (
                await session.scalars(
                    select(BalanceLog.amount).where(BalanceLog.user_id == user_id)
                )
            ).all()  # 全部取出来
        )
        packets = await session.scalar(
            select(func.count()).select_from(RedPacket).where(RedPacket.sender_id == user_id)
        )
    return amounts, int(packets or 0)

# 查为这个红包写下的待发事件类型
async def events(sessions: async_sessionmaker[AsyncSession], red_packet_id: int) -> list[str]:
    """查为这个红包写下的待发事件类型."""
    async with sessions() as session:  # 开一个数据库会话, 用完自动关闭
        rows = await session.scalars(
            select(OutboxEvent.topic).where(OutboxEvent.aggregate_id == str(red_packet_id))
        )
        return sorted(rows.all())

# 给发红包的人充 100 元, 然后发四次
async def main() -> None:
    """给发红包的人充 100 元, 然后发四次."""
    # 拆成 应用, 会话, Redis: 按红包服务的写法装一个业务对象: MySQL
    app, sessions, redis = build(StubUsers())
    sender = app.ids.next_id()
    await top_up(sessions, sender, 10_000)
    try:  # 下面这段可能出错, 出错交给 except 处理
        # 1. 发一个 10 元 5 份的拼手气红包
        request = red_packet(sender, amount="10.00", count=5)
        sent = await app.send(request)
        # 拆成 金额, packets: 等查一个人的流水金额(分), 以及他发出的红包个数
        amounts, packets = await ledger(sessions, sender)
        pool = await redis.llen(RedPacketRedis.pool_key(sent.red_packet_id))
        logger.info(  # 打一条 info 日志
            "发红包",
            balance=await balance_yuan(sessions, sender),
            ledger=amounts,
            packets=packets,
            pool=pool,
        )
        # 打一条 info 日志
        logger.info("待发事件", topics=await events(sessions, sent.red_packet_id))
        # 2. 同一个 clientMessageId 重发: 返回同一个红包, 不再扣钱
        again = await app.send(request)
        # 拆成 金额, packets: 等查一个人的流水金额(分), 以及他发出的红包个数
        amounts, packets = await ledger(sessions, sender)
        logger.info(  # 打一条 info 日志
            "重复提交",
            same_packet=again.red_packet_id == sent.red_packet_id,
            balance=await balance_yuan(sessions, sender),
            ledger=amounts,
            packets=packets,
        )
        # 3. 余额不够: 什么都不留下
        try:  # 下面这段可能出错, 出错交给 except 处理
            await app.send(red_packet(sender, amount="500.00", count=5))
        except RedPacketError as error:
            # 打一条 warning 日志
            logger.warning("余额不够", error_code=int(error.code), message=error.message)
        # 拆成 金额, packets: 等查一个人的流水金额(分), 以及他发出的红包个数
        amounts, packets = await ledger(sessions, sender)
        logger.info(  # 打一条 info 日志
            "失败之后",
            balance=await balance_yuan(sessions, sender),
            ledger=amounts,
            packets=packets,
        )
        # 4. 单聊只能发普通红包
        direct = red_packet(
            sender,
            amount="1.00",
            count=1,
            session_type=SessionType.DIRECT,
            receiver=app.ids.next_id(),
        )
        try:  # 下面这段可能出错, 出错交给 except 处理
            await app.send(direct)
        except RedPacketError as error:
            # 打一条 warning 日志
            logger.warning("单聊发拼手气", error_code=int(error.code), message=error.message)
    finally:  # 不管成功还是出错, 最后都执行这里
        await redis.aclose()

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    asyncio.run(main())  # 启动事件循环跑这个协程: 给发红包的人充 100 元, 然后发四次