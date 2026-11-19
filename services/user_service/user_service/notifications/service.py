"""Build compatible notifications and persist them atomically in the outbox."""
# 组装通知消息，并且通过发件箱原子落库，保证通知可靠推送
import time

from infinitechat.contracts.events import FriendRequestCreated, KafkaTopic
from infinitechat.contracts.websocket import (
    FriendApplicationNotification,
    FriendApplicationNotificationBody,
    NewGroupSessionNotification,
    NewGroupSessionNotificationBody,
    NewSessionNotification,
    NewSessionNotificationBody,
)
from infinitechat.foundation.ids import SnowflakeGenerator

from user_service.outbox import OutboxRepository

# 通知服务，专门生成各类websocket推送通知，走outbox发件箱下发
class NotificationService:
    # 初始化
    # 注入outbox发件箱仓储（用来写消息事件）、雪花id生成器（生成消息id）
    def __init__(self, outbox: OutboxRepository, ids: SnowflakeGenerator) -> None:
        self._outbox = outbox
        self._ids = ids

    # 好友申请通知：别人发加好友申请的时候，给接收方推送通知
    def friend_application(
        self,
        *,  # * 代表后面所有参数必须关键字传参，防止参数顺序搞混
        sender_id: int,      # 申请人ID
        receiver_id: int,    # 接收这条申请的人，也就是要收到通知的人
        nickname: str,       # 申请人昵称，前端展示用
        avatar: str | None,  # 申请人头像，可能为空
        message: str,        # 附加申请留言，比如“你好，交个朋友”
    ) -> None:
        # 获取当前时间，转成毫秒时间戳
        now_ms = time.time_ns() // 1_000_000
        # 组装好友申请通知对象，是给前端websocket的结构体
        notification = FriendApplicationNotification(
            message_id=self._message_id(now_ms), # 这条通知的唯一消息id
            sender_id=sender_id,
            receiver_id=receiver_id,
            timestamp=now_ms, # 通知生成时间（毫秒）
            body=FriendApplicationNotificationBody(
                nickname=nickname,
                avatar=avatar,
                msg=message,
            ),
        )
        # 把通知序列化，写入outbox发件箱，后台任务会推送到websocket网关
        self._add_notification(notification.message_id, receiver_id, notification.to_wire())

    # 新建单聊会话通知：好友同意之后，创建私聊会话，推送通知给对方
    def new_direct_session(
        self,
        *,
        sender_id: int,        # 触发会话创建的用户
        receiver_id: int,      # 接收这条会话通知的用户
        session_id: int,       # 私聊会话id
        session_name: str,     # 会话名称
        avatar: str | None,    # 会话头像
    ) -> None:
        # 获取当前毫秒时间戳
        now_ms = time.time_ns() // 1_000_000
        # 组装单聊会话通知结构体
        notification = NewSessionNotification(
            message_id=self._message_id(now_ms),
            session_id=session_id,
            sender_id=sender_id,
            receiver_id=receiver_id,
            timestamp=now_ms,
            # 通知里面的内容体，前端拿到之后渲染会话
            body=NewSessionNotificationBody(session_name=session_name, avatar=avatar),
        )
        # 序列化通知，写入outbox发件箱
        self._add_notification(notification.message_id, receiver_id, notification.to_wire())
    # 群聊会话通知：创建群聊、有人被拉进群的时候，给成员推送通知
    def new_group_session(
        self,
        *,  # * 代表后面所有参数必须关键字传参，防止参数顺序搞混
        receiver_id: int,      # 接收这条群通知的用户ID
        session_id: int,       # 群会话的唯一ID
        session_name: str,     # 群名称
        avatar: str | None,    # 群头像，为空就传None
        creator_id: int,       # 建群人的用户ID
        members_count: int,    # 当前群里面一共有多少人
    ) -> None:
        # 获取当前时间，转成毫秒时间戳
        now_ms = time.time_ns() // 1_000_000
        # 组装群会话通知对象，给前端websocket用
        notification = NewGroupSessionNotification(
            message_id=self._message_id(now_ms), # 这条通知的唯一消息ID
            session_id=session_id,
            sender_id=None,  # 群通知没有单独发送人，填null
            receiver_id=receiver_id,
            timestamp=now_ms, # 通知生成的毫秒时间戳
            body=NewGroupSessionNotificationBody(
                session_name=session_name,
                avatar=avatar,
                creator_id=creator_id,
                members_count=members_count,
            ),
        )
        # 把通知转成可传输结构，写入outbox发件箱，后台消费推送给网关
        self._add_notification(notification.message_id, receiver_id, notification.to_wire())

    # 好友申请事件
    def friend_request_created(self, apply_friend_id: int, *, now_ms: int | None = None) -> None:
        # 创建时间: 当前毫秒时间 或者 取当前纳秒时间 // 1000000
        create_time = now_ms or time.time_ns() // 1_000_000
        event = FriendRequestCreated(
            apply_friend_id=apply_friend_id,
            create_time=create_time,
            expire_time=create_time + 24 * 60 * 60 * 1000,
        )
        # 事件ID: 字符串 "friend-created-{好友申请ID..."
        event_id = f"friend-created-{apply_friend_id}-{create_time}"
        self._outbox.add(  # 加进去
            event_id=event_id,
            aggregate_type="friend_request",
            aggregate_id=str(apply_friend_id),
            event_type="FriendRequestCreated",
            topic=KafkaTopic.FRIEND_REQUEST_CREATION,
            event_key=str(apply_friend_id),
            payload=event.to_wire(),
            headers={"event-id": event_id, "schema-version": "1"},
        )

    # 写入通知事件
    def _add_notification(
        self,
        message_id: str,
        receiver_id: int,
        payload: dict[str, object],
    ) -> None:
        self._outbox.add(  # 加进去
            event_id=message_id,
            aggregate_type="system_notification",
            aggregate_id=str(receiver_id),
            event_type="SystemNotification",
            topic=KafkaTopic.SYSTEM_NOTIFICATION,
            event_key=str(receiver_id),
            payload=payload,
            headers={"event-id": message_id, "schema-version": "1"},
        )

    def _message_id(self, now_ms: int) -> str:
        return f"msg_{now_ms}_{self._ids.next_id()}"

