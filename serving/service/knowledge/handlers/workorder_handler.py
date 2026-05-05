# -*- coding: utf-8 -*-
"""
工单知识库处理器
处理工单类型的知识库,包括:
- LLM标准化解析
- Canonical-Raw图谱融合
- 三段式相似度判断
- 故障诊断搜索
"""
from typing import List, Dict, Any, Optional
import uuid
import json
import pandas as pd
import numpy as np

from util.logging.logger import get_logger
from service.system.openai_service import openai_service
from config.config import VECTOR_DIMENSION, COLLECTION_WORKORDER_GRAPH_IDENTIFIER as COLLECTION_WORKORDER_GRAPH, RERANK_SCORE_THRESHOLD
from database.graph.workorder_graph_repository import WorkorderGraphRepository
from database.graph.model import NodeType, KnowledgeRelationship, RelationshipType
# from util.constants import KnowledgeLevel

from .base_handler import BaseKnowledgeHandler
from ..prompts.workorder_handler_prompts import WorkorderHandlerPrompts

logger = get_logger(__name__)


def _normalize_clamping_force(value: Any) -> Optional[int]:
    """将 clamping_force 统一转为 int，便于诊断/标准模式检索 filter 一致命中。"""
    try:
        if value is None or str(value).strip() in ("", "nan", "None"):
            return None
        return int(float(str(value)))
    except (ValueError, TypeError):
        return None


class WorkorderKnowledgeHandler(BaseKnowledgeHandler):
    """
    工单知识库处理器

    特化功能:
    1. 支持故障描述和解决方案的LLM标准化
    2. 支持Canonical-Raw图谱融合
    3. 支持三段式相似度判断
    4. 支持故障诊断搜索(基于图谱)
    """

    def _initialize_components(self):
        """初始化工单特有的组件"""
        # 先调用父类的初始化
        super()._initialize_components()

        # 初始化工单图谱仓库，使用 collection_name 作为 Neo4j 数据库名
        # 社区版neo4j不支持多database，只使用默认database
        self.workorder_graph_repository = WorkorderGraphRepository()
        # self.workorder_graph_repository = WorkorderGraphRepository(database=self.collection_name)
    
        # === 缓存初始化 ===
        # 用于批量插入 Milvus 的缓冲区
        self._vector_buffer = []  # List[Dict]: [{'id', 'vector', 'metadata'}]
        # 用于内存匹配的 Canonical 缓存 (避免批量插入前查不到刚生成的节点)
        self._canonical_cache = [] # List[Dict]: [{'id', 'vector', 'metadata'}]
        # 批量处理阈值
        self._batch_size = 1000

    def _flush_vectors(self):
        """将缓冲区中的向量批量写入 Milvus"""
        if not self._vector_buffer:
            return

        try:
            vectors = [item['vector'] for item in self._vector_buffer]
            metadatas = [item['metadata'] for item in self._vector_buffer]
            ids = [item['id'] for item in self._vector_buffer]

            # 批量插入
            self.milvus_client.upsert_vectors(
                collection_name=self.collection_name,
                vectors=vectors,
                metadata_list=metadatas,
                ids=ids
            )
            logger.info(f"批量 Flush Milvus 成功: {len(ids)} 条数据")
            
            # 清空缓冲区
            self._vector_buffer.clear()
            
        except Exception as e:
            logger.error(f"批量 Flush Milvus 失败: {str(e)}")
            # 这里可以选择抛出异常或根据业务需求处理

    def _clear_caches(self):
        """清空所有缓存"""
        self._vector_buffer = []
        self._canonical_cache = []

    # ==================== 必须实现的抽象方法 ====================

    def ingest(
        self,
        files: List[Any],
        filenames: List[str],
        metadata: Optional[Dict] = None
    ) -> List[str]:
        """
        工单数据入库

        支持两种模式:
        1. 如果 metadata 中有 enable_diagnosis="true"，则调用工单特化处理
        2. 否则回退到标准流程

        Args:
            files: 文件流列表
            filenames: 文件名列表
            metadata: 额外的元数据

        Returns:
            List[str]: 创建的文档ID列表
        """
        # 确保集合存在 (只在 flush 时检查一次即可，或者在 start 时检查)
        self.milvus_client.create_collection(
            collection_name=self.collection_name,
            dimension=VECTOR_DIMENSION
        )

        if self.collection_type == "special_workorder":
            raise NotImplementedError(
                "special_workorder 正式入库已迁移到 ingestion，请通过 /api/ingest/upload 调用"
            )

        # 检查是否启用诊断模式
        if metadata and metadata.get("enable_diagnosis") == "true":
            # 使用工单特化的处理流程
            return self._ingest_workorder_with_diagnosis(
                files, filenames, metadata
            )
        else:
            # 回退到标准流程
            return self._ingest_standard(files, filenames, metadata)

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
        工单搜索

        支持两种模式:
        1. 如果 metadata 中有 enable_diagnosis="true" 或查询包含 [DIAGNOSIS] 标记，
           则触发图谱诊断搜索
        2. 否则使用标准的向量搜索

        Args:
            query: 查询文本
            top_k: 返回结果数量
            metadata: 元数据过滤条件
            use_rerank: 是否使用重排序
            rejected_workorder_ids: 要排除的派工单号列表（检索时即不返回）

        Returns:
            List[Dict[str, Any]]: 搜索结果列表
        """
        DIAGNOSIS_TRIGGER = "[DIAGNOSIS]"

        # 检查是否触发诊断
        should_trigger_diagnosis = (
            (metadata and metadata.get("enable_diagnosis") == "true") or
            DIAGNOSIS_TRIGGER in query
        )

        if should_trigger_diagnosis:
            # 使用诊断搜索
            # 移除触发标记
            clean_query = query.replace(DIAGNOSIS_TRIGGER, "").strip()
            diagnosis_result = self.diagnose(clean_query, top_k, metadata=metadata)

            if diagnosis_result:
                # 转换为标准搜索结果格式
                return [{
                    'id': 'diagnosis_result',
                    **diagnosis_result
                }]
            else:
                return []
        else:
            # 使用标准向量搜索（排除的工单在 Milvus 检索时过滤）
            from database.graph.neo4j_manager import Neo4jManager
            if "graph" in self.collection_name.lower() and not Neo4jManager.is_available():
                logger.warning(f"[WorkorderHandler] Neo4j不可用，跳过 {self.collection_name} 检索，返回空")
                return []
            results = self.similarity_search(
                query=query,
                collection_name=self.collection_name,
                top_k=top_k,
                use_rerank=use_rerank,
                metadata=metadata,
                rejected_workorder_ids=rejected_workorder_ids,
                query_vector=query_vector
            )
            if "graph" not in self.collection_name.lower():
                from service.system.file_reader_main import file_reader
                for result in results:
                    result["image_ids"] = file_reader.get_document_image_ids(str(result.get("id")))
            return results

    def delete_documents(self, document_ids: List[str]) -> Dict[str, int]:
        """
        删除工单文档

        工单特化: 需要同时删除图谱和向量数据

        Args:
            document_ids: 要删除的文档ID列表

        Returns:
            Dict[str, int]: 删除结果统计
        """
        return self._delete_workorder_documents(document_ids)

    # ==================== 工单特有的入库逻辑 ====================

    def _ingest_workorder_with_diagnosis(
        self,
        files: List[Any],
        filenames: List[str],
        metadata: Optional[Dict] = None
    ) -> List[str]:
        """
        使用诊断模式进行工单入库

        流程:
        1. 保存文件到磁盘
        2. 调用 process_fault_files 处理Excel
        3. 返回创建的知识节点ID

        Args:
            files: 文件流列表
            filenames: 文件名列表
            metadata: 额外的元数据

        Returns:
            List[str]: 创建的文档ID列表
        """
        from service.system.file_reader_main import file_reader

        # 保存上传的文件
        file_paths = []
        for file, filename in zip(files, filenames):
            file_path = file_reader.save_file(file, filename)
            if file_path:
                file_paths.append(file_path)

        if not file_paths:
            logger.error("没有成功保存任何文件")
            return []

        # 处理故障文件
        details = metadata.get("details", "") if metadata else ""
        knowledge_nodes = self.process_fault_files(file_paths, details)

        # 提取文档ID
        document_ids = []
        for node in knowledge_nodes:
            if isinstance(node, dict) and 'id' in node:
                document_ids.append(node['id'])
            elif hasattr(node, 'id'):
                document_ids.append(node.id)

        logger.info(f"工单入库完成，创建 {len(document_ids)} 个文档")
        return document_ids

    def _ingest_standard(
        self,
        files: List[Any],
        filenames: List[str],
        metadata: Optional[Dict] = None
    ) -> List[str]:
        """
        使用标准流程进行工单入库

        Args:
            files: 文件流列表
            filenames: 文件名列表
            metadata: 额外的元数据

        Returns:
            List[str]: 创建的文档ID列表
        """
        # 使用父类的标准入库流程
        results = self._standard_ingest_flow(
            files=files,
            filenames=filenames,
            collection_name=self.collection_name,
            collection_type=self.collection_type,
            metadata=metadata
        )

        # 提取所有文档ID
        document_ids = []
        for result in results:
            if "document_ids" in result:
                document_ids.extend(result["document_ids"])

        return document_ids

    # ==================== 工单特有的搜索逻辑 ====================

    def diagnose(self, query: str, limit: int = 5, metadata: Dict = None) -> Optional[Dict[str, Any]]:
        """
        基于用户输入进行故障诊断，直接使用Milvus进行相似性检索

        Args:
            query: 用户输入的故障描述
            limit: 返回的最大结果数量

        Returns:
            Optional[Dict[str, Any]]: 包含诊断排查树和metadata的字典
        """
        try:
            # 生成查询文本的嵌入向量
            query_embedding = self.generate_embeddings([query])[0]

            # 在Milvus中搜索相似向量（只召回 CanonicalFault 节点，确保 id 能被 Neo4j Cypher 命中）
            search_results = self.milvus_client.search_vectors(
                collection_name=self.collection_name,
                query_vectors=[query_embedding.tolist()],
                top_k=limit * 2,
                metadata_filter={
                    "知识级别": NodeType.CANONICAL_FAULT.value,
                    "is_deleted": False
                }
            )

            if not search_results or not search_results[0]:
                return None

            # 获取所有相关文档的ID
            raw_hits = search_results[0]
            doc_ids = [str(result["id"]) for result in raw_hits]

            # 打印每个节点的距离分数和 metadata 摘要
            for r in raw_hits:
                r_meta = r.get("metadata", {})
                logger.info(f"[Diagnose]   id={r.get('id')} dist={r.get('distance', '?'):.4f} "
                            f"产品线={r_meta.get('产品线', '')} 机型码={r_meta.get('机型码', '')} "
                            f"代次={r_meta.get('代次', '')}")

            # ===== Fault 层 reranker 精排 =====
            if len(raw_hits) > limit:
                try:
                    # 从 MongoDB 获取 fault 的 description 文本（Milvus 只存了 metadata，没存原文）
                    hit_ids = [str(r["id"]) for r in raw_hits]
                    documents = self.get_documents(document_id=hit_ids, collection_name=self.collection_name)
                    doc_content_map = {doc["document_id"]: doc.get("content", "") for doc in documents}

                    from service.knowledge.vector_retrieval.rerank.dashscope_reranker import DashScopeReranker
                    reranker = DashScopeReranker()

                    # 准备 reranker 输入: text = fault description
                    rerank_docs = []
                    for idx, r in enumerate(raw_hits):
                        text = doc_content_map.get(str(r["id"]), "")
                        rerank_docs.append({"text": text, "id": idx})

                    logger.debug(f"[Diagnose] Fault reranker 输入 ({len(rerank_docs)} 个):")
                    for doc in rerank_docs:
                        logger.debug(f"  [{doc['id']}] text={doc['text'][:60]}")

                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _executor:
                        _future = _executor.submit(
                            reranker.rerank, query=query, documents=rerank_docs, top_k=limit
                        )
                        try:
                            reranked = _future.result(timeout=5.0)
                        except concurrent.futures.TimeoutError:
                            logger.warning("[Diagnose] Rerank 超时(5s)，fallback 到 Milvus 原始顺序")
                            reranked = []

                    # Fallback 1: 返回空
                    if not reranked:
                        logger.warning("[Diagnose] Fault reranker 返回空列表, fallback 到 Milvus 原始顺序")
                        raw_hits = raw_hits[:limit]

                    # Fallback 2: 全 0 分（API 失败）
                    elif all(doc.get("score", 0) <= 0 for doc in reranked):
                        logger.warning(
                            f"[Diagnose] Fault reranker 返回全 0 分 ({len(reranked)} 个), "
                            f"fallback 到 Milvus 原始顺序"
                        )
                        raw_hits = raw_hits[:limit]

                    # 正常路径
                    else:
                        reranked_hits = []
                        for doc in reranked[:limit]:
                            if doc.get("score", 0) < RERANK_SCORE_THRESHOLD:
                                logger.warning(
                                    f"[Diagnose] rerank分数 {doc.get('score', 0):.3f} "
                                    f"< 阈值 {RERANK_SCORE_THRESHOLD}，跳过"
                                )
                                continue
                            idx = doc.get("id")
                            if idx is not None and 0 <= idx < len(raw_hits):
                                hit = raw_hits[idx]
                                hit["fault_rerank_score"] = doc.get("score", 0)
                                reranked_hits.append(hit)

                        logger.info(
                            f"[Diagnose] Fault reranker 精排: {len(raw_hits)} → {len(reranked_hits)} 个 "
                            f"(top={reranked[0].get('score', 0):.3f}, "
                            f"bottom={reranked[min(limit-1, len(reranked)-1)].get('score', 0):.3f})"
                        )
                        # 精排明细
                        for i, hit in enumerate(reranked_hits):
                            text = doc_content_map.get(str(hit["id"]), "")[:60]
                            logger.info(
                                f"[Diagnose]   [{i+1}] score={hit.get('fault_rerank_score', 0):.4f} | {text}"
                            )

                        raw_hits = reranked_hits

                # Fallback 3: 任何异常
                except Exception as e:
                    logger.error(
                        f"[Diagnose] Fault reranker 调用失败, fallback 到 Milvus 原始顺序: {e}",
                        exc_info=True
                    )
                    raw_hits = raw_hits[:limit]
            else:
                logger.info(f"[Diagnose] Milvus 召回数 {len(raw_hits)} ≤ {limit}, 跳过 fault reranker")

            # 获取最终的文档 ID
            doc_ids = [str(result["id"]) for result in raw_hits[:limit]]
            logger.info(f"[Diagnose] 最终 fault 节点 ({len(doc_ids)} 个): {doc_ids}")

            # 从知识图谱中获取相关节点和关系（使用精简的 Canonical 查询）
            diagnosis_result = self._build_canonical_diagnosis_tree(doc_ids)

            return diagnosis_result

        except Exception as e:
            logger.error(f"诊断过程发生错误: {str(e)}")
            import traceback
            traceback.print_exc()
            raise

    def _build_diagnosis_tree(self, doc_ids: List[str]) -> Optional[Dict[str, Any]]:
        """
        构建诊断排查树，返回包含content和metadata的字典

        Args:
            doc_ids: 知识文档ID列表

        Returns:
            Optional[Dict[str, Any]]: 包含content和metadata的字典
        """
        try:
            # 获取所有相关节点及其网络
            network = self.workorder_graph_repository.get_nodes_networks_with_min_requirements(
                doc_ids, max_depth=2
            )

            if not network["nodes"]:
                return None

            # 创建UUID到简单ID的映射
            uuid_to_simple_id = {}
            simple_id_counter = 1

            # 构建实体上下文
            entities_context = []
            for node in network["nodes"]:
                original_id = node.get("id", "")
                if original_id not in uuid_to_simple_id:
                    uuid_to_simple_id[original_id] = simple_id_counter
                    simple_id_counter += 1

                entity = {
                    "id": uuid_to_simple_id[original_id],
                    "entity_type": node.get("type", ""),
                    "description": node.get("description", "")
                }
                entities_context.append(entity)

            # 构建关系上下文
            relations_context = []
            for rel in network.get("relationships", []):
                source_id = rel.get("source_id", "")
                target_id = rel.get("target_id", "")

                if source_id not in uuid_to_simple_id or target_id not in uuid_to_simple_id:
                    continue

                relation = {
                    "src_id": uuid_to_simple_id[source_id],
                    "tgt_id": uuid_to_simple_id[target_id],
                    "relation_type": rel.get("type", "")
                }
                relations_context.append(relation)

            # 转换为JSON字符串
            entities_str = json.dumps(entities_context, ensure_ascii=False)
            relations_str = json.dumps(relations_context, ensure_ascii=False)

            # 格式化为ApeRAG结构化字符串
            content = f"""-----Entities(KG)-----
```json
{entities_str}
```
-----Relationships(KG)-----
```json
{relations_str}
```"""

            # 从Milvus中获取metadata信息
            metadata_list = []

            # 找到"完整故障描述"类型的节点
            fault_description_nodes = []
            for node in network["nodes"]:
                if node.get("type") == "FAULT":
                    fault_description_nodes.append(node.get("id"))

            # 获取故障描述节点的metadata
            if fault_description_nodes:
                vector_results = self.milvus_client.get_by_ids(
                    collection_name=self.collection_name,
                    ids=fault_description_nodes
                )

                for result in vector_results:
                    metadata = result.get("metadata", {})
                    if metadata:
                        filtered_metadata = {
                            k: v for k, v in metadata.items()
                            if k not in ["字段名", "明细", "is_deleted"]
                        }
                        if filtered_metadata:
                            metadata_list.append(filtered_metadata)

            return {
                "content": content,
                "metadata_list": metadata_list,
                "metadata": metadata_list[0] if metadata_list else {}
            }

        except Exception as e:
            logger.error(f"构建诊断树失败: {str(e)}")
            return None

    def _build_canonical_diagnosis_tree(self, doc_ids: List[str], max_solutions: int = 15, neo4j_filter: Dict[str, str] = None, neo4j_exclude: Dict[str, List[str]] = None) -> Optional[Dict[str, Any]]:
        """
        构建精简的标准诊断树，只包含 CanonicalFault 和 CanonicalSolution（同事的 Neo4j 路径）

        Args:
            doc_ids: 知识文档ID列表
            max_solutions: LLM 精简版最多保留的解决方案条数，默认15

        Returns:
            Optional[Dict[str, Any]]: 包含 content（前端图谱用）、llm_content（LLM用）、metadata 的字典
        """
        try:
            # 调用精简的图谱查询
            network = self.workorder_graph_repository.get_canonical_diagnosis_path(doc_ids)

            if not network["nodes"]:
                return None

            # 创建UUID到简单ID的映射
            uuid_to_simple_id = {}
            simple_id_counter = 1

            # 构建实体上下文，同时分离 fault 和 solution
            entities_context = []
            fault_descriptions = []
            solution_descriptions = []

            for node in network["nodes"]:
                original_id = node.get("id", "")
                if original_id not in uuid_to_simple_id:
                    uuid_to_simple_id[original_id] = simple_id_counter
                    simple_id_counter += 1

                entity = {
                    "id": uuid_to_simple_id[original_id],
                    "entity_type": node.get("type", ""),
                    "description": node.get("description", "")
                }
                entities_context.append(entity)

                # 收集 fault 和 solution 描述
                node_type = node.get("type", "")
                description = node.get("description", "").strip()
                if description:
                    if node_type == "CanonicalFault":
                        fault_descriptions.append(description)
                    elif node_type == "CanonicalSolution":
                        # 解析节点 metadata JSON，做 include/exclude 过滤
                        if neo4j_filter or neo4j_exclude:
                            node_meta = {}
                            raw_meta = node.get("metadata", "")
                            if isinstance(raw_meta, str) and raw_meta.strip():
                                try:
                                    node_meta = json.loads(raw_meta)
                                except (json.JSONDecodeError, TypeError):
                                    node_meta = {}
                            elif isinstance(raw_meta, dict):
                                node_meta = raw_meta

                            # include 过滤：neo4j_filter 中的每个字段，节点 metadata 中必须匹配
                            skip = False
                            if neo4j_filter:
                                for field, expected_val in neo4j_filter.items():
                                    actual_val = str(node_meta.get(field, "")).strip()
                                    if not actual_val or actual_val.lower() == "nan":
                                        continue  # 节点没这个字段，不排除（宽松匹配）
                                    if actual_val != expected_val:
                                        skip = True
                                        break

                            # exclude 过滤：neo4j_exclude 中的字段值，节点 metadata 中不能匹配
                            if not skip and neo4j_exclude:
                                for field, excluded_vals in neo4j_exclude.items():
                                    actual_val = str(node_meta.get(field, "")).strip()
                                    if actual_val and actual_val.lower() != "nan" and actual_val in excluded_vals:
                                        skip = True
                                        break

                            if skip:
                                continue

                        solution_descriptions.append(description)

            # 构建关系上下文
            relations_context = []
            for rel in network.get("relationships", []):
                source_id = rel.get("source_id", "")
                target_id = rel.get("target_id", "")

                if source_id not in uuid_to_simple_id or target_id not in uuid_to_simple_id:
                    continue

                relation = {
                    "src_id": uuid_to_simple_id[source_id],
                    "tgt_id": uuid_to_simple_id[target_id],
                    "relation_type": rel.get("type", "")
                }
                relations_context.append(relation)

            # 转换为JSON字符串
            entities_str = json.dumps(entities_context, ensure_ascii=False)
            relations_str = json.dumps(relations_context, ensure_ascii=False)

            # content: 前端图谱渲染用（保留完整结构）
            content = f"""-----Entities(KG)-----
```json
{entities_str}
```
-----Relationships(KG)-----
```json
{relations_str}
```"""

            # llm_content: 给 LLM 用（精简版，只保留描述文本，限制条数）
            truncated_solutions = solution_descriptions[:max_solutions]
            lines = []
            if fault_descriptions:
                lines.append(f"故障类型：{fault_descriptions[0]}")
            lines.append(f"\n以下是与该故障相关的历史工单解决方案（共{len(truncated_solutions)}条，总计{len(solution_descriptions)}条）：")
            for i, desc in enumerate(truncated_solutions, 1):
                lines.append(f"{i}. {desc}")
            llm_content = "\n".join(lines)

            total_solutions = len([e for e in entities_context if e.get("entity_type") == "CanonicalSolution"])
            logger.info(f"[诊断树] 前端图谱: {len(entities_context)}个实体, {len(relations_context)}个关系 | Solution总数: {total_solutions}, 过滤后: {len(solution_descriptions)}, LLM取: {len(truncated_solutions)}条")

            return {
                "content": content,
                "llm_content": llm_content,
                "metadata": {},
                "collection_name": COLLECTION_WORKORDER_GRAPH
            }

        except Exception as e:
            logger.error(f"构建诊断树失败: {str(e)}")
            return None

    # ==================== 工单特有的删除逻辑 ====================

    def _delete_workorder_documents(self, doc_ids: List[str]) -> Dict[str, int]:
        """
        删除工单文档，同时删除图谱和向量数据

        Args:
            doc_ids: 要删除的文档ID列表

        Returns:
            Dict[str, int]: 删除结果统计
        """
        if self.collection_type == "special_workorder":
            return self._soft_delete_special_workorder_documents(doc_ids)

        try:
            logger.info(f"开始删除工单文档，doc_ids: {doc_ids}")
            result = {
                "deleted_nodes": 0,
                "deleted_relationships": 0,
                "deleted_vectors": 0,
                "deleted_mongo": 0,
                "deleted_es": 0,
                "failed_deletions": []
            }

            # 1. 删除图谱节点
            for doc_id in doc_ids:
                try:
                    delete_success = self.workorder_graph_repository.delete_node(doc_id)
                    if delete_success:
                        result["deleted_nodes"] += 1
                    else:
                        result["failed_deletions"].append(f"文档节点 {doc_id} 不存在")
                except Exception as e:
                    logger.error(f"删除文档节点失败: {doc_id}, 错误: {str(e)}")
                    result["failed_deletions"].append(f"删除文档节点失败: {doc_id}")

            # 2. 从Milvus中删除向量
            try:
                delete_result = self.milvus_client.delete_vectors(
                    collection_name=self.collection_name,
                    ids=list(doc_ids)
                )
                if delete_result:
                    result["deleted_vectors"] = len(doc_ids)
            except Exception as e:
                logger.error(f"从Milvus删除向量失败: {str(e)}")
                result["failed_deletions"].append(f"从Milvus删除向量失败")

            # 3. 从MongoDB中软删除文档
            try:
                mongo_deleted_count = self.document_repository.soft_delete_documents(
                    document_ids=list(doc_ids),
                    collection_name=self.collection_name
                )
                result["deleted_mongo"] = mongo_deleted_count
                logger.info(f"从MongoDB软删除 {mongo_deleted_count} 个文档成功")
            except Exception as e:
                logger.error(f"从MongoDB删除文档失败: {str(e)}")
                result["failed_deletions"].append(f"从MongoDB删除文档失败")

            # 4. 从Elasticsearch中软删除文档（与 _standard_delete_documents 保持一致）
            try:
                es_deleted_count = self.es_document_repository.soft_delete_documents(list(doc_ids))
                result["deleted_es"] = es_deleted_count
                logger.info(f"从ES软删除 {es_deleted_count} 个文档成功")
            except Exception as e:
                logger.warning(f"从Elasticsearch删除文档失败，跳过: {str(e)}")
                result["failed_deletions"].append(f"从Elasticsearch删除文档失败")

            logger.info(f"工单删除完成: {result}")
            return result

        except Exception as e:
            logger.error(f"删除工单文档失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return {
                "deleted_nodes": 0,
                "deleted_vectors": 0,
                "deleted_mongo": 0,
                "deleted_es": 0,
                "failed_deletions": [str(e)]
            }

    def _soft_delete_special_workorder_documents(self, doc_ids: List[str]) -> Dict[str, int]:
        """special_workorder 统一走软删除。"""
        try:
            logger.info(f"开始软删除 special_workorder 文档，doc_ids: {doc_ids}")
            result = {
                "deleted_count": 0,
                "total_count": len(doc_ids),
                "deleted_vectors": 0,
                "deleted_mongo": 0,
                "deleted_es": 0,
                "failed_deletions": []
            }

            try:
                milvus_deleted_count = self.milvus_client.soft_delete_by_ids(
                    self.collection_name,
                    list(doc_ids)
                )
                result["deleted_vectors"] = milvus_deleted_count
            except Exception as e:
                logger.warning(f"special_workorder 在 Milvus 中软删除失败: {str(e)}")
                result["failed_deletions"].append("milvus")

            try:
                mongo_deleted_count = self.document_repository.soft_delete_documents(
                    document_ids=list(doc_ids),
                    collection_name=self.collection_name
                )
                result["deleted_mongo"] = mongo_deleted_count
            except Exception as e:
                logger.warning(f"special_workorder 在 MongoDB 中软删除失败: {str(e)}")
                result["failed_deletions"].append("mongodb")

            try:
                es_deleted_count = self.es_document_repository.soft_delete_documents(list(doc_ids))
                result["deleted_es"] = es_deleted_count
            except Exception as e:
                logger.warning(f"special_workorder 在 Elasticsearch 中软删除失败: {str(e)}")
                result["failed_deletions"].append("elasticsearch")

            result["deleted_count"] = max(
                result["deleted_vectors"],
                result["deleted_mongo"],
                result["deleted_es"]
            )
            logger.info(f"special_workorder 软删除完成: {result}")
            return result
        except Exception as e:
            logger.error(f"special_workorder 软删除失败: {str(e)}")
            return {
                "deleted_count": 0,
                "total_count": len(doc_ids),
                "deleted_vectors": 0,
                "deleted_mongo": 0,
                "deleted_es": 0,
                "failed_deletions": [str(e)]
            }

    # ==================== 工单特有的处理方法 ====================

    def process_fault_files(self, file_list: List[str], details: str = "") -> List[Dict[str, Any]]:
        """
        处理故障文件列表，支持基于details筛选Excel数据

        Args:
            file_list: 文件路径列表
            details: 明细信息，用于筛选Excel中的"明细"字段

        Returns:
            List[Dict[str, Any]]: 所有文件处理后的知识节点列表
        """
        try:
            all_knowledge_nodes = []
            total_files = len(file_list)
            logger.info(f"开始处理 {total_files} 个故障文件")

            for file_idx, file_path in enumerate(file_list, 1):
                try:
                    logger.info(f"处理第 {file_idx}/{total_files} 个文件: {file_path}")

                    # 检查是否为Excel文件
                    if file_path.lower().endswith(('.xlsx', '.xls')):
                        # 从Excel读取故障数据
                        fault_data_list = self._read_fault_descriptions_from_excel(file_path, details)
                        logger.info(f"从文件 {file_path} 中读取到 {len(fault_data_list)} 条故障数据")

                        for fault_idx, fault_data in enumerate(fault_data_list, 1):
                            logger.info(f"处理第 {fault_idx}/{len(fault_data_list)} 条故障数据")

                            fault_desc = fault_data['fault_description']
                            solution = fault_data['solution']
                            metadata = fault_data['metadata']

                            # 处理故障工单
                            knowledge_nodes = self._process_fault_solution_pair(
                                fault=fault_desc,
                                solution=solution,
                                details=details,
                                metadata=metadata
                            )
                            all_knowledge_nodes.extend(knowledge_nodes)
                        
                            # 定期 Flush，防止内存过大
                            if len(self._vector_buffer) >= self._batch_size:
                                self._flush_vectors()
                            logger.info(f"当前文件已生成 {len(knowledge_nodes)} 个知识节点")
                    else:
                        logger.warning(f"目前只支持Excel文件处理: {file_path}")
                        continue

                except Exception as e:
                    logger.error(f"处理文件失败 {file_path}: {str(e)}")
                    continue

            logger.info(f"成功处理 {len(all_knowledge_nodes)} 个知识节点")
            return all_knowledge_nodes

        except Exception as e:
            logger.error(f"处理文件列表失败: {str(e)}")
            raise
        finally:
            # 无论正常结束还是异常中断，都确保 buffer 中剩余向量写入 Milvus
            # _flush_vectors() 内部已处理 buffer 为空的情况，不会重复写入
            self._flush_vectors()

    def _read_fault_descriptions_from_excel(
        self, file_path: str, target_value: str = ""
    ) -> List[Dict[str, Any]]:
        """
        从Excel文件中读取故障描述和解决方案数据

        Args:
            file_path: Excel文件路径
            target_value: 用于筛选"明细"字段的值

        Returns:
            List[Dict[str, Any]]: 故障数据列表
        """
        try:
            # 读取Excel文件
            df = pd.read_excel(file_path)

            # 如果有target_value，则筛选特定值的行
            if target_value:
                filtered_df = df[df['明细'] == target_value]
            else:
                filtered_df = df

            # 提取故障描述和解决方案字段
            fault_data = []
            for idx, row in filtered_df.iterrows():
                row_dict = row.to_dict()
                fault_description = row_dict.get('故障描述', '')
                solution = row_dict.get('解决方案', '')
                if fault_description:
                    # 创建metadata，排除故障描述和解决方案字段
                    raw_metadata = {
                        k: v for k, v in row_dict.items()
                        if k not in ['故障描述', '解决方案']
                    }
                    # 将所有metadata字段都转换为字符串，clamping_force/锁模力 统一为 int
                    metadata = {
                        k: str(v) if v is not None else ""
                        for k, v in raw_metadata.items()
                    }
                    cf = _normalize_clamping_force(
                        raw_metadata.get("clamping_force") or raw_metadata.get("锁模力")
                    )
                    if cf is not None:
                        metadata["clamping_force"] = cf
                    if "锁模力" in raw_metadata and cf is not None:
                        metadata["锁模力"] = cf
                    fault_data.append({
                        'fault_description': fault_description,
                        'solution': solution,
                        'metadata': metadata
                    })

            return fault_data
        except Exception as e:
            logger.error(f"读取Excel文件失败: {str(e)}")
            return []

    def _process_fault_solution_pair(
        self, fault: str, solution: str, details: str = "故障工单",
        metadata: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        处理故障工单（新逻辑）
        1. 将一行数据合并为一个完整的 Raw 节点并创建
        2. 尝试在现有 Canonical 节点中寻找匹配
        3. 建立 Raw -> Canonical 的 INSTANCE_OF 关系
        """
        try:
            # 处理故障描述
            raw_fault_id, canonical_fault_id = self._process_and_canonicalize_entry(
                text=fault, 
                details=details, 
                metadata=metadata, 
                knowledge_type="fault"
            )
            
            # 处理解决方案
            raw_solution_id, canonical_solution_id = self._process_and_canonicalize_entry(
                text=solution, 
                details=details, 
                metadata=metadata, 
                knowledge_type="solution"
            )

            # 1. 建立 Raw 层面的关系：Raw Fault -> Raw Solution
            self.workorder_graph_repository.create_relationship(
                relationship=KnowledgeRelationship(
                    source_id=raw_fault_id,
                    target_id=raw_solution_id,
                    type=RelationshipType.HAS_SOLUTION
                )
            )

            # 2. 建立 Canonical 层面的关系：Canonical Fault -> Canonical Solution
            # 只有当两个Canonical节点都存在，且不相同时才建立关系
            if canonical_fault_id and canonical_solution_id:
                # 即使关系已存在，图数据库通常会忽略重复创建或更新属性
                # 这里建立标准故障与标准解决方案的关联，完善图谱结构
                self.workorder_graph_repository.create_relationship(
                    relationship=KnowledgeRelationship(
                        source_id=canonical_fault_id,
                        target_id=canonical_solution_id,
                        type=RelationshipType.HAS_SOLUTION,
                        properties={"source": "workorder_ingest_canonical_link"}
                    )
                )
                logger.debug(f"建立Canonical关系: Fault({canonical_fault_id}) -> Solution({canonical_solution_id})")

            # 返回结果（用于日志或统计）
            return [{
                "id": raw_fault_id,
                "type": NodeType.RAW_FAULT.value,
                "canonical_id": canonical_fault_id
            }, {
                "id": raw_solution_id,
                "type": NodeType.RAW_SOLUTION.value,
                "canonical_id": canonical_solution_id
            }]

        except Exception as e:
            logger.error(f"处理故障工单失败: {str(e)}")
            import traceback
            traceback.print_exc()
            raise

    def _process_and_canonicalize_entry(
        self, text: str, details: str, metadata: Optional[Dict[str, Any]] = None, knowledge_type: str = "fault"
    ) -> tuple[str, str]:
        field_name = "故障描述" if knowledge_type == "fault" else "解决方案"

        # 首先，无条件创建一个新的 Raw 节点
        # 这代表了 Excel 中的这一行原始记录
        raw_node_id = self._create_new_knowledge_node(
            text=text,
            field_name=field_name,
            details=details,
            metadata=metadata,
            node_type=NodeType.RAW_FAULT if knowledge_type == "fault" else NodeType.RAW_SOLUTION
        )

        # 寻找或创建对应的 Canonical 节点
        # 传入 raw_node_id 是为了在没找到匹配时，可以直接复用 raw 的内容来创建 canonical
        canonical_id, fusion_info = self._handle_canonical_matching(
            content=text,
            field_name=field_name,
            details=details,
            metadata=metadata,
            knowledge_type=knowledge_type
        )

        # 4. 建立关系：Raw (实例) -> Canonical (标准)
        self.workorder_graph_repository.create_relationship(
            relationship=KnowledgeRelationship(
                source_id=raw_node_id,
                target_id=canonical_id,
                type=RelationshipType.INSTANCE_OF,
                properties=fusion_info
            )
        )
        logger.debug(f"建立关系: Raw({raw_node_id}) -> Canonical({canonical_id})")

        return raw_node_id, canonical_id
    
    # TODO
    # 判断是否需要进行 Canonical 内容进化
    def _handle_canonical_matching(
        self,
        content: str,
        field_name: str,
        details: str,
        metadata: Dict[str, Any],
        knowledge_type: str = "fault",
    ) -> tuple[str, Dict[str, Any]]:
        """
        处理 Canonical 匹配逻辑
        
        优化：统一本地缓存和数据库的检索结果，共用一套判别逻辑
        """
        # 生成嵌入向量
        embedding = self.generate_embeddings([content])[0]
        target_type = NodeType.CANONICAL_FAULT.value if knowledge_type == "fault" else NodeType.CANONICAL_SOLUTION.value

        # 定义最佳匹配候选对象
        best_candidate = {
            "id": None,
            "score": -1.0,
            "metadata": {},
            "source": None  # 'local' or 'db'
        }

        # === 1. 搜索本地缓存 ===
        if self._canonical_cache:
            cache_candidates = [
                item for item in self._canonical_cache 
                if item['metadata'].get('知识级别') == target_type
            ]
            if cache_candidates:
                candidate_vectors = np.array([item['vector'] for item in cache_candidates])
                query_vector = np.array(embedding)
                
                # 计算余弦相似度
                scores = np.dot(candidate_vectors, query_vector)
                best_idx = np.argmax(scores)
                best_local_score = scores[best_idx]
                
                # 更新最佳候选
                best_candidate = {
                    "id": cache_candidates[best_idx]['id'],
                    "score": best_local_score,
                    "metadata": cache_candidates[best_idx]['metadata'],
                    "source": 'local'
                }
                # logger.debug(f"本地缓存最佳匹配: {best_local_score:.3f}")

        # === 2. 搜索数据库 (如果本地没有达到"绝对确信" > 0.99，还是建议去库里看看有没有更好的) ===
        # 优化：如果本地已经 > 0.95 (高相似度阈值)，为了性能可以跳过查库
        if best_candidate["score"] <= 0.95:
            search_filter = {"知识级别": target_type, "is_deleted": False}
            search_results = self.milvus_client.search_vectors(
                collection_name=self.collection_name,
                query_vectors=[embedding.tolist()],
                top_k=1,
                metadata_filter=search_filter
            )

            if search_results and search_results[0]:
                db_match = search_results[0][0]
                db_score = db_match["distance"]
                
                # 如果数据库里的匹配度更高，则覆盖
                if db_score > best_candidate["score"]:
                    # 注意：Milvus search 结果通常不带 metadata 里的 content，
                    # 如果后续 LLM 需要 content，可能需要 get_documents 查一下。
                    # 这里先取 search 带回来的 metadata
                    best_candidate = {
                        "id": str(db_match["id"]),
                        "score": db_score,
                        "metadata": db_match.get("metadata", {}),
                        "source": 'db'
                    }
                    # logger.debug(f"数据库找到更好匹配: {db_score:.3f}")

        # === 3. 统一逻辑判断 (三段式) ===
        similarity = best_candidate["score"]
        existing_id = best_candidate["id"]
        
        canonical_id = None
        should_create_new = True
        
        # 用于存储在关系上的属性
        fusion_info = {
            "source": "workorder_ingest",
            "similarity": 0.0,
            "reason": "Created New"
        }

        if existing_id is not None:
            if similarity > 0.95:
                # [高相似度] 直接融合
                logger.info(f"高相似度 Canonical ({similarity:.3f}), ID: {existing_id} [Source: {best_candidate['source']}]")
                canonical_id = existing_id
                should_create_new = False
                
                # 记录到关系属性
                fusion_info = {
                    "source": "vector_match",
                    "similarity": float(similarity),
                    "reason": "High Similarity (Auto Merge)",
                    "judge_method": "vector"
                }

                self._merge_canonical_knowledge(existing_id, metadata or {})

            elif 0.80 <= similarity <= 0.95:
                # [中相似度] LLM 介入
                logger.info(f"中等相似度 Canonical ({similarity:.3f}), ID: {existing_id} [Source: {best_candidate['source']}], 调用 LLM 判断")
                
                # 获取用于对比的旧文本
                existing_text_content = ""
                if best_candidate['source'] == 'local':
                    # 本地缓存里直接有 content
                    # 需要在 _create_new_knowledge_node 里存入 content 到 buffer
                    # 假设 _canonical_cache 结构里存了 'content'
                    for item in self._canonical_cache:
                        if item['id'] == existing_id:
                            existing_text_content = item.get('content', '')
                            break
                else:
                    # 数据库查出来的，search result 可能没带 content，需要回查
                    docs = self.get_documents(document_id=existing_id)
                    existing_text_content = docs[0]['content'] if docs else ""

                is_same, reason = self._llm_judge_similarity(
                    query_text=content,
                    existing_text=existing_text_content,
                    similarity=similarity
                )

                if is_same:
                    logger.info("LLM 判断为同一故障")
                    canonical_id = existing_id
                    should_create_new = False
                    
                    # 记录到关系属性
                    fusion_info = {
                        "source": "llm_match",
                        "similarity": float(similarity),
                        "reason": reason,
                        "judge_method": "llm"
                    }

                    self._merge_canonical_knowledge(existing_id, metadata or {})
                else:
                    logger.info("LLM 判断为不同故障")
                    should_create_new = True
            
            else:
                # [低相似度]
                should_create_new = True
        
        # === 4. 创建新节点 ===
        if should_create_new:
            # logger.info("创建新的 Canonical 节点")
            canonical_id = self._create_new_knowledge_node(
                text=content,
                field_name=field_name,
                details=details,
                metadata=metadata,
                node_type=NodeType.CANONICAL_FAULT if knowledge_type == "fault" else NodeType.CANONICAL_SOLUTION
            )
            fusion_info = {
                "source": "new_create",
                "reason": "No match found",
                "similarity": 0.0
            }

        return canonical_id, fusion_info

    def get_document_images(self, document_id: str) -> List[str]:
        from service.system.file_reader_main import file_reader

        return file_reader.get_document_image_ids(document_id)
    
    def _create_new_knowledge_node(
        self,
        text: str,
        field_name: str,
        details: str,
        metadata: Optional[Dict[str, Any]] = None,
        node_type: NodeType = NodeType.RAW_FAULT
    ) -> str:
        """
        创建新的知识节点

        Args:
            text: 文本内容
            field_name: 字段名称
            details: 明细信息
            metadata: 元数据
            knowledge_level: 知识级别

        Returns:
            str: 创建的节点ID
        """
        # 生成UUID
        node_id = str(uuid.uuid4())

        # 生成嵌入向量
        embedding = self.generate_embeddings([text])[0]

        # 准备元数据
        meta = {
            "字段名": field_name,
            "明细": details,
            "知识级别": node_type.value,
            "is_deleted": False
        }

        # 如果有额外metadata,合并进去（clamping_force/锁模力 统一为 int）
        if metadata:
            for k, v in metadata.items():
                if k in ("clamping_force", "锁模力"):
                    normalized = _normalize_clamping_force(v)
                    meta["clamping_force"] = normalized if normalized is not None else ""
                else:
                    meta[k] = str(v) if v is not None else ""

        # === 1. 存入 Milvus Buffer (不再立即 insert) ===
        vector_data = {
            'id': node_id,
            'vector': embedding.tolist(),
            'metadata': meta
        }
        self._vector_buffer.append(vector_data)
        
        # === 2. 如果是 Canonical 节点，同时存入本地缓存 ===
        # 使用 NodeType 枚举值判断
        if node_type in [NodeType.CANONICAL_FAULT, NodeType.CANONICAL_SOLUTION]:
            self._canonical_cache.append({
                'id': node_id,
                'vector': embedding, # 存 numpy 数组方便计算
                'metadata': meta,
                'content': text
            })

        # === 3. 立即创建 Neo4j 节点 (因为后续立即需要建立关系，且 Neo4j 写入较快) ===
        try:
            from database.graph.model import KnowledgeNode

            # 创建Neo4j节点
            graph_node = KnowledgeNode(
                id=node_id,
                type=node_type,
                name=field_name,
                description=text,
                properties=meta
            )

            created_node = self.workorder_graph_repository.create_node(graph_node)
            if created_node:
                logger.info(f"在Neo4j中创建图谱节点成功: {node_id}, 类型: {node_type.value}")
            else:
                logger.warning(f"在Neo4j中创建图谱节点失败: {node_id}")

        except Exception as e:
            logger.error(f"在Neo4j中创建图谱节点时出错: {str(e)}")
            # 不抛出异常，因为向量数据已经成功插入

        # === 4. 保存到 MongoDB (用于文本内容检索) ===
        try:
            self.document_repository.save_document(
                content=text,
                document_id=node_id,
                collection_name=self.collection_name,
                metadata=meta
            )
            logger.debug(f"在MongoDB中创建文档成功: {node_id}")
        except Exception as e:
            logger.error(f"在MongoDB中创建文档失败: {str(e)}")
            # 不抛出异常，允许继续执行

        # === 5. 保存到 ES (用于混合检索关键词验证) ===
        try:
            from database.search.model import DocumentModel as ESDocumentModel
            es_doc = ESDocumentModel(
                content=text,
                collection=self.collection_name,
                metadata=meta
            )
            self.es_document_repository.create(es_doc, doc_id=node_id)
        except Exception as es_err:
            logger.warning(
                "工单诊断模式写入ES失败，跳过: document_id=%s, error=%s",
                node_id, es_err
            )

        logger.info(f"创建新节点: {node_id}, 级别: {node_type.value}")
        return node_id

    def _llm_judge_similarity(
        self,
        query_text: str,
        existing_text: str,
        similarity: float
    ) -> tuple[bool, str]:
        """
        使用LLM判断两个故障描述是否属于同一故障

        Args:
            query_text: 新的故障描述
            existing_text: 已存在的故障描述
            similarity: 向量相似度分数

        Returns:
            bool: True表示应该融合,False表示不融合
        """
        # 使用集中的提示词管理
        judge_prompt = WorkorderHandlerPrompts.get_llm_similarity_judge_prompt(
            query_text=query_text,
            existing_text=existing_text,
            similarity=similarity
        )

        messages = [
            {"role": "system", "content": judge_prompt},
            {"role": "user", "content": "请判断这两个故障是否属于同一故障"}
        ]

        try:
            response = openai_service.chat_completion(
                messages=messages,
                temperature=0.1,
                response_format={"type": "json_object"}
            )

            result = json.loads(response.choices[0].message.content)
            is_same_fault = result.get("is_same_fault", False)
            reason = result.get("reason", "LLM未提供理由")

            logger.info(f"LLM判断结果: {is_same_fault}, 理由: {reason}")
            return is_same_fault, reason

        except Exception as e:
            logger.error(f"LLM判断失败: {str(e)}")
            # 降级策略: 当相似度>=0.85时默认融合
            return similarity >= 0.85, f"LLM调用失败({str(e)})，根据相似度{similarity:.3f}自动判定"

    def _merge_canonical_knowledge(
        self,
        canonical_id: str,
        new_metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        融合新案例到Canonical节点

        Args:
            canonical_id: Canonical节点ID
            new_metadata: 新案例的元数据

        Returns:
            Dict[str, Any]: 融合后的元数据
        """
        from datetime import datetime

        try:
            existing_metadata = None
            existing_vector = None # 需要获取向量以便重新 Upsert
            is_in_cache = False
            
            # 获取现有Canonical的元数据
            if self._canonical_cache:
                for item in self._canonical_cache:
                    if item['id'] == canonical_id:
                        existing_metadata = item['metadata']
                        existing_vector = item['vector'] 
                        is_in_cache = True
                        break
            if not is_in_cache:
                vector_results = self.milvus_client.get_by_ids(
                    collection_name=self.collection_name,
                    ids=[canonical_id]
                )
                if vector_results:
                    result = vector_results[0]
                    existing_metadata = result.get("metadata", {})
                    existing_vector = result.get("vector") # [新增] 获取向量数据

            # 校验
            if not existing_metadata:
                # 只有当既不在缓存也不在库里时才报错
                raise ValueError(f"Canonical节点不存在: {canonical_id}")

            # 融合元数据
            merged = existing_metadata.copy()

            # 特殊处理机型列表(取并集)
            existing_models = set()
            if "机型" in existing_metadata and existing_metadata["机型"]:
                existing_models = set(str(existing_metadata["机型"]).split(","))

            new_models = set()
            if "机型" in new_metadata and new_metadata["机型"]:
                new_models = set(str(new_metadata["机型"]).split(","))

            all_models = existing_models | new_models
            if all_models:
                merged["机型"] = ",".join(sorted(all_models))
                merged["applicable_models"] = list(sorted(all_models))

            # 记录融合次数
            merge_count = int(merged.get("merge_count", 0)) + 1
            merged["merge_count"] = merge_count
            merged["last_merge_time"] = datetime.now().isoformat()

            # A. 更新 Neo4j 节点 (保持原样)
            canonical_node = None
            try:
                canonical_node = self.workorder_graph_repository.get_node_by_id(canonical_id)
                if canonical_node:
                    from database.graph.model import KnowledgeNode
                    updated_node = KnowledgeNode(
                        id=canonical_id,
                        type=NodeType(canonical_node["type"]), # 修正：确保类型转换
                        name=canonical_node.get("name", ""),
                        description=canonical_node.get("description", ""),
                        properties=merged
                    )
                    self.workorder_graph_repository.update_node(updated_node)
            except Exception as e:
                logger.warning(f"更新 Neo4j 节点属性失败: {e}")

            # B. 更新向量数据 (新增：区分缓存和数据库)
            if is_in_cache:
                # 如果在缓存中，直接更新缓存对象 (这样稍后 Flush 时就是最新的)
                # 更新 Canonical 缓存
                for item in self._canonical_cache:
                    if item['id'] == canonical_id:
                        item['metadata'] = merged
                        break
                # 同步更新待插入队列 (Vector Buffer)
                for item in self._vector_buffer:
                    if item['id'] == canonical_id:
                        item['metadata'] = merged
                        break
            else:
                # 1. 放入 Canonical 缓存 (方便本批次后续数据再次匹配)
                self._canonical_cache.append({
                    'id': canonical_id,
                    'vector': existing_vector, # 需要 numpy array 还是 list 取决于你的 calc 逻辑，建议保持一致
                    'metadata': merged,
                    'content': existing_metadata.get('content', '') # 尽可能保留 content
                })
                
                # 2. 放入 Vector Buffer (等待批量 Upsert)
                # 注意：Milvus接收 list 类型的 vector
                vector_list = existing_vector
                if hasattr(existing_vector, 'tolist'):
                    vector_list = existing_vector.tolist()

                self._vector_buffer.append({
                    'id': canonical_id,
                    'vector': vector_list,
                    'metadata': merged
                })
                logger.debug(f"DB Load & Update Buffer: {canonical_id}")

            # C. 更新 MongoDB 文档 (新增：保持三库同步)
            try:
                # 获取 MongoDB 中的原文档
                docs = self.get_documents(document_id=canonical_id)

                if docs:
                    # 如果文档存在，使用 update_document 更新 metadata
                    self.document_repository.update_document(
                        document_id=canonical_id,
                        collection_name=self.collection_name,
                        metadata=merged
                    )
                    logger.debug(f"在MongoDB中更新文档成功: {canonical_id}")
                else:
                    # 如果文档不存在（可能是历史数据），创建新文档
                    # 尝试从 Neo4j 获取 description 作为 content
                    content = existing_metadata.get('content', '')
                    if not content and canonical_node:
                        content = canonical_node.get('description', '')

                    self.document_repository.save_document(
                        content=content,
                        document_id=canonical_id,
                        collection_name=self.collection_name,
                        metadata=merged
                    )
                    logger.debug(f"在MongoDB中创建新文档成功: {canonical_id}")
            except Exception as e:
                logger.error(f"在MongoDB中更新文档失败: {str(e)}")
                # 不抛出异常，允许继续执行

            logger.info(f"成功融合到Canonical {canonical_id}, 融合次数: {merge_count}")
            return merged

        except Exception as e:
            logger.error(f"融合Canonical知识失败: {str(e)}")
            raise

