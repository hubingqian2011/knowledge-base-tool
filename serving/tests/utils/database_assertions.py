# -*- coding: utf-8 -*-
"""
数据库断言工具
提供三库一致性检查的断言方法
"""
from typing import List, Dict, Any, Optional
from loguru import logger


class DatabaseAssertions:
    """数据库断言工具类"""

    def __init__(self, milvus_client, document_repository, graph_repository):
        """
        初始化断言工具

        Args:
            milvus_client: Milvus 客户端
            document_repository: MongoDB 文档仓储
            graph_repository: Neo4j 图仓储
        """
        self.milvus_client = milvus_client
        self.document_repository = document_repository
        self.graph_repository = graph_repository

    def assert_node_exists_in_all_databases(
        self,
        node_id: str,
        collection_name: str
    ) -> Dict[str, bool]:
        """
        断言节点在三个数据库中都存在

        Args:
            node_id: 节点ID
            collection_name: 集合名称

        Returns:
            Dict[str, bool]: 各数据库的存在状态
        """
        results = {
            "milvus": False,
            "mongodb": False,
            "neo4j": False
        }

        # 检查 Milvus
        try:
            milvus_results = self.milvus_client.get_by_ids(
                collection_name=collection_name,
                ids=[node_id]
            )
            results["milvus"] = len(milvus_results) > 0
        except Exception as e:
            logger.error(f"检查 Milvus 失败: {e}")

        # 检查 MongoDB
        try:
            mongo_docs = self.document_repository.get_documents(
                document_id=node_id,
                collection_name=collection_name
            )
            results["mongodb"] = len(mongo_docs) > 0
        except Exception as e:
            logger.error(f"检查 MongoDB 失败: {e}")

        # 检查 Neo4j
        try:
            neo4j_node = self.graph_repository.get_node_by_id(node_id)
            results["neo4j"] = neo4j_node is not None
        except Exception as e:
            logger.error(f"检查 Neo4j 失败: {e}")

        return results

    def assert_metadata_consistency(
        self,
        node_id: str,
        collection_name: str,
        expected_metadata: Dict[str, Any],
        ignore_fields: Optional[List[str]] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        断言三个数据库中的 metadata 一致

        Args:
            node_id: 节点ID
            collection_name: 集合名称
            expected_metadata: 期望的 metadata
            ignore_fields: 忽略的字段列表（如 content、时间戳等）

        Returns:
            Dict[str, Dict[str, Any]]: 各数据库的实际 metadata
        """
        ignore_fields = ignore_fields or ["content", "last_merge_time"]

        milvus_metadata = None
        mongo_metadata = None
        neo4j_metadata = None

        # 获取 Milvus metadata
        try:
            milvus_results = self.milvus_client.get_by_ids(
                collection_name=collection_name,
                ids=[node_id]
            )
            if milvus_results:
                milvus_metadata = milvus_results[0].get("metadata", {})
        except Exception as e:
            logger.error(f"获取 Milvus metadata 失败: {e}")

        # 获取 MongoDB metadata
        try:
            mongo_docs = self.document_repository.get_documents(
                document_id=node_id,
                collection_name=collection_name
            )
            if mongo_docs:
                mongo_metadata = mongo_docs[0].get("metadata", {})
        except Exception as e:
            logger.error(f"获取 MongoDB metadata 失败: {e}")

        # 获取 Neo4j metadata（从 properties）
        try:
            neo4j_node = self.graph_repository.get_node_by_id(node_id)
            if neo4j_node:
                # Neo4j 节点的所有属性都在根级别，不需要 properties 字段
                neo4j_metadata = {k: v for k, v in neo4j_node.items()
                                 if k not in ['id', 'type', 'name', 'description']}
                logger.info(f"Neo4j 节点 metadata: {neo4j_metadata}")
        except Exception as e:
            logger.error(f"获取 Neo4j metadata 失败: {e}")

        # 比较关键字段
        actual_metadata = {
            "milvus": milvus_metadata,
            "mongodb": mongo_metadata,
            "neo4j": neo4j_metadata
        }

        # 过滤掉忽略的字段
        filtered_expected = {
            k: v for k, v in expected_metadata.items()
            if k not in ignore_fields
        }

        # 验证每个数据库的 metadata
        for db_name, metadata in actual_metadata.items():
            if metadata is None:
                raise AssertionError(f"{db_name} 中未找到节点 {node_id}")

            for key, expected_value in filtered_expected.items():
                actual_value = metadata.get(key)
                if actual_value != expected_value:
                    raise AssertionError(
                        f"{db_name} 中字段 '{key}' 不一致: "
                        f"期望={expected_value}, 实际={actual_value}"
                    )

        return actual_metadata

    def assert_node_deleted_from_all_databases(
        self,
        node_id: str,
        collection_name: str
    ) -> Dict[str, bool]:
        """
        断言节点已从三个数据库中删除

        Args:
            node_id: 节点ID
            collection_name: 集合名称

        Returns:
            Dict[str, bool]: 各数据库的删除状态
        """
        results = {
            "milvus": False,
            "mongodb": False,
            "neo4j": False
        }

        # 检查 Milvus（应该返回空结果或 is_deleted=True）
        try:
            milvus_results = self.milvus_client.get_by_ids(
                collection_name=collection_name,
                ids=[node_id]
            )
            if milvus_results:
                # 如果返回结果，检查 is_deleted 标记
                results["milvus"] = milvus_results[0].get("metadata", {}).get("is_deleted", True)
            else:
                results["milvus"] = True  # 不存在视为已删除
        except Exception as e:
            logger.error(f"检查 Milvus 失败: {e}")

        # 检查 MongoDB（应该返回空结果或 is_deleted=True）
        try:
            mongo_docs = self.document_repository.get_documents(
                document_id=node_id,
                collection_name=collection_name
            )
            if mongo_docs:
                results["mongodb"] = mongo_docs[0].get("metadata", {}).get("is_deleted", True)
            else:
                results["mongodb"] = True  # 不存在视为已删除
        except Exception as e:
            logger.error(f"检查 MongoDB 失败: {e}")

        # 检查 Neo4j（应该返回 None）
        try:
            neo4j_node = self.graph_repository.get_node_by_id(node_id)
            results["neo4j"] = neo4j_node is None
        except Exception as e:
            logger.error(f"检查 Neo4j 失败: {e}")

        return results

    def get_merged_field_values(
        self,
        node_id: str,
        collection_name: str,
        field_name: str
    ) -> Dict[str, Any]:
        """
        获取某个字段在三个数据库中的值

        Args:
            node_id: 节点ID
            collection_name: 集合名称
            field_name: 字段名

        Returns:
            Dict[str, Any]: 各数据库中该字段的值
        """
        values = {}

        # Milvus
        try:
            milvus_results = self.milvus_client.get_by_ids(
                collection_name=collection_name,
                ids=[node_id]
            )
            if milvus_results:
                values["milvus"] = milvus_results[0].get("metadata", {}).get(field_name)
        except Exception as e:
            logger.error(f"从 Milvus 获取 {field_name} 失败: {e}")

        # MongoDB
        try:
            mongo_docs = self.document_repository.get_documents(
                document_id=node_id,
                collection_name=collection_name
            )
            if mongo_docs:
                values["mongodb"] = mongo_docs[0].get("metadata", {}).get(field_name)
        except Exception as e:
            logger.error(f"从 MongoDB 获取 {field_name} 失败: {e}")

        # Neo4j
        try:
            neo4j_node = self.graph_repository.get_node_by_id(node_id)
            if neo4j_node:
                # Neo4j 节点的所有属性都在根级别
                values["neo4j"] = neo4j_node.get(field_name)
                logger.info(f"Neo4j 字段 {field_name}: {values['neo4j']}")
        except Exception as e:
            logger.error(f"从 Neo4j 获取 {field_name} 失败: {e}")

        return values
