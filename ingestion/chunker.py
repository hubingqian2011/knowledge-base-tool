# -*- coding: utf-8 -*-
"""
chunker.py — 基于 TOC 结构的滑动窗口分块器

设计思路
--------
manual_parser_standalone 解决"在哪里切"（章节边界）。
chunker 解决"切多细"（滑动窗口，保证边界内容不丢失）。

两者协作：
  1. parse_toc_only() 给出章节边界 → 章节子 PDF（粗粒度，供溯源）
  2. sliding_window_chunks() 在整本 PDF 上滑窗 → 细粒度 chunk（供检索）

每个 chunk 保留 section_number/chapter_title，
可通过 slice_files 映射回对应章节子 PDF，实现"检索定位到 chunk，
溯源跳转到整章子 PDF"的双层索引。

支持两种输入：
  - pdf_path (str)    → 用 PyMuPDF 读取页面（PDF 入库）
  - pages (list[str]) → 直接使用虚拟页文本（Word 入库）

函数签名
--------
sliding_window_chunks(pdf_path_or_pages, toc_items, window=2, overlap=1) -> list[dict]
"""

import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Union

logger = logging.getLogger(__name__)


def sliding_window_chunks(
    pdf_path_or_pages: Union[str, List[str]],
    toc_items: List[Dict[str, Any]],
    window: int = 2,
    overlap: int = 1,
) -> List[Dict[str, Any]]:
    """
    滑动窗口分块，每个 chunk 标注所属章节。

    支持两种输入：
      - pdf_path_or_pages 为 str  → 用 PyMuPDF 读取 PDF 页面
      - pdf_path_or_pages 为 list → 直接使用虚拟页文本（Word 等无页码文档）

    Args:
        pdf_path_or_pages: PDF 文件路径（str）或虚拟页文本列表（list[str]）
        toc_items:  [{title, section_number, target_page, page_start, page_end}]
                    若为空，则做无章节标注的滑窗。
        window:     每个 chunk 包含的页数（默认 2）
        overlap:    相邻 chunk 重叠的页数（默认 1）
                    step = window - overlap，必须 >= 1

    Returns:
        list of {
            page_start, page_end, slice_id, chapter_title, section_number,
            page_texts: list[str],
            has_visual_content: bool,
            pdf_path: str,
        }
    """
    step = max(1, window - overlap)
    if step == window:
        logger.warning(
            f"overlap={overlap} 设置为 0（step={step}），相邻 chunk 无重叠"
        )

    # ---- 读取页面文本 ------------------------------------------------ #
    if isinstance(pdf_path_or_pages, list):
        # 虚拟页模式（Word 等）
        all_page_texts = pdf_path_or_pages
        total_pages = len(all_page_texts)
        source_path = ""
        is_pdf = False
        logger.info(
            f"开始滑窗分块（虚拟页模式）: "
            f"total_pages={total_pages}, window={window}, overlap={overlap}, step={step}"
        )
    else:
        # PDF 模式
        import fitz  # PyMuPDF — 仅 PDF 路径时导入
        doc = fitz.open(str(pdf_path_or_pages))
        total_pages = len(doc)
        source_path = str(pdf_path_or_pages)
        is_pdf = True
        all_page_texts = []
        for p in range(total_pages):
            try:
                all_page_texts.append(doc.load_page(p).get_text().strip())
            except Exception as exc:
                logger.warning(f"第 {p + 1} 页文本提取失败: {exc}")
                all_page_texts.append("")
        doc.close()
        logger.info(
            f"开始滑窗分块: {Path(pdf_path_or_pages).name}, "
            f"total_pages={total_pages}, window={window}, overlap={overlap}, step={step}"
        )

    if total_pages == 0:
        return []

    # ---- 构建 page → chapter 映射 ------------------------------------ #
    chapter_for_page: Dict[int, Dict] = {}
    if toc_items:
        sorted_toc = sorted(
            toc_items,
            key=lambda x: x.get("target_page") or x.get("page_start") or 0,
        )
        for i, item in enumerate(sorted_toc):
            ch_start = item.get("target_page") or item.get("page_start") or 1
            if i < len(sorted_toc) - 1:
                next_start = (
                    sorted_toc[i + 1].get("target_page")
                    or sorted_toc[i + 1].get("page_start")
                    or total_pages
                )
                ch_end = next_start - 1
            else:
                ch_end = total_pages
            for p in range(ch_start, ch_end + 1):
                chapter_for_page[p] = item

    # ---- 视觉内容检测（仅 PDF）---------------------------------------- #
    def _has_visual(page_texts_segment: List[str], start_page: int) -> bool:
        if not is_pdf:
            return False
        try:
            from src.page_classifier import classify_page
            for idx, text in enumerate(page_texts_segment):
                ptype = classify_page({"text": text, "page": start_page + idx}).get("type", "")
                if ptype in ("drawing", "mixed"):
                    return True
        except Exception:
            pass
        return False

    # ---- 滑动窗口 ---------------------------------------------------- #
    chunks: List[Dict[str, Any]] = []
    page_start = 1  # 1-based

    while page_start <= total_pages:
        page_end = min(page_start + window - 1, total_pages)

        # 取当前窗口的文本（转 0-based 索引）
        page_texts = all_page_texts[page_start - 1 : page_end]

        # 所属章节
        chapter = chapter_for_page.get(page_start)
        if chapter:
            chapter_title = chapter.get("title", "")
            section_number = chapter.get("section_number", "") or chapter.get("slice_id", "")
        else:
            chapter_title = ""
            section_number = ""

        # slice_id
        if section_number:
            slice_id = f"{section_number}__p{page_start}_{page_end}"
        else:
            slice_id = f"p{page_start}_{page_end}"

        chunks.append(
            {
                "page_start": page_start,
                "page_end": page_end,
                "slice_id": slice_id,
                "chapter_title": chapter_title,
                "section_number": section_number,
                "page_texts": page_texts,
                "has_visual_content": _has_visual(page_texts, page_start),
                "pdf_path": source_path,
            }
        )

        page_start += step

    logger.info(f"滑窗分块完成: {len(chunks)} 个 chunk")
    return chunks


def find_chapter_pdf(
    page_start: int,
    toc_items: List[Dict[str, Any]],
    slice_files: Dict[str, str],
) -> Optional[str]:
    """
    根据 chunk 的 page_start，找到覆盖该页的章节子 PDF 路径。

    这是双层索引的溯源机制：
      细粒度 chunk 检索命中 → 找到章节子 PDF → 供用户查看完整章节

    Args:
        page_start:  chunk 起始页（1-based）
        toc_items:   parse_toc_only() 返回的章节列表
        slice_files: {"slice_id": "/path/to/slice.pdf"}

    Returns:
        章节子 PDF 路径，找不到则返回 None
    """
    if not toc_items or not slice_files:
        return None

    sorted_toc = sorted(
        toc_items,
        key=lambda x: x.get("target_page") or x.get("page_start") or 0,
    )

    for item in reversed(sorted_toc):
        chapter_start = item.get("target_page") or item.get("page_start") or 0
        if page_start >= chapter_start:
            section_num = item.get("section_number", "") or item.get("slice_id", "")
            pdf_path = slice_files.get(section_num)
            if pdf_path:
                return pdf_path
            # 尝试 title 作为 key
            return slice_files.get(item.get("title", ""))

    return None
