# -*- coding: utf-8 -*-
"""
ingestion 模块统一重试装饰器。

【设计哲学】
1. 只重试 RetryableError 及其子类
   - FatalError 立即抛出（重试也没用）
   - PartialError 不抛异常，由调用方处理
   - 其他未分类异常默认视为 Fatal（保守策略）

2. 指数退避（exponential backoff）
   - 第 N 次重试等待 base_delay × (2^N) 秒，最大不超过 max_delay
   - 加入随机抖动（jitter）防止"重试风暴"

3. 重试耗尽后转换异常
   - 所有重试都失败 → 抛 FatalError，cause 是最后一次的 RetryableError
   - 这样上层不需要再判断"是不是已经重试过了"

4. 详细日志
   - 每次重试都打 WARNING（带尝试次数、延迟、原因）
   - 最后失败打 ERROR

【使用示例】

    from core.retry import retry
    from core.errors import RetryableError, WriterConnectionError

    @retry(max_attempts=3, base_delay=1.0)
    def write_to_milvus(records):
        try:
            collection.insert(records)
        except SomeNetworkError as e:
            raise WriterConnectionError("Milvus 网络抖动", cause=e)

【约束】
- 只装饰 raise RetryableError 的函数
- 装饰可以叠加：先 @retry 再 @some_other_decorator
- 不修改原函数签名
"""

import asyncio
import functools
import logging
import random
import time
from typing import Callable, Optional, Tuple, Type

from core.errors import (
    FatalError,
    RetryableError,
)

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 默认配置
# ════════════════════════════════════════════════════════════════

DEFAULT_MAX_ATTEMPTS = 3      # 总尝试次数（含首次），即最多重试 max_attempts - 1 次
DEFAULT_BASE_DELAY = 1.0      # 第一次重试等待秒数
DEFAULT_MAX_DELAY = 30.0      # 单次延迟上限
DEFAULT_JITTER = 0.3          # 抖动比例（0.3 = ±30%）


# ════════════════════════════════════════════════════════════════
# 内部工具：计算延迟时间
# ════════════════════════════════════════════════════════════════

def _compute_delay(
    attempt: int,
    base_delay: float,
    max_delay: float,
    jitter: float,
) -> float:
    """
    计算第 attempt 次重试前的等待时间（秒）。

    指数退避：delay = base_delay × 2^(attempt-1)
    加抖动：delay × (1 ± jitter * random)
    上限：不超过 max_delay

    Args:
        attempt: 第几次重试（从 1 开始；第 1 次重试 attempt=1）
        base_delay: 基础延迟
        max_delay: 延迟上限
        jitter: 抖动比例

    Returns:
        延迟秒数
    """
    raw_delay = base_delay * (2 ** (attempt - 1))
    raw_delay = min(raw_delay, max_delay)

    if jitter > 0:
        jitter_amount = raw_delay * jitter * (2 * random.random() - 1)
        raw_delay = max(0.0, raw_delay + jitter_amount)

    return raw_delay


# ════════════════════════════════════════════════════════════════
# 主装饰器：支持同步和异步
# ════════════════════════════════════════════════════════════════

def retry(
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    jitter: float = DEFAULT_JITTER,
    retry_on: Tuple[Type[Exception], ...] = (RetryableError,),
) -> Callable:
    """
    重试装饰器。自动识别同步/异步函数。

    Args:
        max_attempts: 最大尝试次数（含首次），>= 1
        base_delay:   首次重试前等待秒数
        max_delay:    单次延迟的上限
        jitter:       抖动比例（0 = 无抖动，0.3 = ±30%）
        retry_on:     哪些异常类型触发重试（默认 RetryableError 及其子类）

    行为：
        - 函数抛 retry_on 中的异常 → 等待 + 重试
        - 函数抛 FatalError → 立即重抛，不重试
        - 函数抛其他 Exception → 立即重抛（保守策略）
        - 重试耗尽 → 抛 FatalError，cause 是最后一次的异常

    Returns:
        装饰后的函数
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts 必须 >= 1，得到 {max_attempts}")

    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):
            return _make_async_wrapper(
                func, max_attempts, base_delay, max_delay, jitter, retry_on
            )
        else:
            return _make_sync_wrapper(
                func, max_attempts, base_delay, max_delay, jitter, retry_on
            )

    return decorator


def _make_sync_wrapper(
    func: Callable,
    max_attempts: int,
    base_delay: float,
    max_delay: float,
    jitter: float,
    retry_on: Tuple[Type[Exception], ...],
) -> Callable:
    """同步函数的重试包装。"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        last_exception: Optional[Exception] = None

        for attempt in range(1, max_attempts + 1):
            try:
                return func(*args, **kwargs)

            except FatalError:
                # FatalError 永远不重试，立即抛出
                raise

            except retry_on as e:
                last_exception = e
                if attempt < max_attempts:
                    delay = _compute_delay(attempt, base_delay, max_delay, jitter)
                    logger.warning(
                        f"[retry] {func.__name__} 第 {attempt}/{max_attempts} 次失败，"
                        f"{delay:.2f}s 后重试: {type(e).__name__}: {e}"
                    )
                    time.sleep(delay)
                else:
                    break

        logger.error(
            f"[retry] {func.__name__} 重试 {max_attempts} 次后仍失败，"
            f"放弃: {type(last_exception).__name__}: {last_exception}"
        )
        raise FatalError(
            f"{func.__name__} 重试 {max_attempts} 次后仍失败",
            context={"max_attempts": max_attempts, "func": func.__name__},
            cause=last_exception,
        )

    return wrapper


def _make_async_wrapper(
    func: Callable,
    max_attempts: int,
    base_delay: float,
    max_delay: float,
    jitter: float,
    retry_on: Tuple[Type[Exception], ...],
) -> Callable:
    """异步函数的重试包装。"""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        last_exception: Optional[Exception] = None

        for attempt in range(1, max_attempts + 1):
            try:
                return await func(*args, **kwargs)

            except FatalError:
                raise

            except retry_on as e:
                last_exception = e
                if attempt < max_attempts:
                    delay = _compute_delay(attempt, base_delay, max_delay, jitter)
                    logger.warning(
                        f"[retry] {func.__name__} 第 {attempt}/{max_attempts} 次失败，"
                        f"{delay:.2f}s 后重试: {type(e).__name__}: {e}"
                    )
                    await asyncio.sleep(delay)
                else:
                    break

        logger.error(
            f"[retry] {func.__name__} 重试 {max_attempts} 次后仍失败，"
            f"放弃: {type(last_exception).__name__}: {last_exception}"
        )
        raise FatalError(
            f"{func.__name__} 重试 {max_attempts} 次后仍失败",
            context={"max_attempts": max_attempts, "func": func.__name__},
            cause=last_exception,
        )

    return wrapper


# ════════════════════════════════════════════════════════════════
# 公共导出
# ════════════════════════════════════════════════════════════════

__all__ = [
    "retry",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_BASE_DELAY",
    "DEFAULT_MAX_DELAY",
    "DEFAULT_JITTER",
]
