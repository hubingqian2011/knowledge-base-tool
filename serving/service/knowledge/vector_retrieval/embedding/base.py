from abc import ABC, abstractmethod
from typing import List, Optional
import numpy as np

class BaseEmbedding(ABC):
    """文本嵌入生成器的抽象基类"""
    
    @abstractmethod
    def __init__(self, **kwargs):
        """
        初始化embedding客户端
        
        Args:
            **kwargs: 模型特定的初始化参数
        """
        pass
    
    @abstractmethod
    def encode(
        self,
        inputs: List[str],
        is_query: bool = False,
        **kwargs
    ) -> np.ndarray:
        """
        生成文本的嵌入向量
        
        Args:
            inputs: 输入文本列表
            is_query: 是否为查询文本
            **kwargs: 模型特定的编码参数
            
        Returns:
            np.ndarray: 归一化后的嵌入向量
        """
        pass
    
    def _normalize_embeddings(self, embeddings: np.ndarray) -> np.ndarray:
        """
        对嵌入向量进行L2归一化
        
        Args:
            embeddings: 原始嵌入向量
            
        Returns:
            np.ndarray: 归一化后的嵌入向量
        """
        norm = np.linalg.norm(embeddings, axis=1, keepdims=True)
        return embeddings / norm 