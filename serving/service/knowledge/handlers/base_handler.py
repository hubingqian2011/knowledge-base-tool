# -*- coding: utf-8 -*-
"""
知识库处理器抽象基类
定义所有知识库处理器必须实现的标准接口,
并提供通用的数据处理方法
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from util.logging.logger import get_logger
from pydantic import BaseModel
from service.auth.knowledge_permission_utils import (
    document_matches_permission,
    pop_permission_filter,
)

logger = get_logger(__name__)


class BaseKnowledgeHandler(ABC):
    """
    知识库处理器抽象基类

    设计原则:
    1. 提供所有通用的数据处理方法(向量操作、搜索、文档管理)
    2. 子类只需要实现特定的业务逻辑差异
    3. 避免循环依赖,Handler 不依赖 knowledge_service
    """

    def __init__(self, collection_name: str, collection_type: str = None):
        """
        初始化处理器

        Args:
            collection_name: 集合名称(实际存储的集合名)
            collection_type: 集合类型(用于选择解析器,如 "manual", "workorder", "principle" 等)
                           如果不指定,则使用 collection_name 作为类型
        """
        self.collection_name = collection_name
        # 如果未指定 collection_type,则使用 collection_name 作为默认值
        self.collection_type = collection_type if collection_type is not None else collection_name
        self._initialize_components()

    def _initialize_components(self):
        """
        初始化通用的组件
        子类可以重写此方法添加特定组件
        """
        # 初始化嵌入向量客户端
        from service.knowledge.vector_retrieval.embedding.openai_embedding import OpenaiEmbedding
        self.embedding_client = OpenaiEmbedding()

        # 初始化文本分块器
        from service.knowledge.vector_retrieval.text_splitter import TextSplitter
        self.text_splitter = TextSplitter()

        # 初始化Milvus客户端
        from database.vector.milvus_client import MilvusClient
        self.milvus_client = MilvusClient()

        # 初始化重排序器
        from config.config import ENABLE_RERANKER
        if ENABLE_RERANKER:
            from service.knowledge.vector_retrieval.rerank.dashscope_reranker import DashScopeReranker
            self.reranker = DashScopeReranker()
        else:
            self.reranker = None

        # 初始化文档仓储
        from database.document.repository import DocumentRepository
        self.document_repository = DocumentRepository()

        # 初始化ES文档仓储
        from database.search.repository import ESDocumentRepository
        self.es_document_repository = ESDocumentRepository()

    # ==================== 抽象方法 - 子类必须实现 ====================

    @abstractmethod
    def ingest(
        self,
        files: List[Any],
        filenames: List[str],
        metadata: Optional[Dict] = None
    ) -> List[str]:
        """
        数据解析与入库

        Args:
            files: 文件流列表
            filenames: 文件名列表
            metadata: 额外的元数据

        Returns:
            List[str]: 创建的文档ID列表
        """
        pass

    @abstractmethod
    def search(
        self,
        query: str,
        top_k: int,
        metadata: Optional[Dict] = None,
        use_rerank: bool = True,
        rejected_workorder_ids: Optional[List[str]] = None,
        query_vector: Optional[List[float]] = None
    ) -> List[Dict[str, Any]]:
        """
        数据检索

        Args:
            query: 查询文本
            top_k: 返回结果数量
            metadata: 元数据过滤条件
            use_rerank: 是否使用重排序
            rejected_workorder_ids: 要排除的派工单号列表（检索时即不返回）

        Returns:
            List[Dict[str, Any]]: 搜索结果列表
        """
        pass

    @abstractmethod
    def delete_documents(self, document_ids: List[str]) -> Dict[str, int]:
        """
        数据清理

        Args:
            document_ids: 要删除的文档ID列表

        Returns:
            Dict[str, int]: 删除结果统计
        """
        pass

    # ==================== 通用方法 - 所有子类共享 ====================

    def _upload_knowledge_files(
        self,
        files: List[Any],
        filenames: List[str],
        document_ids: List[str],
        collection_name: str
    ) -> List[Dict[str, Any]]:
        """
        上传文件并保存路径到MongoDB

        Args:
            files: 文件流列表
            filenames: 文件名列表
            document_ids: 文本对应的id列表
            collection_name: 集合名

        Returns:
            List[Dict]: 每个文件的保存结果
        """
        from service.system.file_reader_main import file_reader

        results = []
        for file, filename, doc_id in zip(files, filenames, document_ids):
            file_path = file_reader.save_file(file, filename)
            success = False
            if file_path:
                # 保存路径到MongoDB
                success = self.document_repository.set_file_path(
                    document_id=doc_id,
                    collection_name=collection_name,
                    file_path=file_path
                )
            results.append({
                "document_id": doc_id,
                "filename": filename,
                "file_path": file_path,
                "success": success
            })
        return results

    def _standard_delete_documents(self, document_ids: List[str]) -> Dict[str, int]:
        """
        标准的文档删除流程（软删除）

        在MongoDB、Milvus、Elasticsearch三个数据库中软删除文档

        Args:
            document_ids: 要删除的文档ID列表

        Returns:
            Dict[str, int]: 删除结果统计
        """
        try:
            deleted_count = 0

            # 1. 使用repository层的方法软删除MongoDB中的文档
            mongo_deleted_count = self.document_repository.soft_delete_documents(
                document_ids, self.collection_name
            )
            deleted_count += mongo_deleted_count

            # 2. 使用Milvus客户端的方法软删除Milvus中的向量数据
            try:
                milvus_deleted_count = self.milvus_client.soft_delete_by_ids(
                    self.collection_name, document_ids
                )
                deleted_count = max(deleted_count, milvus_deleted_count)
            except Exception as e:
                logger.warning(f"在Milvus中更新文档元数据失败: {str(e)}")

            # 3. 使用ES文档仓储的方法软删除Elasticsearch中的文档
            try:
                es_deleted_count = self.es_document_repository.soft_delete_documents(document_ids)
                deleted_count = max(deleted_count, es_deleted_count)
            except Exception as e:
                logger.warning(f"在Elasticsearch中更新文档元数据失败: {str(e)}")

            logger.info(f"成功在集合 {self.collection_name} 中软删除 {deleted_count} 个文档")
            return {
                "deleted_count": deleted_count,
                "total_count": len(document_ids)
            }
        except Exception as e:
            logger.error(f"在集合 {self.collection_name} 中软删除文档失败: {str(e)}")
            return {
                "deleted_count": 0,
                "total_count": len(document_ids)
            }

    def _should_save_file(self) -> bool:
        """
        钩子方法: 子类可重写以决定是否保存原始文件

        Returns:
            bool: 是否保存文件
        """
        return False

    def _should_save_slice_files(self) -> bool:
        """
        钩子方法: 子类可重写以决定是否保存切片文件（如PDF切片）

        Returns:
            bool: 是否保存切片文件
        """
        return False

    def _standard_ingest_flow(
        self,
        files: List[Any],
        filenames: List[str],
        collection_name: str,
        collection_type: str,
        metadata: Optional[Dict] = None
    ) -> List[Dict[str, Any]]:
        from service.system.file_parser.file_parser_factory import FileParserFactory
        from io import BytesIO

        results = []
        parser = FileParserFactory.get_parser(collection_type)

        for file, filename in zip(files, filenames):
            try:
                content = file.read()
                file_stream = BytesIO(content)

                parse_result = parser.parse(file_stream, filename)

                if parse_result.get("success", False) and "texts" in parse_result:
                    texts = parse_result["texts"]
                    metadatas = parse_result["metadatas"]

                    if metadata:
                        for parser_metadata in metadatas:
                            parser_metadata.update(metadata)

                    ids = self.add_texts(texts, collection_name, metadatas)
                    parse_result["ids"] = ids
                    parse_result["count"] = len(ids)

                    # 根据钩子方法决定是否保存文件
                    should_save = self._should_save_file()
                    #logger.debug(f"[ingest] _should_save_file()={should_save}, collection_type={self.collection_type}, ids数量={len(ids)}")

                    if should_save:
                        file_stream_for_save = BytesIO(content)
                        upload_results = self._upload_knowledge_files(
                            files=[file_stream_for_save],
                            filenames=[filename],
                            document_ids=ids,
                            collection_name=collection_name
                        )
                        #logger.debug(f"[ingest] upload_results={upload_results}")

                        if upload_results and ids:
                            file_path = upload_results[0].get("file_path")
                            #logger.debug(f"[ingest] file_path={file_path}, 准备写入ids数量={len(ids)}")
                            if file_path:
                                for doc_id in ids:
                                    result = self.document_repository.set_file_path(
                                        document_id=doc_id,
                                        collection_name=collection_name,
                                        file_path=file_path
                                    )
                                    #logger.debug(f"[ingest] set_file_path: doc_id={doc_id}, success={result}")
                            else:
                                logger.warning(f"[ingest] file_path为空，无法写入所有document_id")
                        else:
                            logger.warning(f"[ingest] upload_results为空或ids为空")

                    # 处理切片文件（如果有）
                    if self._should_save_slice_files() and "slice_files" in parse_result:
                        slice_files = parse_result["slice_files"]
                        slice_filenames = parse_result.get("slice_filenames", [])
                        if slice_files:
                            self._upload_knowledge_files(
                                files=slice_files,
                                filenames=slice_filenames,
                                document_ids=ids,
                                collection_name=collection_name
                            )

                    self._post_process_documents(ids, parse_result, filename)

                    results.append({
                        "document_ids": ids,
                        "filename": filename,
                        "success": True,
                        "result": parse_result
                    })
                else:
                    results.append({
                        "document_ids": [],
                        "filename": filename,
                        "success": False,
                        "result": parse_result
                    })
            except Exception as e:
                logger.error(f"解析或入库异常: {e}")
                import traceback
                traceback.print_exc()
                results.append({
                    "document_ids": [],
                    "filename": filename,
                    "success": False,
                    "result": f"解析或入库异常: {e}"
                })

        return results

    def _post_process_documents(
        self,
        document_ids: List[str],
        parse_result: Dict[str, Any],
        filename: str
    ):
        """
        钩子方法: 子类可重写以进行文档后处理

        Args:
            document_ids: 生成的文档ID列表
            parse_result: 解析结果
            filename: 文件名
        """
        pass

    def generate_embeddings(self, texts: List, is_query: bool = False):
        """
        生成文本的嵌入向量

        Args:
            texts: 文本列表
            is_query: 是否为查询

        Returns:
            np.ndarray: 生成的嵌入向量数组
        """
        import numpy as np

        try:
            #logger.debug(f"[generate_embeddings] 入口: 文本数量={len(texts)}, is_query={is_query}")
            embedding_results = self.embedding_client.encode(
                inputs=texts,
                is_query=is_query
            )

            # 从返回结果中提取embedding字段并转换为numpy数组
            embeddings = np.array([result.embedding for result in embedding_results])

            # 对向量进行L2归一化
            norm = np.linalg.norm(embeddings, axis=1, keepdims=True)
            embeddings = embeddings / norm

            dim = embeddings.shape[1] if len(embeddings) > 0 else 0
            #logger.debug(f"成功生成{len(texts)}个文本的嵌入向量")
            return embeddings
        except Exception as e:
            logger.error(f"生成嵌入向量失败: {str(e)}")
            raise

    def get_documents(
        self,
        document_id: Optional[str] = None,
        collection_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        获取文档，过滤掉已标记为删除的文档

        Args:
            document_id: 向量数据库中的ID（可选）
            collection_name: 向量数据库集合名称（可选）

        Returns:
            List[Dict[str, Any]]: 文档列表
        """
        try:
            coll_name = collection_name or self.collection_name
            documents = self.document_repository.get_documents(
                document_id=document_id,
                collection_name=coll_name
            )
            return documents
        except Exception as e:
            logger.error(f"获取文档失败: {str(e)}")
            return []

    def es_search(
        self,
        query: str,
        collection_name: str,
        top_k: int
    ) -> List[Dict[str, Any]]:
        """
        使用 ES BM25 对 documents 索引做 match 检索，按 collection 过滤。

        Args:
            query: 查询文本
            collection_name: 集合名称（过滤条件）
            top_k: 返回结果数量

        Returns:
            [{"id": document_id, "score": _score}, ...]
        """
        from database.search.model import DocumentModel
        try:
            index_name = DocumentModel.index_name
            response = self.es_document_repository.es_client.es.search(
                index=index_name,
                query={
                    "bool": {
                        "must": [{"match": {"content": query}}],
                        "filter": [{"term": {"collection": collection_name}}]
                    }
                },
                size=max(1, top_k)
            )
            hits = response.get("hits", {}).get("hits", [])
            return [{"id": hit["_id"], "score": float(hit["_score"])} for hit in hits]
        except Exception as e:
            logger.warning(f"ES BM25 检索失败，将降级为纯 Milvus 检索: {e}")
            return []

    def add_texts(
        self,
        texts: List,
        collection_name: str,
        metadatas: Optional[List] = None
    ) -> List[str]:
        """
        添加文本到知识库，如果存在相似文本则返回已存在的ID

        Args:
            texts: 文本列表
            collection_name: 集合名
            metadatas: 元数据列表

        Returns:
            List[str]: 插入向量的 ID 列表
        """
        import uuid
        import numpy as np
        from config.config import VECTOR_DIMENSION, EMBEDDING_BATCH_SIZE

        try:
            #logger.debug(f"[add_texts] 入口: collection={collection_name}, 文本数量={len(texts)}")
            # 确保集合存在
            self.milvus_client.create_collection(
                collection_name=collection_name,
                dimension=VECTOR_DIMENSION
            )

            document_ids = []
            new_documents = []

            # 确保所有元数据都包含is_deleted=False标志
            processed_metadatas = []
            if metadatas:
                for metadata in metadatas:
                    processed_metadata = metadata or {}
                    if "is_deleted" not in processed_metadata:
                        processed_metadata["is_deleted"] = False
                    processed_metadatas.append(processed_metadata)
            else:
                processed_metadatas = [{"is_deleted": False} for _ in texts]

            # 分批生成嵌入向量（如阿里云 text-embedding-v4 每批最多 10 条）
            all_embeddings_list = []
            for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
                batch = texts[i:i + EMBEDDING_BATCH_SIZE]
                batch_embeddings = self.generate_embeddings(batch)
                all_embeddings_list.append(batch_embeddings)
            all_embeddings = np.concatenate(all_embeddings_list, axis=0)
            #logger.debug(f"[add_texts] 批量Embedding完成: {len(texts)}条文本, 形状={all_embeddings.shape}")

            # 对每个文本进行相似度检查
            for i, text in enumerate(texts):
                # 取第i条向量，保持形状 (1, dim) 以满足 Milvus search 接口要求
                embedding = all_embeddings[i:i+1]

                # 在集合中搜索相似文本
                search_results = self.milvus_client.search_vectors(
                    collection_name=collection_name,
                    query_vectors=embedding.tolist(),
                    top_k=1,
                    metadata_filter={"is_deleted": False}
                )

                # 如果找到相似文本且余弦相似度大于阈值
                if search_results and search_results[0] and search_results[0][0]["distance"] > 0.99:
                    existing_id = str(search_results[0][0]["id"])
                    document_ids.append(existing_id)
                    #logger.debug(f"相似度距离满足阈值要求, 使用已存在ID: {existing_id}")
                else:
                    # 生成新的UUID
                    new_id = str(uuid.uuid4())
                    document_ids.append(new_id)
                    new_documents.append({
                        "id": new_id,
                        "text": text,
                        "embedding": all_embeddings[i],   # 形状 (dim,)，与原 embedding[0] 等价
                        "metadata": processed_metadatas[i]
                    })

            # 如果有需要添加的新文本
            if new_documents:
                embeddings = np.array([doc["embedding"] for doc in new_documents])
                new_ids = [doc["id"] for doc in new_documents]
                metadatas_list = [doc["metadata"] for doc in new_documents]

                # 添加向量到Milvus
                self.milvus_client.insert_vectors(
                    collection_name=collection_name,
                    vectors=embeddings.tolist(),
                    metadata_list=metadatas_list,
                    ids=new_ids
                )

                # 将新文档内容保存到MongoDB和ES
                for doc in new_documents:
                    # 保存到MongoDB
                    self.document_repository.save_document(
                        content=doc["text"],
                        document_id=doc["id"],
                        collection_name=collection_name,
                        metadata=doc["metadata"]
                    )
                    # 保存到ES（混合检索用）；失败仅打 WARNING，不影响主流程
                    try:
                        from database.search.model import DocumentModel as ESDocumentModel
                        es_doc = ESDocumentModel(
                            content=doc["text"],
                            collection=collection_name,
                            metadata=doc["metadata"]
                        )
                        self.es_document_repository.create(es_doc, doc_id=doc["id"])
                    except Exception as es_err:
                        logger.warning("写入 ES 失败，跳过: document_id=%s, error=%s", doc["id"], es_err)

            #logger.debug(f"[add_texts] 出口: collection={collection_name}, 新增={len(new_documents)}个, 去重复用={len(document_ids) - len(new_documents)}个")
            #logger.debug(f"成功处理{len(texts)}个文本，其中{len(new_documents)}个为新添加")
            return document_ids
        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error(f"添加文本到知识库失败: {str(e)}")
            return []

    def similarity_search(
        self,
        query: str,
        collection_name: str,
        top_k: int = 10,
        use_rerank: bool = True,
        metadata: Optional[Dict] = None,
        rejected_workorder_ids: Optional[List[str]] = None,
        min_score: Optional[float] = None,
        query_vector: Optional[List[float]] = None
    ) -> List[Dict[str, Any]]:
        """
        Milvus 主检索 + ES 仅验证 Milvus 结果（标记 milvus+es / milvus_only）。阈值仅作用在 Milvus 原始 distance 上；无 es_only。

        Args:
            query: 查询文本
            collection_name: 集合名称
            top_k: 返回结果数量
            use_rerank: 是否使用重排序
            metadata: 元数据过滤条件（仅对 Milvus 生效）
            rejected_workorder_ids: 要排除的派工单号列表（仅 Milvus）
            min_score: Milvus 原始 distance 阈值，≥ 此值才进入后续阶段；None 时使用 config.VECTOR_SEARCH_SCORE_THRESHOLD

        Returns:
            List[Dict[str, Any]]: 搜索结果列表，每条均含 distance（Milvus 原始分）；ES 仅用于验证关键词命中，不引入 es_only
        """
        import time
        from config.config import RERANK_SEARCH_MULTIPLIER, VECTOR_SEARCH_SCORE_THRESHOLD

        try:
            if min_score is None:
                min_score = VECTOR_SEARCH_SCORE_THRESHOLD
            query_preview = (query[:50] + "...") if len(query) > 50 else query
            meta_keys = list((metadata or {}).keys())
            #logger.debug(f"[similarity_search] 入口: collection={collection_name}, query_preview={query_preview}, top_k={top_k}, min_score={min_score}, metadata_filter_keys={meta_keys}")

            search_metadata = dict(metadata or {})
            search_metadata, allowed_permission_levels, allow_legacy_permission = pop_permission_filter(search_metadata)
            search_metadata["is_deleted"] = False
            # Milvus 多召回：top_k * 2，再阈值过滤
            search_top_k = top_k * RERANK_SEARCH_MULTIPLIER
            if allowed_permission_levels is not None:
                search_top_k *= 3
            excluded = (rejected_workorder_ids or []) if isinstance(rejected_workorder_ids, (list, tuple)) else []

            # 第一阶段：Milvus 语义检索
            t0 = time.perf_counter()
            if query_vector is not None:
                embedding = [query_vector] if query_vector and not isinstance(query_vector[0], list) else query_vector
            else:
                embedding = self.generate_embeddings([query], True).tolist()
            vector_results = self.milvus_client.search_vectors(
                collection_name=collection_name,
                query_vectors=embedding,
                top_k=search_top_k,
                metadata_filter=search_metadata,
                excluded_workorder_numbers=excluded
            )
            milvus_elapsed = time.perf_counter() - t0
            milvus_hits = vector_results[0] if vector_results and len(vector_results) > 0 else []

            # 基于 Milvus 原始 distance 阈值过滤
            passed_milvus = [r for r in milvus_hits if (float(r.get("distance") or 0.0)) >= min_score]
            filtered_count = len(milvus_hits) - len(passed_milvus)
            milvus_scores = [float(r.get("distance") or 0.0) for r in milvus_hits]
            score_range = f"[{min(milvus_scores):.4f}, {max(milvus_scores):.4f}]" if milvus_scores else "[]"
            #logger.debug(
            #    f"[similarity_search] Milvus 召回{collection_name}: {len(milvus_hits)} 条, 耗时 {milvus_elapsed:.3f}s, "
            #    f"阈值({min_score})过滤后 {len(passed_milvus)} 条通过, 过滤 {filtered_count} 条, 分数范围 {score_range}"
            #)
            # passed_milvus 为空时不再走 ES 补充，直接返回空
            if not passed_milvus:
                #if milvus_hits:
                #    logger.debug(
                #        f"[similarity_search] {collection_name} Milvus 全部低于阈值(最高分={max(milvus_scores):.4f})，无结果返回"
                #    )
                #else:
                #    logger.debug(f"[similarity_search] {collection_name} Milvus 无召回，无结果返回")
                pass
                return []

            # 第二阶段：ES 仅验证 passed_milvus 中哪些同时命中关键词（不引入 es_only）
            es_hits = self.es_search(query=query, collection_name=collection_name, top_k=top_k * 2)
            passed_milvus_ids = {str(r["id"]) for r in passed_milvus}
            es_ids = {str(h["id"]) for h in es_hits}
            milvus_es_ids = passed_milvus_ids & es_ids
            milvus_only_ids = passed_milvus_ids - es_ids

            #logger.debug(
            #    f"[similarity_search] ES 验证: passed_milvus {len(passed_milvus)} 条中 {len(milvus_es_ids)} 条同时命中 ES 关键词"
            #)

            # 构建 (doc_id, source, milvus_distance)，source 仅 milvus+es / milvus_only
            passed_milvus_by_id = {str(r["id"]): float(r.get("distance") or 0.0) for r in passed_milvus}
            list_milvus_es = [(doc_id, "milvus+es", passed_milvus_by_id[doc_id]) for doc_id in milvus_es_ids]
            list_milvus_es.sort(key=lambda x: -x[2])
            list_milvus_only = [(doc_id, "milvus_only", passed_milvus_by_id[doc_id]) for doc_id in milvus_only_ids]
            list_milvus_only.sort(key=lambda x: -x[2])
            #merged_list = list_milvus_es + list_milvus_only
            #merged_list = list_milvus_es
            merged_list = list_milvus_es if list_milvus_es else list_milvus_only
            merged_top = merged_list[:top_k]

            n_me = sum(1 for _ in merged_top if _[1] == "milvus+es")
            n_mo = sum(1 for _ in merged_top if _[1] == "milvus_only")
            #logger.debug(f"[similarity_search] 最终结果: {len(merged_top)} 条 (milvus+es={n_me}, milvus_only={n_mo})")

            if not merged_top:
                #logger.debug(f"[similarity_search] 出口: collection={collection_name}, 返回 0 条")
                return []

            # 取原文
            document_ids = [x[0] for x in merged_top]
            documents = self.get_documents(document_id=document_ids, collection_name=collection_name)
            doc_map = {
                doc["document_id"]: doc
                for doc in documents
                if document_matches_permission(
                    doc.get("metadata"),
                    allowed_permission_levels,
                    allow_legacy_permission,
                )
            }
            # 每条结果的 distance：Milvus 原始分
            milvus_distance_by_id = {x[0]: x[2] for x in merged_top}

            if self.reranker and use_rerank:
                rerank_docs = [
                    {"text": doc_map[d]["content"], "id": d}
                    for d in document_ids if d in doc_map
                ]
                if not rerank_docs:
                    #logger.debug(f"[similarity_search] 出口: collection={collection_name}, 无有效文档可重排")
                    return []
                reranked_results = self.reranker.rerank(query=query, documents=rerank_docs, top_k=top_k)
                final_results = []
                for doc in reranked_results:
                    document_id = doc["id"]
                    if document_id in doc_map:
                        final_results.append({
                            "id": document_id,
                            "rerank_score": doc["score"],
                            "distance": milvus_distance_by_id.get(document_id, 0.0),
                            "content": doc_map[document_id]["content"],
                            "metadata": doc_map[document_id]["metadata"],
                            "collection_name": collection_name
                        })
            else:
                final_results = []
                for doc_id, _source, milvus_d in merged_top:
                    if doc_id in doc_map:
                        doc = doc_map[doc_id]
                        final_results.append({
                            "id": doc_id,
                            "distance": milvus_d,
                            "content": doc["content"],
                            "metadata": doc["metadata"],
                            "collection_name": collection_name
                        })

            #logger.debug(f"[similarity_search] 出口: collection={collection_name}, 返回 {len(final_results)} 条")
            return final_results
        except Exception as e:
            logger.error(f"相似度搜索失败: {str(e)}")
            return []

    def delete_collection(self, collection_name: str) -> bool:
        """
        删除指定集合中的所有条目

        Args:
            collection_name: 集合名

        Returns:
            bool: 删除是否成功
        """
        try:
            # 删除Milvus中的集合
            self.milvus_client.drop_collection(collection_name=collection_name)

            # 删除MongoDB中该集合的所有文档
            self.document_repository.delete_documents_by_collection(collection_name=collection_name)

            # 删除Elasticsearch中该集合的所有文档
            self.es_document_repository.delete_by_collection(collection=collection_name)

            logger.info(f"成功删除集合 {collection_name} 中的所有条目")
            return True
        except Exception as e:
            logger.error(f"删除集合 {collection_name} 失败: {str(e)}")
            return False
