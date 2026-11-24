"""Streaming-safe HTTPX reverse proxy."""
# 支持流式响应的反向代理，基于httpx实现，适配FastAPI网关

from collections.abc import AsyncIterator  # 导入异步迭代器类型注解

import httpx  # 导入异步HTTP客户端库
from fastapi import Request  # 导入FastAPI请求对象
from starlette.background import BackgroundTask  # 导入后台任务，用于连接关闭
from starlette.responses import StreamingResponse  # 导入流式响应返回体

# 逐跳头集合，代理转发时必须移除，不向上游/下游传递
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "content-length",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
# 网关全权控制的请求头，客户端携带的这些头直接丢弃，网关自行填充
_GATEWAY_OWNED_HEADERS = frozenset({"host", "x-authenticated-user-id"})

# 反向代理类
class ReverseProxy:
    # 初始化方法
    def __init__(self, client: httpx.AsyncClient) -> None:  # 注入httpx异步客户端实例
        self._client = client  # 保存httpx客户端到实例变量

    # 转发请求到上游服务，返回流式响应
    async def forward(
        self,
        request: Request,
        *,  # 标记后面参数必须使用关键字传参
        upstream_base_url: str,
        authenticated_user_id: int | None,
    ) -> StreamingResponse:
        # 过滤原始请求头，剔除逐跳头和网关自有头
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in _HOP_BY_HOP and key.lower() not in _GATEWAY_OWNED_HEADERS
        }
        # 如果存在已认证用户ID，追加网关鉴权头传给上游服务
        if authenticated_user_id is not None:
            headers["X-Authenticated-User-Id"] = str(authenticated_user_id)
        # 构造发向上游的HTTP请求
        upstream_request = self._client.build_request(
            request.method,  # 复用客户端请求方法 GET/POST/PUT...
            f"{upstream_base_url}{request.url.path}",  # 拼接上游地址 + 请求路径
            params=request.query_params,  # 透传URL查询参数
            headers=headers,  # 使用过滤后的请求头
            content=await request.body(),  # 读取请求体内容
        )
        # 发送请求，开启stream流式模式，不一次性读完响应
        upstream = await self._client.send(upstream_request, stream=True)
        # 过滤上游返回的响应头，移除逐跳头
        response_headers = {
            key: value
            for key, value in upstream.headers.items()
            if key.lower() not in _HOP_BY_HOP
        }

        # 定义异步生成器，持续读取上游返回的数据块
        async def body() -> AsyncIterator[bytes]:
            # 循环迭代上游原始数据流块
            async for chunk in upstream.aiter_raw():
                yield chunk  # 将块吐出给下游客户端

        # 返回流式响应，注册后台任务，响应结束后关闭上游连接
        return StreamingResponse(
            body(),
            status_code=upstream.status_code,
            headers=response_headers,
            background=BackgroundTask(upstream.aclose),
        )