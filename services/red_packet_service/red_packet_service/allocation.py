"""Red packet amount allocation in fen."""
# 红包金额拆分：这里所有金额单位都是“分”，全程整数运算，不碰浮点数

import random  # 随机数：拼手气红包要用

# 中文标点是给前端看的提示文案的一部分，不是打错了
# ruff: noqa: RUF001  ← 给 ruff 的指令：本文件允许全角标点，别报警

# 校验红包总金额与个数
def _validate(total_amount: int, total_count: int) -> None:
    # 金额和份数都必须为正，“0 元红包”这种直接拒掉
    if total_amount <= 0 or total_count <= 0:
        raise ValueError("红包金额和数量必须大于0")
    # 每份最少 1 分，总额比份数还少就根本分不出来
    if total_amount < total_count:
        raise ValueError(f"红包总金额不足，至少需要{total_count}分")


# 普通红包：平均分，除不尽的余数补给前面几个人
def allocate_normal(total_amount: int, total_count: int) -> list[int]:
    _validate(total_amount, total_count)  # 先校验，不合法直接抛错
    # divmod 一次同时拿到商和余数：比如 100 分 3 份 → 商 33、余 1
    average, remainder = divmod(total_amount, total_count)
    # index < remainder 为 True 时补 1 分；bool 当 int 用，True 就是 1
    # 效果：前 remainder 个人拿 average+1，后面的人拿 average
    return [average + int(index < remainder) for index in range(total_count)]


# 拼手气红包：把金额随机切成若干份，再打乱顺序
def allocate_random(
        total_amount: int,
        total_count: int,
        *,  # 星号后面的参数只能按名字传，防止 rng 被误当成第三个位置参数
        rng: random.Random | None = None,  # 随机源：测试时可以传固定种子，保证结果可复现
) -> list[int]:
    _validate(total_amount, total_count)  # 先校验
    # 只有一份，不用切，全给它
    if total_count == 1:
        return [total_amount]
    # 先给每份预留 1 分，剩下的钱才是可以随机切的“蛋糕”
    remaining = total_amount - total_count
    # 剩下的还不够每份 1 分（比如 5 分 3 份），随机没意义，退回平均分
    if remaining < total_count:
        return allocate_normal(total_amount, total_count)
    # 用传进来的随机源；没传就用系统级随机数（更安全，不受随机种子影响）
    generator = rng or random.SystemRandom()
    # 在 1 到 remaining-1 之间随机取 total_count-1 个切分点，排序后首尾补上 0 和 remaining
    # 这样就把剩下的钱切成了 total_count 段
    cuts = [0, *sorted(generator.sample(range(1, remaining), total_count - 1)), remaining]
    # 每段长度 + 1（加回刚预留的那 1 分），所有份加起来正好等于 total_amount
    amounts = [cuts[index + 1] - cuts[index] + 1 for index in range(total_count)]
    generator.shuffle(amounts)  # 打乱顺序：谁抢到大份全看运气
    return amounts

