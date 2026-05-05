#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
manual_ingest_agent.py — PDF 说明书入库 Agent（唯一入口）

双 collection 架构：
  manual_total  → 章节级，每章 1 条，LLM 生成 150-250 字描述（对齐主后端 COLLECTION_MANUAL）
  manual_chunks → 滑窗级，window=2/overlap=1，LLM 生成 100-200 字描述

处理管线：
  ┌─ PDF
  │
  ├─ [A] 章节层  ManualParserStandalone.parse()
  │              TOC 检测 → 章节子 PDF（output/slices/{model}/）
  │              LLM 优化每章描述
  │              Embedding → manual_knowledge
  │
  └─ [B] 滑窗层  chunker.sliding_window_chunks()   ← 依赖 A 的 toc_items
                 llm_extractor.extract_chunks_batch()
                 Embedding → manual_knowledge_chunks

每个滑窗 chunk 的 slice_pdf_path 指向其所属章节子 PDF（相对路径）。

用法：
  python manual_ingest_agent.py --pdf path/to/HC300.pdf
  python manual_ingest_agent.py --pdf path/to/MA1600.pdf --model MA1600
  python manual_ingest_agent.py --pdf /app/input/MA1600.pdf --model MA1600 \\
    --window 3 --overlap 1
"""

import os
import sys
import time
import logging
import argparse
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ------------------------------------------------------------------ #
# 1. 环境初始化（必须在所有 src 导入之前）
# ------------------------------------------------------------------ #
_AGENT1_DIR = Path(__file__).parent
if str(_AGENT1_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT1_DIR))

# shared 层：项目根目录下的 shared/ 包
_PROJECT_ROOT = _AGENT1_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_AGENT1_DIR / ".env")

# API KEY 兼容映射已统一到 src/config.py（import src 模块时自动执行）

# ------------------------------------------------------------------ #
# 2. 延迟导入
# ------------------------------------------------------------------ #
from manual_parser_standalone import ManualParserStandalone
from chunker import sliding_window_chunks, find_chapter_pdf
from llm_extractor import extract_chunks_batch, extract_chunks_graph_batch
from document_identity import build_document_fingerprint, build_ingestion_document_id
from src.config import LLMConfig
from src.llm_client import get_embeddings_batch
from db.milvus_writer import MilvusWriter
from db.mongo_writer import MongoWriter
from db.es_writer import ESWriter

from shared.config.settings import (
    EMBEDDING_DIMENSION,
    COLLECTION_MANUAL_TOTAL,
    COLLECTION_MANUAL_CHUNKS,
    COLLECTION_PRINCIPLE_TOTAL,
    COLLECTION_PRINCIPLE_CHUNKS,
)
from shared.schema.entity_types import EntityType

# ------------------------------------------------------------------ #
# 3. 日志
# ------------------------------------------------------------------ #

def _setup_logging(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"ingest_{ts}.log"
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.WARNING)
    ch.setFormatter(fmt)
    root.addHandler(ch)
    return log_file


logger = logging.getLogger(__name__)


def _page_image_collection_type(chapter_collection: str, chunks_collection: str) -> str | None:
    if chapter_collection == COLLECTION_MANUAL_TOTAL and chunks_collection == COLLECTION_MANUAL_CHUNKS:
        return "manual"
    if chapter_collection == COLLECTION_PRINCIPLE_TOTAL and chunks_collection == COLLECTION_PRINCIPLE_CHUNKS:
        return "principle"
    return None


def _require_db_env() -> dict:
    required = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"生成说明书图片映射缺少数据库配置: {', '.join(missing)}")
    return {
        "host": os.environ["DB_HOST"],
        "port": int(os.environ["DB_PORT"]),
        "database": os.environ["DB_NAME"],
        "user": os.environ["DB_USER"],
        "password": os.getenv("DB_PASSWORD", ""),
        "charset": "utf8mb4",
    }


def _image_output_dir() -> tuple[Path, str]:
    save_dir = Path.cwd() / "temp_files"
    save_dir.mkdir(parents=True, exist_ok=True)
    return save_dir, "temp_files"


def _iter_record_pages(records):
    for record in records:
        document_id = record.get("id")
        page_start = int(record.get("page_start") or 0)
        page_end = int(record.get("page_end") or 0)
        if not document_id or page_start <= 0 or page_end <= 0:
            continue
        if page_end < page_start:
            raise ValueError(
                f"无效页码范围: document_id={document_id}, page_start={page_start}, page_end={page_end}"
            )
        for page_num in range(page_start, page_end + 1):
            yield str(document_id), page_num


def _chunked(values, size: int = 500):
    values = list(values)
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _write_page_images(pdf_path: str, document_fingerprint: str, records: list[dict], collection_type: str) -> dict:
    """
    为 manual 入库结果生成页面图片，并写入 image_file/manual_image。

    manual_image.page_num 必须使用原始 PDF 页码，serving 检索侧按该页码范围查图。
    """
    record_pages = list(_iter_record_pages(records))
    if not record_pages:
        return {"image_count": 0, "link_count": 0}

    page_nums = sorted({page_num for _, page_num in record_pages})
    document_ids = sorted({document_id for document_id, _ in record_pages})

    import fitz
    import pymysql

    pdf = fitz.open(pdf_path)
    try:
        total_pages = pdf.page_count
        out_dir, relative_root = _image_output_dir()
        safe_fingerprint = re.sub(r"[^A-Za-z0-9_-]", "_", document_fingerprint)[:32]
        page_to_image = {}

        for page_num in page_nums:
            if page_num > total_pages:
                raise ValueError(f"图片生成页码越界: page={page_num}, total_pages={total_pages}, pdf={pdf_path}")
            image_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{document_fingerprint}:{collection_type}-page:{page_num}"))
            filename = f"{collection_type}_{safe_fingerprint}_page_{page_num}.png"
            output_path = out_dir / filename
            page = pdf.load_page(page_num - 1)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            pix.save(str(output_path))
            page_to_image[page_num] = {
                "id": image_id,
                "filename": filename,
                "filepath": f"{relative_root}/{filename}",
            }
    finally:
        pdf.close()

    conn = pymysql.connect(**_require_db_env())
    try:
        with conn.cursor() as cursor:
            for batch in _chunked(document_ids):
                placeholders = ",".join(["%s"] * len(batch))
                cursor.execute(f"DELETE FROM manual_image WHERE document_id IN ({placeholders})", batch)

            for page_num, image in page_to_image.items():
                cursor.execute(
                    """
                    INSERT INTO image_file (id, filename, filepath, upload_time)
                    VALUES (%s, %s, %s, UTC_TIMESTAMP())
                    ON DUPLICATE KEY UPDATE
                        filename = VALUES(filename),
                        filepath = VALUES(filepath)
                    """,
                    (image["id"], image["filename"], image["filepath"]),
                )

            link_count = 0
            for document_id, page_num in record_pages:
                image_id = page_to_image[page_num]["id"]
                link_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{document_id}:{collection_type}-image-page:{page_num}"))
                cursor.execute(
                    """
                    INSERT INTO manual_image (id, document_id, image_id, page_num)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        image_id = VALUES(image_id),
                        page_num = VALUES(page_num)
                    """,
                    (link_id, document_id, image_id, page_num),
                )
                link_count += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"       Images  [{collection_type}]: {len(page_to_image)} 页, manual_image 映射 {link_count} 条")
    return {"image_count": len(page_to_image), "link_count": link_count}

# ------------------------------------------------------------------ #
# 4. 记录构建辅助函数
# ------------------------------------------------------------------ #

def _build_chapter_records(
    texts, metadatas, slice_files_list, model, source_file, ingested_at,
    collection_name, document_fingerprint,
    device_tags=None,
):
    """
    把 parse() 的章节级结果转换为入库 records。
    id = stable uuid5(collection_name|source_file|document_fingerprint|model|chapter|slice_id)
    chunk_type = "chapter"
    slice_pdf_path = 相对路径（已由 _create_slice_pdfs 写入 metadata）
    """
    records = []
    for i, (text, meta) in enumerate(zip(texts, metadatas)):
        slice_id = meta.get("slice_id", str(i))
        record_id = build_ingestion_document_id(
            collection_name=collection_name,
            source_file=source_file,
            local_key=str(slice_id),
            model=model,
            namespace="chapter",
            document_fingerprint=document_fingerprint,
        )
        records.append(
            {
                "id": record_id,
                "text": text,
                "model": model,
                "title": meta.get("title", ""),
                "slice_id": slice_id,
                "section_number": slice_id,          # 章节级：slice_id 即章节号
                "page_start": int(meta.get("page_start", 0)),
                "page_end": int(meta.get("page_end", 0)),
                "source_file": source_file,
                "slice_pdf_path": (slice_files_list[i] or "") if i < len(slice_files_list) else "",
                "chunk_type": "chapter",
                "ingested_at": ingested_at,
                "manual_type": meta.get("manual_type", ""),
                **(device_tags or {}),
            }
        )
    return records


def _build_graph_chunk_records(
    chunks, graph_results, vectors_map,
    toc_items, slice_files, model, source_file, ingested_at, window, overlap,
    collection_name, document_fingerprint,
    device_tags=None,
):
    """
    GraphRAG 模式：把 chunks 的实体/关系/总结转换为多条 Milvus 记录。

    1个 chunk → 1条 summary + N条 entity + M条 relation

    Args:
        chunks:        sliding_window_chunks() 的原始 chunks
        graph_results: extract_chunks_graph_batch() 返回的结构化结果
        vectors_map:   {text: vector} 映射（所有待 embedding 的文本及其向量）
        toc_items:     目录结构
        slice_files:   章节子 PDF 映射
        ...
    """
    import re as _re

    records = []
    for i, (chunk, graph) in enumerate(zip(chunks, graph_results)):
        if graph is None:
            continue

        page_start = chunk["page_start"]
        page_end = chunk["page_end"]
        slice_id = chunk["slice_id"]
        section_number = chunk.get("section_number", "")
        chapter_title = chunk.get("chapter_title", "")
        chapter_pdf = find_chapter_pdf(page_start, toc_items, slice_files)

        # PDF 原文（2页拼接），所有子记录共享
        raw_text = "\n\n".join(chunk.get("page_texts") or [])

        # 公共 metadata 字段（所有子记录继承）
        base_meta = {
            "model": model,
            "title": chapter_title,
            "slice_id": slice_id,
            "section_number": section_number,
            "page_start": page_start,
            "page_end": page_end,
            "source_file": source_file,
            "slice_pdf_path": chapter_pdf or "",
            "chunk_type": "sliding_window",
            "window": window,
            "overlap": overlap,
            "ingested_at": ingested_at,
            "manual_type": f"{model}机型操作手册",
            **(device_tags or {}),
        }

        # --- summary 记录 ---
        summary_text = graph.get("summary", "")
        if summary_text and summary_text in vectors_map:
            record_id = build_ingestion_document_id(
                collection_name=collection_name,
                source_file=source_file,
                local_key=f"{slice_id}|summary",
                model=model,
                namespace="chunk",
                document_fingerprint=document_fingerprint,
            )
            records.append({
                "id": record_id,
                "text": summary_text,
                "vector": vectors_map[summary_text],
                **base_meta,
                "entity_type": EntityType.SUMMARY,
                "chunk_raw_text": raw_text,
            })

        # --- entity 记录 ---
        for ent in graph.get("entities", []):
            name = (ent.get("name") or "").strip()
            desc = (ent.get("description") or "").strip()
            if not name:
                continue
            embed_text = f"{name}: {desc}" if desc else name
            if embed_text not in vectors_map:
                continue
            record_id = build_ingestion_document_id(
                collection_name=collection_name,
                source_file=source_file,
                local_key=f"{slice_id}|entity|{name}",
                model=model,
                namespace="chunk",
                document_fingerprint=document_fingerprint,
            )
            records.append({
                "id": record_id,
                "text": embed_text,
                "vector": vectors_map[embed_text],
                **base_meta,
                "entity_type": EntityType.ENTITY,
                "entity_name": name,
                "entity_category": ent.get("type", ""),
                "chunk_raw_text": raw_text,
            })

        # --- relation 记录 ---
        for j, rel in enumerate(graph.get("relations", [])):
            desc = (rel.get("description") or "").strip()
            if not desc or desc not in vectors_map:
                continue
            record_id = build_ingestion_document_id(
                collection_name=collection_name,
                source_file=source_file,
                local_key=f"{slice_id}|relation|{j}",
                model=model,
                namespace="chunk",
                document_fingerprint=document_fingerprint,
            )
            records.append({
                "id": record_id,
                "text": desc,
                "vector": vectors_map[desc],
                **base_meta,
                "entity_type": EntityType.RELATION,
                "rel_src": rel.get("src", ""),
                "rel_type": rel.get("rel", ""),
                "rel_tgt": rel.get("tgt", ""),
                "chunk_raw_text": raw_text,
            })

    return records


def _write_to_dbs(
    records, milvus_collection, mongo_collection, es_index, embedding_dim, label
):
    """写入三库，返回 (n_milvus, n_mongo, n_es, errors_dict)。"""
    expected = len(records)
    if expected == 0:
        return 0, 0, 0, {}

    for target_name, target_value in (
        ("Milvus collection", milvus_collection),
        ("Mongo collection", mongo_collection),
        ("ES index", es_index),
    ):
        if not re.fullmatch(r"[A-Za-z0-9_]+", str(target_value or "")):
            raise ValueError(f"{target_name} 非法: {target_value}")

    # Milvus
    milvus = MilvusWriter()
    try:
        milvus.ensure_collection(milvus_collection, dim=embedding_dim)
        n_milvus = milvus.upsert(milvus_collection, records)
    finally:
        milvus.close()
    print(f"       Milvus  [{label}]: {n_milvus} 条")
    if n_milvus != expected:
        raise RuntimeError(f"Milvus 写入数量不一致 [{label}]: expected={expected}, actual={n_milvus}")

    # MongoDB
    mongo = MongoWriter()
    try:
        n_mongo = mongo.upsert_chunks(mongo_collection, records)
    finally:
        mongo.close()
    print(f"       MongoDB [{label}]: {n_mongo} 条")
    if n_mongo != expected:
        raise RuntimeError(f"MongoDB 写入数量不一致 [{label}]: expected={expected}, actual={n_mongo}")

    # ES
    es = ESWriter()
    try:
        es.ensure_index(es_index)
        n_es = es.upsert(es_index, records)
    finally:
        es.close()
    print(f"       ES      [{label}]: {n_es} 条")
    if n_es != expected:
        raise RuntimeError(f"ES 写入数量不一致 [{label}]: expected={expected}, actual={n_es}")

    return n_milvus, n_mongo, n_es, {}


# ------------------------------------------------------------------ #
# 5. 核心入库函数
# ------------------------------------------------------------------ #

def _ingest_chapter_layer(
    pdf_path, model_name, _out_dir, source_file, ingested_at,
    chapter_collection, embedding_dim, drop_collections, chunks_collection,
    document_fingerprint,
    device_tags=None,
    progress_callback=None,
):
    """Step 0-3：解析 PDF → 章节 Embedding → 写入章节级三库。"""

    def _progress(step, name, detail="", current=None, total=None):
        if progress_callback:
            progress_callback(step=step, name=name, detail=detail, current=current, total=total)

    # Step 0: Drop collections（schema 迁移时使用）
    if drop_collections:
        print(f"[0/9] --drop-collections: 删除 Milvus collections 并重建...")
        try:
            milvus = MilvusWriter()
            for col in [chapter_collection, chunks_collection]:
                dropped = milvus.drop_collection(col)
                print(f"       {'已删除' if dropped else '不存在'}: {col}")
            milvus.close()
        except Exception as e:
            logger.error(f"drop_collections 失败: {e}", exc_info=True)
            print(f"  [ERROR] drop_collections: {e}")

    # Step 1: 全量解析
    t_step = time.time()
    _progress(1, "PDF解析", f"解析 {Path(pdf_path).name}")
    print(f"\n[1/9] 全量解析（TOC + 章节子 PDF + 章节描述）: {pdf_path}")
    parser = ManualParserStandalone(output_dir=str(_out_dir))
    parse_result = parser.parse(pdf_path, model_name=model_name)

    if not parse_result.get("success"):
        msg = parse_result.get("msg", "PDF 解析失败")
        print(f"[ERROR] {msg}")
        return {"success": False, "msg": msg}

    texts = parse_result["texts"]
    metadatas = parse_result["metadatas"]
    slice_files_list = parse_result.get("slice_files", [])

    _model = model_name or (metadatas[0].get("model", "") if metadatas else "") or Path(pdf_path).stem
    print(f"[1/9] 解析完成: {len(texts)} 章节, 机型={_model}")
    logger.info(f"[1/9] 解析完成: {len(texts)} 章节, model={_model}, 耗时 {time.time()-t_step:.1f}s")

    # 从 metadatas 重建 toc_items 和 slice_files（供滑窗层使用）
    toc_items = [
        {
            "title": m.get("title", ""),
            "section_number": m.get("slice_id", ""),
            "target_page": m.get("page_start", 1),
            "page_start": m.get("page_start", 0),
            "page_end": m.get("page_end", 0),
        }
        for m in metadatas
    ]
    slice_files = {
        m.get("slice_id", ""): f
        for m, f in zip(metadatas, slice_files_list)
        if f
    }

    # Step 2: 章节级 Embedding
    t_step = time.time()
    _progress(2, "章节Embedding", f"{len(texts)} 条", current=0, total=len(texts))
    print(f"[2/9] 章节级 Embedding ({len(texts)} 条)...")
    chapter_vectors = get_embeddings_batch(
        texts,
        show_progress=True,
        progress_callback=lambda stats: _progress(
            2,
            "章节Embedding",
            f"批次 {stats['batch_index']}/{stats['total_batches']}，已完成 {stats['done']}/{stats['total']} 条",
            current=stats["done"],
            total=stats["total"],
        ),
    )
    if chapter_vectors is None:
        print("[ERROR] 章节 Embedding 失败")
        return {"success": False, "msg": "章节 Embedding 失败"}

    valid_chap = [i for i, v in enumerate(chapter_vectors) if v is not None]
    if len(valid_chap) < len(texts):
        print(f"[WARN] {len(texts) - len(valid_chap)} 章节 Embedding 失败，已跳过")
    print(f"[2/9] 章节 Embedding 完成: {len(valid_chap)} 条")
    logger.info(f"[2/9] Embedding 完成: {len(valid_chap)} 条, 耗时 {time.time()-t_step:.1f}s")

    # Step 3: 写入章节级三库
    t_step = time.time()
    _progress(3, "写入章节库", f"写入 {chapter_collection}")
    print(f"[3/9] 写入章节级数据库 ({chapter_collection})...")
    chap_records_all = _build_chapter_records(
        texts, metadatas, slice_files_list, _model, source_file, ingested_at,
        chapter_collection, document_fingerprint,
        device_tags=device_tags,
    )
    chap_records = [
        {**chap_records_all[i], "vector": chapter_vectors[i]}
        for i in valid_chap
    ]
    n_chap_milvus, n_chap_mongo, n_chap_es, chap_errors = _write_to_dbs(
        chap_records,
        milvus_collection=chapter_collection,
        mongo_collection=chapter_collection,
        es_index=chapter_collection,
        embedding_dim=embedding_dim,
        label="章节",
    )
    logger.info(f"[3/9] 写入完成: Milvus={n_chap_milvus}, Mongo={n_chap_mongo}, ES={n_chap_es}, 耗时 {time.time()-t_step:.1f}s")

    return {
        "success": True,
        "model": _model,
        "toc_items": toc_items,
        "slice_files": slice_files,
        "chap_records": chap_records,
        "n_chap_milvus": n_chap_milvus,
        "n_chap_mongo": n_chap_mongo,
        "n_chap_es": n_chap_es,
        "chap_errors": chap_errors,
    }


def _ingest_chunk_layer(
    pdf_path, toc_items, slice_files, _model, source_file, ingested_at,
    window, overlap, chunks_collection, embedding_dim,
    document_fingerprint,
    device_tags=None,
    progress_callback=None,
):
    """Step 4-9：滑窗分块 → GraphRAG 提取 → Embedding → 写入滑窗级三库。"""

    def _progress(step, name, detail="", current=None, total=None):
        if progress_callback:
            progress_callback(step=step, name=name, detail=detail, current=current, total=total)

    # Step 4: 滑动窗口分块
    t_step = time.time()
    _progress(4, "滑窗分块", f"window={window}, overlap={overlap}")
    print(f"[4/9] 滑窗分块 (window={window}, overlap={overlap})...")
    chunks = sliding_window_chunks(
        pdf_path_or_pages=pdf_path, toc_items=toc_items,
        window=window, overlap=overlap,
    )
    if not chunks:
        print("[ERROR] 滑窗分块结果为空")
        return {"success": False, "msg": "滑窗分块结果为空"}
    print(f"[4/9] 分块完成: {len(chunks)} 个 chunk")
    logger.info(f"[4/9] 分块完成: {len(chunks)} chunk, 耗时 {time.time()-t_step:.1f}s")

    # Step 5: GraphRAG 实体关系提取
    t_step = time.time()
    graph_workers = max(1, int(getattr(LLMConfig, "PDF_GRAPHRAG_MAX_WORKERS", 6) or 1))
    _progress(
        5,
        "GraphRAG提取",
        f"completed=0/{len(chunks)} success=0 text_fallback=0 visual_fallback=0 framework_failures=0",
        current=0,
        total=len(chunks),
    )
    print(f"[5/9] GraphRAG 实体关系提取 ({len(chunks)} 个 chunk, workers={graph_workers})...")

    def _graph_progress(batch_stats):
        _progress(5, "GraphRAG提取", (
            f"completed={batch_stats['completed_chunks']}/{batch_stats['total_chunks']} "
            f"success={batch_stats['success_chunks']} "
            f"text_fallback={batch_stats['text_fallback_chunks']} "
            f"visual_fallback={batch_stats['visual_fallback_chunks']} "
            f"framework_failures={batch_stats['framework_failures']}"
        ), current=batch_stats["completed_chunks"], total=batch_stats["total_chunks"])

    try:
        graph_results, graph_stats = extract_chunks_graph_batch(
            chunks,
            show_progress=True,
            max_workers=graph_workers,
            progress_callback=_graph_progress,
            pdf_mode=True,
            return_stats=True,
        )
    except Exception as e:
        logger.error(f"[5/9] GraphRAG 并行处理失败: {e}", exc_info=True)
        return {"success": False, "msg": f"GraphRAG 并行处理失败: {e}"}

    n_entities = sum(len(g.get("entities", [])) for g in graph_results)
    n_relations = sum(len(g.get("relations", [])) for g in graph_results)
    print(
        f"[5/9] 提取完成: {n_entities} 实体, {n_relations} 关系 | "
        f"success={graph_stats['success_chunks']} "
        f"text_fallback={graph_stats['text_fallback_chunks']} "
        f"visual_fallback={graph_stats['visual_fallback_chunks']} "
        f"framework_failures={graph_stats['framework_failures']}"
    )
    logger.info(
        f"[5/9] GraphRAG 完成: {n_entities} 实体, {n_relations} 关系, "
        f"success={graph_stats['success_chunks']}, "
        f"text_fallback={graph_stats['text_fallback_chunks']}, "
        f"visual_fallback={graph_stats['visual_fallback_chunks']}, "
        f"framework_failures={graph_stats['framework_failures']}, "
        f"workers={graph_workers}, 耗时 {time.time()-t_step:.1f}s"
    )

    # Step 6: 收集所有需要 embedding 的文本
    _progress(6, "收集Embedding文本")
    print(f"[6/9] 收集 embedding 文本...")
    all_embed_texts = []
    for graph in graph_results:
        summary = (graph.get("summary") or "").strip()
        if summary:
            all_embed_texts.append(summary)
        for ent in graph.get("entities", []):
            name = (ent.get("name") or "").strip()
            desc = (ent.get("description") or "").strip()
            if name:
                all_embed_texts.append(f"{name}: {desc}" if desc else name)
        for rel in graph.get("relations", []):
            desc = (rel.get("description") or "").strip()
            if desc:
                all_embed_texts.append(desc)
    all_embed_texts = list(dict.fromkeys(all_embed_texts))  # 去重
    print(f"[6/9] 去重后 {len(all_embed_texts)} 条文本需要 embedding")

    # Step 7: 批量 Embedding
    t_step = time.time()
    _progress(7, "批量Embedding", f"{len(all_embed_texts)} 条", current=0, total=len(all_embed_texts))
    print(f"[7/9] 批量 Embedding ({len(all_embed_texts)} 条)...")
    all_vectors = get_embeddings_batch(
        all_embed_texts,
        show_progress=True,
        progress_callback=lambda stats: _progress(
            7,
            "批量Embedding",
            f"批次 {stats['batch_index']}/{stats['total_batches']}，已完成 {stats['done']}/{stats['total']} 条",
            current=stats["done"],
            total=stats["total"],
        ),
    )
    if all_vectors is None:
        print("[ERROR] 滑窗 Embedding 失败")
        return {"success": False, "msg": "滑窗 GraphRAG Embedding 失败"}

    vectors_map = {}
    n_embed_ok = 0
    for text, vec in zip(all_embed_texts, all_vectors):
        if vec is not None:
            vectors_map[text] = vec
            n_embed_ok += 1
    if n_embed_ok < len(all_embed_texts):
        print(f"[WARN] {len(all_embed_texts) - n_embed_ok} 条 Embedding 失败，已跳过")
    print(f"[7/9] Embedding 完成: {n_embed_ok} 条")
    logger.info(f"[7/9] Embedding 完成: {n_embed_ok}/{len(all_embed_texts)} 条, 耗时 {time.time()-t_step:.1f}s")

    # Step 8: 构建 Milvus 记录
    _progress(8, "构建记录")
    print(f"[8/9] 构建 GraphRAG Milvus 记录...")
    chunk_records = _build_graph_chunk_records(
        chunks, graph_results, vectors_map,
        toc_items, slice_files, _model, source_file, ingested_at, window, overlap,
        chunks_collection, document_fingerprint,
        device_tags=device_tags,
    )
    n_summary = sum(1 for r in chunk_records if r.get("entity_type") == EntityType.SUMMARY)
    n_ent_rec = sum(1 for r in chunk_records if r.get("entity_type") == EntityType.ENTITY)
    n_rel_rec = sum(1 for r in chunk_records if r.get("entity_type") == EntityType.RELATION)
    print(f"[8/9] 记录构建完成: {n_summary} summary + {n_ent_rec} entity + {n_rel_rec} relation = {len(chunk_records)} 条")
    logger.info(f"[8/9] 记录: {n_summary}s + {n_ent_rec}e + {n_rel_rec}r = {len(chunk_records)} 条")

    # Step 9: 写入滑窗级三库
    t_step = time.time()
    _progress(9, "写入滑窗库", f"写入 {chunks_collection}")
    print(f"[9/9] 写入滑窗级数据库 ({chunks_collection})...")
    n_chunk_milvus, n_chunk_mongo, n_chunk_es, chunk_errors = _write_to_dbs(
        chunk_records,
        milvus_collection=chunks_collection,
        mongo_collection=chunks_collection,
        es_index=chunks_collection,
        embedding_dim=embedding_dim,
        label="滑窗(GraphRAG)",
    )
    logger.info(f"[9/9] 写入完成: Milvus={n_chunk_milvus}, Mongo={n_chunk_mongo}, ES={n_chunk_es}, 耗时 {time.time()-t_step:.1f}s")

    return {
        "success": True,
        "chunk_records": chunk_records,
        "n_summary": n_summary,
        "n_ent_rec": n_ent_rec,
        "n_rel_rec": n_rel_rec,
        "n_chunk_milvus": n_chunk_milvus,
        "n_chunk_mongo": n_chunk_mongo,
        "n_chunk_es": n_chunk_es,
        "chunk_errors": chunk_errors,
        "graph_stats": graph_stats,
        "graph_workers": graph_workers,
    }


def _build_and_print_report(
    source_file, _model, elapsed, log_file,
    chap_result, chunk_result, chapter_collection, chunks_collection, window, overlap,
):
    """汇总入库结果并打印报告。"""
    report = {
        "success": True,
        "pdf": source_file,
        "model": _model,
        "chapter": {
            "total": len(chap_result["chap_records"]),
            "collection": chapter_collection,
            "milvus": chap_result["n_chap_milvus"],
            "mongodb": chap_result["n_chap_mongo"],
            "es": chap_result["n_chap_es"],
        },
        "chunks": {
            "total": len(chunk_result["chunk_records"]),
            "summary_count": chunk_result["n_summary"],
            "entity_count": chunk_result["n_ent_rec"],
            "relation_count": chunk_result["n_rel_rec"],
            "graph_workers": chunk_result.get("graph_workers"),
            "graph_stats": chunk_result.get("graph_stats", {}),
            "collection": chunks_collection,
            "window": window,
            "overlap": overlap,
            "milvus": chunk_result["n_chunk_milvus"],
            "mongodb": chunk_result["n_chunk_mongo"],
            "es": chunk_result["n_chunk_es"],
        },
        "elapsed_seconds": elapsed,
        "log_file": str(log_file),
    }
    if chap_result.get("chap_errors"):
        report["chapter"]["errors"] = chap_result["chap_errors"]
    if chunk_result.get("chunk_errors"):
        report["chunks"]["errors"] = chunk_result["chunk_errors"]

    n_chap = chap_result["n_chap_milvus"]
    n_chap_m = chap_result["n_chap_mongo"]
    n_chap_e = chap_result["n_chap_es"]
    n_chunk = chunk_result["n_chunk_milvus"]
    n_chunk_m = chunk_result["n_chunk_mongo"]
    n_chunk_e = chunk_result["n_chunk_es"]

    print(f"\n{'=' * 62}")
    print(f"入库完成 | 耗时 {elapsed}s | 机型 {_model}")
    print(f"  {'层级':<8}  {'collection':<30}  {'Milvus':>6}  {'MongoDB':>7}  {'ES':>4}")
    print(f"  {'-'*58}")
    print(f"  {'章节':<8}  {chapter_collection:<30}  {n_chap:>6}  {n_chap_m:>7}  {n_chap_e:>4}")
    print(f"  {'滑窗':<8}  {chunks_collection:<30}  {n_chunk:>6}  {n_chunk_m:>7}  {n_chunk_e:>4}")
    print(f"{'=' * 62}\n")

    logger.info(f"入库完成: {report}")
    return report


def ingest_manual(
    pdf_path: str,
    model_name: str = None,
    output_dir: str = None,
    window: int = 2,
    overlap: int = 1,
    chapter_collection: str = None,
    chunks_collection: str = None,
    embedding_dim: int = EMBEDDING_DIMENSION,
    drop_collections: bool = False,
    generation: str = None,
    controller: str = None,
    product_line: str = None,
    tonnage: str = None,
    series: str = None,
    progress_callback=None,
    **extra_metadata,
) -> dict:
    """
    完整 PDF 入库（双 collection）。

    章节级 → {chapter_collection}（manual_total，对齐主后端 COLLECTION_MANUAL）
    滑窗级 → {chunks_collection}（manual_chunks）
    MongoDB / ES 使用相同名称作为 collection/index。
    """
    chapter_collection = chapter_collection or COLLECTION_MANUAL_TOTAL
    chunks_collection = chunks_collection or COLLECTION_MANUAL_CHUNKS

    start = time.time()
    _out_dir = Path(output_dir) if output_dir else (
        Path(os.getenv("OUTPUT_DIR", str(_AGENT1_DIR / "output")))
    )
    log_file = _setup_logging(_out_dir / "logs")
    print(f"日志文件: {log_file}")

    source_file = Path(pdf_path).name
    ingested_at = datetime.now(timezone.utc).isoformat()
    document_fingerprint = build_document_fingerprint(pdf_path, source_file)

    # 机型扩展标签与额外 metadata（只保留非空值）
    device_tags = {
        k: v for k, v in {
            "generation": generation or "",
            "controller": controller or "",
            "product_line": product_line or "",
            "tonnage": tonnage or "",
            "series": series or "",
        }.items() if v
    }
    for key, value in extra_metadata.items():
        if value not in (None, "", [], {}):
            device_tags[key] = value
    if "language" not in device_tags:
        device_tags["language"] = "CN"
    if "knowledge_type" not in device_tags:
        device_tags["knowledge_type"] = (
            "principle"
            if chapter_collection == COLLECTION_PRINCIPLE_TOTAL or chunks_collection == COLLECTION_PRINCIPLE_CHUNKS
            else "manual"
        )

    # 章节层：解析 + Embedding + 写库
    chap_result = _ingest_chapter_layer(
        pdf_path, model_name, _out_dir, source_file, ingested_at,
        chapter_collection, embedding_dim, drop_collections, chunks_collection,
        document_fingerprint,
        device_tags=device_tags,
        progress_callback=progress_callback,
    )
    if not chap_result.get("success"):
        return chap_result

    # 滑窗层：分块 + GraphRAG + Embedding + 写库
    chunk_result = _ingest_chunk_layer(
        pdf_path, chap_result["toc_items"], chap_result["slice_files"],
        chap_result["model"], source_file, ingested_at,
        window, overlap, chunks_collection, embedding_dim,
        document_fingerprint,
        device_tags=device_tags,
        progress_callback=progress_callback,
    )
    if not chunk_result.get("success"):
        return chunk_result

    # 汇总报告
    image_collection_type = _page_image_collection_type(chapter_collection, chunks_collection)
    if image_collection_type:
        image_result = _write_page_images(
            pdf_path,
            document_fingerprint,
            chap_result.get("chap_records", []) + chunk_result.get("chunk_records", []),
            image_collection_type,
        )
        chap_result["manual_image_count"] = image_result["image_count"]
        chunk_result["manual_image_link_count"] = image_result["link_count"]

    elapsed = round(time.time() - start, 2)
    return _build_and_print_report(
        source_file, chap_result["model"], elapsed, log_file,
        chap_result, chunk_result, chapter_collection, chunks_collection, window, overlap,
    )


# ------------------------------------------------------------------ #
# 6. CLI
# ------------------------------------------------------------------ #

def main():
    p = argparse.ArgumentParser(
        description="PDF 说明书入库 Agent（双 collection 架构）",
    )
    p.add_argument("--pdf",    required=True,  help="PDF 文件路径")
    p.add_argument("--model",  default=None,   help="机型名称（默认从文件名推断）")
    p.add_argument("--output", default=None,   help="输出目录（默认 ./output 或 $OUTPUT_DIR）")
    p.add_argument("--window", type=int, default=2, help="滑窗页数（默认 2）")
    p.add_argument("--overlap", type=int, default=1, help="重叠页数（默认 1）")
    p.add_argument("--chapter-collection", default=None,
                   help="章节级 collection（默认 $COLLECTION_MANUAL 或 manual_total）")
    p.add_argument("--chunks-collection",  default=None,
                   help="滑窗级 collection（默认 $COLLECTION_MANUAL_CHUNKS 或 manual_chunks）")
    p.add_argument("--drop-collections", action="store_true",
                   help="入库前先删除并重建 Milvus collections（schema 迁移时使用，会清空旧数据）")
    # 机型扩展标签（人工填写，存入每条记录的 metadata）
    p.add_argument("--generation",   default=None, help="代次，如 一代/二代/三代")
    p.add_argument("--controller",   default=None, help="控制器型号")
    p.add_argument("--product-line", default=None, help="产品线，如 二板机/三板机/全电机")
    p.add_argument("--tonnage",      default=None, help="锁模力（吨），如 650/1600/3600")
    p.add_argument("--series",       default=None, help="机型系列，如 MA/MG/HTF")
    args = p.parse_args()

    if args.overlap >= args.window:
        p.error(f"--overlap ({args.overlap}) 必须小于 --window ({args.window})")

    result = ingest_manual(
        pdf_path=args.pdf,
        model_name=args.model,
        output_dir=args.output,
        window=args.window,
        overlap=args.overlap,
        chapter_collection=args.chapter_collection,
        chunks_collection=args.chunks_collection,
        drop_collections=args.drop_collections,
        generation=args.generation,
        controller=args.controller,
        product_line=args.product_line,
        tonnage=args.tonnage,
        series=args.series,
    )
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
