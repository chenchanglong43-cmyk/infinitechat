"""Common HTTP response envelopes."""
# HTTP接口统一返回包装体，所有业务接口都用这套格式返回

from infinitechat.contracts.base import ContractModel
from infinitechat.contracts.errors import ErrorCode, error_message

# 通用统一响应泛型包装，继承契约基类，自动驼峰序列化
# [DataT] 是泛型占位符：DataT = DataType，代表后续使用时再指定data里面的真实类型
# 好处：同一个返回壳子复用，同时保留data内部类型，支持校验和自动生成接口文档
class BaseResponse[DataT](ContractModel):
    # 业务码，成功固定200，失败用ErrorCode里的数字
    code: int
    # 业务返回数据，DataT是泛型占位，使用时替换成具体模型，可为空
    data: DataT | None = None
    # 提示文案，给前端展示
    message: str

    @classmethod
    # 快速构造成功返回，必须传入要返回的数据
    def success(cls, data: DataT) -> "BaseResponse[DataT]":
        return cls(code=200, data=data, message="ok")

    @classmethod
    # 快速构造错误返回，支持自定义提示文案，不传就自动取默认错误文案
    # * 代表后面参数必须关键字传参，防止传参顺序搞混
    def error(
        cls,
        code: ErrorCode,
        *,
        message: str | None = None,
    ) -> "BaseResponse[DataT]":
        return cls(code=code, data=None, message=message or error_message(code))

# 网关专用401鉴权响应，特殊结构，没有data字段，和业务接口区分开
class GatewayAuthErrorResponse(ContractModel):
    """Gateway's current 401 body intentionally has no data member."""
    # 网关返回401时固定返回这个结构，不带data，适配网关侧逻辑
    code: int = ErrorCode.NOT_LOGIN_ERROR
    message: str = error_message(ErrorCode.NOT_LOGIN_ERROR)