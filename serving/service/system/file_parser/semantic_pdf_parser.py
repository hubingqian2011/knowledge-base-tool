from typing import Optional, BinaryIO, List, Dict, Any
import os
import json
from pathlib import Path
from .base_file_parser import BaseFileParser
from service.system.file_reader_main import file_reader
from util.logging.logger import get_logger
from service.knowledge.vector_retrieval.embedding.openai_embedding import OpenaiEmbedding
import fitz  # PyMuPDF
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

logger = get_logger(__name__)

class SemanticPDFParser(BaseFileParser):
    """
    语义PDF解析器：支持PDF文件的语义切分、结构化处理和批量导入。
    采用基于embedding的语义切分方式对PDF完整文本进行切分。
    """
    
    def __init__(self, chunk_size: int = 1000, similarity_threshold: float = 0.85):
        self.chunk_size = chunk_size
        self.similarity_threshold = similarity_threshold
        self.embedding_client = OpenaiEmbedding()
        
    def parse(self, file: BinaryIO, filename: str):
        # 1. 保存PDF文件
        file_path = self._save_file(file, filename)
        if not file_path:
            logger.error(f"文件保存失败: {filename}")
            return {"success": False, "msg": "文件保存失败"}

        # 2. 提取PDF文本并进行语义切分
        try:
            chunks = self._semantic_chunk_pdf_text(file_path)
        except Exception as e:
            logger.error(f"PDF文本语义切分失败: {e}")
            return {"success": False, "msg": f"PDF文本语义切分失败: {e}"}
        if not chunks:
            logger.error(f"PDF文本语义切分结果为空: {file_path}")
            return {"success": False, "msg": "PDF文本语义切分结果为空"}

        # 3. 为每个语义块创建单独的PDF文件
        chunks = self._create_semantic_pdfs(file_path, chunks)

        # 4. 组织知识库导入数据
        texts = [item["text"] for item in chunks]
        metadatas = [item["metadata"] for item in chunks]

        # 5. 返回解析结果，包括切片PDF文件路径
        logger.info("PDF文件语义解析成功")
        return {
            "success": True,
            "texts": texts,
            "metadatas": metadatas,
            "slice_files": [item.get("slice_pdf_path") for item in chunks]
        }

    def _save_file(self, file: BinaryIO, filename: str) -> Optional[str]:
        return file_reader.save_file(file, filename)
        
    def _semantic_chunk_pdf_text(self, pdf_path: str) -> List[Dict[str, Any]]:
        """
        提取PDF文本并按语义进行切分
        
        Args:
            pdf_path: PDF文件路径
            
        Returns:
            List[Dict[str, Any]]: 包含语义块和元数据的列表
        """
        doc = fitz.open(pdf_path)
        pages_text = []
        
        # 提取所有页面的文本和位置信息
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            blocks = page.get_text("dict")["blocks"]
            page_text = ""
            text_positions = []
            
            # 提取文本块和位置信息
            for block in blocks:
                if "lines" in block:
                    for line in block["lines"]:
                        for span in line["spans"]:
                            text = span["text"]
                            bbox = span["bbox"]  # (x0, y0, x1, y1)
                            page_text += text
                            text_positions.append({
                                "text": text,
                                "bbox": bbox,
                                "page_num": page_num
                            })
            
            pages_text.append({
                "page_num": page_num,
                "text": page_text,
                "text_positions": text_positions
            })
        
        doc.close()
        
        # 合并所有文本
        full_text = "\n".join([page["text"] for page in pages_text])
        
        # 初步按固定大小切分文本
        initial_chunks = self._initial_chunk_text(full_text)
        
        # 使用语义相似度进行优化切分
        semantic_chunks = self._refine_chunks_with_semantics(initial_chunks)
        
        # 添加元数据和位置信息
        chunks = []
        for i, chunk_text in enumerate(semantic_chunks):
            meta = {
                "model": Path(pdf_path).stem,
                "file_type": "pdf",
                "chunk_id": f"semantic_chunk_{i}"
            }
            
            chunks.append({
                "text": chunk_text,
                "metadata": meta
            })
            
        return chunks

    def _initial_chunk_text(self, text: str) -> List[str]:
        """
        初步按固定大小切分文本
        
        Args:
            text: 完整文本
            
        Returns:
            List[str]: 初步切分的文本块列表
        """
        chunks = []
        start = 0
        
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            chunk_text = text[start:end]
            chunks.append(chunk_text)
            start = end
            
        return chunks

    def _refine_chunks_with_semantics(self, chunks: List[str]) -> List[str]:
        """
        使用语义相似度优化文本块切分
        
        Args:
            chunks: 初步切分的文本块列表
            
        Returns:
            List[str]: 优化后的语义块列表
        """
        if len(chunks) <= 1:
            return chunks
            
        # 批量生成嵌入向量，避免Too Many Requests错误
        embedding_vectors = self._batch_encode_embeddings(chunks)
        
        # 计算相邻块之间的相似度
        semantic_chunks = []
        current_chunk = chunks[0]
        
        for i in range(1, len(chunks)):
            # 计算当前块与下一块的余弦相似度
            similarity = cosine_similarity(
                embedding_vectors[i-1:i], 
                embedding_vectors[i:i+1]
            )[0][0]
            
            # 如果相似度高于阈值，则合并块
            if similarity >= self.similarity_threshold:
                current_chunk += " " + chunks[i]
            else:
                # 如果当前块过长，进行切分
                if len(current_chunk) > self.chunk_size * 2:
                    # 按句子切分并重新组合
                    sentences = current_chunk.split(". ")
                    sub_chunk = ""
                    for sentence in sentences:
                        if len(sub_chunk) + len(sentence) < self.chunk_size:
                            sub_chunk += sentence + ". "
                        else:
                            semantic_chunks.append(sub_chunk.strip())
                            sub_chunk = sentence + ". "
                    if sub_chunk:
                        semantic_chunks.append(sub_chunk.strip())
                else:
                    semantic_chunks.append(current_chunk)
                current_chunk = chunks[i]
        
        # 添加最后一个块
        semantic_chunks.append(current_chunk)
        
        return semantic_chunks
    
    def _batch_encode_embeddings(self, texts: List[str], batch_size: int = 10) -> np.ndarray:
        """
        批量生成文本嵌入向量，避免API速率限制
        
        Args:
            texts: 文本列表
            batch_size: 批次大小
            
        Returns:
            np.ndarray: 嵌入向量数组
        """
        all_embeddings = []
        
        # 分批处理文本
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i+batch_size]
            try:
                # 生成当前批次的嵌入向量
                batch_embeddings = self.embedding_client.encode(
                    inputs=batch_texts,
                    max_retries=5,  # 增加重试次数
                    base_delay=2.0  # 增加基础延迟
                )
                # 提取embedding数组并添加到结果中
                all_embeddings.extend([emb.embedding for emb in batch_embeddings])
            except Exception as e:
                logger.error(f"批次 {i//batch_size + 1} 嵌入向量生成失败: {str(e)}")
                # 如果批次处理失败，为每个文本生成单独的嵌入向量
                for text in batch_texts:
                    try:
                        single_embedding = self.embedding_client.encode(
                            inputs=[text],
                            max_retries=3,
                            base_delay=1.0
                        )
                        all_embeddings.append(single_embedding[0].embedding)
                    except Exception as single_e:
                        logger.error(f"单个文本嵌入向量生成失败: {str(single_e)}")
                        # 如果单个文本也失败，添加零向量作为占位符
                        # 从配置中获取向量维度
                        from config.config import VECTOR_DIMENSION
                        all_embeddings.append(np.zeros(VECTOR_DIMENSION))
                        
        return np.array(all_embeddings)

    def _create_semantic_pdfs(self, pdf_path: str, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        为每个语义块创建单独的PDF文件
        
        Args:
            pdf_path: 原始PDF文件路径
            chunks: 语义块信息列表
            
        Returns:
            List[Dict[str, Any]]: 更新后的语义块信息，包含slice_pdf_path字段
        """
        try:
            # 打开原始PDF文档
            doc = fitz.open(pdf_path)
            
            # 获取文件名（不包含扩展名）
            base_name = Path(pdf_path).stem
            
            # 为每个语义块创建单独的PDF文件
            for i, chunk_info in enumerate(chunks):
                try:
                    # 创建新的PDF文档
                    new_doc = fitz.open()
                    
                    # 确定语义块的页面范围
                    # 由于语义切分不直接关联到页面位置，我们采用一种启发式方法：
                    # 1. 对于第一个块，包含前几页
                    # 2. 对于中间块，包含前后几页（允许重叠）
                    # 3. 对于最后一个块，包含后几页
                    total_pages = len(doc)
                    if total_pages == 0:
                        # 如果没有页面，复制所有页面（应该不会发生）
                        new_doc.insert_pdf(doc)
                    else:
                        # 计算页面范围
                        if len(chunks) == 1:
                            # 只有一个块，包含所有页面
                            start_page = 0
                            end_page = total_pages
                        else:
                            # 多个块，根据块的索引确定页面范围
                            # 这是一个简化的实现，实际应用中可以进一步优化
                            pages_per_chunk = max(1, total_pages // len(chunks))
                            overlap_pages = min(2, pages_per_chunk)
                            
                            # 计算起始和结束页面
                            start_page = max(0, i * pages_per_chunk - overlap_pages)
                            end_page = min(total_pages, (i + 1) * pages_per_chunk + overlap_pages)
                            
                            # 确保至少包含一页
                            if start_page >= end_page:
                                start_page = max(0, i * pages_per_chunk)
                                end_page = min(total_pages, start_page + 1)
                        
                        # 将指定页面范围插入到新文档中
                        new_doc.insert_pdf(doc, from_page=start_page, to_page=end_page-1)
                    
                    # 生成切片PDF文件名
                    chunk_id = chunk_info.get("metadata", {}).get("chunk_id", f"semantic_chunk_{i}")
                    slice_pdf_name = f"{base_name}_{chunk_id}.pdf"
                    
                    # 保存切片PDF到临时目录
                    slice_pdf_path = file_reader.save_dir / slice_pdf_name
                    new_doc.save(str(slice_pdf_path))
                    new_doc.close()
                    
                    # 更新语义块信息，添加切片PDF路径
                    chunk_info["slice_pdf_path"] = str(slice_pdf_path)
                    
                except Exception as e:
                    logger.error(f"创建语义切片PDF失败 (语义块 {i}): {str(e)}")
                    chunk_info["slice_pdf_path"] = None
                    
            doc.close()
            return chunks
            
        except Exception as e:
            logger.error(f"处理PDF语义切片失败: {str(e)}")
            return chunks