"""Explicit async repositories for UserService-owned tables."""

from collections.abc import Sequence
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from user_service.storage.models import UserModel

# 用户仓储层，专门把用户表所有数据库操作打包在这里，业务层不用写原生SQL
class UserRepository:
    # 初始化，绑定数据库会话，这个仓储里所有操作都共用这一个session
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # 根据用户ID查询单条用户数据
    async def get_by_id(
        self,
        user_id: int,
        *,  # 星号：后面参数只能用关键字传，防止参数顺序搞混传错
        for_update: bool = False,
    ) -> UserModel | None:
        # 拼查询语句：查UserModel，条件是user_id相等
        statement = select(UserModel).where(UserModel.user_id == user_id)
        # 如果需要加行锁，就拼接with_for_update
        # 加锁之后，别的事务不能修改这条记录，直到当前事务提交
        if for_update:
            statement = statement.with_for_update()
        # scalar拿单条结果，cast做类型转换，让IDE识别类型
        return await self.session.scalar(statement)

    # 根据邮箱查用户，只查询未逻辑删除的数据
    async def get_by_email(self, email: str, *, for_update: bool = False) -> UserModel | None:
        # 查询条件：邮箱匹配 + is_delete=0（没删）
        statement = select(UserModel).where(
            UserModel.email == email,
            UserModel.is_delete == 0,
        )
        # 开启行锁，注册场景用来防止并发重复注册
        if for_update:
            statement = statement.with_for_update()
        return  await self.session.scalar(statement)

    # 批量根据一组用户id查询用户，返回 id:用户对象 的字典，上层直接按id取，查找更快
    async def list_by_ids(self, user_ids: Sequence[int]) -> dict[int, UserModel]:
        # 传空id列表直接返回空字典，避免生成没用的SQL去查库
        if not user_ids:
            return {}
        # in批量查询，只查没逻辑删除的用户
        users = (
            await self.session.scalars(
                select(UserModel).where(
                    UserModel.user_id.in_(user_ids),
                    UserModel.is_delete == 0,
                )
            )
        ).all()
        # 列表转字典，key是user_id，业务层用起来很方便
        return {user.user_id: user for user in users}

    # 把用户模型对象加到session会话里，只是标记，提交事务的时候才真正入库
    def add(self, user: UserModel) -> None:
        self.session.add(user)