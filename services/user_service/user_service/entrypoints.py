"""Independent UserService API, Kafka worker and scheduler entrypoints."""

import uvicorn  # ASGI 服务器

from user_service.config import UserServiceSettings

def _settings() -> UserServiceSettings:
    return UserServiceSettings()  # type: ignore[call-arg]  # environment process config

# 启动API进程
def api_main() -> None:
    settings = _settings()
    uvicorn.run(  # 启动 uvicorn 服务
        "user_service.app:create_default_app",
        factory=True,
        host=settings.service_host,
        port=settings.service_port,
        workers=1,
    )