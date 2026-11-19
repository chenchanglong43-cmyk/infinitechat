"""Frozen topic and consumer-group names of the event contract."""

from enum import StrEnum

# Kafka Topic 枚举，这里放所有固定的topic名字，统一管理，避免到处写字符串写错
class KafkaTopic(StrEnum):
    STORE = "store-topic"
    PUSH = "push-topic"
    SYSTEM_NOTIFICATION = "system-notification-topic"  # 系统通知消息topic
    FRIEND_REQUEST_CREATION = "friend-request-creation-topic"  # 发起好友申请事件
    FRIEND_REQUEST_EXPIRATION = "friend-request-expiration-topic"  # 好友申请过期事件
    RED_PACKET_CREATION = "redpacket-creation-topic"  # 红包创建事件
    RED_PACKET_RECEIVE = "topic-redpacket-receive"  # 红包被领取事件
    RED_PACKET_COMPLETED = "topic-redpacket-completed"  # 红包全部领完、结束事件
    RED_PACKET_EXPIRATION = "redpacket-expiration-topic"  # 红包超时过期事件
    CANAL_CDC = "infinitechat-cdc"  # canal同步数据库变更的topic

# Kafka消费组枚举，每个业务消费者单独一个消费组，互不干扰
class ConsumerGroup(StrEnum):
    MESSAGE_STORE = "infinite-chat-store-group"  # 消息存储消费组
    MESSAGE_PUSH = "infinite-chat-push-group-0"  # 消息推送消费组
    SYSTEM_NOTIFICATION = "system-notification-consumer-group"  # 系统通知消费组
    FRIEND_REQUEST_ENQUEUER = "friend-request-enqueuer-group"  # 好友申请入队消费组
    FRIEND_REQUEST_EXECUTOR = "friend-request-executor-group"  # 好友申请处理执行消费组
    RED_PACKET_DELAY_ENQUEUER = "delay-task-enqueuer-group"  # 红包延迟任务入队消费组
    RED_PACKET_RECEIVE = "redpacket-receive-handler-group"  # 红包领取处理消费组
    RED_PACKET_COMPLETED = "redpacket-completed-handler-group"  # 红包完结处理消费组
    RED_PACKET_EXPIRATION = "expiration-task-executor-group"  # 过期任务执行消费组