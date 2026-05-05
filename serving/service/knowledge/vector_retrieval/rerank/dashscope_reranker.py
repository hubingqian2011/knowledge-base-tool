import logging
import os
from typing import List, Dict, Any

import httpx

from .base import BaseReranker

logger = logging.getLogger(__name__)


class DashScopeReranker(BaseReranker):
    """
    DashScope Rerank API（原生接口）
    模型：gte-rerank
    文档：https://help.aliyun.com/zh/model-studio/gte-rerank
    """

    def __init__(self, **kwargs):
        self.api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
        self.model = os.getenv("RERANK_MODEL", "gte-rerank")
        self.api_url = os.getenv(
            "DASHSCOPE_RERANK_URL",
            "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
        )
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY 或 DASHSCOPE_API_KEY 未配置，无法初始化 DashScopeReranker")
        self.http_client = httpx.Client(
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        logger.info(f"DashScopeReranker 初始化完成: model={self.model}")

    def rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int = None,
    ) -> List[Dict[str, Any]]:
        """
        对候选文档重新排序。

        Args:
            query:     查询文本
            documents: [{"text": "...", "id": "...", ...}, ...]
            top_k:     返回条数，None 则返回全部

        Returns:
            按 relevance_score 降序排列的文档列表，每条追加 score 字段
        """
        if not documents:
            return documents

        texts = [doc.get("text", "") for doc in documents]
        top_n = min(top_k, len(texts)) if top_k else len(texts)

        try:
            resp = self.http_client.post(
                self.api_url,
                json={
                    "model": self.model,
                    "input": {
                        "query": query,
                        "documents": texts,
                    },
                    "parameters": {
                        "top_n": top_n,
                        "return_documents": False,
                    },
                },
            )
            resp.raise_for_status()
            results = resp.json()["output"]["results"]

            reranked = []
            for r in results:
                doc = documents[r["index"]].copy()
                doc["score"] = r["relevance_score"]
                reranked.append(doc)

            return reranked

        except Exception as e:
            logger.warning(f"DashScope rerank 调用失败，降级使用原始排序: {e}")
            return [
                dict(doc, score=doc.get("score", 0.0))
                for doc in documents[:top_n]
            ]
