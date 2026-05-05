# -*- coding: utf-8 -*-
"""
管理员 - 知识库管理接口

GET  /api/admin/knowledge/collections                         collection 列表
POST /api/admin/knowledge/collections                         创建/更新 collection 元信息
GET  /api/admin/knowledge/tree                                知识目录树
GET  /api/admin/knowledge/tree-options                        目录归属选项
POST /api/admin/knowledge/tree/validate                       目录 metadata 校验
GET  /api/admin/knowledge/upload-metadata-config              上传文档信息字段配置
PUT  /api/admin/knowledge/upload-metadata-config              更新上传文档信息字段配置
POST /api/admin/knowledge/upload-batch                        后台批量上传
GET  /api/admin/knowledge/upload-batches/{batch_id}          批次状态
POST /api/admin/knowledge/upload-batches/{batch_id}/retry    失败重试
DELETE /api/admin/knowledge/upload-batches/{batch_id}        删除/取消上传批次
GET  /api/admin/knowledge/files                               统一文件列表
DELETE /api/admin/knowledge/files/{document_id}              文件删除
GET  /api/admin/knowledge/files/{document_id}                 文件详情
POST /api/admin/knowledge/files/{document_id}/retry          单文件重试
GET  /api/admin/knowledge/files/{document_id}/preview         文件预览
GET  /api/admin/knowledge/collections/{collection_name}/files collection 文件列表
"""

import base64
import copy
import io
import json
import mimetypes
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .cache_utils import (
    get_admin_ui_cache,
    invalidate_admin_ui_cache_groups,
    set_admin_ui_cache,
)
from .deps import Depends, parse_user_info, require_admin, require_admin_or_reviewer
from config.config import (
    COLLECTION_MANUAL,
    COLLECTION_MANUAL_TOTAL,
    COLLECTION_MANUAL_CHUNKS,
    COLLECTION_PRINCIPLE,
    COLLECTION_PRINCIPLE_TOTAL,
    COLLECTION_PRINCIPLE_CHUNKS,
    COLLECTION_WORKORDER as _COLLECTION_WORKORDER_CSV,
)

_workorder_cols = [c.strip() for c in _COLLECTION_WORKORDER_CSV.split(",")]
from database.document.model import DocumentModel
from database.sql.model.admin import KbCollection, KbFile
from database.sql.repository.repository_factory import RepositoryFactory
from service.auth.knowledge_permission_utils import build_permission_metadata, normalize_permission_level
from service.knowledge.knowledge_service import knowledge_service
from service.system.file_reader_main import file_reader
from util.logging.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["管理后台-知识库管理"])

SERVING_BASE_DIR = Path(__file__).resolve().parents[3]
PREVIEW_TEXT_LIMIT = 20000
SUMMARY_METADATA_KEYS = (
    "batch_id",
    "section_number",
    "page_number",
    "series",
    "generation",
    "controller",
    "product_line",
    "tonnage",
    "industry_major",
    "industry_minor",
    "special_code",
    "slice_id",
    "source_file",
    "document_fingerprint",
    "knowledge_type",
    "permission_level",
    "permission_level_name",
    "folder_path",
    "category_level_1",
    "category_level_2",
    "category_level_3",
    "original_file_name",
    "original_file_type",
    "original_file_path",
)
FOLDER_METADATA_KEYS = ("folder_path", "category_level_1", "category_level_2", "category_level_3")
UPLOADED_AT_KEYS = ("uploaded_at", "ingested_at", "导入时间", "created_at")
UPLOADER_KEYS = ("uploader", "uploaded_by", "uploader_name", "created_by", "created_by_name")
DEFAULT_FOLDER_RULE = {
    "supports_folder_assignment": True,
    "min_depth": 0,
    "max_depth": 3,
    "metadata_keys": list(FOLDER_METADATA_KEYS),
}
COLLECTION_FOLDER_RULES = {
    "parameter": {
        "supports_folder_assignment": False,
        "min_depth": 0,
        "max_depth": 0,
    }
}
ADMIN_UPLOAD_BATCH_DIR = SERVING_BASE_DIR / "temp_admin_upload_batches"
ADMIN_ORIGINAL_FILE_DIR = SERVING_BASE_DIR / "original_knowledge_files"
ADMIN_BATCH_KEY_PREFIX = "admin:knowledge:upload_batch:"
INGEST_COLLECTION_TYPES = {"manual", "principle", "workorder", "excellent_workorder"}
ASYNC_GRAPHRAG_COLLECTION_TYPES = {"manual", "principle"}
KNOWLEDGE_ROUTE_COLLECTIONS = {"parameter_updated", "parameter"}
DEFAULT_WORKORDER_COLLECTION = "workorder"
DEFAULT_UPLOAD_BATCH_TTL_SECONDS = 24 * 60 * 60
KNOWLEDGE_ROUTE_ALLOWED_EXTENSIONS = {
    "parameter_updated": {".xlsx", ".xls"},
    "parameter": {".xlsx", ".xls"},
}
TASK_STATUS_DISPLAY = {
    "pending": "等待中",
    "running": "处理中",
    "done": "已完成",
    "failed": "失败",
    "unknown": "状态未知",
}
BATCH_STATUS_DISPLAY = {
    "running": "处理中",
    "failed": "失败",
    "done": "已完成",
    "partial_failed": "部分失败",
    "unknown": "状态未知",
}
BATCH_STATUS_ORDER = {"running": 0, "failed": 1, "partial_failed": 2, "unknown": 3, "done": 4}
TASK_CANCEL_TTL_SECONDS = 24 * 60 * 60


class CollectionUpsertRequest(BaseModel):
    name: str = Field(..., description="collection name")
    display_name: Optional[str] = Field(None, description="display name")
    type: Optional[str] = Field(None, description="collection type")
    description: Optional[str] = Field(None, description="description")


class KnowledgeTreeValidateRequest(BaseModel):
    collection_name: str = Field(..., description="collection name")
    node_id: Optional[str] = Field(None, description="tree node id")
    selected_category: Optional[str] = Field(None, description="category alias")
    folder_path: Optional[List[str]] = Field(None, description="folder path array")
    category_level_1: Optional[str] = Field(None, description="category level 1")
    category_level_2: Optional[str] = Field(None, description="category level 2")
    category_level_3: Optional[str] = Field(None, description="category level 3")


class RetryUploadBatchRequest(BaseModel):
    failed_items: Optional[List[str]] = Field(
        None,
        description="failed item ids; retry all failed items when omitted",
    )


class RetryKnowledgeFileRequest(BaseModel):
    task_id: Optional[str] = Field(
        None,
        description="failed task id; higher priority than document_id when provided",
    )


def _get_milvus_collection_count(collection_name: str) -> int:
    try:
        from database.vector.milvus_client import MilvusClient
        from pymilvus import Collection, utility

        client = MilvusClient()
        if not utility.has_collection(collection_name, using=client.alias):
            return 0
        col = Collection(collection_name, using=client.alias)
        col.flush()
        return col.num_entities
    except Exception as error:
        logger.warning(f"Get Milvus collection count failed: {collection_name}, {error}")
        return -1


def _list_milvus_collections() -> List[str]:
    try:
        from database.vector.milvus_client import MilvusClient
        from pymilvus import utility

        client = MilvusClient()
        return utility.list_collections(using=client.alias)
    except Exception as error:
        logger.warning(f"List Milvus collections failed: {error}")
        return []


def _infer_collection_type(collection_name: str) -> str:
    lowered = (collection_name or "").lower()
    if "manual" in lowered:
        return "manual"
    if "principle" in lowered:
        return "principle"
    if "video" in lowered:
        return "video"
    if "parameter" in lowered:
        return "parameter"
    if "workorder" in lowered:
        return "workorder"
    return ""


def _get_or_sync_collections(session) -> List[KbCollection]:
    collections = session.query(KbCollection).order_by(KbCollection.id.asc()).all()
    if collections:
        return collections

    milvus_names = _list_milvus_collections()
    if not milvus_names:
        return []

    type_hints = {
        "manual": "manual",
        "workorder": "workorder",
        "principle": "principle",
        "video": "video",
        "parameter": "parameter",
    }
    for name in sorted(milvus_names):
        inferred_type = ""
        for hint_key, hint_val in type_hints.items():
            if hint_key in name.lower():
                inferred_type = hint_val
                break
        session.add(
            KbCollection(
                name=name,
                display_name=name,
                type=inferred_type,
            )
        )
    session.commit()
    return session.query(KbCollection).order_by(KbCollection.id.asc()).all()


def _build_collection_meta_map(session) -> Dict[str, Dict[str, Any]]:
    return {
        row.name: row.to_dict()
        for row in _get_or_sync_collections(session)
    }


def _resolve_relative_file_path(metadata: Dict[str, Any]) -> Optional[str]:
    raw_path = metadata.get("file_path")
    if raw_path in (None, ""):
        return None
    return str(raw_path)


def _resolve_absolute_file_path(metadata: Dict[str, Any]) -> Optional[Path]:
    raw_path = _resolve_relative_file_path(metadata)
    if not raw_path:
        return None

    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return SERVING_BASE_DIR / candidate


def _extract_uploaded_at(metadata: Dict[str, Any]) -> Optional[str]:
    for key in UPLOADED_AT_KEYS:
        value = metadata.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _extract_uploader(metadata: Dict[str, Any]) -> Optional[str]:
    for key in UPLOADER_KEYS:
        value = metadata.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _normalize_folder_path_items(items: Optional[List[str]]) -> List[str]:
    if not items:
        return []

    normalized_items: List[str] = []
    for raw_item in items:
        item = str(raw_item or "").strip()
        if not item:
            raise ValueError("folder_path contains empty segment")
        normalized_items.append(item)
    return normalized_items


def _extract_folder_path(metadata: Dict[str, Any]) -> Optional[List[str]]:
    raw_folder_path = metadata.get("folder_path")
    if isinstance(raw_folder_path, list):
        folder_items = _normalize_folder_path_items(raw_folder_path)
        return folder_items or None
    if isinstance(raw_folder_path, str) and raw_folder_path.strip():
        folder_items = _normalize_folder_path_items(
            [item.strip() for item in raw_folder_path.split("/") if item.strip()]
        )
        return folder_items or None

    category_items = [
        str(metadata.get(key) or "").strip()
        for key in ("category_level_1", "category_level_2", "category_level_3")
    ]
    folder_items = _normalize_folder_path_items([item for item in category_items if item])
    return folder_items or None


def _normalize_folder_path_query(folder_path: Optional[str]) -> List[str]:
    if folder_path in (None, ""):
        return []
    return _normalize_folder_path_items(
        [item.strip() for item in str(folder_path).split("/") if item.strip()]
    )


def _build_category_metadata(folder_path: List[str]) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    if folder_path:
        metadata["folder_path"] = folder_path
    for idx in range(3):
        key = f"category_level_{idx + 1}"
        if idx < len(folder_path):
            metadata[key] = folder_path[idx]
    return metadata


def _normalize_folder_metadata_input(
    *,
    folder_path: Optional[List[str]],
    category_level_1: Optional[str],
    category_level_2: Optional[str],
    category_level_3: Optional[str],
) -> List[str]:
    normalized_folder_path = _normalize_folder_path_items(folder_path)
    raw_category_levels = [category_level_1, category_level_2, category_level_3]
    normalized_category_items: List[str] = []
    seen_empty_level = False
    for raw_level in raw_category_levels:
        level = str(raw_level or "").strip()
        if not level:
            seen_empty_level = True
            continue
        if seen_empty_level:
            raise ValueError("category_level_1/2/3 must be contiguous")
        normalized_category_items.append(level)

    normalized_category_path = _normalize_folder_path_items(normalized_category_items)
    if normalized_folder_path and normalized_category_path and normalized_folder_path != normalized_category_path:
        raise ValueError("folder_path and category_level_1/2/3 are inconsistent")
    return normalized_folder_path or normalized_category_path


def _extract_file_size(metadata: Dict[str, Any]) -> Optional[int]:
    for key in ("file_size", "size", "file_bytes"):
        value = metadata.get(key)
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue

    abs_path = _resolve_absolute_file_path(metadata)
    if abs_path and abs_path.exists() and abs_path.is_file():
        return abs_path.stat().st_size
    return None


def _summarize_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in metadata.items()
        if key in SUMMARY_METADATA_KEYS and value not in (None, "", [])
    }


def _build_download_url(document_id: str, collection_name: str) -> str:
    return f"/api/knowledge_base/download_knowledge_file?document_id={document_id}&collection_name={collection_name}"


def _extract_original_file_path(metadata: Dict[str, Any]) -> Optional[str]:
    raw_path = metadata.get("original_file_path")
    if raw_path not in (None, ""):
        return str(raw_path)

    raw_file_path = metadata.get("file_path")
    source_file = str(metadata.get("source_file") or "").strip()
    if raw_file_path not in (None, "") and source_file:
        path_str = str(raw_file_path)
        if Path(path_str).name == Path(source_file).name:
            return path_str
    return None


def _resolve_absolute_original_file_path(metadata: Dict[str, Any]) -> Optional[Path]:
    raw_path = _extract_original_file_path(metadata)
    if not raw_path:
        return None

    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return SERVING_BASE_DIR / candidate


def _guess_file_type(*values: Optional[str]) -> str:
    for value in values:
        if value in (None, ""):
            continue
        suffix = Path(str(value)).suffix.lower().lstrip(".")
        if suffix:
            return suffix
    return ""


def _get_source_file_storage_root() -> Path:
    candidates = [
        Path("/app/upload"),
        SERVING_BASE_DIR.parent / "upload",
        SERVING_BASE_DIR / "upload",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate / "admin_knowledge_originals"
    return candidates[1] / "admin_knowledge_originals"


def _persist_original_source_file(batch_id: str, item_index: int, filename: str, saved_file_path: Path) -> Path:
    storage_root = _get_source_file_storage_root()
    target_dir = storage_root / batch_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{item_index:03d}_{Path(filename).name}"
    shutil.copy2(saved_file_path, target_path)
    return target_path


def _get_collection_folder_rule(collection_name: str, collection_meta: Dict[str, Any]) -> Dict[str, Any]:
    collection_type = collection_meta.get("type") or _infer_collection_type(collection_name)
    rule = dict(DEFAULT_FOLDER_RULE)
    rule.update(COLLECTION_FOLDER_RULES.get(collection_type, {}))
    rule["collection_name"] = collection_name
    rule["collection_type"] = collection_type
    return rule


def _get_ingest_router_module():
    from api.router.ingest import ingest_router as ingest_api

    return ingest_api


def _get_batch_redis():
    ingest_api = _get_ingest_router_module()
    return ingest_api._get_redis()


def _admin_batch_key(batch_id: str) -> str:
    return f"{ADMIN_BATCH_KEY_PREFIX}{batch_id}"


def _is_path_under(path: Path, root: Path) -> bool:
    resolved_path = path.resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def _delete_allowed_path(path: Path, allowed_roots: List[Path]) -> bool:
    resolved_path = path.resolve(strict=False)
    if not any(_is_path_under(resolved_path, root) for root in allowed_roots):
        raise HTTPException(status_code=500, detail=f"refuse to delete path outside upload roots: {path}")
    if not resolved_path.exists():
        return False
    if resolved_path.is_dir():
        shutil.rmtree(resolved_path)
        return True
    if resolved_path.is_file():
        resolved_path.unlink()
        return True
    raise HTTPException(status_code=500, detail=f"unsupported upload path type: {path}")


def _collect_batch_task_ids(batch_record: Dict[str, Any]) -> List[str]:
    task_ids: List[str] = []
    for item in batch_record["items"]:
        for task_id in list(item.get("task_history") or []) + [item.get("current_task_id")]:
            if task_id and task_id not in task_ids:
                task_ids.append(task_id)
    return task_ids


def _mark_upload_task_cancelled(task_id: str) -> bool:
    ingest_api = _get_ingest_router_module()
    redis_client = ingest_api._get_redis()
    key = ingest_api._task_key(task_id)
    status = redis_client.hget(key, "status")
    if status in ("done", "failed"):
        return False
    redis_client.hset(
        key,
        mapping={
            "cancel": "1",
            "status": "failed",
            "error": "任务被用户取消",
            "finished_at": datetime.now().isoformat(),
        },
    )
    redis_client.expire(key, TASK_CANCEL_TTL_SECONDS)
    return True


def _cleanup_upload_batch_files(batch_record: Dict[str, Any]) -> Dict[str, int]:
    allowed_roots = [
        ADMIN_UPLOAD_BATCH_DIR,
        _get_source_file_storage_root(),
        ADMIN_ORIGINAL_FILE_DIR,
    ]
    deleted_files = 0
    deleted_dirs = 0
    deleted_paths: List[str] = []

    for item in batch_record["items"]:
        raw_temp_path = item.get("file_path")
        if raw_temp_path:
            temp_path = Path(raw_temp_path)
            existed = temp_path.exists()
            if _delete_allowed_path(temp_path, allowed_roots) and existed:
                deleted_files += 1
                deleted_paths.append(str(temp_path))

        task_snapshot = _load_task_snapshot(item["current_task_id"])
        raw_original_path = item.get("metadata", {}).get("original_file_path")
        if raw_original_path and task_snapshot["status"] != "done":
            original_path = Path(raw_original_path)
            existed = original_path.exists()
            if _delete_allowed_path(original_path, allowed_roots) and existed:
                deleted_files += 1
                deleted_paths.append(str(original_path))

    temp_dir = ADMIN_UPLOAD_BATCH_DIR / batch_record["batch_id"]
    if _delete_allowed_path(temp_dir, allowed_roots):
        deleted_dirs += 1
        deleted_paths.append(str(temp_dir))

    for directory in (_get_source_file_storage_root() / batch_record["batch_id"], ADMIN_ORIGINAL_FILE_DIR / batch_record["batch_id"]):
        existed = directory.exists()
        if not existed:
            continue
        if not _is_path_under(directory, _get_source_file_storage_root()) and not _is_path_under(directory, ADMIN_ORIGINAL_FILE_DIR):
            raise HTTPException(status_code=500, detail=f"refuse to delete path outside original file roots: {directory}")
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
            deleted_dirs += 1
            deleted_paths.append(str(directory))

    return {
        "deleted_files": deleted_files,
        "deleted_dirs": deleted_dirs,
        "deleted_paths": len(deleted_paths),
    }


def _save_batch_record(batch_record: Dict[str, Any]) -> None:
    redis_client = _get_batch_redis()
    key = _admin_batch_key(batch_record["batch_id"])
    redis_client.hset(
        key,
        mapping={
            "batch_id": batch_record["batch_id"],
            "created_at": batch_record["created_at"],
            "created_by": batch_record["created_by"],
            "collection_name": batch_record.get("collection_name", ""),
            "collection_type": batch_record.get("collection_type", ""),
            "normalized_folder_path": json.dumps(batch_record.get("normalized_folder_path") or [], ensure_ascii=False),
            "normalized_metadata": json.dumps(batch_record.get("normalized_metadata") or {}, ensure_ascii=False),
            "permission_level": str(batch_record.get("permission_level") or ""),
            "permission_level_name": batch_record.get("permission_level_name") or "",
            "items": json.dumps(batch_record["items"], ensure_ascii=False),
        },
    )
    redis_client.expire(key, DEFAULT_UPLOAD_BATCH_TTL_SECONDS)


def _load_batch_record(batch_id: str) -> Dict[str, Any]:
    redis_client = _get_batch_redis()
    data = redis_client.hgetall(_admin_batch_key(batch_id))
    if not data:
        raise HTTPException(status_code=404, detail="upload batch does not exist")

    raw_items = data.get("items")
    if not raw_items:
        raise HTTPException(status_code=500, detail="upload batch items are missing")

    try:
        items = json.loads(raw_items)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=500, detail=f"invalid upload batch payload: {error}")

    try:
        normalized_folder_path = json.loads(data.get("normalized_folder_path") or "[]")
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=500, detail=f"invalid upload batch folder payload: {error}")

    try:
        normalized_metadata = json.loads(data.get("normalized_metadata") or "{}")
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=500, detail=f"invalid upload batch metadata payload: {error}")

    return {
        "batch_id": data.get("batch_id") or batch_id,
        "created_at": data.get("created_at") or "",
        "created_by": data.get("created_by") or "",
        "collection_name": data.get("collection_name") or "",
        "collection_type": data.get("collection_type") or "",
        "normalized_folder_path": normalized_folder_path,
        "normalized_metadata": normalized_metadata,
        "permission_level": int(data["permission_level"]) if data.get("permission_level") not in (None, "") else None,
        "permission_level_name": data.get("permission_level_name") or None,
        "items": items,
    }


def _save_admin_batch_file(batch_id: str, item_index: int, file: UploadFile) -> Path:
    if not file.filename:
        raise HTTPException(status_code=400, detail="file filename is required")

    target_dir = ADMIN_UPLOAD_BATCH_DIR / batch_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{item_index:03d}_{Path(file.filename).name}"
    with open(target_path, "wb") as output:
        shutil.copyfileobj(file.file, output)
    return target_path


def _parse_admin_metadata_json(metadata: Optional[str]) -> Dict[str, Any]:
    if metadata in (None, ""):
        return {}
    try:
        parsed = json.loads(metadata)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail=f"invalid metadata JSON: {error}")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="metadata must be a JSON object")
    return parsed


def _resolve_upload_route(
    collection_type: Optional[str],
    collection_name: Optional[str],
) -> Dict[str, str]:
    normalized_type = str(collection_type or "").strip()
    normalized_name = str(collection_name or "").strip()

    if normalized_name in KNOWLEDGE_ROUTE_COLLECTIONS or normalized_type in {"parameter", "parameter_updated"}:
        if not normalized_name:
            if normalized_type in {"parameter", "parameter_updated"}:
                normalized_name = "parameter_updated"
            else:
                raise HTTPException(status_code=400, detail="collection_name is required")
        normalized_type = normalized_type or normalized_name
        return {
            "route_kind": "knowledge",
            "collection_type": normalized_type,
            "target_collection_name": normalized_name,
            "validate_collection_name": normalized_name,
        }

    if not normalized_type:
        raise HTTPException(status_code=400, detail="collection_type is required")
    if normalized_type not in INGEST_COLLECTION_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported collection_type: {normalized_type}",
        )

    validate_collection_name = normalized_name
    if normalized_type == "manual":
        validate_collection_name = COLLECTION_MANUAL_CHUNKS
    elif normalized_type == "principle":
        validate_collection_name = COLLECTION_PRINCIPLE_CHUNKS
    elif normalized_type in {"workorder", "excellent_workorder"}:
        validate_collection_name = normalized_name or DEFAULT_WORKORDER_COLLECTION

    return {
        "route_kind": "ingest",
        "collection_type": normalized_type,
        "target_collection_name": normalized_name,
        "validate_collection_name": validate_collection_name,
    }


def _validate_upload_extension(route_info: Dict[str, str], filename: str) -> None:
    ext = Path(filename).suffix.lower()
    if route_info["route_kind"] == "knowledge":
        knowledge_type = route_info["collection_type"]
        if knowledge_type in {"parameter", "parameter_updated"}:
            knowledge_type = "parameter_updated"
        allowed = KNOWLEDGE_ROUTE_ALLOWED_EXTENSIONS.get(knowledge_type)
    else:
        allowed = _get_ingest_router_module().ALLOWED_EXTENSIONS.get(route_info["collection_type"])

    if not allowed:
        raise HTTPException(status_code=400, detail=f"unsupported upload target: {route_info}")
    if ext not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"{route_info['collection_type']} only supports {sorted(allowed)}, got {ext or '<empty>'}",
        )


def _build_upload_metadata(
    *,
    batch_id: str,
    admin_user: Dict[str, Any],
    raw_metadata: Dict[str, Any],
    folder_metadata: Dict[str, Any],
    permission_metadata: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    metadata = dict(raw_metadata)
    metadata.update(folder_metadata)
    if permission_metadata:
        metadata.update(permission_metadata)

    username = str(admin_user.get("username") or admin_user.get("full_name") or "admin")
    uploaded_at = datetime.utcnow().isoformat()
    metadata["batch_id"] = batch_id
    metadata["uploaded_by"] = username
    metadata["uploader"] = username
    metadata["uploaded_at"] = uploaded_at
    return metadata


def _validate_folder_assignment(
    *,
    session,
    validate_collection_name: str,
    folder_path: Optional[List[str]],
    category_level_1: Optional[str],
    category_level_2: Optional[str],
    category_level_3: Optional[str],
) -> Dict[str, Any]:
    collection_meta_map = _build_collection_meta_map(session)
    collection_meta = collection_meta_map.get(validate_collection_name)
    if not collection_meta:
        inferred_type = _infer_collection_type(validate_collection_name)
        if not inferred_type:
            raise HTTPException(status_code=404, detail=f"collection does not exist: {validate_collection_name}")
        collection_meta = {
            "name": validate_collection_name,
            "display_name": validate_collection_name,
            "type": inferred_type,
        }

    rule = _get_collection_folder_rule(validate_collection_name, collection_meta)
    try:
        normalized_folder_path = _normalize_folder_metadata_input(
            folder_path=folder_path,
            category_level_1=category_level_1,
            category_level_2=category_level_2,
            category_level_3=category_level_3,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    if not rule["supports_folder_assignment"] and normalized_folder_path:
        raise HTTPException(
            status_code=400,
            detail=f"{validate_collection_name} does not support folder metadata",
        )
    if len(normalized_folder_path) < rule["min_depth"]:
        raise HTTPException(
            status_code=400,
            detail=f"folder_path depth must be >= {rule['min_depth']}",
        )
    if len(normalized_folder_path) > rule["max_depth"]:
        raise HTTPException(
            status_code=400,
            detail=f"folder_path depth must be <= {rule['max_depth']}",
        )

    return {
        "rule": rule,
        "normalized_folder_path": normalized_folder_path,
        "normalized_metadata": _build_category_metadata(normalized_folder_path),
        "collection_meta": collection_meta,
    }


def _build_batch_item_record(
    *,
    item_id: str,
    filename: str,
    file_path: Path,
    task_id: str,
    route_info: Dict[str, str],
    options: Dict[str, Any],
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "item_id": item_id,
        "filename": filename,
        "file_path": str(file_path),
        "route_kind": route_info["route_kind"],
        "collection_type": route_info["collection_type"],
        "target_collection_name": route_info["target_collection_name"],
        "validate_collection_name": route_info["validate_collection_name"],
        "current_task_id": task_id,
        "task_history": [task_id],
        "metadata": copy.deepcopy(metadata),
        "options": copy.deepcopy(options),
    }


def _save_task_success(task_id: str, filename: str, *, message: str, result_payload: Dict[str, Any], total_steps: int = 5) -> None:
    ingest_api = _get_ingest_router_module()
    ingest_api._raise_if_task_cancelled(task_id)
    redis_client = ingest_api._get_redis()
    redis_client.hset(
        ingest_api._task_key(task_id),
        mapping={
            "status": "done",
            "current_step": total_steps,
            "total_steps": total_steps,
            "step_name": "完成",
            "progress_detail": message,
            "pdf_filename": filename,
            "finished_at": datetime.now().isoformat(),
            "elapsed_seconds": result_payload.get("elapsed_seconds", 0),
            "error": "",
            "result_payload": json.dumps(result_payload, ensure_ascii=False),
        },
    )
    redis_client.expire(ingest_api._task_key(task_id), 86400)


def _load_task_snapshot(task_id: str) -> Dict[str, Any]:
    ingest_api = _get_ingest_router_module()
    data = ingest_api._get_redis().hgetall(ingest_api._task_key(task_id))
    if not data:
        return {
            "task_id": task_id,
            "status": "unknown",
            "current_step": 0,
            "total_steps": 0,
            "step_name": "",
            "progress_detail": "",
            "error": "task record missing",
            "step_current": 0,
            "step_total": 0,
        }

    return {
        "task_id": task_id,
        "status": data.get("status", "unknown"),
        "current_step": int(data.get("current_step", 0) or 0),
        "total_steps": int(data.get("total_steps", 0) or 0),
        "step_name": data.get("step_name", ""),
        "progress_detail": data.get("progress_detail", ""),
        "error": data.get("error", ""),
        "step_current": int(data.get("step_current", 0) or 0),
        "step_total": int(data.get("step_total", 0) or 0),
        "started_at": data.get("started_at") or None,
        "finished_at": data.get("finished_at") or None,
        "pdf_filename": data.get("pdf_filename", ""),
    }


def _calculate_task_progress_percent(task_snapshot: Dict[str, Any]) -> int:
    status = task_snapshot["status"]
    if status == "done":
        return 100

    current_step = task_snapshot["current_step"]
    total_steps = task_snapshot["total_steps"]
    if total_steps <= 0:
        return 0

    step_current = task_snapshot.get("step_current", 0)
    step_total = task_snapshot.get("step_total", 0)
    if step_total > 0:
        completed_steps = max(0, current_step - 1)
        step_fraction = max(0, min(1, step_current / step_total))
        percent = int(((completed_steps + step_fraction) / total_steps) * 100)
        return max(0, min(99, percent))

    percent = int(current_step * 100 / total_steps)
    return max(0, min(99, percent))


def _resolve_task_step_name(task_snapshot: Dict[str, Any]) -> str:
    status = task_snapshot["status"]
    if status == "pending":
        return "等待开始"
    if status == "done":
        return "完成"
    if status == "failed" and not task_snapshot["step_name"]:
        return "失败"
    if status == "running" and (not task_snapshot["step_name"] or task_snapshot["current_step"] == 0):
        return "处理中"
    return task_snapshot["step_name"]


def _build_task_display_message(task_snapshot: Dict[str, Any]) -> str:
    status = task_snapshot["status"]
    if status == "failed":
        return task_snapshot["error"] or "任务失败"
    if status == "done":
        return task_snapshot["progress_detail"] or "入库完成"
    if status == "unknown":
        return task_snapshot["error"] or "任务状态未知"
    if task_snapshot["progress_detail"]:
        return task_snapshot["progress_detail"]
    return _resolve_task_step_name(task_snapshot) or TASK_STATUS_DISPLAY.get(status, "处理中")


def _build_batch_item_status_payload(item: Dict[str, Any], task_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    status = task_snapshot["status"]
    display_step_name = _resolve_task_step_name(task_snapshot)
    return {
        "item_id": item["item_id"],
        "filename": item["filename"],
        "task_id": item["current_task_id"],
        "document_ids": _get_batch_item_document_ids(item),
        "collection_type": item["collection_type"],
        "target_collection_name": item["target_collection_name"],
        "status": status,
        "display_status": TASK_STATUS_DISPLAY.get(status, "状态未知"),
        "progress_percent": _calculate_task_progress_percent(task_snapshot),
        "message": _build_task_display_message(task_snapshot),
        "display_step_name": display_step_name,
        "current_step": task_snapshot["current_step"],
        "total_steps": task_snapshot["total_steps"],
        "step_current": task_snapshot["step_current"],
        "step_total": task_snapshot["step_total"],
        "step_name": task_snapshot["step_name"],
        "progress_detail": task_snapshot["progress_detail"],
        "error": task_snapshot["error"],
        "task_history": item.get("task_history", []),
    }


def _load_task_result_payload(task_id: str) -> Optional[Dict[str, Any]]:
    ingest_api = _get_ingest_router_module()
    raw_payload = ingest_api._get_redis().hget(ingest_api._task_key(task_id), "result_payload")
    if not raw_payload:
        return None
    try:
        parsed_payload = json.loads(raw_payload)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=500, detail=f"invalid task result payload: {error}")
    if not isinstance(parsed_payload, dict):
        raise HTTPException(status_code=500, detail="invalid task result payload")
    return parsed_payload


def _extract_document_ids_from_result_payload(result_payload: Optional[Dict[str, Any]]) -> List[str]:
    if not result_payload:
        return []

    document_ids: List[str] = []
    for raw_id in result_payload.get("document_ids") or []:
        if raw_id not in (None, ""):
            document_ids.append(str(raw_id))

    for result in result_payload.get("results") or []:
        for raw_id in result.get("document_ids") or []:
            if raw_id not in (None, ""):
                document_ids.append(str(raw_id))

    return list(dict.fromkeys(document_ids))


def _get_batch_item_document_ids(item: Dict[str, Any]) -> List[str]:
    document_ids: List[str] = []
    task_ids = list(item.get("task_history") or [])
    if item.get("current_task_id") and item["current_task_id"] not in task_ids:
        task_ids.append(item["current_task_id"])

    for task_id in task_ids:
        document_ids.extend(_extract_document_ids_from_result_payload(_load_task_result_payload(task_id)))

    return list(dict.fromkeys(document_ids))


def _iter_batch_records() -> List[Dict[str, Any]]:
    redis_client = _get_batch_redis()
    batch_records: List[Dict[str, Any]] = []
    for batch_key in sorted(redis_client.keys(f"{ADMIN_BATCH_KEY_PREFIX}*")):
        batch_id = batch_key.removeprefix(ADMIN_BATCH_KEY_PREFIX)
        batch_records.append(_load_batch_record(batch_id))
    return batch_records


def _get_admin_role(admin_payload: Dict[str, Any]) -> str:
    if admin_payload.get("is_superuser"):
        return "admin"
    return str(parse_user_info(admin_payload).get("role") or "")


def _can_view_batch_record(admin_payload: Dict[str, Any], batch_record: Dict[str, Any]) -> bool:
    if _get_admin_role(admin_payload) == "admin":
        return True
    return str(batch_record.get("created_by") or "") == str(admin_payload.get("username") or "")


def _locate_retry_batch_item(
    *,
    document_id: Optional[str],
    task_id: Optional[str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if task_id:
        for batch_record in _iter_batch_records():
            for item in batch_record["items"]:
                if item["current_task_id"] == task_id:
                    return batch_record, item
        raise HTTPException(status_code=404, detail="retry task does not exist")

    if not document_id:
        raise HTTPException(status_code=400, detail="document_id or task_id is required")

    for batch_record in _iter_batch_records():
        for item in batch_record["items"]:
            if document_id in _get_batch_item_document_ids(item):
                return batch_record, item
    raise HTTPException(status_code=404, detail="retry target does not exist")


def _resolve_deleted_count(delete_result: Dict[str, Any]) -> int:
    counters = [
        delete_result.get("deleted_count"),
        delete_result.get("deleted_mongo"),
        delete_result.get("deleted_vectors"),
        delete_result.get("deleted_es"),
        delete_result.get("deleted_nodes"),
    ]
    normalized_counters = [int(value) for value in counters if isinstance(value, int)]
    return max(normalized_counters) if normalized_counters else 0


def _batch_milvus_collection_candidates(batch_record: Dict[str, Any]) -> List[str]:
    candidates: List[str] = []

    collection_type = str(batch_record.get("collection_type") or "").strip()
    if collection_type == "manual":
        candidates.extend([COLLECTION_MANUAL_TOTAL, COLLECTION_MANUAL_CHUNKS])
    elif collection_type == "principle":
        candidates.extend([COLLECTION_PRINCIPLE_TOTAL, COLLECTION_PRINCIPLE_CHUNKS])
    elif collection_type in {"workorder", "excellent_workorder"}:
        candidates.append(DEFAULT_WORKORDER_COLLECTION)

    collection_name = str(batch_record.get("collection_name") or "").strip()
    if collection_name in {COLLECTION_MANUAL_CHUNKS, COLLECTION_MANUAL_TOTAL, COLLECTION_MANUAL}:
        candidates.extend([COLLECTION_MANUAL_TOTAL, COLLECTION_MANUAL_CHUNKS])
    elif collection_name in {COLLECTION_PRINCIPLE_CHUNKS, COLLECTION_PRINCIPLE_TOTAL, COLLECTION_PRINCIPLE}:
        candidates.extend([COLLECTION_PRINCIPLE_TOTAL, COLLECTION_PRINCIPLE_CHUNKS])
    elif collection_name:
        candidates.append(collection_name)

    for item in batch_record.get("items") or []:
        for key in ("target_collection_name", "validate_collection_name", "collection_type"):
            value = str(item.get(key) or "").strip()
            if not value:
                continue
            if value == "manual":
                candidates.extend([COLLECTION_MANUAL_TOTAL, COLLECTION_MANUAL_CHUNKS])
            elif value == "principle":
                candidates.extend([COLLECTION_PRINCIPLE_TOTAL, COLLECTION_PRINCIPLE_CHUNKS])
            else:
                candidates.append(value)

    return list(dict.fromkeys(candidates))


def _query_milvus_batch_document_ids(collection_name: str, batch_id: str) -> List[str]:
    from database.vector.milvus_client import MilvusClient
    from pymilvus import Collection, utility

    client = MilvusClient()
    if not utility.has_collection(collection_name, using=client.alias):
        return []

    collection = Collection(collection_name, using=client.alias)
    collection.load()
    safe_batch_id = str(batch_id).replace("\\", "\\\\").replace("'", "\\'")
    rows = collection.query(
        expr=f"metadata['batch_id'] == '{safe_batch_id}'",
        output_fields=["id", "metadata"],
    )

    document_ids: List[str] = []
    for row in rows:
        metadata = row.get("metadata") or {}
        if metadata.get("is_deleted") is True:
            continue
        raw_id = row.get("id")
        if raw_id in (None, ""):
            logger.critical(
                "Skip invalid Milvus row in batch query: "
                f"batch_id={batch_id}, collection={collection_name}, row={row}"
            )
            continue
        document_ids.append(str(raw_id))

    return list(dict.fromkeys(document_ids))


def _soft_delete_batch_milvus_vectors(batch_record: Dict[str, Any]) -> Dict[str, Any]:
    from database.vector.milvus_client import MilvusClient

    batch_id = batch_record["batch_id"]
    client = MilvusClient()
    collection_results: Dict[str, Dict[str, int]] = {}
    deleted_vectors = 0
    total_vectors = 0
    remaining_vectors = 0

    for collection_name in _batch_milvus_collection_candidates(batch_record):
        collection_deleted = 0
        collection_total = 0
        collection_remaining = 0
        attempts_used = 0

        try:
            for attempt in range(1, 4):
                document_ids = _query_milvus_batch_document_ids(collection_name, batch_id)
                if not document_ids:
                    break

                attempts_used = attempt
                if collection_total == 0:
                    collection_total = len(document_ids)

                deleted_count = client.soft_delete_by_ids(collection_name, document_ids)
                collection_deleted += deleted_count
                collection_remaining = len(document_ids) - deleted_count
                if collection_remaining <= 0:
                    break

                logger.warning(
                    f"Retry batch Milvus cleanup: batch_id={batch_id}, "
                    f"collection={collection_name}, attempt={attempt}, "
                    f"expected={len(document_ids)}, actual={deleted_count}, "
                    f"remaining={collection_remaining}"
                )

            remaining_ids = _query_milvus_batch_document_ids(collection_name, batch_id)
            collection_remaining = len(remaining_ids)
            if collection_remaining:
                logger.critical(
                    "Batch Milvus cleanup still has residual vectors after retries: "
                    f"batch_id={batch_id}, collection={collection_name}, "
                    f"attempts={attempts_used}, remaining={collection_remaining}, "
                    f"residual_ids={remaining_ids[:20]}"
                )
        except Exception as error:
            logger.critical(
                "Batch Milvus cleanup failed but upload batch delete will continue: "
                f"batch_id={batch_id}, collection={collection_name}, "
                f"attempts={attempts_used}, error={error}",
                exc_info=True,
            )
            collection_remaining = -1

        if collection_total == 0 and collection_deleted == 0 and collection_remaining == 0:
            continue

        deleted_vectors += collection_deleted
        total_vectors += collection_total
        if collection_remaining > 0:
            remaining_vectors += collection_remaining
        collection_results[collection_name] = {
            "deleted_count": collection_deleted,
            "total_count": collection_total,
            "remaining_count": collection_remaining,
            "attempts": attempts_used,
        }

    return {
        "deleted_milvus_vectors": deleted_vectors,
        "total_milvus_vectors": total_vectors,
        "remaining_milvus_vectors": remaining_vectors,
        "milvus_collection_results": collection_results,
    }


def _soft_delete_batch_documents(batch_id: str) -> Dict[str, Any]:
    docs = list(DocumentModel.objects(__raw__={
        "metadata.batch_id": batch_id,
        "$or": [
            {"metadata.is_deleted": {"$exists": False}},
            {"metadata.is_deleted": {"$ne": True}},
        ],
    }).only("document_id", "collection_name"))

    grouped_document_ids: Dict[str, List[str]] = {}
    for doc in docs:
        if not doc.collection_name or not doc.document_id:
            raise HTTPException(status_code=500, detail=f"invalid document record for batch: {batch_id}")
        grouped_document_ids.setdefault(doc.collection_name, []).append(doc.document_id)

    deleted_count = 0
    total_count = 0
    collection_results: Dict[str, Dict[str, Any]] = {}
    for collection_name, raw_document_ids in grouped_document_ids.items():
        document_ids = list(dict.fromkeys(raw_document_ids))
        delete_result = knowledge_service.delete_documents(
            document_ids=document_ids,
            collection_name=collection_name,
        )
        collection_deleted_count = _resolve_deleted_count(delete_result)

        deleted_count += collection_deleted_count
        total_count += len(document_ids)
        collection_results[collection_name] = {
            "deleted_count": collection_deleted_count,
            "total_count": len(document_ids),
        }
        if collection_deleted_count < len(document_ids):
            logger.critical(
                "Batch document soft-delete incomplete, continuing to Milvus fallback: "
                f"batch_id={batch_id}, collection={collection_name}, "
                f"expected={len(document_ids)}, actual={collection_deleted_count}"
            )
            collection_results[collection_name]["incomplete"] = True

    return {
        "deleted_documents": deleted_count,
        "total_documents": total_count,
        "deleted_collections": len(collection_results),
        "collection_results": collection_results,
    }


def _select_active_batch_item(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for status in ("running", "pending", "failed", "unknown", "done"):
        for item in items:
            if item["status"] == status:
                return item
    return None


def _calculate_batch_progress_percent(batch_status: str, items: List[Dict[str, Any]]) -> int:
    if batch_status == "done":
        return 100
    if not items:
        return 0

    percent = int(sum(item["progress_percent"] for item in items) / len(items))
    return max(0, min(99, percent))


def _build_batch_display_message(
    batch_status: str,
    counters: Dict[str, int],
    total: int,
    active_item: Optional[Dict[str, Any]],
) -> str:
    if total <= 0:
        return "暂无上传任务"
    if batch_status == "done":
        return f"全部完成，共 {total} 个文件"
    if batch_status == "failed":
        return f"全部失败，共 {total} 个文件"
    if batch_status == "partial_failed":
        return f"{counters['done']} 个完成，{counters['failed']} 个失败"
    if batch_status == "running":
        completed = counters["done"] + counters["failed"]
        if active_item:
            return f"正在处理 {completed}/{total} 个文件：{active_item['filename']}"
        return f"正在处理 {completed}/{total} 个文件"
    return "任务状态未知"


def _build_batch_status_payload(batch_record: Dict[str, Any]) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    counters = {"pending": 0, "running": 0, "done": 0, "failed": 0, "unknown": 0}

    for item in batch_record["items"]:
        task_snapshot = _load_task_snapshot(item["current_task_id"])
        status = task_snapshot["status"]
        counters[status if status in counters else "unknown"] += 1
        items.append(_build_batch_item_status_payload(item, task_snapshot))

    total = len(items)
    if counters["failed"] and counters["failed"] == total:
        batch_status = "failed"
    elif counters["done"] == total and total > 0:
        batch_status = "done"
    elif counters["running"] or counters["pending"]:
        batch_status = "running"
    elif counters["failed"]:
        batch_status = "partial_failed"
    else:
        batch_status = "unknown"

    active_item = _select_active_batch_item(items)

    running_items = [it for it in items if it.get("status") == "running"]
    if running_items:
        first = running_items[0]
        _sc = first.get("step_current")
        _st = first.get("step_total")
        active_step_current = int(_sc) if _sc not in (None, "") else None
        active_step_total = int(_st) if _st not in (None, "") else None
    else:
        active_step_current = None
        active_step_total = None

    return {
        "batch_id": batch_record["batch_id"],
        "status": batch_status,
        "display_status": BATCH_STATUS_DISPLAY.get(batch_status, "状态未知"),
        "progress_percent": _calculate_batch_progress_percent(batch_status, items),
        "message": _build_batch_display_message(batch_status, counters, total, active_item),
        "active_step_name": active_item["display_step_name"] if active_item else "",
        "active_progress_detail": active_item["progress_detail"] if active_item else "",
        "active_step_current": active_step_current,
        "active_step_total": active_step_total,
        "collection_name": batch_record.get("collection_name"),
        "collection_type": batch_record.get("collection_type"),
        "normalized_folder_path": batch_record.get("normalized_folder_path") or [],
        "normalized_metadata": batch_record.get("normalized_metadata") or {},
        "permission_level": batch_record.get("permission_level"),
        "permission_level_name": batch_record.get("permission_level_name"),
        "created_at": batch_record.get("created_at"),
        "created_by": batch_record.get("created_by"),
        "total": total,
        "pending": counters["pending"],
        "running": counters["running"],
        "done": counters["done"],
        "failed": counters["failed"],
        "unknown": counters["unknown"],
        "task_ids": [item["current_task_id"] for item in batch_record["items"]],
        "items": items,
    }


def _cleanup_cancelled_task_outputs(
    *,
    task_id: str,
    collection_type: str,
    collection_name: str,
    metadata: Dict[str, Any],
) -> None:
    ingest_api = _get_ingest_router_module()
    if not ingest_api._is_task_cancelled(task_id):
        return

    batch_id = str((metadata or {}).get("batch_id") or "").strip()
    if not batch_id:
        logger.critical(
            "Cancelled upload task has no batch_id, cannot cleanup task outputs: "
            f"task_id={task_id}, collection_type={collection_type}, collection_name={collection_name}"
        )
        return

    logger.warning(
        "Cleaning outputs written by cancelled upload task: "
        f"task_id={task_id}, batch_id={batch_id}, "
        f"collection_type={collection_type}, collection_name={collection_name}"
    )
    try:
        document_result = _soft_delete_batch_documents(batch_id)
        milvus_result = _soft_delete_batch_milvus_vectors(
            {
                "batch_id": batch_id,
                "collection_type": collection_type,
                "collection_name": collection_name,
                "items": [],
            }
        )
        remaining = int(milvus_result.get("remaining_milvus_vectors") or 0)
        log_message = (
            "Cancelled upload task cleanup finished: "
            f"task_id={task_id}, batch_id={batch_id}, "
            f"document_result={document_result}, milvus_result={milvus_result}"
        )
        if remaining > 0:
            logger.critical(log_message)
        else:
            logger.warning(log_message)
    except Exception as cleanup_error:
        logger.critical(
            "Cancelled upload task cleanup failed: "
            f"task_id={task_id}, batch_id={batch_id}, error={cleanup_error}",
            exc_info=True,
        )


def _run_admin_pdf_ingest_task(
    *,
    task_id: str,
    file_path: str,
    collection_type: str,
    metadata: Dict[str, Any],
    options: Dict[str, Any],
) -> None:
    ingest_api = _get_ingest_router_module()
    start_time = datetime.now()
    ingest_api._get_redis().hset(ingest_api._task_key(task_id), mapping={"status": "running"})
    try:
        ingest_api._raise_if_task_cancelled(task_id)
        ingest_api._ensure_ingestion_path()
        from manual_ingest_agent import ingest_manual

        result = ingest_manual(
            pdf_path=file_path,
            model_name=options.get("model"),
            window=options.get("window", 2),
            overlap=options.get("overlap", 1),
            drop_collections=options.get("drop_collections", False),
            chapter_collection=COLLECTION_PRINCIPLE_TOTAL if collection_type == "principle" else COLLECTION_MANUAL_TOTAL,
            chunks_collection=COLLECTION_PRINCIPLE_CHUNKS if collection_type == "principle" else COLLECTION_MANUAL_CHUNKS,
            progress_callback=ingest_api._make_progress_callback(task_id, start_time),
            series=options.get("series"),
            generation=options.get("generation"),
            controller=options.get("controller"),
            product_line=options.get("product_line"),
            tonnage=options.get("tonnage"),
            **metadata,
        )
        ingest_api._finish_task(task_id, start_time, result, total_steps=9)
    except Exception as error:
        _cleanup_cancelled_task_outputs(
            task_id=task_id,
            collection_type=collection_type,
            collection_name="",
            metadata=metadata,
        )
        ingest_api._fail_task(task_id, start_time, error)


def _run_admin_word_ingest_task(
    *,
    task_id: str,
    file_path: str,
    collection_type: str,
    metadata: Dict[str, Any],
    options: Dict[str, Any],
) -> None:
    ingest_api = _get_ingest_router_module()
    start_time = datetime.now()
    ingest_api._get_redis().hset(ingest_api._task_key(task_id), mapping={"status": "running"})
    try:
        ingest_api._raise_if_task_cancelled(task_id)
        ingest_api._ensure_ingestion_path()
        from word_ingest_agent import ingest_word

        result = ingest_word(
            docx_path=file_path,
            model_name=options.get("model"),
            window=options.get("window", 2),
            overlap=options.get("overlap", 1),
            drop_collections=options.get("drop_collections", False),
            chapter_collection=COLLECTION_PRINCIPLE_TOTAL if collection_type == "principle" else COLLECTION_MANUAL_TOTAL,
            chunks_collection=COLLECTION_PRINCIPLE_CHUNKS if collection_type == "principle" else COLLECTION_MANUAL_CHUNKS,
            progress_callback=ingest_api._make_progress_callback(task_id, start_time),
            series=options.get("series"),
            generation=options.get("generation"),
            controller=options.get("controller"),
            product_line=options.get("product_line"),
            tonnage=options.get("tonnage"),
            **metadata,
        )
        total_steps = 9 if result.get("has_toc") else 5
        ingest_api._finish_task(task_id, start_time, result, total_steps=total_steps)
    except Exception as error:
        _cleanup_cancelled_task_outputs(
            task_id=task_id,
            collection_type=collection_type,
            collection_name="",
            metadata=metadata,
        )
        ingest_api._fail_task(task_id, start_time, error)


def _run_admin_excel_ingest_task(
    *,
    task_id: str,
    file_path: str,
    filename: str,
    collection_type: str,
    collection_name: str,
    metadata: Dict[str, Any],
    options: Dict[str, Any],
) -> None:
    ingest_api = _get_ingest_router_module()
    start_time = datetime.now()
    ingest_api._get_redis().hset(ingest_api._task_key(task_id), mapping={"status": "running"})
    try:
        ingest_api._raise_if_task_cancelled(task_id)
        ingest_api._ensure_ingestion_path()
        from excel_ingest_agent import ingest_excel

        raw_content_columns = options.get("content_columns") or ingest_api.DEFAULT_WORKORDER_CONTENT_COLUMNS
        columns = [column.strip() for column in str(raw_content_columns).split(",") if column.strip()]
        if not columns:
            raise ValueError("content_columns 不能为空")

        # V2 新增：从 options 取出 file_id（None 时 ingest_excel 走老路径）
        v2_file_id = (options or {}).get("v2_file_id")

        result = ingest_excel(
            file_path=file_path,
            content_columns=columns,
            collection_name=collection_name,
            knowledge_type=collection_type,
            model=options.get("model") or "",
            progress_callback=ingest_api._make_progress_callback(task_id, start_time),
            file_id=v2_file_id,
            series=options.get("series"),
            generation=options.get("generation"),
            controller=options.get("controller"),
            product_line=options.get("product_line"),
            tonnage=options.get("tonnage"),
            **metadata,
        )
        if not result.get("success"):
            raise ValueError(result.get("msg") or "Excel 入库失败")

        _save_task_success(
            task_id,
            filename,
            message=f"{result.get('success_rows', 0)} 条",
            result_payload=result,
        )
    except Exception as error:
        _cleanup_cancelled_task_outputs(
            task_id=task_id,
            collection_type=collection_type,
            collection_name=collection_name,
            metadata=metadata,
        )
        ingest_api._fail_task(task_id, start_time, error)


def _run_admin_knowledge_route_task(
    *,
    task_id: str,
    file_path: str,
    filename: str,
    collection_name: str,
    collection_type: str,
    metadata: Dict[str, Any],
) -> None:
    ingest_api = _get_ingest_router_module()
    start_time = datetime.now()
    ingest_api._get_redis().hset(ingest_api._task_key(task_id), mapping={"status": "running"})
    progress_callback = ingest_api._make_progress_callback(task_id, start_time)
    try:
        ingest_api._raise_if_task_cancelled(task_id)
        if collection_type in {"parameter", "parameter_updated"}:
            progress_callback(step=1, name="读取参数文件", detail=filename)
            progress_callback(step=2, name="解析参数", detail=collection_name)
            progress_callback(step=3, name="写入知识库", detail=collection_name)
        else:
            progress_callback(step=1, name="读取文件", detail=filename)
            progress_callback(step=2, name="写入知识库", detail=collection_name)
        with open(file_path, "rb") as file_stream:
            results = knowledge_service.upload_knowledge_files_and_parse(
                files=[file_stream],
                filenames=[filename],
                collection_name=collection_name,
                collection_type=collection_type,
                metadata=metadata,
            )
        success_count = sum(1 for item in results if item.get("success"))
        if success_count != len(results):
            failed_messages = [
                item.get("result", {}).get("msg") or item.get("filename")
                for item in results
                if not item.get("success")
            ]
            raise ValueError("; ".join(str(msg) for msg in failed_messages if msg))

        document_ids: List[str] = []
        for item in results:
            document_ids.extend(item.get("document_ids") or [])

        _save_task_success(
            task_id,
            filename,
            message=f"{success_count} 个文件",
            result_payload={
                "success": True,
                "success_count": success_count,
                "document_ids": document_ids,
                "results": results,
            },
        )
    except Exception as error:
        _cleanup_cancelled_task_outputs(
            task_id=task_id,
            collection_type=collection_type,
            collection_name=collection_name,
            metadata=metadata,
        )
        ingest_api._fail_task(task_id, start_time, error)


def _resolve_upload_task_total_steps(route_info: Dict[str, str]) -> int:
    if route_info["collection_type"] in ASYNC_GRAPHRAG_COLLECTION_TYPES:
        return 9
    if route_info["collection_type"] in {"workorder", "excellent_workorder"}:
        return 5
    if route_info["route_kind"] == "knowledge":
        return 5
    return 5


def _create_kb_file_record(
    *,
    filename: str,
    file_path: Path,
    collection_type: str,
    collection_name: str,
    metadata: Dict[str, Any],
    task_id: str,
) -> Optional[str]:
    """V2: 在 kb_files 表 INSERT 一行，返回新生成的 file_id。

    失败时（MySQL 不可用、字段冲突等）：
      - logger.warning 记录详细错误
      - 返回 None → 调用方走老路径（不传 file_id 给 ingest_excel）
      - 不抛出异常，不影响上传主流程

    决策依据：V2 是增量层，MySQL 暂时挂掉时业务仍要能跑。
    """
    try:
        try:
            file_size = file_path.stat().st_size
        except Exception:
            file_size = None

        file_type = file_path.suffix.lstrip(".").lower() or "unknown"

        permission_level = metadata.get("permission_level")
        if permission_level is not None:
            try:
                permission_level = int(permission_level)
            except (TypeError, ValueError):
                permission_level = None

        sub_category = (
            metadata.get("selected_category")
            or metadata.get("sub_category")
        )
        uploaded_by = metadata.get("uploaded_by") or metadata.get("uploader")
        batch_id = metadata.get("batch_id")

        EXCLUDE_KEYS = {
            "batch_id", "uploaded_by", "uploader", "uploaded_at",
            "permission_level", "selected_category", "sub_category",
            "folder_path", "category_level_1", "category_level_2",
            "category_level_3", "node_id",
        }
        business_metadata = {
            k: v for k, v in metadata.items()
            if k not in EXCLUDE_KEYS
        }

        factory = RepositoryFactory.get_instance()
        session = factory.db_manager.get_session()
        try:
            kb_file = KbFile(
                filename=filename,
                collection_type=collection_type,
                collection_name=collection_name,
                file_type=file_type,
                file_size_bytes=file_size,
                permission_level=permission_level if permission_level is not None else 1,
                sub_category=sub_category,
                metadata_json=business_metadata if business_metadata else None,
                status="pending",
                total_records=0,
                success_records=0,
                failed_records=0,
                uploaded_by=uploaded_by,
                batch_id=batch_id,
                task_id=task_id,
                source_file_path=str(file_path),
            )
            session.add(kb_file)
            session.commit()
            session.refresh(kb_file)
            file_id = kb_file.id
            logger.info(
                f"[V2] kb_files INSERT OK: file_id={file_id}, "
                f"filename={filename}, batch_id={batch_id}, task_id={task_id}"
            )
            return file_id
        finally:
            session.close()
    except Exception as exc:
        logger.warning(
            f"[V2] kb_files INSERT 失败 (filename={filename}, task_id={task_id}): "
            f"{type(exc).__name__}: {exc}. 走老路径（不写 kb_files）。"
        )
        return None


def _submit_upload_batch_item(
    *,
    background_tasks: BackgroundTasks,
    task_id: str,
    filename: str,
    file_path: Path,
    route_info: Dict[str, str],
    metadata: Dict[str, Any],
    options: Dict[str, Any],
) -> None:
    ingest_api = _get_ingest_router_module()
    ext = file_path.suffix.lower()
    total_steps = _resolve_upload_task_total_steps(route_info)
    ingest_api._init_redis_task(task_id, filename, options.get("model") or "", total_steps=total_steps)

    if route_info["route_kind"] == "knowledge":
        background_tasks.add_task(
            _run_admin_knowledge_route_task,
            task_id=task_id,
            file_path=str(file_path),
            filename=filename,
            collection_name=route_info["target_collection_name"],
            collection_type=route_info["collection_type"],
            metadata=metadata,
        )
        return

    if route_info["collection_type"] in ASYNC_GRAPHRAG_COLLECTION_TYPES:
        if ext == ".pdf":
            background_tasks.add_task(
                _run_admin_pdf_ingest_task,
                task_id=task_id,
                file_path=str(file_path),
                collection_type=route_info["collection_type"],
                metadata=metadata,
                options=options,
            )
            return

        background_tasks.add_task(
            _run_admin_word_ingest_task,
            task_id=task_id,
            file_path=str(file_path),
            collection_type=route_info["collection_type"],
            metadata=metadata,
            options=options,
        )
        return

    if route_info["collection_type"] in {"workorder", "excellent_workorder"}:
        # V2 新增：在派发任务前 INSERT kb_files
        v2_file_id = _create_kb_file_record(
            filename=filename,
            file_path=file_path,
            collection_type=route_info["collection_type"],
            collection_name=route_info["target_collection_name"] or DEFAULT_WORKORDER_COLLECTION,
            metadata=metadata,
            task_id=task_id,
        )
        # 浅拷贝 options，把 file_id 写进去（None 也写，task 函数判断）
        options = dict(options) if options else {}
        options["v2_file_id"] = v2_file_id

        background_tasks.add_task(
            _run_admin_excel_ingest_task,
            task_id=task_id,
            file_path=str(file_path),
            filename=filename,
            collection_type=route_info["collection_type"],
            collection_name=route_info["target_collection_name"] or DEFAULT_WORKORDER_COLLECTION,
            metadata=metadata,
            options=options,
        )
        return

    raise HTTPException(status_code=400, detail=f"unsupported batch upload route: {route_info}")


def _build_tree_node_id(collection_name: str, folder_path: List[str]) -> str:
    payload = json.dumps(
        {"collection_name": collection_name, "folder_path": folder_path},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_tree_node_id(node_id: str) -> Tuple[str, List[str]]:
    padded_node_id = node_id + "=" * (-len(node_id) % 4)
    try:
        payload = base64.urlsafe_b64decode(padded_node_id.encode("ascii")).decode("utf-8")
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="invalid node_id")

    collection_name = str(data.get("collection_name") or "").strip()
    if not collection_name:
        raise HTTPException(status_code=400, detail="invalid node_id")
    try:
        folder_path = _normalize_folder_path_items(data.get("folder_path") or [])
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return collection_name, folder_path


def _matches_keyword(doc: DocumentModel, keyword: str) -> bool:
    if not keyword:
        return True

    metadata = dict(doc.metadata or {})
    raw_text = " ".join(
        [
            str(doc.document_id or ""),
            str(doc.collection_name or ""),
            str(metadata.get("source_file") or ""),
            str(metadata.get("file_path") or ""),
            str(doc.content or "")[:500],
        ]
    ).lower()
    return keyword.lower() in raw_text


def _matches_metadata_filter(
    doc: DocumentModel,
    metadata_key: Optional[str],
    metadata_value: Optional[str],
) -> bool:
    if not metadata_key:
        return True

    metadata = dict(doc.metadata or {})
    raw_value = metadata.get(metadata_key)
    if raw_value is None:
        return False
    if metadata_value in (None, ""):
        return True

    if isinstance(raw_value, list):
        return metadata_value.lower() in [str(item).lower() for item in raw_value]
    return metadata_value.lower() in str(raw_value).lower()


def _matches_folder_path(doc: DocumentModel, folder_path: List[str]) -> bool:
    if not folder_path:
        return True

    doc_folder_path = _extract_folder_path(dict(doc.metadata or {})) or []
    if len(doc_folder_path) < len(folder_path):
        return False
    return doc_folder_path[: len(folder_path)] == folder_path


def _matches_category(doc: DocumentModel, category: Optional[str]) -> bool:
    if not category:
        return True

    lowered_category = category.lower()
    metadata = dict(doc.metadata or {})
    folder_path = _extract_folder_path(metadata) or []
    if any(str(item).lower() == lowered_category for item in folder_path):
        return True

    for key in ("category_level_1", "category_level_2", "category_level_3"):
        value = metadata.get(key)
        if value is not None and str(value).lower() == lowered_category:
            return True
    return False


def _resolve_folder_filter(
    *,
    collection_name: Optional[str],
    folder_path: Optional[str],
    node_id: Optional[str],
) -> Tuple[Optional[str], List[str]]:
    resolved_collection_name = collection_name
    resolved_folder_path = _normalize_folder_path_query(folder_path)

    if not node_id:
        return resolved_collection_name, resolved_folder_path

    node_collection_name, node_folder_path = _decode_tree_node_id(node_id)
    if resolved_collection_name and resolved_collection_name != node_collection_name:
        raise HTTPException(status_code=400, detail="collection_name conflicts with node_id")
    if resolved_folder_path and resolved_folder_path != node_folder_path:
        raise HTTPException(status_code=400, detail="folder_path conflicts with node_id")
    return node_collection_name, node_folder_path


def _resolve_upload_folder_selection(
    *,
    validate_collection_name: str,
    folder_path: Optional[List[str]],
    category_level_1: Optional[str],
    category_level_2: Optional[str],
    category_level_3: Optional[str],
    node_id: Optional[str],
    selected_category: Optional[str],
) -> Tuple[str, Optional[List[str]], Optional[str], Optional[str], Optional[str]]:
    resolved_folder_path = folder_path
    resolved_category_level_1 = category_level_1
    resolved_category_level_2 = category_level_2
    resolved_category_level_3 = category_level_3

    if selected_category and any([folder_path, category_level_1, category_level_2, category_level_3, node_id]):
        raise HTTPException(status_code=400, detail="selected_category conflicts with explicit folder metadata")

    if selected_category:
        resolved_category_level_1 = selected_category

    if not node_id:
        return (
            validate_collection_name,
            resolved_folder_path,
            resolved_category_level_1,
            resolved_category_level_2,
            resolved_category_level_3,
        )

    node_collection_name, node_folder_path = _decode_tree_node_id(node_id)
    compatible_node_collections = {
        COLLECTION_MANUAL_CHUNKS: {COLLECTION_MANUAL_CHUNKS, COLLECTION_MANUAL_TOTAL, COLLECTION_MANUAL},
        COLLECTION_PRINCIPLE_CHUNKS: {COLLECTION_PRINCIPLE_CHUNKS, COLLECTION_PRINCIPLE_TOTAL},
    }
    allowed_node_collections = compatible_node_collections.get(validate_collection_name, {validate_collection_name})
    if node_collection_name not in allowed_node_collections:
        raise HTTPException(status_code=400, detail="node_id conflicts with upload target collection")

    explicit_folder_path = _normalize_folder_metadata_input(
        folder_path=resolved_folder_path,
        category_level_1=resolved_category_level_1,
        category_level_2=resolved_category_level_2,
        category_level_3=resolved_category_level_3,
    )
    if explicit_folder_path and explicit_folder_path != node_folder_path:
        raise HTTPException(status_code=400, detail="node_id conflicts with explicit folder metadata")

    return (
        validate_collection_name,
        node_folder_path,
        None,
        None,
        None,
    )


def _build_file_record(
    doc: DocumentModel,
    collection_meta_map: Dict[str, Dict[str, Any]],
    *,
    include_full_metadata: bool = False,
) -> Dict[str, Any]:
    metadata = dict(doc.metadata or {})
    collection_meta = collection_meta_map.get(doc.collection_name, {})
    raw_file_path = _resolve_relative_file_path(metadata)
    abs_file_path = _resolve_absolute_file_path(metadata)
    source_file = metadata.get("source_file") or raw_file_path or doc.document_id
    filename = Path(str(source_file)).name if source_file else doc.document_id
    content_str = str(doc.content or "")
    folder_path = _extract_folder_path(metadata)
    permission_level = metadata.get("permission_level")
    permission_level_name = metadata.get("permission_level_name")
    is_deleted = bool(metadata.get("is_deleted", False))

    record = {
        "document_id": doc.document_id,
        "collection_name": doc.collection_name,
        "collection_display_name": collection_meta.get("display_name") or doc.collection_name,
        "collection_type": collection_meta.get("type") or _infer_collection_type(doc.collection_name),
        "filename": filename,
        "source_file": metadata.get("source_file"),
        "file_path": raw_file_path,
        "file_exists": bool(abs_file_path and abs_file_path.exists()),
        "file_size": _extract_file_size(metadata),
        "entity_type": metadata.get("entity_type"),
        "status": "archived" if is_deleted else "active",
        "is_deleted": is_deleted,
        "permission_level": permission_level,
        "permission_level_name": permission_level_name,
        "uploaded_at": _extract_uploaded_at(metadata),
        "uploader": _extract_uploader(metadata),
        "folder_path": folder_path,
        "folder_path_label": "/".join(folder_path) if folder_path else None,
        "content_preview": content_str[:100] + ("..." if len(content_str) > 100 else ""),
        "download_url": _build_download_url(doc.document_id, doc.collection_name),
        "preview_available": bool(
            content_str or metadata.get("steps") or (abs_file_path and abs_file_path.exists())
        ),
        "metadata": metadata if include_full_metadata else _summarize_metadata(metadata),
    }

    if include_full_metadata:
        record["content_excerpt"] = content_str[:2000] + ("..." if len(content_str) > 2000 else "")

    return record


def _query_knowledge_files(
    *,
    session,
    collection_name: Optional[str],
    keyword: Optional[str],
    category: Optional[str],
    permission_level: Optional[int],
    entity_type: Optional[str],
    metadata_key: Optional[str],
    metadata_value: Optional[str],
    folder_path: List[str],
    page: int,
    page_size: int,
    status: str,
) -> Dict[str, Any]:
    query: Dict[str, Any] = {}
    if collection_name:
        query["collection_name"] = collection_name
    if permission_level is not None:
        query["metadata__permission_level"] = permission_level
    if entity_type:
        query["metadata__entity_type"] = entity_type
    if status == "active":
        query["metadata__is_deleted__ne"] = True
    elif status == "archived":
        query["metadata__is_deleted"] = True

    docs = DocumentModel.objects(**query).order_by("-id")
    collection_meta_map = _build_collection_meta_map(session)

    filtered_docs = [
        doc
        for doc in docs
        if _matches_keyword(doc, keyword or "")
        and _matches_category(doc, category)
        and _matches_metadata_filter(doc, metadata_key, metadata_value)
        and _matches_folder_path(doc, folder_path)
    ]

    total = len(filtered_docs)
    start = (page - 1) * page_size
    paged_docs = filtered_docs[start : start + page_size]
    files = [_build_file_record(doc, collection_meta_map) for doc in paged_docs]

    return {
        "files": files,
        "total": total,
        "page": page,
        "page_size": page_size,
        "collection_name": collection_name,
        "keyword": keyword,
        "category": category,
        "status": status,
        "folder_path": folder_path or None,
        "folder_path_label": "/".join(folder_path) if folder_path else None,
    }


def _get_document_or_404(document_id: str, collection_name: str) -> DocumentModel:
    doc = DocumentModel.objects(document_id=document_id, collection_name=collection_name).first()
    if not doc:
        raise HTTPException(status_code=404, detail="file does not exist")
    return doc


def _build_preview_payload(doc: DocumentModel) -> Dict[str, Any]:
    metadata = dict(doc.metadata or {})
    collection_name = doc.collection_name
    filename = Path(str(metadata.get("source_file") or metadata.get("file_path") or doc.document_id)).name
    raw_content = str(doc.content or "")
    abs_file_path = _resolve_absolute_file_path(metadata)

    if metadata.get("steps"):
        return {
            "document_id": doc.document_id,
            "collection_name": collection_name,
            "filename": filename,
            "preview_type": "workorder",
            "title": metadata.get("title"),
            "notes": metadata.get("notes"),
            "steps": metadata.get("steps") or [],
            "image_ids": metadata.get("image_ids") or [],
            "download_url": _build_download_url(doc.document_id, collection_name),
        }

    if abs_file_path and abs_file_path.exists() and abs_file_path.suffix.lower() == ".json":
        try:
            json_content = json.loads(abs_file_path.read_text(encoding="utf-8-sig"))
            return {
                "document_id": doc.document_id,
                "collection_name": collection_name,
                "filename": filename,
                "preview_type": "json",
                "content": json_content,
                "download_url": _build_download_url(doc.document_id, collection_name),
            }
        except (UnicodeDecodeError, json.JSONDecodeError, OSError):
            pass

    if abs_file_path and abs_file_path.exists():
        file_text = file_reader.read_file(str(abs_file_path))
        if file_text:
            truncated = len(file_text) > PREVIEW_TEXT_LIMIT
            return {
                "document_id": doc.document_id,
                "collection_name": collection_name,
                "filename": filename,
                "preview_type": "text",
                "content": file_text[:PREVIEW_TEXT_LIMIT],
                "truncated": truncated,
                "download_url": _build_download_url(doc.document_id, collection_name),
            }

    if raw_content:
        truncated = len(raw_content) > PREVIEW_TEXT_LIMIT
        return {
            "document_id": doc.document_id,
            "collection_name": collection_name,
            "filename": filename,
            "preview_type": "text",
            "content": raw_content[:PREVIEW_TEXT_LIMIT],
            "truncated": truncated,
            "download_url": _build_download_url(doc.document_id, collection_name),
        }

    return {
        "document_id": doc.document_id,
        "collection_name": collection_name,
        "filename": filename,
        "preview_type": "download",
        "content": None,
        "download_url": _build_download_url(doc.document_id, collection_name),
    }


def _build_tree_query(collection_name: str, folder_path: List[str]) -> Dict[str, Any]:
    node_id = _build_tree_node_id(collection_name, folder_path)
    return {
        "collection_name": collection_name,
        "node_id": node_id,
        "folder_path": folder_path,
        "folder_path_label": "/".join(folder_path) if folder_path else None,
        "category": folder_path[-1] if folder_path else None,
    }


def _build_tree_node(
    *,
    collection_name: str,
    collection_display_name: str,
    collection_type: str,
    label: str,
    node_type: str,
    folder_path: List[str],
    depth: int,
) -> Dict[str, Any]:
    node_id = _build_tree_node_id(collection_name, folder_path)
    return {
        "node_id": node_id,
        "node_type": node_type,
        "label": label,
        "collection_name": collection_name,
        "collection_display_name": collection_display_name,
        "collection_type": collection_type,
        "folder_path": folder_path,
        "path": folder_path,
        "depth": depth,
        "document_count": 0,
        "direct_document_count": 0,
        "has_children": False,
        "children": [],
        "query": _build_tree_query(collection_name, folder_path),
        "upload_payload": {
            "collection_name": collection_name,
            "node_id": node_id,
            "folder_path": folder_path,
        },
    }


def _sort_tree_nodes(nodes: List[Dict[str, Any]]) -> None:
    nodes.sort(key=lambda item: (item["depth"], item["label"]))
    for node in nodes:
        if node["children"]:
            _sort_tree_nodes(node["children"])
            node["has_children"] = True


def _build_tree_option(node: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "label": node["label"],
        "value": node["node_id"],
        "node_id": node["node_id"],
        "node_type": node["node_type"],
        "collection_name": node["collection_name"],
        "collection_display_name": node["collection_display_name"],
        "collection_type": node["collection_type"],
        "folder_path": node["folder_path"],
        "path": node["path"],
        "query": node["query"],
        "upload_payload": node["upload_payload"],
        "children": [_build_tree_option(child) for child in node["children"]],
    }


def _build_knowledge_tree(
    *,
    session,
    collection_name: Optional[str] = None,
) -> Dict[str, Any]:
    collection_meta_map = _build_collection_meta_map(session)

    if collection_name:
        if collection_name not in collection_meta_map:
            raise HTTPException(status_code=404, detail="collection does not exist")
        collection_names = [collection_name]
    else:
        collection_names = list(collection_meta_map.keys())

    tree_nodes: List[Dict[str, Any]] = []
    rules: List[Dict[str, Any]] = []

    for name in collection_names:
        collection_meta = collection_meta_map[name]
        display_name = collection_meta.get("display_name") or name
        collection_type = collection_meta.get("type") or _infer_collection_type(name)
        rules.append(_get_collection_folder_rule(name, collection_meta))

        root_node = _build_tree_node(
            collection_name=name,
            collection_display_name=display_name,
            collection_type=collection_type,
            label=display_name,
            node_type="collection",
            folder_path=[],
            depth=0,
        )

        docs = DocumentModel.objects(collection_name=name, metadata__is_deleted__ne=True).only("metadata")
        path_node_map: Dict[Tuple[str, ...], Dict[str, Any]] = {}
        for doc in docs:
            root_node["document_count"] += 1
            doc_folder_path = _extract_folder_path(dict(doc.metadata or {})) or []
            if not doc_folder_path:
                root_node["direct_document_count"] += 1
                continue

            parent_node = root_node
            for depth, item in enumerate(doc_folder_path, start=1):
                path_key = tuple(doc_folder_path[:depth])
                current_node = path_node_map.get(path_key)
                if current_node is None:
                    current_node = _build_tree_node(
                        collection_name=name,
                        collection_display_name=display_name,
                        collection_type=collection_type,
                        label=item,
                        node_type="folder",
                        folder_path=list(path_key),
                        depth=depth,
                    )
                    parent_node["children"].append(current_node)
                    parent_node["has_children"] = True
                    path_node_map[path_key] = current_node
                current_node["document_count"] += 1
                parent_node = current_node
            parent_node["direct_document_count"] += 1

        _sort_tree_nodes(root_node["children"])
        tree_nodes.append(root_node)

    _sort_tree_nodes(tree_nodes)
    return {
        "tree": tree_nodes,
        "tree_options": [_build_tree_option(node) for node in tree_nodes],
        "rules": rules,
        "collection_count": len(tree_nodes),
    }


@router.get("/collections", summary="Collection list")
async def list_collections(
    _admin: dict = Depends(require_admin),
):
    cached_payload = get_admin_ui_cache("knowledge", "collections")
    if cached_payload is not None:
        return cached_payload

    factory = RepositoryFactory.get_instance()
    session = None

    try:
        session = factory.db_manager.get_session()
        collections = _get_or_sync_collections(session)

        result = []
        for kb in collections:
            item = kb.to_dict()
            item["document_count"] = _get_milvus_collection_count(kb.name)
            result.append(item)

        response = {
            "code": 0,
            "msg": "success",
            "data": {"collections": result},
        }
        set_admin_ui_cache(
            "knowledge",
            "collections",
            payload=response,
            ttl_seconds=60,
        )
        return response
    except Exception as error:
        logger.error("List collections failed: %s", error, exc_info=True)
        raise HTTPException(status_code=500, detail=f"list collections failed: {error}")
    finally:
        if session:
            session.close()


@router.post("/collections", summary="Create or update collection metadata")
async def upsert_collection(
    data: CollectionUpsertRequest,
    _admin: dict = Depends(require_admin),
):
    factory = RepositoryFactory.get_instance()
    session = None

    try:
        session = factory.db_manager.get_session()
        existing = session.query(KbCollection).filter(KbCollection.name == data.name).first()

        if existing:
            if data.display_name is not None:
                existing.display_name = data.display_name
            if data.type is not None:
                existing.type = data.type
            if data.description is not None:
                existing.description = data.description
            session.commit()
            session.refresh(existing)
            collection = existing
            logger.info("Admin %s updated collection meta: %s", _admin.get("username"), data.name)
        else:
            collection = KbCollection(
                name=data.name,
                display_name=data.display_name or data.name,
                type=data.type or "",
                description=data.description or "",
            )
            session.add(collection)
            session.commit()
            session.refresh(collection)
            logger.info("Admin %s created collection meta: %s", _admin.get("username"), data.name)

        response = {
            "code": 0,
            "msg": "success",
            "data": {"collection": collection.to_dict()},
        }
        invalidate_admin_ui_cache_groups("dashboard", "knowledge")
        return response
    except Exception as error:
        if session:
            session.rollback()
        logger.error("Upsert collection failed: %s", error, exc_info=True)
        raise HTTPException(status_code=500, detail=f"upsert collection failed: {error}")
    finally:
        if session:
            session.close()


@router.get("/tree", summary="Knowledge tree")
async def get_knowledge_tree(
    collection_name: Optional[str] = Query(None, description="filter by collection"),
    _admin: dict = Depends(require_admin),
):
    factory = RepositoryFactory.get_instance()
    session = None

    try:
        session = factory.db_manager.get_session()
        result = _build_knowledge_tree(session=session, collection_name=collection_name)
        return {
            "code": 0,
            "msg": "success",
            "data": result,
        }
    except HTTPException:
        raise
    except Exception as error:
        logger.error("Get knowledge tree failed: %s", error, exc_info=True)
        raise HTTPException(status_code=500, detail=f"get knowledge tree failed: {error}")
    finally:
        if session:
            session.close()


@router.get("/tree-options", summary="Knowledge tree options")
async def get_knowledge_tree_options(
    collection_name: Optional[str] = Query(None, description="filter by collection"),
    _admin: dict = Depends(require_admin),
):
    factory = RepositoryFactory.get_instance()
    session = None

    try:
        session = factory.db_manager.get_session()
        result = _build_knowledge_tree(session=session, collection_name=collection_name)
        return {
            "code": 0,
            "msg": "success",
            "data": {
                "tree_options": result["tree_options"],
                "rules": result["rules"],
                "collection_count": result["collection_count"],
            },
        }
    except HTTPException:
        raise
    except Exception as error:
        logger.error("Get knowledge tree options failed: %s", error, exc_info=True)
        raise HTTPException(status_code=500, detail=f"get knowledge tree options failed: {error}")
    finally:
        if session:
            session.close()


@router.post("/tree/validate", summary="Validate folder metadata")
async def validate_tree_metadata(
    data: KnowledgeTreeValidateRequest,
    _admin: dict = Depends(require_admin),
):
    factory = RepositoryFactory.get_instance()
    session = None

    try:
        session = factory.db_manager.get_session()
        collection_meta_map = _build_collection_meta_map(session)
        collection_meta = collection_meta_map.get(data.collection_name)
        if not collection_meta:
            raise HTTPException(status_code=404, detail="collection does not exist")

        rule = _get_collection_folder_rule(data.collection_name, collection_meta)
        (
            _resolved_collection_name,
            resolved_folder_path,
            resolved_category_level_1,
            resolved_category_level_2,
            resolved_category_level_3,
        ) = _resolve_upload_folder_selection(
            validate_collection_name=data.collection_name,
            folder_path=data.folder_path,
            category_level_1=data.category_level_1,
            category_level_2=data.category_level_2,
            category_level_3=data.category_level_3,
            node_id=data.node_id,
            selected_category=data.selected_category,
        )
        try:
            normalized_folder_path = _normalize_folder_metadata_input(
                folder_path=resolved_folder_path,
                category_level_1=resolved_category_level_1,
                category_level_2=resolved_category_level_2,
                category_level_3=resolved_category_level_3,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))

        if not rule["supports_folder_assignment"] and normalized_folder_path:
            raise HTTPException(
                status_code=400,
                detail=f"{data.collection_name} does not support folder metadata",
            )
        if len(normalized_folder_path) < rule["min_depth"]:
            raise HTTPException(
                status_code=400,
                detail=f"folder_path depth must be >= {rule['min_depth']}",
            )
        if len(normalized_folder_path) > rule["max_depth"]:
            raise HTTPException(
                status_code=400,
                detail=f"folder_path depth must be <= {rule['max_depth']}",
            )

        normalized_metadata = _build_category_metadata(normalized_folder_path)
        return {
            "code": 0,
            "msg": "success",
            "data": {
                "collection_name": data.collection_name,
                "collection_display_name": collection_meta.get("display_name") or data.collection_name,
                "collection_type": rule["collection_type"],
                "rule": rule,
                "normalized_folder_path": normalized_folder_path,
                "normalized_metadata": normalized_metadata,
                "query": _build_tree_query(data.collection_name, normalized_folder_path),
            },
        }
    except HTTPException:
        raise
    except Exception as error:
        logger.error("Validate tree metadata failed: %s", error, exc_info=True)
        raise HTTPException(status_code=500, detail=f"validate tree metadata failed: {error}")
    finally:
        if session:
            session.close()


@router.post("/upload-batch", summary="Admin upload batch")
async def upload_batch(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    collection_type: Optional[str] = Form(None),
    collection_name: Optional[str] = Form(None),
    permission_level: Optional[str] = Form(None),
    metadata: Optional[str] = Form(None),
    node_id: Optional[str] = Form(None),
    selected_category: Optional[str] = Form(None),
    folder_path: Optional[List[str]] = Form(None),
    category_level_1: Optional[str] = Form(None),
    category_level_2: Optional[str] = Form(None),
    category_level_3: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    content_columns: Optional[str] = Form(None),
    series: Optional[str] = Form(None),
    generation: Optional[str] = Form(None),
    controller: Optional[str] = Form(None),
    product_line: Optional[str] = Form(None),
    tonnage: Optional[str] = Form(None),
    window: int = Form(2),
    overlap: int = Form(1),
    drop_collections: bool = Form(False),
    _admin: dict = Depends(require_admin_or_reviewer),
):
    if not files:
        raise HTTPException(status_code=400, detail="files are required")

    route_info = _resolve_upload_route(collection_type=collection_type, collection_name=collection_name)
    if route_info["collection_type"] == "principle" and collection_name:
        raise HTTPException(status_code=400, detail="principle does not support custom collection_name")
    if route_info["collection_type"] in ASYNC_GRAPHRAG_COLLECTION_TYPES and overlap >= window:
        raise HTTPException(status_code=400, detail=f"overlap ({overlap}) must be less than window ({window})")

    raw_metadata = _parse_admin_metadata_json(metadata)
    reserved_metadata_keys = {"batch_id", "uploaded_at", "uploaded_by", "uploader"}
    for key in (*FOLDER_METADATA_KEYS, "permission_level", "permission_level_name", *reserved_metadata_keys):
        if key in raw_metadata:
            raise HTTPException(status_code=400, detail=f"metadata must not include reserved key: {key}")

    permission_metadata = None
    requires_permission = not (route_info["route_kind"] == "knowledge" and route_info["target_collection_name"] in {"parameter_updated", "parameter"})
    if requires_permission:
        if not permission_level:
            raise HTTPException(status_code=400, detail="permission_level is required")
        try:
            permission_metadata = build_permission_metadata(permission_level)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))

    factory = RepositoryFactory.get_instance()
    session = None

    try:
        session = factory.db_manager.get_session()
        (
            resolved_validate_collection_name,
            resolved_folder_path,
            resolved_category_level_1,
            resolved_category_level_2,
            resolved_category_level_3,
        ) = _resolve_upload_folder_selection(
            validate_collection_name=route_info["validate_collection_name"],
            folder_path=folder_path,
            category_level_1=category_level_1,
            category_level_2=category_level_2,
            category_level_3=category_level_3,
            node_id=node_id,
            selected_category=selected_category,
        )
        folder_result = _validate_folder_assignment(
            session=session,
            validate_collection_name=resolved_validate_collection_name,
            folder_path=resolved_folder_path,
            category_level_1=resolved_category_level_1,
            category_level_2=resolved_category_level_2,
            category_level_3=resolved_category_level_3,
        )
        merged_metadata = _build_upload_metadata(
            batch_id=str(uuid.uuid4()),
            admin_user=_admin,
            raw_metadata=raw_metadata,
            folder_metadata=folder_result["normalized_metadata"],
            permission_metadata=permission_metadata,
        )

        batch_id = merged_metadata["batch_id"]
        created_at = datetime.utcnow().isoformat()
        options = {
            "model": model,
            "content_columns": content_columns,
            "series": series,
            "generation": generation,
            "controller": controller,
            "product_line": product_line,
            "tonnage": tonnage,
            "window": window,
            "overlap": overlap,
            "drop_collections": drop_collections,
        }

        items: List[Dict[str, Any]] = []
        task_ids: List[str] = []
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prepared_uploads: List[Dict[str, Any]] = []

        for index, upload_file in enumerate(files, start=1):
            if not upload_file.filename:
                raise HTTPException(status_code=400, detail="file filename is required")
            _validate_upload_extension(route_info, upload_file.filename)
            prepared_uploads.append(
                {
                    "index": index,
                    "filename": upload_file.filename,
                    "task_id": f"admin_batch_{timestamp}_{index}_{uuid.uuid4().hex[:8]}",
                    "upload_file": upload_file,
                }
            )

        saved_paths: List[Path] = []
        persisted_original_paths: List[Path] = []
        try:
            for prepared in prepared_uploads:
                saved_file_path = _save_admin_batch_file(batch_id, prepared["index"], prepared["upload_file"])
                prepared["saved_file_path"] = saved_file_path
                saved_paths.append(saved_file_path)
                original_file_path = _persist_original_source_file(
                    batch_id=batch_id,
                    item_index=prepared["index"],
                    filename=prepared["filename"],
                    saved_file_path=saved_file_path,
                )
                prepared["original_file_path"] = original_file_path
                persisted_original_paths.append(original_file_path)
        except Exception:
            for saved_path in saved_paths:
                if saved_path.exists():
                    saved_path.unlink()
            for persisted_path in persisted_original_paths:
                if persisted_path.exists():
                    persisted_path.unlink()
            batch_dir = ADMIN_UPLOAD_BATCH_DIR / batch_id
            if batch_dir.exists():
                shutil.rmtree(batch_dir, ignore_errors=True)
            raise

        for prepared in prepared_uploads:
            item_metadata = copy.deepcopy(merged_metadata)
            item_metadata.update(
                {
                    "original_file_path": str(prepared["original_file_path"]),
                    "original_file_name": prepared["filename"],
                    "original_file_type": _guess_file_type(prepared["filename"]),
                }
            )
            items.append(
                _build_batch_item_record(
                    item_id=f"item_{prepared['index']}",
                    filename=prepared["filename"],
                    file_path=prepared["saved_file_path"],
                    task_id=prepared["task_id"],
                    route_info=route_info,
                    options=options,
                    metadata=item_metadata,
                )
            )
            task_ids.append(prepared["task_id"])

        effective_collection_name = route_info["target_collection_name"] or route_info["validate_collection_name"]

        batch_record = {
            "batch_id": batch_id,
            "created_at": created_at,
            "created_by": str(_admin.get("username") or "admin"),
            "collection_name": effective_collection_name,
            "collection_type": route_info["collection_type"],
            "normalized_folder_path": folder_result["normalized_folder_path"],
            "normalized_metadata": folder_result["normalized_metadata"],
            "permission_level": permission_metadata.get("permission_level") if permission_metadata else None,
            "permission_level_name": permission_metadata.get("permission_level_name") if permission_metadata else None,
            "items": items,
        }
        try:
            _save_batch_record(batch_record)
        except Exception:
            for saved_path in saved_paths:
                if saved_path.exists():
                    saved_path.unlink()
            for persisted_path in persisted_original_paths:
                if persisted_path.exists():
                    persisted_path.unlink()
            batch_dir = ADMIN_UPLOAD_BATCH_DIR / batch_id
            if batch_dir.exists():
                shutil.rmtree(batch_dir, ignore_errors=True)
            raise

        for prepared in prepared_uploads:
            _submit_upload_batch_item(
                background_tasks=background_tasks,
                task_id=prepared["task_id"],
                filename=prepared["filename"],
                file_path=prepared["saved_file_path"],
                route_info=route_info,
                metadata=copy.deepcopy(merged_metadata),
                options=copy.deepcopy(options),
            )

        response = {
            "code": 0,
            "msg": "success",
            "data": {
                "batch_id": batch_id,
                "task_ids": task_ids,
                "total": len(items),
                "collection_name": effective_collection_name,
                "collection_type": route_info["collection_type"],
                "normalized_folder_path": folder_result["normalized_folder_path"],
                "normalized_metadata": folder_result["normalized_metadata"],
                "permission_level": permission_metadata.get("permission_level") if permission_metadata else None,
                "permission_level_name": permission_metadata.get("permission_level_name") if permission_metadata else None,
            },
        }
        invalidate_admin_ui_cache_groups("dashboard", "knowledge")
        return response
    finally:
        if session:
            session.close()


@router.get("/upload-batches", summary="List upload batches")
async def list_upload_batches(
    status: Optional[str] = Query(None),
    _admin: dict = Depends(require_admin_or_reviewer),
):
    allowed_statuses = {"running", "failed", "done", "partial_failed", "unknown"}
    if status is not None and status not in allowed_statuses:
        raise HTTPException(status_code=400, detail="invalid upload batch status")

    batch_payloads: List[Dict[str, Any]] = []
    for batch_record in _iter_batch_records():
        if not _can_view_batch_record(_admin, batch_record):
            continue
        batch_payload = _build_batch_status_payload(batch_record)
        if status is not None and batch_payload["status"] != status:
            continue
        batch_payloads.append(batch_payload)

    batch_payloads.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    batch_payloads.sort(key=lambda item: BATCH_STATUS_ORDER.get(item["status"], len(BATCH_STATUS_ORDER)))
    return {
        "code": 0,
        "msg": "success",
        "data": {
            "items": batch_payloads,
            "total": len(batch_payloads),
        },
    }


@router.get("/upload-batches/{batch_id}", summary="Upload batch status")
async def get_upload_batch_status(
    batch_id: str,
    _admin: dict = Depends(require_admin_or_reviewer),
):
    batch_record = _load_batch_record(batch_id)
    if not _can_view_batch_record(_admin, batch_record):
        raise HTTPException(status_code=404, detail="upload batch does not exist")
    return {
        "code": 0,
        "msg": "success",
        "data": _build_batch_status_payload(batch_record),
    }


@router.delete("/upload-batches/{batch_id}", summary="Delete upload batch")
async def delete_upload_batch(
    batch_id: str,
    _admin: dict = Depends(require_admin_or_reviewer),
):
    batch_record = _load_batch_record(batch_id)
    if not _can_view_batch_record(_admin, batch_record):
        raise HTTPException(status_code=404, detail="upload batch does not exist")

    batch_status = _build_batch_status_payload(batch_record)["status"]
    task_ids = _collect_batch_task_ids(batch_record)
    cancelled_task_ids: List[str] = []
    for task_id in task_ids:
        if _mark_upload_task_cancelled(task_id):
            cancelled_task_ids.append(task_id)

    ingested_cleanup_result = {
        "deleted_documents": 0,
        "total_documents": 0,
        "deleted_collections": 0,
        "collection_results": {},
        "deleted_milvus_vectors": 0,
        "total_milvus_vectors": 0,
        "remaining_milvus_vectors": 0,
        "milvus_collection_results": {},
    }
    if batch_status != "done":
        ingested_cleanup_result = _soft_delete_batch_documents(batch_id)
        ingested_cleanup_result.update(_soft_delete_batch_milvus_vectors(batch_record))

    cleanup_result = _cleanup_upload_batch_files(batch_record)
    _get_batch_redis().delete(_admin_batch_key(batch_id))
    invalidate_admin_ui_cache_groups("dashboard", "knowledge")
    return {
        "code": 0,
        "msg": "success",
        "data": {
            "batch_id": batch_id,
            "batch_status": batch_status,
            "task_ids": task_ids,
            "cancelled_task_ids": cancelled_task_ids,
            **ingested_cleanup_result,
            **cleanup_result,
        },
    }


@router.post("/upload-batches/{batch_id}/retry", summary="Retry failed upload batch items")
async def retry_upload_batch(
    batch_id: str,
    data: RetryUploadBatchRequest,
    background_tasks: BackgroundTasks,
    _admin: dict = Depends(require_admin_or_reviewer),
):
    batch_record = _load_batch_record(batch_id)
    if not _can_view_batch_record(_admin, batch_record):
        raise HTTPException(status_code=404, detail="upload batch does not exist")
    selected_ids = set(data.failed_items or [])
    retried_task_ids: List[str] = []
    retried_item_ids: List[str] = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    for item in batch_record["items"]:
        current_snapshot = _load_task_snapshot(item["current_task_id"])
        if current_snapshot["status"] != "failed":
            continue
        if selected_ids and item["item_id"] not in selected_ids and item["current_task_id"] not in selected_ids:
            continue

        file_path = Path(item["file_path"])
        if not file_path.exists():
            raise HTTPException(status_code=404, detail=f"upload source file missing: {file_path}")

        new_task_id = f"admin_retry_{timestamp}_{uuid.uuid4().hex[:8]}"
        _submit_upload_batch_item(
            background_tasks=background_tasks,
            task_id=new_task_id,
            filename=item["filename"],
            file_path=file_path,
            route_info={
                "route_kind": item["route_kind"],
                "collection_type": item["collection_type"],
                "target_collection_name": item["target_collection_name"],
                "validate_collection_name": item["validate_collection_name"],
            },
            metadata=copy.deepcopy(item["metadata"]),
            options=copy.deepcopy(item["options"]),
        )
        item["current_task_id"] = new_task_id
        item.setdefault("task_history", []).append(new_task_id)
        retried_task_ids.append(new_task_id)
        retried_item_ids.append(item["item_id"])

    if not retried_task_ids:
        raise HTTPException(status_code=400, detail="no failed items to retry")

    _save_batch_record(batch_record)
    response = {
        "code": 0,
        "msg": "success",
        "data": {
            "batch_id": batch_id,
            "retried_count": len(retried_item_ids),
            "retried_item_ids": retried_item_ids,
            "task_ids": retried_task_ids,
        },
    }
    invalidate_admin_ui_cache_groups("dashboard", "knowledge")
    return response


@router.get("/files", summary="Unified file list")
async def list_files(
    collection_name: Optional[str] = Query(None, description="filter by collection"),
    keyword: Optional[str] = Query(None, description="search keyword"),
    category: Optional[str] = Query(None, description="category alias filter"),
    permission_level: Optional[int] = Query(None, description="permission level"),
    entity_type: Optional[str] = Query(None, description="entity type"),
    metadata_key: Optional[str] = Query(None, description="metadata key"),
    metadata_value: Optional[str] = Query(None, description="metadata value"),
    folder_path: Optional[str] = Query(None, description="folder path, split by /"),
    node_id: Optional[str] = Query(None, description="tree node id"),
    status: str = Query("active", description="active / archived / all"),
    page: int = Query(1, ge=1, description="page"),
    page_size: int = Query(20, ge=1, le=100, description="page size"),
    _admin: dict = Depends(require_admin),
):
    if permission_level is not None:
        try:
            permission_level = normalize_permission_level(permission_level)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))

    if status not in {"active", "archived", "all"}:
        raise HTTPException(status_code=400, detail="status only supports active / archived / all")

    resolved_collection_name, resolved_folder_path = _resolve_folder_filter(
        collection_name=collection_name,
        folder_path=folder_path,
        node_id=node_id,
    )

    factory = RepositoryFactory.get_instance()
    session = None

    try:
        session = factory.db_manager.get_session()
        result = _query_knowledge_files(
            session=session,
            collection_name=resolved_collection_name,
            keyword=keyword,
            category=category,
            permission_level=permission_level,
            entity_type=entity_type,
            metadata_key=metadata_key,
            metadata_value=metadata_value,
            folder_path=resolved_folder_path,
            page=page,
            page_size=page_size,
            status=status,
        )
        return {
            "code": 0,
            "msg": "success",
            "data": result,
        }
    except HTTPException:
        raise
    except Exception as error:
        logger.error("List files failed: %s", error, exc_info=True)
        raise HTTPException(status_code=500, detail=f"list files failed: {error}")
    finally:
        if session:
            session.close()


@router.delete("/files/{document_id}", summary="Delete file")
async def delete_file(
    document_id: str,
    collection_name: str = Query(..., description="collection name"),
    _admin: dict = Depends(require_admin),
):
    doc = _get_document_or_404(document_id, collection_name)
    if bool((doc.metadata or {}).get("is_deleted", False)):
        raise HTTPException(status_code=400, detail="file is already deleted")

    delete_result = knowledge_service.delete_documents([document_id], collection_name)
    deleted_count = _resolve_deleted_count(delete_result)
    if deleted_count < 1:
        raise HTTPException(status_code=500, detail="delete file failed")

    response = {
        "code": 0,
        "msg": "success",
        "data": {
            "document_id": document_id,
            "collection_name": collection_name,
            "deleted_count": deleted_count,
            "result": delete_result,
            "status": "deleted",
        },
    }
    invalidate_admin_ui_cache_groups("dashboard", "knowledge")
    return response


@router.get("/files/{document_id}", summary="File detail")
async def get_file_detail(
    document_id: str,
    collection_name: str = Query(..., description="collection name"),
    _admin: dict = Depends(require_admin),
):
    factory = RepositoryFactory.get_instance()
    session = None

    try:
        session = factory.db_manager.get_session()
        doc = _get_document_or_404(document_id, collection_name)
        collection_meta_map = _build_collection_meta_map(session)
        return {
            "code": 0,
            "msg": "success",
            "data": {
                "file": _build_file_record(
                    doc,
                    collection_meta_map,
                    include_full_metadata=True,
                )
            },
        }
    except HTTPException:
        raise
    except Exception as error:
        logger.error(
            "Get file detail failed: collection=%s, document_id=%s, %s",
            collection_name,
            document_id,
            error,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"get file detail failed: {error}")
    finally:
        if session:
            session.close()


@router.post("/files/{document_id}/retry", summary="Retry failed knowledge file")
async def retry_file(
    document_id: str,
    data: RetryKnowledgeFileRequest,
    background_tasks: BackgroundTasks,
    _admin: dict = Depends(require_admin_or_reviewer),
):
    requested_task_id = (data.task_id or "").strip() or None
    batch_record, item = _locate_retry_batch_item(
        document_id=document_id,
        task_id=requested_task_id,
    )
    if not _can_view_batch_record(_admin, batch_record):
        raise HTTPException(status_code=404, detail="retry target does not exist")
    current_snapshot = _load_task_snapshot(item["current_task_id"])
    if current_snapshot["status"] != "failed":
        raise HTTPException(status_code=400, detail="file retry only supports failed items")

    file_path = Path(item["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"upload source file missing: {file_path}")

    previous_task_id = item["current_task_id"]
    new_task_id = f"admin_retry_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    _submit_upload_batch_item(
        background_tasks=background_tasks,
        task_id=new_task_id,
        filename=item["filename"],
        file_path=file_path,
        route_info={
            "route_kind": item["route_kind"],
            "collection_type": item["collection_type"],
            "target_collection_name": item["target_collection_name"],
            "validate_collection_name": item["validate_collection_name"],
        },
        metadata=copy.deepcopy(item["metadata"]),
        options=copy.deepcopy(item["options"]),
    )
    item["current_task_id"] = new_task_id
    item.setdefault("task_history", []).append(new_task_id)
    _save_batch_record(batch_record)

    matched_document_ids = _get_batch_item_document_ids(item)
    return {
        "code": 0,
        "msg": "success",
        "data": {
            "batch_id": batch_record["batch_id"],
            "item_id": item["item_id"],
            "document_id": document_id if document_id in matched_document_ids else None,
            "document_ids": matched_document_ids,
            "previous_task_id": previous_task_id,
            "task_id": new_task_id,
            "status": "queued",
        },
    }


@router.get("/files/{document_id}/preview", summary="File preview")
async def preview_file(
    document_id: str,
    collection_name: str = Query(..., description="collection name"),
    _admin: dict = Depends(require_admin),
):
    try:
        doc = _get_document_or_404(document_id, collection_name)
        return {
            "code": 0,
            "msg": "success",
            "data": {
                "preview": _build_preview_payload(doc),
            },
        }
    except HTTPException:
        raise
    except Exception as error:
        logger.error(
            "Get file preview failed: collection=%s, document_id=%s, %s",
            collection_name,
            document_id,
            error,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"get file preview failed: {error}")


@router.get("/collections/{collection_name}/files", summary="Collection file list")
async def list_collection_files(
    collection_name: str,
    page: int = Query(1, ge=1, description="page"),
    page_size: int = Query(20, ge=1, le=100, description="page size"),
    keyword: Optional[str] = Query(None, description="search keyword"),
    category: Optional[str] = Query(None, description="category alias filter"),
    permission_level: Optional[int] = Query(None, description="permission level"),
    entity_type: Optional[str] = Query(None, description="entity type"),
    metadata_key: Optional[str] = Query(None, description="metadata key"),
    metadata_value: Optional[str] = Query(None, description="metadata value"),
    folder_path: Optional[str] = Query(None, description="folder path, split by /"),
    node_id: Optional[str] = Query(None, description="tree node id"),
    status: str = Query("active", description="active / archived / all"),
    _admin: dict = Depends(require_admin),
):
    if permission_level is not None:
        try:
            permission_level = normalize_permission_level(permission_level)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))

    if status not in {"active", "archived", "all"}:
        raise HTTPException(status_code=400, detail="status only supports active / archived / all")

    resolved_collection_name, resolved_folder_path = _resolve_folder_filter(
        collection_name=collection_name,
        folder_path=folder_path,
        node_id=node_id,
    )
    cache_params = {
        "collection_name": resolved_collection_name,
        "keyword": keyword,
        "category": category,
        "permission_level": permission_level,
        "entity_type": entity_type,
        "metadata_key": metadata_key,
        "metadata_value": metadata_value,
        "folder_path": resolved_folder_path,
        "node_id": node_id,
        "status": status,
        "page": page,
        "page_size": page_size,
    }
    cached_payload = get_admin_ui_cache(
        "knowledge",
        "collection_files",
        params=cache_params,
    )
    if cached_payload is not None:
        return cached_payload

    factory = RepositoryFactory.get_instance()
    session = None

    try:
        session = factory.db_manager.get_session()
        result = _query_knowledge_files(
            session=session,
            collection_name=resolved_collection_name,
            keyword=keyword,
            category=category,
            permission_level=permission_level,
            entity_type=entity_type,
            metadata_key=metadata_key,
            metadata_value=metadata_value,
            folder_path=resolved_folder_path,
            page=page,
            page_size=page_size,
            status=status,
        )
        response = {
            "code": 0,
            "msg": "success",
            "data": result,
        }
        set_admin_ui_cache(
            "knowledge",
            "collection_files",
            params=cache_params,
            payload=response,
            ttl_seconds=15,
        )
        return response
    except HTTPException:
        raise
    except Exception as error:
        logger.error(
            "List collection files failed: collection=%s, %s",
            collection_name,
            error,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"list collection files failed: {error}")
    finally:
        if session:
            session.close()
