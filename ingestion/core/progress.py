# -*- coding: utf-8 -*-
"""
ingestion 模块统一进度回调协议。

【设计哲学】
1. 接口先行
   定义 ProgressReporter 抽象类，所有 agent 通过这个接口报告进度，
   不再各自定义 _progress() 函数。

2. 多种实现
   - CallbackProgressReporter: 包装原有的 progress_callback(step, name, ...)
     用于和 admin_knowledge.py 的 _make_progress_callback 兼容
   - NoOpProgressReporter:     什么都不做。CLI / 单元测试用
   - ConsoleProgressReporter:  打印到控制台，本地调试用

3. 三种回调粒度
   - report_step(step, name, detail)              主步骤切换（1/5 → 2/5）
   - report_substep(current, total, detail)       子进度（处理 500/7495 行）
   - report_status(status, message)               状态变化（pending → running → done/failed）

【使用示例】

    from core.progress import ProgressReporter, CallbackProgressReporter, NoOpProgressReporter

    # Agent 接受 reporter 参数
    def ingest_excel(file_path: str, reporter: ProgressReporter = None):
        if reporter is None:
            reporter = NoOpProgressReporter()

        reporter.report_step(1, "读取Excel", file_path)
        df = pd.read_excel(file_path)

        reporter.report_step(2, "构造文本")
        for i, row in enumerate(df.iterrows(), 1):
            ...
            if i % 100 == 0:
                reporter.report_substep(i, len(df), f"已处理 {i}/{len(df)} 行")

        reporter.report_step(3, "批量Embedding")
        ...

    # admin 上传时：包装原 callback
    callback = make_admin_progress_callback(task_id, start_time)
    reporter = CallbackProgressReporter(callback)
    ingest_excel(file_path, reporter=reporter)

    # CLI 时：用 NoOp
    ingest_excel(file_path, reporter=NoOpProgressReporter())

【约束】
- 接口必须无副作用（除了写日志/Redis）
- 实现必须线程/异步安全（callback 在 BackgroundTask 里跑）
- 不抛异常（进度报告失败不能影响主流程）
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 抽象接口
# ════════════════════════════════════════════════════════════════

class ProgressReporter(ABC):
    """
    进度报告器抽象接口。

    所有 agent 通过这个接口报告进度。具体实现决定进度去哪里：
      - 写 Redis（admin 上传场景）
      - 打印控制台（CLI 调试）
      - 什么都不做（单元测试）
    """

    @abstractmethod
    def report_step(
        self,
        step: int,
        name: str,
        detail: str = "",
    ) -> None:
        """
        报告主步骤切换。

        Args:
            step:   当前步骤编号（1-based）
            name:   步骤名称（"读取Excel"、"批量Embedding"、"写入数据库"）
            detail: 可选的额外说明
        """
        pass

    @abstractmethod
    def report_substep(
        self,
        current: int,
        total: int,
        detail: str = "",
    ) -> None:
        """
        报告子进度（在某个 step 内部）。

        Args:
            current: 当前进度（已完成数量）
            total:   总数
            detail:  可选的额外说明（"已写入 batch 3/15"）
        """
        pass

    @abstractmethod
    def report_status(
        self,
        status: str,
        message: str = "",
    ) -> None:
        """
        报告任务状态变化。

        Args:
            status:  状态字符串（pending / running / done / failed / cancelled）
            message: 可选的状态说明（失败原因等）
        """
        pass


# ════════════════════════════════════════════════════════════════
# 实现 1：NoOp（什么都不做，CLI/测试用）
# ════════════════════════════════════════════════════════════════

class NoOpProgressReporter(ProgressReporter):
    """
    什么都不做的实现。
    用于 CLI 入库或单元测试场景，不需要进度反馈。
    """

    def report_step(self, step: int, name: str, detail: str = "") -> None:
        pass

    def report_substep(self, current: int, total: int, detail: str = "") -> None:
        pass

    def report_status(self, status: str, message: str = "") -> None:
        pass


# ════════════════════════════════════════════════════════════════
# 实现 2：Callback（包装原 progress_callback 函数）
# ════════════════════════════════════════════════════════════════

class CallbackProgressReporter(ProgressReporter):
    """
    包装一个 callback 函数。

    这是给 admin 上传场景用的。admin_knowledge.py 已经有
    _make_progress_callback() 返回一个 callback，签名是：

        callback(step: int, name: str, detail: str = "",
                 current: Optional[int] = None,
                 total: Optional[int] = None) -> None

    我们把它包装成 ProgressReporter，让 agent 用统一接口调用。

    设计要点：
    - 失败时记日志，不抛异常（进度上报不能影响主流程）
    - 内部记住"当前 step"，substep 时自动带上 step 号
    """

    def __init__(self, callback: Optional[Callable] = None):
        """
        Args:
            callback: 原始 callback 函数。如果为 None，行为等同 NoOp。
        """
        self._callback = callback
        self._current_step = 0
        self._current_step_name = ""

    def report_step(self, step: int, name: str, detail: str = "") -> None:
        self._current_step = step
        self._current_step_name = name
        self._safe_call(step=step, name=name, detail=detail)

    def report_substep(self, current: int, total: int, detail: str = "") -> None:
        self._safe_call(
            step=self._current_step,
            name=self._current_step_name,
            detail=detail,
            current=current,
            total=total,
        )

    def report_status(self, status: str, message: str = "") -> None:
        detail = f"[status={status}] {message}".strip()
        self._safe_call(
            step=self._current_step,
            name=self._current_step_name,
            detail=detail,
        )

    def _safe_call(self, **kwargs) -> None:
        """安全调用 callback，失败不抛异常。"""
        if self._callback is None:
            return
        try:
            self._callback(**kwargs)
        except Exception as e:
            logger.warning(
                f"[CallbackProgressReporter] callback 调用失败，忽略: {type(e).__name__}: {e}"
            )


# ════════════════════════════════════════════════════════════════
# 实现 3：Console（打印到控制台，CLI 调试用）
# ════════════════════════════════════════════════════════════════

class ConsoleProgressReporter(ProgressReporter):
    """
    打印到控制台的实现。
    用于本地命令行调试，看进度直观。
    """

    def __init__(self, prefix: str = ""):
        """
        Args:
            prefix: 前缀字符串（如 "[ingest_excel] "）
        """
        self._prefix = prefix
        self._start_time = time.time()

    def _elapsed(self) -> str:
        seconds = int(time.time() - self._start_time)
        return f"{seconds}s"

    def report_step(self, step: int, name: str, detail: str = "") -> None:
        msg = f"{self._prefix}[step {step}] {name}"
        if detail:
            msg += f" - {detail}"
        msg += f" ({self._elapsed()})"
        print(msg, flush=True)

    def report_substep(self, current: int, total: int, detail: str = "") -> None:
        pct = (current / total * 100) if total > 0 else 0
        msg = f"{self._prefix}  -> {current}/{total} ({pct:.1f}%)"
        if detail:
            msg += f" - {detail}"
        print(msg, flush=True)

    def report_status(self, status: str, message: str = "") -> None:
        msg = f"{self._prefix}[STATUS={status}]"
        if message:
            msg += f" {message}"
        msg += f" ({self._elapsed()})"
        print(msg, flush=True)


# ════════════════════════════════════════════════════════════════
# 公共导出
# ════════════════════════════════════════════════════════════════

__all__ = [
    "ProgressReporter",
    "NoOpProgressReporter",
    "CallbackProgressReporter",
    "ConsoleProgressReporter",
]
