# -*- coding: utf-8 -*-
"""
知识库服务 - 重构版
使用 Handler 架构,将具体业务逻辑委托给特化的 Handler
"""
from typing import List, Dict, Any, Optional
from pathlib import Path

from util.logging.logger import get_logger
from config.config import COLLECTION_MANUAL

_manual_cols = [c.strip() for c in COLLECTION_MANUAL.split(",")]
COLLECTION_MANUAL_CHUNKS = _manual_cols[1] if len(_manual_cols) > 1 else "manual_chunks"
from service.knowledge.handlers import KnowledgeHandlerFactory

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]
INGESTION_OUTPUT_ROOT = Path("/app/ingestion/output")


class KnowledgeService:
    """
    知识库服务 Facade

    职责:
    1. 根据集合名称路由到正确的 Handler
    2. 提供统一的接口给上层调用
    3. 保留一些通用的辅助方法(文件下载、元数据管理等)
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """初始化知识库服务"""
        logger.info("初始化 KnowledgeService (Handler 架构)")

    def _get_handler(self, collection_name: str, collection_type: Optional[str] = None):
        """
        工厂方法: 根据集合名称返回对应的策略处理器

        Args:
            collection_name: 集合名称
            collection_type: 集合类型（可选，如果未提供则使用 collection_name 作为类型）

        Returns:
            BaseKnowledgeHandler: 处理器实例
        """
        return KnowledgeHandlerFactory.get_handler(collection_name, collection_type)

    # ==================== 核心接口 - 委托给 Handler ====================

    def upload_knowledge_files_and_parse(
        self,
        files: List[Any],
        filenames: List[str],
        collection_name: str,
        collection_type: str = None,
        metadata: Optional[Dict] = None
    ) -> List[Dict[str, Any]]:
        """
        上传知识库文件并自动解析入库

        根据 collection_name 自动选择对应的 Handler

        Args:
            files: 文件流列表
            filenames: 文件名列表
            collection_name: 知识库集合名
            collection_type: 知识库类型(可选,用于覆盖默认选择)
            metadata: 额外的元数据

        Returns:
            List[Dict]: 每个文件的解析和入库结果
        """
        try:
            ct = collection_type or collection_name
            file_info = [f"{fn}(size=N/A)" for fn in filenames]
            logger.debug(f"[upload_knowledge] 入口: collection_name={collection_name}, collection_type={ct}, 文件数={len(files)}, 文件名={file_info}")

            # 获取对应的 Handler
            handler = self._get_handler(collection_name, collection_type)

            # 调用 Handler 的 ingest 方法
            document_ids = handler.ingest(files, filenames, metadata)

            # 构造返回结果
            # 注意：GraphRAG 入库的 handler 返回空列表（通过 ingestion/ 独立写三库），
            # 此时按 filenames 构造"入库成功但无 document_ids"的结果
            results = []
            if document_ids:
                for i, (file_id, filename) in enumerate(zip(document_ids, filenames)):
                    ids_list = [file_id] if isinstance(file_id, str) else (file_id if isinstance(file_id, list) else [file_id])
                    results.append({
                        "document_ids": ids_list,
                        "filename": filename,
                        "success": True,
                        "result": {
                            "ids": ids_list,
                            "count": len(ids_list)
                        }
                    })
            else:
                for filename in filenames:
                    results.append({
                        "document_ids": [],
                        "filename": filename,
                        "success": True,
                        "result": {
                            "ids": [],
                            "count": 0,
                            "message": "入库完成（通过 ingestion 模块独立写库）"
                        }
                    })
            total_records = sum(len(r["document_ids"]) for r in results)
            logger.debug(f"[upload_knowledge] 出口: 成功文件数={len(results)}, 总记录数={total_records}")
            return results

        except Exception as e:
            logger.error(f"上传知识库文件失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return []

    def similarity_search(
        self,
        query: str,
        collection_name: str,
        top_k: int = 10,
        use_rerank: bool = True,
        metadata: Optional[Dict] = None,
        rejected_workorder_ids: Optional[List[str]] = None,
        query_vector: Optional[List[float]] = None
    ) -> List[Dict[str, Any]]:
        """
        相似度搜索

        根据 collection_name 自动选择对应的 Handler

        Args:
            query: 查询文本
            collection_name: 集合名称
            top_k: 返回结果数量
            use_rerank: 是否使用重排序
            metadata: 元数据过滤条件
            rejected_workorder_ids: 要排除的派工单号列表，在检索时即不返回（保证条数）
            query_vector: 预计算的 query embedding 向量，None 时内部自行计算

        Returns:
            List[Dict[str, Any]]: 搜索结果列表
        """
        try:
            # 获取对应的 Handler
            handler = self._get_handler(collection_name)

            # 调用 Handler 的 search 方法
            results = handler.search(
                query=query,
                top_k=top_k,
                metadata=metadata,
                use_rerank=use_rerank,
                rejected_workorder_ids=rejected_workorder_ids,
                query_vector=query_vector
            )

            return results

        except Exception as e:
            logger.error(f"相似度搜索失败: {str(e)}")
            return []

    def delete_documents(
        self,
        document_ids: List[str],
        collection_name: str
    ) -> Dict[str, int]:
        """
        删除文档

        根据 collection_name 自动选择对应的 Handler

        Args:
            document_ids: 要删除的文档ID列表
            collection_name: 集合名称

        Returns:
            Dict[str, int]: 删除结果统计
        """
        try:
            # 获取对应的 Handler
            handler = self._get_handler(collection_name)

            # 调用 Handler 的 delete_documents 方法
            result = handler.delete_documents(document_ids)

            return result

        except Exception as e:
            logger.error(f"删除文档失败: {str(e)}")
            return {
                "deleted_count": 0,
                "total_count": len(document_ids)
            }

    # ==================== 通用方法 - 保留在 Service 层 ====================

    def generate_embeddings(self, texts: List, is_query: bool = False):
        """
        生成文本的嵌入向量

        保留此方法用于其他模块直接调用

        Args:
            texts: 文本列表
            is_query: 是否为查询

        Returns:
            np.ndarray: 生成的嵌入向量数组
        """
        # 获取一个通用 handler 来生成嵌入
        handler = self._get_handler("generic")
        return handler.generate_embeddings(texts, is_query)

    def add_texts(
        self,
        texts: List,
        collection_name: str,
        metadatas: Optional[List] = None
    ) -> List[str]:
        """
        添加文本到知识库

        保留此方法用于其他模块直接调用

        Args:
            texts: 文本列表
            collection_name: 集合名
            metadatas: 元数据列表

        Returns:
            List[str]: 插入向量的 ID 列表
        """
        # 获取对应的 handler
        handler = self._get_handler(collection_name)
        return handler.add_texts(texts, collection_name, metadatas)

    def get_documents(
        self,
        document_id: Optional[str] = None,
        collection_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        获取文档

        保留此方法用于其他模块直接调用

        Args:
            document_id: 向量数据库中的ID（可选）
            collection_name: 向量数据库集合名称（可选）

        Returns:
            List[Dict[str, Any]]: 文档列表
        """
        # 获取对应的 handler
        handler = self._get_handler(collection_name or "generic")
        return handler.get_documents(document_id, collection_name)

    def full_text_search(
        self,
        query: str,
        collection_name: str,
        top_k: int = 10,
        metadata: Optional[Dict] = None
    ) -> List[Dict[str, Any]]:
        """
        全文搜索

        保留此方法用于向后兼容

        Args:
            query: 查询文本
            collection_name: 集合名称
            top_k: 返回结果数量
            metadata: 元数据过滤条件

        Returns:
            List[Dict[str, Any]]: 搜索结果列表
        """
        # 获取对应的 handler
        handler = self._get_handler(collection_name)

        # 使用 handler 的组件进行全文搜索
        try:
            search_metadata = metadata or {}
            search_metadata["is_deleted"] = False

            results = handler.es_document_repository.search_by_collection_and_content(
                collection=collection_name,
                content=query,
                size=top_k,
                metadata=search_metadata
            )

            search_results = []
            for _dict in results:
                doc = _dict["document"]
                search_results.append({
                    "id": doc.document_id,
                    "content": doc.content,
                    "metadata": doc.metadata,
                    "score": _dict["score"]
                })

            logger.info(f"成功执行全文搜索，返回{len(search_results)}个结果")
            return search_results

        except Exception as e:
            logger.error(f"全文搜索失败: {str(e)}")
            return []

    def hybrid_search(
        self,
        query: str,
        collection_name: str,
        similarity_weight: float = 1.0,
        full_text_weight: float = 1.0,
        top_k: int = 10,
        metadata: Optional[Dict] = None
    ) -> List[Dict[str, Any]]:
        """
        混合搜索

        保留此方法用于向后兼容

        Args:
            query: 查询文本
            collection_name: 集合名称
            similarity_weight: 相似度搜索结果的权重
            full_text_weight: 全文搜索结果的权重
            top_k: 返回结果数量
            metadata: 元数据过滤条件

        Returns:
            List[Dict[str, Any]]: 搜索结果列表
        """
        from config.config import ENABLE_RERANKER

        try:
            search_metadata = metadata or {}
            search_metadata["is_deleted"] = False

            # 执行相似度搜索
            similarity_results = self.similarity_search(
                query=query,
                collection_name=collection_name,
                top_k=top_k,
                use_rerank=False,
                metadata=search_metadata
            )

            # 执行全文搜索
            full_text_results = self.full_text_search(
                query=query,
                collection_name=collection_name,
                top_k=top_k,
                metadata=search_metadata
            )

            # 合并结果并去重
            merged_results = {}
            for result in similarity_results:
                doc_id = result["id"]
                merged_results[doc_id] = {
                    "id": doc_id,
                    "content": result["content"],
                    "metadata": result["metadata"],
                    "weight": similarity_weight
                }

            for result in full_text_results:
                doc_id = result["id"]
                if doc_id in merged_results:
                    merged_results[doc_id]["weight"] += full_text_weight
                else:
                    merged_results[doc_id] = {
                        "id": doc_id,
                        "content": result["content"],
                        "metadata": result["metadata"],
                        "weight": full_text_weight
                    }

            # 准备重排序的文档
            handler = self._get_handler(collection_name)
            documents = [
                {
                    "text": doc["content"],
                    "id": doc["id"],
                    "weight": doc["weight"]
                }
                for doc in merged_results.values()
            ]

            # 执行重排序
            if ENABLE_RERANKER and handler.reranker:
                reranked_results = handler.reranker.rerank(
                    query=query,
                    documents=documents,
                    top_k=top_k
                )

                final_results = []
                for doc in reranked_results:
                    vector_id = doc["id"]
                    result = {
                        "id": vector_id,
                        "rerank_score": doc["score"],
                        "content": merged_results[vector_id]["content"],
                        "metadata": merged_results[vector_id]["metadata"],
                        "weight": merged_results[vector_id]["weight"]
                    }
                    final_results.append(result)

                logger.info(f"成功执行混合搜索和重排序，返回{len(final_results)}个结果")
                return final_results
            else:
                # 如果没有启用重排序，按权重排序
                sorted_results = sorted(
                    merged_results.values(),
                    key=lambda x: x["weight"],
                    reverse=True
                )[:top_k]

                logger.info(f"成功执行混合搜索，返回{len(sorted_results)}个结果")
                return sorted_results

        except Exception as e:
            logger.error(f"混合搜索失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return []

    def get_workorder_json_by_id(
        self,
        document_id: str,
        collection_name: str
    ) -> Optional[Dict]:
        """
        根据document_id和collection_name获取工单json内容

        Args:
            document_id: 文档ID
            collection_name: 集合名

        Returns:
            dict: 解析后的json内容，若无则返回None
        """
        docs = self.get_documents(document_id=document_id, collection_name=collection_name)
        if not docs:
            return None

        doc = docs[0]
        metadata = doc.get("metadata", {})

        if collection_name == "workorder":
            file_path = metadata.get("file_path")
            if not file_path or not isinstance(file_path, str) or not file_path.strip():
                return None

            try:
                import json
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    return json.loads(content)
            except Exception as e:
                logger.error(f"读取工单json文件失败: {str(e)}")
                return None
        elif collection_name == "workorder_total":
            result = {"工单内容": doc.get("content", "")}
            if isinstance(metadata, dict):
                result.update(metadata)
            return result
        else:
            return None

    def get_knowledge_file_by_id(
        self,
        document_id: str,
        collection_name: str
    ) -> Optional[str]:
        """
        根据document_id和collection_name获取文件路径

        Args:
            document_id: 文档ID
            collection_name: 集合名

        Returns:
            文件路径（str），若为workorder则返回None
        """
        if collection_name == "workorder":
            return None

        docs = self.get_documents(document_id=document_id, collection_name=collection_name)
        if not docs:
            return None

        doc = docs[0]
        metadata = doc.get("metadata", {})

        candidate_paths = []
        if collection_name in {
            COLLECTION_MANUAL_CHUNKS,
            "manual_chunks",
            "principle_chunks",
        }:
            candidate_paths.append(metadata.get("slice_pdf_path"))
        candidate_paths.append(metadata.get("file_path"))

        for raw_path in candidate_paths:
            resolved = self._resolve_knowledge_file_path(raw_path)
            if resolved:
                return resolved

        return None

    def _resolve_knowledge_file_path(self, raw_path: Optional[str]) -> Optional[str]:
        if not raw_path or not isinstance(raw_path, str) or not raw_path.strip():
            return None

        path_str = raw_path.strip()
        path_obj = Path(path_str)
        candidate_paths = []

        if path_obj.is_absolute():
            candidate_paths.append(path_obj)
        else:
            candidate_paths.extend(
                [
                    INGESTION_OUTPUT_ROOT / path_obj,
                    BACKEND_ROOT / path_obj,
                    PROJECT_ROOT / path_obj,
                    PROJECT_ROOT / "ingestion" / "output" / path_obj,
                ]
            )

        for candidate in candidate_paths:
            if candidate.exists():
                return str(candidate)

        return None

    def get_manual_images_by_id(
        self,
        document_id: str,
        collection_name: str
    ) -> Optional[List[str]]:
        """
        根据document_id获取所有图片id列表

        Args:
            document_id: 文档ID
            collection_name: 集合名

        Returns:
            list: 图片id列表
        """
        # 获取 manual handler
        handler = self._get_handler(collection_name or COLLECTION_MANUAL)
        return handler.get_manual_images(document_id)

    def get_manual_images_by_page_range(
        self,
        page_start,
        page_end,
        document_id: Optional[str] = None,
    ) -> List[str]:
        """
        按页码范围查图片 ID 列表，JOIN image_file 确认图片真实存在，不触发文件系统检查。

        Args:
            page_start: 起始页码（含）
            page_end: 结束页码（含）

        Returns:
            list: 图片 image_id 字符串列表
        """
        if page_start is None or page_end is None:
            return []
        try:
            from database.sql.repository.repository_factory import RepositoryFactory
            with RepositoryFactory.get_instance().manual_image_repository as repo:
                if document_id:
                    records = repo.get_images_by_document_page_range(
                        str(document_id),
                        int(page_start),
                        int(page_end),
                    )
                else:
                    records = repo.get_images_by_page_range_global(int(page_start), int(page_end))
                return [r.image_id for r in records]
        except Exception as e:
            logger.warning(f"[get_manual_images_by_page_range] page={page_start}-{page_end} 查询失败: {e}")
            return []

    def upload_knowledge_files(
        self,
        files: List[Any],
        filenames: List[str],
        document_ids: List[str],
        collection_name: str
    ) -> List[Dict[str, Any]]:
        """
        为已有知识库文档上传文件并保存 file_path 到 metadata（不解析、不向量化）。

        Args:
            files: 文件流列表（与 document_ids 一一对应）
            filenames: 文件名列表
            document_ids: 文档 ID 列表
            collection_name: 集合名（如 manual）

        Returns:
            List[Dict]: 每条包含 document_id, filename, file_path, success
        """
        handler = self._get_handler(collection_name)
        return handler._upload_knowledge_files(
            files=files,
            filenames=filenames,
            document_ids=document_ids,
            collection_name=collection_name
        )

    def update_document_metadata(
        self,
        document_id: str,
        collection_name: str,
        metadata_updates: Dict[str, Any]
    ) -> bool:
        """
        更新文档的metadata

        Args:
            document_id: 文档ID
            collection_name: 集合名称
            metadata_updates: 新的metadata字典

        Returns:
            bool: 更新是否成功
        """
        # 获取对应的 handler
        handler = self._get_handler(collection_name)

        # 使用 handler 的组件更新 metadata
        try:
            # 更新MongoDB
            from database.document.model import DocumentModel
            doc = DocumentModel.objects(
                document_id=document_id,
                collection_name=collection_name
            ).first()

            if doc:
                doc.metadata = metadata_updates
                doc.save()
                return True
            return False

        except Exception as e:
            logger.error(f"更新文档metadata失败: {str(e)}")
            return False

    def delete_collection(self, collection_name: str) -> bool:
        """
        删除指定集合中的所有条目

        Args:
            collection_name: 集合名

        Returns:
            bool: 删除是否成功
        """
        # 获取对应的 handler
        handler = self._get_handler(collection_name)
        return handler.delete_collection(collection_name)


# 创建全局单例实例
knowledge_service = KnowledgeService()
