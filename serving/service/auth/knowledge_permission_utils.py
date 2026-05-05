# -*- coding: utf-8 -*-
"""
知识权限等级工具。

当前只支持三档数值权限：
- 1: 总部
- 2: 办事处
- 3: 客户
"""
import json
from typing import Any, Dict, List, Optional


PERMISSION_LEVEL_TO_NAME = {
    1: "总部",
    2: "办事处",
    3: "客户",
}

USER_PERMISSION_FIELD_CANDIDATES = (
    "permission_level",
)

PERMISSION_FILTER_LEVELS_KEY = "__permission_allowed_levels"
PERMISSION_FILTER_ALLOW_LEGACY_KEY = "__permission_allow_legacy"


def normalize_permission_level(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("permission_level 不支持布尔值")

    if isinstance(value, int):
        if value in PERMISSION_LEVEL_TO_NAME:
            return value
        raise ValueError(f"无效的 permission_level: {value}")

    text = str(value or "").strip()
    if not text:
        raise ValueError("permission_level 不能为空")

    if text in {"1", "2", "3"}:
        return int(text)

    raise ValueError(f"无效的 permission_level: {value}")


def build_permission_metadata(value: Any) -> Dict[str, Any]:
    level = normalize_permission_level(value)
    return {
        "permission_level": level,
        "permission_level_name": PERMISSION_LEVEL_TO_NAME[level],
    }


def extract_user_info(user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(user, dict):
        return {}

    user_info = user.get("user_info") or {}
    if isinstance(user_info, str):
        try:
            return json.loads(user_info)
        except json.JSONDecodeError:
            return {}
    if isinstance(user_info, dict):
        return dict(user_info)
    return {}


def resolve_user_permission_level(user_info: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(user_info, dict):
        return None

    for field_name in USER_PERMISSION_FIELD_CANDIDATES:
        raw_value = user_info.get(field_name)
        if raw_value in (None, ""):
            continue
        return normalize_permission_level(raw_value)

    return None


def get_allowed_permission_levels(user_info: Optional[Dict[str, Any]]) -> List[int]:
    try:
        level = resolve_user_permission_level(user_info)
    except ValueError:
        return []
    if level is None:
        return []
    return list(range(level, 4))


def apply_permission_filter(
    metadata: Optional[Dict[str, Any]],
    user_info: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    filtered_metadata = dict(metadata or {})
    filtered_metadata[PERMISSION_FILTER_LEVELS_KEY] = get_allowed_permission_levels(user_info)
    filtered_metadata[PERMISSION_FILTER_ALLOW_LEGACY_KEY] = True
    return filtered_metadata


def pop_permission_filter(
    metadata: Optional[Dict[str, Any]],
) -> tuple[Dict[str, Any], Optional[List[int]], bool]:
    clean_metadata = dict(metadata or {})
    raw_allowed_levels = clean_metadata.pop(PERMISSION_FILTER_LEVELS_KEY, None)
    allow_legacy = bool(clean_metadata.pop(PERMISSION_FILTER_ALLOW_LEGACY_KEY, False))

    if raw_allowed_levels is None:
        return clean_metadata, None, allow_legacy

    if isinstance(raw_allowed_levels, (list, tuple, set)):
        allowed_levels = []
        for item in raw_allowed_levels:
            if item in (None, ""):
                continue
            allowed_levels.append(normalize_permission_level(item))
        return clean_metadata, allowed_levels, allow_legacy

    return clean_metadata, [normalize_permission_level(raw_allowed_levels)], allow_legacy


def document_matches_permission(
    metadata: Optional[Dict[str, Any]],
    allowed_levels: Optional[List[int]],
    allow_legacy: bool,
) -> bool:
    if allowed_levels is None:
        return True

    document_metadata = metadata if isinstance(metadata, dict) else {}
    raw_permission_level = document_metadata.get("permission_level")

    if raw_permission_level in (None, ""):
        return allow_legacy

    try:
        normalized_level = normalize_permission_level(raw_permission_level)
    except ValueError:
        return False

    return normalized_level in allowed_levels
