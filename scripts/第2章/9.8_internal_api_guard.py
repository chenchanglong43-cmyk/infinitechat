"""
内部接口门锁测试脚本
规则：不带服务令牌、或者令牌对应的服务不在白名单，直接返回401
只有合法白名单里的服务令牌，才能查到用户状态
封禁用户、不存在用户，各自返回不同提示
前提：本地MySQL跑起来，user服务已经启动
"""

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

import structlog
from infinitechat.contracts.enums import UserState

# 工具类：JWT编解码、服务身份、服务令牌生成
from infinitechat.foundation.security import JwtCodec, ServiceIdentity, ServiceTokenCodec
from infinitechat.foundation.telemetry import configure_logging
from user_service.config import UserServiceSettings
from user_service.integrations.mysql import create_mysql_engine, create_session_factory
from user_service.storage.models import UserModel
from user_service.storage.repositories import UserRepository

logger = structlog.get_logger(__name__)

# 准备三个测试用户id
NORMAL_USER_ID = 9_000_000_101  # 正常用户
BANNED_USER_ID = 9_000_000_102  # 被封禁用户
MISSING_USER_ID = 9_000_000_199  # 根本不存在的用户

# 要初始化进数据库的用户
SEED_USERS = (
    (NORMAL_USER_ID, UserState.NORMAL),
    (BANNED_USER_ID, UserState.BANNED),
)
# 演示账号不让登录，密码写个假值占位
DISABLED_LOGIN = "!demo!"

async def seed_users(dsn: str) -> None:
    """往库里插测试用户，如果已经有了就跳过，避免重复造数据"""
    engine = create_mysql_engine(dsn)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session, session.begin():
            repo = UserRepository(session)
            for user_id, state in SEED_USERS:
                # 查一下有没有这个用户
                if await repo.get_by_id(user_id) is None:
                    # 没有就新增
                    repo.add(
                        UserModel(
                            user_id=user_id,
                            email=f"u{user_id}@example.com",
                            password=DISABLED_LOGIN,
                            nickname=f"演示用户{user_id % 1000}",
                            state=state,
                        )
                    )
    finally:
        # 不管成功失败，最后关掉数据库连接池
        await engine.dispose()

def call(base: str, user_id: int, token: str | None) -> tuple[int, dict[str, Any]]:
    """发请求去查用户状态，返回http状态码和后端返回的json内容"""
    req = urllib.request.Request(f"{base}/api/internal/user/status?userId={user_id}")
    if token is not None:
        req.add_header("X-Service-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        # 后端返回4xx/5xx的时候会进到这里，照样把返回内容读出来
        return err.code, json.loads(err.read())

def main() -> None:
    """主流程：先准备测试数据，再分3种情况敲门测试鉴权"""
    # 读取和user服务一模一样的环境配置，mysql地址、jwt密钥都从这里拿
    settings = UserServiceSettings()
    # 异步跑函数，初始化测试用户
    asyncio.run(seed_users(settings.mysql_dsn))

    # 服务地址
    base_url = f"http://127.0.0.1:{settings.service_port}"
    # 用同一个密钥，生成服务令牌
    token_tool = ServiceTokenCodec(JwtCodec(settings.service_jwt_secret))

    # 场景1：啥令牌都不带，直接敲门
    status, body = call(base_url, NORMAL_USER_ID, None)
    logger.warning("测试：不带令牌", status=status, code=body["code"], msg=body["message"])

    # 场景2：令牌签名没问题，但是这个服务不在后端白名单里（GatewayService）
    bad_token = token_tool.issue(ServiceIdentity("GatewayService"))
    status, body = call(base_url, NORMAL_USER_ID, bad_token)
    logger.warning("测试：服务不在白名单", status=status, code=body["code"])

    # 场景3：白名单内合法服务 RealTimeService，分别查正常、封禁、不存在用户
    good_token = token_tool.issue(ServiceIdentity("RealTimeService"))
    for uid in (NORMAL_USER_ID, BANNED_USER_ID, MISSING_USER_ID):
        status, body = call(base_url, uid, good_token)
        data = body["data"]
        logger.info(
            "测试：拿到用户状态",
            http_status=status,
            user_id=data["userId"],
            state=data["state"],
            state_name=data["stateName"],
        )

if __name__ == "__main__":
    # 打开日志，不输出json格式，方便看控制台
    configure_logging(json_output=False)
    main()