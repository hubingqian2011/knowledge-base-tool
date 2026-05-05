# -*- coding: utf-8 -*-
"""
llm_extractor.py — 基于 LLM 的 chunk 内容提炼器

v1（保留）：把 2 页原始文本提炼为 100-200 字泛化总结。
v2（新增）：GraphRAG 模式——从 2 页原始文本中提取实体、关系和总结的结构化 JSON。

公开接口
--------
extract_chunk_content(...)       -> str           # v1 泛化总结（保留，向后兼容）
extract_chunks_batch(...)        -> list[str]     # v1 批量（保留，向后兼容）
extract_chunk_graph(...)         -> dict          # v2 GraphRAG 结构化提取
extract_chunks_graph_batch(...)  -> list[dict]    # v2 批量
"""

import sys
import os
import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Callable

import fitz  # PyMuPDF
from tqdm import tqdm

_AGENT1_DIR = Path(__file__).parent
if str(_AGENT1_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT1_DIR))

from src.llm_client import call_llm_text, call_llm_visual

logger = logging.getLogger(__name__)

# 最大文本长度（超出截断，避免超 token 限制）
_MAX_TEXT_CHARS = 3000

# ── Prompt 加载（从外部 .txt 文件读取，便于非工程师调优）────────────
_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(filename: str) -> str:
    return (_PROMPTS_DIR / filename).read_text(encoding="utf-8")


_SYSTEM_PROMPT = _load_prompt("chunk_summary_system.txt")
_CHUNK_SUMMARY_USER_TEMPLATE = _load_prompt("chunk_summary_user.txt")

_VISUAL_SYSTEM_PROMPT = (
    "你是注塑机技术图纸理解专家。"
    "请描述图纸的主要内容：液压/电气回路的连接关系、标注的部件名称和编号、关键参数值。"
    "输出100-150字，只输出描述文字。"
)


def _sanitize_path_component(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return text.strip("._-") or "chunk"


def _build_chunk_context(chunk: dict, chunk_index: Optional[int] = None) -> str:
    parts = []
    if chunk_index is not None:
        parts.append(f"index={chunk_index}")
    slice_id = str(chunk.get("slice_id", "")).strip()
    if slice_id:
        parts.append(f"slice_id={slice_id}")
    section_number = str(chunk.get("section_number", "")).strip()
    if section_number:
        parts.append(f"section={section_number}")
    page_start = chunk.get("page_start")
    page_end = chunk.get("page_end")
    if page_start or page_end:
        parts.append(f"pages={page_start}-{page_end}")
    return ", ".join(parts) or "unknown_chunk"


def _build_graph_batch_stats(total_chunks: int) -> Dict[str, int]:
    return {
        "total_chunks": total_chunks,
        "completed_chunks": 0,
        "success_chunks": 0,
        "text_fallback_chunks": 0,
        "visual_fallback_chunks": 0,
        "framework_failures": 0,
    }


def _build_progress_detail(stats: Dict[str, int]) -> str:
    return (
        f"completed={stats['completed_chunks']}/{stats['total_chunks']} "
        f"success={stats['success_chunks']} "
        f"text_fallback={stats['text_fallback_chunks']} "
        f"visual_fallback={stats['visual_fallback_chunks']} "
        f"framework_failures={stats['framework_failures']}"
    )


def _generate_visual_description(
    pdf_path: str,
    page_start: int,
    page_end: int,
    *,
    chunk_key: str = "chunk",
) -> Optional[str]:
    """截取页面截图并调用视觉模型描述内容。失败返回 None，不影响主流程。"""
    tmp_dir = Path("/tmp/page_screenshots")
    tmp_files: List[Path] = []
    doc = None
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        doc = fitz.open(pdf_path)
        for p in range(page_start, page_end + 1):
            if p - 1 >= len(doc):
                break
            pix = doc.load_page(p - 1).get_pixmap(dpi=150)
            tmp_path = tmp_dir / (
                f"{_sanitize_path_component(chunk_key)}_"
                f"page_{p}_{uuid.uuid4().hex}.png"
            )
            pix.save(str(tmp_path))
            tmp_files.append(tmp_path)
        if not tmp_files:
            return None
        result = call_llm_visual(
            system_prompt=_VISUAL_SYSTEM_PROMPT,
            user_prompt="请描述以上页面的主要技术内容。",
            image_paths=tmp_files,
            parse_json=False,
            max_tokens=300,
        )
        return result.strip() if result else None
    except Exception as e:
        logger.warning(
            f"视觉描述生成失败: chunk={chunk_key}, pages={page_start}-{page_end}, error={e}"
        )
        return None
    finally:
        if doc:
            try:
                doc.close()
            except Exception:
                pass
        for f in tmp_files:
            try:
                f.unlink()
            except Exception:
                pass


def _maybe_enhance_with_visual(
    chunk: dict,
    page_texts: List[str],
    *,
    chunk_index: Optional[int] = None,
) -> Tuple[List[str], bool]:
    """如果 chunk 含视觉内容，调用视觉模型生成描述并追加到 page_texts 末尾。"""
    if not (chunk.get("has_visual_content") and chunk.get("pdf_path")):
        return page_texts, False
    chunk_context = _build_chunk_context(chunk, chunk_index)
    try:
        visual_desc = _generate_visual_description(
            chunk["pdf_path"],
            chunk.get("page_start", 1),
            chunk.get("page_end", 1),
            chunk_key=f"{chunk.get('slice_id', 'chunk')}_{chunk_index if chunk_index is not None else 'na'}",
        )
        if visual_desc:
            logger.debug(
                f"视觉增强成功: {chunk_context}"
            )
            return page_texts + [f"[图纸视觉描述] {visual_desc}"], False
    except Exception as e:
        logger.warning(f"视觉增强失败，回退纯文本 GraphRAG: {chunk_context}, error={e}")
        return page_texts, True
    logger.warning(f"视觉增强失败，回退纯文本 GraphRAG: {chunk_context}, reason=empty_visual_description")
    return page_texts, True


def extract_chunk_content(
    page_texts: List[str],
    chapter_title: str = "",
    section_number: str = "",
) -> str:
    """
    将原始页面文本提炼为 100-200 字的检索优化描述。

    重点保留：
      - 零部件名称和编号（如「液压马达」「伺服驱动器 A204」「编码器 E-01」）
      - 操作步骤关键词（如「调压」「参数设定」「手动操作」「保压切换」）
      - 故障现象和错误代码（如「E-01 电机过热」「油温超限」「保压失败」）
      - 技术参数和数值（如「120 bar」「24V DC」「300ms」「±5%」）
      - 警告和注意事项（如「禁止」「危险」「必须」）

    Args:
        page_texts:     原始页面文本列表（每个元素对应一页）
        chapter_title:  所属章节标题（上下文提示，可选）
        section_number: 章节编号，如 "3.1"（可选）

    Returns:
        100-200 字的纯文本描述。LLM 失败时返回原文前 200 字兜底。
    """
    # 合并页面文本
    combined = "\n\n".join(t for t in page_texts if t and t.strip())
    if not combined.strip():
        fallback = (
            f"{section_number} {chapter_title}".strip()
            or "内容为空，无有效文本"
        )
        logger.warning(f"chunk 文本为空，返回标题兜底: section={section_number}")
        return fallback

    # 截断超长文本
    if len(combined) > _MAX_TEXT_CHARS:
        combined = combined[:_MAX_TEXT_CHARS] + "…（已截断）"

    chapter_ctx = f"{section_number} {chapter_title}".strip()
    chapter_ctx_line = f"【所属章节】{chapter_ctx}" if chapter_ctx else ""

    user_prompt = _CHUNK_SUMMARY_USER_TEMPLATE.format(
        chapter_ctx=chapter_ctx_line,
        combined=combined,
    )

    result = call_llm_text(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        parse_json=False,
        temperature=0.1,
        max_tokens=400,
    )

    if result and result.strip():
        return result.strip()

    # LLM 失败兜底：取原文前 200 字
    fallback = combined[:200].strip()
    logger.warning(
        f"LLM 提炼失败，使用原文兜底: section={section_number}, "
        f"原文前50字={combined[:50]!r}"
    )
    return fallback


def extract_chunks_batch(
    chunks: List[dict],
    show_progress: bool = True,
) -> List[str]:
    """
    批量提炼所有 chunk 的描述。

    Args:
        chunks:        sliding_window_chunks() 返回的 chunk 列表，
                       每个 chunk 需含 page_texts, chapter_title, section_number
        show_progress: 是否打印进度

    Returns:
        与 chunks 等长的描述列表（顺序对应）
    """
    total = len(chunks)
    descriptions: List[str] = []

    for chunk in tqdm(chunks, desc="[LLM] 提炼滑窗描述", disable=not show_progress):
        section_number = chunk.get("section_number", "")
        chapter_title = chunk.get("chapter_title", "")
        page_texts, _ = _maybe_enhance_with_visual(chunk, list(chunk.get("page_texts", [])))

        desc = extract_chunk_content(
            page_texts=page_texts,
            chapter_title=chapter_title,
            section_number=section_number,
        )
        descriptions.append(desc)

    return descriptions


# ============================================================
# v2: GraphRAG 结构化实体/关系提取
# ============================================================

_GRAPH_SYSTEM_PROMPT = _load_prompt("graph_system.txt")
_GRAPH_USER_PROMPT_TEMPLATE = _load_prompt("graph_user.txt")


def _parse_graph_json(raw: str) -> Optional[Dict[str, Any]]:
    """从 LLM 输出中解析 graph JSON，失败返回 None。"""
    if not raw:
        return None

    # 尝试提取 ```json ... ```
    import re
    m = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    if m:
        text = m.group(1).strip()
    else:
        m = re.search(r"```\s*(.*?)\s*```", raw, re.DOTALL)
        if m:
            text = m.group(1).strip()
        else:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                text = m.group(0).strip()
            else:
                return None

    # 修复 trailing comma
    text = re.sub(r",\s*([}\]])", r"\1", text)

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None

    # 校验必要字段
    if not isinstance(data, dict):
        return None
    if "summary" not in data:
        return None
    if not isinstance(data.get("entities"), list):
        data["entities"] = []
    if not isinstance(data.get("relations"), list):
        data["relations"] = []

    return data


def _extract_chunk_graph_with_status(
    page_texts: List[str],
    chapter_title: str = "",
    section_number: str = "",
    *,
    chunk_context: str = "",
) -> Tuple[Dict[str, Any], bool]:
    """
    GraphRAG 版本：从原始页面文本中提取实体、关系和总结。

    Args:
        page_texts:     原始页面文本列表（每个元素对应一页）
        chapter_title:  所属章节标题
        section_number: 章节编号

    Returns:
        {
            "summary": "100-150字总结",
            "entities": [{"name", "type", "description"}, ...],
            "relations": [{"src", "rel", "tgt", "description"}, ...],
        }
        LLM 失败时降级为 {"summary": 原文前200字, "entities": [], "relations": []}
    """
    combined = "\n\n".join(t for t in page_texts if t and t.strip())
    if not combined.strip():
        fallback_summary = (
            f"{section_number} {chapter_title}".strip()
            or "内容为空，无有效文本"
        )
        return {"summary": fallback_summary, "entities": [], "relations": []}, False

    if len(combined) > _MAX_TEXT_CHARS:
        combined = combined[:_MAX_TEXT_CHARS] + "…（已截断）"

    chapter_ctx = ""
    if section_number or chapter_title:
        chapter_ctx = f"【所属章节】{section_number} {chapter_title}".strip()

    user_prompt = _GRAPH_USER_PROMPT_TEMPLATE.format(
        chapter_ctx=chapter_ctx,
        combined=combined,
    )

    result = call_llm_text(
        system_prompt=_GRAPH_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        parse_json=False,
        temperature=0.1,
        max_tokens=1500,
    )

    if result:
        parsed = _parse_graph_json(result)
        if parsed:
            # 校验 entities 格式
            valid_entities = []
            for e in parsed.get("entities", []):
                if isinstance(e, dict) and e.get("name"):
                    valid_entities.append({
                        "name": str(e["name"]).strip(),
                        "type": str(e.get("type", "")).strip(),
                        "description": str(e.get("description", "")).strip(),
                    })
            # 校验 relations 格式
            valid_relations = []
            for r in parsed.get("relations", []):
                if isinstance(r, dict) and r.get("src") and r.get("tgt"):
                    valid_relations.append({
                        "src": str(r["src"]).strip(),
                        "rel": str(r.get("rel", "")).strip(),
                        "tgt": str(r["tgt"]).strip(),
                        "description": str(r.get("description", "")).strip(),
                    })

            return {
                "summary": str(parsed.get("summary", "")).strip() or combined[:200],
                "entities": valid_entities,
                "relations": valid_relations,
            }, False

    # LLM 失败降级：用 v1 的纯文本总结作为 summary
    logger.warning(
        f"GraphRAG 提取失败，降级为纯文本: {chunk_context or f'section={section_number}'}, "
        f"原文前50字={combined[:50]!r}"
    )
    fallback_summary = extract_chunk_content(
        page_texts=page_texts,
        chapter_title=chapter_title,
        section_number=section_number,
    )
    return {"summary": fallback_summary, "entities": [], "relations": []}, True


def extract_chunk_graph(
    page_texts: List[str],
    chapter_title: str = "",
    section_number: str = "",
) -> Dict[str, Any]:
    graph, _ = _extract_chunk_graph_with_status(
        page_texts=page_texts,
        chapter_title=chapter_title,
        section_number=section_number,
    )
    return graph


def _process_graph_chunk(index: int, chunk: dict, *, pdf_mode: bool = False) -> Dict[str, Any]:
    section_number = chunk.get("section_number", "")
    chapter_title = chunk.get("chapter_title", "")
    chunk_context = _build_chunk_context(chunk, index)
    page_texts = list(chunk.get("page_texts", []))
    visual_fallback_used = False

    if pdf_mode:
        page_texts, visual_fallback_used = _maybe_enhance_with_visual(
            chunk,
            page_texts,
            chunk_index=index,
        )
    else:
        page_texts, _ = _maybe_enhance_with_visual(
            chunk,
            page_texts,
            chunk_index=index,
        )

    graph, text_fallback_used = _extract_chunk_graph_with_status(
        page_texts=page_texts,
        chapter_title=chapter_title,
        section_number=section_number,
        chunk_context=chunk_context,
    )

    status = "success"
    if text_fallback_used:
        status = "text_fallback"
    elif visual_fallback_used:
        status = "visual_fallback"

    return {
        "index": index,
        "graph": graph,
        "status": status,
        "text_fallback_used": text_fallback_used,
        "visual_fallback_used": visual_fallback_used,
    }


def extract_chunks_graph_batch(
    chunks: List[dict],
    show_progress: bool = True,
    max_workers: int = 1,
    progress_callback: Optional[Callable[[Dict[str, int]], None]] = None,
    pdf_mode: bool = False,
    return_stats: bool = False,
) -> Any:
    """
    批量 GraphRAG 提取。

    Args:
        chunks:        sliding_window_chunks() 返回的 chunk 列表
        show_progress: 是否打印进度

    Returns:
        与 chunks 等长的 graph dict 列表，每个元素格式同 extract_chunk_graph 返回值
    """
    total = len(chunks)
    graph_results: List[Optional[Dict[str, Any]]] = [None] * total
    stats = _build_graph_batch_stats(total)
    max_workers = max(1, int(max_workers or 1))

    def _update_stats(result: Dict[str, Any]) -> None:
        stats["completed_chunks"] += 1
        status = result.get("status")
        if status == "text_fallback":
            stats["text_fallback_chunks"] += 1
        elif status == "visual_fallback":
            stats["visual_fallback_chunks"] += 1
        else:
            stats["success_chunks"] += 1

    def _emit_progress(force: bool = False) -> None:
        if not force and stats["completed_chunks"] not in (total,) and stats["completed_chunks"] % 10 != 0:
            return
        detail = _build_progress_detail(stats)
        logger.info(f"[GraphRAG] {detail}")
        if progress_callback:
            progress_callback(dict(stats))

    if total == 0:
        return ([], stats) if return_stats else []

    if max_workers == 1:
        with tqdm(total=total, desc="[GraphRAG] 实体关系提取", disable=not show_progress) as progress_bar:
            try:
                for i, chunk in enumerate(chunks):
                    result = _process_graph_chunk(i, chunk, pdf_mode=pdf_mode)
                    graph_results[i] = result["graph"]
                    _update_stats(result)
                    progress_bar.update(1)
                    _emit_progress()
            except Exception:
                stats["framework_failures"] += 1
                _emit_progress(force=True)
                raise
    else:
        with tqdm(total=total, desc="[GraphRAG] 实体关系提取", disable=not show_progress) as progress_bar:
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="pdf-graphrag") as executor:
                future_map = {
                    executor.submit(_process_graph_chunk, i, chunk, pdf_mode=pdf_mode): i
                    for i, chunk in enumerate(chunks)
                }
                try:
                    for future in as_completed(future_map):
                        result = future.result()
                        graph_results[result["index"]] = result["graph"]
                        _update_stats(result)
                        progress_bar.update(1)
                        _emit_progress()
                except Exception:
                    stats["framework_failures"] += 1
                    for pending in future_map:
                        pending.cancel()
                    _emit_progress(force=True)
                    raise

    if any(item is None for item in graph_results):
        stats["framework_failures"] += 1
        _emit_progress(force=True)
        raise RuntimeError("GraphRAG 并行聚合失败：存在未完成 chunk 结果")

    _emit_progress(force=True)
    finalized = [item for item in graph_results if item is not None]
    return (finalized, stats) if return_stats else finalized
