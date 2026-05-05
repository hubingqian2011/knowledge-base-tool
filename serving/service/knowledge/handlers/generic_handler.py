# -*- coding: utf-8 -*-
"""
通用知识库处理器。

用于 parameter 等标准 RAG 知识库。
principle 已切到双库 GraphRAG 链路，不再走这里。
"""
from typing import List, Dict, Any, Optional

from util.logging.logger import get_logger

from .base_handler import BaseKnowledgeHandler

logger = get_logger(__name__)


class GenericKnowledgeHandler(BaseKnowledgeHandler):
    """
    通用知识库处理器。

    适用于标准文件解析 + 标准向量检索的知识库。
    """

    def ingest(
        self,
        files: List[Any],
        filenames: List[str],
        metadata: Optional[Dict] = None
    ) -> List[str]:
        if self.collection_type == "principle":
            raise ValueError("principle 已改为双库 GraphRAG 流程，请使用 /api/ingest/upload")

        parse_results = self._standard_ingest_flow(
            files=files,
            filenames=filenames,
            collection_name=self.collection_name,
            collection_type=self.collection_type,
            metadata=metadata,
        )

        results = []
        for result in parse_results:
            if result.get("success") and "document_ids" in result:
                results.extend(result["document_ids"])

        logger.info(f"通用知识库入库完成，创建 {len(results)} 个文档")
        return results

    def search(
        self,
        query: str,
        top_k: int,
        metadata: Optional[Dict] = None,
        use_rerank: bool = True,
        rejected_workorder_ids: Optional[List[str]] = None,
        query_vector: Optional[List[float]] = None
    ) -> List[Dict[str, Any]]:
        return self.similarity_search(
            query=query,
            collection_name=self.collection_name,
            top_k=top_k,
            use_rerank=use_rerank,
            metadata=metadata,
            rejected_workorder_ids=rejected_workorder_ids,
            query_vector=query_vector,
        )

    def delete_documents(self, document_ids: List[str]) -> Dict[str, int]:
        return self._standard_delete_documents(document_ids)
