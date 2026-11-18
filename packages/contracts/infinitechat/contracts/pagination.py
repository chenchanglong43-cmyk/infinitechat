"""Page request and response structures of the client contract."""
# 分页的入参、出参契约模型，给列表查询接口统一使用

from collections.abc import Sequence
from math import ceil

from infinitechat.contracts.base import ContractModel
from pydantic import Field

# 分页请求参数，前端查列表时传过来
class PageRequest(ContractModel):
    # 页码，默认第1页，最小不能小于1
    page_num: int = Field(default=1, ge=1)
    # 每页条数，默认20条，限制范围1~100，防止一次性拉巨量数据
    page_size: int = Field(default=20, ge=1, le=100)

# 分页返回模型，[ItemT]是泛型占位符，使用时指定列表里面是什么实体
# 同一个分页壳子复用，同时保留列表内元素的类型，用于校验和生成接口文档
class PageResponse[ItemT](ContractModel):
    # 当前页的数据列表，ItemT就是单条数据的类型
    list: list[ItemT]
    # 符合条件的全部数据总数，大于等于0
    total: int = Field(ge=0)
    # 每页大小
    page_size: int = Field(ge=1)
    # 当前页码
    page_num: int = Field(ge=1)
    # 一共多少页
    pages: int = Field(ge=0)
    # 是否存在下一页
    has_next: bool
    # 是否存在上一页
    has_previous: bool

    @classmethod
    def from_items(
        cls,
        items: Sequence[ItemT],
        *,
        page_num: int,
        page_size: int,
    ) -> "PageResponse[ItemT]":
        total = len(items)
        # 向上取整算出总页数；没有数据时总页数为0
        pages = ceil(total / page_size) if total else 0
        # 计算切片起始下标
        start = (page_num - 1) * page_size
        return cls(
            list=list(items[start : start + page_size]),
            total=total,
            page_size=page_size,
            page_num=page_num,
            pages=pages,
            has_next=page_num < pages,
            has_previous=page_num > 1,
        )