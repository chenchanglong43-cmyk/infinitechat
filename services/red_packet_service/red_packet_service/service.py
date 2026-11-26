"""Red packet application service: send, receive and settle."""
# 红包业务核心：发红包、抢红包、结算（把结果落库）都在这个文件里

# 提示文案里的中文标点会直接展示给用户，是刻意保留的
# ruff: noqa: RUF001  ← 告诉 ruff：本文件允许全角标点，别报警

import hashlib  # 哈希：生成摘要用，比如做去重键
import json  # JSON 序列化：组装 Outbox 事件的 payload
from contextlib import suppress  # 安静地忽略指定异常，比如删不存在的 key
from datetime import datetime, timedelta  # 时间点 / 时间间隔（算过期时间）
from decimal import Decimal  # 十进制金额，避免浮点误差
from typing import Any, cast  # Any 通用类型；cast 给类型检查器“指定类型”
from zoneinfo import ZoneInfo  # 时区：统一按上海时间算

from infinitechat.contracts import ErrorCode, error_message  # 错误码 + 错误码转文案
from infinitechat.contracts.enums import (  # 业务枚举
    BalanceLogType,  # 余额流水类型：发红包扣款/领红包入账/退款
    MessageType,  # 消息类型：红包消息
    RedPacketStatus,  # 红包状态：待领/部分领取/领完/过期
    RedPacketType,  # 红包类型：普通/拼手气
    SessionType,  # 会话类型：单聊/群聊
    UserState,  # 用户状态：正常/封禁
)

from infinitechat.contracts.events import RedPacketCompleted, RedPacketCreated, RedPacketReceived  # 三类领域事件

from infinitechat.contracts.events.topics import KafkaTopic  # Kafka 主题常量
from infinitechat.contracts.http import (  # HTTP 出入口模型
    ReceiveResult,  # 领红包的返回结果
    RedPacketReceiveRequest,  # 领红包请求
    RedPacketSendRequest,  # 发红包请求
    RedPacketSendResponse,  # 发红包返回
)

from infinitechat.contracts.websocket import MessageBody, MessageRequest  # 往会话里发“红包消息”用的模型
from infinitechat.foundation.ids import SnowflakeGenerator  # 雪花ID：红包、流水、消息的主键
from redis.asyncio import Redis  # 异步 Redis 客户端
from sqlalchemy import func, select, update  # SQL 查询/更新/聚合函数
from sqlalchemy.dialects.mysql import insert as mysql_insert  # MySQL 的 INSERT ... ON DUPLICATE KEY UPDATE
from sqlalchemy.engine import CursorResult  # 执行结果类型，能看影响行数

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # 异步会话 / 会话工厂

from red_packet_service.allocation import allocate_normal, allocate_random  # 金额拆分：普通/拼手气
from red_packet_service.errors import RedPacketError  # 红包服务的业务异常
from red_packet_service.outbox import add_event  # 往 Outbox 表里塞待发事件
from red_packet_service.ports import UserValidationPort  # 用户/群校验端口
from red_packet_service.redis_store import RedPacketRedis  # Redis 金额池、领取记录
from red_packet_service.storage.models import (  # 数据库 ORM 模型
    BalanceLog,  # 余额流水
    RedPacket,  # 红包主表
    RedPacketReceive,  # 领取记录
    UserBalance,  # 用户余额
)

# 获取当前时间：按上海时区算，但去掉时区信息，方便直接存 MySQL 的 DATETIME
def now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)

# 元 → 分：把 Decimal 金额换成整数分；小数超过两位就直接报错
def to_fen(amount: Decimal) -> int:
    fen = amount * 100  # 乘 100 变成“分”
    if fen != fen.to_integral_value():
        # 还有零头说明不只是两位小数，比如 1.005 元，拒绝
        raise RedPacketError(ErrorCode.PARAMS_ERROR, "红包金额最多保留两位小数")
    return int(fen)

# 分 → 元：整数分换回 Decimal 元，固定保留两位小数
def to_yuan(amount: int) -> Decimal:
    return (Decimal(amount) / Decimal(100)).quantize(Decimal("0.00"))



# 红包服务
class RedPacketApplication:
    # 初始化
    def __init__(  # 构造方法: 创建对象时把依赖存起来
        self,
        sessions: async_sessionmaker[AsyncSession],
        redis_store: RedPacketRedis,
        dedupe_redis: Redis,
        users: UserValidationPort,
        ids: SnowflakeGenerator,
        *,  # 星号后面的参数只能按名字传
        expire_seconds: int = 24 * 3600,
    ) -> None:
        self.sessions = sessions
        self.redis_store = redis_store
        self.dedupe_redis = dedupe_redis
        self.users = users
        self.ids = ids
        self.expire_seconds = expire_seconds

    # 发送红包：校验 -> 防重 -> 拆金额 -> 扣钱 + 落库 + 写 Redis -> 记事件
    async def send(self, request: RedPacketSendRequest) -> RedPacketSendResponse:
        await self._validate_send(request)  # 先做业务校验（用户状态、权限、份数等）
        duplicate_key: str | None = None  # Redis 防重复键，默认没有
        if request.client_message_id is None:
            # 客户端没给消息ID，就用整个请求算一个指纹，防止连点/重复提交
            # sort_keys=True 让字段顺序固定，ensure_ascii=False 保留中文原样，哈希才稳定
            fingerprint = hashlib.sha256(
                json.dumps(request.to_wire(), sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            # 交给 Redis 做 3 秒互斥：SET NX 成功才算“第一次”，失败说明刚有人发过一样的
            duplicate_key = f"prevent:duplicate:send:{fingerprint}"
            if not await self.dedupe_redis.set(duplicate_key, "1", ex=3, nx=True):
                raise RedPacketError(ErrorCode.OPERATION_ERROR, "操作过于频繁，请稍后再试")

        # 红包ID和消息ID各生成一个雪花ID
        red_packet_id = self.ids.next_id()
        message_id = self.ids.next_id()
        # 前端传的是“元”，内部统一换成“分”来算，避免小数误差
        total_amount = to_fen(request.body.total_amount)
        # 普通红包均分，拼手气红包随机切
        amounts = (
            allocate_normal(total_amount, request.body.total_count)
            if request.body.red_packet_type is RedPacketType.NORMAL
            else allocate_random(total_amount, request.body.total_count)
        )
        created = now()  # 统一用同一个时间戳，创建/更新/过期都基于它
        initialized = False
        try:
            # 打开会话并开启事务：正常退出自动提交，抛异常自动回滚
            async with self.sessions() as session, session.begin():
                # 客户端带了消息ID：先按 (发送人, 消息ID) 查一下，重复请求直接返回原来的红包
                if request.client_message_id:
                    existing = await session.scalar(
                        select(RedPacket).where(
                            RedPacket.sender_id == request.sender_id,
                            RedPacket.client_message_id == request.client_message_id,
                        )
                    )
                    if existing is not None:
                        if existing.message_id is None:
                            # 记录在但消息ID没写进去，属于数据异常
                            raise RedPacketError(ErrorCode.SYSTEM_ERROR, "红包消息ID缺失")
                        # 幂等返回：不重新扣钱，把上次的结果还给客户端
                        return RedPacketSendResponse(
                            red_packet_id=existing.red_packet_id,
                            message_id=existing.message_id,
                        )
                # 扣余额：把“余额够不够”写进 WHERE，让数据库原子完成判断+扣减，防止并发多扣
                debited = cast(
                    CursorResult[Any],
                    await session.execute(
                        update(UserBalance)
                        .where(
                            UserBalance.user_id == request.sender_id,
                            UserBalance.balance >= total_amount,  # 余额不够就更新不到任何行
                        )
                        .values(balance=UserBalance.balance - total_amount, updated_time=created)
                    ),
                )
                if debited.rowcount != 1:
                    # 影响行数不是 1：要么余额不足，要么这个用户的余额记录不存在
                    raise RedPacketError(ErrorCode.OPERATION_ERROR, "余额不足或扣款失败")
                # 红包过期时间 = 创建时间 + 配置的有效期（默认 24 小时）
                expires_at = created + timedelta(seconds=self.expire_seconds)
                # 红包主记录：先记成“待领取”
                session.add(
                    RedPacket(
                        red_packet_id=red_packet_id,
                        sender_id=request.sender_id,
                        receiver_id=request.receiver_id,
                        session_id=request.session_id,
                        session_type=int(request.session_type),
                        red_packet_wrapper_text=request.body.red_packet_wrapper_text,  # 红包上的祝福文案
                        red_packet_type=int(request.body.red_packet_type),
                        total_amount=total_amount,  # 库里存的是分
                        total_count=request.body.total_count,
                        status=int(RedPacketStatus.NOT_COMPLETED),  # 待领取
                        client_message_id=request.client_message_id,
                        message_id=message_id,
                        expires_at=expires_at,
                        created_time=created,
                        updated_time=created,
                    )
                )
                # 记一条余额流水：金额为负，表示支出
                self._add_log(
                    session,
                    user_id=request.sender_id,
                    amount=-total_amount,
                    kind=BalanceLogType.SEND,
                    red_packet_id=red_packet_id,
                    at=created,
                )
                # 把拆好的金额写进 Redis 金额池，之后大家就从这里抢
                initialized = await self.redis_store.initialize(red_packet_id, amounts)
                if not initialized:
                    # Redis 写失败就抛错，让整个事务回滚，钱不会被扣走
                    raise RedPacketError(ErrorCode.SYSTEM_ERROR, "红包缓存初始化失败")
                # 写 Outbox 事件：红包创建、消息通知，跟业务数据同事务落库
                self._add_send_events(
                    session,
                    request=request,
                    red_packet_id=red_packet_id,
                    message_id=message_id,
                    created=created,
                )
        except Exception:
            # 任何一步失败：尽量把 Redis 里的半成品清掉，别留下脏池子
            with suppress(Exception):  # 清理失败也不影响把原始异常抛出去
                await self.redis_store.cleanup(red_packet_id)
            if duplicate_key is not None:
                with suppress(Exception):  # 删防重复键，让用户能立刻重试，不用干等 3 秒
                    await self.dedupe_redis.delete(duplicate_key)
            raise  # 原样抛出，交给上层转成错误响应
        return RedPacketSendResponse(red_packet_id=red_packet_id, message_id=message_id)

    
    # 领取红包：查红包 -> 校验权限 -> 防重/抢一份 -> 落库结算 -> 返回结果
    async def receive(self, request: RedPacketReceiveRequest) -> ReceiveResult:
        # 先把红包查出来；查不到就直接返回“不存在”，不抛异常
        packet = await self._packet(request.red_packet_id)
        if packet is None:
            return ReceiveResult(status=RedPacketStatus.NOT_EXIST, message="红包不存在")
        # 校验这个人有没有资格领（单聊看双方、群聊看成员等），没资格直接抛业务异常
        if not await self._can_receive(request.user_id, packet):
            raise self._receive_denied(request.user_id, packet)
        # 红包已经不是“待领取”状态（领完/过期）：先看这个人之前是不是领过
        if packet.status != int(RedPacketStatus.NOT_COMPLETED):
            previous = await self.redis_store.previous_amount(
                request.red_packet_id,
                request.user_id,
            )
            if previous is not None:
                # 领过就原样告诉他“已经领过了”，金额用当时领到的
                return ReceiveResult(
                    status=RedPacketStatus(packet.status),
                    message="您已经领取过该红包了",
                    amount=to_yuan(previous),
                )
            # 没领过，就按红包当前状态返回“已抢完/已过期”这类终态提示
            return self._terminal_result(packet.status)

        # 红包还能领：走 Redis 原子脚本，查重 + 弹一份金额 + 写领取记+录
        claim = await self.redis_store.claim(request.red_packet_id, request.user_id)
        if claim.code == -2:
            # Redis 池子空了，但数据库状态可能还没更新，重新查一次再给终态
            current = await self._packet(request.red_packet_id)
            return self._terminal_result(
                current.status if current is not None else int(RedPacketStatus.NOT_EXIST)
            )
        # 走到这里说明领到了（或之前领过），金额一定存在
        assert claim.amount is not None  # 断言：认领的金额不为空，不成立就报错
        # 把这次领取真正落库：插领取记录、给对方加余额、更新红包状态（内部幂等）
        # 返回 (实际入账金额或 None, 红包最新状态)
        amount, status = await self._settle_claim(
            red_packet_id=request.red_packet_id,
            user_id=request.user_id,
            amount=claim.amount,
        )
        if status is not RedPacketStatus.NOT_COMPLETED and amount is None:
            # 红包已到终态且这次没有实际入账（比如并发下已经被处理过），清掉 Redis 残留
            await self.redis_store.cleanup(request.red_packet_id)
            return self._terminal_result(int(status))
        if claim.code == -1:
            # -1 是重复领取：结算多半没实际入账，金额用 Redis 里的旧值兜底
            return ReceiveResult(
                status=status,
                message="您已经领取过该红包了",
                amount=to_yuan(amount or claim.amount),
            )
        # 正常领取成功
        return ReceiveResult(
            status=status,
            message="恭喜您，领取成功",
            amount=to_yuan(amount or claim.amount),
        )

    # 发送前校验：先查参数范围，再查用户状态，最后查单聊/群聊权限
    async def _validate_send(self, request: RedPacketSendRequest) -> None:
        # 这条消息必须是红包消息（接口只收 type=3 的消息）
        if request.type is not MessageType.RED_PACKET:
            raise RedPacketError(ErrorCode.PARAMS_ERROR, "红包消息类型必须为3")
        # 元换成分，后面所有金额比较都用“分”
        total_amount = to_fen(request.body.total_amount)
        # 金额要能塞进 BIGINT（2^63-1），否则数据库存不下
        if total_amount > 2**63 - 1:
            raise RedPacketError(ErrorCode.PARAMS_ERROR, "红包金额超出范围")
        # 份数上限 1 万，防止一个红包拆出天量记录
        if request.body.total_count > 10_000:
            raise RedPacketError(ErrorCode.PARAMS_ERROR, "红包数量超出范围")
        # 文案列是 VARCHAR(255)，超了数据库会截断/报错，提前拦
        if (
            request.body.red_packet_wrapper_text is not None
            and len(request.body.red_packet_wrapper_text) > 255
        ):
            raise RedPacketError(ErrorCode.PARAMS_ERROR, "红包文案过长")
        # client_message_id 列是 VARCHAR(128)，同理
        if request.client_message_id is not None and len(request.client_message_id) > 128:
            raise RedPacketError(ErrorCode.PARAMS_ERROR, "clientMessageId过长")
        # 每份至少 1 分，总额比份数还少就分不出来
        if total_amount < request.body.total_count:
            raise RedPacketError(
                ErrorCode.PARAMS_ERROR,
                "红包总金额不能少于红包数量（每个红包至少1分）",
            )
        # 平均每份不能超过 200 元（20000 分），防止单个红包金额过大
        if total_amount > request.body.total_count * 20_000:
            raise RedPacketError(ErrorCode.PARAMS_ERROR, "单个红包金额不能超过200元")
        # 先查发送者状态：不存在 / 被封禁 / 已注销都不让发
        state = await self.users.user_state(request.sender_id)
        if state is None:
            raise RedPacketError(ErrorCode.NOT_FOUND_ERROR, "发送者用户不存在")
        if state is UserState.BANNED:
            raise RedPacketError(ErrorCode.SENDER_DISABLED, "您的账号已被封禁，无法发送红包")
        if state is UserState.CANCELLED:
            raise RedPacketError(ErrorCode.SENDER_DISABLED, "您的账号已注销，无法发送红包")
        # 单聊红包：必须指定接收人，而且只能是普通红包
        if request.session_type is SessionType.DIRECT:
            if request.receiver_id is None:
                raise RedPacketError(ErrorCode.PARAMS_ERROR, "单聊接收者ID为空")
            if request.body.red_packet_type is not RedPacketType.NORMAL:
                raise RedPacketError(ErrorCode.REDPACKET_SINGLE_ONLY_NORMAL, "单聊只能发送普通红包")
            # 接收人也要存在且状态正常
            receiver_state = await self.users.user_state(request.receiver_id)
            if receiver_state is None:
                raise RedPacketError(ErrorCode.NOT_FOUND_ERROR, "接收者用户不存在")
            if receiver_state is not UserState.NORMAL:
                raise RedPacketError(ErrorCode.RECEIVER_DISABLED, "接收者账号异常")
            # 再让用户服务校验一遍单聊关系（好友/拉黑/会话是否有效等）
            # 返回 (是否允许, 错误码, 提示文案)；不允许就把对方的错误原样抛出去
            allowed, code, message = await self.users.validate_direct(
                sender_id=request.sender_id,
                receiver_id=request.receiver_id,
                session_id=request.session_id,
            )
            if not allowed:
                raise RedPacketError(code or ErrorCode.VALIDATION_FAILED, message or "消息校验失败")
        # 群红包：不认单个接收人，重点校验“是不是群成员 + 份数别超群人数”
        elif request.session_type is SessionType.GROUP:
            # 群红包的接收人没有意义，传了也清掉，免得后面误用
            request.receiver_id = None
            if not await self.users.group_membership(request.sender_id, request.session_id):
                raise RedPacketError(ErrorCode.NOT_GROUP_MEMBER, "您不是该群成员，无法发送红包")
            member_count = await self.users.group_member_count(request.session_id)
            # 份数比群人数多，注定有人领不到，直接拒绝并告诉他当前人数
            if request.body.total_count > member_count:
                raise RedPacketError(
                    ErrorCode.REDPACKET_COUNT_EXCEED_MEMBERS,
                    f"红包数量不能超过群成员数量（当前群成员数：{member_count}）",
                )
        else:
            # 其他会话类型（比如系统会话）不支持发红包
            raise RedPacketError(ErrorCode.PARAMS_ERROR, "红包仅支持单聊或群聊")

        # 是否可以领取红包
    async def _can_receive(self, user_id: int, packet: RedPacket) -> bool:
        if packet.session_type == int(SessionType.DIRECT):
            return user_id == packet.receiver_id
        return await self.users.group_membership(user_id, packet.session_id)

    # 领不了时的具体原因
    @staticmethod  # 静态方法: 不需要 self
    # 领不了时的具体原因
    def _receive_denied(user_id: int, packet: RedPacket) -> RedPacketError:
        if packet.session_type != int(SessionType.DIRECT):
            code = ErrorCode.REDPACKET_NOT_IN_CONVERSATION  # 码: 不在该会话中
        elif user_id == packet.sender_id:
            code = ErrorCode.REDPACKET_SENDER_CANNOT_RECEIVE  # 码: 不能领自己发的
        else:
            code = ErrorCode.REDPACKET_NOT_THE_RECEIVER  # 码: 不是发给你的
        return RedPacketError(code, error_message(code))

    # 结算领取：把 Redis 抢到的金额真正落库（锁行 -> 查重 -> 记账 -> 判完成）
    # 返回 (实际入账金额或 None, 红包最新状态)
    async def _settle_claim(
        self,
        *,  # 星号后面的参数只能按名字传
        red_packet_id: int,
        user_id: int,
        amount: int,
    ) -> tuple[int | None, RedPacketStatus]:
        current = now()
        # 打开会话并开启事务：正常退出自动提交，抛异常自动回滚
        async with self.sessions() as session, session.begin():
            # 关键：先把红包这一行锁住（SELECT ... FOR UPDATE）
            # 同一个红包的结算会被这把行锁串行化，后面统计已领金额/份数才是准的
            packet = await session.scalar(
                select(RedPacket).where(RedPacket.red_packet_id == red_packet_id).with_for_update()
            )
            if packet is None:
                return None, RedPacketStatus.NOT_EXIST
            # 幂等检查：这个人是不是已经有领取记录了
            existing = await session.scalar(
                select(RedPacketReceive).where(
                    RedPacketReceive.red_packet_id == red_packet_id,
                    RedPacketReceive.receiver_id == user_id,
                )
            )
            if existing is not None:
                # 已经领过：不再入账，把当时的金额和当前状态还回去
                return existing.amount, RedPacketStatus(packet.status)
            # 红包已经领完/过期，就不再处理
            if packet.status != int(RedPacketStatus.NOT_COMPLETED):
                return None, RedPacketStatus(packet.status)
            # 数据库里已经过了有效期：顺手结算成“过期”（后续处理在 _expire_locked 里）
            if packet.expires_at is not None and packet.expires_at <= current:
                await self._expire_locked(session, packet, current)
                return None, RedPacketStatus.EXPIRED
            # 统计已经领走多少钱：没记录时 SUM 是 NULL，用 coalesce 兜成 0
            received_amount = int(
                await session.scalar(
                    select(func.coalesce(func.sum(RedPacketReceive.amount), 0)).where(
                        RedPacketReceive.red_packet_id == red_packet_id
                    )
                )
                or 0
            )
            # 统计已经领走多少份
            received_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(RedPacketReceive)  # 从领取记录表里数
                    .where(RedPacketReceive.red_packet_id == red_packet_id)  # 只数这个红包的
                )
                or 0
            )
            # 兜底校验：已发金额 + 这次金额不能超过红包总额，超了说明前面的账已经错了
            if received_amount + amount > packet.total_amount:
                raise RedPacketError(ErrorCode.SYSTEM_ERROR, "红包金额校验失败")
            # 写一条领取记录
            session.add(
                RedPacketReceive(
                    red_packet_receive_id=self.ids.next_id(),
                    red_packet_id=red_packet_id,
                    receiver_id=user_id,
                    amount=amount,
                    received_at=current,
                    created_time=current,
                    updated_time=current,
                )
            )
            # 给领取人加余额（由 _credit 负责把余额更新/建户）
            await self._credit(session, user_id, amount, current)
            # 记一条余额流水：正数表示收入
            self._add_log(
                session,
                user_id=user_id,
                amount=amount,
                kind=BalanceLogType.RECEIVE,
                red_packet_id=red_packet_id,
                at=current,
            )
            # 组装“红包被领取”的事件
            receive_event = RedPacketReceived(
                user_id=user_id,
                red_packet_id=red_packet_id,
                received_amount=amount,
                receive_time=int(current.timestamp() * 1000),  # 毫秒时间戳
            )
            add_event(  # 写进 Outbox，跟上面这些数据库操作同事务提交
                session,
                event_id=f"rp-receive:{red_packet_id}:{user_id}",  # 事件ID带用户，防重复投递
                aggregate_id=red_packet_id,
                event_type="RedPacketReceived",
                topic=str(KafkaTopic.RED_PACKET_RECEIVE),
                key=str(red_packet_id),
                payload=receive_event.to_wire(),
            )
            # 注意：并发下 Redis 的弹出顺序和 MySQL 的落库顺序可能不一样，
            # 所以“是否领完”不能信 Redis 的 completed，只能信这里锁住的数据库状态。
            # 已领份数 + 这次 1 份 == 总份数，就是刚好领完
            is_completed = received_count + 1 == packet.total_count
            if is_completed:
                # 领完时必须守恒：之前领的 + 这次领的 == 红包总额
                if received_amount + amount != packet.total_amount:
                    raise RedPacketError(ErrorCode.SYSTEM_ERROR, "红包资金分配不守恒")
                packet.status = int(RedPacketStatus.COMPLETED)
                packet.updated_time = current
                # 再写一条“红包完成”的事件
                completed_event = RedPacketCompleted(red_packet_id=red_packet_id)
                add_event(  # 同样写进 Outbox，跟业务数据一起提交
                    session,
                    event_id=f"rp-complete:{red_packet_id}",  # 一个红包只会有一次完成事件
                    aggregate_id=red_packet_id,
                    event_type="RedPacketCompleted",
                    topic=str(KafkaTopic.RED_PACKET_COMPLETED),
                    key=str(red_packet_id),
                    payload=completed_event.to_wire(),
                )
                return amount, RedPacketStatus.COMPLETED
            # 还没领完：返回这次的金额和“待领取”状态
            return amount, RedPacketStatus.NOT_COMPLETED
    

    # 过期结算：把没领完的钱退给发送人，并把红包标成“过期”
    # 方法名带 _locked：调用前必须已经锁住这个红包行（SELECT ... FOR UPDATE），
    # 这里不再重复加锁，由调用方保证事务还没结束
    async def _expire_locked(
        self,
        session: AsyncSession,
        packet: RedPacket,
        current: datetime,
    ) -> None:
        # 算一下已经领走多少：一条记录都没有时 SUM 返回 NULL，用 coalesce 兜底成 0
        received = int(
            await session.scalar(
                select(func.coalesce(func.sum(RedPacketReceive.amount), 0)).where(
                    RedPacketReceive.red_packet_id == packet.red_packet_id
                )
            )
            or 0
        )
        # 剩下的就是该退的钱：总金额 - 已领金额
        refund = packet.total_amount - received
        if refund < 0:
            # 已领金额竟然比总额还多，说明账已经乱了，直接报系统错误
            raise RedPacketError(ErrorCode.SYSTEM_ERROR, "红包资金对账失败")
        if refund:
            # 还有剩钱：原路退回发送人余额
            await self._credit(session, packet.sender_id, refund, current)
            # 记一条退款流水；refund 为 0 时不用记，也没钱可退
            self._add_log(
                session,
                user_id=packet.sender_id,
                amount=refund,
                kind=BalanceLogType.REFUND,
                red_packet_id=packet.red_packet_id,
                at=current,
            )
        # 把红包状态改成“过期”，更新时间也刷新一下
        packet.status = int(RedPacketStatus.EXPIRED)
        packet.updated_time = current

    # 给余额加钱：有账户就加，没账户就顺手建一个（MySQL 的原子 upsert）
    async def _credit(
        self,
        session: AsyncSession,
        user_id: int,
        amount: int,
        current: datetime,
    ) -> None:
        # 先按“插入”来拼：用户还没有余额记录时，就插一条 balance=amount 的新行
        statement = mysql_insert(UserBalance).values(
            user_id=user_id,
            balance=amount,
            created_time=current,
            updated_time=current,
        )
        # 如果主键已存在（用户有账户了），就改成 balance = balance + amount
        # “判断有没有账户 + 加钱”在数据库里一次完成，并发下也不会丢更新
        await session.execute(
            statement.on_duplicate_key_update(
                balance=UserBalance.balance + amount,
                updated_time=current,
            )
        )

    # 记一条余额流水：只往 session 里 add，等外层事务一起提交
    def _add_log(
        self,
        session: AsyncSession,
        *,  # 星号后面的参数只能按名字传
        user_id: int,
        amount: int,
        kind: BalanceLogType,
        red_packet_id: int,
        at: datetime,
    ) -> None:
        session.add(  # 放进会话, 提交时写库
            BalanceLog(
                balance_log_id=self.ids.next_id(),  # 流水ID：雪花ID
                user_id=user_id,  # 谁的余额变了
                amount=amount,  # 正数入账、负数支出，由调用方传进来
                type=int(kind),  # 流水类型：发红包/领红包/退款，库里是 TINYINT
                related_id=red_packet_id,  # 关联红包ID；配合 (user_id,type,related_id) 唯一约束防重复记账
                created_time=at,  # 用同一个业务时间，保证一笔事务里的时间一致
                updated_time=at,
            )
        )

    # 写发送红包时要发的消息事件：一条“聊天消息”存库+推送，一条“红包创建”事件
    # 全部通过 Outbox 跟业务数据同事务落库，提交后再由发布器发 Kafka
    def _add_send_events(
        self,
        session: AsyncSession,
        *,  # 星号后面的参数只能按名字传
        request: RedPacketSendRequest,
        red_packet_id: int,
        message_id: int,
        created: datetime,
    ) -> None:
        # 先组装一条“聊天消息”：红包在会话里就是一条 type=RED_PACKET 的消息
        message = MessageRequest(
            session_id=request.session_id,  # 消息发到哪个会话
            receiver_id=request.receiver_id,  # 接收人；群聊时是 None（前面校验时已清空）
            sender_id=request.sender_id,  # 发送人
            type=MessageType.RED_PACKET,  # 消息类型：红包
            session_type=request.session_type,  # 会话类型：单聊/群聊
            created_time=int(created.timestamp() * 1000),  # 毫秒时间戳，跟客户端统一
            message_id=message_id,  # 服务端分配的消息ID
            body=MessageBody(
                red_packet_id=str(red_packet_id),  # 消息体里带红包ID，点开才能查详情
                red_packet_wrapper_text=request.body.red_packet_wrapper_text,  # 红包上的祝福文案
            ),
            client_message_id=request.client_message_id,  # 客户端消息ID，方便前端去重/对账
        )
        # 转成可 JSON 序列化的 dict；exclude_none 去掉空字段（群聊的 receiver_id 等），payload 更小
        payload = message.to_wire(exclude_none=True)
        # 同一条消息要发两个 topic：
        #   store：给离线数据服务落库
        #   push ：给实时服务推给在线用户
        # key 决定分区：store 不关心顺序用 None；push 用 session_id，保证同一会话有序
        for suffix, topic, key in (
            ("store", KafkaTopic.STORE, None),
            ("push", KafkaTopic.PUSH, str(request.session_id)),
        ):
            add_event(  # 写进 Outbox，跟红包记录同事务提交
                session,
                event_id=f"rp-{suffix}:{red_packet_id}",  # 同一红包两个事件ID不同，避免主键冲突
                aggregate_id=red_packet_id,
                event_type="RedPacketMessage",
                topic=str(topic),
                key=key,
                payload=payload,
            )
        # 再记一条“红包创建成功”的领域事件，给其他业务（统计、风控等）用
        created_event = RedPacketCreated(
            red_packet_id=red_packet_id,
            create_time=int(created.timestamp() * 1000),
        )
        add_event(  # 同样进 Outbox，跟业务数据一起提交
            session,
            event_id=f"rp-created:{red_packet_id}",  # 一个红包只创建一次
            aggregate_id=red_packet_id,
            event_type="RedPacketCreated",
            topic=str(KafkaTopic.RED_PACKET_CREATION),
            key=str(red_packet_id),
            payload=created_event.to_wire(),
        )

    # 按主键查红包：开一个短会话，查完自动关闭
    async def _packet(self, red_packet_id: int) -> RedPacket | None:
        async with self.sessions() as session:  # 开一个数据库会话，用完自动关闭
            return await session.get(RedPacket, red_packet_id)

    # 把“不能正常领取”的状态翻译成给前端的提示（不带金额）
    @staticmethod  # 静态方法：不需要 self
    def _terminal_result(status: int) -> ReceiveResult:
        # 数据库里存的是 int，先转成枚举再查文案
        normalized = RedPacketStatus(status)
        messages = {
            RedPacketStatus.NOT_EXIST: "红包不存在",
            RedPacketStatus.NOT_COMPLETED: "红包暂时无法领取",  # 兜底文案，正常流程不会走到这里
            RedPacketStatus.COMPLETED: "红包已被领完",
            RedPacketStatus.EXPIRED: "红包已过期",
        }
        # 统一返回：只有状态和提示，没有 amount
        return ReceiveResult(status=normalized, message=messages[normalized])