"""UserService-owned persistence models; schema changes live in Alembic."""
# UserService 数据库ORM模型，表结构变更统一走Alembic迁移脚本，不要直接改库

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,  # 定长字符串，手机号专用
    BigInteger,  # 大整数，用户主键
    DateTime,  # 日期时间类型
    Index,  # 普通索引
    Integer,
    String,  # 变长字符串
    Text,  # 大文本，适合简介这类长内容
    UniqueConstraint,  # 唯一约束
    func,
    text,  # 原生SQL片段
)
from sqlalchemy.dialects.mysql import JSON, TINYINT  # MySQL专属类型
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.elements import quoted_name

# 所有表共用配置：utf8mb4 支持emoji，配套排序规则
MYSQL_TABLE_OPTIONS = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}

# ORM模型基类，所有业务表都继承这个基类
class Base(DeclarativeBase):
    pass

# 时间戳混入类，需要创建时间、更新时间的表直接继承它
class TimestampMixin:
    # 创建时间：数据库自动填充，插入时生成
    created_time: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    )
    # 更新时间：数据库自动更新，数据修改时自动刷新
    updated_time: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
    )

# 用户表
class UserModel(TimestampMixin, Base):
    # user是MySQL关键字，用quoted_name自动加反引号包裹，避免语法报错
    __tablename__ = quoted_name("user", quote=True)
    __table_args__ = (
        UniqueConstraint("email", name="uq_user_email"),  # 邮箱唯一，不能重复注册
        UniqueConstraint("phone", name="uq_user_phone"),  # 手机号唯一
        Index("ix_user_state", "state"),  # 用户状态索引，按状态筛选快
        Index("ix_user_role", "role"),  # 角色索引，区分管理员/普通用户
        Index("ix_user_created_time", "created_time"),  # 创建时间索引，分页按时间查
        MYSQL_TABLE_OPTIONS,
    )

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    phone: Mapped[str | None] = mapped_column(CHAR(11), nullable=True)  # 手机号，11位定长，可以为空
    email: Mapped[str] = mapped_column(String(128), nullable=False)  # 邮箱，必填
    # 密码，存bcrypt哈希，不存明文
    password: Mapped[str] = mapped_column(String(256), nullable=False)
    nickname: Mapped[str] = mapped_column(String(128), nullable=False)  # 昵称
    avatar: Mapped[str | None] = mapped_column(String(512), nullable=True)  # 头像链接
    # 性别，默认2=未知
    gender: Mapped[int] = mapped_column(TINYINT, nullable=False, server_default=text("2"))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)  # 个人简介
    # 用户状态，0正常
    state: Mapped[int] = mapped_column(TINYINT, nullable=False, server_default=text("0"))
    # 角色，0普通用户
    role: Mapped[int] = mapped_column(TINYINT, nullable=False, server_default=text("0"))
    is_delete: Mapped[int] = mapped_column(
        TINYINT,
        nullable=False,
        server_default=text("0"),  # 软删除标记，0未删除，1已删除
    )


# 好友表
class FriendModel(TimestampMixin, Base):
    __tablename__ = "friend"
    # 联合主键：user_id + friend_id，记录双向好友关系
    __table_args__ = (Index("ix_friend_friend_id", "friend_id"), MYSQL_TABLE_OPTIONS)

    # 用户ID，联合主键之一
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # 好友ID，联合主键之一
    friend_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # 好友状态：0正常，1拉黑等
    status: Mapped[int] = mapped_column(TINYINT, nullable=False, server_default=text("0"))

# 好友申请表
class ApplyFriendModel(TimestampMixin, Base):
    __tablename__ = "apply_friend"
    __table_args__ = (
        # 同一个发送人对同一个接收人，只能存在一条待处理申请
        UniqueConstraint("sender_id", "receiver_id", name="uq_apply_friend_pair"),
        # 发送人+状态索引，方便查我发出的申请
        Index("ix_apply_friend_sender_status", "sender_id", "status"),
        # 接收人+状态索引，方便查别人发给我的申请
        Index("ix_apply_friend_receiver_status", "receiver_id", "status"),
        MYSQL_TABLE_OPTIONS,
    )

    # 申请记录主键
    apply_friend_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    # 申请人ID
    sender_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 接收人ID
    receiver_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 申请附言
    message: Mapped[str] = mapped_column(String(255), nullable=False)
    # 申请状态：0待处理，1同意，2拒绝
    status: Mapped[int] = mapped_column(TINYINT, nullable=False, server_default=text("0"))

# 会话表（单聊/群聊）
class SessionModel(TimestampMixin, Base):
    # session是MySQL关键字，用quoted_name包裹避免报错
    __tablename__ = quoted_name("session", quote=True)
    __table_args__ = MYSQL_TABLE_OPTIONS

    # 会话主键
    session_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    # 会话名称，群聊用，单聊可以为空
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 会话类型：1单聊，2群聊
    type: Mapped[int] = mapped_column(TINYINT, nullable=False)
    # 会话状态：0正常，1解散
    status: Mapped[int] = mapped_column(TINYINT, nullable=False, server_default=text("0"))
    # 会话头像，群聊头像
    avatar: Mapped[str | None] = mapped_column(String(512), nullable=True)

# 用户会话关联表：记录哪些用户在哪个会话里
class UserSessionModel(TimestampMixin, Base):
    __tablename__ = "user_session"
    __table_args__ = (Index("ix_user_session_session_id", "session_id"), MYSQL_TABLE_OPTIONS)

    # 用户ID，联合主键
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # 会话ID，联合主键
    session_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # 在会话里的角色：0普通成员，1群主，2管理员
    role: Mapped[int] = mapped_column(TINYINT, nullable=False)
    # 成员状态：0正常，1退出，2被踢出
    status: Mapped[int] = mapped_column(TINYINT, nullable=False, server_default=text("0"))


# 发件箱事件表（事务消息、Outbox模式）
class OutboxEventModel(Base):
    __tablename__ = "outbox_event"
    __table_args__ = (
        # 轮询任务索引：按状态+可发送时间捞待发布事件
        Index("ix_outbox_status_available", "status", "available_at"),
        MYSQL_TABLE_OPTIONS,
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)  # 事件唯一ID，全局唯一
    # 聚合根类型，比如user、friend
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)  # 聚合根ID，比如用户id
    # 事件名称，UserCreated、FriendApplied
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    topic: Mapped[str] = mapped_column(String(128), nullable=False)  # 投递到MQ的topic
    # MQ分区key，用于顺序消息
    event_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)  # 事件主体数据
    # 消息头，放追踪ID之类
    headers: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'pending'"),  # pending待发布 / success成功 / failed失败
    )
    # 重试次数
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),  # 最早可以投递的时间，用于延迟消息
    )
    created_time: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    )
    published_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 成功投递时刻
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)  # 最近一次失败原因