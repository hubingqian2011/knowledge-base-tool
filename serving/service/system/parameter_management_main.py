# -*- coding: utf-8 -*-
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from dataclasses import dataclass, field

from database.sql.repository.repository_factory import RepositoryFactory
from util.logging.logger import get_logger

logger = get_logger(__name__)

# 数据模型定义
@dataclass
class ParameterNode:
    name: str
    description: Optional[str] = None
    children: Optional[Dict[str, 'ParameterNode']] = field(default_factory=dict)
    list_children: Optional[List['ParameterNode']] = field(default_factory=list)

class ParameterManagement:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        """初始化参数管理类"""
        self.repository = RepositoryFactory.get_instance().parameter_repository
    
    def _parse_parameter_tree(self, data: Dict[str, Any]) -> Dict[str, ParameterNode]:
        """解析JSON格式的参数树
        
        Args:
            data: 输入的JSON数据
            
        Returns:
            Dict[str, ParameterNode]: 解析后的参数树
        """
        result = {}
        
        for key, value in data.items():
            if isinstance(value, str):
                # 如果值是字符串，将其作为description
                result[key] = ParameterNode(name=key, description=value)
            elif isinstance(value, dict):
                # 如果值是字典，递归处理
                children = self._parse_parameter_tree(value)
                result[key] = ParameterNode(name=key, children=children)
            elif isinstance(value, list):
                # 如果值是列表，处理列表中的每个字典
                list_children = []
                for item in value:
                    if not isinstance(item, dict):
                        raise ValueError(f"列表中的元素必须是字典类型: {item}")
                    # 处理列表中的每个字典项
                    for item_key, item_value in item.items():
                        if isinstance(item_value, str):
                            list_children.append(ParameterNode(name=item_key, description=item_value))
                        elif isinstance(item_value, dict):
                            children = self._parse_parameter_tree({item_key: item_value})
                            list_children.append(ParameterNode(name=item_key, children=children))
                        else:
                            raise ValueError(f"不支持的值类型: {type(item_value)}")
                result[key] = ParameterNode(name=key, list_children=list_children, description="LIST")
            else:
                raise ValueError(f"不支持的值类型: {type(value)}")
                
        return result

    def create_parameter_tree(self, name: str, description: Optional[str], data: Dict[str, Any]):
        """从JSON格式创建树状结构的参数集

        Args:
            name: 参数集名称
            description: 参数集描述
            data: 输入的JSON数据
        """
        session = self.repository.session
        try:
            # 解析JSON数据为参数树结构
            parameters = self._parse_parameter_tree(data)

            # 创建参数集，Repository层已经处理了flush
            parameter_set = self.repository.create_parameter_set(name, description)

            def create_parameter_node(node: ParameterNode, parent_id: Optional[int] = None):
                # 创建当前节点
                parameter = self.repository.create_parameter(
                    name=node.name,
                    parameter_set_id=parameter_set.id,
                    description=node.description,
                    parent_id=parent_id
                )

                # 递归创建字典类型的子节点
                if node.children:
                    for child_name, child_node in node.children.items():
                        create_parameter_node(child_node, parameter.id)

                # 递归创建列表类型的子节点
                if node.list_children:
                    for child_node in node.list_children:
                        create_parameter_node(child_node, parameter.id)

                return parameter

            # 创建所有根节点及其子节点
            for param_name, param_node in parameters.items():
                create_parameter_node(param_node)

            # 提交事务
            session.commit()
            logger.info(f"成功创建树状参数集: {name}")
            return parameter_set
        except (SQLAlchemyError, Exception) as e:
            # 回滚事务
            session.rollback()
            logger.error(f"创建树状参数集失败: {str(e)}")
            raise

    def get_parameter_tree(self, parameter_set_id: int):
        """获取参数集的树状结构
        
        Args:
            parameter_set_id: 参数集ID
            
        Returns:
            Dict: 包含参数集信息和树状结构的字典
        """
        try:
            # 获取参数集基本信息
            parameter_set = self.repository.get_parameter_set(parameter_set_id)
            if not parameter_set:
                logger.warning(f"未找到参数集: {parameter_set_id}")
                return None
                
            # 获取树状结构
            tree = self.repository.get_parameter_tree(parameter_set_id)
            
            # 组合返回结果
            return {
                "id": parameter_set.id,
                "name": parameter_set.name,
                "description": parameter_set.description,
                "tree": tree
            }
        except Exception as e:
            logger.error(f"获取参数集树状结构失败: {str(e)}")
            raise

    def _generate_tree_description(self, tree: Dict[str, Any], indent: int = 0) -> str:
        """递归生成参数树的描述性文本
        
        Args:
            tree: 参数树数据
            indent: 当前缩进级别
            
        Returns:
            str: 描述性文本
        """
        description = []
        indent_str = "    " * indent
        
        for key, value in tree.items():
            if isinstance(value, str):
                description.append(f'{indent_str}- "{key}"字段是一个字符串，表示"{value}"')
            elif isinstance(value, dict):
                description.append(f'{indent_str}- "{key}"字段是一个字典，包含以下字段：')
                description.append(self._generate_tree_description(value, indent + 1))
            elif isinstance(value, list):
                description.append(f'{indent_str}- "{key}"字段是一个列表，每个元素包含以下属性:')
                for item in value:
                    description.append(self._generate_tree_description(item, indent + 1))
        
        return "\n".join(description)

    def get_parameter_tree_description(self, parameter_set_id: int) -> str:
        """获取参数集的描述性文本
        
        Args:
            parameter_set_id: 参数集ID
            
        Returns:
            str: 参数集的描述性文本
        """
        try:
            # 获取参数集基本信息
            parameter_set = self.repository.get_parameter_set(parameter_set_id)
            if not parameter_set:
                logger.warning(f"未找到参数集: {parameter_set_id}")
                return None
                
            # 获取树状结构
            tree = self.repository.get_parameter_tree(parameter_set_id)
            
            # 生成描述性文本
            description = f'该JSON包含以下字段\n'
            description += self._generate_tree_description(tree)
            
            return description
        except Exception as e:
            logger.error(f"获取参数集描述性文本失败: {str(e)}")
            raise

    def _validate_tree_structure(self, input_data: Dict[str, Any], template_tree: Dict[str, Any]) -> bool:
        """递归验证输入数据是否符合模板树结构
        
        Args:
            input_data: 输入的JSON数据
            template_tree: 模板树结构
            
        Returns:
            bool: 是否验证通过
        """
        # 检查所有必需的键是否都存在
        for key in template_tree.keys():
            if key not in input_data:
                logger.error(f"缺少必需的键: {key}")
                return False
                
            template_value = template_tree[key]
            input_value = input_data[key]
            
            # 如果模板值是字符串，检查输入值是否也是字符串
            if isinstance(template_value, str):
                if not isinstance(input_value, str):
                    logger.error(f"键 {key} 的值必须是字符串类型")
                    return False
                    
            # 如果模板值是字典，递归验证
            elif isinstance(template_value, dict):
                if not isinstance(input_value, dict):
                    logger.error(f"键 {key} 的值必须是字典类型")
                    return False
                if not self._validate_tree_structure(input_value, template_value):
                    return False
                    
            # 如果模板值是列表，验证列表结构
            elif isinstance(template_value, list):
                if not isinstance(input_value, list):
                    logger.error(f"键 {key} 的值必须是列表类型")
                    return False
                    
                # 验证列表中的每个元素
                for item in input_value:
                    if not isinstance(item, dict):
                        logger.error(f"列表中的元素必须是字典类型")
                        return False
                    
                    # 检查当前元素是否匹配任意一个模板项
                    item_valid = False
                    for template_item in template_value:
                        try:
                            if self._validate_tree_structure(item, template_item):
                                item_valid = True
                                break
                        except Exception:
                            continue
                    
                    if not item_valid:
                        logger.error(f"列表元素 {item} 不符合任何模板项的要求")
                        return False
                            
        return True

    def validate_parameter_json(self, json_str: str, parameter_set_id: int) -> bool:
        """验证JSON字符串是否符合参数集的格式要求
        
        Args:
            json_str: 要验证的JSON字符串
            parameter_set_id: 参数集ID
            
        Returns:
            bool: 是否验证通过
        """
        try:
            import json
            # 获取参数集的树状结构
            parameter_set = self.repository.get_parameter_set(parameter_set_id)
            if not parameter_set:
                logger.error(f"未找到参数集: {parameter_set_id}")
                return False
            
            template_tree = self.repository.get_parameter_tree(parameter_set_id)
            
            # 解析输入的JSON字符串
            try:
                # print(json_str)
                input_data = json.loads(json_str)
            except json.JSONDecodeError as e:
                logger.error(f"JSON格式错误: {str(e)}")
                return False
            
            # 验证JSON结构
            if not isinstance(input_data, dict):
                logger.error("输入的JSON必须是字典类型")
                return False
                
            return self._validate_tree_structure(input_data, template_tree)
            
        except ValueError as e:
            logger.error(f"验证参数JSON失败: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"验证参数JSON失败: {str(e)}")
            return False

# 创建全局单例实例
parameter_management = ParameterManagement() 