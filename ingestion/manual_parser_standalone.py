# -*- coding: utf-8 -*-
"""
manual_parser_standalone.py — 解耦后的 PDF 说明书解析器

改造自 manual_file_parser.py，去掉所有主后端依赖：
- 无 BaseFileParser 继承
- 无 FileReader / openai_service 依赖
- LLM 调用走 src/llm_client.py
- Slice PDF 保存到 self.slices_dir（可配置）
- parse(pdf_path, model_name) 直接接受磁盘路径
"""

import os
import sys
import json
import logging
import re
from pathlib import Path
from typing import Optional, List, Dict, Any

# 确保 src/ 可导入
_AGENT1_DIR = Path(__file__).parent
if str(_AGENT1_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT1_DIR))

from dotenv import load_dotenv
load_dotenv(_AGENT1_DIR / ".env")

# API KEY 兼容映射已统一到 src/config.py（import src 模块时自动执行）

import fitz  # PyMuPDF
from tqdm import tqdm

from src.llm_client import call_llm_text
from src.page_classifier import classify_page
from src.config import (
    TOC_FALLBACK_CHUNK_SIZE,
    CHAPTER_SPLIT_THRESHOLD, TABLE_SPLIT_THRESHOLD,
    CHAPTER_SPLIT_SIZE, TABLE_SPLIT_SIZE,
)

logger = logging.getLogger(__name__)


class ManualParserStandalone:
    """
    PDF 说明书解析器（独立版）

    输入：磁盘上的 PDF 文件路径
    输出：{success, texts, metadatas, slice_files}
    """

    def __init__(self, output_dir: str = None):
        # TOC 检测参数
        self.link_threshold = 3
        self.max_toc_pages = 10
        self.min_toc_items = 3
        self.toc_quality_threshold = 60
        # 章节长度软/硬上限（页）
        self.max_section_pages = 30
        self.max_section_pages_hard = 80
        # 无目录时的兜底切片粒度（页）
        self.fallback_chunk_pages = TOC_FALLBACK_CHUNK_SIZE
        # 页脚映射启用阈值
        self.footer_min_match_count = 3
        self.footer_match_ratio_threshold = 0.35

        # 切片 PDF 保存目录
        _out = Path(output_dir) if output_dir else _AGENT1_DIR / "output"
        self.slices_dir = _out / "slices"
        self.slices_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # 轻量入口：仅提取 TOC + 生成章节子 PDF，不调用 LLM
    # ------------------------------------------------------------------ #

    def parse_toc_only(self, pdf_path: str, model_name: str = None) -> Dict[str, Any]:
        """
        轻量解析：TOC 检测 → 章节子 PDF 生成，跳过所有 LLM 调用。

        供 manual_ingest_agent.py 在使用 chunker + llm_extractor 管线时调用，
        避免重复的 LLM 章节描述开销。

        Returns:
            {
                "toc_items":  [{title, section_number, target_page, page_start, page_end}],
                "slice_files": {"slice_id": "/path/to/slice.pdf"},
                "total_pages": int,
                "model": str,
            }
        """
        file_path = str(pdf_path)
        if not Path(file_path).exists():
            logger.error(f"PDF 文件不存在: {file_path}")
            return {"toc_items": [], "slice_files": {}, "total_pages": 0, "model": ""}

        _model = model_name or Path(file_path).stem

        # Step 1: 目录切片（同 parse()，但不调 LLM）
        try:
            slices = self._manual_slice_pdf(file_path, model_name=_model)
        except Exception as e:
            logger.error(f"TOC 提取失败: {e}", exc_info=True)
            return {"toc_items": [], "slice_files": {}, "total_pages": 0, "model": _model}

        if not slices:
            return {"toc_items": [], "slice_files": {}, "total_pages": 0, "model": _model}

        # Step 2: 生成章节子 PDF（写入 self.slices_dir）
        slices = self._create_slice_pdfs(file_path, slices)

        # Step 3: 构建返回结构
        toc_items = []
        slice_files: Dict[str, str] = {}
        for s in slices:
            meta = s.get("metadata", {})
            toc_items.append(
                {
                    "title": meta.get("title", ""),
                    "section_number": meta.get("slice_id", ""),
                    "target_page": s.get("target_page") or meta.get("page_start", 1),
                    "page_start": meta.get("page_start", 0),
                    "page_end": meta.get("page_end", 0),
                }
            )
            if s.get("slice_pdf_path"):
                slice_files[meta.get("slice_id", "")] = s["slice_pdf_path"]

        doc = fitz.open(file_path)
        total_pages = len(doc)
        doc.close()

        logger.info(
            f"parse_toc_only 完成: {Path(file_path).name}, "
            f"{len(toc_items)} 章节, total_pages={total_pages}"
        )
        return {
            "toc_items": toc_items,
            "slice_files": slice_files,
            "total_pages": total_pages,
            "model": _model,
        }

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #

    def parse(self, pdf_path: str, model_name: str = None) -> Dict[str, Any]:
        """
        解析 PDF 说明书。

        Args:
            pdf_path: 磁盘上的 PDF 路径
            model_name: 机型名称（覆盖默认的文件名推断）

        Returns:
            {success, texts, metadatas, slice_files}
        """
        file_path = str(pdf_path)
        if not Path(file_path).exists():
            logger.error(f"PDF 文件不存在: {file_path}")
            return {"success": False, "msg": f"PDF 文件不存在: {file_path}"}

        filename = Path(file_path).name

        # Step 1: 目录切片与结构化处理
        try:
            slices = self._manual_slice_pdf(file_path, model_name=model_name)
        except Exception as e:
            logger.error(f"PDF 切片失败: {e}", exc_info=True)
            return {"success": False, "msg": f"PDF 切片失败: {e}"}

        if not slices:
            logger.error(f"PDF 切片结构化失败: {file_path}")
            return {"success": False, "msg": "PDF 切片结构化失败"}

        print(f"[TOC] 检测到 {len(slices)} 个章节")

        # Step 2: 为每个切片创建子 PDF
        slices = self._create_slice_pdfs(file_path, slices)

        # Step 3: 切片质量告警摘要（写入首条 metadata）
        slices = self._annotate_slice_alert_summary(slices, filename)

        # Step 4: LLM 优化每个切片的描述（生成向量化就绪的文本）
        slices = self._optimize_descriptions_with_llm(slices)

        # Step 5: 组织返回数据
        texts = [item["description"] for item in slices]
        metadatas = [item["metadata"] for item in slices]

        logger.info(f"PDF 解析成功: {filename}，共 {len(slices)} 个切片")
        return {
            "success": True,
            "texts": texts,
            "metadatas": metadatas,
            "slice_files": [item.get("slice_pdf_path") for item in slices],
        }

    # ------------------------------------------------------------------ #
    # 核心切片逻辑
    # ------------------------------------------------------------------ #

    def _manual_slice_pdf(
        self, pdf_path: str, model_name: str = None
    ) -> List[Dict[str, Any]]:
        """按 TOC 切片，返回 [{description, metadata, target_page}]"""
        doc = fitz.open(pdf_path)
        _model = model_name or Path(pdf_path).stem
        try:
            total_pages = len(doc)
            toc_pymupdf = doc.get_toc()
            logger.debug(
                f"入口: PDF总页数={total_pages}, PyMuPDF书签数={len(toc_pymupdf) if toc_pymupdf else 0}"
            )

            # 1. 检测目录页
            toc_pages = self._detect_toc_pages(doc)

            # 2. 正则解析目录
            toc_items = self._parse_toc_items(doc, toc_pages)
            toc_items = self._normalize_toc_items(toc_items, total_pages)
            regex_quality = self._evaluate_toc_quality(toc_items, total_pages)
            logger.debug(f"正则目录质量: score={regex_quality}, items={len(toc_items)}")

            # 3. 页脚映射校准
            footer_mapping = self._build_footer_page_mapping(doc, toc_pages)
            if footer_mapping:
                calibrated_items, calibrated = self._calibrate_toc_items_with_footer_mapping(
                    toc_items=toc_items,
                    footer_mapping=footer_mapping,
                    total_pages=total_pages,
                )
                if calibrated:
                    toc_items = calibrated_items
                    regex_quality = self._evaluate_toc_quality(toc_items, total_pages)
                    logger.info(
                        f"页脚映射校准生效: items={len(toc_items)}, score={regex_quality}"
                    )

            # 4. LLM 兜底（正则质量不足时）
            if regex_quality < self.toc_quality_threshold:
                llm_toc_items = self._parse_toc_items_with_llm(doc, toc_pages, total_pages)
                llm_toc_items = self._normalize_toc_items(llm_toc_items, total_pages)
                llm_quality = self._evaluate_toc_quality(llm_toc_items, total_pages)
                logger.debug(f"LLM 目录质量: score={llm_quality}, items={len(llm_toc_items)}")
                if llm_quality > regex_quality:
                    logger.info("使用 LLM 目录识别结果替换正则结果")
                    toc_items = llm_toc_items

            logger.debug(f"解析到的章节数量: {len(toc_items)}")

            if not toc_items:
                logger.warning("章节数为 0，退化为固定页长切片")
                return self._fallback_chunk_slices(doc, pdf_path, model_name=_model)

            # 5. 生成切片结构（含长章节二次切分）
            slices = []
            slice_ids = set()
            for idx, item in enumerate(toc_items):
                item_target_page = int(item.get("target_page", 1))
                target_page = item_target_page - 1

                # 计算章节页数跨度
                if idx < len(toc_items) - 1:
                    next_target = int(toc_items[idx + 1].get("target_page", item_target_page + 1))
                    chapter_span = max(1, next_target - item_target_page)
                else:
                    chapter_span = min(self.max_section_pages_hard, total_pages - item_target_page + 1)

                page = doc.load_page(min(target_page, len(doc) - 1))
                content = page.get_text().strip()

                # 判断章节页面类型（决定切分粒度）
                try:
                    page_type_result = classify_page({"text": content, "page": item_target_page})
                    page_type = page_type_result.get("type", "description")
                except Exception:
                    page_type = "description"

                # catalog_table 类型用更细粒度切分
                if page_type == "catalog_table":
                    split_threshold, split_size = TABLE_SPLIT_THRESHOLD, TABLE_SPLIT_SIZE
                else:
                    split_threshold, split_size = CHAPTER_SPLIT_THRESHOLD, CHAPTER_SPLIT_SIZE

                desc = (
                    content
                    if content
                    else f"章节：{item['title']}。本章节介绍设备相关功能、操作方法、注意事项及常见故障。"
                )

                section_number = str(item.get("section_number", "")).strip()
                title_value = str(item.get("title", "")).strip()
                if section_number:
                    base_slice_id = section_number
                elif title_value:
                    base_slice_id = title_value
                else:
                    base_slice_id = f"page_{item_target_page}"
                slice_title = title_value or base_slice_id

                if chapter_span <= split_threshold:
                    # 无需二次切分
                    if base_slice_id in slice_ids:
                        continue
                    slice_ids.add(base_slice_id)
                    slices.append({
                        "description": desc,
                        "metadata": {
                            "model": _model,
                            "manual_type": f"{_model}机型操作手册",
                            "slice_id": base_slice_id,
                            "title": slice_title,
                            "section_number": section_number,
                        },
                        "target_page": item_target_page,
                    })
                else:
                    # 二次切分：每 split_size 页一段
                    logger.info(
                        f"章节 {base_slice_id} 类型={page_type}, span={chapter_span}页 > {split_threshold}，"
                        f"切分为每 {split_size} 页一段"
                    )
                    part = 1
                    for part_start in range(item_target_page, item_target_page + chapter_span, split_size):
                        part_slice_id = f"{base_slice_id}_part{part}"
                        if part_slice_id in slice_ids:
                            part += 1
                            continue
                        slice_ids.add(part_slice_id)
                        part_page_idx = min(part_start - 1, len(doc) - 1)
                        part_content = doc.load_page(part_page_idx).get_text().strip()
                        part_desc = part_content if part_content else desc
                        slices.append({
                            "description": part_desc,
                            "metadata": {
                                "model": _model,
                                "manual_type": f"{_model}机型操作手册",
                                "slice_id": part_slice_id,
                                "title": slice_title,
                                "section_number": section_number or base_slice_id,
                            },
                            "target_page": part_start,
                        })
                        part += 1
            return slices
        finally:
            doc.close()

    # ------------------------------------------------------------------ #
    # TOC 检测
    # ------------------------------------------------------------------ #

    def _detect_toc_pages(self, doc: fitz.Document) -> List[int]:
        toc_pages = []
        for page_num in range(min(self.max_toc_pages, len(doc))):
            page = doc.load_page(page_num)
            links = page.get_links()
            if len(links) >= self.link_threshold:
                toc_pages.append(page_num + 1)
            elif toc_pages:
                break

        if not toc_pages:
            toc_pages = list(range(1, min(6, len(doc) + 1)))
            logger.debug(f"未检测到目录页，回退到固定页: {toc_pages}")
            return toc_pages
        logger.debug(f"检测到的目录页: {toc_pages}")
        return toc_pages

    def _parse_toc_items(
        self, doc: fitz.Document, toc_pages: List[int]
    ) -> List[Dict[str, Any]]:
        items = []
        for page_num in toc_pages:
            page = doc.load_page(page_num - 1)
            links = page.get_links()
            for link in links:
                if "page" in link:
                    rect = link.get("from")
                    if rect:
                        try:
                            target_page = int(link["page"]) + 1
                        except (TypeError, ValueError):
                            continue
                        title_text = page.get_text("text", clip=rect).strip()
                        if title_text:
                            title_text = re.sub(r"^\d+\s*\n", "", title_text)
                            section_number, title = self._extract_section_number_and_title(
                                title_text
                            )
                            toc_page_code = self._extract_toc_page_code(title_text)
                            items.append(
                                {
                                    "section_number": section_number,
                                    "title": title,
                                    "target_page": target_page,
                                    "toc_page_code": toc_page_code,
                                }
                            )
        return items

    def _normalize_toc_items(
        self, toc_items: List[Dict[str, Any]], total_pages: int
    ) -> List[Dict[str, Any]]:
        normalized = []
        seen = set()
        for item in toc_items or []:
            try:
                target_page = int(item.get("target_page", 0))
            except (TypeError, ValueError):
                continue
            if target_page < 1 or target_page > total_pages:
                continue
            title = str(item.get("title", "")).strip()
            section_number = str(item.get("section_number", "")).strip()
            unique_key = (title, target_page)
            if unique_key in seen:
                continue
            seen.add(unique_key)
            normalized.append(
                {
                    "title": title,
                    "section_number": section_number,
                    "target_page": target_page,
                    "toc_page_code": str(item.get("toc_page_code", "")).strip(),
                }
            )

        normalized.sort(key=lambda x: x["target_page"])
        strictly_increasing = []
        last_page = 0
        for item in normalized:
            if item["target_page"] <= last_page:
                continue
            strictly_increasing.append(item)
            last_page = item["target_page"]
        return strictly_increasing

    def _evaluate_toc_quality(
        self, toc_items: List[Dict[str, Any]], total_pages: int
    ) -> int:
        if not toc_items:
            return 0
        item_count = len(toc_items)
        pages = [int(i.get("target_page", 0)) for i in toc_items]
        pages = [p for p in pages if 1 <= p <= total_pages]
        if not pages:
            return 0

        increasing_ok = sum(
            1 for idx in range(1, len(pages)) if pages[idx] > pages[idx - 1]
        )
        increasing_ratio = 1.0 if len(pages) <= 1 else increasing_ok / (len(pages) - 1)
        coverage = (max(pages) - min(pages) + 1) / max(1, total_pages)

        spans = []
        for idx, start in enumerate(pages):
            end = pages[idx + 1] if idx < len(pages) - 1 else total_pages + 1
            spans.append(max(1, end - start))
        oversized_ratio = sum(1 for s in spans if s > self.max_section_pages) / max(
            1, len(spans)
        )

        score = 0
        score += 30 if item_count >= self.min_toc_items else 10
        score += 25 if increasing_ratio > 0.95 else (15 if increasing_ratio > 0.8 else 5)
        score += 20 if 0.08 <= coverage <= 0.95 else 10
        score += 25 if oversized_ratio <= 0.2 else (10 if oversized_ratio <= 0.4 else 0)
        return max(0, min(100, score))

    # ------------------------------------------------------------------ #
    # TOC 文本解析
    # ------------------------------------------------------------------ #

    def _extract_section_number_and_title(self, title_text: str) -> tuple:
        text = str(title_text or "").strip()
        text = re.sub(r"\s+", " ", text.replace("＿", "_")).strip()
        if not text:
            return "", ""

        match = re.match(
            r"^((?:\d+[.\-])*\d+)\s+(.+?)\s*(?:[._·…-]{2,}|\s{2,})\s*([IVXLCDMivxlcdm]+|\d+(?:[.\-]\d+){0,6})\s*$",
            text,
        )
        if match:
            return match.group(1).strip(), match.group(2).strip()

        match = re.match(
            r"^(.+?)\s*(?:[._·…-]{2,}|\s{2,})\s*([IVXLCDMivxlcdm]+|\d+(?:[.\-]\d+){0,6})\s*$",
            text,
        )
        if match:
            return "", match.group(1).strip()

        match = re.match(r"^((?:\d+[.\-])*\d+)\s*[\)\]】）\.、:\-]?\s*(.+)$", text)
        if match:
            return match.group(1).strip(), match.group(2).strip()

        match = re.match(r"^([IVXLCDMivxlcdm]+)\s*[\)\]】）\.、:\-]?\s*(.+)$", text)
        if match:
            return match.group(1).strip(), match.group(2).strip()

        match = re.match(
            r"^(第[一二三四五六七八九十百千万零〇两0-9]+[章节篇部卷节])\s*(.+)$", text
        )
        if match:
            return match.group(1).strip(), match.group(2).strip()

        return "", text

    def _extract_toc_page_code(self, title_text: str) -> str:
        text = str(title_text or "").strip().replace("＿", "_")
        if not text:
            return ""
        match = re.search(
            r"(?:[._·…-]{2,}|\s{2,})\s*([IVXLCDMivxlcdm]+|\d+(?:[.\-]\d+){0,6})\s*$",
            text,
        )
        if not match:
            return ""
        return self._normalize_page_code(match.group(1))

    def _normalize_page_code(self, page_code: str) -> str:
        return str(page_code or "").strip().upper()

    # ------------------------------------------------------------------ #
    # 页脚映射
    # ------------------------------------------------------------------ #

    def _extract_footer_page_codes_from_page(self, page: fitz.Page) -> List[str]:
        patterns = [
            r"\b\d+(?:\.\d+){1,3}-\d+\b",
            r"\b\d+-\d+\b",
            r"\b[IVXLCDMivxlcdm]+\b",
        ]
        candidates = set()
        page_rect = page.rect
        threshold_y = page_rect.height * 0.8
        data = page.get_text("dict")
        for block in data.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    bbox = span.get("bbox", [0, 0, 0, 0])
                    y_pos = bbox[1] if len(bbox) >= 2 else 0
                    if y_pos < threshold_y:
                        continue
                    text = span.get("text", "")
                    for pattern in patterns:
                        for m in re.findall(pattern, text):
                            candidates.add(self._normalize_page_code(m))
        return list(candidates)

    def _build_footer_page_mapping(
        self, doc: fitz.Document, toc_pages: List[int]
    ) -> Dict[str, int]:
        mapping: Dict[str, int] = {}
        toc_set = set(toc_pages or [])
        for idx in range(len(doc)):
            page_num = idx + 1
            if page_num in toc_set:
                continue
            page = doc.load_page(idx)
            codes = self._extract_footer_page_codes_from_page(page)
            for code in codes:
                if code and code not in mapping:
                    mapping[code] = page_num
        logger.debug(f"页脚映射构建完成: {len(mapping)} 项")
        return mapping

    def _calibrate_toc_items_with_footer_mapping(
        self,
        toc_items: List[Dict[str, Any]],
        footer_mapping: Dict[str, int],
        total_pages: int,
    ) -> tuple:
        if not toc_items or not footer_mapping:
            return toc_items, False

        calibrated = []
        matched = 0
        for item in toc_items:
            new_item = dict(item)
            toc_page_code = self._normalize_page_code(item.get("toc_page_code", ""))
            mapped_page = footer_mapping.get(toc_page_code)
            if mapped_page and 1 <= mapped_page <= total_pages:
                new_item["target_page"] = mapped_page
                matched += 1
            calibrated.append(new_item)

        match_ratio = matched / max(1, len(toc_items))
        if (
            matched < self.footer_min_match_count
            or match_ratio < self.footer_match_ratio_threshold
        ):
            logger.debug(
                f"页脚映射置信度不足，跳过校准: matched={matched}, ratio={match_ratio:.2f}"
            )
            return toc_items, False

        calibrated = self._normalize_toc_items(calibrated, total_pages)
        logger.debug(
            f"页脚映射校准通过: matched={matched}, ratio={match_ratio:.2f}"
        )
        return calibrated, True

    # ------------------------------------------------------------------ #
    # 兜底：固定页长切片
    # ------------------------------------------------------------------ #

    def _fallback_chunk_slices(
        self, doc: fitz.Document, pdf_path: str, model_name: str = None
    ) -> List[Dict[str, Any]]:
        total_pages = len(doc)
        _model = model_name or Path(pdf_path).stem
        slices = []
        chunk_size = max(5, self.fallback_chunk_pages)
        for start in range(1, total_pages + 1, chunk_size):
            slice_id = f"chunk_{start:04d}"
            end = min(total_pages, start + chunk_size - 1)
            page = doc.load_page(start - 1)
            content = page.get_text().strip()
            desc = content if content else f"说明书分段切片，起始页为第 {start} 页。"
            slices.append(
                {
                    "description": desc,
                    "metadata": {
                        "model": _model,
                        "manual_type": f"{_model}机型操作手册",
                        "slice_id": slice_id,
                        "title": f"第{start}-{end}页（兜底分段）",
                    },
                    "target_page": start,
                }
            )
        logger.warning(f"目录识别失败，已使用固定页长切片: {len(slices)} 段")
        return slices

    # ------------------------------------------------------------------ #
    # 切片 PDF 生成
    # ------------------------------------------------------------------ #

    def _create_slice_pdfs(
        self, pdf_path: str, slices: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        try:
            doc = fitz.open(pdf_path)
            total = len(slices)
            base_name = Path(pdf_path).stem
            logger.debug(f"共 {total} 个章节需要生成子 PDF")

            for i, slice_info in enumerate(tqdm(slices, desc="[切片] 生成章节子PDF")):
                try:
                    new_doc = fitz.open()
                    start_page = slice_info.get("target_page", 1) - 1

                    next_target_raw = None
                    if i < len(slices) - 1:
                        next_target_raw = slices[i + 1].get("target_page")

                    if isinstance(next_target_raw, int):
                        end_page = next_target_raw - 1
                    else:
                        end_page = start_page + self.max_section_pages

                    if end_page <= start_page:
                        end_page = start_page + 1

                    if end_page - start_page > self.max_section_pages_hard:
                        capped_end = start_page + self.max_section_pages_hard
                        logger.warning(
                            f"章节过大已截断: slice={slice_info.get('metadata', {}).get('slice_id', i)}, "
                            f"start={start_page + 1}, capped_end={capped_end}"
                        )
                        end_page = capped_end

                    if i == len(slices) - 1 and next_target_raw is None:
                        end_page = min(len(doc), start_page + self.max_section_pages_hard)

                    start_page = max(0, start_page)
                    end_page = min(len(doc), end_page)
                    if start_page >= end_page:
                        end_page = start_page + 1

                    new_doc.insert_pdf(doc, from_page=start_page, to_page=end_page - 1)

                    slice_id = slice_info.get("metadata", {}).get("slice_id", f"slice_{i}")
                    # 文件名安全化（去掉 / \ 空格等不合法字符）
                    safe_slice_id = re.sub(r'[/\\<>:"|?*\s]', "_", slice_id)

                    # 按 base_name 建子目录：output/slices/{model}/{slice}.pdf
                    model_subdir = self.slices_dir / base_name
                    model_subdir.mkdir(parents=True, exist_ok=True)
                    slice_pdf_path = model_subdir / f"{safe_slice_id}.pdf"
                    new_doc.save(str(slice_pdf_path))
                    new_doc.close()

                    slice_info["metadata"]["page_start"] = start_page + 1
                    slice_info["metadata"]["page_end"] = end_page
                    # 存相对路径（相对于 output_dir），方便 HTTP 对外暴露
                    slice_info["slice_pdf_path"] = f"slices/{base_name}/{safe_slice_id}.pdf"

                    logger.debug(f"切片进度: {i + 1}/{total} - {slice_id}")

                except Exception as e:
                    logger.error(f"创建切片 PDF 失败 (切片 {i}): {e}")
                    slice_info["slice_pdf_path"] = None

            doc.close()
        except Exception as e:
            logger.error(f"处理 PDF 切片失败: {e}", exc_info=True)
        return slices

    # ------------------------------------------------------------------ #
    # 质量告警
    # ------------------------------------------------------------------ #

    def _annotate_slice_alert_summary(
        self, slices: List[Dict[str, Any]], filename: str
    ) -> List[Dict[str, Any]]:
        if not slices:
            return slices

        total_slices = len(slices)
        missing_range_count = 0
        chunk_fallback_count = 0
        valid_spans: List[int] = []

        for item in slices:
            meta = item.get("metadata", {}) if isinstance(item, dict) else {}
            if not isinstance(meta, dict):
                meta = {}
            slice_id = str(meta.get("slice_id", "")).strip().lower()
            if slice_id.startswith("chunk_"):
                chunk_fallback_count += 1

            page_start = meta.get("page_start")
            page_end = meta.get("page_end")
            try:
                start = int(page_start)
                end = int(page_end)
            except (TypeError, ValueError):
                missing_range_count += 1
                continue

            if end < start:
                missing_range_count += 1
                continue
            valid_spans.append(end - start + 1)

        max_span = max(valid_spans) if valid_spans else None
        large_slice_count = sum(1 for s in valid_spans if s >= self.max_section_pages)
        chunk_ratio = chunk_fallback_count / max(1, total_slices)
        large_ratio = large_slice_count / max(1, total_slices)

        flags: List[str] = []
        if chunk_fallback_count > 0:
            flags.append("CHUNK_FALLBACK")
        if missing_range_count > 0:
            flags.append("MISSING_RANGE")
        if max_span is not None and max_span >= self.max_section_pages_hard:
            flags.append("OVERSIZE_SLICE")
        if large_ratio > 0.2:
            flags.append("TOO_MANY_LARGE_SLICES")

        if not flags:
            return slices

        if any(f in flags for f in ("MISSING_RANGE", "OVERSIZE_SLICE")):
            level = "high"
        elif "CHUNK_FALLBACK" in flags:
            level = "medium"
        else:
            level = "low"

        summary = {
            "level": level,
            "flags": flags,
            "summary": {
                "slice_count": total_slices,
                "max_span": max_span,
                "large_slice_count": large_slice_count,
                "missing_range_count": missing_range_count,
                "chunk_fallback_count": chunk_fallback_count,
                "chunk_fallback_ratio": round(chunk_ratio, 4),
            },
        }

        first_meta = slices[0].get("metadata", {})
        if not isinstance(first_meta, dict):
            first_meta = {}
            slices[0]["metadata"] = first_meta
        first_meta["manual_slice_alert_summary"] = summary

        logger.warning(
            "[manual_slice_alert] filename=%s level=%s flags=%s slice_count=%s max_span=%s",
            filename, level, ",".join(flags), total_slices, max_span,
        )
        return slices

    # ------------------------------------------------------------------ #
    # LLM 描述优化
    # ------------------------------------------------------------------ #

    def _optimize_descriptions_with_llm(
        self, slices: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        total = len(slices)
        logger.debug(f"共 {total} 个切片需要 LLM 优化")

        system_prompt = (
            "你是注塑机技术手册内容优化助手，专注于生成高质量的向量检索描述。"
            "不要输出 Markdown，只输出纯文本描述。"
        )

        for i, slice_info in enumerate(tqdm(slices, desc="[LLM] 生成章节描述")):
            slice_id = slice_info.get("metadata", {}).get("slice_id", f"slice_{i}")
            try:
                content = slice_info.get("description", "")
                title = slice_info.get("metadata", {}).get("title", slice_id)

                user_prompt = f"""请为这个设备手册章节生成一个专门用于向量化检索的功能描述。

⚠️ 重要说明：这个描述将被用于：
1. 向量化 embedding 处理
2. 与用户查询进行语义匹配
3. 故障诊断和技术支持的检索
4. 当用户查询匹配时，直接推送对应的 PDF 切片

🎯 核心要求：
1. **功能概述**：明确说明这个功能是什么，解决什么问题
2. **关键操作**：列出主要的操作步骤和设置方法
3. **故障相关**：可能出现的问题、警报、异常情况
4. **技术术语**：包含相关的专业术语和参数名称
5. **应用场景**：什么时候需要使用这个功能

🔍 检索优化要求：
- 使用丰富的同义词和相关术语
- 包含用户可能搜索的问题描述
- 涵盖故障现象和解决方案的关键词
- 便于语义匹配和相似度计算
- 不要包含页码信息

📝 格式要求：
- 150-250 字长度
- 语言专业但易懂
- 结构清晰，信息密度高
- 适合向量化处理
- 不要输出 Markdown，只输出纯文本

章节信息：
- 标题：{title}

章节内容：
{content}

请生成描述："""

                optimized = call_llm_text(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    parse_json=False,
                    temperature=0.1,
                    max_tokens=600,
                )
                if optimized:
                    slice_info["description"] = optimized.strip()

                page_start = slice_info.get("metadata", {}).get("page_start", "?")
                page_end = slice_info.get("metadata", {}).get("page_end", "?")
                print(f"  ✓ {title[:30]} ({page_start}-{page_end}页)")
                logger.debug(f"LLM 优化进度: {i + 1}/{total} - {slice_id}")

            except Exception as e:
                logger.warning(f"LLM 优化失败，保留原描述: {slice_id}, error={e}")

        return slices

    # ------------------------------------------------------------------ #
    # LLM 兜底目录识别
    # ------------------------------------------------------------------ #

    def _parse_toc_items_with_llm(
        self,
        doc: fitz.Document,
        toc_pages: List[int],
        total_pages: int,
    ) -> List[Dict[str, Any]]:
        try:
            toc_text_parts = []
            for page_num in toc_pages:
                page = doc.load_page(page_num - 1)
                page_text = page.get_text("text").strip()
                if page_text:
                    toc_text_parts.append(f"[目录页{page_num}]\n{page_text}")
            toc_text = "\n\n".join(toc_text_parts)
            if not toc_text:
                return []

            system_prompt = "你是 PDF 目录识别助手，只输出 JSON，不要解释。"
            user_prompt = f"""请从下面目录文本中提取章节。
要求：
1. 仅输出 JSON 对象，不要解释。
2. 格式必须是：{{"items":[{{"title":"", "target_page": 1, "section_number": ""}}]}}
3. target_page 必须是整数，范围 1 到 {total_pages}。
4. 没有章节编号时 section_number 置空字符串。
5. 不要编造章节。

目录文本：
{toc_text}
"""

            result = call_llm_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                parse_json=True,
                temperature=0.1,
                max_tokens=1200,
            )

            if not result or not isinstance(result, dict):
                return []

            raw_items = result.get("items", [])
            if not isinstance(raw_items, list):
                return []

            items = []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title", "")).strip()
                section_number = str(item.get("section_number", "")).strip()
                try:
                    target_page = int(item.get("target_page", 0))
                except (TypeError, ValueError):
                    continue
                items.append(
                    {
                        "title": title,
                        "section_number": section_number,
                        "target_page": target_page,
                    }
                )
            return items

        except Exception as e:
            logger.warning(f"LLM 目录识别失败: {e}")
            return []
