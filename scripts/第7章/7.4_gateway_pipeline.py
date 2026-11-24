"""网关整条链路演示.

真实的网关应用, 只把上游换成进程内的假应用, 把服务发现换成固定地址: 内部接口不转发;
没登录挡在网关; 登录后转发给上游, 客户端伪造的用户头被覆盖, 逐跳头被去掉, 请求 ID
带到上游又带回来; 免鉴权的路径不要令牌.
前置条件: 需 Redis, 变量写在仓库根目录的 .env 里.
"""

import asyncio  # 异步编程
import secrets  # 安全随机数
from datetime import timedelta

import httpx  # HTTP 客户端
import structlog  # 结构化日志
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from infinitechat.foundation.settings import read_env  # 先读进程环境变量，再读 .env
from gateway.app import GatewayContainer, create_app
from gateway.auth import GatewayAuthenticator
from gateway.config import GatewaySettings
from gateway.proxy import ReverseProxy
from infinitechat.foundation.discovery import ServiceInstance

from infinitechat.foundation.security import JwtCodec, TokenType

from infinitechat.foundation.telemetry import configure_logging
from redis.asyncio import Redis

from user_service.integrations.redis import UserRedis

logger = structlog.get_logger(__name__)

USER_ID = 9_000_008_001

# 按 user 服务签发令牌的写法签一个, 带随机 jti
def issue(codec: JwtCodec, token_type: TokenType, ttl_seconds: int) -> str:
    """按 user 服务签发令牌的写法签一个, 带随机 jti."""
    return codec.encode(
        subject=str(USER_ID),
        expires_in=timedelta(seconds=ttl_seconds),
        token_type=token_type,
        additional_claims={"jti": secrets.token_hex(16)},
    )

# 模拟一次登录: 签一组令牌
async def login(codec: JwtCodec, store: UserRedis) -> tuple[str, str]:
    """模拟一次登录: 签一组令牌, 按 user 服务的写法存成当前令牌."""
    access = issue(codec, TokenType.ACCESS, 1800)
    refresh = issue(codec, TokenType.REFRESH, 604_800)
    await store.store_tokens(
        USER_ID,
        access_token=access,
        refresh_token=refresh,
        access_ttl_seconds=1800,
        refresh_ttl_seconds=604_800,
    )
    return access, refresh

upstream = FastAPI()

# 装饰器 api_route: 给下面的定义加上额外行为
@upstream.api_route("/{path:path}", methods=["GET", "POST"])
# 假上游: 把收到的路径和几个关键请求头原样返回
async def echo(path: str, request: Request) -> JSONResponse:
    """假上游: 把收到的路径和几个关键请求头原样返回."""
    seen = {
        "path": f"/{path}",  # "path": 字符串 "/{路径}"
        "user": request.headers.get("x-authenticated-user-id"),  # "user": 取 "x-authenticated-..."
        "proxy_auth": request.headers.get("proxy-authorization"),  # "proxy_auth"
        "request_id": request.headers.get("x-request-id"),  # "request_id": 取 "x-request-id"
    }
    return JSONResponse(seen, headers={"X-Upstream": "fake-user-service"})

# 替身上游解析: 不查 Nacos
class FixedResolver:
    """替身上游解析: 不查 Nacos, 记下每次解析的服务名和路由键, 一律指向假上游."""

    def __init__(self) -> None:  # 构造方法: 创建对象时把依赖存起来
        """还没有解析过任何服务."""
        self.calls: list[tuple[str, str]] = []  # calls(元组列表): 空列表

    # 记下这次解析
    async def resolve(self, service_name: str, *, routing_key: str) -> str:
        """记下这次解析, 返回假上游的地址."""
        self.calls.append((service_name, routing_key))  # 把 (服务名, 路由键) 追加到calls
        return "http://upstream"

# 占位注册表: 容器要求有这一项
class UnusedRegistry:
    """占位注册表: 容器要求有这一项, 这条演示链路里用不到."""

async def main() -> None:
    """装一个真实的网关应用, 从外面敲四种请求."""
    settings = GatewaySettings(
        redis_url=read_env("REDIS_URL"),
        jwt_secret=read_env("JWT_SECRET"),
        nacos_server_addr="127.0.0.1:8848",
        nacos_cache_dir=".runtime/nacos/cache",
        nacos_log_dir=".runtime/nacos/log",
    )
    redis = Redis.from_url(
        settings.redis_url,
        db=settings.redis_db,
        decode_responses=True,
        protocol=2,
    )
    codec = JwtCodec(settings.jwt_secret, key_encoding=settings.jwt_key_encoding)
    upstream_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=upstream))
    resolver = FixedResolver()  # 创建替身上游解析: 不查 Nacos
    app = create_app(
        GatewayContainer(
            settings=settings,
            redis=redis,
            # registry(注册中心): 创建占位注册表: 容器要求有这一项, 这条演示链路里用不到
            registry=UnusedRegistry(),  # type: ignore[arg-type]
            registered_instance=ServiceInstance(
                service_name="GateWay",
                ip="127.0.0.1",
                port=10_010,
            ),
            http_client=upstream_client,
            authenticator=GatewayAuthenticator(codec, redis),
            # resolver: resolver
            resolver=resolver,  # type: ignore[arg-type]
            proxy=ReverseProxy(upstream_client),
        )
    )
    params = {"userId": USER_ID}
    try:  # 下面这段可能出错, 出错交给 except 处理
        async with (  # 进入应用的生命周期(启动并在结束时关闭)
            app.router.lifespan_context(app),  # 打开 进入应用的生命周期(启动并在结束时关闭)
            httpx.AsyncClient(  # 打开 建异步 HTTP 客户端
                # transport: 直接调用应用, 不走网络
                transport=httpx.ASGITransport(app=app),
                base_url="http://gateway",
            ) as client,
        ):
            # 1. 内部接口: 网关直接 404, 上游一次都没被解析
            response = await client.get("/api/internal/user/status", params=params)
            # 打一条 warning 日志
            logger.warning("内部接口", status=response.status_code, resolved=len(resolver.calls))
            # 2. 没登录: 挡在网关, 401 加业务码
            response = await client.get("/api/user/get/sessions", params=params)
            code = response.json()["code"]  # 码: 解析响应 JSON["code"]
            logger.warning(  # 打一条 warning 日志
                "没登录",
                status=response.status_code,
                error_code=code,
                resolved=len(resolver.calls),
            )
            # 3. 登录后转发: 伪造的用户头被覆盖, 代理凭证头被去掉, 请求 ID 一路带着
            # 拆成 访问, : 等模拟一次登录: 签一组令牌, 按 user 服务的写法存成当前令牌
            access, _ = await login(codec, UserRedis(redis))
            headers = {
                "Access-Token": access,  # "Access-Token": 访问
                "X-Authenticated-User-Id": "1",  # "X-Authenticated-...": "1"
                "Proxy-Authorization": "Basic ZGVtbzpkZW1v",  # 代理认证头：应被打码
                "X-Request-Id": "req-gateway-demo",  # "X-Request-Id": "req-gateway-demo"
            }
            response = await client.get("/api/user/get/sessions", params=params, headers=headers)
            seen = response.json()
            logger.info(  # 打一条 info 日志
                "转发成功",
                status=response.status_code,
                path=seen["path"],
                user_header_is_real=seen["user"] == str(USER_ID),
                proxy_auth=seen["proxy_auth"],
                request_id=seen["request_id"],
                echoed=response.headers.get("X-Request-Id"),
                upstream=response.headers.get("X-Upstream"),
            )
            service, routing_key = resolver.calls[-1]  # 拆成 服务, 路由键: resolver的calls[-1]
            # 打一条 info 日志
            logger.info("上游解析", service=service, keyed_by_user=routing_key == str(USER_ID))
            # 4. 免鉴权路径: 不带令牌也转发, 上游收不到用户头
            response = await client.post("/api/user/login/password", json={})
            # 打一条 info 日志
            logger.info("免鉴权", status=response.status_code, user_header=response.json()["user"])
    finally:  # 不管成功还是出错, 最后都执行这里
        await upstream_client.aclose()
        await redis.aclose()

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    asyncio.run(main())  # 启动事件循环跑这个协程