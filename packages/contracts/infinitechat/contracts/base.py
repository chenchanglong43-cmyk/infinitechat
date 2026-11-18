"""Shared Pydantic behavior for wire contracts."""
# 所有接口契约模型共用的基础封装，统一内外字段命名规则
# 代码内部 Python 侧习惯 snake_case 下划线命名，和前端/外部通信时自动转 camelCase 小驼峰

from typing import Any

from pydantic import BaseModel, ConfigDict

def to_camel(field_name: str) -> str:
    """Convert internal snake_case names to the camelCase names used on the wire."""
    # 命名转换工具函数：把下划线字段名转成小驼峰
    # 示例：user_name → userName、order_id → orderId
    # split("_")拆分字符串，head拿第一段，剩下的全部放到tail列表
    head, *tail = field_name.split("_")
    # 第一段保持原样，后面每一段首字母大写，再拼接在一起
    return head + "".join(part.capitalize() for part in tail)

class ContractModel(BaseModel):
    """Base class that keeps snake_case names internal and camelCase names on the wire."""
    # 契约基类，所有入参、出参模型都继承它，自动统一接口行为
    model_config = ConfigDict(
        alias_generator=to_camel,  # 自动调用上面的函数生成驼峰别名，不用手写alias
        extra="forbid",  # 严格模式：请求带了模型里没定义的字段直接校验失败，防止脏数据
        populate_by_name=True,  # 两种赋值方式都支持：可以用原字段名，也可以用驼峰别名
        validate_assignment=True,  # 实例创建之后，修改属性值的时候也触发校验，避免脏数据
    )

    def to_wire(self, *, exclude_none: bool = False) -> dict[str, Any]:
        """Return a JSON-compatible object that uses the wire aliases."""
        # by_alias=True：导出的时候使用驼峰别名
        # mode="json"：自动把日期、Decimal这类类型转成json可序列化格式
        # exclude_none=False默认保留null；设为True时，值为None的字段直接删掉不返回
        return self.model_dump(by_alias=True, mode="json", exclude_none=exclude_none)