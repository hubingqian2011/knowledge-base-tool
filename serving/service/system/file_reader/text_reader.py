# -*- coding: utf-8 -*-
from typing import Optional

from .base_reader import BaseReader
from util.logging.logger import get_logger

logger = get_logger(__name__)

class TextReader(BaseReader):
    """纯文本文件读取器类，支持.txt格式"""
    
    def read_text(self, file_path: str, encoding: str = 'utf-8') -> Optional[str]:
        """
        读取纯文本文件内容
        
        Args:
            file_path: 文本文件路径
            encoding: 文件编码，默认utf-8
        
        Returns:
            str: 文本文件内容，如果读取失败则返回None
        """
        try:
            if not self._check_file_exists(file_path):
                return None
            with open(file_path, 'r', encoding=encoding) as f:
                content = f.read()
            if content.strip():
                logger.info(f"成功读取文本文件内容: {file_path}")
                return content
            else:
                logger.warning(f"文本文件内容为空: {file_path}")
                return None
        except Exception as e:
            logger.error(f"读取文本文件内容失败: {str(e)}")
            return None

# 创建全局单例实例
text_reader = TextReader() 