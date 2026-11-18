"""第一个服务的体检.

请求 ID 回带或生成, 带空格的被拒, /metrics 按路径和结果计数.
前置条件: 需先启动 user 服务, 无需中间件.
"""

import urllib.error
import urllib.request

import structlog  # 结构化日志

from infinitechat.foundation.telemetry import configure_logging
from user_service.config import UserServiceSettings

logger = structlog.get_logger(__name__)

# 发一个 GET
def call(url: str, request_id: str | None = None) -> tuple[int, str | None, str]:
    """发一个 GET, 返回状态码, 响应头里的请求 ID 和响应体."""
    # 地址只来自本服务配置, 不拼外部输入
    request = urllib.request.Request(url)  # noqa: S310
    if request_id is not None:
        request.add_header("X-Request-Id", request_id)  # 加一个请求头
    try:  # 下面这段可能出错, 出错交给 except 处理
        # 打开网址 response, 用完自动收尾
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            return response.status, response.headers.get("X-Request-Id"), response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("X-Request-Id"), error.read().decode()

# 三次探活：带 ID、不带 ID、带空格的 ID，看请求 ID 怎么处理
def main() -> None:
    """三次探活: 带 ID, 不带 ID, 带空格的 ID; 最后读 /metrics 对账."""
    # 基础: 字符串 "http://127.0.0.1:{创建用户..."
    base = f"http://127.0.0.1:{UserServiceSettings().service_port}"
    # 1. 带了请求 ID: 原样回带
    # 拆成 状态, echoed, : 发一个 GET, 返回状态码, 响应头里的请求 ID 和响应体
    status, echoed, _ = call(f"{base}/health/live", "req-ch01-0001")
    logger.info("带 ID 探活", status=status, echoed=echoed)  # 打一条 info 日志
    # 2. 没带: 服务自己生成一个 UUID
    # 拆成 状态, echoed, : 发一个 GET, 返回状态码, 响应头里的请求 ID 和响应体
    status, echoed, _ = call(f"{base}/health/live")
    # 打一条 info 日志
    logger.info("不带 ID 探活", status=status, generated_length=len(echoed or ""))
    # 3. 带空格: 在中间件入口就被拒, 响应里也没有回带
    # 拆成 状态, echoed, : 发一个 GET, 返回状态码, 响应头里的请求 ID 和响应体
    status, echoed, _ = call(f"{base}/health/live", "req ch01")
    logger.warning("带空格的 ID", status=status, echoed=echoed)  # 打一条 warning 日志
    # 4. 读指标: 同一个路径按 success, error 分开计数
    # 拆成,, 消息体: 发一个 GET, 返回状态码, 响应头里的请求 ID 和响应体
    _, _, body = call(f"{base}/metrics")
    for line in body.splitlines():
        if line.startswith("user_service_requests_total{") and 'operation="/health/live"' in line:
            labels, value = line.rsplit(" ", 1)  # 拆成 标签, 值: 调用 rsplit
            outcome = labels.split('outcome="', 1)[1].split('"', 1)[0]  # 结果: 把它按 """ 拆开[0]
            # 打一条 info 日志
            logger.info("请求计数", path="/health/live", outcome=outcome, count=float(value))

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    main()  # 三次探活: 带 ID, 不带 ID, 带空格的 ID; 最后读 /metrics 对账