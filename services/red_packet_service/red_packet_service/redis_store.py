"""Redis amount pool, claim scripts and expiry queue."""
# 红包金额池的 Redis 操作：预先把每份金额放进列表，抢的时候原子弹出一份
# 文件里放的是 Lua 脚本，脚本在 Redis 里整段执行，中间不会被别的请求插队

from dataclasses import dataclass  # 数据类：用来定义轻量的数据载体

from redis.asyncio import Redis  # 异步 Redis 客户端

# 原子领取：防重复、从金额池弹出一份、记录谁领了多少
# 下面这段 Lua 会在 Redis 里一次性执行完，相当于一个不可打断的事务
RECEIVE_SCRIPT = """
-- KEYS[1] = 金额池列表，KEYS[2] = 领取记录哈希（userId -> 金额）
-- ARGV[1] = 用户ID，ARGV[2] = 记录过期时间（秒）
local poolKey = KEYS[1]
local recordsKey = KEYS[2]
local userId = ARGV[1]
local ttl = tonumber(ARGV[2])
-- 先看这个用户是不是已经领过：领过就把上次的金额原样返回，状态码 -1
if redis.call('HEXISTS', recordsKey, userId) == 1 then
    return {-1, tonumber(redis.call('HGET', recordsKey, userId))}
end
-- 没领过，从池子左边弹出一份金额
local amount = redis.call('LPOP', poolKey)
-- 弹不出来说明池子空了，状态码 -2 表示已经被抢完
if not amount then
    return {-2, 0}
end
-- 记下这个用户领到的金额，并给整张记录表设置过期时间（每次领取都会把 TTL 刷新一遍）
redis.call('HSET', recordsKey, userId, amount)  -- 记录：这个用户领到了多少
redis.call('EXPIRE', recordsKey, ttl)
-- 弹出这一份后池子正好空了，就把“已领完”标记置 1
local completed = 0
if redis.call('LLEN', poolKey) == 0 then completed = 1 end
-- 返回 {领到的金额, 是否刚好领完}
return {tonumber(amount), completed}
"""

# 初始化金额池：把事先算好的每份金额塞进 Redis 列表，已存在就跳过（可重复调用）
# 同样是 Lua 脚本，保证初始化时不会并发插队
INIT_SCRIPT = """
local poolKey = KEYS[1]
-- 池子已经建过就直接返回 0，避免重复初始化把金额翻倍
if redis.call('EXISTS', poolKey) == 1 then return 0 end
-- 最后一个参数是过期时间，前面的参数全是金额，从右边一个一个追加
-- RPUSH 进、LPOP 出，正好是先进先出，领取顺序和放入顺序一致
for index = 1, #ARGV - 1 do redis.call('RPUSH', poolKey, ARGV[index]) end
-- 给整个池子设置过期时间，到点 Redis 自动清理，不用额外定时任务
redis.call('EXPIRE', poolKey, tonumber(ARGV[#ARGV]))
-- 返回实际放入了多少份
return #ARGV - 1
"""

# Redis 领取脚本的返回结果：把 Lua 返回的数组翻译成一个好用的对象
@dataclass(frozen=True, slots=True)  # 数据类：自动生成构造方法；frozen 创建后不能改，slots 省内存
class ClaimResult:
    code: int  # 状态码：0 领取成功，-1 之前已经领过，-2 红包已被抢完
    amount: int | None = None  # 领到的金额（分）；-2 时没有金额，所以可空
    completed: bool = False  # 这次领取是不是刚好把最后一份领走

# 红包在 Redis 里的读写入口：金额池、领取记录、到期队列都从这里操作
class RedPacketRedis:
    DUE_KEY = "redpacket-expire-zset"  # 到期队列的键：有序集合（zset），存该处理过期的红包
    INFLIGHT_KEY = "redpacket-expire-inflight-zset"  # 处理中队列的键：已从到期队列捞走、还没处理完的红包放这里

    # 构造方法: 创建对象时把依赖存起来
    def __init__(self, redis: Redis, *, ttl_seconds: int) -> None:
        self.redis = redis  # Redis 客户端，后面所有操作都靠它
        self.ttl_seconds = ttl_seconds  # 过期时间（秒）：金额池和领取记录都按这个时间过期

    @staticmethod  # 静态方法: 不需要 self，用类名就能调
    def pool_key(red_packet_id: int) -> str:
        # 金额池的键：一个红包一个列表，键里带红包ID避免互相串
        return f"redpacket:{red_packet_id}:pool"

    @staticmethod  # 静态方法: 不需要 self，用类名就能调
    def records_key(red_packet_id: int) -> str:
        # 领取记录的键：一个红包一个哈希，字段是 userId、值是领到的金额
        return f"redpacket:{red_packet_id}:records"

    # 写入金额池：把预先算好的每份金额一次性塞进 Redis
    async def initialize(self, red_packet_id: int, amounts: list[int]) -> bool:
        result = await self.redis.eval(
            INIT_SCRIPT,
            1,  # 这个脚本只用到 1 个 KEYS
            self.pool_key(red_packet_id),
            *(str(amount) for amount in amounts),  # 金额转字符串后逐个摊开传；Redis 参数只能是字符串
            str(self.ttl_seconds),  # 最后一个参数是过期时间，Lua 靠位置区分金额和 TTL
        )
        # 脚本返回实际写入的份数；等于金额总数才算全部写成功
        # 注意：池子已存在时脚本直接返回 0，这里也会得到 False
        return int(result) == len(amounts)

    # 用户抢一份红包：查重、弹金额、写记录全在 Lua 里一条龙完成
    async def claim(self, red_packet_id: int, user_id: int) -> ClaimResult:
        raw = await self.redis.eval(
            RECEIVE_SCRIPT,
            2,  # 这个脚本用到 2 个 KEYS：金额池 + 领取记录
            self.pool_key(red_packet_id),
            self.records_key(red_packet_id),
            str(user_id),
            str(self.ttl_seconds),
        )
        values = [int(value) for value in raw]  # Lua 返回的一串值，先统一转成整数
        if values[0] == -1:
            # -1：之前领过，第二个值是上次领到的金额
            return ClaimResult(-1, values[1])
        if values[0] == -2:
            # -2：池子空了，已经被抢完
            return ClaimResult(-2)
        # 0：领取成功，第二个值是金额，第三个值表示是不是刚好领完
        return ClaimResult(0, values[0], bool(values[1]))

    # 读取这个红包的全部领取记录：userId -> 金额
    async def records(self, red_packet_id: int) -> dict[int, int]:
        raw = await self.redis.hgetall(self.records_key(red_packet_id))
        # hgetall 返回的键值都是字节串，统一转成 int 方便业务直接用
        return {int(user_id): int(amount) for user_id, amount in raw.items()}

    # 查某个用户在这个红包里领过多少钱；没领过返回 None
    async def previous_amount(self, red_packet_id: int, user_id: int) -> int | None:
        value = await self.redis.hget(self.records_key(red_packet_id), str(user_id))
        return int(value) if value is not None else None

    # 清理：金额池、领取记录、到期队列里的痕迹一次删干净
    async def cleanup(self, red_packet_id: int) -> None:
        # 开事务管道：几条命令攒在一起提交，减少来回网络开销，中间不会被别的命令插队
        async with self.redis.pipeline(transaction=True) as pipe:
            # 删掉金额池和领取记录这两个键
            pipe.delete(self.pool_key(red_packet_id), self.records_key(red_packet_id))
            # 把红包从到期队列里摘掉，省得后面再被扫到
            pipe.zrem(self.DUE_KEY, str(red_packet_id))
            # 也从处理中队列里摘掉
            pipe.zrem(self.INFLIGHT_KEY, str(red_packet_id))
            # 真正提交：前面只是把命令攒在管道里，这里才发出去
            await pipe.execute()