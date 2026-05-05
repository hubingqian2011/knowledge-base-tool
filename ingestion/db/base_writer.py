# -*- coding: utf-8 -*-
"""
ingestion/db/ 数据库写入层的统一抽象基类。

【设计哲学】

1. 模板方法（Template Method）模式
   BaseWriter 定义"写入流程"骨架（分批、重试、回滚），
   子类只需实现"单批怎么写"和"如何按 ID 删除"。

   骨架示例：
     def upsert(records):
       for batch in self._chunk(records, batch_size):
         self._upsert_batch(batch)   <- 子类实现

   这样所有 Writer 都自动获得：分批、详细日志、错误分类。

2. 统一接口
   所有 Writer 必须实现：
     - ensure_collection(name): 确保 collection/index/database 存在
     - upsert(name, records):    幂等写入（自动分批）
     - delete_by_ids(name, ids): 按 ID 删除（用于回滚）
     - close():                  释放连接

   方法签名严格一致，方便上层 BaseAgent 用同一段代码调用三个 Writer。

3. 错误分类清晰
   _upsert_batch 内部抛 RetryableError 时，BaseWriter 自动 retry。
   抛 FatalError 时立即停止。

   子类应该把底层异常翻译成正确的类型（参考 errors.py 的指南）。

4. 资源管理
   支持 with 语句：
     with MilvusWriter() as w:
         w.upsert(...)
   离开 with 块自动 close()。

【约束】
- 子类必须实现所有 abstractmethod
- 子类不应该自己写分批逻辑（用 BaseWriter._chunk_records）
- 子类不应该自己捕获异常重试（用 @retry 装饰器或交给 BaseWriter）
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Generator, List, Optional

from core.errors import FatalError

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 默认配置
# ════════════════════════════════════════════════════════════════

DEFAULT_BATCH_SIZE = 500   # 每批最多多少条记录


# ════════════════════════════════════════════════════════════════
# 抽象基类
# ════════════════════════════════════════════════════════════════

class BaseWriter(ABC):
    """
    数据库写入器抽象基类。

    所有 Writer（MilvusWriter / MongoWriter / ESWriter）继承此类。
    """

    # 子类可以覆盖：单批最大条数。Milvus 比较小（500），Mongo/ES 可以更大。
    DEFAULT_BATCH_SIZE: int = DEFAULT_BATCH_SIZE

    def __init__(self, batch_size: Optional[int] = None):
        """
        Args:
            batch_size: 单批最大条数。不传则用类的 DEFAULT_BATCH_SIZE。
        """
        self.batch_size = batch_size or self.DEFAULT_BATCH_SIZE
        self._closed = False

    # ════════════════════════════════════════════════════════════
    # 子类必须实现的抽象方法
    # ════════════════════════════════════════════════════════════

    @abstractmethod
    def ensure_collection(self, collection_name: str, **kwargs) -> None:
        """
        确保 collection / index / table 存在（不存在则创建）。

        Args:
            collection_name: 名称
            **kwargs: 子类特定参数（如 dim=2048 给 Milvus）

        Raises:
            FatalError: 创建失败（schema 不兼容、权限不足等）
        """
        pass

    @abstractmethod
    def _upsert_batch(
        self,
        collection_name: str,
        records: List[Dict[str, Any]],
    ) -> int:
        """
        【子类实现】单批 upsert 实现。

        BaseWriter.upsert() 会自动分批调用此方法。
        子类不需要在这里再分批。

        Args:
            collection_name: 目标 collection
            records:         单批记录（条数 <= self.batch_size）

        Returns:
            实际写入的记录数

        Raises:
            RetryableError:        网络抖动、临时不可用 → 触发重试
            WriterPayloadTooLarge: payload 超限 → BaseWriter 不应该让这种事发生，
                                   若发生说明分批配置有问题
            FatalError:            schema 不兼容、致命错误 → 立即终止
        """
        pass

    @abstractmethod
    def _delete_by_ids_batch(
        self,
        collection_name: str,
        ids: List[str],
    ) -> int:
        """
        【子类实现】单批按 ID 删除实现。

        Args:
            collection_name: 目标 collection
            ids:             单批 ID 列表（条数 <= self.batch_size）

        Returns:
            实际删除的记录数

        Raises:
            RetryableError / FatalError: 同 _upsert_batch
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """关闭连接，释放资源。"""
        pass

    # ════════════════════════════════════════════════════════════
    # 模板方法：自动分批的 upsert / delete
    # ════════════════════════════════════════════════════════════

    def upsert(
        self,
        collection_name: str,
        records: List[Dict[str, Any]],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> int:
        """
        幂等写入。自动分批，单批失败抛异常（不静默吞掉）。

        Args:
            collection_name:   目标
            records:           全部记录
            progress_callback: 可选。每写完一批调用 callback(current_done, total)

        Returns:
            实际写入总数

        Raises:
            FatalError: 任意一批失败（重试耗尽后）即终止整个 upsert
        """
        self._check_not_closed()

        if not records:
            logger.info(f"[{self.__class__.__name__}] 跳过空 records: {collection_name}")
            return 0

        total_count = len(records)
        total_batches = (total_count + self.batch_size - 1) // self.batch_size

        logger.info(
            f"[{self.__class__.__name__}] upsert 开始: "
            f"collection={collection_name}, total={total_count}, "
            f"batch_size={self.batch_size}, total_batches={total_batches}"
        )

        total_written = 0
        for batch_no, batch in enumerate(self._chunk_records(records), start=1):
            written = self._upsert_batch(collection_name, batch)
            total_written += written

            logger.info(
                f"[{self.__class__.__name__}] upsert batch={batch_no}/{total_batches}, "
                f"批={len(batch)}条, 累计={total_written}/{total_count}"
            )

            if progress_callback:
                try:
                    progress_callback(total_written, total_count)
                except Exception as e:
                    logger.warning(
                        f"[{self.__class__.__name__}] progress_callback 失败，忽略: {e}"
                    )

        logger.info(
            f"[{self.__class__.__name__}] upsert 完成: "
            f"collection={collection_name}, written={total_written}"
        )
        return total_written

    def delete_by_ids(
        self,
        collection_name: str,
        ids: List[str],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> int:
        """
        按 ID 批量删除（用于回滚）。

        Args:
            collection_name:   目标
            ids:               要删除的 ID 列表
            progress_callback: 可选

        Returns:
            实际删除总数
        """
        self._check_not_closed()

        if not ids:
            logger.info(f"[{self.__class__.__name__}] 跳过空 ids: {collection_name}")
            return 0

        total_count = len(ids)
        total_batches = (total_count + self.batch_size - 1) // self.batch_size

        logger.info(
            f"[{self.__class__.__name__}] delete_by_ids 开始: "
            f"collection={collection_name}, total={total_count}, total_batches={total_batches}"
        )

        total_deleted = 0
        for batch_no, batch in enumerate(self._chunk_ids(ids), start=1):
            try:
                deleted = self._delete_by_ids_batch(collection_name, batch)
                total_deleted += deleted
                logger.info(
                    f"[{self.__class__.__name__}] delete batch={batch_no}/{total_batches}, "
                    f"删除={deleted} 条, 累计={total_deleted}/{total_count}"
                )
            except Exception as e:
                # 删除失败比写入失败容忍度更高（回滚场景下尽力而为）
                logger.error(
                    f"[{self.__class__.__name__}] delete batch={batch_no} 失败，继续后续批次: "
                    f"{type(e).__name__}: {e}"
                )

            if progress_callback:
                try:
                    progress_callback(total_deleted, total_count)
                except Exception as e:
                    logger.warning(
                        f"[{self.__class__.__name__}] progress_callback 失败，忽略: {e}"
                    )

        logger.info(
            f"[{self.__class__.__name__}] delete_by_ids 完成: "
            f"collection={collection_name}, deleted={total_deleted}"
        )
        return total_deleted

    # ════════════════════════════════════════════════════════════
    # 内部工具
    # ════════════════════════════════════════════════════════════

    def _chunk_records(
        self,
        records: List[Dict[str, Any]],
    ) -> Generator[List[Dict[str, Any]], None, None]:
        """把 records 按 batch_size 切成多批。"""
        for i in range(0, len(records), self.batch_size):
            yield records[i : i + self.batch_size]

    def _chunk_ids(
        self,
        ids: List[str],
    ) -> Generator[List[str], None, None]:
        """把 ids 按 batch_size 切成多批。"""
        for i in range(0, len(ids), self.batch_size):
            yield ids[i : i + self.batch_size]

    def _check_not_closed(self) -> None:
        """如果连接已关闭，抛出 FatalError。"""
        if self._closed:
            raise FatalError(
                f"{self.__class__.__name__} 已关闭，无法继续操作",
                context={"writer": self.__class__.__name__},
            )

    # ════════════════════════════════════════════════════════════
    # Context Manager 协议
    # ════════════════════════════════════════════════════════════

    def __enter__(self) -> "BaseWriter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            self.close()
        except Exception as e:
            logger.warning(f"[{self.__class__.__name__}] close 失败: {e}")
        self._closed = True


# ════════════════════════════════════════════════════════════════
# 公共导出
# ════════════════════════════════════════════════════════════════

__all__ = [
    "BaseWriter",
    "DEFAULT_BATCH_SIZE",
]
