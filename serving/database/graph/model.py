from dataclasses import dataclass
from typing import List, Optional, Union
from enum import Enum

class NodeType(Enum):
    # 工单节点类型枚举 - 支持故障和解决方案的语义区分
    RAW_FAULT = "RawFault"  # 原始故障案例
    RAW_SOLUTION = "RawSolution"  # 原始解决方案案例
    CANONICAL_FAULT = "CanonicalFault"  # 标准化故障知识
    CANONICAL_SOLUTION = "CanonicalSolution"  # 标准化解决方案知识

class RelationshipType(Enum):
    # Canonical-Raw结构的核心关系类型
    INSTANCE_OF = "INSTANCE_OF"  # Raw案例实例化为Canonical Sop
    MERGED_FROM = "MERGED_FROM"  # Canonical由哪些Raw案例融合而来
    HAS_SOLUTION = "HAS_SOLUTION"  # 故障描述对应的解决方案

@dataclass
class KnowledgeNode:
    id: str
    type: NodeType
    name: str
    description: str
    properties: dict = None

@dataclass
class KnowledgeRelationship:
    source_id: str
    target_id: str
    type: RelationshipType
    properties: dict = None
