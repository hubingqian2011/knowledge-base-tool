import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SEARCH_ALIAS_GROUPS_KEY = "__metadata_alias_groups__"

logger = logging.getLogger(__name__)


COMMON_CANONICAL_FIELDS: Tuple[str, ...] = (
    "model",
    "series",
    "generation",
    "controller",
    "product_line",
    "tonnage",
    "language",
    "service_life",
)

COLLECTION_SPECIFIC_CANONICAL_FIELDS: Dict[str, Tuple[str, ...]] = {
    "workorder": (
        "work_order_number",
        "component_category",
        "component_subcategory",
        "product_model",
        "fault_type",
        "handling_method",
        "details",
        "serial_number",
        "service_provider",
        "work_order_create_date",
        "manufacture_date",
        "generation_name",
        "characteristic_char",
    ),
    "workorder_graph": (
        "work_order_number",
        "component_category",
        "component_subcategory",
        "product_model",
        "fault_type",
        "handling_method",
        "details",
    ),
    "special_workorder": ("work_order_number",),
    "parameter": ("controller_type", "version"),
    "parameter_updated": ("controller_type", "version"),
}

COMMON_ALIAS_DEFINITIONS: Dict[str, Tuple[str, ...]] = {
    "model": ("model", "machine_code", "机型码"),
    "series": ("series", "系列"),
    "generation": ("generation", "代次"),
    "controller": ("controller", "控制器"),
    "product_line": ("product_line", "ipd", "产品线"),
    "tonnage": ("tonnage", "clamping_force", "锁模力"),
    "language": ("language", "语言"),
    "service_life": ("service_life", "使用年限"),
}

_WORKORDER_ALIAS_DEFINITIONS: Dict[str, Tuple[str, ...]] = {
    "work_order_number": ("work_order_number", "派工单号"),
    "component_category": ("component_category", "部件大类"),
    "component_subcategory": ("component_subcategory", "部件小类"),
    "product_model": ("product_model", "产品型号"),
    "fault_type": ("fault_type", "故障类型"),
    "handling_method": ("handling_method", "处理方式"),
    "details": ("details", "明细"),
    "serial_number": ("serial_number", "序列号"),
    "service_provider": ("service_provider", "服务机构"),
    "work_order_create_date": ("work_order_create_date", "派工单创建日期"),
    "manufacture_date": ("manufacture_date", "出厂日期"),
    "generation_name": ("generation_name", "代次名称"),
    "characteristic_char": ("characteristic_char", "特性字符"),
}

COLLECTION_SPECIFIC_ALIAS_DEFINITIONS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "workorder": dict(_WORKORDER_ALIAS_DEFINITIONS),
    "workorder_graph": {
        k: v for k, v in _WORKORDER_ALIAS_DEFINITIONS.items()
        if k in (
            "work_order_number", "component_category", "component_subcategory",
            "product_model", "fault_type", "handling_method", "details",
        )
    },
    "special_workorder": {
        "work_order_number": ("work_order_number", "派工单号"),
    },
    "parameter": {
        "controller_type": ("controller_type", "控制器型号"),
        "version": ("version", "版本"),
    },
    "parameter_updated": {
        "controller_type": ("controller_type", "控制器型号"),
        "version": ("version", "版本"),
    },
}

COLLECTION_TYPE_GROUPS: Dict[str, Tuple[str, ...]] = {
    "manual": ("manual", "manual_total", "manual_chunks"),
    "principle": ("principle", "principle_total", "principle_chunks"),
    "video": ("video", "video_description"),
    "workorder": ("workorder", "workorder_v2"),
    "workorder_graph": ("workorder_graph",),
    "special_workorder": ("special_workorder",),
    "parameter": ("parameter",),
    "parameter_updated": ("parameter_updated",),
}

STRICT_WRITE_REQUIRED_COLLECTIONS = frozenset(
    {
        "manual",
        "principle",
        "video",
        "workorder",
        "workorder_graph",
        "special_workorder",
        "parameter",
        "parameter_updated",
    }
)

_COLLECTION_ALIAS_TO_CANONICAL: Dict[str, str] = {}
_GLOBAL_ALIAS_TO_CANONICAL: Dict[str, str] = {}


def _register_alias(alias_map: Dict[str, str], alias: str, canonical_key: str) -> None:
    existing = alias_map.get(alias)
    if existing and existing != canonical_key:
        raise ValueError(
            f"metadata alias '{alias}' conflicts between canonical keys '{existing}' and '{canonical_key}'"
        )
    alias_map[alias] = canonical_key


def _init_alias_indexes() -> None:
    global _COLLECTION_ALIAS_TO_CANONICAL, _GLOBAL_ALIAS_TO_CANONICAL

    if _COLLECTION_ALIAS_TO_CANONICAL and _GLOBAL_ALIAS_TO_CANONICAL:
        return

    for canonical_type, aliases in COLLECTION_TYPE_GROUPS.items():
        for alias in aliases:
            _register_alias(_COLLECTION_ALIAS_TO_CANONICAL, alias, canonical_type)

    for canonical_key, aliases in COMMON_ALIAS_DEFINITIONS.items():
        for alias in aliases:
            _register_alias(_GLOBAL_ALIAS_TO_CANONICAL, alias, canonical_key)
    for alias_map in COLLECTION_SPECIFIC_ALIAS_DEFINITIONS.values():
        for canonical_key, aliases in alias_map.items():
            for alias in aliases:
                _register_alias(_GLOBAL_ALIAS_TO_CANONICAL, alias, canonical_key)


_init_alias_indexes()


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _comparable_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return str(value)


def _dedupe(items: Iterable[str]) -> Tuple[str, ...]:
    ordered: List[str] = []
    for item in items:
        if item not in ordered:
            ordered.append(item)
    return tuple(ordered)


def infer_collection_type(collection_type: str, metadata: Optional[Dict[str, Any]] = None) -> str:
    def _match_candidate(candidate: str) -> str:
        direct = _COLLECTION_ALIAS_TO_CANONICAL.get(candidate)
        if direct:
            return direct

        for alias, canonical in sorted(
            _COLLECTION_ALIAS_TO_CANONICAL.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            if (
                candidate.startswith(f"{alias}_")
                or candidate.endswith(f"_{alias}")
                or f"_{alias}_" in candidate
                or candidate.startswith(f"{alias}-")
                or candidate.endswith(f"-{alias}")
                or f"-{alias}-" in candidate
            ):
                return canonical
        return ""

    candidates = [
        str(collection_type or "").strip().lower(),
        str((metadata or {}).get("knowledge_type", "") or "").strip().lower(),
        str((metadata or {}).get("collection_name", "") or "").strip().lower(),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        canonical = _match_candidate(candidate)
        if canonical:
            return canonical
    return ""


def get_canonical_schema_for_collection(collection_type: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Tuple[str, ...]]:
    return _build_alias_map(infer_collection_type(collection_type, metadata or {}))


def build_source_schema(collection_type: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Tuple[str, ...]]:
    return get_canonical_schema_for_collection(collection_type, metadata)


def canonicalize_metadata_key(key: str) -> str:
    return _GLOBAL_ALIAS_TO_CANONICAL.get(key, key)


def get_aliases_for_canonical(canonical_key: str, collection_type: str = "") -> Tuple[str, ...]:
    alias_map = _build_alias_map(collection_type)
    aliases = alias_map.get(canonical_key)
    return aliases if aliases else (canonical_key,)


def _build_alias_map(collection_type: str) -> Dict[str, Tuple[str, ...]]:
    canonical_type = infer_collection_type(collection_type, {})
    alias_map = dict(COMMON_ALIAS_DEFINITIONS)
    if canonical_type in COLLECTION_SPECIFIC_ALIAS_DEFINITIONS:
        alias_map.update(COLLECTION_SPECIFIC_ALIAS_DEFINITIONS[canonical_type])
    return alias_map


def _required_canonical_fields(collection_type: str) -> Tuple[str, ...]:
    canonical_type = infer_collection_type(collection_type, {})
    base_fields = list(COMMON_CANONICAL_FIELDS)
    base_fields.extend(COLLECTION_SPECIFIC_CANONICAL_FIELDS.get(canonical_type, ()))
    return _dedupe(base_fields)


def _resolve_conflict(
    canonical_key: str,
    hits: Sequence[Tuple[str, Any]],
    aliases: Sequence[str],
    strict_conflicts: bool,
) -> Any:
    by_alias = {key: value for key, value in hits}
    unique_values = {_comparable_value(value) for _, value in hits}
    if len(unique_values) <= 1:
        return hits[0][1]

    priority_order = [canonical_key] + [alias for alias in aliases if alias != canonical_key]
    for candidate_key in priority_order:
        if candidate_key in by_alias:
            selected = by_alias[candidate_key]
            if strict_conflicts:
                raise ValueError(f"conflicting metadata aliases for '{canonical_key}': {hits}")
            logger.warning(
                "[metadata-normalize] conflict on '%s', prefer '%s'='%s', candidates=%s",
                canonical_key,
                candidate_key,
                selected,
                hits,
            )
            return selected

    if strict_conflicts:
        raise ValueError(f"conflicting metadata aliases for '{canonical_key}': {hits}")
    logger.warning(
        "[metadata-normalize] conflict on '%s', fallback first candidate=%s",
        canonical_key,
        hits,
    )
    return hits[0][1]


def normalize_metadata_for_ingest(
    collection_type: str,
    metadata: Dict[str, Any],
    include_alias_keys: bool = False,
    strict_conflicts: bool = True,
    require_collection_type: bool = True,
) -> Dict[str, Any]:
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a dict")

    effective_collection_type = infer_collection_type(collection_type, metadata)
    if require_collection_type and not effective_collection_type:
        raise ValueError(f"unable to infer collection type for metadata ingest: {collection_type!r}")

    alias_map = _build_alias_map(effective_collection_type)
    required_fields = _required_canonical_fields(effective_collection_type)
    normalized = dict(metadata)

    for canonical_key, aliases in alias_map.items():
        hits: List[Tuple[str, Any]] = []
        for alias in aliases:
            if alias in metadata and not _is_empty(metadata[alias]):
                hits.append((alias, metadata[alias]))
        if not hits:
            continue

        normalized_value = _resolve_conflict(
            canonical_key=canonical_key,
            hits=hits,
            aliases=aliases,
            strict_conflicts=strict_conflicts,
        )
        normalized[canonical_key] = normalized_value
        if include_alias_keys:
            for alias in aliases:
                normalized[alias] = normalized_value

    output: Dict[str, Any] = {}
    for key, value in normalized.items():
        if key in required_fields:
            output[key] = value
            continue
        if key not in _GLOBAL_ALIAS_TO_CANONICAL:
            output[key] = value

    if include_alias_keys:
        for canonical_key, aliases in alias_map.items():
            if canonical_key not in output:
                continue
            for alias in aliases:
                output[alias] = output[canonical_key]

    return output


def canonicalize_result_metadata(collection_type: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}

    effective_collection_type = infer_collection_type(collection_type, metadata)
    alias_map = _build_alias_map(effective_collection_type)
    canonical_view = dict(metadata)

    for canonical_key, aliases in alias_map.items():
        hits: List[Tuple[str, Any]] = []
        for alias in aliases:
            if alias in metadata and not _is_empty(metadata[alias]):
                hits.append((alias, metadata[alias]))
        if hits:
            canonical_view[canonical_key] = _resolve_conflict(
                canonical_key=canonical_key,
                hits=hits,
                aliases=aliases,
                strict_conflicts=False,
            )

    return canonical_view


def normalize_filter_for_search(
    collection_type: str,
    filter_metadata: Dict[str, Any],
    strict_conflicts: bool = False,
) -> Dict[str, Any]:
    if filter_metadata is None:
        return {}
    if not isinstance(filter_metadata, dict):
        raise ValueError("filter_metadata must be a dict")

    effective_collection_type = infer_collection_type(collection_type, filter_metadata)
    alias_map = _build_alias_map(effective_collection_type)
    normalized: Dict[str, Any] = {}
    alias_groups: Dict[str, List[str]] = {}
    hit_groups: Dict[str, List[Tuple[str, Any]]] = {}

    for raw_key, value in filter_metadata.items():
        if raw_key == SEARCH_ALIAS_GROUPS_KEY:
            continue
        canonical_key = canonicalize_metadata_key(raw_key)
        hit_groups.setdefault(canonical_key, []).append((raw_key, value))

    for canonical_key, hits in hit_groups.items():
        aliases = alias_map.get(canonical_key)
        if aliases:
            alias_groups[canonical_key] = [alias for alias in aliases if alias != canonical_key]
        else:
            alias_groups[canonical_key] = []

        if len(hits) == 1:
            normalized[canonical_key] = hits[0][1]
            continue

        normalized[canonical_key] = _resolve_conflict(
            canonical_key=canonical_key,
            hits=hits,
            aliases=aliases or (canonical_key,),
            strict_conflicts=strict_conflicts,
        )

    if alias_groups:
        normalized[SEARCH_ALIAS_GROUPS_KEY] = alias_groups
    return normalized
