# -*- coding: utf-8 -*-
"""
shared/schema/record_schema.py — Milvus / MongoDB 记录结构定义

TypedDict 定义：仅用于类型提示和文档化，不强制运行时校验。
ingestion 写入时参照此定义构造 record，Backend 读取时参照此定义解析 metadata。

字段来源：
  ingestion/db/milvus_writer.py   → MilvusMetadata (写入 Milvus metadata JSON)
  ingestion/db/mongo_writer.py    → MongoDocument  (写入 MongoDB documents collection)
  ingestion/manual_ingest_agent.py _build_graph_chunk_records() → GraphRAG 扩展字段
"""
from typing import TypedDict, Optional, List


class ManualChunkRecord(TypedDict, total=False):
    """manual_chunks collection 的完整记录结构（入库时构造）

    必填字段：id, text, vector
    其余字段根据 entity_type 不同可选
    """
    # ── Milvus 顶层字段 ──
    id: str                      # 主键，格式：{model}__{slice_id}__{entity_type}[__{name}]
    text: str                    # embedding 源文本（summary / entity描述 / relation描述）
    # vector 不定义在 TypedDict 里（List[float] 太大，由 embedding 管线生成）

    # ── 共享 metadata 字段 ──
    model: str                   # 机型名称
    language: str                # "CN" / "EN"
    controller_type: str         # 参数库专用字段：控制器型号（可选）
    knowledge_type: str          # KnowledgeType 枚举值，manual 固定为 "manual"
    is_deleted: bool             # 软删除标志，固定 False

    # ── manual 独有字段 ──
    title: str                   # 所属章节标题
    slice_id: str                # 切片 ID，格式如 "3.6.__p131_142"
    section_number: str          # 章节编号，如 "3.6."
    page_start: int              # 起始页（1-based）
    page_end: int                # 结束页（含）
    source_file: str             # 原始 PDF 文件名
    slice_pdf_path: str          # 章节子 PDF 相对路径
    chunk_type: str              # ChunkType 枚举值

    # ── GraphRAG 扩展字段 ──
    entity_type: str             # EntityType 枚举值：summary / entity / relation
    chunk_raw_text: str          # PDF 原文（2页拼接），所有子记录共享
    entity_name: str             # entity 记录专用：实体名称
    entity_category: str         # entity 记录专用：实体类别
    rel_src: str                 # relation 记录专用：关系源
    rel_type: str                # relation 记录专用：关系类型
    rel_tgt: str                 # relation 记录专用：关系目标

    # ── 机型扩展标签（CLI 人工填写）──
    generation: str              # 代次，如 "一代" "二代" "三代"
    controller: str              # 通用标准字段：控制器型号
    product_line: str            # 产品线，如 "二板机" "三板机" "全电机"
    tonnage: str                 # 锁模力（吨），如 "650" "1600"
    series: str                  # 机型系列，如 "MA" "MG" "HTF"

    # ── 入库元信息 ──
    ingested_at: str             # ISO 8601 时间戳
    manual_type: str             # 人类可读描述
    window: int                  # 滑窗大小
    overlap: int                 # 重叠页数


class MongoDocument(TypedDict, total=False):
    """MongoDB documents collection 的文档结构

    upsert 条件：{document_id, collection_name}
    """
    document_id: str             # 主键，对应 Milvus id
    content: str                 # 正文：chunk_raw_text 优先，text 兜底
    collection_name: str         # 所属 collection（manual_total / manual_chunks）
    metadata: dict               # 业务字段（与 Milvus metadata 结构一致）
