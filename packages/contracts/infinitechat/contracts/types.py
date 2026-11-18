"""Wire-level primitive types whose exact runtime framing is not yet frozen."""
# 接口传输层面的基础类型定义，后续序列化格式还可能调整，没完全固定死

# JSON里最基础的标量类型，不能再拆成别的结构
type JsonScalar = str | int | float | bool | None

# 完整JSON值类型：可以是基础标量、标量数组、key为字符串的JSON对象（递归定义）
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

# 时间戳比较特殊：不同接口规则不一样
# 有的接口用毫秒时间戳数字，有的用格式化时间字符串；两种都能接收、都属于传输标量
type WireTimestamp = int | str