"""Stable application errors mapped to the client response envelope."""
# 业务异常基类，抛出去之后，统一包装成前端能看懂的返回体
from infinitechat.contracts import ErrorCode, error_message

# 业务自定义异常
class BusinessError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str | None = None) -> None:
        # 优先用传入的提示文字；没传就拿错误码自带的默认文案
        super().__init__(message or error_message(code))
        # 保存错误码，上层捕获后用来组装返回JSON
        self.code = code
        self.message = message or error_message(code)