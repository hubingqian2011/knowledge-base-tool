# -*- coding: utf-8 -*-
from typing import Optional
from abc import ABC, abstractmethod
import os


from util.logging.logger import get_logger

logger = get_logger(__name__)

class BaseReader(ABC):
    """文件读取器基类"""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        """初始化读取器"""
        pass
    
    @abstractmethod
    def read_text(self, file_path: str) -> Optional[str]:
        """
        读取文件中的纯文本内容
        
        Args:
            file_path: 文件路径
            
        Returns:
            str: 文件中的文本内容，如果读取失败则返回None
        """
        pass
    
    def _check_file_exists(self, file_path: str) -> bool:
        """
        检查文件是否存在
        
        Args:
            file_path: 文件路径
            
        Returns:
            bool: 文件是否存在
        """
        if not os.path.exists(file_path):
            logger.error(f"文件不存在: {file_path}")
            return False
        return True 