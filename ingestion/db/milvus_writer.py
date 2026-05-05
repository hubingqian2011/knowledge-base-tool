# -*- coding: utf-8 -*-
"""
db/milvus_writer.py - Milvus 向量数据写入

【设计】
继承 BaseWriter，自动获得分批能力。子类只实现单批操作。

Collection schema:
  id       VARCHAR(256)      主键
  vector   FLOAT_VECTOR(dim)
  metadata JSON              业务 metadata

【批次策略】
单批 <= 500 条，远低于 Milvus gRPC 64MB 限制。
2048 维 float32: 500 × 2048 × 4 = 4MB，加 metadata 约 5-8MB。

【错误处理】
- WriterPayloadTooLarge（FatalError）：单批超 64MB（理论上不该发生，因为已分批）
- WriterTransientError（RetryableError）：gRPC UNAVAILABLE / DEADLINE_EXCEEDED 等
- WriterFatalError（FatalError）：schema 不兼容、collection 不存在
- WriterConnectionError（RetryableError）：连接失败
"""

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# 确保 ingestion/ 和项目根目录在 sys.path
# - ingestion/: 让 db/ 能 import core/ 和 db/base_writer
# - 项目根 (X-Manubot/): 让 shared/ 可以被 import
_INGESTION_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _INGESTION_DIR.parent
for _p in (_INGESTION_DIR, _PROJECT_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared.schema.metadata_normalizer import normalize_metadata_for_ingest

from core.errors import (
    WriterConnectionError,
    WriterFatalError,
    WriterPayloadTooLarge,
    WriterTransientError,
)
from db.base_writer import BaseWriter

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 常量
# ════════════════════════════════════════════════════════════════

# 单批 Milvus insert 最多条数。2048 维 float32 × 500 条 ≈ 4MB，
# 加 metadata 约 5-8MB，远低于 gRPC 64MB 阈值。
MILVUS_BATCH_SIZE = 500

# 不进入 metadata 字段的保留 key
_MILVUS_METADATA_RESERVED_KEYS = {"id", "vector", "text", "chunk_raw_text"}


# ════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════

def _build_vector_metadata(record: dict) -> Dict[str, object]:
    """从 record 构造 Milvus 的 metadata 字段。"""
    metadata = {}
    for key, value in record.items():
        if key in _MILVUS_METADATA_RESERVED_KEYS:
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


def _classify_milvus_exception(exc: Exception, batch_size: int) -> Exception:
    """
    把 pymilvus 抛出的原生异常分类为 ingestion 自定义异常。

    分类规则：
    - RESOURCE_EXHAUSTED / larger than max  → WriterPayloadTooLarge（Fatal，不可重试）
    - UNAVAILABLE / DEADLINE_EXCEEDED       → WriterTransientError（可重试）
    - connection refused / no route         → WriterConnectionError（可重试）
    - 其他                                  → WriterFatalError（保守，不重试）
    """
    err_msg = str(exc)
    err_type = type(exc).__name__

    if "RESOURCE_EXHAUSTED" in err_msg or "larger than max" in err_msg:
        return WriterPayloadTooLarge(
            f"Milvus 单批 payload 过大（已分批 {batch_size} 条仍超限）",
            context={"batch_size": batch_size, "raw_error_type": err_type},
            cause=exc,
        )

    if any(k in err_msg for k in ("UNAVAILABLE", "DEADLINE_EXCEEDED", "RST_STREAM")):
        return WriterTransientError(
            "Milvus 临时不可用",
            context={"batch_size": batch_size, "raw_error_type": err_type},
            cause=exc,
        )

    if any(k in err_msg.lower() for k in ("connection refused", "no route", "timeout", "connect failed")):
        return WriterConnectionError(
            "Milvus 连接失败",
            context={"batch_size": batch_size, "raw_error_type": err_type},
            cause=exc,
        )

    return WriterFatalError(
        f"Milvus 写入失败: {err_msg[:100]}",
        context={"batch_size": batch_size, "raw_error_type": err_type},
        cause=exc,
    )


# ════════════════════════════════════════════════════════════════
# MilvusWriter 主类
# ════════════════════════════════════════════════════════════════

class MilvusWriter(BaseWriter):
    """
    Milvus 写入客户端。schema 对齐 backend 的 {id, vector, metadata JSON}。

    继承 BaseWriter 自动获得分批 upsert / delete_by_ids 能力。
    子类只需实现 _upsert_batch / _delete_by_ids_batch / close。

    【向后兼容】
    公开方法签名与原版保持一致：
      - upsert(collection_name, records) -> int         （继承自 BaseWriter）
      - ensure_collection(collection_name, dim=2048)    （保留）
      - drop_collection(collection_name) -> bool         （保留）
      - close()                                          （实现 BaseWriter.close）
    """

    def __init__(self):
        super().__init__(batch_size=MILVUS_BATCH_SIZE)
        self._host = os.getenv("MILVUS_HOST", "standalone")
        self._port = int(os.getenv("MILVUS_PORT", "19530"))
        self._user = os.getenv("MILVUS_USER", "")
        self._password = os.getenv("MILVUS_PASSWORD", "")
        self._connect()

    # ── 连接管理 ──────────────────────────────────────────────

    def _connect(self, max_retries: int = 3, delay: float = 2.0) -> None:
        from pymilvus import connections

        for attempt in range(max_retries):
            try:
                connections.connect(
                    alias="default",
                    host=self._host,
                    port=self._port,
                    user=self._user,
                    password=self._password,
                    timeout=10,
                )
                logger.info(f"Milvus 连接成功: {self._host}:{self._port}")
                return
            except Exception as exc:
                logger.warning(f"Milvus 连接失败 ({attempt + 1}/{max_retries}): {exc}")
                if attempt < max_retries - 1:
                    time.sleep(delay)

        raise WriterConnectionError(
            f"Milvus 连接失败，已重试 {max_retries} 次",
            context={"host": self._host, "port": self._port},
        )

    def close(self) -> None:
        """关闭 Milvus 连接。BaseWriter.__exit__ 会调用此方法。"""
        try:
            from pymilvus import connections
            connections.disconnect("default")
            logger.info("Milvus 连接已断开")
        except Exception as e:
            logger.warning(f"Milvus 断开连接失败（忽略）: {e}")
        finally:
            self._closed = True

    # ── Collection 管理 ──────────────────────────────────────

    def ensure_collection(self, collection_name: str, dim: int = 2048):
        """
        确保 collection 存在。不存在则创建并建立 IVF_FLAT 索引。

        【向后兼容】保留原签名，老 agent 直接调用此方法。

        Args:
            collection_name: collection 名称
            dim: 向量维度，默认 2048

        Returns:
            pymilvus.Collection 对象
        """
        from pymilvus import (
            Collection, CollectionSchema, DataType, FieldSchema, utility,
        )

        if utility.has_collection(collection_name):
            logger.info(f"Milvus collection 已存在: {collection_name}")
            return Collection(collection_name)

        logger.info(f"创建 Milvus collection: {collection_name}, dim={dim}")
        fields = [
            FieldSchema(
                name="id",
                dtype=DataType.VARCHAR,
                is_primary=True,
                auto_id=False,
                max_length=256,
            ),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dim),
            FieldSchema(name="metadata", dtype=DataType.JSON),
        ]
        schema = CollectionSchema(fields, description=f"{collection_name} knowledge")
        collection = Collection(collection_name, schema)
        collection.create_index(
            "vector",
            {"index_type": "IVF_FLAT", "metric_type": "IP", "params": {"nlist": 1024}},
        )
        logger.info(f"Milvus collection 创建成功: {collection_name}")
        return collection

    def drop_collection(self, collection_name: str) -> bool:
        """删除 collection（用于测试/重置）。"""
        from pymilvus import utility

        try:
            if utility.has_collection(collection_name):
                utility.drop_collection(collection_name)
                logger.info(f"Milvus collection 已删除: {collection_name}")
                return True
            logger.info(f"Milvus collection 不存在，无需删除: {collection_name}")
            return False
        except Exception as exc:
            logger.error(f"删除 Milvus collection 失败: {collection_name}, error={exc}")
            return False

    # ── BaseWriter 钩子：单批实现 ────────────────────────────

    def _upsert_batch(self, collection_name: str, batch: List[dict]) -> int:
        """
        写入单批数据到 Milvus。由 BaseWriter.upsert() 自动分批调用。

        Args:
            collection_name: Milvus collection 名
            batch:           单批 records，长度 <= MILVUS_BATCH_SIZE

        Returns:
            实际写入的条数

        Raises:
            WriterPayloadTooLarge: 单批仍超 64MB（异常情况，说明分批配置有误）
            WriterTransientError:  gRPC 临时错误，可重试
            WriterConnectionError: 连接错误，可重试
            WriterFatalError:      schema 错、参数错等，不可重试
        """
        from pymilvus import Collection

        if not batch:
            return 0

        try:
            collection = Collection(collection_name)
            collection.load()

            ids = [str(record["id"])[:256] for record in batch]

            # upsert 语义：先 delete 同 ID（覆盖写兼容）
            try:
                ids_expr = ", ".join(f"'{item_id}'" for item_id in ids)
                collection.delete(f"id in [{ids_expr}]")
            except Exception as exc:
                # delete 失败通常是首次写入或 ID 不存在，仅警告
                logger.warning(f"Milvus delete 失败（可能首次写入）: {exc}")

            entities = [
                {
                    "id": str(record["id"])[:256],
                    "vector": record["vector"],
                    "metadata": _build_vector_metadata(record),
                }
                for record in batch
            ]

            # insert + flush 确保数据落盘
            collection.insert(entities)
            collection.flush()

            return len(batch)

        except (WriterPayloadTooLarge, WriterTransientError,
                WriterConnectionError, WriterFatalError):
            # 已分类的异常直接往上传
            raise
        except Exception as exc:
            classified = _classify_milvus_exception(exc, batch_size=len(batch))
            logger.warning(
                f"Milvus 单批写入失败 -> 分类为 {type(classified).__name__}: {classified}"
            )
            raise classified

    def _delete_by_ids_batch(self, collection_name: str, ids: List[str]) -> int:
        """
        按 ID 删除单批。由 BaseWriter.delete_by_ids() 自动分批调用。

        Args:
            collection_name: Milvus collection 名
            ids:             单批 ID 列表，长度 <= MILVUS_BATCH_SIZE

        Returns:
            实际删除的条数（Milvus 不返回精确数量，返回输入数量）
        """
        from pymilvus import Collection

        if not ids:
            return 0

        try:
            collection = Collection(collection_name)
            collection.load()

            ids_expr = ", ".join(f"'{str(i)[:256]}'" for i in ids)
            collection.delete(f"id in [{ids_expr}]")
            collection.flush()

            return len(ids)

        except (WriterPayloadTooLarge, WriterTransientError,
                WriterConnectionError, WriterFatalError):
            raise
        except Exception as exc:
            classified = _classify_milvus_exception(exc, batch_size=len(ids))
            logger.warning(
                f"Milvus 单批删除失败 -> 分类为 {type(classified).__name__}: {classified}"
            )
            raise classified


# ════════════════════════════════════════════════════════════════
# 公共导出
# ════════════════════════════════════════════════════════════════

__all__ = [
    "MilvusWriter",
    "MILVUS_BATCH_SIZE",
]
