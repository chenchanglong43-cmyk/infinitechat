"""UserService SQLAlchemy models and repositories."""
# UserService 模型统一导出入口，其他地方导入模型直接从这里引入，不用关心各模型文件路径

from user_service.storage.models import (
    ApplyFriendModel,
    Base,
    FriendModel,
    OutboxEventModel,
    SessionModel,
    UserModel,
    UserSessionModel,
)

# __all__ 控制 import * 时对外暴露的模型清单，收口统一管理
__all__ = (
    "ApplyFriendModel",
    "Base",
    "FriendModel",
    "OutboxEventModel",
    "SessionModel",
    "UserModel",
    "UserSessionModel",
)