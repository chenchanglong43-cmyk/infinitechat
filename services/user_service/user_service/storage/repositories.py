"""Explicit async repositories for UserService-owned tables."""
# 用户仓储层，专门封装user表的数据库操作，把SQL逻辑和业务代码分开

from typing import cast

# sqlalchemy 查询语句
from sqlalchemy import select
# 异步数据库会话
from sqlalchemy.ext.asyncio import AsyncSession

# 用户表ORM模型
from user_service.storage.models import UserModel


# 用户仓储类，只负责和user表打交道
class UserRepository:
    # 构造方法：传入数据库会话，后面所有查询都复用这个session
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # 根据用户id查询用户
    async def get_by_id(
        self,
        user_id: int,
        *,  # 后面参数必须关键字传参，防止传参顺序搞错
        for_update: bool = False,  # 是否加行锁
    ) -> UserModel | None:
        # 组装查询语句：根据user_id匹配
        statement = select(UserModel).where(UserModel.user_id == user_id)
        # 如果需要行锁，加上with_for_update，事务内锁住这条记录，防止并发争抢
        if for_update:
            statement = statement.with_for_update()
        # scalar拿到单条结果，cast做类型提示转换，告诉IDE返回类型
        return  await self.session.scalar(statement)

    # 根据邮箱查询用户，默认只查未逻辑删除的账号
    async def get_by_email(self, email: str, *, for_update: bool = False) -> UserModel | None:
        # 查询条件：邮箱匹配 + is_delete=0（没有被逻辑删除）
        statement = select(UserModel).where(
            UserModel.email == email,
            UserModel.is_delete == 0,
        )
        # 开启行锁，并发注册/登录场景防超卖、重复注册
        if for_update:
            statement = statement.with_for_update()
        # 执行查询，返回用户对象或者None
        return await self.session.scalar(statement)

    # 添加用户到会话，注意：只是加入session，不是立刻入库，等事务commit才真正写数据库
    def add(self, user: UserModel) -> None:
        self.session.add(user)