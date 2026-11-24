"""Local WebSocket connections and their ownership in Redis."""

# 异步协程库
import asyncio
# json序列化，存redis用
import json
# 获取时间戳
import time
# 数据类装饰器
from dataclasses import dataclass

# fastapi的ws对象
from fastapi import WebSocket
# 异步redis客户端
from redis.asyncio import Redis


# WebSocket连接实体，slots节省内存
@dataclass(slots=True)
class Connection:
    user_id: int  # 用户id
    connection_id: str  # 这条连接唯一id
    websocket: WebSocket  # fastapi原生ws对象，用来发消息


# Lua脚本：原子释放用户连接归属，防止并发删错
# 逻辑：只有redis里存的instanceId、connectionId和当前传入一致，才删除key
_RELEASE_OWNER = """
local current = redis.call('GET', KEYS[1])
if not current then return 0 end
local value = cjson.decode(current)
if value.connectionId == ARGV[1] and value.instanceId == ARGV[2] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


# 本机ws连接管理器，管理当前这个服务实例上的所有长连接
class ConnectionManager:
    def __init__(
        self,
        redis: Redis,
        *,  # 后面参数强制关键字传参，防止传错顺序
        instance_id: str,
        ttl_seconds: int,
        send_timeout_seconds: float,
    ) -> None:
        self._redis = redis
        self.instance_id = instance_id  # 当前服务实例id
        self._ttl = ttl_seconds  # redis连接记录的过期时间
        self._send_timeout = send_timeout_seconds  # 推送消息超时时间
        # 本地内存映射：用户id -> 连接对象，只存本机的连接
        self._connections: dict[int, Connection] = {}
        self._lock = asyncio.Lock()  # 异步锁，保护本地连接字典并发读写

    # 新增一条ws连接，写入redis，同时覆盖这个用户旧的连接记录
    async def add(self, connection: Connection) -> Connection | None:
        async with self._lock:
            # 拿到用户之前在本机的连接
            previous = self._connections.get(connection.user_id)
            # 本地字典覆盖成新连接
            self._connections[connection.user_id] = connection
            # 写入redis，记录这个用户现在属于哪个实例、哪条连接
            await self._redis.set(
                self.connection_key(connection.user_id),
                json.dumps(
                    {
                        "instanceId": self.instance_id,
                        "connectionId": connection.connection_id,
                        "connectedAt": time.time_ns() // 1_000_000,  # 毫秒时间戳
                    },
                    separators=(",", ":"),
                ),
                ex=self._ttl,
            )
            return previous

    # 移除连接，lua脚本原子校验后删除redis归属，标记用户离线
    async def remove(self, connection: Connection) -> bool:
        async with self._lock:
            # 本地存的连接和传入的对不上，直接拒绝
            if self._connections.get(connection.user_id) is not connection:
                return False
            # 本地删掉这条连接
            self._connections.pop(connection.user_id, None)
            # 执行lua脚本，原子删除redis归属记录，避免并发误删
            await self._redis.eval(
                _RELEASE_OWNER,
                1,
                self.connection_key(connection.user_id),
                connection.connection_id,
                self.instance_id,
            )
            # 写离线时间标记
            await self._redis.set(
                f"user:offline:{connection.user_id}",
                str(time.time_ns() // 1_000_000),
            )
            return True

    # 给本机在线的用户推送消息
    async def send(self, user_id: int, payload: str) -> bool:
        # 先查本地有没有这个用户的连接
        connection = self._connections.get(user_id)
        if connection is None:
            return False
        try:
            # 带超时发ws文本，防止卡住协程
            await asyncio.wait_for(
                connection.websocket.send_text(payload),
                timeout=self._send_timeout,
            )
            return True
        except (OSError, RuntimeError, TimeoutError):
            # 连接断了、超时都算发送失败
            return False

    # 心跳刷新：给所有本机用户的redis连接记录续期，同时刷新实例存活标记
    async def refresh(self) -> None:
        # tuple复制一份，避免遍历的时候字典变更报错
        for connection in tuple(self._connections.values()):
            await self._redis.expire(self.connection_key(connection.user_id), self._ttl)
        # 标记当前realtime实例还活着
        await self._redis.set(
            f"realtime:instance:{self.instance_id}",
            str(time.time_ns() // 1_000_000),
            ex=self._ttl,
        )

    # 获取本机所有在线用户id
    def local_user_ids(self) -> tuple[int, ...]:
        return tuple(self._connections)

    # 静态方法，拼接redis key
    @staticmethod
    def connection_key(user_id: int) -> str:
        return f"realtime:connection:{user_id}"