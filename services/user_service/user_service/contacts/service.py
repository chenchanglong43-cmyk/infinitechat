"""Friend relationships, applications and direct-session transactions."""
# 好友关系、好友申请、单聊会话相关的业务事务逻辑
# Chinese punctuation is part of the client-visible messages.
# 中文标点是给前端展示用的，允许保留，关闭ruff的RUF001检查
# ruff: noqa: RUF001

import re  # 导入正则模块，用来校验手机号、邮箱格式

from infinitechat.contracts import ErrorCode, PageRequest, PageResponse
# 通用契约：错误码、分页请求对象、分页返回对象
from infinitechat.contracts.enums import (
    FriendApplicationStatus,  # 好友申请状态枚举
    FriendStatus,             # 好友关系状态枚举
    GroupMemberRole,          # 群成员角色枚举
    SessionType,              # 会话类型枚举（单聊/群聊）
    UserState,                # 用户状态枚举
)
from infinitechat.contracts.http import (
    FriendApplication,  # 好友申请HTTP出参结构体
    FriendDetail,      # 好友详情HTTP出参结构体
    FriendSummary,      # 好友简要信息HTTP出参结构体
    NewSession,        # 新建会话返回结构体
)
from infinitechat.foundation.ids import SnowflakeGenerator
# 雪花ID生成器，用来生成全局唯一ID
from sqlalchemy.exc import IntegrityError
# SQLAlchemy异常：唯一约束冲突，比如重复插入主键、重复唯一索引

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
# 异步数据库会话、异步会话工厂，用来创建数据库session

from user_service.errors import BusinessError
# 自定义业务异常，业务校验不通过的时候抛出

from user_service.integrations.redis import UserRedis
# Redis封装类，用户相关缓存操作

from user_service.notifications import NotificationService
# 通知服务，用来生成websocket通知、写入outbox事件

from user_service.outbox import OutboxRepository
# 发件箱仓储，操作outbox表，实现可靠消息投递

from user_service.storage.models import ApplyFriendModel, SessionModel, UserModel, UserSessionModel
# 数据库模型：好友申请表、会话表、用户表、用户会话关联表
from user_service.storage.repositories import (
    ContactRepository,
    FriendApplicationRepository,
    SessionRepository,
    UserRepository,
    database_now,
)
# 各类仓储：联系人、好友申请、会话、用户仓储，还有获取数据库时间的工具函数

# 手机号正则：校验国内手机号，1开头，第二位3-9，后面9位数字
_PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")
# 邮箱正则：校验标准邮箱格式
_EMAIL_PATTERN = re.compile(
    r"^[a-zA-Z0-9_+&*-]+(?:\.[a-zA-Z0-9_+&*-]+)*@"
    r"(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$"
)

# 联系人服务，负责搜索用户、查看好友详情
class ContactService:
    # 初始化
    def __init__(
        self,
        *,  # * 后面所有参数必须关键字传参，防止传参顺序搞错
        session_factory: async_sessionmaker[AsyncSession],  # 会话工厂，用来随时新建数据库session
        cache: UserRedis,                                   # redis缓存实例，缓存用户相关数据
        id_generator: SnowflakeGenerator,                    # 雪花ID生成器，生成全局唯一ID
    ) -> None:
        self._sessions = session_factory
        self._cache = cache
        self._ids = id_generator

    # 搜索用户：通过手机号或者邮箱找人
    async def search(self, user_id: int, keyword: str) -> FriendDetail:
        # 去掉前后空格，判断关键字是不是空
        if not keyword.strip():
            # 搜索关键字不能为空
            raise BusinessError(ErrorCode.PARAMS_ERROR, "搜索关键字不能为空")
        # 用正则校验：必须是手机号 或者 邮箱格式
        if not (_PHONE_PATTERN.fullmatch(keyword) or _EMAIL_PATTERN.fullmatch(keyword)):
            # 格式不对，抛出参数错误
            raise BusinessError(ErrorCode.PARAMS_ERROR, "请输入有效的手机号或邮箱")
        # 从工厂取出一个数据库会话，async with 结束自动关闭session
        async with self._sessions() as session:
            # 调用用户仓储，根据手机号/邮箱查询用户
            user = await UserRepository(session).find_by_phone_or_email(keyword)
            # 查不到用户，抛出不存在异常
            if user is None:
                # 用户不存在
                raise BusinessError(ErrorCode.NOT_FOUND_ERROR, "用户不存在")
            # 组装返回给前端的好友详情结构体，带上当前登录人的user_id
            return await self._friend_detail(session, user_id, user)

    # 查看好友详情：传入好友id，拿到对方资料以及和我的好友状态
    async def detail(self, user_id: int, friend_id: int) -> FriendDetail:
        # 新建数据库会话
        async with self._sessions() as session:
            # 根据用户id查询目标用户
            user = await UserRepository(session).get_by_id(friend_id)
            # 找不到这个用户
            if user is None:
                # 用户不存在
                raise BusinessError(ErrorCode.NOT_FOUND_ERROR, "用户不存在")
            # 统一调用私有方法，封装详情组装逻辑
            return await self._friend_detail(session, user_id, user)

    # 查询好友列表，支持分页、搜索昵称
    async def list_friends(
        self,
        user_id: int,
        page: PageRequest,
        *,  # *后面参数必须关键字传参
        key: str = "",
    ) -> PageResponse[FriendSummary]:
        # 创建数据库会话，执行完自动关闭
        async with self._sessions() as session:
            # 调用联系人仓储，分页查询好友，拿到好友数据和总条数
            rows, total = await ContactRepository(session).list_friends(
                user_id,
                key=key,
                page_num=page.page_num,
                page_size=page.page_size,
            )
            # 实例化会话仓储，用来查两个人的单聊会话id
            sessions = SessionRepository(session)
            # 循环组装每一条好友摘要，给前端展示
            items = [
                FriendSummary(
                    user_id=str(user.user_id),          # 好友id转字符串返回
                    nickname=user.nickname,             # 好友昵称
                    avatar=user.avatar,                 # 好友头像
                    status=FriendStatus(relationship.status), # 好友关系状态
                    signature=user.description,         # 个性签名
                    # 查我和这个好友有没有单聊会话，有就返回会话id，没有返回None
                    session_id=(
                        str(session_id)
                        if (session_id := await sessions.direct_session(user_id, user.user_id))
                        else None
                    ),
                )
                for relationship, user in rows
            ]
        # 计算总页数：向上取整；总数为0时总页数=0
        """
        - 不加 -1：(20 + 10) //10 = 30//10 = 3 ❌ 算多一页
        - 减 1 之后：(20 +10 -1)//10 = 29//10 = 2 ✅ 正确
            (28 + 10 - 1) // 10
            = 37 // 10
            = 3
        """
        pages = (total + page.page_size - 1) // page.page_size if total else 0
        # 封装分页返回对象，包含有没有上一页、下一页标记
        return PageResponse(
            list=items,
            total=total,
            page_size=page.page_size,
            page_num=page.page_num,
            pages=pages,
            has_next=page.page_num < pages,
            has_previous=page.page_num > 1,
        )

    # 发送好友申请
    async def send_friend_request(
        self,
        sender_id: int,
        receiver_id: int,
        message: str,
    ) -> bool:
        # 不能自己加自己
        if sender_id == receiver_id:
            raise BusinessError(ErrorCode.OPERATION_ERROR, "不能添加自己")
        try:
            # 开启会话 + 事务，里面所有操作要么全部成功，要么全部回滚
            async with self._sessions() as session, session.begin():
                users = UserRepository(session)
                # 校验发送人存在且状态正常
                sender = self._active_user(await users.get_by_id(sender_id), "发送者用户不存在")
                # 校验接收人存在且状态正常
                self._active_user(await users.get_by_id(receiver_id), "接收者用户不存在")
                # 校验两人已经是好友，直接报错
                if await ContactRepository(session).get_friend(sender_id, receiver_id):
                    raise BusinessError(ErrorCode.OPERATION_ERROR, "已经是好友关系，无需重复添加")

                applications = FriendApplicationRepository(session)
                # 查询【我发给对方】这条申请记录，for_update上锁，防止并发重复提交
                application = await applications.get_pair(
                    sender_id,
                    receiver_id,
                    for_update=True,
                )
                now = database_now()
                # 没有历史申请，新建一条申请记录
                if application is None:
                    application = ApplyFriendModel(
                        apply_friend_id=self._ids.next_id(),
                        sender_id=sender_id,
                        receiver_id=receiver_id,
                        message=message,
                        status=FriendApplicationStatus.UNREAD,
                        created_time=now,
                        updated_time=now,
                    )
                    applications.add(application)
                # 如果这条申请已经被同意，说明已经是好友，报错
                elif application.status == FriendApplicationStatus.ACCEPTED:
                    raise BusinessError(
                        ErrorCode.OPERATION_ERROR,
                        "已经是好友关系，无需重复添加",
                    )
                # 申请存在但还没处理（待读/拒绝），直接覆盖留言、重置为未读状态
                else:
                    application.status = FriendApplicationStatus.UNREAD
                    application.message = message
                    application.updated_time = now

                # 初始化通知服务，outbox和雪花id注入进去
                notifications = NotificationService(OutboxRepository(session), self._ids)
                # 生成websocket通知，推送给接收人（弹窗小红点）
                notifications.friend_application(
                    sender_id=sender_id,
                    receiver_id=receiver_id,
                    nickname=sender.nickname,
                    avatar=sender.avatar,
                    message=message,
                )
                # 生成Kafka业务事件，写入outbox，供其他服务消费
                notifications.friend_request_created(application.apply_friend_id)
        # 捕获唯一索引冲突异常，并发重复提交的时候触发
        except IntegrityError as error:
            raise BusinessError(ErrorCode.OPERATION_ERROR, "好友申请已存在，请重试") from error
        return True
    # 查询好友申请列表，包含我发起的、别人发给我的申请，分页返回
    async def applications(
        self,
        user_id: int,
        page: PageRequest,
    ) -> PageResponse[FriendApplication]:
        # 开一个数据库会话，用完自动关闭
        async with self._sessions() as session:
            # 调用好友申请仓储，拿到申请记录列表和总条数
            applications, total = await FriendApplicationRepository(session).list_for_user(
                user_id,
                page_num=page.page_num,
                page_size=page.page_size,
            )
            # 收集所有对方用户id，用来批量查用户信息，避免循环查库
            """
                举例子帮你看懂：
                假设 user_id = 100（我自己 ID）
                applications 里面有 3 条申请：
                
                1. sender_id=100，receiver_id=200 → 我发给 200
                2. sender_id=300，receiver_id=100 → 300 发给我
                3. sender_id=100，receiver_id=200 → 我发给 200（重复）
                执行推导式：
                
                - 第一条：取 200
                - 第二条：取 300
                - 第三条：取 200
            """
            target_ids = {
                # 如果这条申请是我发的，对方就是接收人
                application.receiver_id
                if application.sender_id == user_id
                # 如果这条申请是别人发给我的，对方就是发送人
                else application.sender_id
                for application in applications
            }
            # 根据收集到的id，一次性批量查询用户
            users = await UserRepository(session).list_by_ids(tuple(target_ids))
            # 准备一个空列表，用来装组装好给前端的数据
            items = []
            # 循环遍历每一条好友申请
            for application in applications:
                # 判断：我是不是这条申请的接收方？是=1，不是=0
                is_receiver = int(application.receiver_id == user_id)
                # 拿到对方的用户id：我是接收人，对方就是申请人；反之对方是接收人
                target_id = application.sender_id if is_receiver else application.receiver_id
                # 从批量查出来的用户字典里取出对方信息
                target = users.get(target_id)
                # 对方用户找不到，直接跳过这条申请
                if target is None:
                    continue
                # 组装前端需要的申请结构体，放进列表
                items.append(
                    FriendApplication(
                        user_id=str(target.user_id),  # 对方id转字符串返回
                        nickname=target.nickname,     # 对方昵称
                        avatar=target.avatar,         # 对方头像
                        msg=application.message,      # 申请留言
                        status=FriendApplicationStatus(application.status), # 申请状态
                        time=application.updated_time, # 更新时间
                        is_receiver=is_receiver,      # 标记我是不是接收方
                    )
                )
        # 向上取整算总页数，总数是0的时候页数直接给0
        pages = (total + page.page_size - 1) // page.page_size if total else 0
        # 封装分页返回对象，带上上下一页标记
        return PageResponse(
            list=items,
            total=total,
            page_size=page.page_size,
            page_num=page.page_num,
            pages=pages,
            has_next=page.page_num < pages,
            has_previous=page.page_num > 1,
        )

    # 获取未读好友申请数量，前端用来做小红点
    async def unread_count(self, user_id: int) -> int:
        # 新建数据库会话
        async with self._sessions() as session:
            # 调用仓储统计未读申请数量，直接返回
            return await FriendApplicationRepository(session).unread_count(user_id)

    # 统一处理好友申请：标记已读、同意、拒绝
    async def modify_application(
        self,
        receiver_id: int,
        sender_ids: list[int],
        status: int,
    ) -> NewSession | bool:
        try:
            # 把前端传的数字转成枚举，顺便校验这个状态合法不
            target_status = FriendApplicationStatus(status)
        except ValueError as error:
            # 转枚举失败，说明状态值不对，抛参数错误异常
            raise BusinessError(ErrorCode.PARAMS_ERROR, "不允许修改为该状态值") from error
        # 分支逻辑：标记已读，这个支持批量操作
        if target_status is FriendApplicationStatus.READ:
            # 开会话+事务，里面操作要么全部成功，要么回滚
            async with self._sessions() as session, session.begin():
                # 调用仓储，把这些申请标记成已读
                updated = await FriendApplicationRepository(session).mark_read(
                    receiver_id,
                    sender_ids,
                )
                # 更新行数<=0，说明没有找到可以标记的申请，报错
                if updated <= 0:
                    raise BusinessError(ErrorCode.OPERATION_ERROR, "标记已读失败")
            # 标记成功，返回true
            return True
        # 状态不是同意、也不是拒绝，直接报错
        if target_status not in {
            FriendApplicationStatus.ACCEPTED,
            FriendApplicationStatus.REJECTED,
        }:
            raise BusinessError(ErrorCode.PARAMS_ERROR, "不允许修改为该状态值")
        # 同意或者拒绝，一次只能处理一条，不能批量
        if len(sender_ids) != 1:
            # 判断是通过还是拒绝，用来拼接提示文字
            action = "通过" if target_status is FriendApplicationStatus.ACCEPTED else "拒绝"
            raise BusinessError(ErrorCode.PARAMS_ERROR, f"{action}状态只能包含一个接收者")
        # 取出唯一的申请人id
        sender_id = sender_ids[0]
        # 默认返回true；同意好友的时候会替换成会话对象
        result: NewSession | bool = True
        # 开启事务，保证多张表数据一致性
        async with self._sessions() as session, session.begin():
            # 实例化好友申请仓储
            applications = FriendApplicationRepository(session)
            # 上锁查询这条申请，防止并发重复处理
            application = await applications.get_pair(sender_id, receiver_id, for_update=True)
            # 查不到这条申请，抛不存在异常
            if application is None:
                raise BusinessError(ErrorCode.NOT_FOUND_ERROR, "好友申请不存在")
            # 申请已经被同意/拒绝了，不能再处理，报错
            if application.status not in {
                FriendApplicationStatus.UNREAD,
                FriendApplicationStatus.READ,
            }:
                raise BusinessError(ErrorCode.OPERATION_ERROR, "该好友申请已处理或已过期")
            # 执行状态流转，限制只能从未读/已读转到目标状态
            transitioned = await applications.transition(
                application.apply_friend_id,
                from_statuses=(
                    FriendApplicationStatus.UNREAD,
                    FriendApplicationStatus.READ,
                ),
                to_status=target_status,
            )
            # 状态流转失败，说明已经被别人处理过了
            if not transitioned:
                raise BusinessError(ErrorCode.OPERATION_ERROR, "该好友申请已处理或已过期")
            # 如果是同意好友，调用私有方法，建好友关系、创建单聊会话
            if target_status is FriendApplicationStatus.ACCEPTED:
                result = await self._accept_application(session, receiver_id, sender_id)
        # 同意好友之后，删掉redis缓存，防止缓存是旧数据
        if target_status is FriendApplicationStatus.ACCEPTED:
            await self._cache.evict_friend_status(receiver_id, sender_id)
        # 返回结果，同意就返回会话信息，拒绝返回true
        return result
    # 删除好友
    async def delete_friend(self, user_id: int, friend_id: int) -> bool:
        # 开启数据库会话，同时开启事务，出错自动回滚
        async with self._sessions() as session, session.begin():
            # 实例化联系人仓储
            contacts = ContactRepository(session)
            # 上锁查询这条好友关系，防止并发删好友
            if await contacts.get_friend(user_id, friend_id, for_update=True) is None:
                # 查不到好友关系，抛出异常
                raise BusinessError(ErrorCode.NOT_FOUND_ERROR, "好友关系不存在")
            # 调用仓储，删掉双向好友记录
            await contacts.delete_relationship(user_id, friend_id)
        # 删除redis里的好友缓存，避免脏数据
        await self._cache.evict_friend_status(user_id, friend_id)
        # 删除成功，返回true
        return True

    # 拉黑好友
    async def block_friend(self, user_id: int, friend_id: int) -> bool:
        # 开启数据库会话和事务，保证操作原子性
        async with self._sessions() as session, session.begin():
            # 实例化联系人仓储
            contacts = ContactRepository(session)
            # 上锁检查好友关系是否存在
            if await contacts.get_friend(user_id, friend_id, for_update=True) is None:
                # 没有好友关系，不能拉黑
                raise BusinessError(ErrorCode.NOT_FOUND_ERROR, "好友关系不存在")
            # 修改好友状态为拉黑
            result = await contacts.set_status(user_id, friend_id, FriendStatus.BLOCKED)
        # 清理缓存，让好友状态及时更新
        await self._cache.evict_friend_status(user_id, friend_id)
        # 返回修改结果
        return result

    # 取消拉黑
    async def unblock_friend(self, user_id: int, friend_id: int) -> bool:
        # 开启会话+事务，保证数据安全
        async with self._sessions() as session, session.begin():
            # 实例化联系人仓储
            contacts = ContactRepository(session)
            # 上锁查询好友关系记录
            relationship = await contacts.get_friend(user_id, friend_id, for_update=True)
            # 好友关系不存在，报错
            if relationship is None:
                raise BusinessError(ErrorCode.NOT_FOUND_ERROR, "好友关系不存在")
            # 判断当前状态是不是拉黑，不是就不让取消拉黑
            if relationship.status != FriendStatus.BLOCKED:
                raise BusinessError(ErrorCode.OPERATION_ERROR, "该好友未被拉黑")
            # 把状态改回正常好友
            result = await contacts.set_status(user_id, friend_id, FriendStatus.NORMAL)
        # 清除缓存，刷新好友状态
        await self._cache.evict_friend_status(user_id, friend_id)
        # 返回结果
        return result
    # 私有方法：接受好友申请，建立好友关系、创建单聊会话
    async def _accept_application(
        self,
        session: AsyncSession,
        receiver_id: int,
        sender_id: int,
    ) -> NewSession:
        # 实例化用户仓储
        users = UserRepository(session)
        # 校验接收人是正常可用用户
        receiver = self._active_user(await users.get_by_id(receiver_id), "接收者用户不存在")
        # 校验申请人是正常可用用户
        sender = self._active_user(await users.get_by_id(sender_id), "好友申请者不存在")
        # 实例化联系人仓储
        contacts = ContactRepository(session)
        # 先查一遍，防止已经是好友
        if await contacts.get_friend(receiver_id, sender_id) is not None:
            # 已经是好友关系
            raise BusinessError(ErrorCode.OPERATION_ERROR, "已经是好友关系")
        # 写入双向好友记录，A是B好友，B也是A好友
        contacts.add_bidirectional(receiver_id, sender_id)
        # 生成新的会话id
        session_id = self._ids.next_id()
        # 实例化会话仓储
        sessions = SessionRepository(session)
        # 新增一条会话记录，类型是单聊
        sessions.add_session(
            SessionModel(
                session_id=session_id,
                name="",
                type=SessionType.DIRECT,
                status=0,
                avatar=None,
            )
        )
        # 添加两个会话成员，双方都进这个会话
        sessions.add_members(
            [
                UserSessionModel(
                    user_id=receiver_id,
                    session_id=session_id,
                    role=GroupMemberRole.MEMBER,
                    status=0,
                ),
                UserSessionModel(
                    user_id=sender_id,
                    session_id=session_id,
                    role=GroupMemberRole.MEMBER,
                    status=0,
                ),
            ]
        )
        # 发一条单聊创建通知，写入outbox，推送给前端
        NotificationService(OutboxRepository(session), self._ids).new_direct_session(
            sender_id=receiver_id,
            receiver_id=sender_id,
            session_id=session_id,
            session_name=receiver.nickname,
            avatar=receiver.avatar,
        )
        # 组装会话信息返回给接口
        return NewSession(
            user_id=str(sender.user_id),
            session_id=str(session_id),
            session_type=SessionType.DIRECT,
            session_name=sender.nickname,
            avatar=sender.avatar,
        )

    # 私有方法：组装好友详情，给搜索用户、查看资料接口复用
    async def _friend_detail(
        self,
        session: AsyncSession,
        user_id: int,
        target: UserModel,
    ) -> FriendDetail:
        # 判断目标账号是否被封禁
        if target.state == UserState.BANNED:
            # 该用户已被封禁
            raise BusinessError(ErrorCode.FORBIDDEN_ERROR, "该用户已被封禁")
        # 判断账号是否注销或者删除
        if target.state == UserState.CANCELLED or target.is_delete:
            # 该用户已注销
            raise BusinessError(ErrorCode.NOT_FOUND_ERROR, "该用户已注销")
        # 查询我和对方的好友关系
        relationship = await ContactRepository(session).get_friend(user_id, target.user_id)
        # 查询我和对方有没有单聊会话
        session_id = await SessionRepository(session).direct_session(user_id, target.user_id)
        # 组装详情结构体返回
        return FriendDetail(
            user_id=str(target.user_id),
            nickname=target.nickname,
            avatar=target.avatar,
            email=target.email,
            phone=target.phone,
            signature=target.description,
            gender=target.gender,
            session_id=str(session_id) if session_id is not None else None,
            # 有好友关系就取对应状态，没有就是非好友
            status=(FriendStatus(relationship.status) if relationship else FriendStatus.NON_FRIEND),
        )

    # 静态工具方法，校验用户是否正常可用
    @staticmethod
    def _active_user(user: UserModel | None, message: str) -> UserModel:
        # 用户为空、状态不正常、已删除，直接抛异常
        if user is None or user.state != UserState.NORMAL or user.is_delete != 0:
            raise BusinessError(ErrorCode.NOT_FOUND_ERROR, message)
        # 校验通过，返回用户对象
        return user


