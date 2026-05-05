#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
word_ingest_agent.py — Word 文档入库 Agent（GraphRAG 架构）

把 Word 文档按"虚拟页"切分，复用 PDF 入库的 GraphRAG 流程。
虚拟页 = 按 ~500 字切出的文本段，模拟 PDF 的物理页。

有标题（Heading 1/2/3）→ 双 collection：manual_total + manual_chunks
没有标题              → 单 collection：manual_chunks

用法：详细参数见 --help
"""

import os
import sys
import re
import time
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

# ------------------------------------------------------------------ #
# 环境初始化
# ------------------------------------------------------------------ #
_AGENT_DIR = Path(__file__).parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

_PROJECT_ROOT = _AGENT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_AGENT_DIR / ".env")

from docx import Document

from chunker import sliding_window_chunks, find_chapter_pdf
from llm_extractor import extract_chunk_content, extract_chunks_graph_batch
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

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Word 读取 & 虚拟页切分
# ------------------------------------------------------------------ #

def word_to_virtual_pages(
    docx_path: str,
    chars_per_page: int = 500,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    读取 Word 文件，按字数切成虚拟页，同时检测标题样式提取 TOC。

    Args:
        docx_path:      Word 文件路径
        chars_per_page:  每个虚拟页的目标字数

    Returns:
        (pages, toc_items)
        pages:     list[str]  每个虚拟页的文本
        toc_items: list[dict] 章节信息（无标题时为空列表）
            每项: {"title", "section_number", "target_page", "page_start", "page_end"}
    """
    doc = Document(docx_path)

    # ---- 1. 遍历段落，识别标题，累积文本 ---- #
    paragraphs: List[str] = []
    heading_positions: List[Dict[str, Any]] = []  # [{para_index, level, text}]

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        para_idx = len(paragraphs)
        paragraphs.append(text)

        # 检测 Heading 样式
        style_name = (para.style.name or "").lower()
        if style_name.startswith("heading"):
            try:
                level = int(style_name.replace("heading", "").strip())
            except ValueError:
                level = 1
            heading_positions.append({
                "para_index": para_idx,
                "level": level,
                "text": text,
            })

    if not paragraphs:
        return [], []

    # ---- 2. 按字数切虚拟页 ---- #
    pages: List[str] = []
    current_page: List[str] = []
    current_chars = 0
    # 记录每个段落属于第几个虚拟页
    para_to_page: Dict[int, int] = {}

    for i, para_text in enumerate(paragraphs):
        para_to_page[i] = len(pages)

        if current_chars + len(para_text) > chars_per_page and current_page:
            # 当前页满了，存下来开新页
            pages.append("\n".join(current_page))
            current_page = []
            current_chars = 0
            para_to_page[i] = len(pages)  # 更新到新页

        current_page.append(para_text)
        current_chars += len(para_text)

    # 最后一页
    if current_page:
        pages.append("\n".join(current_page))

    # ---- 3. 根据标题位置构建 toc_items ---- #
    toc_items: List[Dict[str, Any]] = []

    if heading_positions:
        heading_counter = 0
        for h in heading_positions:
            heading_counter += 1
            page_idx = para_to_page.get(h["para_index"], 0)

            # section_number: 尝试从标题文本提取编号，没有则用序号
            match = re.match(r'^(\d+(?:\.\d+){0,3})\b\s*(.*)', h["text"])
            if match:
                section_number = match.group(1)
                title = match.group(2).strip() or h["text"]
            else:
                section_number = str(heading_counter)
                title = h["text"]

            toc_items.append({
                "title": title,
                "section_number": section_number,
                "target_page": page_idx + 1,  # 1-based
                "page_start": page_idx + 1,
                "page_end": 0,  # 后面填充
            })

        # 填充 page_end
        for i in range(len(toc_items)):
            if i < len(toc_items) - 1:
                toc_items[i]["page_end"] = toc_items[i + 1]["target_page"] - 1
            else:
                toc_items[i]["page_end"] = len(pages)

    logger.info(
        f"Word 虚拟页切分完成: {len(pages)} 页, "
        f"{len(toc_items)} 个章节标题, 文件={Path(docx_path).name}"
    )
    return pages, toc_items


# ------------------------------------------------------------------ #
# 记录构建（复用 manual_ingest_agent 的逻辑）
# ------------------------------------------------------------------ #

# 直接导入 manual_ingest_agent 的构建函数和写入函数
from manual_ingest_agent import (
    _build_chapter_records,
    _build_graph_chunk_records,
    _write_to_dbs,
)
from document_identity import build_document_fingerprint


# ------------------------------------------------------------------ #
# 核心入库函数
# ------------------------------------------------------------------ #

def ingest_word(
    docx_path: str,
    model_name: str,
    window: int = 2,
    overlap: int = 1,
    chars_per_page: int = 500,
    chapter_collection: str = None,
    chunks_collection: str = None,
    embedding_dim: int = EMBEDDING_DIMENSION,
    drop_collections: bool = False,
    progress_callback=None,
    **device_tags,
) -> dict:
    """
    Word 文档入库（GraphRAG 架构）。

    有标题 → 9 步流程，写 manual_total + manual_chunks
    无标题 → 5 步流程，只写 manual_chunks
    """
    chapter_collection = chapter_collection or COLLECTION_MANUAL_TOTAL
    chunks_collection = chunks_collection or COLLECTION_MANUAL_CHUNKS
    device_tags = {k: v for k, v in device_tags.items() if v}
    if "knowledge_type" not in device_tags:
        device_tags["knowledge_type"] = (
            "principle"
            if chapter_collection == COLLECTION_PRINCIPLE_TOTAL or chunks_collection == COLLECTION_PRINCIPLE_CHUNKS
            else "manual"
        )

    start = time.time()
    source_file = Path(docx_path).name
    ingested_at = datetime.now(timezone.utc).isoformat()
    document_fingerprint = build_document_fingerprint(docx_path, source_file)

    def _progress(step, name, detail="", current=None, total=None):
        if progress_callback:
            progress_callback(step=step, name=name, detail=detail, current=current, total=total)

    # ==== Step 1: 读取 Word → 虚拟页 =================================== #
    t_step = time.time()
    _progress(1, "读取Word", source_file)
    print(f"[1/9] 读取 Word 文档: {docx_path}")

    try:
        pages, toc_items = word_to_virtual_pages(docx_path, chars_per_page=chars_per_page)
    except Exception as e:
        msg = f"Word 读取失败: {e}"
        print(f"[ERROR] {msg}")
        return {"success": False, "msg": msg}

    if not pages:
        msg = "Word 文档内容为空"
        print(f"[ERROR] {msg}")
        return {"success": False, "msg": msg}

    has_toc = len(toc_items) > 0
    total_steps = 9 if has_toc else 5
    print(f"[1/{total_steps}] 虚拟页: {len(pages)} 页, 章节: {len(toc_items)} 个, 模式={'有目录' if has_toc else '无目录'}")
    logger.info(f"[1/{total_steps}] Word 解析完成: {len(pages)} 虚拟页, {len(toc_items)} 章节, 耗时 {time.time()-t_step:.1f}s")

    # ==== 有目录：章节层（Steps 2-3）==================================== #
    if has_toc:
        # Step 2: 章节描述 Embedding
        _progress(2, "章节Embedding", f"{len(toc_items)} 章", current=0, total=len(toc_items))
        print(f"[2/{total_steps}] 章节描述生成 + Embedding...")

        chapter_texts = []
        chapter_metadatas = []
        for item in toc_items:
            # 取该章节覆盖的虚拟页文本
            ps = item["page_start"] - 1  # 0-based
            pe = item.get("page_end", len(pages))
            chapter_content = "\n\n".join(pages[ps:pe])

            # 用 v1 提炼章节描述
            desc = extract_chunk_content(
                page_texts=[chapter_content],
                chapter_title=item["title"],
                section_number=item["section_number"],
            )
            chapter_texts.append(desc)
            chapter_metadatas.append({
                "title": item["title"],
                "slice_id": item["section_number"],
                "page_start": item["page_start"],
                "page_end": item.get("page_end", 0),
                "manual_type": f"{model_name}机型操作手册",
            })

        chapter_vectors = get_embeddings_batch(
            chapter_texts,
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
        print(f"[2/{total_steps}] 章节 Embedding 完成: {len(valid_chap)} 条")

        # Step 3: 写入章节级三库
        _progress(3, "写入章节库", chapter_collection)
        print(f"[3/{total_steps}] 写入章节级数据库 ({chapter_collection})...")

        chap_records_all = _build_chapter_records(
            chapter_texts, chapter_metadatas, [""] * len(chapter_texts),
            model_name, source_file, ingested_at, device_tags=device_tags,
            collection_name=chapter_collection,
            document_fingerprint=document_fingerprint,
        )
        chap_records = [
            {**chap_records_all[i], "vector": chapter_vectors[i]}
            for i in valid_chap
        ]

        if drop_collections:
            try:
                milvus = MilvusWriter()
                for col in [chapter_collection, chunks_collection]:
                    dropped = milvus.drop_collection(col)
                    print(f"       {'已删除' if dropped else '不存在'}: {col}")
                milvus.close()
            except Exception as e:
                logger.error(f"drop_collections 失败: {e}", exc_info=True)

        _write_to_dbs(
            chap_records,
            milvus_collection=chapter_collection,
            mongo_collection=chapter_collection,
            es_index=chapter_collection,
            embedding_dim=embedding_dim,
            label="章节",
        )

    # ==== 滑窗层（Steps 4-9 或 1-5）==================================== #
    chunk_step_offset = 0 if has_toc else -3  # 无目录时步骤号前移

    # Step 4: 滑窗分块（传虚拟页列表，不传 pdf_path）
    step4 = 4 + chunk_step_offset
    _progress(step4, "滑窗分块", f"window={window}, overlap={overlap}")
    print(f"[{step4}/{total_steps}] 滑窗分块...")

    chunks = sliding_window_chunks(
        pdf_path_or_pages=pages,
        toc_items=toc_items,
        window=window,
        overlap=overlap,
    )
    if not chunks:
        return {"success": False, "msg": "滑窗分块结果为空"}
    print(f"[{step4}/{total_steps}] 分块完成: {len(chunks)} 个 chunk")

    # Step 5: GraphRAG 提取
    t_step = time.time()
    step5 = 5 + chunk_step_offset
    _progress(step5, "GraphRAG提取", f"{len(chunks)} 个 chunk", current=0, total=len(chunks))
    print(f"[{step5}/{total_steps}] GraphRAG 实体关系提取...")

    graph_workers = max(1, int(getattr(LLMConfig, "PDF_GRAPHRAG_MAX_WORKERS", 6) or 1))

    def _graph_progress(batch_stats):
        _progress(
            step5,
            "GraphRAG提取",
            (
                f"completed={batch_stats['completed_chunks']}/{batch_stats['total_chunks']} "
                f"success={batch_stats['success_chunks']} "
                f"text_fallback={batch_stats['text_fallback_chunks']} "
                f"visual_fallback={batch_stats['visual_fallback_chunks']} "
                f"framework_failures={batch_stats['framework_failures']}"
            ),
            current=batch_stats["completed_chunks"],
            total=batch_stats["total_chunks"],
        )

    graph_results, graph_stats = extract_chunks_graph_batch(
        chunks,
        show_progress=True,
        max_workers=graph_workers,
        progress_callback=_graph_progress,
        return_stats=True,
    )
    n_entities = sum(len(g.get("entities", [])) for g in graph_results)
    n_relations = sum(len(g.get("relations", [])) for g in graph_results)
    print(f"[{step5}/{total_steps}] 提取完成: {n_entities} 实体, {n_relations} 关系")
    logger.info(f"[{step5}/{total_steps}] GraphRAG 完成: {n_entities} 实体, {n_relations} 关系, 耗时 {time.time()-t_step:.1f}s")

    # Step 6: 收集 Embedding 文本
    step6 = 6 + chunk_step_offset
    _progress(step6, "收集Embedding文本")
    print(f"[{step6}/{total_steps}] 收集 embedding 文本...")

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
    all_embed_texts = list(dict.fromkeys(all_embed_texts))
    print(f"[{step6}/{total_steps}] 去重后 {len(all_embed_texts)} 条")

    # Step 7: 批量 Embedding
    t_step = time.time()
    step7 = 7 + chunk_step_offset
    _progress(step7, "批量Embedding", f"{len(all_embed_texts)} 条", current=0, total=len(all_embed_texts))
    print(f"[{step7}/{total_steps}] 批量 Embedding...")

    all_vectors = get_embeddings_batch(
        all_embed_texts,
        show_progress=True,
        progress_callback=lambda stats: _progress(
            step7,
            "批量Embedding",
            f"批次 {stats['batch_index']}/{stats['total_batches']}，已完成 {stats['done']}/{stats['total']} 条",
            current=stats["done"],
            total=stats["total"],
        ),
    )
    if all_vectors is None:
        return {"success": False, "msg": "滑窗 Embedding 失败"}

    vectors_map = {}
    for text, vec in zip(all_embed_texts, all_vectors):
        if vec is not None:
            vectors_map[text] = vec
    print(f"[{step7}/{total_steps}] Embedding 完成: {len(vectors_map)} 条")
    logger.info(f"[{step7}/{total_steps}] Embedding 完成: {len(vectors_map)} 条, 耗时 {time.time()-t_step:.1f}s")

    # Step 8: 构建记录
    step8 = 8 + chunk_step_offset
    _progress(step8, "构建记录")
    print(f"[{step8}/{total_steps}] 构建 GraphRAG 记录...")

    chunk_records = _build_graph_chunk_records(
        chunks, graph_results, vectors_map,
        toc_items, {}, model_name, source_file, ingested_at, window, overlap,
        collection_name=chunks_collection,
        document_fingerprint=document_fingerprint,
        device_tags=device_tags,
    )
    n_summary = sum(1 for r in chunk_records if r.get("entity_type") == EntityType.SUMMARY)
    n_ent_rec = sum(1 for r in chunk_records if r.get("entity_type") == EntityType.ENTITY)
    n_rel_rec = sum(1 for r in chunk_records if r.get("entity_type") == EntityType.RELATION)
    print(f"[{step8}/{total_steps}] {n_summary} summary + {n_ent_rec} entity + {n_rel_rec} relation = {len(chunk_records)} 条")

    # Step 9: 写入滑窗级三库
    step9 = 9 + chunk_step_offset
    _progress(step9, "写入滑窗库", chunks_collection)
    print(f"[{step9}/{total_steps}] 写入滑窗级数据库 ({chunks_collection})...")

    _write_to_dbs(
        chunk_records,
        milvus_collection=chunks_collection,
        mongo_collection=chunks_collection,
        es_index=chunks_collection,
        embedding_dim=embedding_dim,
        label="滑窗(GraphRAG)",
    )

    elapsed = round(time.time() - start, 2)
    _progress(step9, "完成", f"耗时 {elapsed}s")

    report = {
        "success": True,
        "source_file": source_file,
        "model": model_name,
        "has_toc": has_toc,
        "virtual_pages": len(pages),
        "chapters": len(toc_items),
        "chunk_records": len(chunk_records),
        "summary_count": n_summary,
        "entity_count": n_ent_rec,
        "relation_count": n_rel_rec,
        "graph_stats": graph_stats,
        "graph_workers": graph_workers,
        "elapsed_seconds": elapsed,
    }

    print(f"\n{'=' * 50}")
    print(f"Word 入库完成 | {source_file} | {model_name}")
    print(f"  模式: {'有目录(9步)' if has_toc else '无目录(5步)'}")
    print(f"  虚拟页: {len(pages)} | 章节: {len(toc_items)} | 记录: {len(chunk_records)}")
    print(f"  耗时: {elapsed}s")
    print(f"{'=' * 50}\n")

    return report


# ------------------------------------------------------------------ #
# CLI
# ------------------------------------------------------------------ #

def main():
    p = argparse.ArgumentParser(
        description="Word 文档入库 Agent（GraphRAG 架构）",
    )
    p.add_argument("--docx", required=True, help="Word 文件路径（.docx）")
    p.add_argument("--model", required=True, help="机型名称")
    p.add_argument("--window", type=int, default=2, help="滑窗虚拟页数（默认 2）")
    p.add_argument("--overlap", type=int, default=1, help="重叠虚拟页数（默认 1）")
    p.add_argument("--chars-per-page", type=int, default=500, help="每虚拟页字数（默认 500）")
    p.add_argument("--drop-collections", action="store_true", help="入库前删除旧 collections")
    # 机型扩展标签
    p.add_argument("--series", default=None)
    p.add_argument("--generation", default=None)
    p.add_argument("--controller", default=None)
    p.add_argument("--product-line", default=None)
    p.add_argument("--tonnage", default=None)
    args = p.parse_args()

    if args.overlap >= args.window:
        p.error(f"--overlap ({args.overlap}) 必须小于 --window ({args.window})")

    result = ingest_word(
        docx_path=args.docx,
        model_name=args.model,
        window=args.window,
        overlap=args.overlap,
        chars_per_page=args.chars_per_page,
        drop_collections=args.drop_collections,
        series=args.series,
        generation=args.generation,
        controller=args.controller,
        product_line=args.product_line,
        tonnage=args.tonnage,
    )
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
