# -*- coding: utf-8 -*-
"""
用户权限工具函数

入库权限检查。
"""
import json
from typing import Any, Dict


def _parse_user_info(user: dict) -> Dict[str, Any]:
    """安全地解析 user 上的 user_info（可能是 str 或 dict）。"""
    user_info = user.get("user_info") or {}
    if isinstance(user_info, str):
        try:
            user_info = json.loads(user_info)
        except (json.JSONDecodeError, TypeError):
            user_info = {}
    return user_info if isinstance(user_info, dict) else {}


def check_ingest_permission(user: dict) -> bool:
    """
    检查用户是否有入库权限。
    只有超级用户或 admin 角色才能入库。
    """
    if not user:
        return False

    if user.get("is_superuser"):
        return True

    user_info = _parse_user_info(user)
    role = user_info.get("role", "")
    return role == "admin"
