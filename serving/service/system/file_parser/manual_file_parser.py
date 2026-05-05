from typing import Optional, BinaryIO, List, Dict, Any
import os
import json
from pathlib import Path
from .base_file_parser import BaseFileParser
from service.system.file_reader_main import file_reader
from service.system.file_reader import pdf_reader
from service.system.openai_service import openai_service
import fitz  # PyMuPDF
import re
from util.logging.logger import get_logger

logger = get_logger(__name__)

class ManualFileParser(BaseFileParser):
    """
    Manual说明书解析器：支持PDF说明书的内容切片、结构化处理和批量导入。
    """
    def __init__(self):
        self.link_threshold = 3
        self.max_toc_pages = 10
        
    def parse(self, file: BinaryIO, filename: str):
        # 1. 保存PDF文件
        file_path = self._save_file(file, filename)
        if not file_path:
            logger.error(f"文件保存失败: {filename}")
            return {"success": False, "msg": "文件保存失败"}

        # 2. 目录切片与结构化处理
        try:
            slices = self._manual_slice_pdf(file_path)
        except Exception as e:
            logger.error(f"PDF切片失败: {e}")
            return {"success": False, "msg": f"PDF切片失败: {e}"}
        if not slices:
            logger.error(f"PDF切片结构化失败: {file_path}")
            return {"success": False, "msg": "PDF切片结构化失败"}

        # 3. 为每个切片创建单独的PDF文件
        slices = self._create_slice_pdfs(file_path, slices)

        # 4. 使用大模型优化内容描述
        slices = self._optimize_descriptions_with_llm(slices, file_path)

        # 5. 组织知识库导入数据
        texts = [item["description"] for item in slices]
        metadatas = [item["metadata"] for item in slices]

        # 6. 返回解析结果，包括切片PDF文件路径
        logger.info("PDF说明书解析成功")
        return {
            "success": True,
            "texts": texts,
            "metadatas": metadatas,
            "slice_files": [item.get("slice_pdf_path") for item in slices]
        }

    def _create_slice_pdfs(self, pdf_path: str, slices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        为每个切片创建单独的PDF文件
        
        Args:
            pdf_path: 原始PDF文件路径
            slices: 切片信息列表，需要包含target_page信息
            
        Returns:
            List[Dict[str, Any]]: 更新后的切片信息，包含slice_pdf_path字段
        """
        try:
            # 打开原始PDF文档
            doc = fitz.open(pdf_path)
            total = len(slices)
            logger.debug(f"[manual_slice_pdf] 共 {total} 个章节需要切片")

            # 获取文件名（不包含扩展名）
            base_name = Path(pdf_path).stem

            # 为每个切片创建单独的PDF文件
            for i, slice_info in enumerate(slices):
                try:
                    # 创建新的PDF文档
                    new_doc = fitz.open()
                    
                    # 确定切片的页码范围
                    # 起始页是当前切片的目标页面（已经是1-based）
                    start_page = slice_info.get("target_page", 1) - 1  # 转换为0-based索引
                    
                    # 结束页是下一个切片的目标页面，如果没有下一个切片，则是文档最后一页
                    if i < len(slices) - 1:
                        end_page = slices[i + 1].get("target_page", len(doc) + 1) - 1  # 转换为0-based索引
                    else:
                        end_page = len(doc)
                    
                    # 确保页码范围有效
                    start_page = max(0, start_page)
                    end_page = min(len(doc), end_page)
                    
                    # 特殊处理：确保不会创建空的PDF文件
                    if start_page >= end_page:
                        # 如果起始页大于等于结束页，只取起始页
                        end_page = start_page + 1
                    
                    # 将指定页面插入到新文档中
                    new_doc.insert_pdf(doc, from_page=start_page, to_page=end_page-1)
                    
                    # 生成切片PDF文件名
                    slice_id = slice_info.get("metadata", {}).get("slice_id", f"slice_{i}")
                    slice_pdf_name = f"{base_name}_{slice_id}.pdf"
                    
                    # 保存切片PDF到临时目录
                    slice_pdf_path = file_reader.save_dir / slice_pdf_name
                    new_doc.save(str(slice_pdf_path))
                    new_doc.close()
                    
                    # 更新切片信息，添加切片PDF路径及页码范围
                    slice_info["metadata"]["page_start"] = start_page + 1  # 转回1-based，含
                    slice_info["metadata"]["page_end"] = end_page          # 1-based末页，含
                    slice_info["slice_pdf_path"] = str(slice_pdf_path)
                    slice_name = slice_info.get("metadata", {}).get("slice_id", f"slice_{i}")
                    logger.debug(f"[manual_slice_pdf] 切片进度: {i+1}/{total} - {slice_name}")

                except Exception as e:
                    logger.error(f"创建切片PDF失败 (切片 {i}): {str(e)}")
                    slice_info["slice_pdf_path"] = None
                    
            doc.close()
            return slices
            
        except Exception as e:
            logger.error(f"处理PDF切片失败: {str(e)}")
            return slices

    def _save_file(self, file: BinaryIO, filename: str) -> Optional[str]:
        return file_reader.save_file(file, filename)
        
    def _detect_toc_pages(self, doc: fitz.Document) -> List[int]:
        """
        基于链接数量自动检测目录页
        一般前几页中存在较多点击跳转的内容，就可以认为它们是目录页
        
        Args:
            doc: PyMuPDF文档对象
            
        Returns:
            目录页码列表 (1-based)
        """
        toc_pages = []
        
        # 遍历前几页查找目录页
        for page_num in range(min(self.max_toc_pages, len(doc))):
            page = doc.load_page(page_num)
            links = page.get_links()
            
            # 如果当前页链接数超过阈值，则认为是目录页
            if len(links) >= self.link_threshold:
                toc_pages.append(page_num + 1)
            # 如果已经开始识别目录页，但当前页不是目录页，则停止搜索
            elif toc_pages:
                break
        
        # 如果没有找到目录页，回退到固定页数方法
        if not toc_pages:
            toc_pages = list(range(1, min(6, len(doc) + 1)))
            logger.debug(f"[manual_slice_pdf] 未检测到目录页，回退到固定页: {toc_pages}")
            return toc_pages
        logger.debug(f"[manual_slice_pdf] 检测到的目录页: {toc_pages}")
        return toc_pages

    def _manual_slice_pdf(self, pdf_path: str) -> List[Dict[str, Any]]:
        """
        简化版：自动提取PDF目录页，解析目录，按目录切片，生成结构化描述。
        返回: [{description, metadata, ...}]
        """
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        toc_pymupdf = doc.get_toc()
        toc_pymupdf_len = len(toc_pymupdf) if toc_pymupdf else 0
        logger.debug(f"[manual_slice_pdf] 入口: PDF总页数={total_pages}, PyMuPDF书签数(doc.get_toc())={toc_pymupdf_len}")

        # 1. 目录页自动检测（基于链接数量）
        toc_pages = self._detect_toc_pages(doc)

        # 2. 解析目录结构
        toc_items = self._parse_toc_items(doc, toc_pages)
        logger.debug(f"[manual_slice_pdf] 解析到的章节数量: {len(toc_items)}")
        if len(toc_items) == 0:
            logger.warning("[manual_slice_pdf] 解析到的章节数为0，将退化为全书一个切片")
        if not toc_items:
            # 若未能解析目录，退化为全书一个切片
            title = Path(pdf_path).stem
            return [{
                "description": f"这是关于{title}的操作手册章节，包含设备功能、操作步骤、故障处理等内容。",
                "metadata": {
                    "model": title,
                    "manual_type": f"{title}机型操作手册",
                    "slice_id": "all"
                }
            }]
        
        # 3. 生成切片结构
        slices = []
        slice_ids = set()  # 用于跟踪已使用的slice_id
        for item in toc_items:
            # 从目标页面提取内容作为描述
            target_page = item.get("target_page", 1) - 1  # 转换为0-based索引
            page = doc.load_page(min(target_page, len(doc) - 1))  # 确保不越界
            content = page.get_text().strip()
            
            # 如果页面内容为空，使用默认描述
            desc = content if content else f"章节：{item['title']}。本章节介绍设备相关功能、操作方法、注意事项及常见故障。"
            
            # 生成slice_id
            slice_id = item.get('section_number', item.get('title', ''))
            
            # 如果slice_id已经存在，则跳过该项
            if slice_id in slice_ids:
                continue
            
            # 添加到已使用的slice_id集合中
            slice_ids.add(slice_id)
            
            meta = {
                "model": Path(pdf_path).stem,
                "manual_type": f"{Path(pdf_path).stem}机型操作手册",
                "slice_id": slice_id
            }
            slices.append({
                "description": desc,
                "metadata": meta,
                "target_page": item.get("target_page")  # 保存目标页面信息供后续使用
            })
        return slices

    def _parse_toc_items(self, doc: fitz.Document, toc_pages: List[int]) -> List[Dict[str, Any]]:
        """
        从链接信息中解析目录项
        返回[{title, section_number}]
        """
        items = []
        
        # 遍历所有目录页
        for page_num in toc_pages:
            page = doc.load_page(page_num - 1)  # 转换为0-based索引
            links = page.get_links()
            
            # 遍历页面上的所有链接
            for link in links:
                # 检查是否是跳转到文档内部页面的链接
                if "page" in link:
                    # 获取链接区域的文本内容作为标题
                    rect = link.get("from")
                    if rect:
                        # 提取链接区域的文本
                        title_text = page.get_text("text", clip=rect).strip()
                        if title_text:
                            # 清理文本，移除多余的行号或其他无关字符
                            # 移除开头的数字和换行符（可能来自于页码或其他编号）
                            title_text = re.sub(r'^\d+\s*\n', '', title_text)
                            
                            # 从标题中提取章节编号（如"3.1 机器总体布局介绍"中的"3.1"）
                            section_number = ""
                            title = title_text
                            # 使用正则表达式匹配章节编号
                            # 优化：处理章节编号在标题末尾的情况，如"安全注意事项 .............................................................. 3.1-1"
                            match = re.match(r'^(.+?)\s*\.{3,}\s*(\d+(?:\.\d+){0,3}(?:-\d+)?)\s*$', title_text)
                            if match:
                                title = match.group(1)
                                section_number = match.group(2)
                            else:
                                # 原始匹配方式（章节编号在开头）
                                match = re.match(r'^(\d+(?:\.\d+){0,3})\b\s*(.+)', title_text)
                                if match:
                                    section_number = match.group(1)
                                    title = match.group(2)
                            
                            items.append({
                                "section_number": section_number,
                                "title": title,
                                "target_page": link["page"] + 1  # 转换为1-based页码
                            })
        
        return items

    def _optimize_descriptions_with_llm(self, slices: List[Dict[str, Any]], pdf_path: str) -> List[Dict[str, Any]]:
        """
        使用大模型优化切片的内容描述，参考manual_slicer.py中的实现方式
        
        Args:
            slices: 切片列表
            pdf_path: PDF文件路径
            
        Returns:
            优化后的切片列表
        """
        try:
            total = len(slices)
            logger.debug(f"[manual_slice_pdf] 共 {total} 个切片需要LLM优化")

            # 遍历每个切片，使用大模型优化描述
            for i, slice_info in enumerate(slices):
                slice_id = slice_info.get("metadata", {}).get("slice_id", f"slice_{i}")
                try:
                    content = slice_info.get("description", "")

                    # 构造提示词，参考manual_slicer.py中的实现
                    title = slice_id
                    prompt = f"""请为这个设备手册章节生成一个专门用于向量化检索的功能描述。

⚠️ 重要说明：这个描述将被用于：
1. 向量化embedding处理
2. 与用户查询进行语义匹配
3. 故障诊断和技术支持的检索
4. 当用户查询匹配时，直接推送对应的PDF切片

🎯 核心要求：
1. **功能概述**：明确说明这个功能是什么，解决什么问题
2. **关键操作**：列出主要的操作步骤和设置方法
3. **故障相关**：可能出现的问题、警报、异常情况
4. **技术术语**：包含相关的专业术语和参数名称
5. **应用场景**：什么时候需要使用这个功能

🔍 检索优化要求：
- 使用丰富的同义词和相关术语
- 包含用户可能搜索的问题描述
- 涵盖故障现象和解决方案的关键词
- 便于语义匹配和相似度计算
- 不要包含页码信息

📝 格式要求：
- 150-250字长度
- 语言专业但易懂
- 结构清晰，信息密度高
- 适合向量化处理
- 专注于检索关键词，不是给人阅读的整理

章节信息：
- 标题：{title}

章节内容：
{content}

请生成描述："""
  
                    # 构建消息
                    messages = [{"role": "user", "content": prompt}]

                    # 使用openai_service调用大模型
                    try:
                        response = openai_service.chat_completion(
                            messages=messages,
                            temperature=0.1,
                            max_tokens=600
                        )

                        # 提取响应内容
                        optimized_description = response.choices[0].message.content.strip()
                        if optimized_description:
                            slice_info["description"] = optimized_description
                        logger.debug(f"[manual_slice_pdf] LLM优化进度: {i+1}/{total} - {slice_id}")

                    except Exception as e:
                        logger.warning(f"LLM优化失败，保留原描述: {slice_id}, error={e}")
                        # 保持原描述，不抛出异常

                except Exception as e:
                    logger.warning(f"LLM优化失败，保留原描述: {slice_id}, error={e}")
                    # 保留原始描述
                    
            return slices
            
        except Exception as e:
            logger.error(f"使用大模型优化描述失败: {str(e)}")
            # 出现错误时返回原始切片列表
            return slices
