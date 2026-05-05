# -*- coding: utf-8 -*-
"""
llm_logger.py — LLM 调试日志工具

用环境变量 LLM_DEBUG=1 控制开关。
输出完整的 messages、tools、LLM 输出为 JSON 格式（indent=2）。
生产环境设 LLM_DEBUG=0 关闭。
"""

import json
import os
from util.logging.logger import get_logger

logger = get_logger(__name__)
LLM_DEBUG = os.getenv("LLM_DEBUG", "0") == "1"


def log_llm_call(call_id: str, phase: str, messages: list,
                 tools: list = None, tool_choice: str = None,
                 model: str = None):
    """记录完整的 LLM 输入，一次性输出整个 JSON"""
    if not LLM_DEBUG:
        return

    log_data = {
        "call_id": call_id,
        "phase": phase,
        "model": model,
        "tool_choice": tool_choice,
        "messages_count": len(messages),
        "messages": messages,
    }
    if tools:
        log_data["tools"] = tools

    logger.debug(
        f"\n{'=' * 80}\n"
        f"[LLM 输入] call_id={call_id} | phase={phase}\n"
        f"{'=' * 80}\n"
        f"{json.dumps(log_data, ensure_ascii=False, indent=2, default=str)}\n"
        f"{'=' * 80}"
    )


def log_llm_output(call_id: str, phase: str,
                   content: str = None, tool_calls: list = None):
    """记录完整的 LLM 输出"""
    if not LLM_DEBUG:
        return

    output_data = {
        "call_id": call_id,
        "phase": phase,
        "content": content,
        "tool_calls": tool_calls,
    }

    logger.debug(
        f"\n{'*' * 80}\n"
        f"[LLM 输出] call_id={call_id} | phase={phase}\n"
        f"{'*' * 80}\n"
        f"{json.dumps(output_data, ensure_ascii=False, indent=2, default=str)}\n"
        f"{'*' * 80}"
    )
