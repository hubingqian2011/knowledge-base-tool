# -*- coding: utf-8 -*-
"""请求级 context：用于在单次请求内传递 request_id，便于日志串联。"""
import contextvars
from typing import Optional

_request_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_id", default=None
)


def set_request_id(request_id: Optional[str]) -> None:
    """设置当前请求的 request_id（入口处调用）。"""
    _request_id_ctx.set(request_id)


def get_request_id() -> Optional[str]:
    """获取当前请求的 request_id（日志等处使用，无则返回 None）。"""
    return _request_id_ctx.get()
