# -*- coding: utf-8 -*-
from typing import Optional, List, Dict, Any
import pandas as pd
import os

from .base_reader import BaseReader
from util.logging.logger import get_logger

logger = get_logger(__name__)

class CsvReader(BaseReader):
    """CSV文件读取器类，支持.csv格式"""
    
    def read_text(self, file_path: str, encoding: str = 'utf-8', sep: str = ',') -> Optional[str]:
        """
        读取CSV文件中的文本内容
        
        Args:
            file_path: CSV文件路径
            encoding: 文件编码，默认utf-8
            sep: 分隔符，默认逗号
            
        Returns:
            str: CSV文件中的文本内容，如果读取失败则返回None
        """
        try:
            if not self._check_file_exists(file_path):
                return None
            
            # 读取CSV文件
            df = pd.read_csv(file_path, encoding=encoding, sep=sep)
            
            # 处理表头
            headers = df.columns.tolist()
            text_content = [" | ".join(str(h) for h in headers)]
            
            # 处理数据行
            for _, row in df.iterrows():
                # 将每行数据转换为字符串并用 | 连接
                row_text = " | ".join(str(cell) for cell in row)
                if row_text.strip():  # 只添加非空行
                    text_content.append(row_text)
            
            # 合并所有文本内容
            full_text = "\n".join(text_content)
            
            if full_text.strip():
                logger.info(f"成功读取CSV文件文本内容: {file_path}")
                return full_text
            else:
                logger.warning(f"CSV文件内容为空: {file_path}")
                return None
                
        except Exception as e:
            logger.error(f"读取CSV文件文本内容失败: {str(e)}")
            return None
    
    def read_dataframe(self, file_path: str, encoding: str = 'utf-8', sep: str = ',') -> Optional[pd.DataFrame]:
        """
        读取CSV文件内容为DataFrame
        
        Args:
            file_path: CSV文件路径
            encoding: 文件编码，默认utf-8
            sep: 分隔符，默认逗号
            
        Returns:
            pd.DataFrame: CSV文件数据，如果读取失败则返回None
        """
        try:
            if not self._check_file_exists(file_path):
                return None
            
            # 读取CSV文件
            df = pd.read_csv(file_path, encoding=encoding, sep=sep)
            logger.info(f"成功读取CSV文件数据: {file_path}")
            return df
                
        except Exception as e:
            logger.error(f"读取CSV文件数据失败: {str(e)}")
            return None
    
    def get_column_names(self, file_path: str, encoding: str = 'utf-8', sep: str = ',') -> Optional[List[str]]:
        """
        获取CSV文件的列名
        
        Args:
            file_path: CSV文件路径
            encoding: 文件编码，默认utf-8
            sep: 分隔符，默认逗号
            
        Returns:
            List[str]: 列名列表，如果读取失败则返回None
        """
        try:
            if not self._check_file_exists(file_path):
                return None
            
            # 读取CSV文件
            df = pd.read_csv(file_path, encoding=encoding, sep=sep)
            column_names = df.columns.tolist()
            logger.info(f"成功获取CSV文件列名: {file_path}")
            return column_names
                
        except Exception as e:
            logger.error(f"获取CSV文件列名失败: {str(e)}")
            return None
    
    def get_row_count(self, file_path: str, encoding: str = 'utf-8', sep: str = ',') -> Optional[int]:
        """
        获取CSV文件的行数
        
        Args:
            file_path: CSV文件路径
            encoding: 文件编码，默认utf-8
            sep: 分隔符，默认逗号
            
        Returns:
            int: 行数，如果读取失败则返回None
        """
        try:
            if not self._check_file_exists(file_path):
                return None
            
            # 读取CSV文件
            df = pd.read_csv(file_path, encoding=encoding, sep=sep)
            row_count = len(df)
            logger.info(f"成功获取CSV文件行数: {file_path} - {row_count}")
            return row_count
                
        except Exception as e:
            logger.error(f"获取CSV文件行数失败: {str(e)}")
            return None

# 创建全局单例实例
csv_reader = CsvReader() 