"""Argon2-only writes with isolated legacy salted-MD5 verification.
核心规则：新密码一律用Argon2；只在校验时兼容老的加盐MD5，校验通过自动升级成Argon2
"""

# 哈希工具，用来兼容历史遗留的MD5密码
import hashlib
# 安全对比工具，避免普通==带来的时序攻击漏洞
import hmac
# 正则，用来识别老版本32位MD5字符串
import re
# 数据类，用来封装校验结果
from dataclasses import dataclass

# argon2 是现在推荐的安全密码哈希算法，抗暴力破解
from argon2 import PasswordHasher
# 下面几个是argon2校验时可能抛出的异常
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
# SecretStr 保密字符串，打印日志时不会直接泄露明文
from pydantic import SecretStr

# 正则规则：匹配32位大小写十六进制字符串，就是旧版MD5的格式
_LEGACY_MD5 = re.compile(r"^[0-9a-fA-F]{32}$")


# 密码校验结果封装类
@dataclass(frozen=True, slots=True)
class PasswordVerification:
    valid: bool  # 最终校验结果，True=密码正确，False=密码错误
    upgraded_hash: str | None = None  # 需要升级的新哈希；老密码校验成功后这里才有值，交给业务层更新数据库


# 密码管理器，统一封装密码哈希、校验、老密码兼容、防枚举逻辑
class PasswordManager:
    def __init__(
        self,
        *,  # 星号：后面所有参数必须用关键字传参，防止参数顺序传错引发事故
        legacy_salt: SecretStr | None = None,  # 老MD5密码用的盐值，如果没配置就不兼容MD5
        hasher: PasswordHasher | None = None,  # 自定义argon2实例，方便单元测试；不传就自动新建默认配置的
    ) -> None:
        # 保存老盐值，SecretStr类型，不会被日志打印泄露
        self._legacy_salt = legacy_salt
        # 传入了hasher就用传入的，没有就新建一个默认Argon2哈希器
        self._hasher = hasher or PasswordHasher()
        # 提前生成一个假的argon2哈希串，专门给【账号不存在】场景做模拟校验
        # 目的：账号不存在和账号存在但密码错，耗时基本一致，防止黑客通过响应快慢枚举邮箱
        self._dummy_hash = self._hasher.hash("infinitechat-dummy-verification-value")

    # 给明文密码生成Argon2哈希，新注册、密码修改都调用这个，不生成MD5
    def hash_password(self, password: str) -> str:
        # 拦截空密码，不允许入库
        if not password:
            raise ValueError("password must not be empty")
        # 调用argon2生成哈希字符串，里面自带随机盐，不用自己维护盐字段
        return self._hasher.hash(password)

    # 核心校验函数：自动识别库里的哈希是新版Argon2还是老MD5，分开校验
    def verify(self, stored_hash: str, password: str) -> PasswordVerification:
        # 分支1：数据库存的是Argon2哈希（新账号）
        if stored_hash.startswith("$argon2"):
            try:
                # argon2自带校验逻辑，内部自动比对
                self._hasher.verify(stored_hash, password)
            # 捕获三类失败情况：哈希串损坏、格式非法、密码对不上
            except (InvalidHashError, VerificationError, VerifyMismatchError):
                # 校验失败，直接返回结果，不需要升级哈希
                return PasswordVerification(valid=False)

            # 密码校验成功，检查argon2的参数是否过时（比如版本、算力参数升级了）
            if self._hasher.check_needs_rehash(stored_hash):
                # 参数过时，重新生成新的argon2哈希，交给上层更新数据库
                upgraded = self._hasher.hash(password)
            else:
                # 参数没问题，不用更新
                upgraded = None
            return PasswordVerification(valid=True, upgraded_hash=upgraded)

        # 分支2：匹配MD5格式，并且配置了老盐，开启兼容老密码逻辑
        if _LEGACY_MD5.fullmatch(stored_hash) and self._legacy_salt is not None:
            # 取出盐的明文，拼接【盐+用户输入密码】，转字节再算md5
            legacy_value = (self._legacy_salt.get_secret_value() + password).encode()
            # 把【老盐 + 用户输入密码】拼好的数据，算出 MD5 串（候选值）
            candidate = hashlib.md5(legacy_value, usedforsecurity=False).hexdigest()
            # 拿刚才算出来的 MD5，和数据库存的旧 MD5 做安全比对
            valid = hmac.compare_digest(candidate, stored_hash.lower())

            if valid:
                # 老密码校验成功！立刻生成新版Argon2哈希，业务层要更新这条用户记录
                new_hash = self._hasher.hash(password)
            else:
                # 密码错，不用升级
                new_hash = None
            return PasswordVerification(valid=valid, upgraded_hash=new_hash)

        # 分支3：既不是合法argon2，也不是合法可兼容MD5，直接判定密码错误
        return PasswordVerification(valid=False)

    # 假校验：登录时查不到用户的时候调用，用来抹平响应时间差异，防邮箱枚举
    def consume_dummy_verification(self, password: str) -> None:
        """Keep the unknown-user path close to an Argon2 failure path."""
        try:
            # 拿预先准备好的假哈希，执行一次完整的argon2校验流程
            # 一定会抛出VerifyMismatchError，我们只需要消耗时间，不需要结果
            self._hasher.verify(self._dummy_hash, password)
        except VerifyMismatchError:
            # 捕获这个必然出现的异常，啥都不返回，函数悄悄结束
            pass
