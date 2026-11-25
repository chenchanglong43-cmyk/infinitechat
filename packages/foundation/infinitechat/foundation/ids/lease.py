"""Redis-backed lease that keeps snowflake worker IDs unique."""
# 基于 Redis 的租约：保证雪花算法的 worker_id 同一时刻只被一个进程占用

import asyncio  # 异步编程：等待停止事件、周期刷新
import secrets  # 安全随机数：给租约持有者生成一个随机后缀，避免撞名
from typing import Any, Protocol  # Any 表示任意类型；Protocol 用来定义“只要长得像就行”的接口

# 刷新脚本: 下面这段 Lua 脚本在 Redis 里一次性原子执行
# 逻辑：只有 key 里存的 owner 还是我，才给我续期；否则说明租约已经不是我的了
REFRESH_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

# 释放脚本: 下面这段 Lua 脚本在 Redis 里一次性原子执行
# 逻辑：只有 owner 还是我，才允许删除；避免误删别人的租约
RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

# 租约存储协议
# 租约存储协议 (协议: 只约定要有哪些方法)
class LeaseStore(Protocol):
    # 写 key；支持 nx（不存在才写）、ex（过期秒数）这些选项
    async def set(self, name: str, value: str, **options: Any) -> Any: ...

    # 执行 Lua 脚本；numkeys 表示前几个参数是 key，后面的都是参数
    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: object,  # 再乘以 表达式
    ) -> Any: ...

# 节点租约冲突
# 节点租约冲突 (自定义异常)
class SnowflakeNodeConflictError(RuntimeError):
    pass  # 什么都不做, 只占个位置

# Snowflake 节点租约
class SnowflakeNodeLease:
    # 初始化
    def __init__(  # 构造方法: 创建对象时把依赖存起来
        self,
        store: LeaseStore,  # Redis 客户端（或测试里的假实现）
        *,  # 星号后面的参数只能按名字传
        data_center_id: int,  # 数据中心编号
        worker_id: int,  # 机器/工作节点编号
        owner_label: str,  # 持有者标签，比如服务名或主机名
        ttl_seconds: int = 30,  # 租约有效期，默认 30 秒
    ) -> None:
        self.store = store  # 存下 Redis 客户端
        # 每个“数据中心 + 工作节点”对应一个 key，谁抢到谁才能用这个编号
        self.key = f"runtime:snowflake:lease:{data_center_id}:{worker_id}"
        # 持有者身份：标签 + 随机串，保证不同进程不会重名
        self.owner = f"{owner_label}:{secrets.token_hex(16)}"
        self.ttl_seconds = ttl_seconds  # 记下 TTL，后面续期用
        self.held = False  # 当前有没有拿到租约

    # 获取租约
    async def acquire(self) -> None:
        # SET key owner NX EX ttl：key 不存在才能写成功，写成功就代表抢到租约
        self.held = bool(
            await self.store.set(
                self.key,
                self.owner,
                nx=True,  # 只有 key 不存在时才写入
                ex=self.ttl_seconds,  # 自动过期，防止进程崩溃后永远占着
            )
        )
        # 没抢到：说明这个 worker_id 已经被别的实例占用
        if not self.held:
            raise SnowflakeNodeConflictError(f"Snowflake node is already leased: {self.key}")

    # 刷新租约
    async def refresh(self) -> bool:
        # 用 Lua 原子执行“确认 owner 是我 → 续期”，避免并发下误续别人的租约
        self.held = bool(
            await self.store.eval(
                REFRESH_SCRIPT,  # 刷新脚本
                1,  # 后面的参数里，第 1 个是 key
                self.key,  # KEYS[1]
                self.owner,  # ARGV[1]：我的身份
                self.ttl_seconds,  # ARGV[2]：续期秒数
            )
        )
        return self.held  # 返回续期成功与否

    # 释放租约
    async def release(self) -> None:
        if self.held:  # 只有自己持有的时候才需要释放
            # Lua 原子执行“确认 owner 是我 → 删除”，避免误删别人的租约
            await self.store.eval(RELEASE_SCRIPT, 1, self.key, self.owner)
        self.held = False  # 无论删没删，本地都标记为未持有

    # 周期续约直到停止
    async def maintain(self, stop: asyncio.Event) -> None:
        while not stop.is_set():  # 只要“停止事件”还没触发，就一直循环
            try:  # 下面这段可能出错, 出错交给 except 处理
                # 等待停止事件，最多等 TTL 的三分之一；超时就说明该续期了
                await asyncio.wait_for(stop.wait(), timeout=self.ttl_seconds / 3)
            except TimeoutError:  # 等超时了，说明还没收到停止信号，去续期
                if not await self.refresh():  # 续期失败：租约已经不属于我了
                    return  # 直接退出，上层应该停止再生成 ID，避免编号冲突
