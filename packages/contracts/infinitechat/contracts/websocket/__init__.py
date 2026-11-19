"""WebSocket chat and system-notification contracts."""

from infinitechat.contracts.websocket.notifications import (
    FriendApplicationNotification,
    FriendApplicationNotificationBody,
    NewGroupSessionNotification,
    NewGroupSessionNotificationBody,
    NewSessionNotification,
    NewSessionNotificationBody,
    SystemNotification,
    parse_system_notification,
)

__all__ = (
    "FriendApplicationNotification",
    "FriendApplicationNotificationBody",
    "NewGroupSessionNotification",
    "NewGroupSessionNotificationBody",
    "NewSessionNotification",
    "NewSessionNotificationBody",
    "SystemNotification",
    "parse_system_notification",
)