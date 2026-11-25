"""RedPacketService persistence models; schema changes live in Alembic."""
# 红包服务的数据库模型：这里只描述 ORM 映射，真正的建表/改表交给 Alembic 迁移

from datetime import datetime  # 时间类型：映射 DATETIME 字段
from typing import Any  # 任意类型：后面 JSON 字段/通用值会用到

from sqlalchemy import (
    JSON,  # JSON 列类型：存结构化数据
    BigInteger,  # 大整数：雪花 ID 用
    CheckConstraint,  # 检查约束：比如金额不能为负
    DateTime,  # 日期时间类型
    Index,  # 普通索引
    Integer,  # 整数
    String,  # 变长字符串
    Text,  # 长文本
    UniqueConstraint,  # 唯一约束
    func,  # SQL 函数：比如 CURRENT_TIMESTAMP
    text,  # 原生 SQL 片段
)

from sqlalchemy.dialects.mysql import TINYINT  # MySQL 的 TINYINT：存状态、小整数更省空间

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column  # ORM 基类、类型化字段声明

# 所有表共用的表选项: utf8mb4 字符集和 utf8mb4_0900_ai_ci 排序规则
MYSQL_TABLE_OPTIONS = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}

# 红包表的 ORM 基类：本服务自己建的表都继承它
# Alembic 只登记这个基类的元数据，所以迁移只会管红包自己的表
class Base(DeclarativeBase):
    pass  # 什么都不做, 只占个位置

# 共用表的 ORM 基类：只用来映射别的服务已经建好的表（比如 user、session）
# 不登记进迁移环境，避免红包服务重复建别人的表
class SharedTableBase(DeclarativeBase):
    pass  # 什么都不做, 只占个位置

# 创建/更新时间字段
class TimestampMixin:
    # 创建时间：DATETIME，不能为空，插入时数据库自动填当前时间
    created_time: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    )
    # 更新时间：DATETIME，不能为空
    updated_time: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        # 库默认值：当前时间；每次 UPDATE 时数据库自动刷新
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
    )

# 用户余额
class UserBalance(TimestampMixin, Base):
    __tablename__ = "user_balance"  # 表名：每个用户一行余额
    __table_args__ = (  # 表参数, 见下面几行
        # 余额不能为负，数据库层兜底，防止程序算错写进负数
        CheckConstraint("balance >= 0", name="ck_user_balance_nonnegative"),
        MYSQL_TABLE_OPTIONS,  # 最后一项: 表选项（字符集/排序规则，必须放最后）
    )

    # 用户ID：主键，雪花算法生成，不靠数据库自增
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    # 余额：不能为空，库里默认值 0（单位：分，避免小数）
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))

# 余额变动流水
class BalanceLog(TimestampMixin, Base):
    __tablename__ = "balance_log"  # 表名：每笔余额变动一行
    __table_args__ = (
        # 同一个用户 + 同一种类型 + 同一个业务ID 只能记一条，防止重复入账
        UniqueConstraint("user_id", "type", "related_id", name="uq_balance_log_business"),
        # 按用户查流水：走这个索引
        Index("ix_balance_log_user", "user_id"),
        # 按业务ID反查：比如“这个红包相关流水”
        Index("ix_balance_log_related", "related_id"),
        MYSQL_TABLE_OPTIONS,  # 最后一项: 表选项
    )

    # 余额流水ID：主键，雪花算法生成，不靠数据库自增
    balance_log_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    # 用户ID：不能为空，这笔流水属于谁
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 金额：不能为空，正数表示入账、负数表示扣减（单位：分）
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 类型：不能为空，区分是充值、发红包扣款、领红包入账、退款等
    type: Mapped[int] = mapped_column(TINYINT, nullable=False)
    # 关联业务ID：比如红包ID、订单ID，可空（有些流水不需要关联）
    related_id: Mapped[int | None] = mapped_column(BigInteger)



# 红包主表
class RedPacket(TimestampMixin, Base):
    __tablename__ = "red_packet"
    __table_args__ = (
        UniqueConstraint(
            "sender_id",
            "client_message_id",
            name="uq_red_packet_sender_client_message",
        ),
        CheckConstraint("total_amount > 0", name="ck_red_packet_total_amount"),
        CheckConstraint("total_count > 0", name="ck_red_packet_total_count"),
        Index("ix_red_packet_session_created", "session_id", "created_time"),
        Index("ix_red_packet_status_expires", "status", "expires_at"),
        MYSQL_TABLE_OPTIONS,  # 最后一项: 表选项
    )

    # 红包ID：主键
    red_packet_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    # 发送人ID：不能为空
    sender_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 接收人ID：整数(可为空)
    receiver_id: Mapped[int | None] = mapped_column(BigInteger)
    # 会话ID：不能为空
    session_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 会话类型：不能为空
    session_type: Mapped[int] = mapped_column(TINYINT, nullable=False)
    # 红包文案：字符串(可为空)，创建string
    red_packet_wrapper_text: Mapped[str | None] = mapped_column(String(255))
    # 红包类型：不能为空
    red_packet_type: Mapped[int] = mapped_column(TINYINT, nullable=False)
    # 总金额：不能为空
    total_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 总份数：INT，不能为空
    total_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # 状态：不能为空，库里默认值 原样 SQL
    status: Mapped[int] = mapped_column(TINYINT, nullable=False, server_default=text("0"))
    # 客户端消息ID：字符串(可为空)，创建string
    client_message_id: Mapped[str | None] = mapped_column(String(128))
    # 消息ID：整数(可为空)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    # 过期时间：日期时间(可为空)，DATETIME
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)

# 红包领取记录
class RedPacketReceive(TimestampMixin, Base):
    __tablename__ = "red_packet_receive"
    __table_args__ = (
        UniqueConstraint("red_packet_id", "receiver_id", name="uq_red_packet_receive_user"),
        Index("ix_red_packet_receive_packet_time", "red_packet_id", "received_at"),
        MYSQL_TABLE_OPTIONS,  # 最后一项: 表选项
    )

    # 红包领取ID：主键
    red_packet_receive_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    # 红包ID：不能为空
    red_packet_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 接收人ID：不能为空
    receiver_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 金额：不能为空
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 领取时间：DATETIME，不能为空
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

# Outbox 待发布事件表：表结构是 user 服务那边建好的（0001 迁移），这边只借来读写
# 所以挂在共用表基类 SharedTableBase 上，不参与本服务的迁移，避免重复建表
class OutboxEvent(SharedTableBase):
    __tablename__ = "outbox_event"  # 表名：每一条待发/已发事件一行

    # 事件ID：主键，字符串（一般是 UUID），全局唯一，不靠数据库自增
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # 来源类型：这条事件是谁产生的，比如 "red_packet"、"user"
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # 来源ID：具体是哪条业务记录，比如某个红包ID
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # 事件类型：到底发生了什么事，比如 "red_packet.created"
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    # 目标 topic：这条消息将来要发到哪个 Kafka 主题
    topic: Mapped[str] = mapped_column(String(128), nullable=False)
    # 消息键（分区键）：同一个业务的消息落到同一分区，可为空
    event_key: Mapped[str | None] = mapped_column(String(128))
    # 消息体：真正要发给 Kafka 的 JSON 内容
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # 消息头：附加的 headers，可为空，不是每条消息都需要
    headers: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # 状态：待发布 pending / 已发布 published / 失败 failed，库里默认给 'pending'
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'pending'"),
    )
    # 尝试次数：发过几次，库里默认 0；重试一次加一，用来限制不能无限重试
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # 可发布时间：到这个时间点才能被捞去发。默认当前时间；失败重试时可以往后推（延迟重试）
    available_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    )
    # 创建时间：插入时数据库自动填当前时间
    created_time: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    )
    # 发布时间：真正发出去的那一刻，还没发就是 NULL
    published_time: Mapped[datetime | None] = mapped_column(DateTime)
    # 最后一次错误：发送失败时把异常信息写这里，方便事后排查，可为空
    last_error: Mapped[str | None] = mapped_column(Text)