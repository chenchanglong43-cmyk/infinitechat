"""契约演示.

内部写 snake_case, 线上是 camelCase; 两种名字都能解析, 多余字段直接拒绝;
错误响应的文案按错误码自动带出.
前置条件: 纯函数, 无需密钥/中间件.
"""

import structlog  # 结构化日志

from infinitechat.contracts import BaseResponse, ErrorCode, PageRequest, PageResponse

from infinitechat.foundation.telemetry import configure_logging
from pydantic import ValidationError

logger = structlog.get_logger(__name__)

# 同一份分页参数用两种名字解析
def main() -> None:
    """同一份分页参数用两种名字解析, 再看多余字段, 越界值和两种响应壳."""
    # 1. 线上名和内部名都能解析, 解析出来是同一个对象
    from_wire = PageRequest.model_validate({"pageNum": 2, "pageSize": 50})
    from_python = PageRequest(page_num=2, page_size=50)
    # 打一条 info 日志
    logger.info("两种名字解析", same=from_wire == from_python, wire=from_wire.to_wire())
    # 2. 多出来的字段, 超出上限的值都被拒
    for case, payload in (
        ("多余字段", {"pageNum": 1, "pageSize": 20, "sortBy": "id"}),
        ("超出上限", {"pageNum": 1, "pageSize": 1000}),
    ):
        try:  # 下面这段可能出错, 出错交给 except 处理
            PageRequest.model_validate(payload)  # 按模型校验数据
        except ValidationError as error:
            # 打一条 warning 日志
            logger.warning("参数被拒", case=case, error_type=error.errors()[0]["type"])
    # 3. 成功响应: 外壳和里面的分页字段, 线上一律是驼峰
    page = PageResponse[int].from_items(list(range(1, 46)), page_num=2, page_size=20)
    body = BaseResponse[PageResponse[int]].success(page).to_wire()
    logger.info(  # 打一条 info 日志
        "成功响应",
        keys=sorted(body),
        data_keys=sorted(body["data"]),
        items=len(body["data"]["list"]),
    )
    # 4. 错误响应: 文案按错误码自动带出, data 是空的; 日志里不拿 code 当键名, 它会被打码
    failure = BaseResponse[None].error(ErrorCode.USER_NOT_EXISTS).to_wire()
    logger.warning(  # 打一条 warning 日志
        "错误响应",
        error_code=failure["code"],
        message=failure["message"],
        data=failure["data"],
    )

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    main()  # 同一份分页参数用两种名字解析, 再看多余字段