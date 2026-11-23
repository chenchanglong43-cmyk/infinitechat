"""User scheduler process loop."""
# 用户调度器进程循环入口

import asyncio  # 导入异步IO库

from user_service.schedulers.friend_requests import FriendRequestExpirationScheduler
# 导入好友申请过期调度器类

# 运行用户调度器主循环
async def run_user_scheduler(
    scheduler: FriendRequestExpirationScheduler,  # 调度器实例
    *,  # 标记后面参数必须关键字传参
    interval_seconds: float = 1.0,  # 每轮调度间隔，默认1秒
) -> None:
    while True:  # 无限循环，持续执行调度
        await scheduler.run_once()  # 执行一轮过期调度逻辑
        await asyncio.sleep(interval_seconds)  # 间隔休眠，防止高频空跑