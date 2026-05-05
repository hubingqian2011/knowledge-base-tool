from typing import Dict, Any, List, Type, TypeVar, Generic, Optional
from util.logging.logger import get_logger
from .elasticsearch_client import ElasticsearchClient
from .model import BaseModel, DocumentModel, ModelManager
from service.auth.knowledge_permission_utils import pop_permission_filter
from shared.schema.metadata_normalizer import (
    SEARCH_ALIAS_GROUPS_KEY,
    normalize_filter_for_search,
)

logger = get_logger(__name__)

T = TypeVar('T', bound=BaseModel)

# 全局 ModelManager 实例
_model_manager = ModelManager()


def _build_metadata_filter_clauses(collection_name: str, search_metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not search_metadata:
        return []

    normalized_filter = normalize_filter_for_search(collection_name, search_metadata)
    alias_groups = normalized_filter.pop(SEARCH_ALIAS_GROUPS_KEY, {})
    filter_clauses: List[Dict[str, Any]] = []

    for key, value in normalized_filter.items():
        candidate_keys = [key] + list(alias_groups.get(key, []))
        candidate_keys = list(dict.fromkeys(candidate_keys))

        should_terms: List[Dict[str, Any]] = []
        for candidate_key in candidate_keys:
            field_name = f"metadata.{candidate_key}.keyword"
            if isinstance(value, (list, tuple, set)):
                values = [item for item in value if item not in (None, "")]
                if not values:
                    continue
                should_terms.append({"terms": {field_name: values}})
            else:
                should_terms.append({"term": {field_name: value}})

        if not should_terms:
            filter_clauses.append({"terms": {f"metadata.{key}.keyword": ["__never_match__"]}})
            continue

        filter_clauses.append(
            {
                "bool": {
                    "should": should_terms,
                    "minimum_should_match": 1,
                }
            }
        )

    return filter_clauses

class BaseRepository(Generic[T]):
    """Elasticsearch 文档基础仓库类"""
    
    def __init__(self, model_class: Type[T]):
        """
        初始化仓库实例
        
        Args:
            model_class: 模型类
        """
        self.model_class = model_class
    
    @property
    def es_client(self) -> ElasticsearchClient:
        """获取 Elasticsearch 客户端实例"""
        return _model_manager.es_client
    
    def create(self, model: T, doc_id: Optional[str] = None) -> bool:
        """
        创建文档

        Args:
            model: 模型实例
            doc_id: 文档ID（可选）

        Returns:
            bool: 是否创建成功
        """
        try:
            logger.info(f"开始创建文档，索引: {self.model_class.index_name}, 文档ID: {doc_id or '自动生成'}")
            result = self.es_client.index_document(
                self.model_class.index_name,
                model.to_dict(),
                doc_id
            )
            if result:
                logger.info(f"文档创建成功，索引: {self.model_class.index_name}, 文档ID: {doc_id or '自动生成'}")
            else:
                logger.error(f"文档创建失败，索引: {self.model_class.index_name}, 文档ID: {doc_id or '自动生成'}")
            return result
        except Exception as e:
            logger.error(f"创建文档时发生异常，索引: {self.model_class.index_name}, 文档ID: {doc_id or '自动生成'}, 错误: {str(e)}")
            return False
    
    def update(self, doc_id: str, model: T) -> bool:
        """
        更新文档

        Args:
            doc_id: 文档ID
            model: 模型实例

        Returns:
            bool: 是否更新成功
        """
        try:
            logger.info(f"开始更新文档，索引: {self.model_class.index_name}, 文档ID: {doc_id}")
            result = self.es_client.update_document(
                self.model_class.index_name,
                doc_id,
                model.to_dict()
            )
            if result:
                logger.info(f"文档更新成功，索引: {self.model_class.index_name}, 文档ID: {doc_id}")
            else:
                logger.error(f"文档更新失败，索引: {self.model_class.index_name}, 文档ID: {doc_id}")
            return result
        except Exception as e:
            logger.error(f"更新文档时发生异常，索引: {self.model_class.index_name}, 文档ID: {doc_id}, 错误: {str(e)}")
            return False
    
    def delete(self, doc_id: str) -> bool:
        """
        删除文档

        Args:
            doc_id: 文档ID

        Returns:
            bool: 是否删除成功
        """
        try:
            logger.info(f"开始删除文档，索引: {self.model_class.index_name}, 文档ID: {doc_id}")
            result = self.es_client.delete_document(
                self.model_class.index_name,
                doc_id
            )
            if result:
                logger.info(f"文档删除成功，索引: {self.model_class.index_name}, 文档ID: {doc_id}")
            else:
                logger.error(f"文档删除失败，索引: {self.model_class.index_name}, 文档ID: {doc_id}")
            return result
        except Exception as e:
            logger.error(f"删除文档时发生异常，索引: {self.model_class.index_name}, 文档ID: {doc_id}, 错误: {str(e)}")
            return False
    
    def search(self, query: Dict[str, Any], size: int = 10) -> List[T]:
        """
        搜索文档

        Args:
            query: 查询条件
            size: 返回结果数量

        Returns:
            List[T]: 模型实例列表
        """
        try:
            logger.info(f"开始搜索文档，索引: {self.model_class.index_name}, 查询大小: {size}")
            results = self.es_client.search(
                self.model_class.index_name,
                query,
                size
            )
            # 过滤掉已标记为删除的文档
            filtered_results = []
            for dict in results:
                doc = self.model_class.from_dict(dict["source"])
                # 检查文档是否标记为已删除
                if not doc.metadata.get("is_deleted", False):
                    filtered_results.append({"document": doc, "score": dict["score"]})
            logger.info(f"搜索完成，索引: {self.model_class.index_name}, 原始结果: {len(results)}, 过滤后结果: {len(filtered_results)}")
            return filtered_results
        except Exception as e:
            logger.error(f"搜索文档时发生异常，索引: {self.model_class.index_name}, 错误: {str(e)}")
            return []
    
    def get_by_id(self, doc_id: str) -> Optional[T]:
        """
        根据文档ID获取文档

        Args:
            doc_id: 文档ID

        Returns:
            Optional[T]: 文档模型实例，如果未找到则返回None
        """
        try:
            logger.info(f"根据ID获取文档，索引: {self.model_class.index_name}, 文档ID: {doc_id}")
            # 构造查询条件
            query = {
                "term": {
                    "_id": doc_id
                }
            }

            # 搜索文档
            results = self.search(query, 1)

            # 如果找到文档，返回第一个结果
            if results:
                logger.info(f"成功获取文档，索引: {self.model_class.index_name}, 文档ID: {doc_id}")
                return results[0]["document"]

            logger.warning(f"未找到文档，索引: {self.model_class.index_name}, 文档ID: {doc_id}")
            return None
        except Exception as e:
            logger.error(f"根据ID获取文档时发生异常，索引: {self.model_class.index_name}, 文档ID: {doc_id}, 错误: {str(e)}")
            return None

class ESDocumentRepository(BaseRepository[DocumentModel]):
    """文档仓库类"""
    
    def __init__(self):
        """初始化文档仓库"""
        super().__init__(DocumentModel)
    
    def search_by_collection(self, collection: str, size: int = 10) -> List[DocumentModel]:
        """
        按集合搜索文档

        Args:
            collection: 集合名称
            size: 返回结果数量

        Returns:
            List[DocumentModel]: 文档列表
        """
        try:
            logger.info(f"按集合搜索文档，集合: {collection}, 大小: {size}")
            # 构造查询条件，排除已删除的文档
            query = {
                "bool": {
                    "must": [
                        {"term": {"collection": collection}}
                    ],
                    "filter": [
                        {
                            "bool": {
                                "should": [
                                    {"bool": {"must_not": {"exists": {"field": "metadata.is_deleted"}}}},
                                    {"term": {"metadata.is_deleted": False}}
                                ]
                            }
                        }
                    ]
                }
            }
            results = self.search(query, size)
            logger.info(f"按集合搜索完成，集合: {collection}, 结果数量: {len(results)}")
            return results
        except Exception as e:
            logger.error(f"按集合搜索文档时发生异常，集合: {collection}, 错误: {str(e)}")
            return []
    
    def search_by_collection_and_content(self, collection: str, content: str, size: int = 10, metadata: Optional[dict] = None) -> List[DocumentModel]:
        """
        按集合、内容和metadata搜索文档

        Args:
            collection: 集合名称
            content: 搜索内容
            size: 返回结果数量
            metadata: 元数据过滤条件
        Returns:
            List[DocumentModel]: 文档列表
        """
        try:
            logger.info(f"按集合和内容搜索文档，集合: {collection}, 内容: {content[:100]}..., 大小: {size}")
            search_metadata, allowed_permission_levels, allow_legacy_permission = pop_permission_filter(metadata)
            must_clauses = [
                {"term": {"collection": collection}},
                {"match": {"content": content}}
            ]

            # 添加过滤条件，排除已删除的文档
            filter_clauses = [
                {
                    "bool": {
                        "should": [
                            {"bool": {"must_not": {"exists": {"field": "metadata.is_deleted"}}}},
                            {"term": {"metadata.is_deleted": False}}
                        ]
                    }
                }
            ]

            # 添加其他metadata过滤条件
            if search_metadata:
                user_metadata = {k: v for k, v in search_metadata.items() if k != "is_deleted"}
                filter_clauses.extend(_build_metadata_filter_clauses(collection, user_metadata))

            if allowed_permission_levels is not None:
                permission_should = []
                if allow_legacy_permission:
                    permission_should.append(
                        {"bool": {"must_not": {"exists": {"field": "metadata.permission_level"}}}}
                    )
                if allowed_permission_levels:
                    permission_should.append(
                        {"terms": {"metadata.permission_level": allowed_permission_levels}}
                    )
                if permission_should:
                    filter_clauses.append(
                        {
                            "bool": {
                                "should": permission_should,
                                "minimum_should_match": 1,
                            }
                        }
                    )
                else:
                    filter_clauses.append({"terms": {"metadata.permission_level": ["__never_match__"]}})

            query = {
                "bool": {
                    "must": must_clauses,
                    "filter": filter_clauses
                }
            }
            results = self.search(query, size)
            logger.info(f"按集合和内容搜索完成，集合: {collection}, 内容: {content[:100]}..., 结果数量: {len(results)}")
            return results
        except Exception as e:
            logger.error(f"按集合和内容搜索文档时发生异常，集合: {collection}, 错误: {str(e)}")
            return []

    def delete_by_collection(self, collection: str) -> bool:
        """
        删除指定集合中的所有文档

        Args:
            collection: 集合名称

        Returns:
            bool: 是否删除成功
        """
        try:
            logger.info(f"开始删除集合中的所有文档，集合: {collection}")
            # 构造删除查询
            query = {
                "query": {
                    "term": {
                        "collection": collection
                    }
                }
            }

            # 执行删除操作
            response = self.es_client.es.delete_by_query(
                index=self.model_class.index_name,
                body=query
            )

            deleted_count = response.get('deleted', 0)
            logger.info(f"成功删除集合 {collection} 中的 {deleted_count} 个文档")
            return True
        except Exception as e:
            logger.error(f"删除集合 {collection} 中的文档失败: {str(e)}")
            return False
    
    def soft_delete_documents(self, document_ids: List[str]) -> int:
        """
        软删除指定ID的文档（标记为已删除而不是物理删除）

        Args:
            document_ids: 要删除的文档ID列表

        Returns:
            int: 成功标记为删除的文档数量
        """
        try:
            logger.info(f"开始软删除文档，文档数量: {len(document_ids)}")
            deleted_count = 0
            for doc_id in document_ids:
                try:
                    es_doc = self.get_by_id(doc_id=doc_id)
                    if es_doc:
                        # 更新元数据，添加is_deleted标记
                        if not es_doc.metadata:
                            es_doc.metadata = {}
                        es_doc.metadata["is_deleted"] = True
                        if self.update(doc_id, es_doc):
                            deleted_count += 1
                    else:
                        logger.warning(f"在Elasticsearch中未找到文档 {doc_id}")
                except Exception as e:
                    logger.error(f"在Elasticsearch中标记文档 {doc_id} 为已删除失败: {str(e)}")
            logger.info(f"软删除文档完成，成功删除: {deleted_count}/{len(document_ids)}")
            return deleted_count
        except Exception as e:
            logger.error(f"在Elasticsearch中软删除文档失败: {str(e)}")
            return 0
