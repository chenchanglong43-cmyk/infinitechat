"""Static HTTP route registry used for completeness checks and Gateway planning."""
# 静态HTTP路由契约，用于完整性校验、网关路由规划

from typing import Literal  # 导入字面量类型，用来限定枚举取值

from infinitechat.contracts.base import ContractModel  # 导入契约基础模型

# 路由契约
# 路由契约 (数据模型)
class RouteContract(ContractModel):
    method: Literal["GET", "POST", "PUT", "DELETE"]  # 请求方法，仅支持这四种
    path: str  # 接口路由路径
    service: Literal["user", "offline", "red_packet", "realtime"]  # 归属后端服务
    request_model: str | None = None  # 请求体模型名称，可为空，默认空
    response_model: str  # 响应体模型名称
    visibility: Literal["public", "internal"] = "public"  # 可见范围：公开/内部，默认公开
    auth_required: bool = True  # 是否需要鉴权，默认开启
    enveloped: bool = True  # 是否包裹统一返回外层结构，默认开启
    compatibility: Literal["core", "p1", "web"] = "core"  # 兼容端，默认core通用端



HTTP_ROUTES: tuple[RouteContract, ...] = (  # HTTP路由元组，一共35条接口契约
    RouteContract(
        method="GET",  # 请求方式GET
        path="/api/user/sendCaptcha",  # 接口路径：发送验证码
        service="user",  # 归属user服务
        response_model="str",  # 返回数据模型为字符串
        auth_required=False,  # 不需要登录鉴权
    ),
    RouteContract(
        method="POST",  # 请求方式POST
        path="/api/user/register",  # 接口路径：用户注册
        service="user",  # 归属user服务
        request_model="UserRegisterRequest",  # 请求模型：用户注册入参
        response_model="LoginSessionResponse",  # 返回模型：登录会话信息
        auth_required=False,  # 不需要登录鉴权
    ),
    RouteContract(
        method="POST",  # 请求方式POST
        path="/api/user/login/password",  # 接口路径：密码登录
        service="user",  # 归属user服务
        request_model="PasswordLoginRequest",  # 请求模型：密码登录入参
        response_model="LoginSessionResponse",  # 返回模型：登录会话信息
        auth_required=False,  # 不需要登录鉴权
    ),
    RouteContract(
        method="POST",  # 请求方式POST
        path="/api/user/login/code",  # 接口路径：验证码登录
        service="user",  # 归属user服务
        request_model="CodeLoginRequest",  # 请求模型：验证码登录入参
        response_model="LoginSessionResponse",  # 返回模型：登录会话信息
        auth_required=False,  # 不需要登录鉴权
    ),
    RouteContract(method="GET", path="/api/user/logout", service="user", response_model="bool"),  # 退出登录，返回布尔
    RouteContract(
        method="POST",  # 请求方式POST
        path="/api/user/refresh",  # 接口路径：刷新token
        service="user",  # 归属user服务
        response_model="TokenResponse",  # 返回模型：新令牌
        auth_required=False,  # 不需要登录鉴权
    ),
    RouteContract(method="GET", path="/api/user/refresh/uri", service="user", response_model="str"),  # 获取刷新token地址，返回字符串
    RouteContract(
        method="GET",  # 请求方式GET
        path="/api/user/uploadUrl",  # 接口路径：获取文件预签名上传地址
        service="user",  # 归属user服务
        response_model="UploadUrlResponse",  # 返回模型：上传地址信息
    ),
    RouteContract(
        method="POST",  # 请求方式POST
        path="/api/user/update/avatar",  # 接口路径：更新头像
        service="user",  # 归属user服务
        request_model="UpdateAvatarRequest",  # 请求模型：头像更新入参
        response_model="bool",  # 返回布尔结果
    ),
    RouteContract(
        method="GET",  # 请求方式GET
        path="/api/user/get/nickname",  # 接口路径：获取昵称
        service="user",  # 归属user服务
        response_model="dict[str,str]",  # 返回字典
        enveloped=False,  # 不使用统一外层返回包装
    ),
    RouteContract(
        method="GET",  # 请求方式GET
        path="/api/user/get/receivers",  # 接口路径：获取接收人列表
        service="user",  # 归属user服务
        response_model="list[int]",  # 返回int数组
        enveloped=False,  # 不使用统一外层返回包装
    ),
    RouteContract(
        method="GET",  # 请求方式GET
        path="/api/user/get/sessions",  # 接口路径：获取会话列表
        service="user",  # 归属user服务
        response_model="list[int]",  # 返回int数组
        enveloped=False,  # 不使用统一外层返回包装
    ),
    RouteContract(
        method="GET",  # 请求方式GET
        path="/api/contact/{userId}/user/search",  # 接口路径：搜索用户
        service="user",  # 归属user服务
        response_model="FriendDetail",  # 返回模型：好友详情
    ),
    RouteContract(
        method="GET",  # 请求方式GET
        path="/api/contact/{userId}/friend",  # 接口路径：分页获取好友列表
        service="user",  # 归属user服务
        request_model="PageRequest",  # 请求模型：分页参数
        response_model="PageResponse[FriendSummary]",  # 返回模型：分页好友摘要
    ),
    RouteContract(
        method="POST",  # 请求方式POST
        path="/api/contact/{userId}/friend/{receiveuserId}",  # 接口路径：添加好友
        service="user",  # 归属user服务
        request_model="AddFriendRequest",  # 请求模型：加好友入参
        response_model="bool",  # 返回布尔结果
    ),
    RouteContract(
        method="GET",  # 请求方式GET
        path="/api/contact/{userId}/apply",  # 接口路径：分页获取好友申请记录
        service="user",  # 归属user服务
        request_model="PageRequest",  # 请求模型：分页参数
        response_model="PageResponse[FriendApplication]",  # 返回模型：分页好友申请
    ),
    RouteContract(
        method="GET",  # 请求方式GET
        path="/api/contact/{userId}/applyCount",  # 接口路径：获取好友申请未读数量
        service="user",  # 归属user服务
        response_model="ApplyCount",  # 返回模型：申请数量
    ),
    RouteContract(
        method="DELETE",  # 请求方式DELETE
        path="/api/contact/{userId}/friend/{receiveuserId}",  # 接口路径：删除好友
        service="user",  # 归属user服务
        response_model="bool",  # 返回布尔结果
    ),
    RouteContract(
        method="POST",  # 请求方式POST
        path="/api/contact/{userId}/block/{receiveuserId}",  # 接口路径：拉黑用户
        service="user",  # 归属user服务
        response_model="bool",  # 返回布尔结果
    ),
    RouteContract(
        method="DELETE",  # 请求方式DELETE
        path="/api/contact/{userId}/block/{receiveuserId}",  # 接口路径：解除拉黑
        service="user",  # 归属user服务
        response_model="bool",  # 返回布尔结果
    ),
    RouteContract(
        method="POST",  # 请求方式POST
        path="/api/contact/{userId}/application/{status}",  # 接口路径：处理好友申请
        service="user",  # 归属user服务
        request_model="ModifyApplicationRequest",  # 请求模型：处理申请入参
        response_model="NewSession | bool",  # 返回模型：新会话或者布尔
    ),
    RouteContract(
        method="GET",  # 请求方式GET
        path="/api/contact/{userId}/friend/{friendId}",  # 接口路径：查询单个好友详情
        service="user",  # 归属user服务
        response_model="FriendDetail",  # 返回模型：好友详情
    ),
    RouteContract(
        method="POST",  # 请求方式POST
        path="/api/group",  # 接口路径：创建群聊
        service="user",  # 归属user服务
        request_model="CreateGroupRequest",  # 请求模型：建群入参
        response_model="CreateGroupResponse",  # 返回模型：建群结果
    ),
    RouteContract(
        method="POST",  # 请求方式POST
        path="/api/group/invite",  # 接口路径：邀请进群
        service="user",  # 归属user服务
        request_model="InviteGroupRequest",  # 请求模型：邀请入参
        response_model="GroupMemberMutationResponse",  # 返回模型：群成员变更结果
        compatibility="p1",  # 兼容端标记p1
    ),
    RouteContract(
        method="POST",  # 请求方式POST
        path="/api/message/offline",  # 接口路径：拉取离线消息
        service="offline",  # 归属offline离线消息服务
        request_model="OfflineMessageRequest",  # 请求模型：离线消息入参
        response_model="dict[str,list[MessageResponse]]",  # 返回字典，value是消息列表
    ),
    RouteContract(
        method="POST",  # 请求方式POST
        path="/api/message/history",  # 接口路径：拉取历史消息
        service="offline",  # 归属offline离线消息服务
        request_model="HistoryMessageRequest",  # 请求模型：历史消息入参
        response_model="list[MessageResponse]",  # 返回消息列表
    ),
    RouteContract(
        method="POST",  # 请求方式POST
        path="/api/chat/redPacket/send",  # 接口路径：发送红包
        service="red_packet",  # 归属red_packet红包服务
        request_model="RedPacketSendRequest",  # 请求模型：发红包入参
        response_model="RedPacketSendResponse",  # 返回模型：发红包结果
    ),
    RouteContract(
        method="POST",  # 请求方式POST
        path="/api/chat/redPacket/receive",  # 接口路径：领取红包
        service="red_packet",  # 归属red_packet红包服务
        request_model="RedPacketReceiveRequest",  # 请求模型：领红包入参
        response_model="ReceiveResult",  # 返回模型：领取结果
    ),
    RouteContract(
        method="GET",  # 请求方式GET
        path="/api/chat/redPacket/",  # 接口路径：分页查询红包记录
        service="red_packet",  # 归属red_packet红包服务
        request_model="PageRequest",  # 请求模型：分页参数
        response_model="RedPacketDetail",  # 返回模型：红包详情
    ),
    RouteContract(
        method="GET",  # 请求方式GET
        path="/api/chat/redPacket/basic",  # 接口路径：获取红包基础信息
        service="red_packet",  # 归属red_packet红包服务
        response_model="RedPacketBasic",  # 返回模型：红包基础信息
    ),
    RouteContract(
        method="GET",  # 请求方式GET
        path="/api/internal/user/status",  # 内部接口：查询用户在线状态
        service="user",  # 归属user服务
        response_model="UserStatus",  # 返回模型：用户状态
        visibility="internal",  # 内部接口，不对外暴露
    ),
    RouteContract(
        method="GET",  # 请求方式GET
        path="/api/internal/group/memberCount",  # 内部接口：查询群成员数量
        service="user",  # 归属user服务
        response_model="GroupMemberCount",  # 返回模型：群人数
        visibility="internal",  # 内部接口，不对外暴露
    ),
    RouteContract(
        method="GET",  # 请求方式GET
        path="/api/internal/group/isMember",  # 内部接口：校验是否是群成员
        service="user",  # 归属user服务
        response_model="GroupMembership",  # 返回模型：群成员校验结果
        visibility="internal",  # 内部接口，不对外暴露
    ),
    RouteContract(
        method="POST",  # 请求方式POST
        path="/api/internal/validation/single-message",  # 内部接口：单条消息内容校验
        service="user",  # 归属user服务
        request_model="SingleMessageValidateRequest",  # 请求模型：消息校验入参
        response_model="MessageValidation",  # 返回模型：消息校验结果
        visibility="internal",  # 内部接口，不对外暴露
    ),
    RouteContract(
        method="GET",  # 请求方式GET
        path="/api/internal/users/batch",  # 内部接口：批量查询用户信息
        service="user",  # 归属user服务
        response_model="dict[str,UserInfo]",  # 返回字典，key用户id，value用户信息
        visibility="internal",  # 内部接口，不对外暴露
    ),
)