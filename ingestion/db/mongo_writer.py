# -*- coding: utf-8 -*-
"""
db/mongo_writer.py - MongoDB 文档写入

【设计】
继承 BaseWriter 自动获得分批 upsert / delete_by_ids 能力。

写入目标：documents collection（与 backend DocumentModel 一致）
文档格式：
  document_id     str      主键（对应 Milvus id）
  content         str      正文或切片文本
  collection_name str      所属 collection
  metadata        dict     业务 metadata

metadata 规则：
  - 以 record 原始字段为准
  - writer 仅排除向量写库保留字段：id / vector / text / chunk_raw_text
  - 其余业务字段一律原样保留
  - writer 只补一个兜底字段：is_deleted=False（若 record 未显式提供）

upsert 条件：{document_id: xxx, collection_name: xxx}

【批次策略】
单批 <= 1000 条。MongoDB bulk_write 理论上可以更大（100k），
但保守一些便于错误定位、内存可控。
"""

import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

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

MONGO_BATCH_SIZE = 1000

_MONGO_METADATA_RESERVED_KEYS = {"id", "vector", "text", "chunk_raw_text"}


# ════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════

def _build_document_metadata(record: dict) -> Dict[str, object]:
    """构造 MongoDB documents.metadata 字段。"""
    metadata = {}
    for key, value in record.items():
        if key in _MONGO_METADATA_RESERVED_KEYS:
            continue
        metadata[key] = value

    collection_hint = str(
        record.get("knowledge_type") or record.get("collection_name") or ""
    )
    metadata = normalize_metadata_for_ingest(
        collection_hint,
        metadata,
        include_alias_keys=False,
        strict_conflicts=True,
        require_collection_type=True,
    )
    metadata["is_deleted"] = bool(metadata.get("is_deleted", False))
    return metadata


def _classify_mongo_exception(exc: Exception, batch_size: int) -> Exception:
    """把 pymongo 原生异常分类为 ingestion 异常。"""
    err_msg = str(exc)
    err_type = type(exc).__name__

    # 临时性错误（重试可解）
    if any(k in err_msg for k in (
        "NotMasterError", "NotPrimaryNoSecondaryOk",
        "NetworkTimeout", "ConnectionFailure",
        "ExecutionTimeout",
    )) or err_type in ("AutoReconnect", "NetworkTimeout"):
        return WriterTransientError(
            "MongoDB 临时不可用",
            context={"batch_size": batch_size, "raw_error_type": err_type},
            cause=exc,
        )

    # 连接错误
    if any(k in err_msg.lower() for k in (
        "connection refused", "no route", "timeout", "connect failed",
    )) or err_type in ("ConnectionFailure", "ServerSelectionTimeoutError"):
        return WriterConnectionError(
            "MongoDB 连接失败",
            context={"batch_size": batch_size, "raw_error_type": err_type},
            cause=exc,
        )

    # 其他（schema/权限/参数等）→ Fatal
    return WriterFatalError(
        f"MongoDB 写入失败: {err_msg[:100]}",
        context={"batch_size": batch_size, "raw_error_type": err_type},
        cause=exc,
    )


# ════════════════════════════════════════════════════════════════
# MongoWriter
# ════════════════════════════════════════════════════════════════

class MongoWriter(BaseWriter):
    """
    MongoDB 写入客户端，schema 对齐 backend（documents collection）。

    继承 BaseWriter 自动获得分批 upsert / delete_by_ids 能力。

    【向后兼容】
      - upsert_chunks(collection_name, records) 保留，内部转发 BaseWriter.upsert
    """

    def __init__(self):
        super().__init__(batch_size=MONGO_BATCH_SIZE)

        host = os.getenv("MONGO_HOST", "mongo")
        port = int(os.getenv("MONGO_PORT", "27017"))
        user = os.getenv("MONGO_USER", "")
        password = os.getenv("MONGO_PASSWORD", "")
        db_name = os.getenv("MONGO_DATABASE", "ai_fault_diagnosis")
        auth_source = os.getenv("MONGO_AUTH_SOURCE", "admin")

        self._db_name = db_name
        self._client = self._connect(host, port, user, password, db_name, auth_source)
        self._db = self._client[db_name]

    def _connect(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        db_name: str,
        auth_source: str,
        max_retries: int = 3,
        delay: float = 2.0,
    ):
        from pymongo import MongoClient

        if user and password:
            uri = f"mongodb://{user}:{password}@{host}:{port}/{db_name}?authSource={auth_source}"
        else:
            uri = f"mongodb://{host}:{port}/{db_name}"

        for attempt in range(max_retries):
            try:
                client = MongoClient(uri, serverSelectionTimeoutMS=10_000)
                client.admin.command("ping")
                logger.info(f"MongoDB 连接成功: {host}:{port}/{db_name}")
                return client
            except Exception as e:
                logger.warning(f"MongoDB 连接失败 ({attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(delay)

        raise WriterConnectionError(
            f"MongoDB 连接失败，已重试 {max_retries} 次",
            context={"host": host, "port": port, "db": db_name},
        )

    def close(self) -> None:
        try:
            if self._client is not None:
                self._client.close()
                logger.info("MongoDB 连接已断开")
        except Exception as e:
            logger.warning(f"MongoDB 关闭失败（忽略）: {e}")
        finally:
            self._closed = True

    # ── BaseWriter 必须实现的抽象方法 ────────────────────────

    def ensure_collection(self, collection_name: str, **kwargs) -> None:
        """MongoDB 集合在插入时自动创建，此方法为空操作。"""
        pass

    def _upsert_batch(self, collection_name: str, batch: List[dict]) -> int:
        """
        单批 upsert 到 MongoDB documents collection。
        由 BaseWriter.upsert() 自动分批调用。
        """
        from pymongo import UpdateOne

        if not batch:
            return 0

        try:
            documents_col = self._db["documents"]
            operations = []
            for record in batch:
                document_id = str(record["id"])[:256]
                content = str(
                    record.get("chunk_raw_text") or record.get("text", "") or ""
                )
                metadata = _build_document_metadata(record)
                doc = {
                    "document_id": document_id,
                    "content": content,
                    "collection_name": collection_name,
                    "metadata": metadata,
                }
                operations.append(
                    UpdateOne(
                        {"document_id": document_id, "collection_name": collection_name},
                        {"$set": doc},
                        upsert=True,
                    )
                )

            result = documents_col.bulk_write(operations, ordered=False)
            written = result.upserted_count + result.matched_count
            logger.info(
                f"MongoDB 单批写入成功: upserted={result.upserted_count}, "
                f"matched={result.matched_count}, modified={result.modified_count}"
            )
            return written

        except (WriterConnectionError, WriterTransientError, WriterFatalError):
            raise
        except Exception as exc:
            classified = _classify_mongo_exception(exc, batch_size=len(batch))
            logger.warning(
                f"MongoDB 单批写入失败 -> 分类为 {type(classified).__name__}: {classified}"
            )
            raise classified

    def _delete_by_ids_batch(self, collection_name: str, ids: List[str]) -> int:
        """
        单批按 ID 软删除（设 metadata.is_deleted=True）。
        与 serving 侧 DocumentModel 软删策略对齐。
        由 BaseWriter.delete_by_ids() 自动分批调用。
        """
        if not ids:
            return 0

        try:
            documents_col = self._db["documents"]
            result = documents_col.update_many(
                {
                    "document_id": {"$in": [str(i)[:256] for i in ids]},
                    "collection_name": collection_name,
                },
                {"$set": {"metadata.is_deleted": True}},
            )
            return result.modified_count

        except (WriterConnectionError, WriterTransientError, WriterFatalError):
            raise
        except Exception as exc:
            classified = _classify_mongo_exception(exc, batch_size=len(ids))
            logger.warning(
                f"MongoDB 单批软删除失败 -> 分类为 {type(classified).__name__}: {classified}"
            )
            raise classified

    # ── 向后兼容方法 ─────────────────────────────────────────

    def upsert_chunks(self, collection_name: str, records: List[dict]) -> int:
        """
        【向后兼容方法】老 agent 调用此方法。
        内部转发到 BaseWriter.upsert（自动分批）。
        """
        return self.upsert(collection_name, records)


# ════════════════════════════════════════════════════════════════
# 公共导出
# ════════════════════════════════════════════════════════════════

__all__ = [
    "MongoWriter",
    "MONGO_BATCH_SIZE",
]
