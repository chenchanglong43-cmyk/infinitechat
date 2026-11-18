"""Explicit async repositories for UserService-owned tables."""
# 这个文件是用户仓储层，专门封装User表的数据库操作
# 好处：SQL语句统一放在这里，业务代码不用到处写select，方便维护

from sqlalchemy import select
# select：SQLAlchemy的查询构造器，用来拼select语句

from sqlalchemy.ext.asyncio import AsyncSession
# AsyncSession：异步数据库会话，所有增删查改都要靠它

from user_service.storage.models import UserModel
# 导入用户表模型，对应数据库里的user表

# 用户仓储类，所有和用户表相关的数据库操作都写在这里
class UserRepository:
    # 构造方法，创建仓储对象的时候，必须传入数据库会话
    def __init__(self, session: AsyncSession) -> None:
        # 把会话存到实例变量里，后面查、加数据都用这个session
        self.session = session

    # 根据用户id查询单个用户
    # for_update：是否加行锁，更新数据的时候开启，防止并发争抢
    async def get_by_id(
        self,
        user_id: int,
        *,
        for_update: bool = False,
    ) -> UserModel | None:
        # 拼SQL：select * from user where user_id = 传入的id
        statement = select(UserModel).where(UserModel.user_id == user_id)

        # 注意：行锁必须在事务里面才生效，单独查询不加事务没用
        if for_update:
            statement = statement.with_for_update()

        # scalar() 拿到第一条数据，没有就返回None
        return await self.session.scalar(statement)

    # 添加一条用户数据
    # 注意：这里只是把对象放进会话缓存，不会立刻写入数据库
    # 真正入库要等外层调用 await session.commit()
    def add(self, user: UserModel) -> None:
        self.session.add(user)

    # 根据邮箱查询用户，登录场景用
    async def get_by_email(self, email: str) -> UserModel | None:
        # 拼SQL：select * from user where email = 传入邮箱
        stmt = select(UserModel).where(UserModel.email == email)
        return await self.session.scalar(stmt)