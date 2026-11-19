"""Explicit async repositories for UserService-owned tables."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from infinitechat.contracts.enums import FriendApplicationStatus, FriendStatus, SessionType

from sqlalchemy import String, and_, delete, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from user_service.storage.models import (
    ApplyFriendModel,
    FriendModel,
    SessionModel,
    UserModel,
    UserSessionModel,
)

# 获取业务当前时间，固定上海时区
# 为啥去掉tzinfo？数据库存的是不带时区的datetime，两边格式保持一致
def database_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)

# 用户仓储类，所有和用户表相关的数据库操作都放这里
# 仓储的作用：把SQL查询逻辑收拢，业务service层不用写select
class UserRepository:
    # 初始化，把数据库会话传进来，这个会话贯穿这一次的数据库操作
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # 根据用户ID查询单个用户
    # for_update 这个参数很关键：开启的话会给这条记录加行锁，别的事务暂时改不了它，用来处理并发
    # 这里的 * 意思：*后面的参数必须用关键字传参，防止传参顺序搞混踩坑
    async def get_by_id(
        self,
        user_id: int,
        *,
        for_update: bool = False,
    ) -> UserModel | None:
        # 拼SQL：select * from user where user_id = xxx
        statement = select(UserModel).where(UserModel.user_id == user_id)
        # 如果要加行锁，就加上 for update
        if for_update:
            statement = statement.with_for_update()
        # scalar 拿到第一条结果，找不到就返回 None
        return await self.session.scalar(statement)

    # 根据邮箱查用户，只查没被逻辑删除的用户
    async def get_by_email(self, email: str, *, for_update: bool = False) -> UserModel | None:
        statement = select(UserModel).where(
            UserModel.email == email,
            UserModel.is_delete == 0,  # 逻辑删除标记，0=正常，1=已删
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    # 传入手机号或者邮箱，自动匹配查找用户
    # 登录场景用的多，用户输入账号，分不清是手机号还是邮箱，直接丢这个方法
    async def find_by_phone_or_email(self, keyword: str) -> UserModel | None:
        return await self.session.scalar(
            select(UserModel).where(
                    UserModel.is_delete == 0,
                    or_(UserModel.phone == keyword, UserModel.email == keyword),
                )
            )


    # 批量根据一堆用户ID查用户
    # 返回字典 key=user_id，value=用户对象，外面循环拿数据非常方便
    async def list_by_ids(self, user_ids: Sequence[int]) -> dict[int, UserModel]:
        # 传空数组直接返回空字典，避免生成无效的SQL，浪费数据库性能
        if not user_ids:
            return {}
        # in 查询，一次性查出这批id对应的用户
        users = (
            await self.session.scalars(
                select(UserModel).where(
                    UserModel.user_id.in_(user_ids),
                    UserModel.is_delete == 0,
                )
            )
        ).all()
        # 把查到的列表转成字典，方便根据id快速取
        return {user.user_id: user for user in users}

    # 新增用户，只是把对象加到会话里缓存起来
    # 注意：这里不会立刻插入数据库！要等上层事务commit才真正写入
    def add(self, user: UserModel) -> None:
        self.session.add(user)


# 会话仓储，专门管会话表、会话成员关联表的所有数据库操作
class SessionRepository:
    # 初始化，把异步数据库会话传进来，后面所有查询都共用这个session
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # 添加一条会话记录
    # 注意：只是加到session缓存，commit之后才真正写到数据库
    def add_session(self, session_model: SessionModel) -> None:
        self.session.add(session_model)

    # 批量添加多个会话成员记录
    # add_all 适合一次性塞一堆对象，比循环add性能好一点
    # 给调用方松绑，列表元组都接收，代码更好用，类型校验又不炸
    def add_members(self, members: Sequence[UserSessionModel]) -> None:
        self.session.add_all(members)

    # 根据会话ID查询会话信息
    # for_update=True 会上行锁，并发修改会话的时候用
    async def get_session(
        self,
        session_id: int,
        *,  # 后面参数强制关键字传参，防止传参顺序搞错
        for_update: bool = False,
    ) -> SessionModel | None:
        statement = select(SessionModel).where(SessionModel.session_id == session_id)
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    # 获取某个会话里面所有成员的user_id列表
    # active_only=True 只查状态正常的成员，过滤掉退出、被踢出的人
    async def user_ids(self, session_id: int, *, active_only: bool = True) -> list[int]:
        statement = select(UserSessionModel.user_id).where(
            UserSessionModel.session_id == session_id
        )
        if active_only:
            statement = statement.where(UserSessionModel.status == 0)
        # 把查到的结果转成普通列表返回
        return list((await self.session.scalars(statement)).all())

    # 获取某个用户加入的所有会话id列表
    # active_only=True 只保留正常有效的会话
    async def session_ids(self, user_id: int, *, active_only: bool = True) -> list[int]:
        statement = select(UserSessionModel.session_id).where(UserSessionModel.user_id == user_id)
        if active_only:
            statement = statement.where(UserSessionModel.status == 0)
        return list((await self.session.scalars(statement)).all())

    # 查询某个用户和某个会话的成员关系记录
    # 用来判断：这个人到底在不在这个会话里面
    async def membership(
        self,
        user_id: int,
        session_id: int,
        *,
        active_only: bool = True,
        for_update: bool = False,
    ) -> UserSessionModel | None:
        statement = select(UserSessionModel).where(
            UserSessionModel.user_id == user_id,
            UserSessionModel.session_id == session_id,
        )
        if active_only:
            statement = statement.where(UserSessionModel.status == 0)
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    # 统计一个会话里正常状态的成员数量
    # 只算status=0有效的成员，退群的不算
    async def member_count(self, session_id: int) -> int:
        value = await self.session.scalar(
            select(func.count())
            .select_from(UserSessionModel)
            .where(
                UserSessionModel.session_id == session_id,
                UserSessionModel.status == 0,
            )
        )
        # 防止查出来是None，没有成员的时候返回0
        return int(value or 0)

    # 查找两个人之间已经存在的私聊会话
    # 逻辑：先拿到A用户所有会话，再在里面找B用户也在里面、并且类型是单聊的会话
    """
        1. 加好友的时候先查一遍，避免重复创建私聊窗口，两个人永远只保留一个私聊会话
        2. 删除好友的时候查到这个会话 ID，把私聊会话一起删掉
    """
    async def direct_session(self, first_user_id: int, second_user_id: int) -> int | None:
        # 子查询：拿到第一个用户所有有效的会话
        first_sessions = select(UserSessionModel.session_id).where(
            UserSessionModel.user_id == first_user_id,
            UserSessionModel.status == 0,
        )
        return await self.session.scalar(
                select(UserSessionModel.session_id)
                # 关联会话主表，用来过滤会话类型是单聊
                .join(SessionModel, SessionModel.session_id == UserSessionModel.session_id)
                .where(
                    UserSessionModel.user_id == second_user_id,
                    UserSessionModel.status == 0,
                    # 会话必须在第一个用户的会话列表里面
                    UserSessionModel.session_id.in_(first_sessions),
                    # 会话类型是私聊
                    SessionModel.type == SessionType.DIRECT,
                    SessionModel.status == 0,
                )
                .limit(1)  # 找到一条就够了，两个人只能有一个私聊会话
            )


# 联系人仓储，专门处理好友关系、好友申请相关的数据库操作
class ContactRepository:
    # 初始化，把异步db会话传进来，后面所有操作共用这个session
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # 查询单条好友记录：查 user_id 和 friend_id 的好友关系
    # for_update=True 会上行锁，处理好友同意、删除这种并发场景用
    async def get_friend(
        self,
        user_id: int,
        friend_id: int,
        *,  # 后面参数必须关键字传参，防止传参顺序搞错
        for_update: bool = False,
    ) -> FriendModel | None:
        statement = select(FriendModel).where(
            FriendModel.user_id == user_id,
            FriendModel.friend_id == friend_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    # 好友分页列表，支持搜索昵称、手机号、用户ID
    # 返回值：(好友+用户信息的列表, 总条数)，给前端做分页
    async def list_friends(
        self,
        user_id: int,
        *,
        key: str,  # 搜索关键词，空字符串就是不搜索
        page_num: int,
        page_size: int,
    ) -> tuple[list[tuple[FriendModel, UserModel]], int]:
        # 基础过滤条件：是这个用户的好友、好友关系没有被删除、对方账号没有逻辑删除
        filters = [
            FriendModel.user_id == user_id,
            FriendModel.status != FriendStatus.DELETED,
            UserModel.is_delete == 0,
        ]
        # 如果传了搜索词，追加模糊查询条件，满足任意一个就匹配上
        if key:
            filters.append(
                or_(
                    UserModel.nickname.contains(key),  # 昵称模糊匹配
                    UserModel.phone.contains(key),     # 手机号模糊匹配
                    func.cast(UserModel.user_id, String).contains(key),  # 用户ID转字符串再模糊匹配
                )
            )
        # 关联条件：好友表里的friend_id 对应用户表的user_id
        join_condition = UserModel.user_id == FriendModel.friend_id

        # 先查总数量，用来算分页
        total = await self.session.scalar(
            select(func.count())  # 我想要：count计数结果
            .select_from(FriendModel)  # 数据源起点：FriendModel好友表
            .join(UserModel, join_condition)
            .where(*filters)
        )
        # 分页查数据，同时查出好友关系记录 + 对方用户信息
        rows = (
            await self.session.execute(
                select(FriendModel, UserModel)
                .join(UserModel, join_condition)
                .where(*filters)
                .order_by(FriendModel.created_time.desc())  # 新加的好友排在前面
                .offset((page_num - 1) * page_size)  # 跳过前面已经展示过的数据
                .limit(page_size)  # 一页最多拿这么多条
            )
        ).all()
        # 把结果整理成元组列表，再返回数据和总数
        return [(row[0], row[1]) for row in rows], int(total or 0)

    # 双向新增好友，A加B好友，同时生成两条好友记录
    # 好友表是单向存储的，A的好友列表有B，B的好友列表也要有A，所以一次性加两条
    def add_bidirectional(self, first_user_id: int, second_user_id: int) -> None:
        now = database_now()
        self.session.add_all(
            [
                FriendModel(
                    user_id=first_user_id,
                    friend_id=second_user_id,
                    status=FriendStatus.NORMAL,
                    created_time=now,
                    updated_time=now,
                ),
                FriendModel(
                    user_id=second_user_id,
                    friend_id=first_user_id,
                    status=FriendStatus.NORMAL,
                    created_time=now,
                    updated_time=now,
                ),
            ]
        )

    # 修改好友状态，比如拉黑、解除好友
    # 返回True代表确实更新到了这条记录，False就是没找到这条好友关系
    async def set_status(self, user_id: int, friend_id: int, status: FriendStatus) -> bool:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(FriendModel)
                .where(
                    FriendModel.user_id == user_id,
                    FriendModel.friend_id == friend_id,
                )
                .values(status=status, updated_time=database_now())
            ),
        )
        # rowcount 是本次SQL影响的行数，等于1说明修改成功
        return result.rowcount == 1

    # 删除好友关系，连带清理所有关联数据
    # 1. 删除两个人之间所有的好友申请记录
    # 2. 删除双向的两条好友记录
    # 3. 找到他俩的私聊会话，删掉会话成员、删掉会话本身
    async def delete_relationship(self, user_id: int, friend_id: int) -> None:
        # 删掉双向的好友申请记录（A发给B、B发给A的申请都清掉）
        await self.session.execute(
            delete(ApplyFriendModel).where(
                or_(
                    and_(
                        ApplyFriendModel.sender_id == user_id,
                        ApplyFriendModel.receiver_id == friend_id,
                    ),
                    and_(
                        ApplyFriendModel.sender_id == friend_id,
                        ApplyFriendModel.receiver_id == user_id,
                    ),
                )
            )
        )
        # 删除双向好友记录
        await self.session.execute(
            delete(FriendModel).where(
                or_(
                    and_(FriendModel.user_id == user_id, FriendModel.friend_id == friend_id),
                    and_(FriendModel.user_id == friend_id, FriendModel.friend_id == user_id),
                )
            )
        )
        # 查找他俩的私聊会话
        session_id = await SessionRepository(self.session).direct_session(user_id, friend_id)
        if session_id is not None:
            # 先删会话成员关联记录，再删会话主记录
            await self.session.execute(
                delete(UserSessionModel).where(UserSessionModel.session_id == session_id)
            )
            await self.session.execute(
                delete(SessionModel).where(SessionModel.session_id == session_id)
            )


# 好友申请仓储，专门管好友申请这张表的所有数据库操作
class FriendApplicationRepository:
    # 构造方法，初始化
    # 把数据库会话传进来，后面所有查询、新增、修改都共用这一个session
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # 查询一条单向的好友申请记录：谁发给谁的申请
    # sender_id：发起加好友的人
    # receiver_id：接收这个申请的人
    # for_update：要不要上排他行锁，处理同意、拒绝的时候防止并发乱改数据
    async def get_pair(
        self,
        sender_id: int,
        receiver_id: int,
        *,  # 这个星号：后面的参数必须用关键字传参，防止传参顺序搞混
        for_update: bool = False,
    ) -> ApplyFriendModel | None:
        # 拼查询语句：找 申请人=sender_id 并且接收人=receiver_id 的申请记录
        statement = select(ApplyFriendModel).where(
            ApplyFriendModel.sender_id == sender_id,
            ApplyFriendModel.receiver_id == receiver_id,
        )
        # 如果需要上锁，就加上for update，别的事务暂时不能改这条数据
        if for_update:
            statement = statement.with_for_update()
        # scalar拿第一条结果，找不到就返回None
        return  await self.session.scalar(statement)

    # 根据申请记录的主键id，单独查这条好友申请
    async def get_by_id(
        self,
        apply_friend_id: int,
        *,
        for_update: bool = False,
    ) -> ApplyFriendModel | None:
        # 按申请主键去匹配
        statement = select(ApplyFriendModel).where(
            ApplyFriendModel.apply_friend_id == apply_friend_id
        )
        # 并发场景上锁
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    # 新增一条好友申请
    # 注意：只是加到session缓存里面，还没真正进数据库，等上层commit才写入
    def add(self, application: ApplyFriendModel) -> None:
        self.session.add(application)

    # 查询和我相关的所有好友申请，分页返回
    # 包含两种：我发给别人的申请、别人发给我的申请
    async def list_for_user(
        self,
        user_id: int,
        *,
        page_num: int,
        page_size: int,
    ) -> tuple[list[ApplyFriendModel], int]:
        # 筛选条件：我是申请人 OR 我是接收人，只要跟我有关全部查出来
        predicate = or_(
            ApplyFriendModel.sender_id == user_id,
            ApplyFriendModel.receiver_id == user_id,
        )
        # 先统计符合条件的总条数，用来给前端做分页
        total = await self.session.scalar(
            select(func.count()).select_from(ApplyFriendModel).where(predicate)
        )
        # 分页查询申请数据
        rows = (
            await self.session.scalars(
                select(ApplyFriendModel)
                .where(predicate)  # 套上面那个条件，只拿和我有关的申请
                .order_by(ApplyFriendModel.updated_time.desc())  # 按更新时间倒序，最新的申请放最顶上
                .offset((page_num - 1) * page_size)  # 偏移量：跳过前面已经看过的页的数据
                .limit(page_size)  # 限制一页最多拿多少条
            )
        ).all()  # 把查到的结果全部拿出来
        # 转成普通列表，返回【申请列表，总条数】给上层
        return list(rows), int(total or 0)

    # 统计发给我的、还没读的好友申请数量，用来做小红点
    async def unread_count(self, receiver_id: int) -> int:
        value = await self.session.scalar(
            select(func.count())
            .select_from(ApplyFriendModel)  # 从好友申请表查
            .where(
                ApplyFriendModel.receiver_id == receiver_id,  # 接收人是我
                ApplyFriendModel.status == FriendApplicationStatus.UNREAD,  # 状态是未读
            )
        )
        # 防止查出来是None，没有未读就返回0
        return int(value or 0)

    # 批量标记好友申请为已读
    # receiver_id：接收人也就是我，sender_ids：一批申请人id
    # 只改状态是未读的记录，返回成功修改的条数
    async def mark_read(self, receiver_id: int, sender_ids: Sequence[int]) -> int:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(ApplyFriendModel)
                .where(
                    ApplyFriendModel.receiver_id == receiver_id,  # 接收人是我
                    ApplyFriendModel.sender_id.in_(sender_ids),  # 申请人在这批id里面
                    ApplyFriendModel.status == FriendApplicationStatus.UNREAD,  # 只改未读的
                )
                .values(status=FriendApplicationStatus.READ, updated_time=database_now())  # 改成已读，同时更新时间
            ),
        )
        # rowcount 是这条update语句影响了多少行
        return result.rowcount

    # 状态迁移，安全修改申请状态（同意/拒绝都走这个）
    # from_statuses：允许从哪些状态改过来；to_status：要改成什么状态
    # 好处：防止重复同意、重复拒绝，避免脏数据
    async def transition(
        self,
        apply_friend_id: int,
        *,
        from_statuses: Sequence[FriendApplicationStatus],
        to_status: FriendApplicationStatus,
    ) -> bool:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(ApplyFriendModel)
                .where(
                    ApplyFriendModel.apply_friend_id == apply_friend_id,  # 锁定这条申请
                    ApplyFriendModel.status.in_(from_statuses),  # 当前状态必须在允许的状态列表里才能改
                )
                .values(status=to_status, updated_time=database_now())  # 更新成目标状态，刷新更新时间
            ),
        )
        # 修改成功影响行数=1返回True；找不到或者状态不对，返回False
        return result.rowcount == 1