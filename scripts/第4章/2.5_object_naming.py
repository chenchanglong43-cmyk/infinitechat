"""对象命名演示.

上传地址里的对象名一律由服务端生成: 只认常见图片后缀, 文件名里夹带路径直接拒绝, 每次都是新的随机名.
前置条件: 无, 纯函数, 不连 MinIO.
"""

import structlog  # 结构化日志

from infinitechat.foundation.telemetry import configure_logging

from user_service.integrations.minio import avatar_object_name

logger = structlog.get_logger(__name__)

# 拿几个文件名分别生成对象名
def main() -> None:
    """拿几个文件名分别生成对象名."""
    # 1. 正常的图片名: 换成随机名, 只留后缀; 同一个文件名两次生成的对象名也不一样
    first = avatar_object_name("我的头像.png")
    second = avatar_object_name("我的头像.png")
    logger.info("正常图片", object_name=first, same_twice=first == second)  # 打一条 info 日志
    # 2. 后缀不在白名单里、文件名夹带路径、干脆没有后缀, 都直接拒绝
    # cases: (("可执行文件", "avatar.exe"), ("夹带路径", "../../etc/a.png"), ( "可执行文件"
    cases = (("可执行文件", "avatar.exe"), ("夹带路径", "../../etc/a.png"), ("没有后缀", "avatar"))
    for case, file_name in cases:
        try:  # 下面这段可能出错, 出错交给 except 处理
            avatar_object_name(file_name)
        except ValueError as error:
            logger.warning("被拒", case=case, reason=str(error))  # 打一条 warning 日志

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    main()  # 拿几个文件名分别生成对象名