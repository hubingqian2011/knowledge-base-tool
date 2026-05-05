from typing import List, Optional, Dict, Any
import json
from .neo4j_manager import Neo4jManager
from .model import KnowledgeNode, KnowledgeRelationship, NodeType, RelationshipType
from util.logging.logger import get_logger
from config.config import NEO4J_DATABASE

class WorkorderGraphRepository:
    def __init__(self, database: Optional[str] = None):
        """
        初始化工单图谱仓库，使用Neo4jManager单例

        Args:
            database: Neo4j数据库名称，如果不指定则使用配置文件中的默认数据库
        """
        self.neo4j_manager = Neo4jManager.get_instance()
        self.database = database or NEO4J_DATABASE
        self.logger = get_logger(__name__)

        # 确保数据库存在，如果不存在则创建
        self._ensure_database_exists()

    def _ensure_database_exists(self):
        """确保数据库存在，如果不存在则创建"""
        try:
            if not self.neo4j_manager.database_exists(self.database):
                self.logger.info(f"数据库 {self.database} 不存在，正在创建...")
                success = self.neo4j_manager.create_database(self.database)
                if success:
                    self.logger.info(f"数据库 {self.database} 创建成功")
                else:
                    self.logger.warning(f"数据库 {self.database} 创建失败")
        except Exception as e:
            self.logger.error(f"确保数据库存在时出错: {str(e)}")

    def _serialize_metadata(self, properties: Optional[Dict[str, Any]]) -> str:
        """将 metadata 字典序列化为 JSON 字符串存储"""
        if not properties:
            return "{}"
        return json.dumps(properties, ensure_ascii=False)

    def _deserialize_metadata(self, metadata_str: str) -> Dict[str, Any]:
        """将 JSON 字符串反序列化为 metadata 字典"""
        try:
            return json.loads(metadata_str) if metadata_str else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def _process_node_result(self, node_data: Dict[str, Any]) -> Dict[str, Any]:
        """处理从数据库读取的节点数据，反序列化 metadata 字段"""
        if "metadata" in node_data and isinstance(node_data["metadata"], str):
            node_data["metadata"] = self._deserialize_metadata(node_data["metadata"])
        return node_data

    def _process_relationship_result(self, rel_data: Dict[str, Any]) -> Dict[str, Any]:
        """处理从数据库读取的关系数据，反序列化 metadata 字段"""
        if "metadata" in rel_data and isinstance(rel_data["metadata"], str):
            rel_data["metadata"] = self._deserialize_metadata(rel_data["metadata"])
        return rel_data

    def create_node(self, node: KnowledgeNode) -> Optional[Dict[str, Any]]:
        """创建知识节点：将 metadata 作为一个独立字段存储"""
        try:
            final_props = {
                "id": node.id,
                "name": node.name,
                "description": node.description,
                "type": node.type.value,
                "metadata": self._serialize_metadata(node.properties)  # 单一 JSON 字符串字段
            }

            with self.neo4j_manager.session(database=self.database) as session:
                query = f"CREATE (n:{node.type.value} $props) RETURN n"
                result = session.run(query, {"props": final_props})
                record = result.single()
                if record:
                    return self._process_node_result(dict(record["n"]))
                return None
        except Exception as e:
            self.logger.error(f"创建节点失败: {str(e)}")
            return None

    def create_relationship(self, relationship: KnowledgeRelationship) -> Optional[Dict[str, Any]]:
        """创建知识关系：使用 MERGE 避免重复创建"""
        try:
            # 验证节点存在
            source_node = self.get_node_by_id(relationship.source_id)
            target_node = self.get_node_by_id(relationship.target_id)

            if not source_node or not target_node:
                self.logger.warning(f"关系创建失败：源节点({relationship.source_id})或目标节点({relationship.target_id})不存在")
                return None

            # 根据大模型的响应来看，会有不满足现在的验证关系的relationship，我决定允许这种非法的relationship，减少大模型的提示词量。
            # 验证关系是否合法
            # print(source_node, target_node)
            # if not relationship.validate_relationship(
            #     NodeType(source_node["type"]),
            #     NodeType(target_node["type"])
            # ):
            #     print("关系类型与节点类型不匹配")
            #     return None
            # 将 properties 包装在 metadata 字段中
            rel_props = {
                "metadata": self._serialize_metadata(relationship.properties)
            }

            with self.neo4j_manager.session(database=self.database) as session:
                # 关键修复：使用 MERGE 替代 CREATE 避免重复关系
                # MERGE 保证同类型、同方向的关系唯一
                query = (
                    f"MATCH (a), (b) "
                    f"WHERE a.id = $source_id AND b.id = $target_id "
                    f"MERGE (a)-[r:{relationship.type.value}]->(b) "
                    f"ON CREATE SET r = $props "  # 只有在新建关系时才设置属性
                    f"RETURN a.id as source_id, b.id as target_id, type(r) as type, properties(r) as properties"
                )
                result = session.run(query, {
                    "source_id": relationship.source_id,
                    "target_id": relationship.target_id,
                    "props": rel_props
                })
                record = result.single()
                return dict(record) if record else None
        except Exception as e:
            self.logger.error(f"创建/合并关系失败: {str(e)}")
            return None

    def get_node_by_id(self, node_id: str) -> Optional[Dict[str, Any]]:
        """根据ID获取节点并自动解析 metadata 字段"""
        try:
            with self.neo4j_manager.session(database=self.database) as session:
                query = "MATCH (n) WHERE n.id = $node_id RETURN n"
                result = session.run(query, {"node_id": node_id})
                record = result.single()
                if record:
                    return self._process_node_result(dict(record["n"]))
                return None
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.logger.error(f"获取节点失败: {str(e)}")
            return None

    def get_nodes_by_ids(self, node_ids: List[str]) -> List[Dict[str, Any]]:
        """
        根据ID列表批量获取节点

        Args:
            node_ids: 节点ID列表

        Returns:
            List[Dict[str, Any]]: 节点信息列表
        """
        try:
            with self.neo4j_manager.session(database=self.database) as session:
                query = (
                    "MATCH (n) "
                    "WHERE n.id IN $node_ids "
                    "RETURN n"
                )
                result = session.run(query, {"node_ids": node_ids})
                return [self._process_node_result(dict(record["n"])) for record in result]
        except Exception as e:
            self.logger.error(f"批量获取节点失败: {str(e)}")
            return []

    def get_relationship(self, source_id: str, target_id: str, type: RelationshipType) -> Optional[Dict[str, Any]]:
        """检查关系是否已存在"""
        try:
            with self.neo4j_manager.session(database=self.database) as session:
                query = (
                    f"MATCH (a)-[r:{type.value}]->(b) "
                    f"WHERE a.id = $source_id AND b.id = $target_id "
                    f"RETURN a.id as source_id, b.id as target_id, type(r) as type, properties(r) as properties"
                )
                result = session.run(query, {
                    "source_id": source_id,
                    "target_id": target_id
                })
                record = result.single()
                if record:
                    rel_data = dict(record)
                    # 处理 properties 中的 metadata
                    if "properties" in rel_data and isinstance(rel_data["properties"], dict):
                        if "metadata" in rel_data["properties"] and isinstance(rel_data["properties"]["metadata"], str):
                            rel_data["properties"]["metadata"] = self._deserialize_metadata(
                                rel_data["properties"].get("metadata", "{}")
                            )
                    return rel_data
                return None
        except Exception as e:
            self.logger.error(f"获取关系失败: {str(e)}")
            return None

    def update_node(self, node: KnowledgeNode) -> bool:
        """更新节点信息"""
        try:
            props_to_update = {
                "name": node.name,
                "description": node.description,
                "metadata": self._serialize_metadata(node.properties)
            }

            with self.neo4j_manager.session(database=self.database) as session:
                query = (
                    f"MATCH (n) WHERE n.id = $id "
                    f"SET n += $props"
                )
                session.run(query, {
                    "id": node.id,
                    "props": props_to_update
                })
                return True
        except Exception as e:
            self.logger.error(f"更新节点失败: {str(e)}")
            return False

    def delete_node(self, node_id: str) -> bool:
        """删除节点及其关系"""
        try:
            with self.neo4j_manager.session(database=self.database) as session:
                query = "MATCH (n {id: $node_id}) DETACH DELETE n"
                session.run(query, {"node_id": node_id})
                return True
        except Exception as e:
            self.logger.error(f"删除节点失败: {str(e)}")
            return False

    def get_node_network(self, node_id: str, max_depth: int = 2) -> Dict[str, Any]:
        """
        获取节点的完整上下游链路，限制关联深度避免结果过大

        Args:
            node_id: 起始节点ID
            max_depth: 最大关联深度，默认为2

        Returns:
            Dict[str, Any]: 包含节点和关系的网络信息
            {
                'nodes': List[Dict[str, Any]],  # 所有相关节点
                'relationships': List[Dict[str, Any]]  # 所有相关关系
            }
        """
        try:
            with self.neo4j_manager.session(database=self.database) as session:
                # 使用可变长度路径匹配，限制路径长度避免结果过大
                query = (
                    "MATCH path = (start {id: $node_id})-[r*1.." + str(max_depth) + "]-(related) "
                    "RETURN path"
                )
                result = session.run(query, {
                    "node_id": node_id
                })
                
                # 收集所有节点和关系
                nodes = []
                relationships = []
                seen_nodes = set()  # 用于跟踪已处理的节点ID
                seen_relationships = set()  # 用于跟踪已处理的关系
                
                for record in result:
                    path = record["path"]
                    # 收集路径中的所有节点
                    for node in path.nodes:
                        node_dict = self._process_node_result(dict(node))
                        if node_dict["id"] not in seen_nodes:
                            nodes.append(node_dict)
                            seen_nodes.add(node_dict["id"])
                    # 收集路径中的所有关系
                    for rel in path.relationships:
                        rel_dict = {
                            "source_id": rel.start_node["id"],
                            "target_id": rel.end_node["id"],
                            "type": type(rel).__name__
                        }
                        rel_key = f"{rel_dict['source_id']}-{rel_dict['target_id']}-{rel_dict['type']}"
                        if rel_key not in seen_relationships:
                            relationships.append(rel_dict)
                            seen_relationships.add(rel_key)
                
                return {
                    "nodes": nodes,
                    "relationships": relationships
                }
        except Exception as e:
            self.logger.error(f"获取节点网络失败: {str(e)}")
            return {"nodes": [], "relationships": []}

    def get_nodes_networks(self, node_ids: List[str], max_depth: int = 2) -> Dict[str, Any]:
        """
        批量获取多个节点的网络并去重，限制关联深度避免结果过大

        Args:
            node_ids: 节点ID列表
            max_depth: 最大关联深度，默认为2

        Returns:
            Dict[str, Any]: 合并后的网络信息
            {
                'nodes': List[Dict[str, Any]],  # 所有相关节点（去重）
                'relationships': List[Dict[str, Any]]  # 所有相关关系（去重）
            }
        """
        try:
            # 使用集合来存储节点和关系，自动去重
            all_nodes = set()
            all_relationships = set()

            # 遍历每个节点ID，获取其网络
            for node_id in node_ids:
                network = self.get_node_network(node_id, max_depth)
                
                # 将节点转换为可哈希的元组形式，以便去重
                for node in network['nodes']:
                    node_tuple = tuple(sorted(node.items()))
                    all_nodes.add(node_tuple)
                
                # 将关系转换为可哈希的元组形式，以便去重
                for rel in network['relationships']:
                    rel_tuple = tuple(sorted(rel.items()))
                    all_relationships.add(rel_tuple)
            
            # 将元组转换回字典
            return {
                'nodes': [dict(node) for node in all_nodes],
                'relationships': [dict(rel) for rel in all_relationships]
            }
        except Exception as e:
            self.logger.error(f"批量获取节点网络失败: {str(e)}")
            return {"nodes": [], "relationships": []}

    def get_nodes_networks_with_min_requirements(self, node_ids: List[str], max_depth: int = 2) -> Dict[str, Any]:
        """
        批量获取多个节点的网络，确保至少检索到fault和solution节点

        Args:
            node_ids: 节点ID列表
            max_depth: 最大关联深度，默认为2

        Returns:
            Dict[str, Any]: 合并后的网络信息，确保包含至少一个fault和一个solution节点
        """
        try:
            # 使用新的统一查询方法，避免分别查询导致的网络过大问题
            network = self.get_unified_network_with_depth_limit(node_ids, max_depth)

            # 检查是否包含fault和solution节点
            has_fault = any(node.get("type") == "FAULT" for node in network["nodes"])
            has_solution = any(node.get("type") == "Solution" for node in network["nodes"])

            # 如果已经满足要求，直接返回
            if has_fault and has_solution:
                self.logger.info(f"深度 {max_depth} 找到足够节点：{len(network['nodes'])} 个节点")
                return network

            # 如果不满足要求，逐步增加深度直到找到所需节点或达到最大深度
            current_depth = max_depth + 1
            max_allowed_depth = 5  # 设置最大允许深度

            while current_depth <= max_allowed_depth and not (has_fault and has_solution):
                self.logger.info(f"当前深度 {current_depth - 1} 未找到足够的fault和solution节点，增加到深度 {current_depth}")

                # 使用增加的深度重新获取网络
                expanded_network = self.get_unified_network_with_depth_limit(node_ids, current_depth)

                # 检查是否满足要求
                has_fault = any(node.get("type") == "FAULT" for node in expanded_network["nodes"])
                has_solution = any(node.get("type") == "Solution" for node in expanded_network["nodes"])

                # 如果找到了更多节点，更新网络
                if len(expanded_network["nodes"]) > len(network["nodes"]):
                    network = expanded_network

                current_depth += 1

            # 记录最终结果
            if not has_fault:
                self.logger.warning("未找到FAULT类型节点")
            if not has_solution:
                self.logger.warning("未找到Solution类型节点")

            self.logger.info(f"最终检索结果：{len(network['nodes'])} 个节点，{len(network['relationships'])} 个关系")

            return network

        except Exception as e:
            self.logger.error(f"获取节点网络（带最小要求）失败: {str(e)}")
            return {"nodes": [], "relationships": []}

    def get_unified_network_with_depth_limit(self, node_ids: List[str], max_depth: int = 2) -> Dict[str, Any]:
        """
        统一查询多个节点的网络，严格控制深度限制

        Args:
            node_ids: 起始节点ID列表
            max_depth: 最大关联深度

        Returns:
            Dict[str, Any]: 网络信息
        """
        try:
            with self.neo4j_manager.session(database=self.database) as session:
                # 使用UNION ALL合并多个起始节点的查询，但严格限制总深度
                query_parts = []
                for i, node_id in enumerate(node_ids):
                    query_parts.append(
                        f"MATCH path = (start{i} {{id: '{node_id}'}})-[r*1..{max_depth}]-(related{i}) "
                        f"RETURN path"
                    )

                # 如果没有节点ID，返回空结果
                if not query_parts:
                    return {"nodes": [], "relationships": []}

                # 组合查询
                combined_query = " UNION ALL ".join(query_parts)

                result = session.run(combined_query)

                # 收集所有节点和关系，严格去重
                nodes = []
                relationships = []
                seen_nodes = set()  # 用于跟踪已处理的节点ID
                seen_relationships = set()  # 用于跟踪已处理的关系

                for record in result:
                    path = record["path"]
                    # 收集路径中的所有节点
                    for node in path.nodes:
                        node_dict = self._process_node_result(dict(node))
                        if node_dict["id"] not in seen_nodes:
                            nodes.append(node_dict)
                            seen_nodes.add(node_dict["id"])

                    # 收集路径中的所有关系
                    for rel in path.relationships:
                        rel_dict = {
                            "source_id": rel.start_node["id"],
                            "target_id": rel.end_node["id"],
                            "type": type(rel).__name__
                        }
                        rel_key = f"{rel_dict['source_id']}-{rel_dict['target_id']}-{rel_dict['type']}"
                        if rel_key not in seen_relationships:
                            relationships.append(rel_dict)
                            seen_relationships.add(rel_key)

                # 限制节点数量，防止结果过大
                if len(nodes) > 100:  # 设置一个合理的上限
                    self.logger.warning(f"节点数量过多 ({len(nodes)})，将被限制为100个")
                    nodes = nodes[:100]
                    # 只保留与这些节点相关的关系
                    valid_node_ids = set(node["id"] for node in nodes)
                    relationships = [
                        rel for rel in relationships
                        if rel["source_id"] in valid_node_ids and rel["target_id"] in valid_node_ids
                    ]

                self.logger.info(f"深度 {max_depth} 查询结果：{len(nodes)} 个节点，{len(relationships)} 个关系")

                return {
                    "nodes": nodes,
                    "relationships": relationships
                }

        except Exception as e:
            self.logger.error(f"统一查询失败: {str(e)}")
            return {"nodes": [], "relationships": []}

    def get_canonical_diagnosis_path(self, canonical_fault_ids: List[str]) -> Dict[str, Any]:
        """
        专为诊断设计：只查询 CanonicalFault 及其对应的 CanonicalSolution
        忽略底层的 Raw 节点，减少噪音。

        Args:
            canonical_fault_ids: CanonicalFault 节点 ID 列表

        Returns:
            Dict[str, Any]: 包含节点和关系的网络信息
        """
        try:
            with self.neo4j_manager.session(database=self.database) as session:
                # Cypher 语法：查出匹配的故障节点，以及通过 HAS_SOLUTION 关联的解决方案节点
                query = """
                MATCH (fault:CanonicalFault)
                WHERE fault.id IN $node_ids
                OPTIONAL MATCH (fault)-[r:HAS_SOLUTION]->(solution:CanonicalSolution)
                RETURN fault, r, solution
                """
                result = session.run(query, {"node_ids": canonical_fault_ids})

                nodes = []
                relationships = []
                seen_node_ids = set()

                for record in result:
                    # 1. 收集故障节点
                    fault_node = self._process_node_result(dict(record["fault"]))
                    if fault_node["id"] not in seen_node_ids:
                        nodes.append(fault_node)
                        seen_node_ids.add(fault_node["id"])

                    # 2. 收集关联的方案节点和关系
                    solution_record = record.get("solution")
                    if solution_record:
                        solution_node = self._process_node_result(dict(solution_record))
                        if solution_node["id"] not in seen_node_ids:
                            nodes.append(solution_node)
                            seen_node_ids.add(solution_node["id"])

                        rel = record["r"]
                        relationships.append({
                            "source_id": fault_node["id"],
                            "target_id": solution_node["id"],
                            "type": type(rel).__name__
                        })

                self.logger.info(f"Canonical 路径查询结果：{len(nodes)} 个节点，{len(relationships)} 个关系")
                return {"nodes": nodes, "relationships": relationships}

        except Exception as e:
            self.logger.error(f"查询 Canonical 路径失败: {str(e)}")
            return {"nodes": [], "relationships": []}