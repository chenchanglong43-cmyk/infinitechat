"""aiokafka producer owned by the User worker process."""
# 用户工作进程专用的aiokafka生产者

import json  # 用来序列化消息体成json
from typing import Any  # 任意类型注解

from aiokafka import AIOKafkaProducer  # type: ignore[import-untyped]
# 异步kafka生产者库

# 封装Kafka生产者，给用户服务发消息用
class UserKafkaProducer:
    # 构造方法，创建实例的时候把kafka连接信息存好
    def __init__(self, bootstrap_servers: str, *, client_id: str) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap_servers,  # kafka集群地址
            client_id=client_id,  # 客户端标识
            enable_idempotence=True,  # 开启幂等，防止重复发消息
            request_timeout_ms=10_000,  # 请求超时10秒
        )

    # 启动生产者连接
    async def start(self) -> None:
        await self._producer.start()

    # 发送消息，*代表后面参数必须关键字传参
    async def send(
        self,
        *,
        topic: str,  # 要发到哪个主题
        key: str | None,  # kafka消息key，用来分区，可为空
        payload: dict[str, Any],  # 消息主体数据
        headers: dict[str, Any],  # 消息头，放追踪信息之类
    ) -> None:
        # 把消息头转成kafka要求的二元组字节格式
        encoded_headers = [
            (str(header_key), str(value).encode())
            for header_key, value in headers.items()
        ]
        # 阻塞等待消息发送成功
        await self._producer.send_and_wait(
            topic,
            key=key.encode() if key is not None else None,  # key转字节，空就传None
            # 字典转紧凑json再编码成字节，不强制转ascii
            value=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
            headers=encoded_headers,
        )

    # 关闭生产者，释放连接
    async def stop(self) -> None:
        await self._producer.stop()