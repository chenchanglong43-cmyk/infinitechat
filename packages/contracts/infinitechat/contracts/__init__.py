"""Cross-service transport contracts for InfiniteChat."""
# 契约层统一导出入口，其他服务导入契约直接从这里拿，不用关心各个文件的路径

from infinitechat.contracts.errors import ErrorCode, error_message
from infinitechat.contracts.pagination import PageRequest, PageResponse
from infinitechat.contracts.responses import BaseResponse, GatewayAuthErrorResponse

# __all__ 控制 import * 时能导出哪些内容，统一对外暴露的契约清单
__all__ = (
    "BaseResponse",
    "ErrorCode",
    "GatewayAuthErrorResponse",
    "PageRequest",
    "PageResponse",
    "error_message",
)