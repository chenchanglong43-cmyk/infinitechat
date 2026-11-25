"""Business error raised by the red packet service."""

from infinitechat.contracts import ErrorCode

# 红包业务异常(携带错误码与文案)
class RedPacketError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str) -> None:  # 构造方法: 创建对象时把依赖存起来
        super().__init__(message)  # 把提示文字交给父类保存
        self.code = code
        self.message = message