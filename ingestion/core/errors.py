# -*- coding: utf-8 -*-
"""
ingestion 模块统一异常体系。

【设计哲学】
错误分三类：
  - FatalError       不可恢复，立即终止整个任务
                     例：Milvus collection 创建失败、Embedding API key 失效
  - RetryableError   可重试，让 @retry 装饰器自动重试
                     例：Milvus gRPC 网络抖动、MongoDB 临时写入失败
  - PartialError     部分失败，调用方决定继续或中止
                     例：500 行中 3 行 embedding 失败，剩余 497 行可继续

【使用场景】
  - db/ 下的 Writer 实现抛出这些异常
  - core/retry.py 的装饰器只 retry RetryableError，其他类型直接抛出
  - base_agent.py 的主循环根据异常类型走不同分支

【约束】
所有自定义异常都继承 IngestionError，方便上层一把 catch
"""

from typing import Any, Dict, List, Optional


# ════════════════════════════════════════════════════════════════
# 根异常
# ════════════════════════════════════════════════════════════════

class IngestionError(Exception):
    """ingestion 模块所有自定义异常的根类。"""

    def __init__(
        self,
        message: str,
        *,
        context: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None,
    ):
        """
        Args:
            message: 错误描述（应该简短、可读）
            context: 错误上下文，例如 {"file": "x.xlsx", "batch_no": 3}
            cause:   原始异常（来自底层库的 Exception）
        """
        super().__init__(message)
        self.message = message
        self.context = context or {}
        self.cause = cause

    def __str__(self) -> str:
        parts = [self.message]
        if self.context:
            ctx_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            parts.append(f"[context: {ctx_str}]")
        if self.cause:
            parts.append(f"[caused by: {type(self.cause).__name__}: {self.cause}]")
        return " ".join(parts)


# ════════════════════════════════════════════════════════════════
# 三大错误分类
# ════════════════════════════════════════════════════════════════

class FatalError(IngestionError):
    """
    不可恢复的错误，必须立即终止当前任务。

    典型场景：
      - 配置错误（API key 错、连接信息错）
      - schema 不兼容（Milvus collection 字段不对）
      - 文件根本不存在或解析失败
      - LLM 服务认证失败

    上层处理：直接 fail 任务，不重试，记录错误信息。
    """
    pass


class RetryableError(IngestionError):
    """
    临时性错误，可以通过重试解决。

    典型场景：
      - 网络抖动（gRPC timeout、连接被重置）
      - 数据库临时不可用（Milvus busy、MongoDB lock）
      - LLM API 限流（rate limit）
      - 单批 embedding 偶发失败

    上层处理：被 @retry 装饰器自动捕获并重试，超过最大次数后转换为 FatalError。
    """
    pass


class PartialError(IngestionError):
    """
    部分失败的错误，包含成功和失败的细节。

    典型场景：
      - 1000 行中 3 行 embedding 返回 None
      - 一批 ES 写入中 5 条失败、995 条成功

    与前两种不同：PartialError 不一定要终止任务，也不一定要重试。
    上层根据 success_count / fail_count 比例决定继续还是中止。
    """

    def __init__(
        self,
        message: str,
        *,
        success_count: int = 0,
        fail_count: int = 0,
        failed_items: Optional[List[Any]] = None,
        context: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None,
    ):
        super().__init__(message, context=context, cause=cause)
        self.success_count = success_count
        self.fail_count = fail_count
        self.failed_items = failed_items or []

    @property
    def total_count(self) -> int:
        return self.success_count + self.fail_count

    @property
    def success_rate(self) -> float:
        if self.total_count == 0:
            return 0.0
        return self.success_count / self.total_count

    def __str__(self) -> str:
        base = super().__str__()
        stats = f"[partial: {self.success_count}/{self.total_count} succeeded, {self.fail_count} failed]"
        return f"{base} {stats}"


# ════════════════════════════════════════════════════════════════
# 具体子类（按场景细分）
# ════════════════════════════════════════════════════════════════

# ── 文件 / 解析类 ──────────────────────────────────────────────

class FileNotFoundError_(FatalError):
    """文件不存在或无法读取。注意名字加下划线避免和内置 FileNotFoundError 冲突。"""
    pass


class ParseError(FatalError):
    """文件解析失败（Excel 格式错误、PDF 损坏等）。"""
    pass


class InvalidContentError(FatalError):
    """文件内容格式不符合预期（必填列缺失、全空行等）。"""
    pass


# ── Embedding 类 ──────────────────────────────────────────────

class EmbeddingError(IngestionError):
    """Embedding 错误的基类。具体子类决定是否可重试。"""
    pass


class EmbeddingAPIError(RetryableError):
    """Embedding API 调用失败（网络、限流、超时）。可重试。"""
    pass


class EmbeddingPartialFailure(PartialError):
    """部分 embedding 失败（如 1000 条中 3 条返回 None）。"""
    pass


# ── 数据库写入类 ──────────────────────────────────────────────

class WriterError(IngestionError):
    """数据库写入错误的基类。"""
    pass


class WriterConnectionError(RetryableError):
    """数据库连接失败、断开。可重试。"""
    pass


class WriterTransientError(RetryableError):
    """数据库临时错误（busy、lock conflict）。可重试。"""
    pass


class WriterFatalError(FatalError):
    """数据库致命错误（schema 不兼容、collection 不存在等）。"""
    pass


class WriterPayloadTooLarge(WriterFatalError):
    """
    单批数据超过数据库的限制（如 Milvus gRPC 64MB）。

    这是 WriterFatalError（不可重试），因为重试也没用——必须分批。
    上层（BaseWriter）应该在写入前就分批，避免触发此错误。
    """
    pass


# ── 任务级 ──────────────────────────────────────────────────

class TaskCancelledError(IngestionError):
    """任务被用户主动取消。不是错误，但需要立即终止流程。"""
    pass


class TaskTimeoutError(FatalError):
    """任务超时。"""
    pass


# ════════════════════════════════════════════════════════════════
# 公共导出
# ════════════════════════════════════════════════════════════════

__all__ = [
    # 根
    "IngestionError",
    # 三大类
    "FatalError",
    "RetryableError",
    "PartialError",
    # 文件/解析
    "FileNotFoundError_",
    "ParseError",
    "InvalidContentError",
    # Embedding
    "EmbeddingError",
    "EmbeddingAPIError",
    "EmbeddingPartialFailure",
    # 数据库写入
    "WriterError",
    "WriterConnectionError",
    "WriterPayloadTooLarge",
    "WriterTransientError",
    "WriterFatalError",
    # 任务级
    "TaskCancelledError",
    "TaskTimeoutError",
]
