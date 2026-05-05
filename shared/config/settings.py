# -*- coding: utf-8 -*-
"""
shared/config/settings.py — 统一配置（Single Source of Truth）

所有模块（ingestion / serving / evaluation）共享的配置常量。
各模块通过 from shared.config.settings import ... 引用，不再各自硬编码。
"""
from dotenv import load_dotenv
import os

load_dotenv()

# ── Embedding ────────────────────────────────────────────────────────
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v4")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "2048"))

# ── Milvus Collections ───────────────────────────────────────────────
# 逗号分隔，支持通过环境变量扩展为多个并行检索 collection
COLLECTION_MANUAL = os.getenv("COLLECTION_MANUAL", "manual_total,manual_chunks")
COLLECTION_PRINCIPLE = os.getenv("COLLECTION_PRINCIPLE", "principle_total,principle_chunks")
COLLECTION_REPAIR = os.getenv("COLLECTION_REPAIR", "repair_doc_chunks")
COLLECTION_VIDEO = os.getenv("COLLECTION_VIDEO", "video_description")
COLLECTION_WORKORDER = os.getenv("COLLECTION_WORKORDER", "workorder_graph,workorder,special_workorder")
COLLECTION_PARAMETER = os.getenv("COLLECTION_PARAMETER", "")
COLLECTION_WORKORDER_GRAPH = os.getenv("COLLECTION_WORKORDER_GRAPH", "workorder_graph")

_COLLECTION_MANUAL_PARTS = [c.strip() for c in COLLECTION_MANUAL.split(",") if c.strip()]
COLLECTION_MANUAL_TOTAL = _COLLECTION_MANUAL_PARTS[0] if _COLLECTION_MANUAL_PARTS else "manual_total"
COLLECTION_MANUAL_CHUNKS = _COLLECTION_MANUAL_PARTS[1] if len(_COLLECTION_MANUAL_PARTS) > 1 else "manual_chunks"

_COLLECTION_PRINCIPLE_PARTS = [c.strip() for c in COLLECTION_PRINCIPLE.split(",") if c.strip()]
COLLECTION_PRINCIPLE_TOTAL = _COLLECTION_PRINCIPLE_PARTS[0] if _COLLECTION_PRINCIPLE_PARTS else "principle_total"
COLLECTION_PRINCIPLE_CHUNKS = _COLLECTION_PRINCIPLE_PARTS[1] if len(_COLLECTION_PRINCIPLE_PARTS) > 1 else "principle_chunks"

# ── Vector Search ────────────────────────────────────────────────────
MILVUS_METRIC_TYPE = "IP"
MILVUS_INDEX_TYPE = "IVF_FLAT"
MILVUS_NLIST = 1024
VECTOR_SEARCH_SCORE_THRESHOLD = float(
    os.getenv("VECTOR_SEARCH_SCORE_THRESHOLD", "0.45"))

# ── GraphRAG ─────────────────────────────────────────────────────────
ENTITY_BOOST_FACTOR = float(os.getenv("ENTITY_BOOST_FACTOR", "1.2"))
SLIDING_WINDOW_SIZE = int(os.getenv("SLIDING_WINDOW_SIZE", "2"))
SLIDING_WINDOW_OVERLAP = int(os.getenv("SLIDING_WINDOW_OVERLAP", "1"))

# ── LLM ──────────────────────────────────────────────────────────────
LLM_MODEL_FAST = os.getenv("LLM_MODEL_FAST", "qwen3.5-flash")
LLM_MODEL_STRONG = os.getenv("LLM_MODEL_STRONG", "qwen3-max")
LLM_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
