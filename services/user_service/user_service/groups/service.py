"""Transactional group creation and P1 membership lifecycle."""

# 导入错误码常量
from infinitechat.contracts import ErrorCode
# 导入枚举：好友状态、群成员角色、会话类型、用户状态
from infinitechat.contracts.enums import FriendStatus, GroupMemberRole, SessionType, UserState
# 导入HTTP请求响应结构体
from infinitechat.contracts.http import (
    CreateGroupRequest,
    CreateGroupResponse,
    GroupMemberMutationResponse,
    InviteGroupRequest,
)
# 导入雪花ID生成工具
from infinitechat.foundation.ids import SnowflakeGenerator
# 导入异步数据库会话
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
# 导入服务配置
from user_service.config import UserServiceSettings
# 导入自定义业务异常
from user_service.errors import BusinessError
# 导入通知服务
from user_service.notifications import NotificationService
# 导入消息发件箱仓库
from user_service.outbox import OutboxRepository
# 导入数据库模型
from user_service.storage.models import SessionModel, UserModel, UserSessionModel
# 导入各类仓库
from user_service.storage.repositories import (
    ContactRepository,
    SessionRepository,
    UserRepository,
    database_now,
)

# 群聊服务，负责建群、成员管理
class GroupService:
    # 构造方法，注入依赖
    def __init__(
        self,
        *,  # 星号后面的参数只能按名字传，防止传参顺序搞错
        session_factory: async_sessionmaker[AsyncSession],
        id_generator: SnowflakeGenerator,
        settings: UserServiceSettings,
    ) -> None:
        self._sessions = session_factory  # 保存数据库会话工厂
        self._ids = id_generator         # 保存雪花ID生成器
        self._settings = settings        # 保存服务配置

    # 创建群聊
    async def create(self, request: CreateGroupRequest) -> CreateGroupResponse:
        # 去重成员ID，防止重复拉同一个人
        # 给列表去重，同时保留原来元素的顺序
        member_ids = list(dict.fromkeys(request.member_ids))
        # 判断成员列表是不是空
        if not member_ids:
            # 成员ID列表不能为空
            raise BusinessError(ErrorCode.PARAMS_ERROR, "成员ID列表不能为空")
        # 如果创建人自己在成员列表里，把他删掉，群主不需要重复加入
        if request.creator_id in member_ids:
            member_ids.remove(request.creator_id)
        failed: list[str] = []  # 存拉群失败的成员id字符串
        # 开启数据库会话 + 事务，出错自动回滚
        async with self._sessions() as session, session.begin():
            users = UserRepository(session)  # 实例化用户仓库
            # 查询群主信息
            creator = await users.get_by_id(request.creator_id)
            # 校验群主：用户存在、状态正常、没有注销删除
            if creator is None or creator.state != UserState.NORMAL or creator.is_delete:
                # 用户不存在或状态异常
                raise BusinessError(ErrorCode.NOT_FOUND_ERROR, "用户不存在或状态异常")
            contacts = ContactRepository(session)  # 实例化好友关系仓库
            valid_ids: list[int] = []  # 存放校验通过、可以进群的成员id
            # 循环挨个校验每一个待拉成员
            for member_id in member_ids:
                # 查群主和这个成员是不是正常好友
                relationship = await contacts.get_friend(request.creator_id, member_id)
                # 反向查成员到群主的好友记录，拉黑是单向的，必须两边都看
                reverse = await contacts.get_friend(member_id, request.creator_id)
                # 查询这个成员的用户信息
                member = await users.get_by_id(member_id)
                # 校验条件：双向都是正常好友 + 用户存在 + 状态正常 + 没有注销
                if (
                    relationship is not None
                    and relationship.status == FriendStatus.NORMAL
                    and reverse is not None
                    and reverse.status == FriendStatus.NORMAL
                    and member is not None
                    and member.state == UserState.NORMAL
                    and not member.is_delete
                ):
                    valid_ids.append(member_id)  # 校验通过，放进有效列表
                else:
                    failed.append(str(member_id))  # 校验不通过，加入失败列表
            # 有效成员为空，不能建群
            if not valid_ids:
                # 没有有效的好友可加入群聊
                raise BusinessError(ErrorCode.OPERATION_ERROR, "没有有效的好友可加入群聊")
            # 批量查询群主+所有有效成员的用户信息，后面用来拼群名
            user_map = await users.list_by_ids([request.creator_id, *valid_ids])
            # 拼接群名：多个昵称用顿号隔开，最多保留16个字符
            group_name = "、".join(
                user_map[user_id].nickname
                for user_id in [request.creator_id, *valid_ids]
                if user_id in user_map
            )[:16]
            session_id = self._ids.next_id()  # 生成群会话ID
            sessions = SessionRepository(session)  # 实例化会话仓库
            # 新增一条群会话记录
            sessions.add_session(
                SessionModel(
                    session_id=session_id,
                    name=group_name,
                    type=SessionType.GROUP,
                    status=0,
                    avatar=self._settings.default_group_avatar_url,
                )
            )
            # 批量添加会话成员，群主是OWNER，其他人是普通成员
            sessions.add_members(
                [
                    UserSessionModel(
                        user_id=request.creator_id,
                        session_id=session_id,
                        role=GroupMemberRole.OWNER,
                        status=0,
                    ),
                    *[
                        UserSessionModel(
                            user_id=member_id,
                            session_id=session_id,
                            role=GroupMemberRole.MEMBER,
                            status=0,
                        )
                        for member_id in valid_ids
                    ],
                ]
            )
            # 初始化通知服务，用发件箱保证消息可靠投递
            notifications = NotificationService(OutboxRepository(session), self._ids)
            # 给每一个成功进群的成员推送新建群通知
            for member_id in valid_ids:
                notifications.new_group_session(
                    receiver_id=member_id,
                    session_id=session_id,
                    session_name=group_name,
                    avatar=self._settings.default_group_avatar_url,
                    creator_id=request.creator_id,
                    members_count=len(valid_ids) + 1,
                )
        # 组装返回结果，给到接口
        return CreateGroupResponse(
            creator_id=str(request.creator_id),
            session_id=str(session_id),
            session_name=group_name,
            avatar=self._settings.default_group_avatar_url,
            failed_member_ids=failed,
        )


    # 邀请用户入群
    async def invite(self, request: InviteGroupRequest) -> GroupMemberMutationResponse:
        # 对要邀请的id去重，避免重复邀请同一个人
        invitee_ids = list(dict.fromkeys(request.invitee_ids))
        # 用来存放邀请成功的用户id
        successful: list[int] = []
        # 用来存放邀请失败的用户id
        failed: list[int] = []
        # 开启数据库会话，同时开启事务，出错自动回滚
        async with self._sessions() as session, session.begin():
            # 实例化会话仓库，操作群会话数据
            sessions = SessionRepository(session)
            # 根据群id查询群信息，加行锁，校验群是否合法
            group = self._require_group(
                await sessions.get_session(request.session_id, for_update=True)
            )
            # 查询邀请人在这个群里的成员记录，加锁防止并发
            inviter_membership = await sessions.membership(
                request.inviter_id,
                request.session_id,
                for_update=True,
            )
            # 判断：查不到邀请人的群记录，说明他不在群里
            if inviter_membership is None:
                # 抛出无权限异常：您不在该群聊中
                raise BusinessError(ErrorCode.NO_AUTH_ERROR, "您不在该群聊中")
            # 判断邀请人角色，只有群主和管理员才有拉人权限
            if inviter_membership.role not in {GroupMemberRole.OWNER, GroupMemberRole.ADMIN}:
                # 抛出异常：只有群主或管理员才能邀请成员
                raise BusinessError(ErrorCode.NO_AUTH_ERROR, "只有群主或管理员才能邀请成员")
            # 实例化用户仓库，用来查用户信息
            users = UserRepository(session)
            # 校验邀请人账号是正常可用状态
            self._require_active_user(await users.get_by_id(request.inviter_id))
            # 实例化好友仓库，校验双向好友关系
            contacts = ContactRepository(session)
            # 获取数据库当前时间，给新增/更新记录用
            now = database_now()
            # 循环遍历每一个待邀请的用户
            for invitee_id in invitee_ids:
                # 查询被邀请人的用户信息
                invitee = await users.get_by_id(invitee_id)
                # 查邀请人到被邀请人的好友记录
                forward = await contacts.get_friend(request.inviter_id, invitee_id)
                # 反向查被邀请人到邀请人的好友记录，必须双向好友
                reverse = await contacts.get_friend(invitee_id, request.inviter_id)
                # 查询这个人在本群的成员记录，包含已退群的旧记录，加锁
                membership = await sessions.membership(
                    invitee_id,
                    request.session_id,
                    active_only=False,
                    for_update=True,
                )
                # 一堆失败条件挨个判断
                if (
                    invitee_id == request.inviter_id # 自己不能拉自己
                    or not self._is_active_user(invitee) # 被邀请人账号异常
                    or forward is None # 正向好友关系不存在
                    or reverse is None # 反向好友关系不存在
                    or forward.status != FriendStatus.NORMAL # 正向好友不是正常状态
                    or reverse.status != FriendStatus.NORMAL # 反向好友不是正常状态
                    or (membership is not None and membership.status == 0) # 已经在群里了
                ):
                    # 符合失败条件，把id放进失败列表
                    failed.append(invitee_id)
                    # 跳过当前循环，处理下一个用户
                    continue
                # 判断：之前没有这条群成员记录，新建一条
                if membership is None:
                    # 把新成员对象加入会话，事务提交才写入数据库
                    session.add(
                        UserSessionModel(
                            user_id=invitee_id,
                            session_id=request.session_id,
                            role=GroupMemberRole.MEMBER,
                            status=0,
                            created_time=now,
                            updated_time=now,
                        )
                    )
                else:
                    # 有旧记录，说明之前退群了，重新激活
                    membership.role = GroupMemberRole.MEMBER
                    membership.status = 0
                    membership.created_time = now
                    membership.updated_time = now
                # 校验全部通过，加入成功列表
                successful.append(invitee_id)
            # 判断：一个都没邀请成功，直接抛异常
            if not successful:
                # 抛出操作异常：没有有效的好友可加入群聊
                raise BusinessError(ErrorCode.OPERATION_ERROR, "没有有效的好友可加入群聊")
            # 查询当前群的总人数，通知里带上人数
            members_count = await sessions.member_count(request.session_id)
            # 初始化通知服务，用outbox保证消息可靠投递
            notifications = NotificationService(OutboxRepository(session), self._ids)
            # 循环给所有成功入群的人发通知
            for invitee_id in successful:
                # 推送入群通知
                notifications.new_group_session(
                    receiver_id=invitee_id,
                    session_id=request.session_id,
                    session_name=group.name or "群聊",
                    avatar=group.avatar,
                    creator_id=request.inviter_id,
                    members_count=members_count,
                )
        # 组装返回结构体，id转字符串给前端
        return GroupMemberMutationResponse(
            success_ids=[str(user_id) for user_id in successful],
            failed_ids=[str(user_id) for user_id in failed],
        )

    # 校验群聊是否存在、状态正常，并且是群类型会话
    @staticmethod
    def _require_group(group: SessionModel | None) -> SessionModel:
        # 判断群记录为空或者状态不是0（已解散）
        if group is None or group.status != 0:
            # 抛出异常：群聊不存在或已解散
            raise BusinessError(ErrorCode.NOT_FOUND_ERROR, "群聊不存在或已解散")
        # 判断会话类型不是群聊
        if group.type != SessionType.GROUP:
            # 抛出异常：该会话不是群聊
            raise BusinessError(ErrorCode.PARAMS_ERROR, "该会话不是群聊")
        # 校验全部通过，返回群对象
        return group

    # 校验用户是否正常可用，不通过直接抛异常
    @classmethod
    def _require_active_user(cls, user: UserModel | None) -> UserModel:
        # 调用判断方法，用户不可用就进分支
        if not cls._is_active_user(user):
            # 抛出异常：用户不存在或状态异常
            raise BusinessError(ErrorCode.FORBIDDEN_ERROR, "用户不存在或状态异常")
        # 断言user不为空，兜底保证类型安全
        assert user is not None
        # 校验通过，返回用户对象
        return user

    # 判断用户是否正常可用，返回布尔值，只做判断不抛异常
    @staticmethod
    def _is_active_user(user: UserModel | None) -> bool:
        # 用户不为空、状态正常、没有被删除，才返回true
        return user is not None and user.state == UserState.NORMAL and not user.is_delete