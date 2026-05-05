from abc import ABC, abstractmethod
from typing import List, Dict, Any

class BaseReranker(ABC):
    """文本重排序器的抽象基类"""
    
    @abstractmethod
    def __init__(self, **kwargs):
        """
        初始化重排序器
        
        Args:
            **kwargs: 模型特定的初始化参数
        """
        pass
    
    @abstractmethod
    def rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int = None
    ) -> List[Dict[str, Any]]:
        """
        对文档进行重排序
        
        Args:
            query: 查询文本
            documents: 待重排序的文档列表，每个文档包含text字段
            top_k: 返回结果数量，如果为None则返回所有结果
            
        Returns:
            List[Dict[str, Any]]: 重排序后的文档列表
        """
        pass 