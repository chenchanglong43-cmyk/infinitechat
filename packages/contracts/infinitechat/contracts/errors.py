"""Stable business error codes of the client contract."""
# 对外契约里固定的业务错误码枚举，前端会依赖这份编码，不能随便改动

# 下面的全角标点是给前端展示用的提示文案的一部分，不要删
# ruff: noqa: RUF001  关闭ruff对全角字符的检查

from enum import IntEnum

# 业务错误码枚举，继承IntEnum，序列化后直接是数字，方便前端判断
class ErrorCode(IntEnum):
    PARAMS_ERROR = 40000
    SENSITIVE_WORD_ERROR = 40003  # 内容包含敏感词
    NOT_LOGIN_ERROR = 40100  # 未登录
    NO_AUTH_ERROR = 40101  # 没有操作权限
    TOKEN_MISSING = 40102  # 请求里没带上token
    TOKEN_EXPIRED = 40103  # token已经过期
    TOKEN_INVALID = 40104  # token格式或者签名非法
    TOKEN_MISMATCH = 40105  # token和当前用户/设备不匹配
    FORBIDDEN_ERROR = 40300  # 拒绝访问，禁止操作
    NOT_FOUND_ERROR = 40400  # 资源不存在
    SYSTEM_ERROR = 50000  # 服务端内部异常
    OPERATION_ERROR = 50001  # 业务操作执行失败
    INVALID_PARAMETER_ERROR = 50003  # 单个参数校验不通过
    PLEASE_LOGIN = 50004  # 请先登录（业务层提示）
    SAME_LOGIN_CONFLICT = 50005  # 异地登录冲突
    SYSTEM_BUSY = 50008  # 系统繁忙，请稍后重试
    PHONE_EMAIL_ERROR = 70000  # 手机号或邮箱格式错误
    USER_ALREADY_EXISTS = 70001  # 用户已注册
    USER_NOT_EXISTS = 70002  # 用户不存在
    REGISTER_ERROR = 70003  # 注册失败
    LOGIN_ERROR_CODE = 70004  # 登录流程异常
    LOGIN_ERROR = 70005  # 登录失败
    LOGIN_PASSWORD_ERROR = 70006  # 登录密码错误
    CURRENT_PASSWORD_ERROR = 70007  # 原密码输入错误
    PASSWORD_REUSE_ERROR = 70008  # 新密码不能和旧密码重复
    PHONE_ALREADY_EXISTS = 70009  # 该手机号已被绑定
    SIGNAL_TYPE_ERROR = 90000  # 消息信号类型不支持
    GROUP_TYPE_ERROR = 90001  # 群组类型错误
    INVALID_TOKEN = 90003  # 通信token无效
    USER_EMAIL_LIST_EMPTY = 90004  # 用户邮箱列表为空
    VALIDATION_FAILED = 91001  # 消息校验失败
    NOT_FRIEND = 91002  # 双方不是好友关系
    BLOCKED_BY_RECEIVER = 91003  # 被接收方拉黑
    FRIEND_DELETED = 91004  # 好友关系已解除
    NOT_GROUP_MEMBER = 91005  # 不是群成员
    SENDER_DISABLED = 91006  # 发送账号被封禁
    RECEIVER_DISABLED = 91007  # 接收账号被封禁
    SERVICE_UNAVAILABLE = 91008  # 聊天服务暂时不可用
    REDPACKET_COUNT_EXCEED_MEMBERS = 92001  # 红包个数不能超过群人数
    REDPACKET_SINGLE_ONLY_NORMAL = 92002  # 单发红包只能是普通红包
    REDPACKET_SENDER_CANNOT_RECEIVE = 92003  # 发红包的人不能领自己红包
    REDPACKET_NOT_THE_RECEIVER = 92004  # 这个红包不是发给你的
    REDPACKET_NOT_IN_CONVERSATION = 92005  # 你不在红包所属会话里


# 默认错误消息映射表：错误码 → 前端展示文案，和ErrorCode一一对应
DEFAULT_ERROR_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.PARAMS_ERROR: "请求参数错误",
    ErrorCode.SENSITIVE_WORD_ERROR: "包含敏感词，请求被拒绝",
    ErrorCode.NOT_LOGIN_ERROR: "未登录",
    ErrorCode.NO_AUTH_ERROR: "无权限",
    ErrorCode.TOKEN_MISSING: "令牌缺失",
    ErrorCode.TOKEN_EXPIRED: "令牌已过期",
    ErrorCode.TOKEN_INVALID: "令牌无效",
    ErrorCode.TOKEN_MISMATCH: "令牌与用户不匹配",
    ErrorCode.FORBIDDEN_ERROR: "禁止访问",
    ErrorCode.NOT_FOUND_ERROR: "请求数据不存在",
    ErrorCode.SYSTEM_ERROR: "系统内部异常",
    ErrorCode.OPERATION_ERROR: "操作失败",
    ErrorCode.INVALID_PARAMETER_ERROR: "参数校验失败",
    ErrorCode.PLEASE_LOGIN: "请先登录",
    ErrorCode.SAME_LOGIN_CONFLICT: "账号已在其他地方登录",
    ErrorCode.SYSTEM_BUSY: "系统繁忙，请稍后重试",
    ErrorCode.PHONE_EMAIL_ERROR: "手机号/邮箱格式错误",
    ErrorCode.USER_ALREADY_EXISTS: "用户已存在",
    ErrorCode.USER_NOT_EXISTS: "用户不存在",
    ErrorCode.REGISTER_ERROR: "注册失败",
    ErrorCode.LOGIN_ERROR_CODE: "验证码错误",
    ErrorCode.LOGIN_ERROR: "登录失败, 用户名或密码错误",
    ErrorCode.LOGIN_PASSWORD_ERROR: "两次密码不一致",
    ErrorCode.CURRENT_PASSWORD_ERROR: "当前密码错误",
    ErrorCode.PASSWORD_REUSE_ERROR: "新密码不能与当前密码相同",
    ErrorCode.PHONE_ALREADY_EXISTS: "手机号已被使用",
    ErrorCode.SIGNAL_TYPE_ERROR: "单聊消息必须指定接收者",
    ErrorCode.GROUP_TYPE_ERROR: "群聊消息不需要指定接收者",
    ErrorCode.INVALID_TOKEN: "无效token，请重新登录",
    ErrorCode.USER_EMAIL_LIST_EMPTY: "用户邮箱列表为空，请检查用户服务是否正常或没有用户注册",
    ErrorCode.VALIDATION_FAILED: "消息校验失败",
    ErrorCode.NOT_FRIEND: "对方不是您的好友",
    ErrorCode.BLOCKED_BY_RECEIVER: "您已被对方拉黑",
    ErrorCode.FRIEND_DELETED: "好友关系已删除",
    ErrorCode.NOT_GROUP_MEMBER: "您不是该群成员",
    ErrorCode.SENDER_DISABLED: "发送者账号异常",
    ErrorCode.RECEIVER_DISABLED: "接收者账号异常",
    ErrorCode.SERVICE_UNAVAILABLE: "服务暂时不可用，请稍后重试",
    ErrorCode.REDPACKET_COUNT_EXCEED_MEMBERS: "红包数量不能超过群成员数量",
    ErrorCode.REDPACKET_SINGLE_ONLY_NORMAL: "单聊只能发送普通红包",
    ErrorCode.REDPACKET_SENDER_CANNOT_RECEIVE: (
        "不能领取自己发出的红包，24 小时内未被领取将自动退回"
    ),
    ErrorCode.REDPACKET_NOT_THE_RECEIVER: "该红包不是发给你的",
    ErrorCode.REDPACKET_NOT_IN_CONVERSATION: "你不在该会话中，无法领取该红包",
}

# 根据错误码拿到默认提示文案
def error_message(code: ErrorCode | int) -> str:
    """Resolve a stable default message, falling back to the system error."""
    # 支持传枚举或者纯数字错误码
    try:
        # 把数字转成ErrorCode枚举，方便查表
        normalized = ErrorCode(code)
    except ValueError:
        # 传了一个不存在的错误码，兜底返回系统异常
        normalized = ErrorCode.SYSTEM_ERROR
    # 从映射字典取出对应文案返回
    return DEFAULT_ERROR_MESSAGES[normalized]