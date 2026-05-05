# -*- coding: utf-8 -*-
"""
入库管理 API。

POST /upload
    统一文件入库入口
    - manual: PDF/Word 异步 GraphRAG 入库
    - workorder/excellent_workorder: Excel 同步结构化入库
    - special_workorder: docx/zip 同步图文步骤入库
GET /status/{task_id}
    查询任务进度
GET /list
    列出任务
DELETE /{task_id}
    取消任务
"""

import logging
import os
import re
import shutil
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

import redis
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile

from api.schema.ingest.ingest_models import (
    IngestListItem,
    IngestListResponse,
    IngestStatusResponse,
    IngestTaskResponse,
)
from config.config import (
    COLLECTION_MANUAL_TOTAL,
    COLLECTION_MANUAL_CHUNKS,
    COLLECTION_PRINCIPLE_TOTAL,
    COLLECTION_PRINCIPLE_CHUNKS,
    COLLECTION_WORKORDER as _COLLECTION_WORKORDER_CSV,
    REDIS_DB,
    REDIS_HOST,
    REDIS_PASSWORD,
    REDIS_PORT,
)

_workorder_cols = [c.strip() for c in _COLLECTION_WORKORDER_CSV.split(",")]
COLLECTION_SPECIAL_WORKORDER = _workorder_cols[2] if len(_workorder_cols) > 2 else "special_workorder"
from service.auth.knowledge_permission_utils import build_permission_metadata

logger = logging.getLogger(__name__)
router = APIRouter()

ALLOWED_EXTENSIONS = {
    "manual": {".pdf", ".docx", ".doc"},
    "workorder": {".xlsx", ".xls"},
    "special_workorder": {".docx", ".zip"},
    "excellent_workorder": {".xlsx", ".xls"},
    "principle": {".pdf", ".docx", ".doc"},
}
DEFAULT_WORKORDER_CONTENT_COLUMNS = "故障描述,解决方案"

UPLOAD_DIR = Path(os.getenv("INGEST_UPLOAD_DIR", "/app/upload"))
_redis: Optional[redis.Redis] = None


def _get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis(
            host=REDIS_HOST,
            port=int(REDIS_PORT),
            db=int(REDIS_DB),
            password=REDIS_PASSWORD or None,
            decode_responses=True,
        )
    return _redis


def _task_key(task_id: str) -> str:
    return f"ingest:task:{task_id}"


def _is_task_cancelled(task_id: str) -> bool:
    return _get_redis().hget(_task_key(task_id), "cancel") == "1"


def _raise_if_task_cancelled(task_id: str) -> None:
    if _is_task_cancelled(task_id):
        raise RuntimeError("任务被用户取消")


def _sanitize_task_component(value: Optional[str]) -> str:
    normalized = unicodedata.normalize("NFKC", (value or "").strip())
    normalized = re.sub(r"\s+", "_", normalized)
    normalized = re.sub(r"[^A-Za-z0-9._-]", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("._-")
    return normalized or "task"


def _ensure_ingestion_path() -> None:
    for path in ("/app/ingestion", "/app"):
        if path not in sys.path:
            sys.path.insert(0, path)


def _make_progress_callback(task_id: str, start_time: datetime):
    redis_client = _get_redis()
    key = _task_key(task_id)

    def callback(
        step: int,
        name: str,
        detail: str = "",
        current: Optional[int] = None,
        total: Optional[int] = None,
    ) -> None:
        _raise_if_task_cancelled(task_id)
        elapsed = int((datetime.now() - start_time).total_seconds())
        mapping = {
            "current_step": step,
            "step_name": name,
            "progress_detail": detail,
            "elapsed_seconds": elapsed,
        }
        if current is not None and total is not None:
            mapping["step_current"] = int(current)
            mapping["step_total"] = int(total)
        else:
            mapping["step_current"] = ""
            mapping["step_total"] = ""
        redis_client.hset(key, mapping=mapping)

    return callback


def _finish_task(task_id: str, start_time: datetime, result: dict, total_steps: int) -> None:
    _raise_if_task_cancelled(task_id)
    redis_client = _get_redis()
    key = _task_key(task_id)
    elapsed = int((datetime.now() - start_time).total_seconds())

    if result.get("success"):
        redis_client.hset(
            key,
            mapping={
                "status": "done",
                "current_step": total_steps,
                "step_name": "完成",
                "progress_detail": "",
                "finished_at": datetime.now().isoformat(),
                "elapsed_seconds": elapsed,
            },
        )
        logger.info("入库任务完成: %s, 耗时 %ss", task_id, elapsed)
        return

    message = result.get("msg", "入库失败")
    redis_client.hset(
        key,
        mapping={
            "status": "failed",
            "finished_at": datetime.now().isoformat(),
            "elapsed_seconds": elapsed,
            "error": message,
        },
    )
    logger.error("入库任务失败: %s, %s", task_id, message)


def _fail_task(task_id: str, start_time: datetime, error: Exception) -> None:
    redis_client = _get_redis()
    key = _task_key(task_id)
    elapsed = int((datetime.now() - start_time).total_seconds())
    error_message = "任务被用户取消" if redis_client.hget(key, "cancel") == "1" else str(error)
    redis_client.hset(
        key,
        mapping={
            "status": "failed",
            "finished_at": datetime.now().isoformat(),
            "elapsed_seconds": elapsed,
            "error": error_message,
        },
    )
    logger.error("入库任务异常: %s, %s", task_id, error, exc_info=True)


def _init_redis_task(task_id: str, filename: str, model: str, total_steps: int) -> None:
    redis_client = _get_redis()
    key = _task_key(task_id)
    redis_client.hset(
        key,
        mapping={
            "status": "pending",
            "current_step": 0,
            "total_steps": total_steps,
            "step_name": "等待开始",
            "progress_detail": "",
            "model": model,
            "pdf_filename": filename,
            "started_at": datetime.now().isoformat(),
            "finished_at": "",
            "elapsed_seconds": 0,
            "error": "",
        },
    )
    redis_client.expire(key, 86400)


def _save_sync_task_to_redis(task_id: str, filename: str, result: dict) -> None:
    redis_client = _get_redis()
    key = _task_key(task_id)
    redis_client.hset(
        key,
        mapping={
            "status": "done",
            "current_step": 5,
            "total_steps": 5,
            "step_name": "完成",
            "progress_detail": f"{result.get('success_rows', 0)} 条",
            "model": result.get("model", ""),
            "pdf_filename": filename,
            "started_at": datetime.now().isoformat(),
            "finished_at": datetime.now().isoformat(),
            "elapsed_seconds": result.get("elapsed_seconds", 0),
            "error": "",
        },
    )
    redis_client.expire(key, 86400)


def _save_upload_file(task_id: str, file: UploadFile) -> Path:
    task_dir = UPLOAD_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    file_path = task_dir / file.filename
    with open(file_path, "wb") as output:
        shutil.copyfileobj(file.file, output)
    return file_path


def _parse_elapsed_seconds(raw_value) -> Optional[int]:
    if raw_value in (None, ""):
        return None
    try:
        return int(float(raw_value))
    except (TypeError, ValueError):
        raise HTTPException(status_code=500, detail=f"invalid elapsed_seconds: {raw_value}")


def _run_pdf_ingest_task(
    task_id: str,
    file_path: str,
    model: str,
    window: int,
    overlap: int,
    drop_collections: bool,
    chapter_collection: Optional[str],
    chunks_collection: Optional[str],
    series: str,
    generation: str,
    controller: str,
    product_line: str,
    tonnage: str,
    permission_level: int,
    permission_level_name: str,
) -> None:
    start_time = datetime.now()
    _get_redis().hset(_task_key(task_id), mapping={"status": "running"})
    try:
        _ensure_ingestion_path()
        from manual_ingest_agent import ingest_manual

        result = ingest_manual(
            pdf_path=file_path,
            model_name=model,
            window=window,
            overlap=overlap,
            drop_collections=drop_collections,
            chapter_collection=chapter_collection,
            chunks_collection=chunks_collection,
            progress_callback=_make_progress_callback(task_id, start_time),
            series=series,
            generation=generation,
            controller=controller,
            product_line=product_line,
            tonnage=tonnage,
            permission_level=permission_level,
            permission_level_name=permission_level_name,
        )
        _finish_task(task_id, start_time, result, total_steps=9)
    except Exception as error:
        _fail_task(task_id, start_time, error)


def _run_word_ingest_task(
    task_id: str,
    file_path: str,
    model: str,
    window: int,
    overlap: int,
    drop_collections: bool,
    chapter_collection: Optional[str],
    chunks_collection: Optional[str],
    series: str,
    generation: str,
    controller: str,
    product_line: str,
    tonnage: str,
    permission_level: int,
    permission_level_name: str,
) -> None:
    start_time = datetime.now()
    _get_redis().hset(_task_key(task_id), mapping={"status": "running"})
    try:
        _ensure_ingestion_path()
        from word_ingest_agent import ingest_word

        result = ingest_word(
            docx_path=file_path,
            model_name=model,
            window=window,
            overlap=overlap,
            drop_collections=drop_collections,
            chapter_collection=chapter_collection,
            chunks_collection=chunks_collection,
            progress_callback=_make_progress_callback(task_id, start_time),
            series=series,
            generation=generation,
            controller=controller,
            product_line=product_line,
            tonnage=tonnage,
            permission_level=permission_level,
            permission_level_name=permission_level_name,
        )
        total_steps = 9 if result.get("has_toc") else 5
        _finish_task(task_id, start_time, result, total_steps=total_steps)
    except Exception as error:
        _fail_task(task_id, start_time, error)


@router.post("/upload", summary="上传文件并提交入库任务", response_model=IngestTaskResponse)
async def upload_and_ingest(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    collection_type: str = Form("manual"),
    permission_level: str = Form(...),
    model: Optional[str] = Form(None),
    content_columns: Optional[str] = Form(None),
    collection_name: Optional[str] = Form(None),
    series: Optional[str] = Form(None),
    generation: Optional[str] = Form(None),
    controller: Optional[str] = Form(None),
    product_line: Optional[str] = Form(None),
    tonnage: Optional[str] = Form(None),
    window: int = Form(2),
    overlap: int = Form(1),
    drop_collections: bool = Form(False),
):
    from service.auth.permission_utils import check_ingest_permission

    current_user = getattr(request.state, "user", None) or {}
    if not check_ingest_permission(current_user):
        raise HTTPException(status_code=403, detail="权限不足：只有管理员才能执行入库操作")

    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    ext = Path(file.filename).suffix.lower()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        permission_metadata = build_permission_metadata(permission_level)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    allowed = ALLOWED_EXTENSIONS.get(collection_type)
    if allowed is None:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的 collection_type: {collection_type}，可选 {list(ALLOWED_EXTENSIONS.keys())}",
        )
    if ext not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"{collection_type} 类型只支持 {sorted(allowed)}，上传的是 {ext}",
        )

    if overlap >= window:
        raise HTTPException(status_code=400, detail=f"overlap ({overlap}) 必须小于 window ({window})")

    safe_model = _sanitize_task_component(model)

    if collection_type == "manual":
        if ext == ".pdf":
            task_id = f"ingest_{ts}_{safe_model}"
            file_path = _save_upload_file(task_id, file)
            _init_redis_task(task_id, file.filename, model, total_steps=9)
            background_tasks.add_task(
                _run_pdf_ingest_task,
                task_id=task_id,
                file_path=str(file_path),
                model=model,
                window=window,
                overlap=overlap,
                drop_collections=drop_collections,
                chapter_collection=COLLECTION_MANUAL_TOTAL,
                chunks_collection=COLLECTION_MANUAL_CHUNKS,
                series=series,
                generation=generation,
                controller=controller,
                product_line=product_line,
                tonnage=tonnage,
                **permission_metadata,
            )
            return IngestTaskResponse(
                task_id=task_id,
                status="pending",
                message="PDF 入库任务已提交",
            )

        task_id = f"ingest_{ts}_{safe_model}_word"
        file_path = _save_upload_file(task_id, file)
        _init_redis_task(task_id, file.filename, model, total_steps=9)
        background_tasks.add_task(
            _run_word_ingest_task,
            task_id=task_id,
            file_path=str(file_path),
            model=model,
            window=window,
            overlap=overlap,
            drop_collections=drop_collections,
            chapter_collection=COLLECTION_MANUAL_TOTAL,
            chunks_collection=COLLECTION_MANUAL_CHUNKS,
            series=series,
            generation=generation,
            controller=controller,
            product_line=product_line,
            tonnage=tonnage,
            **permission_metadata,
        )
        return IngestTaskResponse(
            task_id=task_id,
            status="pending",
            message="Word 入库任务已提交",
        )

    if collection_type == "principle":
        if collection_name:
            raise HTTPException(status_code=400, detail="principle 不支持自定义 collection_name")

        if ext == ".pdf":
            task_id = f"ingest_{ts}_{safe_model}_principle"
            file_path = _save_upload_file(task_id, file)
            _init_redis_task(task_id, file.filename, model, total_steps=9)
            background_tasks.add_task(
                _run_pdf_ingest_task,
                task_id=task_id,
                file_path=str(file_path),
                model=model,
                window=window,
                overlap=overlap,
                drop_collections=drop_collections,
                chapter_collection=COLLECTION_PRINCIPLE_TOTAL,
                chunks_collection=COLLECTION_PRINCIPLE_CHUNKS,
                series=series,
                generation=generation,
                controller=controller,
                product_line=product_line,
                tonnage=tonnage,
                **permission_metadata,
            )
            return IngestTaskResponse(
                task_id=task_id,
                status="pending",
                message="Principle PDF 入库任务已提交",
            )

        task_id = f"ingest_{ts}_{safe_model}_principle_word"
        file_path = _save_upload_file(task_id, file)
        _init_redis_task(task_id, file.filename, model, total_steps=9)
        background_tasks.add_task(
            _run_word_ingest_task,
            task_id=task_id,
            file_path=str(file_path),
            model=model,
            window=window,
            overlap=overlap,
            drop_collections=drop_collections,
            chapter_collection=COLLECTION_PRINCIPLE_TOTAL,
            chunks_collection=COLLECTION_PRINCIPLE_CHUNKS,
            series=series,
            generation=generation,
            controller=controller,
            product_line=product_line,
            tonnage=tonnage,
            **permission_metadata,
        )
        return IngestTaskResponse(
            task_id=task_id,
            status="pending",
            message="Principle Word 入库任务已提交",
        )

    if collection_type in ("workorder", "excellent_workorder"):
        task_id = f"sync_{ts}_{collection_type}"
        file_path = _save_upload_file(task_id, file)
        actual_collection = collection_name or "workorder"
        raw_content_columns = content_columns or DEFAULT_WORKORDER_CONTENT_COLUMNS
        columns = [column.strip() for column in raw_content_columns.split(",") if column.strip()]
        if not columns:
            raise HTTPException(status_code=400, detail="content_columns 不能为空")

        try:
            _ensure_ingestion_path()
            from excel_ingest_agent import ingest_excel

            result = ingest_excel(
                file_path=str(file_path),
                content_columns=columns,
                collection_name=actual_collection,
                knowledge_type=collection_type,
                model=model or "",
                series=series,
                generation=generation,
                controller=controller,
                product_line=product_line,
                tonnage=tonnage,
                **permission_metadata,
            )
            if not result.get("success"):
                raise HTTPException(status_code=500, detail=result.get("msg", "Excel 入库失败"))

            _save_sync_task_to_redis(task_id, file.filename, result)
            return IngestTaskResponse(
                task_id=task_id,
                status="done",
                message=f"入库完成，共 {result.get('success_rows', 0)} 条",
                collection_type=collection_type,
                rows_ingested=result.get("success_rows", 0),
            )
        except HTTPException:
            raise
        except Exception as error:
            logger.error("Excel 入库失败: %s, %s", task_id, error, exc_info=True)
            raise HTTPException(status_code=500, detail=f"Excel 入库失败: {str(error)}")

    raise HTTPException(status_code=400, detail=f"未处理的 collection_type: {collection_type}")


@router.get("/status/{task_id}", summary="查询入库任务进度", response_model=IngestStatusResponse)
async def get_ingest_status(task_id: str):
    redis_client = _get_redis()
    key = _task_key(task_id)
    data = redis_client.hgetall(key)
    if not data:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    return IngestStatusResponse(
        task_id=task_id,
        status=data.get("status", "unknown"),
        current_step=int(data.get("current_step", 0)),
        total_steps=int(data.get("total_steps", 9)),
        step_name=data.get("step_name", ""),
        progress_detail=data.get("progress_detail", ""),
        model=data.get("model", ""),
        pdf_filename=data.get("pdf_filename", ""),
        started_at=data.get("started_at") or None,
        finished_at=data.get("finished_at") or None,
        elapsed_seconds=_parse_elapsed_seconds(data.get("elapsed_seconds")),
        error=data.get("error") or None,
    )


@router.get("/list", summary="列出所有入库任务", response_model=IngestListResponse)
async def list_ingest_tasks():
    redis_client = _get_redis()
    tasks = []
    for key in redis_client.keys("ingest:task:*"):
        data = redis_client.hgetall(key)
        if not data:
            continue
        tasks.append(
            IngestListItem(
                task_id=key.removeprefix("ingest:task:"),
                status=data.get("status", "unknown"),
                model=data.get("model", ""),
                pdf_filename=data.get("pdf_filename", ""),
                started_at=data.get("started_at") or None,
                elapsed_seconds=_parse_elapsed_seconds(data.get("elapsed_seconds")),
            )
        )
    tasks.sort(key=lambda item: item.started_at or "", reverse=True)
    return IngestListResponse(tasks=tasks)


@router.delete("/{task_id}", summary="取消入库任务", response_model=IngestTaskResponse)
async def cancel_ingest_task(task_id: str):
    redis_client = _get_redis()
    key = _task_key(task_id)
    data = redis_client.hgetall(key)
    if not data:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    status = data.get("status", "")
    if status in ("done", "failed"):
        return IngestTaskResponse(
            task_id=task_id,
            status=status,
            message=f"任务已结束（{status}），无法取消",
        )

    redis_client.hset(key, "cancel", "1")
    redis_client.hset(key, "status", "failed")
    redis_client.hset(key, "error", "任务被用户取消")
    return IngestTaskResponse(task_id=task_id, status="failed", message="任务取消成功")
