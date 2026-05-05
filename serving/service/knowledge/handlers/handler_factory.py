# -*- coding: utf-8 -*-
"""
知识库处理器工厂
根据集合名称返回对应的处理器实例
"""
from typing import Optional
from util.logging.logger import get_logger
from config.config import (
    COLLECTION_MANUAL,
    COLLECTION_PARAMETER,
    COLLECTION_PRINCIPLE,
)

_manual_cols = [c.strip() for c in COLLECTION_MANUAL.split(",")]
_principle_cols = [c.strip() for c in COLLECTION_PRINCIPLE.split(",")]
COLLECTION_MANUAL_CHUNKS = _manual_cols[1] if len(_manual_cols) > 1 else "manual_chunks"
COLLECTION_PRINCIPLE_TOTAL = _principle_cols[0] if _principle_cols else "principle_total"
COLLECTION_PRINCIPLE_CHUNKS = _principle_cols[1] if len(_principle_cols) > 1 else "principle_chunks"

from .base_handler import BaseKnowledgeHandler
from .workorder_handler import WorkorderKnowledgeHandler
from .manual_handler import ManualKnowledgeHandler
from .generic_handler import GenericKnowledgeHandler
from .parameter_handler import ParameterKnowledgeHandler

logger = get_logger(__name__)

GRAPHRAG_DOCUMENT_COLLECTIONS = {
    COLLECTION_MANUAL,
    COLLECTION_MANUAL_CHUNKS,
    COLLECTION_PRINCIPLE_TOTAL,
    COLLECTION_PRINCIPLE_CHUNKS,
}


class KnowledgeHandlerFactory:
    """
    知识库处理器工厂

    根据集合名称(collection_name)自动选择合适的处理器:

    1. **workorder** -> WorkorderKnowledgeHandler
       - 支持LLM标准化
       - 支持Canonical-Raw图谱融合
       - 支持故障诊断搜索

    2. **manual** -> ManualKnowledgeHandler
       - 支持PDF目录切片
       - 支持图片自动提取
       - 搜索时自动补充图片URL

    3. **其他** -> GenericKnowledgeHandler
       - 使用标准RAG流程
       - 适用于 principle, parameter 等
    """

    @staticmethod
    def get_handler(collection_name: str, collection_type: Optional[str] = None) -> BaseKnowledgeHandler:
        """
        根据集合名称获取对应的处理器

        Args:
            collection_name: 集合名称
            collection_type: 集合类型（可选，如果未提供则使用 collection_name 作为类型）

        Returns:
            BaseKnowledgeHandler: 处理器实例

        Raises:
            ValueError: 如果集合名称为空
        """
        if not collection_name:
            raise ValueError("collection_name 不能为空")

        if collection_type is None:
            collection_type = collection_name

        normalized_collection = collection_name.lower()

        # 根据集合名称中的关键字选择处理器
        if collection_type == "workorder" or "workorder" in normalized_collection:
            #logger.debug(f"[get_handler] collection_name={collection_name}, collection_type={collection_type}, handler=WorkorderKnowledgeHandler")
            return WorkorderKnowledgeHandler(collection_name, collection_type)
        elif collection_type in {"manual", "principle"} or collection_name in GRAPHRAG_DOCUMENT_COLLECTIONS:
            #logger.debug(f"[get_handler] collection_name={collection_name}, collection_type={collection_type}, handler=ManualKnowledgeHandler")
            return ManualKnowledgeHandler(collection_name, collection_type)
        elif collection_type == COLLECTION_PARAMETER or COLLECTION_PARAMETER in normalized_collection:
            #logger.debug(f"[get_handler] collection_name={collection_name}, collection_type={collection_type}, handler=ParameterKnowledgeHandler")
            return ParameterKnowledgeHandler(collection_name, collection_type)
        else:
            #logger.debug(f"[get_handler] collection_name={collection_name}, collection_type={collection_type}, handler=GenericKnowledgeHandler")
            return GenericKnowledgeHandler(collection_name, collection_type)

    @staticmethod
    def is_workorder_collection(collection_name: str) -> bool:
        """
        判断是否为工单类型集合

        Args:
            collection_name: 集合名称

        Returns:
            bool: 是否为工单类型
        """
        return "workorder" in collection_name.lower()

    @staticmethod
    def is_manual_collection(collection_name: str) -> bool:
        """
        判断是否为说明书类型集合

        Args:
            collection_name: 集合名称

        Returns:
            bool: 是否为说明书类型
        """
        return collection_name in GRAPHRAG_DOCUMENT_COLLECTIONS

    @staticmethod
    def is_generic_collection(collection_name: str) -> bool:
        """
        判断是否为通用类型集合

        Args:
            collection_name: 集合名称

        Returns:
            bool: 是否为通用类型
        """
        return not (
            KnowledgeHandlerFactory.is_workorder_collection(collection_name) or
            KnowledgeHandlerFactory.is_manual_collection(collection_name)
        )
