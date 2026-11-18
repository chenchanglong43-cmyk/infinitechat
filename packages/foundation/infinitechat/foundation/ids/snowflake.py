"""Thread-safe 64-bit Snowflake IDs with explicit worker/data-center identity."""

import time  # 时间
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock

DEFAULT_EPOCH_MS = 1_288_834_974_657
_SEQUENCE_BITS = 12
_WORKER_BITS = 5
_DATA_CENTER_BITS = 5
_MAX_SEQUENCE = (1 << _SEQUENCE_BITS) - 1  # 常量 最大序号: 1 左移 序号bits 位 - 1
_MAX_WORKER_ID = (1 << _WORKER_BITS) - 1  # 常量 最大机器号: 1 左移 机器bits 位 - 1
# 常量 最大数据中心号: 1 左移 数据中心bits 位 - 1
_MAX_DATA_CENTER_ID = (1 << _DATA_CENTER_BITS) - 1
_WORKER_SHIFT = _SEQUENCE_BITS
_DATA_CENTER_SHIFT = _SEQUENCE_BITS + _WORKER_BITS  # 常量 数据中心号位移: 序号bits + 机器bits
# 常量 时间戳位移: 序号bits + 机器bits + 数据中心bits
_TIMESTAMP_SHIFT = _SEQUENCE_BITS + _WORKER_BITS + _DATA_CENTER_BITS

# 时钟回拨错误
# 时钟回拨错误 (自定义异常)
class ClockRollbackError(RuntimeError):
    pass  # 什么都不做, 只占个位置

# Snowflake 解析结果
@dataclass(frozen=True, slots=True)  # 数据类: 自动生成构造函数等方法, 不可修改, 省内存
# Snowflake 解析结果
class SnowflakeParts:
    timestamp_ms: int  # 时间戳毫秒
    data_center_id: int  # 数据中心号
    worker_id: int  # 机器号
    sequence: int  # 序号


# Snowflake ID 生成器
class SnowflakeGenerator:
    def __init__(
        self,
        *,
        worker_id: int,
        data_center_id: int,
        epoch_ms: int = DEFAULT_EPOCH_MS,
        clock: Callable[[], int] | None = None,
    ) -> None:
        # 校验机器ID范围 0~31
        if not 0 <= worker_id <= _MAX_WORKER_ID:
            raise ValueError("worker_id must be between 0 and 31")
        # 校验数据中心ID范围 0~31
        if not 0 <= data_center_id <= _MAX_DATA_CENTER_ID:
            raise ValueError("data_center_id must be between 0 and 31")
        self.worker_id = worker_id
        self.data_center_id = data_center_id
        self.epoch_ms = epoch_ms
        # 支持传入自定义时钟，方便单元测试
        self._clock = clock or (lambda: time.time_ns() // 1_000_000)
        self._last_timestamp = -1
        self._sequence = 0
        self._lock = Lock()  # 多线程锁，保证同一毫秒内序号不重复

    def next_id(self) -> int:
        """生成下一个雪花ID"""
        with self._lock:
            timestamp = self._clock()
            # 时钟回拨，直接抛异常
            if timestamp < self._last_timestamp:
                raise ClockRollbackError(
                    f"clock moved backwards by {self._last_timestamp - timestamp} ms"
                )
            if timestamp == self._last_timestamp:
                # 同一毫秒，序号自增，超出最大值自动归零
                self._sequence = (self._sequence + 1) & _MAX_SEQUENCE
                # 序号归零，说明本毫秒号用完，阻塞等到下一毫秒
                if self._sequence == 0:
                    timestamp = self._wait_for_next_millisecond(timestamp)
            else:
                # 新毫秒，序号重置为0
                self._sequence = 0
            # 时间不能早于设定的起始纪元
            if timestamp < self.epoch_ms:
                raise ValueError("current timestamp precedes Snowflake epoch")
            self._last_timestamp = timestamp
            # 按位拼接：时间戳 | 数据中心 | 机器号 | 序列号
            return (
                ((timestamp - self.epoch_ms) << _TIMESTAMP_SHIFT)
                | (self.data_center_id << _DATA_CENTER_SHIFT)
                | (self.worker_id << _WORKER_SHIFT)
                | self._sequence
            )

    def decode(self, snowflake_id: int) -> SnowflakeParts:
        """反向解析雪花ID，拆出时间、数据中心、机器号、序号"""
        if snowflake_id < 0:
            raise ValueError("Snowflake ID must be non-negative")
        return SnowflakeParts(
            timestamp_ms=(snowflake_id >> _TIMESTAMP_SHIFT) + self.epoch_ms,
            data_center_id=(snowflake_id >> _DATA_CENTER_SHIFT) & _MAX_DATA_CENTER_ID,
            worker_id=(snowflake_id >> _WORKER_SHIFT) & _MAX_WORKER_ID,
            sequence=snowflake_id & _MAX_SEQUENCE,
        )

    def _wait_for_next_millisecond(self, previous: int) -> int:
        # 忙等，直到拿到新的毫秒时间戳
        timestamp = self._clock()
        while timestamp <= previous:
            time.sleep(0.0001)
            timestamp = self._clock()
        return timestamp