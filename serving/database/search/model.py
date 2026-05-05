from typing import Dict, Any, Type, ClassVar, Optional, List
from abc import ABC, abstractmethod
from datetime import datetime
from elasticsearch import Elasticsearch
from .elasticsearch_client import ElasticsearchClient
from config.config import ES_URL, ES_USER, ES_PASSWORD
from util.logging.logger import get_logger

logger = get_logger(__name__)

class BaseModel(ABC):
    """Elasticsearch 文档基础模型类"""
    
    # 索引名称，子类必须重写
    index_name: ClassVar[str] = ""
    
    # 字段映射，子类必须重写
    mappings: ClassVar[Dict[str, Any]] = {}
    
    def __init__(self, **kwargs):
        """
        初始化模型实例
        
        Args:
            **kwargs: 文档字段值
        """
        for key, value in kwargs.items():
            setattr(self, key, value)
    
    @classmethod
    def create_index(cls, es_client: ElasticsearchClient) -> bool:
        """
        创建索引
        
        Args:
            es_client: Elasticsearch 客户端实例
            
        Returns:
            bool: 是否创建成功
        """
        return es_client.create_index(cls.index_name, cls.mappings)
    
    def to_dict(self) -> Dict[str, Any]:
        """
        将模型实例转换为字典
        
        Returns:
            Dict[str, Any]: 文档数据
        """
        return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BaseModel':
        """
        从字典创建模型实例
        
        Args:
            data: 文档数据
            
        Returns:
            BaseModel: 模型实例
        """
        return cls(**data)


class DocumentModel(BaseModel):
    """文档模型类"""
    
    index_name = "documents"
    
    mappings = {
        "properties": {
            "content": {
                "type": "text",
                "analyzer": "ik_max_word",
                "search_analyzer": "ik_smart"
            },
            "metadata": {"type": "object"},
            "collection": {"type": "keyword"}
        }
    }
    
    def __init__(
        self,
        content: str,
        collection: str,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        """
        初始化文档模型
        
        Args:
            content: 文档内容
            collection: 文档集合
            metadata: 元数据
            **kwargs: 其他字段
        """
        super().__init__(**kwargs)
        self.content = content
        self.collection = collection
        self.metadata = metadata or {}


class ModelManager:
    """模型管理器类"""

    _instance: Optional['ModelManager'] = None
    _es_client: Optional[ElasticsearchClient] = None
    _models: Dict[str, Type[BaseModel]] = {}
    _indices_initialized: bool = False

    def __new__(cls):
        """单例模式实现"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """初始化模型管理器"""
        if self._es_client is None:
            self._es_client = ElasticsearchClient(
                url=ES_URL,
                username=ES_USER,
                password=ES_PASSWORD
            )
            # 自动扫描并注册所有模型类
            self._scan_models()
        if not ModelManager._indices_initialized:
            ModelManager._indices_initialized = True
            self.initialize_indices()
    
    def _scan_models(self) -> None:
        """扫描并注册所有 BaseModel 的子类"""
        for model_class in BaseModel.__subclasses__():
            if hasattr(model_class, 'index_name') and model_class.index_name:
                self._models[model_class.index_name] = model_class
                logger.info(f"已注册模型: {model_class.__name__} (index: {model_class.index_name})")
    
    @property
    def es_client(self) -> ElasticsearchClient:
        """获取 Elasticsearch 客户端实例"""
        return self._es_client
    
    def get_model(self, index_name: str) -> Optional[Type[BaseModel]]:
        """
        获取模型类
        
        Args:
            index_name: 索引名称
            
        Returns:
            Optional[Type[BaseModel]]: 模型类
        """
        return self._models.get(index_name)
    
    def indices_exist(self, index_names: Optional[List[str]] = None) -> bool:
        """
        检查指定的索引是否存在
        
        Args:
            index_names: 要检查的索引名称列表，如果为None则检查所有已注册模型的索引
            
        Returns:
            bool: 如果所有指定的索引都存在则返回True，否则返回False
        """
        try:
            if index_names is None:
                # 获取所有已注册模型的索引名称
                index_names = list(self._models.keys())
            
            for index_name in index_names:
                if not self._es_client.es.indices.exists(index=index_name):
                    logger.debug(f"索引 {index_name} 不存在")
                    return False
            
            logger.info("所有索引已存在")
            return True
        except Exception as e:
            logger.error(f"检查索引是否存在时出错: {str(e)}")
            return False
    
    def create_indices(self) -> Dict[str, bool]:
        """
        创建所有已注册模型的索引（单次失败不抛异常，由 create_index 内部打 WARNING）

        Returns:
            Dict[str, bool]: 索引创建结果
        """
        try:
            results = {}
            for model_class in self._models.values():
                results[model_class.index_name] = model_class.create_index(self._es_client)
            if all(results.values()):
                logger.info("索引创建成功")
            return results
        except Exception as e:
            logger.error(f"创建索引失败: {str(e)}")
            return {}
    
    def initialize_indices(self) -> bool:
        """
        初始化索引，如果索引不存在则创建
        
        Returns:
            bool: 操作是否成功
        """
        if not self.indices_exist():
            results = self.create_indices()
            return all(results.values())
        return True
    
    def delete_indices(self) -> Dict[str, bool]:
        """
        删除所有已注册模型的索引
        
        Returns:
            Dict[str, bool]: 索引删除结果
        """
        try:
            results = {}
            for model_class in self._models.values():
                results[model_class.index_name] = self._es_client.delete_index(model_class.index_name)
            logger.info("索引删除成功")
            return results
        except Exception as e:
            logger.error(f"删除索引失败: {str(e)}")
            return {} 