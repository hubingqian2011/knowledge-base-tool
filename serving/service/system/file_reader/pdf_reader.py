# -*- coding: utf-8 -*-
from typing import Optional, List
import PyPDF2
from pdf2image import convert_from_path
import os
from PIL import Image

from .base_reader import BaseReader
from util.logging.logger import get_logger

logger = get_logger(__name__)

class PDFReader(BaseReader):
    """PDF文件读取器类"""
    
    def read_text(self, file_path: str) -> Optional[str]:
        """
        读取PDF文件中的纯文本内容
        
        Args:
            file_path: PDF文件路径
            
        Returns:
            str: PDF文件中的文本内容，如果读取失败则返回None
        """
        try:
            if not self._check_file_exists(file_path):
                return None
                
            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text_content = []
                
                # 读取每一页的内容
                for page in pdf_reader.pages:
                    text_content.append(page.extract_text())
                
                # 合并所有页面的文本
                full_text = '\n'.join(text_content)
                logger.info(f"成功读取PDF文件文本内容: {file_path}")
                return full_text
                
        except Exception as e:
            logger.error(f"读取PDF文件文本内容失败: {str(e)}")
            return None

    def pdf_to_images(self, file_path: str,
                      page_start: int = None, page_end: int = None) -> Optional[List[Image.Image]]:
        """
        将PDF每页转为PIL Image对象列表，不保存文件
        Args:
            file_path: PDF文件路径
            page_start: 起始页（1-based，含），None表示从第1页开始
            page_end: 结束页（1-based，含），None表示到最后一页
        Returns:
            List[Image.Image]: 图片对象列表
        """
        try:
            if not self._check_file_exists(file_path):
                return None
            kwargs = {}
            if page_start is not None:
                kwargs["first_page"] = page_start
            if page_end is not None:
                kwargs["last_page"] = page_end
            images = convert_from_path(file_path, **kwargs)
            logger.info(
                f"PDF转图片成功: {file_path} -> {len(images)} images"
                + (f" (pages {page_start}-{page_end})" if page_start or page_end else "")
            )
            return images
        except Exception as e:
            logger.error(f"PDF转图片失败: {str(e)}")
            return None

# 创建全局单例实例
pdf_reader = PDFReader() 