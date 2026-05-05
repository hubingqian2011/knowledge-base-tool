import hashlib
import json
from typing import Any, Dict, Iterable, Optional

from database.cache.cache_store import CacheStore
from util.logging.logger import get_logger

logger = get_logger(__name__)

ADMIN_UI_CACHE_PREFIX = "admin_ui"

ADMIN_UI_CACHE_GROUP_PREFIXES = {
    "dashboard": (
        f"{ADMIN_UI_CACHE_PREFIX}:knowledge:collections:",
    ),
    "knowledge": (
        f"{ADMIN_UI_CACHE_PREFIX}:knowledge:collections:",
        f"{ADMIN_UI_CACHE_PREFIX}:knowledge:collection_files:",
    ),
}


def _get_cache_store() -> Optional[CacheStore]:
    try:
        return CacheStore()
    except Exception as exc:
        logger.warning("Admin UI cache unavailable, degrade to no-cache: {}", exc)
        return None


def _normalize_cache_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_cache_value(value[key]) for key in sorted(value.keys(), key=str)}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_cache_value(item) for item in value]
    return value


def _build_payload_digest(payload: Dict[str, Any]) -> str:
    raw = json.dumps(_normalize_cache_value(payload), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def build_admin_ui_cache_key(
    namespace: str,
    identifier: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    role_context: Optional[Dict[str, Any]] = None,
) -> str:
    normalized_payload = {
        "params": _normalize_cache_value(params or {}),
        "role": _normalize_cache_value(role_context or {}),
    }
    return f"{ADMIN_UI_CACHE_PREFIX}:{namespace}:{identifier}:{_build_payload_digest(normalized_payload)}"


def get_admin_ui_cache(
    namespace: str,
    identifier: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    role_context: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    store = _get_cache_store()
    if not store:
        return None
    cache_key = build_admin_ui_cache_key(namespace, identifier, params=params, role_context=role_context)
    cached_payload = store.get(cache_key)
    if isinstance(cached_payload, dict):
        return cached_payload
    return None


def set_admin_ui_cache(
    namespace: str,
    identifier: str,
    *,
    payload: Dict[str, Any],
    ttl_seconds: int,
    params: Optional[Dict[str, Any]] = None,
    role_context: Optional[Dict[str, Any]] = None,
) -> None:
    store = _get_cache_store()
    if not store:
        return
    cache_key = build_admin_ui_cache_key(namespace, identifier, params=params, role_context=role_context)
    store.set(cache_key, payload, expire=ttl_seconds)


def invalidate_admin_ui_cache_groups(*groups: str) -> int:
    prefixes: list[str] = []
    for group in groups:
        prefixes.extend(ADMIN_UI_CACHE_GROUP_PREFIXES.get(group, ()))
    return invalidate_admin_ui_cache_prefixes(prefixes)


def invalidate_admin_ui_cache_prefixes(prefixes: Iterable[str]) -> int:
    store = _get_cache_store()
    if not store:
        return 0
    deleted_total = 0
    for prefix in prefixes:
        try:
            matched_keys = store.client.keys(store._get_key(f"{prefix}*"))
            if not matched_keys:
                continue
            deleted_total += int(store.client.delete(*matched_keys) or 0)
        except Exception as exc:
            logger.warning("Invalidate admin UI cache by prefix failed: prefix={}, error={}", prefix, exc)
    return deleted_total
