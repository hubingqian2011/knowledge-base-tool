# -*- coding: utf-8 -*-
"""
shared/schema/entity_types.py — 枚举常量

改这里，三个模块同步感知：
  ingestion 写入时引用 → serving 读取时引用 → evaluation 不直接使用但保持一致
"""
from enum import StrEnum


class EntityType(StrEnum):
    """Milvus metadata.entity_type 的合法值"""
    SUMMARY = "summary"
    ENTITY = "entity"
    RELATION = "relation"


class ChunkType(StrEnum):
    """Milvus metadata.chunk_type 的合法值"""
    CHAPTER = "chapter"
    SLIDING_WINDOW = "sliding_window"


class KnowledgeType(StrEnum):
    """Milvus metadata.knowledge_type 的合法值"""
    MANUAL = "manual"
    WORKORDER = "workorder"
    PARAMETER = "parameter"
    PRINCIPLE = "principle"
    VIDEO = "video"
