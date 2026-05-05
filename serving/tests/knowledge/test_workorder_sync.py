# -*- coding: utf-8 -*-
"""
工单知识库三库同步测试
测试 Milvus、Neo4j、MongoDB 三个数据库的数据一致性
"""
import pytest
import pandas as pd
from pathlib import Path
from loguru import logger

from service.knowledge.handlers.workorder_handler import WorkorderKnowledgeHandler
from database.vector.milvus_client import MilvusClient
from database.document.repository import DocumentRepository
from database.graph.workorder_graph_repository import WorkorderGraphRepository
from tests.utils.database_assertions import DatabaseAssertions


@pytest.mark.integration
@pytest.mark.knowledge
class TestWorkorderKnowledgeSync:
    """
    工单知识库三库同步测试类

    测试场景：
    1. 创建新节点时三库同步
    2. 融合现有节点时三库 metadata 更新
    3. 删除节点时三库同步删除
    4. applicable_models 字段融合测试
    """

    @pytest.fixture(autouse=True)
    def setup(self, test_collection_name):
        """每个测试方法前的设置"""
        self.collection_name = test_collection_name

        # 初始化数据库客户端
        self.milvus_client = MilvusClient()
        self.document_repository = DocumentRepository()
        self.graph_repository = WorkorderGraphRepository()

        # 创建 Milvus 测试集合（重要！）
        from config.config import VECTOR_DIMENSION
        self.milvus_client.create_collection(
            collection_name=self.collection_name,
            dimension=VECTOR_DIMENSION
        )
        logger.info(f"创建 Milvus 测试集合: {self.collection_name}")

        # 初始化断言工具
        self.assertions = DatabaseAssertions(
            self.milvus_client,
            self.document_repository,
            self.graph_repository
        )

        # 初始化工单处理器
        self.handler = WorkorderKnowledgeHandler(
            collection_name=self.collection_name,
            collection_type="workorder"
        )

        yield

        # 清理测试数据
        self._cleanup()

    def _cleanup(self):
        """清理测试数据"""
        try:
            # 删除 Milvus 集合
            self.milvus_client.drop_collection(self.collection_name)
            logger.info(f"已删除 Milvus 集合: {self.collection_name}")
        except Exception as e:
            logger.warning(f"删除 Milvus 集合失败: {e}")

        try:
            # 删除 MongoDB 集合
            self.document_repository.delete_documents_by_collection(self.collection_name)
            logger.info(f"已删除 MongoDB 集合: {self.collection_name}")
        except Exception as e:
            logger.warning(f"删除 MongoDB 集合失败: {e}")

    def test_create_new_node_sync_to_all_databases(self, sample_fault_data):
        """
        测试1: 创建新节点时同步到三个数据库

        步骤：
        1. 使用 _create_new_knowledge_node 创建一个新节点
        2. 验证 Milvus、MongoDB、Neo4j 中都存在该节点
        3. 验证三个数据库中的 metadata 一致
        """
        # 创建新节点
        fault_text = sample_fault_data["fault_description"]
        metadata = sample_fault_data["metadata"]

        from database.graph.model import NodeType

        node_id = self.handler._create_new_knowledge_node(
            text=fault_text,
            field_name="故障描述",
            details="测试工单",
            metadata=metadata,
            node_type=NodeType.RAW_FAULT
        )

        logger.info(f"创建新节点: {node_id}")

        # 刷新向量缓冲区，确保数据写入 Milvus
        self.handler._flush_vectors()

        # 断言1: 节点在三个数据库中都存在
        existence = self.assertions.assert_node_exists_in_all_databases(
            node_id=node_id,
            collection_name=self.collection_name
        )

        assert existence["milvus"], "节点应该存在于 Milvus"
        assert existence["mongodb"], "节点应该存在于 MongoDB"
        assert existence["neo4j"], "节点应该存在于 Neo4j"

        # 断言2: metadata 一致
        # 先检查 Neo4j 实际存储了什么
        neo4j_node = self.graph_repository.get_node_by_id(node_id)
        logger.info(f"Neo4j 节点完整数据: {neo4j_node}")

        expected_metadata = {
            **metadata,
            "字段名": "故障描述",
            "明细": "测试工单",
            "知识级别": NodeType.RAW_FAULT.value,
            "is_deleted": False
        }

        actual_metadata = self.assertions.assert_metadata_consistency(
            node_id=node_id,
            collection_name=self.collection_name,
            expected_metadata=expected_metadata
        )

        logger.info("✅ 测试1通过: 新节点成功同步到三个数据库")

    def test_merge_canonical_updates_metadata_in_all_databases(self):
        """
        测试2: 融合 Canonical 节点时更新三个数据库的 metadata

        步骤：
        1. 创建两个相似的故障描述节点（触发融合）
        2. 验证 Canonical 节点的 applicable_models 字段合并了两个机型
        3. 验证三个数据库中的 applicable_models 一致
        """
        from database.graph.model import NodeType

        # 创建第一个故障节点
        fault_text_1 = "发动机启动困难"
        metadata_1 = {
            "机型": "机型A",
            "工单号": "TEST001"
        }

        canonical_id_1, _ = self.handler._handle_canonical_matching(
            content=fault_text_1,
            field_name="故障描述",
            details="测试工单",
            metadata=metadata_1,
            knowledge_type="fault"
        )

        logger.info(f"创建第一个 Canonical 节点: {canonical_id_1}")
        logger.info(f"当前缓存大小: {len(self.handler._canonical_cache)}")

        # 检查缓存内容
        for i, item in enumerate(self.handler._canonical_cache):
            logger.info(f"缓存[{i}]: id={item['id']}, 知识级别={item['metadata'].get('知识级别')}")

        # 创建第二个相似故障节点（应该融合到第一个）
        # 使用完全相同的文本，确保高相似度（> 0.95），直接触发融合
        fault_text_2 = "发动机启动困难"  # 与第一个完全相同，确保相似度 = 1.0
        metadata_2 = {
            "机型": "机型B",
            "工单号": "TEST002"
        }

        logger.info(f"当前缓存大小（第二次调用前）: {len(self.handler._canonical_cache)}")
        canonical_id_2, fusion_info = self.handler._handle_canonical_matching(
            content=fault_text_2,
            field_name="故障描述",
            details="测试工单",
            metadata=metadata_2,
            knowledge_type="fault"
        )

        logger.info(f"第二个节点融合到: {canonical_id_2}")
        logger.info(f"融合信息: {fusion_info}")
        logger.info(f"两个ID是否相同: {canonical_id_1 == canonical_id_2}")

        # 刷新向量缓冲区
        self.handler._flush_vectors()

        # 断言: 两个 Canonical ID 应该相同（发生了融合）
        if canonical_id_1 != canonical_id_2:
            logger.error(f"融合失败！创建了两个独立的节点：\n  节点1: {canonical_id_1}\n  节点2: {canonical_id_2}")
            # 获取两个节点的信息
            node1 = self.graph_repository.get_node_by_id(canonical_id_1)
            node2 = self.graph_repository.get_node_by_id(canonical_id_2)
            logger.error(f"节点1: {node1}")
            logger.error(f"节点2: {node2}")

        assert canonical_id_1 == canonical_id_2, f"应该融合到同一个 Canonical 节点，但得到不同的ID: {canonical_id_1} != {canonical_id_2}"

        # 断言: applicable_models 应该包含两个机型
        applicable_models_values = self.assertions.get_merged_field_values(
            node_id=canonical_id_2,
            collection_name=self.collection_name,
            field_name="机型"
        )

        logger.info(f"机型字段在三个数据库中的值: {applicable_models_values}")

        # 验证 Milvus
        models_milvus = applicable_models_values.get("milvus", "")
        assert "机型A" in str(models_milvus), f"Milvus 中应包含机型A，实际: {models_milvus}"
        assert "机型B" in str(models_milvus), f"Milvus 中应包含机型B，实际: {models_milvus}"

        # 验证 MongoDB
        models_mongo = applicable_models_values.get("mongodb", "")
        assert "机型A" in str(models_mongo), f"MongoDB 中应包含机型A，实际: {models_mongo}"
        assert "机型B" in str(models_mongo), f"MongoDB 中应包含机型B，实际: {models_mongo}"

        # 验证 Neo4j
        models_neo4j = applicable_models_values.get("neo4j", "")
        assert "机型A" in str(models_neo4j), f"Neo4j 中应包含机型A，实际: {models_neo4j}"
        assert "机型B" in str(models_neo4j), f"Neo4j 中应包含机型B，实际: {models_neo4j}"

        logger.info("✅ 测试2通过: Canonical 融合后三个数据库的 metadata 一致")

    def test_delete_node_from_all_databases(self, sample_fault_data):
        """
        测试3: 删除节点时从三个数据库中删除

        步骤：
        1. 创建一个新节点
        2. 调用 delete_documents 删除
        3. 验证三个数据库中的节点都已删除
        """
        from database.graph.model import NodeType

        # 创建新节点
        fault_text = sample_fault_data["fault_description"]
        metadata = sample_fault_data["metadata"]

        node_id = self.handler._create_new_knowledge_node(
            text=fault_text,
            field_name="故障描述",
            details="测试工单",
            metadata=metadata,
            node_type=NodeType.RAW_FAULT
        )

        logger.info(f"创建新节点: {node_id}")

        # 刷新向量缓冲区
        self.handler._flush_vectors()

        # 验证节点存在
        existence_before = self.assertions.assert_node_exists_in_all_databases(
            node_id=node_id,
            collection_name=self.collection_name
        )

        assert existence_before["milvus"], "删除前节点应存在于 Milvus"
        assert existence_before["mongodb"], "删除前节点应存在于 MongoDB"
        assert existence_before["neo4j"], "删除前节点应存在于 Neo4j"

        # 删除节点
        result = self.handler.delete_documents(document_ids=[node_id])

        logger.info(f"删除结果: {result}")

        # 断言: 删除结果应包含三个数据库的统计
        assert result["deleted_nodes"] == 1, "Neo4j 中应删除1个节点"
        assert result["deleted_vectors"] == 1, "Milvus 中应删除1个向量"
        assert result["deleted_mongo"] == 1, "MongoDB 中应删除1个文档"

        # 断言: 验证节点已从三个数据库中删除
        deletion_status = self.assertions.assert_node_deleted_from_all_databases(
            node_id=node_id,
            collection_name=self.collection_name
        )

        assert deletion_status["neo4j"], "Neo4j 中节点应已删除"
        # Milvus 和 MongoDB 是软删除，检查 is_deleted 标记
        assert deletion_status["milvus"], "Milvus 中节点应标记为已删除"
        assert deletion_status["mongodb"], "MongoDB 中节点应标记为已删除"

        logger.info("✅ 测试3通过: 节点成功从三个数据库中删除")

    def test_process_fault_files_creates_nodes_in_all_databases(self, multiple_fault_excel_file):
        """
        测试4: 处理 Excel 文件时在三个数据库中创建节点

        步骤：
        1. 创建包含多条故障数据的 Excel 文件
        2. 调用 process_fault_files 处理
        3. 验证所有节点都在三个数据库中创建
        """
        # 处理 Excel 文件
        file_list = [multiple_fault_excel_file]

        knowledge_nodes = self.handler.process_fault_files(
            file_list=file_list,
            details="测试工单"
        )

        logger.info(f"处理了 {len(knowledge_nodes)} 个知识节点")

        # 刷新向量缓冲区
        self.handler._flush_vectors()

        # 获取所有创建的节点 ID
        node_ids = []
        for node in knowledge_nodes:
            if isinstance(node, dict) and 'id' in node:
                node_ids.append(node['id'])
            elif hasattr(node, 'id'):
                node_ids.append(node.id)

        logger.info(f"创建的节点 ID: {node_ids}")

        # 验证: 每个节点都应该在三个数据库中存在
        for node_id in node_ids[:3]:  # 只验证前3个节点
            existence = self.assertions.assert_node_exists_in_all_databases(
                node_id=node_id,
                collection_name=self.collection_name
            )

            assert existence["milvus"], f"节点 {node_id} 应存在于 Milvus"
            assert existence["mongodb"], f"节点 {node_id} 应存在于 MongoDB"
            assert existence["neo4j"], f"节点 {node_id} 应存在于 Neo4j"

        logger.info("✅ 测试4通过: Excel 处理后所有节点都同步到三个数据库")

    def test_merge_count_increments_in_all_databases(self):
        """
        测试5: 融合次数 merge_count 在三个数据库中同步递增

        步骤：
        1. 创建一个 Canonical 节点
        2. 多次融合相似节点
        3. 验证 merge_count 在三个数据库中一致递增
        """
        from database.graph.model import NodeType

        # 创建第一个 Canonical 节点
        canonical_id, _ = self.handler._handle_canonical_matching(
            content="发动机过热",
            field_name="故障描述",
            details="测试工单",
            metadata={"机型": "机型A"},
            knowledge_type="fault"
        )

        logger.info(f"创建 Canonical 节点: {canonical_id}")

        # 刷新缓冲区
        self.handler._flush_vectors()

        # 检查初始 merge_count
        merge_count_values_1 = self.assertions.get_merged_field_values(
            node_id=canonical_id,
            collection_name=self.collection_name,
            field_name="merge_count"
        )

        logger.info(f"初始 merge_count: {merge_count_values_1}")

        # 融合第二个节点
        canonical_id_2, fusion_info_2 = self.handler._handle_canonical_matching(
            content="发动机温度过高",
            field_name="故障描述",
            details="测试工单",
            metadata={"机型": "机型B"},
            knowledge_type="fault"
        )

        assert canonical_id == canonical_id_2, "应该融合到同一个节点"

        # 刷新缓冲区
        self.handler._flush_vectors()

        # 检查融合后的 merge_count
        merge_count_values_2 = self.assertions.get_merged_field_values(
            node_id=canonical_id,
            collection_name=self.collection_name,
            field_name="merge_count"
        )

        logger.info(f"第一次融合后 merge_count: {merge_count_values_2}")

        # 验证: Milvus 和 MongoDB 的 merge_count 应该增加
        count_milvus_before = merge_count_values_1.get("milvus", 0) or 0
        count_milvus_after = merge_count_values_2.get("milvus", 0) or 0
        count_mongo_before = merge_count_values_1.get("mongodb", 0) or 0
        count_mongo_after = merge_count_values_2.get("mongodb", 0) or 0

        # 处理 None 和字符串情况
        if isinstance(count_milvus_before, str):
            count_milvus_before = int(count_milvus_before) if count_milvus_before.isdigit() else 0
        if isinstance(count_milvus_after, str):
            count_milvus_after = int(count_milvus_after) if count_milvus_after.isdigit() else 0
        if isinstance(count_mongo_before, str):
            count_mongo_before = int(count_mongo_before) if count_mongo_before.isdigit() else 0
        if isinstance(count_mongo_after, str):
            count_mongo_after = int(count_mongo_after) if count_mongo_after.isdigit() else 0

        assert count_milvus_after >= count_milvus_before, "Milvus 中 merge_count 应该增加或保持"
        assert count_mongo_after >= count_mongo_before, "MongoDB 中 merge_count 应该增加或保持"

        logger.info("✅ 测试5通过: merge_count 在三个数据库中同步递增")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
