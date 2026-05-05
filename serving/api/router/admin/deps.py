# -*- coding: utf-8 -*-
"""
管理员鉴权依赖

提供 require_admin 依赖注入，所有 /api/admin/* 路由统一使用。
检查逻辑：
  1. 验证 JWT token（复用现有 get_current_user）
  2. 检查 is_superuser == True 或 user_info.role == "admin"
"""

import json
from typing import Dict, Any

from fastapi import Depends, HTTPException, status

from api.middleware.auth_middleware import get_current_user


def parse_user_info(user_or_obj) -> dict:
    """安全解析 user_info 字段（兼容 dict 和 ORM 对象）。

    - dict: 从 user.get("user_info") 读取
    - ORM 对象: 从 getattr(user, "user_info") 读取
    - 值可能是 str(JSON) 或 dict，统一返回 dict
    """
    if isinstance(user_or_obj, dict):
        raw = user_or_obj.get("user_info") or {}
    else:
        raw = getattr(user_or_obj, "user_info", None) or {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    if isinstance(raw, dict):
        return raw
    return {}


def require_admin(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    管理员权限依赖。

    验证当前用户是否具有管理员权限：
    - is_superuser == True
    - 或 user_info.role == "admin"

    Returns:
        当前用户信息字典

    Raises:
        HTTPException 403: 用户不是管理员
    """
    if current_user.get("is_superuser"):
        return current_user

    user_info = parse_user_info(current_user)
    if user_info.get("role") == "admin":
        return current_user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="权限不足：此操作需要管理员权限",
    )


def require_admin_or_reviewer(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """管理员或审核员权限依赖。"""
    if current_user.get("is_superuser"):
        return current_user

    user_info = parse_user_info(current_user)
    if user_info.get("role") in ("admin", "reviewer"):
        return current_user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="权限不足：此操作需要管理员或审核员权限",
    )
