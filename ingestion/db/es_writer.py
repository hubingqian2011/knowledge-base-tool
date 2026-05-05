# -*- coding: utf-8 -*-
"""
db/es_writer.py - Elasticsearch 全文索引写入

【设计】
继承 BaseWriter 自动获得分批能力。
ES 写入失败在上层是可容忍的（不阻断），但底层仍按规范分类异常。

Index mapping (v2):
  id             keyword
  title          text (ik_max_word) + keyword subfield
  text           text (ik_max_word)
  model          keyword
  slice_id       keyword
  section_number keyword
  page_start     integer
  page_end       integer
  source_file    keyword
  chunk_type     keyword
  ingested_at    date

优先使用 ik_max_word 中文分词，未安装 IK 插件时自动降级为 standard。
ES 8.x 默认开启 HTTPS + 自签名证书，使用 verify_certs=False 兼容。
bulk upsert，按 _id 去重，不存储 vector 字段。

【批次策略】
单批 <= 500 条，ES bulk 推荐范围 100-1000，500 是常规安全值。
"""

import logging
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

# 路径设置：确保 ingestion/ 和项目根目录在 sys.path
_INGESTION_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _INGESTION_DIR.parent
for _p in (_INGESTION_DIR, _PROJECT_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared.schema.metadata_normalizer import normalize_metadata_for_ingest

from core.errors import (
    WriterConnectionError,
    WriterFatalError,
    WriterTransientError,
)
from db.base_writer import BaseWriter

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 常量
# ════════════════════════════════════════════════════════════════

ES_BATCH_SIZE = 500

_ES_STRUCTURAL_KEYS = {
    "id",
    "vector",
    "text",
    "chunk_raw_text",
    "title",
    "slice_id",
    "section_number",
    "page_start",
    "page_end",
    "source_file",
    "chunk_type",
    "ingested_at",
    "collection_name",
    "knowledge_type",
}


# ════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════

def _normalize_es_record(record: dict) -> dict:
    """
    ES 文档规范化：
    - 结构化字段直接提取
    - 其他业务字段通过 metadata_normalizer 规范化后合并
    - 不存储 vector 字段
    """
    normalized_record = {
        key: value for key, value in record.items()
        if key in _ES_STRUCTURAL_KEYS and key != "vector"
    }
    metadata = {key: value for key, value in record.items() if key not in _ES_STRUCTURAL_KEYS}
    collection_hint = str(record.get("knowledge_type") or record.get("collection_name") or "")
    normalized_metadata = normalize_metadata_for_ingest(
        collection_hint,
        metadata,
        include_alias_keys=False,
        strict_conflicts=True,
        require_collection_type=True,
    )
    normalized_record.update(normalized_metadata)
    return normalized_record


def _build_mapping(analyzer: str) -> dict:
    """构造 ES index mapping。"""
    text_field = {
        "type": "text",
        "analyzer": analyzer,
        "fields": {"keyword": {"type": "keyword", "ignore_above": 512}},
    }
    if analyzer == "ik_max_word":
        text_field["search_analyzer"] = "ik_smart"

    return {
        "mappings": {
            "properties": {
                "id":             {"type": "keyword"},
                "title":          text_field,
                "text":           {
                    "type": "text",
                    "analyzer": analyzer,
                    **({"search_analyzer": "ik_smart"} if analyzer == "ik_max_word" else {}),
                },
                "model":          {"type": "keyword"},
                "slice_id":       {"type": "keyword"},
                "section_number": {"type": "keyword"},
                "page_start":     {"type": "integer"},
                "page_end":       {"type": "integer"},
                "source_file":    {"type": "keyword"},
                "chunk_type":     {"type": "keyword"},
                "ingested_at":    {"type": "date"},
            }
        }
    }


def _classify_es_exception(exc: Exception, batch_size: int) -> Exception:
    """把 elasticsearch 原生异常分类为 ingestion 异常。"""
    err_msg = str(exc)
    err_type = type(exc).__name__

    if err_type in ("ConnectionError", "ConnectionTimeout"):
        return WriterConnectionError(
            "ES 连接失败",
            context={"batch_size": batch_size, "raw_error_type": err_type},
            cause=exc,
        )

    if any(k in err_msg.lower() for k in (
        "timeout", "connection refused", "unavailable",
    )) or err_type == "TransportError":
        return WriterTransientError(
            "ES 临时不可用",
            context={"batch_size": batch_size, "raw_error_type": err_type},
            cause=exc,
        )

    return WriterFatalError(
        f"ES 写入失败: {err_msg[:100]}",
        context={"batch_size": batch_size, "raw_error_type": err_type},
        cause=exc,
    )


# ════════════════════════════════════════════════════════════════
# ESWriter
# ════════════════════════════════════════════════════════════════

class ESWriter(BaseWriter):
    """
    Elasticsearch 写入客户端。

    继承 BaseWriter 自动获得分批能力。

    【向后兼容】
      - ensure_index(index_name) 保留
      - upsert(index_name, records) 继承自 BaseWriter（签名不变）
    """

    def __init__(self):
        super().__init__(batch_size=ES_BATCH_SIZE)

        host = os.getenv("ES_HOST", "es01")
        port = int(os.getenv("ES_PORT", "9200"))
        user = os.getenv("ES_USER", "elastic")
        password = os.getenv("ES_PASSWORD", "elastic")
        scheme = os.getenv("ES_SCHEME", "http")

        self._host = host
        self._port = port
        self._scheme = scheme
        self._client = self._connect(host, port, user, password, scheme)

    def _connect(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        scheme: str,
        max_retries: int = 3,
        delay: float = 2.0,
    ):
        from elasticsearch import Elasticsearch

        for attempt in range(max_retries):
            try:
                client = Elasticsearch(
                    f"{scheme}://{host}:{port}",
                    basic_auth=(user, password),
                    verify_certs=False,      # ES 8.x 自签名证书兼容
                    ssl_show_warn=False,     # 静默 SSL 警告
                    request_timeout=30,
                    retry_on_timeout=True,
                    max_retries=3,
                )
                info = client.info()
                logger.info(
                    f"Elasticsearch 连接成功: {scheme}://{host}:{port}, "
                    f"version={info['version']['number']}"
                )
                return client
            except Exception as e:
                logger.warning(f"ES 连接失败 ({attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(delay)

        raise WriterConnectionError(
            f"Elasticsearch 连接失败，已重试 {max_retries} 次",
            context={"host": host, "port": port, "scheme": scheme},
        )

    def close(self) -> None:
        try:
            if self._client is not None:
                self._client.close()
                logger.info("ES 连接已关闭")
        except Exception as e:
            logger.warning(f"ES 关闭失败（忽略）: {e}")
        finally:
            self._closed = True

    # ── BaseWriter 必须实现的抽象方法 ────────────────────────

    def ensure_collection(self, collection_name: str, **kwargs) -> None:
        """
        映射到 ensure_index，满足 BaseWriter 抽象接口。
        老代码直接调用 ensure_index 同样有效。
        """
        self.ensure_index(collection_name)

    def _upsert_batch(self, index_name: str, batch: List[dict]) -> int:
        """
        单批 bulk index 到 ES。
        由 BaseWriter.upsert() 自动分批调用。
        ES 部分失败只记日志，不抛（与项目惯例一致）。
        """
        from elasticsearch.helpers import bulk

        if not batch:
            return 0

        try:
            actions = [
                {
                    "_index": index_name,
                    "_id": record["id"],
                    "_op_type": "index",
                    "_source": _normalize_es_record(record),
                }
                for record in batch
            ]

            success, errors = bulk(
                self._client, actions,
                raise_on_error=False, stats_only=False,
            )

            if errors:
                # 部分失败：记日志但不抛（ES 失败可容忍）
                logger.warning(
                    f"ES bulk 部分失败: {len(errors)}/{len(batch)} 条失败"
                )
                for err in errors[:3]:
                    logger.warning(f"  ES 错误样本: {err}")

            return success

        except (WriterConnectionError, WriterTransientError, WriterFatalError):
            raise
        except Exception as exc:
            classified = _classify_es_exception(exc, batch_size=len(batch))
            logger.warning(
                f"ES 单批写入失败 -> 分类为 {type(classified).__name__}: {classified}"
            )
            raise classified

    def _delete_by_ids_batch(self, index_name: str, ids: List[str]) -> int:
        """
        单批按 ID 删除 ES 文档。
        由 BaseWriter.delete_by_ids() 自动分批调用。
        找不到 ID 时静默（delete 操作 404 正常）。
        """
        from elasticsearch.helpers import bulk

        if not ids:
            return 0

        try:
            actions = [
                {
                    "_index": index_name,
                    "_id": str(i),
                    "_op_type": "delete",
                }
                for i in ids
            ]

            success, errors = bulk(
                self._client, actions,
                raise_on_error=False, stats_only=False,
            )

            if errors:
                # delete 404 很常见（ID 不存在），降为 debug
                logger.debug(f"ES 删除部分未命中: {len(errors)} 条（可能 ID 不存在）")

            return success

        except (WriterConnectionError, WriterTransientError, WriterFatalError):
            raise
        except Exception as exc:
            classified = _classify_es_exception(exc, batch_size=len(ids))
            logger.warning(
                f"ES 单批删除失败 -> 分类为 {type(classified).__name__}: {classified}"
            )
            raise classified

    # ── 向后兼容方法 ─────────────────────────────────────────

    def ensure_index(self, index_name: str) -> None:
        """
        若 index 不存在则创建；优先 IK，降级 standard。
        【向后兼容】老 agent 直接调用此方法。
        """
        if self._client.indices.exists(index=index_name):
            logger.info(f"ES index 已存在: {index_name}")
            return

        for analyzer in ("ik_max_word", "standard"):
            try:
                self._client.indices.create(
                    index=index_name, body=_build_mapping(analyzer)
                )
                logger.info(f"ES index 创建成功（{analyzer}）: {index_name}")
                return
            except Exception as e:
                err_str = str(e).lower()
                if analyzer == "ik_max_word" and ("analyzer" in err_str or "ik" in err_str):
                    logger.warning(f"IK 未安装，降级为 standard: {e}")
                    continue
                raise


# ════════════════════════════════════════════════════════════════
# 公共导出
# ════════════════════════════════════════════════════════════════

__all__ = [
    "ESWriter",
    "ES_BATCH_SIZE",
]
