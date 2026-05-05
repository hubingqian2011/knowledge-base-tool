# -*- coding: utf-8 -*-
"""
ingestion 模块统一 Agent 基类。

【设计模式】
模板方法（Template Method）：
  - 基类定义"5 步流程 + 三库写入 + 失败回滚"的骨架
  - 子类只实现"如何解析特定文件类型"

【5 步流程】
  Step 1: _parse_file()       子类实现 — 读文件解析为 Chunk
  Step 2: _validate()          子类实现 — 校验和过滤
  Step 3: _enrich_metadata()   钩子    — 默认空操作，子类可重写加 metadata
  Step 4: _build_records()     基类实现 — 加 ID/默认字段
  Step 5: _embed_and_write()   基类实现 — Embedding + 三库写入

【三库一致性】
方案 A（批次原子性）：
  对每一批：
    1. Milvus 写入
    2. Milvus 成功 → MongoDB 写入 → ES 写入（容忍失败）
    3. Milvus 失败 → 当批整批跳过（已写入 0 条）
    4. MongoDB 失败 → 调 mongo.delete_by_ids 软删 + Milvus.delete_by_ids 硬删 → 整批回滚
    5. ES 失败 → 仅警告（项目惯例）
  整体任务报告：success_batches / failed_batches / total_written

【错误分类】
- FatalError      → 立即终止任务，标记 fatal
- RetryableError  → 由 @retry 装饰器自动重试（已在 writer 层处理）
- PartialError    → 单批失败但任务继续，最后汇总
- 其他 Exception  → 视为 Fatal，立即终止

【约束】
- 子类不得重写 ingest()
- 子类必须实现 _parse_file() 和 _validate()
- 所有写入失败必须分类后再传播
- 进度报告必须分粒度：step（5 个主步骤）、substep（每批进度）
"""

import logging
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 路径设置
_INGESTION_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _INGESTION_DIR.parent
for _p in (_INGESTION_DIR, _PROJECT_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from core.errors import (
    FatalError,
    IngestionError,
    InvalidContentError,
    WriterFatalError,
)
from core.progress import NoOpProgressReporter, ProgressReporter
from db.es_writer import ESWriter
from db.milvus_writer import MilvusWriter
from db.mongo_writer import MongoWriter
from document_identity import (
    build_document_fingerprint,
    build_ingestion_document_id,
)

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 数据结构
# ════════════════════════════════════════════════════════════════

@dataclass
class Chunk:
    """
    解析后的单个数据块。

    所有 agent 解析文件后必须输出 List[Chunk]。
    """
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    local_key: Optional[str] = None


@dataclass
class IngestResult:
    """
    Agent.ingest() 的统一返回结构。

    替代旧版 {"success": True, ...} 的非结构化 dict。
    """
    success: bool
    total_rows: int = 0
    valid_rows: int = 0
    embedded_rows: int = 0
    written_rows: int = 0

    total_batches: int = 0
    success_batches: int = 0
    failed_batches: int = 0

    msg: str = ""
    errors: Dict[str, str] = field(default_factory=dict)
    failed_record_ids: List[str] = field(default_factory=list)

    collection_name: str = ""
    elapsed_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """转换为 dict（向后兼容旧的 report 格式）。"""
        return {
            "success": self.success,
            "total_rows": self.total_rows,
            "valid_rows": self.valid_rows,
            "embedded_rows": self.embedded_rows,
            "written_rows": self.written_rows,
            "total_batches": self.total_batches,
            "success_batches": self.success_batches,
            "failed_batches": self.failed_batches,
            "msg": self.msg,
            "errors": self.errors,
            "failed_record_ids": self.failed_record_ids,
            "collection_name": self.collection_name,
            "elapsed_seconds": self.elapsed_seconds,
        }


# ════════════════════════════════════════════════════════════════
# BaseIngestAgent
# ════════════════════════════════════════════════════════════════

class BaseIngestAgent(ABC):
    """
    所有 ingestion agent 的基类。

    使用方式：
        class ExcelIngestAgent(BaseIngestAgent):
            def _parse_file(self, file_path, **kwargs):
                # 子类只实现这两个方法
                ...
            def _validate(self, chunks):
                ...

        agent = ExcelIngestAgent(collection_name="workorder", knowledge_type="workorder")
        result = agent.ingest(file_path="data.xlsx")
    """

    # 每批进入完整三库写入流程的最大条数
    PIPELINE_BATCH_SIZE = 500

    def __init__(
        self,
        collection_name: str,
        knowledge_type: str,
        embedding_dim: int = 2048,
        progress_reporter: Optional[ProgressReporter] = None,
        file_id: Optional[str] = None,    # V2 新增：关联 kb_files 主键
    ):
        self.collection_name = collection_name
        self.knowledge_type = knowledge_type
        self.embedding_dim = embedding_dim
        self.reporter = progress_reporter or NoOpProgressReporter()
        self.file_id = file_id            # V2 新增

        # Writer 在 ingest 时按需实例化（避免 import 时连接数据库）
        self._milvus: Optional[MilvusWriter] = None
        self._mongo: Optional[MongoWriter] = None
        self._es: Optional[ESWriter] = None

    # ════════════════════════════════════════════════════════
    # 主入口：5 步流程总指挥（子类不要重写）
    # ════════════════════════════════════════════════════════

    def ingest(self, file_path: str, **kwargs) -> IngestResult:
        """
        统一入口。子类不要重写。

        kwargs 会传递给 _parse_file 和 _enrich_metadata，
        子类可以通过 kwargs 接收特定参数（如 content_columns、device_tags）。
        """
        start_time = time.time()
        result = IngestResult(success=False, collection_name=self.collection_name)

        try:
            self.reporter.report_status("running", f"开始入库: {file_path}")

            # Step 1: 解析文件
            self.reporter.report_step(1, "解析文件", str(file_path))
            chunks = self._parse_file(file_path, **kwargs)
            result.total_rows = len(chunks)
            logger.info(f"[{self.__class__.__name__}] Step 1 解析完成: {result.total_rows} 行")

            if result.total_rows == 0:
                raise InvalidContentError(
                    "文件解析后无任何内容",
                    context={"file_path": str(file_path)},
                )

            # Step 2: 校验过滤
            self.reporter.report_step(2, "校验过滤", f"{result.total_rows} 行")
            chunks = self._validate(chunks)
            result.valid_rows = len(chunks)
            logger.info(
                f"[{self.__class__.__name__}] Step 2 校验完成: "
                f"{result.valid_rows}/{result.total_rows} 行通过"
            )

            if result.valid_rows == 0:
                raise InvalidContentError(
                    "校验后无有效内容",
                    context={"file_path": str(file_path), "total_rows": result.total_rows},
                )

            # Step 3: 元数据增强（可选钩子）
            self.reporter.report_step(3, "元数据增强", f"{result.valid_rows} 行")
            chunks = self._enrich_metadata(chunks, **kwargs)

            # Step 4: 构造 records
            self.reporter.report_step(4, "构造记录", f"{result.valid_rows} 行")
            file_path_obj = Path(file_path)
            document_fingerprint = build_document_fingerprint(str(file_path_obj))
            records = self._build_records(
                chunks=chunks,
                source_file=file_path_obj.name,
                document_fingerprint=document_fingerprint,
            )
            records = self._post_process_records(records, **kwargs)

            # Step 5: Embedding + 三库写入
            self.reporter.report_step(5, "Embedding+三库写入", f"{len(records)} 条")
            self._embed_and_write(records, result)

            # 最终判定
            if result.total_batches == 0:
                result.success = False
                result.msg = "无任何批次写入"
            elif result.failed_batches == 0:
                result.success = True
            elif result.failed_batches / result.total_batches < 0.5:
                result.success = True
                result.msg = (
                    f"部分批次失败: {result.failed_batches}/{result.total_batches}，"
                    f"已写入 {result.written_rows}/{result.valid_rows} 行"
                )
            else:
                result.success = False
                result.msg = (
                    f"超过半数批次失败: {result.failed_batches}/{result.total_batches}"
                )

        except FatalError as e:
            result.success = False
            result.msg = str(e)
            result.errors["fatal"] = str(e)
            logger.error(f"[{self.__class__.__name__}] FatalError: {e}")
            self.reporter.report_status("failed", str(e))

        except IngestionError as e:
            result.success = False
            result.msg = str(e)
            result.errors["ingestion"] = str(e)
            logger.exception(f"[{self.__class__.__name__}] IngestionError: {e}")
            self.reporter.report_status("failed", str(e))

        except Exception as e:
            result.success = False
            result.msg = f"未预期错误: {type(e).__name__}: {e}"
            result.errors["unknown"] = str(e)
            logger.exception(f"[{self.__class__.__name__}] Unexpected error: {e}")
            self.reporter.report_status("failed", result.msg)

        finally:
            self._cleanup_writers()
            result.elapsed_seconds = round(time.time() - start_time, 2)

            if result.success:
                self.reporter.report_status(
                    "done",
                    f"入库完成: 写入 {result.written_rows}/{result.valid_rows} 行，"
                    f"耗时 {result.elapsed_seconds}s",
                )

            logger.info(
                f"[{self.__class__.__name__}] 任务完成: "
                f"success={result.success} written={result.written_rows}/{result.valid_rows} "
                f"batches={result.success_batches}/{result.total_batches} "
                f"elapsed={result.elapsed_seconds}s"
            )

        # V2 新增：更新 kb_files 状态（在 writer 已关闭之后执行）
        self._finalize_kb_file(result)

        return result

    # ════════════════════════════════════════════════════════
    # 子类必须实现的钩子
    # ════════════════════════════════════════════════════════

    @abstractmethod
    def _parse_file(self, file_path: str, **kwargs) -> List[Chunk]:
        """
        Step 1: 解析文件为 List[Chunk]。子类必须实现。

        Args:
            file_path: 文件路径
            **kwargs:  子类可接收特定参数

        Returns:
            List[Chunk]，每个 chunk 含 text 和 metadata

        Raises:
            ParseError / FileNotFoundError_ / InvalidContentError 等 FatalError
        """
        raise NotImplementedError

    @abstractmethod
    def _validate(self, chunks: List[Chunk]) -> List[Chunk]:
        """
        Step 2: 校验和过滤 chunks。子类必须实现。

        Args:
            chunks: 解析后的全部 chunks

        Returns:
            合法的 chunks（可以是 chunks 的子集）
        """
        raise NotImplementedError

    # ════════════════════════════════════════════════════════
    # 可选钩子（子类可重写，默认空操作）
    # ════════════════════════════════════════════════════════

    def _enrich_metadata(self, chunks: List[Chunk], **kwargs) -> List[Chunk]:
        """
        Step 3 钩子：为 chunks 补充额外 metadata。默认直接返回。

        子类可重写来加 device_tags、ingested_by 等。
        """
        return chunks

    def _post_process_records(self, records: List[Dict], **kwargs) -> List[Dict]:
        """
        Step 4 钩子：record 构造后的后处理。默认直接返回。

        子类可重写来加特殊字段、过滤等。
        """
        return records

    # ════════════════════════════════════════════════════════
    # 基类实现：通用流程（子类不要重写）
    # ════════════════════════════════════════════════════════

    def _build_records(
        self,
        chunks: List[Chunk],
        source_file: str,
        document_fingerprint: str,
    ) -> List[Dict]:
        """
        Step 4: 为每个 chunk 构造完整 record（含 document_id + 标准字段）。

        每个 record 含：
          - id              确定性生成的 document_id
          - text            chunk.text
          - chunk_raw_text  原文备份
          - knowledge_type / collection_name / source_file / ingested_at（标准字段）
          - chunk.metadata  子类提供的业务字段
        """
        ingested_at = datetime.now().isoformat()
        records = []

        for idx, chunk in enumerate(chunks):
            local_key = chunk.local_key or f"chunk:{idx}"

            record_id = build_ingestion_document_id(
                collection_name=self.collection_name,
                source_file=source_file,
                local_key=local_key,
                model="",
                namespace=self.knowledge_type,
                document_fingerprint=document_fingerprint,
            )

            record = {
                "id": record_id,
                "text": chunk.text,
                "chunk_raw_text": chunk.text,
                "knowledge_type": self.knowledge_type,
                "collection_name": self.collection_name,
                "source_file": source_file,
                "ingested_at": ingested_at,
                **chunk.metadata,
            }
            records.append(record)

        return records

    def _embed_and_write(self, records: List[Dict], result: IngestResult) -> None:
        """
        Step 5: Embedding + 三库写入（按批次原子性）。

        流程：
          for batch in records.split(PIPELINE_BATCH_SIZE):
              1. embedding 该批
              2. Milvus 写该批
              3. Milvus 成功 → MongoDB 写
              4. MongoDB 成功 → ES 写（失败仅警告）
              5. 任何前置步骤失败 → 回滚已写入的部分
        """
        # 懒加载 writers
        self._milvus = MilvusWriter()
        self._milvus.ensure_collection(self.collection_name, dim=self.embedding_dim)
        self._mongo = MongoWriter()
        self._es = ESWriter()
        try:
            self._es.ensure_index(self.collection_name)
        except Exception as e:
            logger.warning(f"[{self.__class__.__name__}] ES ensure_index 失败（忽略）: {e}")

        total_records = len(records)
        result.total_batches = (
            (total_records + self.PIPELINE_BATCH_SIZE - 1) // self.PIPELINE_BATCH_SIZE
        )

        for batch_idx, start in enumerate(
            range(0, total_records, self.PIPELINE_BATCH_SIZE), start=1
        ):
            batch = records[start : start + self.PIPELINE_BATCH_SIZE]
            batch_size = len(batch)

            try:
                self._process_one_batch(
                    batch, batch_idx, result,
                    global_offset=start,
                    global_total=total_records,
                )
                result.success_batches += 1
                result.written_rows += batch_size

            except FatalError:
                # FatalError 立即往上抛，整个任务终止
                raise

            except Exception as e:
                # 单批失败：记录后继续下一批
                result.failed_batches += 1
                result.errors[f"batch_{batch_idx}"] = str(e)
                result.failed_record_ids.extend(r["id"] for r in batch)
                logger.warning(
                    f"[{self.__class__.__name__}] 批次 {batch_idx}/{result.total_batches} 失败: "
                    f"{type(e).__name__}: {e}"
                )

    def _process_one_batch(
        self,
        batch: List[Dict],
        batch_idx: int,
        result: IngestResult,
        *,
        global_offset: int = 0,
        global_total: int = None,
    ) -> None:
        """
        处理单批：embedding → Milvus → MongoDB → ES。

        【批次原子性】
        - Milvus 失败 → 不写 MongoDB/ES，整批跳过
        - MongoDB 失败 → 回滚 Milvus（delete_by_ids 硬删）
        - ES 失败 → 仅警告（项目惯例）
        """
        from src.llm_client import get_embeddings_batch

        # 1. Embedding (子批进度回调到全局 reporter)
        texts = [r["text"] for r in batch]

        _gtotal = global_total if global_total is not None else len(batch)
        _gstart = global_offset

        def _embedding_progress(info):
            sub_done = info.get("done") or info.get("completed") or 0
            try:
                current = _gstart + int(sub_done)
            except (TypeError, ValueError):
                return
            if current > _gtotal:
                current = _gtotal
            try:
                self.reporter.report_substep(
                    current=current,
                    total=_gtotal,
                    detail=f"Embedding {current}/{_gtotal}",
                )
            except Exception:
                pass

        vectors = get_embeddings_batch(
            texts,
            show_progress=False,
            progress_callback=_embedding_progress,
        )

        if vectors is None or len(vectors) != len(batch):
            raise WriterFatalError(
                f"Embedding 失败: 期望 {len(batch)} 条，"
                f"得到 {len(vectors) if vectors else 0} 条",
                context={"batch_idx": batch_idx},
            )

        # 过滤 embedding 失败的（None 向量）
        valid_indices = [i for i, v in enumerate(vectors) if v is not None]
        if not valid_indices:
            raise WriterFatalError(
                "Embedding 全部失败",
                context={"batch_idx": batch_idx},
            )

        for i in valid_indices:
            batch[i]["vector"] = vectors[i]
        valid_batch = [batch[i] for i in valid_indices]

        result.embedded_rows += len(valid_batch)

        # 2. Milvus 写入（失败 → 整批跳过）
        try:
            self._milvus.upsert(self.collection_name, valid_batch)
        except Exception as milvus_exc:
            logger.error(
                f"[{self.__class__.__name__}] 批次 {batch_idx} Milvus 写入失败，"
                f"整批跳过: {type(milvus_exc).__name__}: {milvus_exc}"
            )
            raise

        # 3. MongoDB 写入（失败 → 回滚 Milvus）
        try:
            self._mongo.upsert_chunks(self.collection_name, valid_batch)
        except Exception as mongo_exc:
            logger.error(
                f"[{self.__class__.__name__}] 批次 {batch_idx} MongoDB 写入失败，"
                f"回滚 Milvus: {type(mongo_exc).__name__}: {mongo_exc}"
            )
            try:
                ids = [r["id"] for r in valid_batch]
                self._milvus.delete_by_ids(self.collection_name, ids)
                logger.info(
                    f"[{self.__class__.__name__}] 批次 {batch_idx} Milvus 已回滚 {len(ids)} 条"
                )
            except Exception as rollback_exc:
                logger.error(
                    f"[{self.__class__.__name__}] 批次 {batch_idx} Milvus 回滚失败"
                    f"（产生孤立向量）: {rollback_exc}"
                )
            raise

        # 4. ES 写入（失败仅警告，不回滚）
        try:
            self._es.upsert(self.collection_name, valid_batch)
        except Exception as es_exc:
            logger.warning(
                f"[{self.__class__.__name__}] 批次 {batch_idx} ES 写入失败（已忽略）: "
                f"{type(es_exc).__name__}: {es_exc}"
            )

        # V2 新增：写 kb_records（ES 失败时也执行，内部捕获异常不影响主流程）
        self._write_kb_records(valid_batch)

    def _write_kb_records(self, valid_batch: List[Dict]) -> None:
        """V2: 把当前 batch 的 records 写入 MySQL kb_records 表。

        如果 self.file_id 为 None（兼容旧调用方未传 file_id 的情况），
        直接跳过，不影响主流程。

        失败处理：捕获异常，logger.warning，不抛出（避免影响三库写入）。
        理由：kb_records 是 V2 增量层，写失败可由 Phase 6 对账脚本补偿。
        """
        if not self.file_id:
            return  # 旧调用方未传 file_id，跳过

        try:
            from database.sql.model.admin import KbRecord
            from database.sql.repository.repository_factory import RepositoryFactory

            factory = RepositoryFactory.get_instance()
            session = factory.db_manager.get_session()
            try:
                rows = []
                for r in valid_batch:
                    rows.append(KbRecord(
                        file_id=self.file_id,
                        collection_name=self.collection_name,
                        record_no=r.get("record_no", 1),
                        mongo_doc_id=r["id"],
                        milvus_id=r["id"],
                        es_id=r["id"],
                        status="active",
                        is_indexed_milvus=True,
                        is_indexed_mongo=True,
                        is_indexed_es=True,
                        fault_description=r.get("故障描述") or r.get("故障现象"),
                        solution=r.get("解决方案") or r.get("处理方案"),
                        related_signal=r.get("涉及的设备信号") or r.get("设备信号"),
                        chunk_text_preview=(r.get("text") or "")[:500],
                    ))
                session.bulk_save_objects(rows)
                session.commit()
                logger.info(
                    f"[V2] kb_records 写入 {len(rows)} 条 (file_id={self.file_id})"
                )
            finally:
                session.close()
        except Exception as exc:
            logger.warning(
                f"[V2] kb_records 写入失败 (file_id={self.file_id}): "
                f"{type(exc).__name__}: {exc}"
            )

    def _finalize_kb_file(self, result: "IngestResult") -> None:
        """V2: 任务结束时更新 kb_files 状态。

        根据 IngestResult 判定最终状态：
          - success=True 且 failed_batches=0 → 'active'
          - success=True 且 failed_batches>0 → 'partial'
          - success=False → 'failed'

        失败处理：捕获异常 + logger.warning，不抛出。
        """
        if not self.file_id:
            return  # 旧调用方未传 file_id，跳过

        try:
            from datetime import datetime as _dt
            from database.sql.model.admin import KbFile
            from database.sql.repository.repository_factory import RepositoryFactory

            if result.success and result.failed_batches == 0:
                new_status = "active"
            elif result.success and result.failed_batches > 0:
                new_status = "partial"
            else:
                new_status = "failed"

            factory = RepositoryFactory.get_instance()
            session = factory.db_manager.get_session()
            try:
                update_count = session.query(KbFile).filter(
                    KbFile.id == self.file_id
                ).update({
                    "status": new_status,
                    "total_records": result.total_rows,
                    "success_records": result.written_rows,
                    "failed_records": max(0, result.total_rows - result.written_rows),
                    "ingested_at": _dt.now(),
                })
                session.commit()
                logger.info(
                    f"[V2] kb_files 状态更新: file_id={self.file_id}, "
                    f"status={new_status}, total={result.total_rows}, "
                    f"success={result.written_rows} (updated={update_count} row)"
                )
            finally:
                session.close()
        except Exception as exc:
            logger.warning(
                f"[V2] kb_files 状态更新失败 (file_id={self.file_id}): "
                f"{type(exc).__name__}: {exc}"
            )

    def _cleanup_writers(self) -> None:
        """关闭所有 writer 连接。"""
        for attr in ("_milvus", "_mongo", "_es"):
            writer = getattr(self, attr, None)
            if writer is not None:
                try:
                    writer.close()
                except Exception as e:
                    logger.warning(f"关闭 {attr} 失败: {e}")
                setattr(self, attr, None)


# ════════════════════════════════════════════════════════════════
# 公共导出
# ════════════════════════════════════════════════════════════════

__all__ = [
    "BaseIngestAgent",
    "Chunk",
    "IngestResult",
]
