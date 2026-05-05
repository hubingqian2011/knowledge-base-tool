from pymilvus import (
    connections,
    Collection,
    CollectionSchema,
    FieldSchema,
    DataType,
    utility
)
from typing import List, Dict, Any, Optional, Union
from util.logging.logger import get_logger
import numpy as np
import time
from config.config import MILVUS_HOST, MILVUS_PORT, MILVUS_USER, MILVUS_PASSWORD
from .field_mapper import FieldMapper
from shared.schema.metadata_normalizer import (
    SEARCH_ALIAS_GROUPS_KEY,
    normalize_filter_for_search,
    normalize_metadata_for_ingest,
)

class MilvusClient:
    """Milvus 客户端封装类，同一 host:port 复用单例"""

    _instances: Dict[str, "MilvusClient"] = {}

    def __new__(cls, host: str = MILVUS_HOST, port: str = MILVUS_PORT, username: str = MILVUS_USER, passowrd: str = MILVUS_PASSWORD, **kwargs):
        key = f"{host}:{port}:{username}"
        if key not in cls._instances:
            cls._instances[key] = super().__new__(cls)
        return cls._instances[key]

    def __init__(self, host: str = MILVUS_HOST, port: str = MILVUS_PORT, username: str = MILVUS_USER, passowrd: str = MILVUS_PASSWORD):
        """
        初始化 Milvus 客户端（同一 host:port 仅连接一次）
        """
        self.logger = get_logger(__name__)
        self.host = host
        self.port = port
        self.username = username
        self.password = passowrd
        self.alias = self._build_alias(host=host, port=port, username=username)
        if getattr(self, "_initialized", False):
            self._ensure_connection()
            return
        self._connected = False
        self._initialized = True
        self._connect()

    @staticmethod
    def _build_alias(host: str, port: str, username: str) -> str:
        alias = f"milvus_{host}_{port}_{username}"
        return alias.replace(".", "_").replace("-", "_").replace(":", "_")

    def _connect(self, force: bool = False) -> None:
        if force and connections.has_connection(self.alias):
            connections.disconnect(self.alias)
        connections.connect(
            alias=self.alias,
            host=self.host,
            port=self.port,
            user=self.username,
            password=self.password,
        )
        self._connected = True

    def create_collection(
        self,
        collection_name: str,
        dimension: int,
        index_type: str = "IVF_FLAT",
        metric_type: str = "IP",
        index_params: Optional[Dict[str, Any]] = None,
        auto_id: bool = False
    ) -> bool:
        """
        创建集合

        Args:
            collection_name: 集合名称
            dimension: 向量维度
            index_type: 索引类型
            metric_type: 距离度量方式
            index_params: 索引参数
            auto_id: 是否自动生成ID（注意：当使用字符串ID时，此参数无效）

        Returns:
            bool: 是否创建成功
        """
        try:
            self._ensure_connection()
            if utility.has_collection(collection_name, using=self.alias):
                #self.logger.warning(f"集合 {collection_name} 已存在")
                return False

            fields = [
                FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, auto_id=auto_id, max_length=36),
                FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dimension),
                FieldSchema(name="metadata", dtype=DataType.JSON)
            ]

            schema = CollectionSchema(fields=fields, description=f"{collection_name} collection")
            collection = Collection(name=collection_name, schema=schema, using=self.alias)

            # 创建索引
            index_params = index_params or {"nlist": 1024}
            collection.create_index(
                field_name="vector",
                index_params={
                    "index_type": index_type,
                    "metric_type": metric_type,
                    "params": index_params
                }
            )

            #self.logger.info(f"集合 {collection_name} 创建成功")
            return True
        except Exception as e:
            #self.logger.error(f"创建集合失败: {str(e)}")
            return False

    def drop_collection(self, collection_name: str) -> bool:
        """
        删除集合

        Args:
            collection_name: 集合名称

        Returns:
            bool: 是否删除成功
        """
        try:
            self._ensure_connection()
            if utility.has_collection(collection_name, using=self.alias):
                utility.drop_collection(collection_name, using=self.alias)
                #self.logger.info(f"集合 {collection_name} 删除成功")
                return True
            return False
        except Exception as e:
            #self.logger.error(f"删除集合失败: {str(e)}")
            return False

    def insert_vectors(
        self,
        collection_name: str,
        vectors: List[List[float]],
        metadata_list: Optional[List[Dict[str, Any]]] = None,
        ids: Optional[List[str]] = None
    ) -> List[str]:
        """
        插入向量数据，支持中文metadata key输入，内部自动转换为英文

        Args:
            collection_name: 集合名称
            vectors: 向量列表
            metadata_list: 元数据列表，支持中文字段名
            ids: 向量ID列表，如果提供则使用指定的ID

        Returns:
            List[str]: 插入向量的 ID 列表
        """
        start_time = time.time()
        try:
            self._ensure_connection()
            collection = Collection(collection_name, using=self.alias)
            collection.load()

            if metadata_list is None:
                metadata_list = [{} for _ in range(len(vectors))]

            # 处理元数据，将中文字段名转换为英文
            processed_metadata_list = []
            for metadata in metadata_list:
                # 检查是否包含中文字段名
                normalized_metadata = normalize_metadata_for_ingest(
                    collection_name,
                    metadata or {},
                    include_alias_keys=False,
                    strict_conflicts=True,
                    require_collection_type=True,
                )
                if FieldMapper.contains_chinese_fields(normalized_metadata):
                    # 转换中文字段名为英文
                    processed_metadata = FieldMapper.map_dict_keys(normalized_metadata, "chinese_to_english")
                    processed_metadata_list.append(processed_metadata)
                else:
                    processed_metadata_list.append(normalized_metadata)

            entities = []
            if ids is not None:
                if len(ids) != len(vectors):
                    raise ValueError("IDs列表长度必须与向量列表长度相同")
                entities = [ids, vectors, processed_metadata_list]
            else:
                entities = [vectors, processed_metadata_list]

            insert_result = collection.insert(entities)
            collection.flush()
            end_time = time.time()
            elapsed_time = end_time - start_time
            #self.logger.debug(f"向量数据插入成功，数量: {len(vectors)}，耗时: {elapsed_time:.2f}秒")
            return insert_result.primary_keys
        except Exception as e:
            end_time = time.time()
            elapsed_time = end_time - start_time
            self.logger.error(
                "insert_vectors failed: collection=%s count=%s elapsed=%.2fs error=%s",
                collection_name,
                len(vectors),
                elapsed_time,
                str(e),
            )
            raise

    def flush(self, collection_name: str) -> bool:
        """
        将内存中的数据刷新到磁盘

        Args:
            collection_name: 集合名称

        Returns:
            bool: 是否刷新成功
        """
        try:
            self._ensure_connection()
            collection = Collection(collection_name, using=self.alias)
            collection.flush()
            #self.logger.debug(f"集合 {collection_name} 数据刷新成功")
            return True
        except Exception as e:
            #self.logger.error(f"刷新数据失败: {str(e)}")
            return False

    def search_vectors(
        self,
        collection_name: str,
        query_vectors: List[List[float]],
        top_k: int = 10,
        search_params: Optional[Dict[str, Any]] = None,
        metadata_filter: Optional[Dict[str, Any]] = None,
        excluded_workorder_numbers: Optional[List[str]] = None
    ) -> List[List[Dict[str, Any]]]:
        """
        搜索向量，支持metadata过滤，支持中文字段名过滤条件，支持按派工单号排除

        Args:
            collection_name: 集合名称
            query_vectors: 查询向量列表
            top_k: 返回结果数量
            search_params: 搜索参数
            metadata_filter: 元数据过滤条件，支持中文字段名
            excluded_workorder_numbers: 要排除的派工单号列表，在检索时即不返回这些工单（保证 top_k 数量）

        Returns:
            List[List[Dict[str, Any]]]: 搜索结果列表，字段名为中文
        """
        try:
            self._ensure_connection()
            collection = Collection(collection_name, using=self.alias)
            collection.load()
            search_params = search_params or {"nprobe": 10}

            # 处理过滤条件，将中文字段名转换为英文
            expr_list = []
            if metadata_filter:
                normalized_filter = normalize_filter_for_search(collection_name, metadata_filter)
                alias_groups = normalized_filter.pop(SEARCH_ALIAS_GROUPS_KEY, {})
                processed_filter = {}
                for k, v in normalized_filter.items():
                    if k in FieldMapper.get_mapping_dict():
                        processed_filter[FieldMapper.chinese_to_english(k)] = v
                    else:
                        processed_filter[k] = v
                for k, v in processed_filter.items():
                    candidate_keys = [k]
                    for alias in alias_groups.get(k, []):
                        if alias in FieldMapper.get_mapping_dict():
                            candidate_keys.append(alias)
                            candidate_keys.append(FieldMapper.chinese_to_english(alias))
                        else:
                            candidate_keys.append(alias)
                    unique_candidate_keys = list(dict.fromkeys(candidate_keys))

                    if isinstance(v, (list, tuple, set)):
                        values = list(v)
                        if not values:
                            expr_list.append("1 == 0")
                            continue
                        if all(isinstance(item, str) for item in values):
                            escaped_values = []
                            for item in values:
                                escaped_item = str(item).replace("\\", "\\\\").replace("'", "\\'")
                                escaped_values.append(f"'{escaped_item}'")
                            formatted_values = ", ".join(escaped_values)
                        else:
                            formatted_values = ", ".join(str(item) for item in values)
                        field_expr = " or ".join(
                            f"metadata['{candidate_key}'] in [{formatted_values}]"
                            for candidate_key in unique_candidate_keys
                        )
                        expr_list.append(f"({field_expr})")
                    elif isinstance(v, str):
                        safe_value = v.replace("\\", "\\\\").replace("'", "\\'")
                        field_expr = " or ".join(
                            f"metadata['{candidate_key}'] == '{safe_value}'"
                            for candidate_key in unique_candidate_keys
                        )
                        expr_list.append(f"({field_expr})")
                    else:
                        field_expr = " or ".join(
                            f"metadata['{candidate_key}'] == {v}"
                            for candidate_key in unique_candidate_keys
                        )
                        expr_list.append(f"({field_expr})")

            # 排除指定派工单号：在检索时用 expr 排除，避免先搜再删导致条数不足
            if excluded_workorder_numbers:
                for w in excluded_workorder_numbers:
                    if w is not None and str(w).strip():
                        # Milvus 中派工单号字段为 work_order_number
                        safe_val = str(w).replace("\\", "\\\\").replace("'", "\\'")
                        expr_list.append(f"metadata['work_order_number'] != '{safe_val}'")

            expr = " and ".join(expr_list) if expr_list else None
            results = collection.search(
                data=query_vectors,
                anns_field="vector",
                param=search_params,
                limit=top_k,
                output_fields=["metadata"],
                expr=expr
            )

            # NOTE: 主检索链路(similarity_search)实际使用 Mongo 返回的 metadata，
            # 此处的中文转换仅影响直接调用 search_vectors 的场景。
            formatted_results = []
            for hits in results:
                hit_list = []
                for hit in hits:
                    metadata = hit.entity.get("metadata", {})
                    chinese_metadata = FieldMapper.map_dict_keys(metadata, "english_to_chinese")

                    hit_list.append({
                        "id": hit.id,
                        "distance": hit.distance,
                        "metadata": chinese_metadata
                    })
                formatted_results.append(hit_list)
            return formatted_results
        except Exception as e:
            self.logger.warning(
                f"搜索向量失败（collection 不存在，已跳过）: collection={collection_name}, top_k={top_k}, "
                f"filter_keys={list((metadata_filter or {{}}).keys())}, error={e}"
            )
            return []

    def get_collection_stats(self, collection_name: str) -> Dict[str, Any]:
        """
        获取集合统计信息

        Args:
            collection_name: 集合名称

        Returns:
            Dict[str, Any]: 统计信息
        """
        try:
            self._ensure_connection()
            collection = Collection(collection_name, using=self.alias)
            collection.flush()
            return {
                "row_count": collection.num_entities,
            }
        except Exception as e:
            #self.logger.error(f"获取集合统计信息失败: {str(e)}")
            return {}

    def get_by_ids(self, collection_name: str, ids: List[str]) -> List[Dict[str, Any]]:
        """
        根据ID列表获取向量数据和metadata

        Args:
            collection_name: 集合名称
            ids: 要查询的ID列表

        Returns:
            List[Dict[str, Any]]: 查询结果列表，每个结果包含id、vector和metadata
        """
        try:
            self._ensure_connection()
            collection = Collection(collection_name, using=self.alias)
            collection.load()

            results = []
            for doc_id in ids:
                # 使用query方法通过ID获取数据
                query_results = collection.query(
                    expr=f"id == '{doc_id}'",
                    output_fields=["vector", "metadata"]
                )

                if query_results:
                    result = query_results[0]
                    # 将英文字段名转换为中文
                    metadata = result.get("metadata", {})
                    chinese_metadata = FieldMapper.map_dict_keys(metadata, "english_to_chinese")

                    results.append({
                        "id": result.get("id"),
                        "vector": result.get("vector"),
                        "metadata": chinese_metadata
                    })
                else:
                    pass
                    #self.logger.warning(f"未找到ID为 {doc_id} 的记录")

            #self.logger.info(f"成功获取 {len(results)} 条记录")
            return results

        except Exception as e:
            #self.logger.error(f"根据ID获取数据失败: {str(e)}")
            return []

    def upsert_vectors(
        self,
        collection_name: str,
        vectors: List[List[float]],
        metadata_list: Optional[List[Dict[str, Any]]] = None,
        ids: Optional[List[str]] = None
    ) -> List[str]:
        """
        使用 upsert 方式更新或插入向量数据
        如果ID已存在则更新，否则插入新记录
        支持中文metadata key输入，内部自动转换为英文

        Args:
            collection_name: 集合名称
            vectors: 向量列表
            metadata_list: 元数据列表，支持中文字段名
            ids: 向量ID列表，如果提供则使用指定的ID

        Returns:
            List[str]: 操作的向量 ID 列表
        """
        start_time = time.time()
        try:
            self._ensure_connection()
            collection = Collection(collection_name, using=self.alias)
            collection.load()

            if metadata_list is None:
                metadata_list = [{} for _ in range(len(vectors))]

            # 处理元数据，将中文字段名转换为英文
            processed_metadata_list = []
            for metadata in metadata_list:
                # 检查是否包含中文字段名
                normalized_metadata = normalize_metadata_for_ingest(
                    collection_name,
                    metadata or {},
                    include_alias_keys=False,
                    strict_conflicts=True,
                    require_collection_type=True,
                )
                if FieldMapper.contains_chinese_fields(normalized_metadata):
                    # 转换中文字段名为英文
                    processed_metadata = FieldMapper.map_dict_keys(normalized_metadata, "chinese_to_english")
                    processed_metadata_list.append(processed_metadata)
                else:
                    processed_metadata_list.append(normalized_metadata)

            # 构造实体数据（使用列表格式）
            if ids is not None:
                if len(ids) != len(vectors):
                    raise ValueError("IDs列表长度必须与向量列表长度相同")
                entities = [ids, vectors, processed_metadata_list]
            else:
                entities = [vectors, processed_metadata_list]

            # 使用 upsert 方法
            upsert_result = collection.upsert(entities)
            collection.flush()

            end_time = time.time()
            elapsed_time = end_time - start_time
            #self.logger.info(f"向量数据 upsert 成功，数量: {len(vectors)}，耗时: {elapsed_time:.2f}秒")
            return upsert_result.primary_keys
        except Exception as e:
            end_time = time.time()
            elapsed_time = end_time - start_time
            self.logger.error(
                f"向量数据 upsert 失败: collection={collection_name}, count={len(vectors)}, elapsed={elapsed_time:.2f}s, error={str(e)}"
            )
            raise

    def _ensure_connection(self) -> None:
        if not connections.has_connection(self.alias):
            self._connect()
            return
        try:
            utility.get_server_version(using=self.alias, timeout=2)
        except Exception:
            self._connect(force=True)

    def update_metadata_by_id(
        self,
        collection_name: str,
        id: str,
        metadata: Dict[str, Any]
    ) -> bool:
        """
        根据ID更新元数据，支持中文字段名

        Args:
            collection_name: 集合名称
            id: 向量ID
            metadata: 新的元数据，支持中文字段名

        Returns:
            bool: 是否更新成功
        """
        try:
            self._ensure_connection()
            collection = Collection(collection_name, using=self.alias)
            collection.load()

            # 处理元数据，将中文字段名转换为英文
            normalized_metadata = normalize_metadata_for_ingest(
                collection_name,
                metadata or {},
                include_alias_keys=False,
                strict_conflicts=True,
                require_collection_type=True,
            )
            processed_metadata = normalized_metadata
            if FieldMapper.contains_chinese_fields(normalized_metadata):
                # 转换中文字段名为英文
                processed_metadata = FieldMapper.map_dict_keys(normalized_metadata, "chinese_to_english")

            results = collection.query(
                expr=f"id == '{id}'",
                output_fields=["vector"]
            )

            if not results:
                return False

            vector = results[0]["vector"]
            entities = [[id], [vector], [processed_metadata]]

            collection.upsert(entities)
            collection.flush()

            return True
        except Exception as e:
            self.logger.error(f"update_metadata_by_id failed: collection={collection_name}, id={id}, error={str(e)}")
            raise
    def delete_vectors(self, collection_name: str, ids: List[str]) -> bool:
        """
        物理删除指定ID的向量数据，使用单条删除策略

        Args:
            collection_name: 集合名称
            ids: 要删除的向量ID列表

        Returns:
            bool: 是否删除成功
        """
        try:
            self._ensure_connection()
            if not utility.has_collection(collection_name, using=self.alias):
                #self.logger.warning(f"集合 {collection_name} 不存在")
                return False

            collection = Collection(collection_name, using=self.alias)
            collection.load()

            #self.logger.info(f"开始删除向量，数量: {len(ids)}")

            # 批量删除所有向量，最后统一flush
            for doc_id in ids:
                try:
                    expr = f'id == "{doc_id}"'
                    collection.delete(expr)
                except Exception as e:
                    #self.logger.error(f"删除ID {doc_id} 失败: {str(e)}")
                    continue

            # 所有删除完成后统一flush一次
            collection.flush()

            #self.logger.info(f"删除向量完成")
            return True

        except Exception as e:
            #self.logger.error(f"删除向量失败: {str(e)}")
            return False

    def soft_delete_by_ids(self, collection_name: str, ids: List[str]) -> int:
        """
        软删除指定ID的向量数据（标记为已删除而不是物理删除）

        Args:
            collection_name: 集合名称
            ids: 要删除的向量ID列表

        Returns:
            int: 成功标记为删除的向量数量
        """
        try:
            self._ensure_connection()
            collection = Collection(collection_name, using=self.alias)
            collection.load()

            deleted_count = 0
            # 逐个更新文档的元数据
            for doc_id in ids:
                try:
                    # 查询该ID对应的向量数据
                    results = collection.query(
                        expr=f"id == '{doc_id}'",
                        output_fields=["vector", "metadata"]
                    )

                    if results:
                        # 获取向量数据和元数据
                        vector = results[0]["vector"]
                        metadata = results[0].get("metadata", {})

                        # 更新元数据，添加is_deleted标记
                        metadata["is_deleted"] = True

                        # 使用 upsert 进行更新
                        entities = [[doc_id], [vector], [metadata]]
                        collection.upsert(entities)
                        deleted_count += 1
                    else:
                        self.logger.warning(
                            f"在Milvus中未找到待软删除文档: collection={collection_name}, id={doc_id}"
                        )
                except Exception as e:
                    self.logger.error(
                        f"在Milvus中标记文档为已删除失败: collection={collection_name}, id={doc_id}, error={str(e)}",
                        exc_info=True,
                    )

            collection.flush()
            self.logger.info(f"成功在集合 {collection_name} 中软删除 {deleted_count}/{len(ids)} 个向量")
            return deleted_count
        except Exception as e:
            self.logger.error(f"在集合 {collection_name} 中软删除向量失败: {str(e)}", exc_info=True)
            return 0
