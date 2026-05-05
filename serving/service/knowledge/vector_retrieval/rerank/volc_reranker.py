# from typing import List, Dict, Any, Optional
# from .base import BaseReranker
# from ai_platform.utility.log import get_log
# from ai_platform.app.knowledge_base.embedding import VolcEmbedding

# logger = get_log("VOLC_RERANKER")

# class VolcReranker(BaseReranker):
#     """火山引擎文本重排序器"""
    
#     def __init__(self):
#         """
#         初始化火山引擎重排序器
#         """
#         try:
#             self.volc_embedding = VolcEmbedding()
#             logger.info(f"成功初始化火山引擎重排序器")
#         except Exception as e:
#             logger.error(f"初始化火山引擎重排序器失败: {str(e)}")
#             raise
    
#     def rerank(
#         self,
#         query: str,
#         documents: List[Dict[str, Any]],
#         top_k: int = None
#     ) -> List[Dict[str, Any]]:
#         """
#         对文档进行重排序
        
#         Args:
#             query: 查询文本
#             documents: 待重排序的文档列表，每个文档包含text字段
#             top_k: 返回结果数量，如果为None则返回所有结果
            
#         Returns:
#             List[Dict[str, Any]]: 重排序后的文档列表
#         """
#         try:
#             # 准备输入数据
#             texts = [doc.get("text", "") for doc in documents]
            
#             # 执行重排序
#             results = self.reranker.rank(
#                 query=query,
#                 docs=texts,
#                 doc_ids=list(range(len(texts)))
#             )
#             print(results)
            
#             # 处理返回结果
#             reranked_docs = []
#             for result in results.results:
#                 doc = documents[result.document.doc_id].copy()
#                 doc["rerank_score"] = result.score
#                 reranked_docs.append(doc)
            
#             # 如果指定了top_k，则只返回前k个结果
#             if top_k is not None:
#                 reranked_docs = reranked_docs[:top_k]
            
#             logger.info(f"成功对{len(documents)}个文档进行重排序")
#             return reranked_docs
            
#         except Exception as e:
#             logger.error(f"重排序失败: {str(e)}")
#             return documents  # 如果重排序失败，返回原始文档列表 