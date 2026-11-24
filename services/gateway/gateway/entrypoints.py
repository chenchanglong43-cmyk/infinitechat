"""Independent Gateway API process entrypoint."""
# 网关独立进程启动入口文件

import uvicorn  # 导入ASGI服务器，用来运行FastAPI应用

from gateway.config import GatewaySettings  # 导入网关配置类

# 启动网关服务
def api_main() -> None:
    settings = GatewaySettings()  # type: ignore[call-arg]  # 从环境变量加载网关配置
    uvicorn.run(  # 启动uvicorn ASGI服务
        "gateway.app:create_default_app",  # 指定应用工厂位置：模块:函数
        factory=True,  # 开启工厂模式，uvicorn会调用函数获取app实例
        host=settings.service_host,  # 监听IP，读取配置
        port=settings.service_port,  # 监听端口，读取配置
        workers=1,  # 工作进程数量，网关这里单进程
    )