# -*- coding: utf-8 -*-
from typing import Optional, List, Dict
import pandas as pd
import os

from .base_reader import BaseReader
from util.logging.logger import get_logger

logger = get_logger(__name__)

class ExcelReader(BaseReader):
    """Excel文件读取器类，支持.xlsx和.xls格式"""
    
    def read_text(self, file_path: str) -> Optional[str]:
        """
        读取Excel文件中的文本内容
        
        Args:
            file_path: Excel文件路径
            
        Returns:
            str: Excel文件中的文本内容，如果读取失败则返回None
        """
        try:
            if not self._check_file_exists(file_path):
                return None
            
            # 读取所有sheet页
            excel_file = pd.ExcelFile(file_path)
            sheet_names = excel_file.sheet_names
            
            text_content = []
            
            # 遍历每个sheet页
            for sheet_name in sheet_names:
                # 读取当前sheet页
                df = pd.read_excel(file_path, sheet_name=sheet_name)
                
                # 添加sheet页名称
                text_content.append(f"\n=== Sheet: {sheet_name} ===\n")
                
                # 处理表头
                headers = df.columns.tolist()
                text_content.append(" | ".join(str(h) for h in headers))
                
                # 处理数据行
                for _, row in df.iterrows():
                    # 将每行数据转换为字符串并用 | 连接
                    row_text = " | ".join(str(cell) for cell in row)
                    if row_text.strip():  # 只添加非空行
                        text_content.append(row_text)
            
            # 合并所有文本内容
            full_text = "\n".join(text_content)
            
            if full_text.strip():
                logger.info(f"成功读取Excel文件文本内容: {file_path}")
                return full_text
            else:
                logger.warning(f"Excel文件内容为空: {file_path}")
                return None
                
        except Exception as e:
            logger.error(f"读取Excel文件文本内容失败: {str(e)}")
            return None
    
    def read_sheet(self, file_path: str, sheet_name: str) -> Optional[pd.DataFrame]:
        """
        读取指定sheet页的内容
        
        Args:
            file_path: Excel文件路径
            sheet_name: sheet页名称
            
        Returns:
            pd.DataFrame: sheet页的数据，如果读取失败则返回None
        """
        try:
            if not self._check_file_exists(file_path):
                return None
            
            # 读取指定sheet页
            df = pd.read_excel(file_path, sheet_name=sheet_name)
            logger.info(f"成功读取Excel文件sheet页: {file_path} - {sheet_name}")
            return df
                
        except Exception as e:
            logger.error(f"读取Excel文件sheet页失败: {str(e)}")
            return None
    
    def get_sheet_names(self, file_path: str) -> Optional[List[str]]:
        """
        获取Excel文件中的所有sheet页名称
        
        Args:
            file_path: Excel文件路径
            
        Returns:
            List[str]: sheet页名称列表，如果读取失败则返回None
        """
        try:
            if not self._check_file_exists(file_path):
                return None
            
            # 获取所有sheet页名称
            excel_file = pd.ExcelFile(file_path)
            sheet_names = excel_file.sheet_names
            logger.info(f"成功获取Excel文件sheet页列表: {file_path}")
            return sheet_names
                
        except Exception as e:
            logger.error(f"获取Excel文件sheet页列表失败: {str(e)}")
            return None

# 创建全局单例实例
excel_reader = ExcelReader() 