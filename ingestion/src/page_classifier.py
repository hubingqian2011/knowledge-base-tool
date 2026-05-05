# -*- coding: utf-8 -*-
"""
规则式文档族 / 页类型分类器。
"""

from __future__ import annotations

from typing import Dict, List, Tuple
import math
import re


PAGE_TYPES = (
    "drawing",
    "catalog_table",
    "legend_or_rule",
    "description",
    "toc",
    "mixed",
)

DOCUMENT_FAMILIES = (
    "drawing-heavy",
    "manual-like",
    "table-heavy",
    "mixed",
)

HYDRAULIC_CODE_PATTERN = re.compile(r"\b(?:[A-Z]{1,3}\d+(?:\.\d+)?(?:/[A-Z]?\d+(?:\.\d+)?)?)\b")
ELECTRIC_CODE_PATTERN = re.compile(r"(?:=[0-9A-Z+\-./]+|-[A-Z]{1,4}\d+|&[A-Z]{2,}=[0-9A-Z+\-./]+)")
TOC_PAGE_PATTERN = re.compile(r"\b\d+\.\d+(?:\.\d+)?-\d+\b")

KEYWORDS = {
    "toc": ("目录", "ht_toc", "contents", "页面描述"),
    "catalog_table": ("元件表", "设备列表", "参数", "对照表", "图表", "序号", "代号", "编码", "型号", "备注"),
    "legend_or_rule": ("结构标识总览", "连接点代号", "位置代号", "设备标识符", "说明", "标识", "完整设备标识符"),
    "description": ("故障", "操作", "保养", "调整", "安装", "调校", "注意事项", "声明", "前言"),
    "drawing": ("原理图", "回路", "接线图", "组件图", "动作图", "图纸", "电磁铁动作图"),
    "manual": ("使用手册", "安全手册", "前言", "安装及调校"),
    "table": ("参数", "规格", "尺寸", "设备列表", "元件表"),
    "rule": ("结构标识总览", "连接点代号", "位置代号", "设备标识符"),
    "noise": ("电话", "传真", "地址", "email", "www.", "zipcode", "add:"),
}


def _normalized_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _keyword_hits(text: str, keywords: tuple[str, ...]) -> int:
    lower_text = text.lower()
    compact_text = lower_text.replace(" ", "")
    return sum(1 for keyword in keywords if keyword.lower() in lower_text or keyword.lower().replace(" ", "") in compact_text)


def _long_text_signal(text: str) -> float:
    normalized = _normalized_text(text)
    if len(normalized) < 180:
        return 0.0
    sentences = sum(normalized.count(mark) for mark in ("。", "；", "：", ".", ";", ":"))
    return min(4.0, len(normalized) / 320.0 + sentences * 0.3)


def _code_density(text: str) -> Tuple[int, int]:
    return len(HYDRAULIC_CODE_PATTERN.findall(text)), len(ELECTRIC_CODE_PATTERN.findall(text))


def classify_page(page: Dict) -> Dict:
    text = _normalized_text(page.get("text", ""))
    compact_text = text.replace(" ", "")
    page_no = page.get("page", 0)
    scores = {page_type: 0.0 for page_type in PAGE_TYPES}
    signals: List[str] = []

    toc_hits = _keyword_hits(text, KEYWORDS["toc"])
    toc_codes = len(TOC_PAGE_PATTERN.findall(text))
    if toc_hits:
        scores["toc"] += toc_hits * 3.0
        signals.append("目录关键词")
    if toc_codes >= 2:
        scores["toc"] += min(4.0, toc_codes / 2.0)
        signals.append("目录页码索引")
    if page_no <= 4 and ("目录" in compact_text or "ht_toc" in compact_text or "页面描述" in compact_text or toc_codes >= 2):
        scores["toc"] += 3.2
        signals.append("前序目录页")
    if toc_codes >= 3 and page_no <= 3:
        scores["toc"] += 2.0
        signals.append("强目录索引")

    catalog_hits = _keyword_hits(text, KEYWORDS["catalog_table"])
    if catalog_hits:
        scores["catalog_table"] += catalog_hits * 1.6
        signals.append("表格关键词")
    unit_hits = sum(compact_text.count(unit) for unit in ("mm", "kn", "mpa", "kw", "cm3", "g/s", "r/min", "℃", "%"))
    numeric_hits = len(re.findall(r"\d+(?:\.\d+)?", compact_text))
    if unit_hits >= 2 or numeric_hits >= 25:
        scores["catalog_table"] += min(3.5, unit_hits * 0.8 + numeric_hits / 18.0)
        signals.append("参数表数值密集")

    legend_hits = _keyword_hits(text, KEYWORDS["legend_or_rule"])
    if legend_hits:
        scores["legend_or_rule"] += legend_hits * 2.2
        signals.append("规则页关键词")

    description_hits = _keyword_hits(text, KEYWORDS["description"])
    if description_hits:
        scores["description"] += description_hits * 1.5
        signals.append("说明页关键词")

    drawing_hits = _keyword_hits(text, KEYWORDS["drawing"])
    if drawing_hits:
        scores["drawing"] += drawing_hits * 1.8
        signals.append("图纸关键词")

    hydraulic_codes, electric_codes = _code_density(text)
    if "动作图" in compact_text and (hydraulic_codes >= 4 or electric_codes >= 3):
        scores["legend_or_rule"] += 3.5
        signals.append("动作图规则页")
    if hydraulic_codes >= 4:
        scores["drawing"] += min(3.5, hydraulic_codes / 4.0)
        signals.append("液压编码密集")
    if electric_codes >= 3:
        scores["drawing"] += min(4.0, electric_codes / 3.0)
        scores["legend_or_rule"] += min(2.0, electric_codes / 5.0)
        signals.append("电气编码密集")

    long_text = _long_text_signal(text)
    if long_text:
        scores["description"] += long_text
        signals.append("长文本说明")

    if "序号" in text and "名称" in text:
        scores["catalog_table"] += 2.5
        signals.append("表头命中")
    if ("序号" in compact_text and "名称" in compact_text) or ("代号" in compact_text and "型号" in compact_text):
        scores["catalog_table"] += 3.0
        signals.append("紧凑表头命中")

    total = sum(scores.values())
    if total <= 0.0:
        return {
            "page": page_no,
            "type": "mixed",
            "scores": {key: 0.0 for key in PAGE_TYPES},
            "signals": ["无显著特征"],
        }

    normalized_scores = {
        key: round(value / total, 4) for key, value in scores.items()
    }
    top_type, top_score = max(normalized_scores.items(), key=lambda item: item[1])
    forced_type = None
    if page_no <= 2 and toc_codes >= 3:
        forced_type = "toc"
    elif page_no <= 4 and ("目录" in compact_text or "ht_toc" in compact_text):
        forced_type = "toc"
    elif "动作图" in compact_text and (hydraulic_codes >= 4 or electric_codes >= 3):
        forced_type = "legend_or_rule"
    if top_score < 0.34:
        top_type = "mixed"
    if forced_type:
        top_type = forced_type
    return {
        "page": page_no,
        "type": top_type,
        "scores": normalized_scores,
        "signals": signals or ["规则弱命中"],
    }


def classify_pages(pages: List[Dict]) -> List[Dict]:
    return [classify_page(page) for page in pages]


def classify_document_family(pages: List[Dict], page_types: List[Dict]) -> tuple[str, Dict[str, float]]:
    family_scores = {family: 0.0 for family in DOCUMENT_FAMILIES}
    type_counts = {page_type: 0 for page_type in PAGE_TYPES}
    total_text = 0
    noise_hits = 0
    numeric_dense_pages = 0

    for page, page_type in zip(pages, page_types):
        page_kind = page_type.get("type", "mixed")
        type_counts[page_kind] = type_counts.get(page_kind, 0) + 1
        text = _normalized_text(page.get("text", ""))
        compact_text = text.replace(" ", "")
        total_text += len(text)
        noise_hits += _keyword_hits(text, KEYWORDS["noise"])
        numeric_hits = len(re.findall(r"\d+(?:\.\d+)?", compact_text))
        unit_hits = sum(compact_text.count(unit) for unit in ("mm", "kn", "mpa", "kw", "cm3", "g/s", "r/min", "℃", "%"))
        if numeric_hits >= 20 or unit_hits >= 2:
            numeric_dense_pages += 1

    page_count = max(len(pages), 1)
    avg_text = total_text / page_count

    family_scores["drawing-heavy"] = (
        type_counts.get("drawing", 0) * 2.2
        + type_counts.get("catalog_table", 0) * 1.1
        + type_counts.get("legend_or_rule", 0) * 1.5
    )
    family_scores["manual-like"] = (
        type_counts.get("description", 0) * 2.0
        + type_counts.get("toc", 0) * 1.2
        + (0.8 if avg_text > 450 else 0.0)
        + _keyword_hits(" ".join(_normalized_text(page.get("text", "")) for page in pages[:5]), KEYWORDS["manual"]) * 1.3
    )
    family_scores["table-heavy"] = (
        type_counts.get("catalog_table", 0) * 2.4
        + _keyword_hits(" ".join(_normalized_text(page.get("text", "")) for page in pages[:5]), KEYWORDS["table"]) * 1.2
        + (1.0 if noise_hits >= 2 else 0.0)
        + numeric_dense_pages * 1.4
        + (1.8 if type_counts.get("catalog_table", 0) >= 1 and numeric_dense_pages >= 2 else 0.0)
    )
    family_scores["mixed"] = (
        type_counts.get("mixed", 0) * 1.2
        + math.sqrt(page_count)
        + (1.0 if len({page_type.get("type") for page_type in page_types}) >= 4 else 0.0)
    )

    total_score = sum(family_scores.values()) or 1.0
    normalized_scores = {key: round(value / total_score, 4) for key, value in family_scores.items()}
    top_family, top_score = max(normalized_scores.items(), key=lambda item: item[1])
    if type_counts.get("drawing", 0) >= max(4, int(page_count * 0.3)):
        top_family = "drawing-heavy"
    elif type_counts.get("description", 0) >= max(4, int(page_count * 0.5)):
        top_family = "manual-like"
    elif type_counts.get("catalog_table", 0) >= 1 and numeric_dense_pages >= 2:
        top_family = "table-heavy"
    elif top_score < 0.38:
        top_family = "mixed"
    return top_family, normalized_scores
