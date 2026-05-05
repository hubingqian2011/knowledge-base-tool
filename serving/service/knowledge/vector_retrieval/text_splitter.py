from typing import List, Dict, Any

class TextSplitter:
    """文本分块处理类"""
    
    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        separator: str = "\n",
        keep_separator: bool = True
    ):
        """
        初始化文本分块器
        
        Args:
            chunk_size: 每个块的最大字符数
            chunk_overlap: 块之间的重叠字符数
            separator: 分隔符
            keep_separator: 是否保留分隔符
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separator = separator
        self.keep_separator = keep_separator
    
    def split_text(self, text: str) -> List[str]:
        """
        将文本分割成块
        
        Args:
            text: 输入文本
            
        Returns:
            List[str]: 文本块列表
        """
        if not text:
            return []
            
        # 使用分隔符分割文本
        segments = text.split(self.separator)
        if not self.keep_separator:
            segments = [s.strip() for s in segments]
        
        chunks = []
        current_chunk = []
        current_length = 0
        
        for segment in segments:
            segment_length = len(segment)
            
            # 如果单个段落超过块大小，需要进一步分割
            if segment_length > self.chunk_size:
                if current_chunk:
                    chunks.append(self.separator.join(current_chunk))
                    current_chunk = []
                    current_length = 0
                
                # 分割长段落
                sub_chunks = self._split_long_segment(segment)
                chunks.extend(sub_chunks)
                continue
            
            # 如果添加当前段落会超出块大小
            if current_length + segment_length > self.chunk_size:
                if current_chunk:
                    chunks.append(self.separator.join(current_chunk))
                    # 保留重叠部分
                    overlap_start = max(0, len(current_chunk) - self.chunk_overlap)
                    current_chunk = current_chunk[overlap_start:]
                    current_length = sum(len(s) for s in current_chunk)
            
            current_chunk.append(segment)
            current_length += segment_length
        
        # 添加最后一个块
        if current_chunk:
            chunks.append(self.separator.join(current_chunk))
        
        return chunks
    
    def _split_long_segment(self, segment: str) -> List[str]:
        """
        分割长段落
        
        Args:
            segment: 长段落文本
            
        Returns:
            List[str]: 分割后的文本块列表
        """
        chunks = []
        start = 0
        
        while start < len(segment):
            end = min(start + self.chunk_size, len(segment))
            
            # 如果不是最后一块，尝试在句子边界分割
            if end < len(segment):
                # 查找最后一个句子结束符
                last_period = segment.rfind(".", start, end)
                last_question = segment.rfind("?", start, end)
                last_exclamation = segment.rfind("!", start, end)
                
                # 找到最接近块末尾的句子结束符
                last_sentence_end = max(last_period, last_question, last_exclamation)
                
                if last_sentence_end > start:
                    end = last_sentence_end + 1
            
            chunks.append(segment[start:end].strip())
            start = end - self.chunk_overlap
        
        return chunks
    
    def split_documents(self, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        分割文档列表
        
        Args:
            documents: 文档列表，每个文档包含 text 字段
            
        Returns:
            List[Dict[str, Any]]: 分割后的文档列表
        """
        split_docs = []
        
        for doc in documents:
            text = doc.get("text", "")
            if not text:
                continue
                
            chunks = self.split_text(text)
            
            for i, chunk in enumerate(chunks):
                chunk_doc = doc.copy()
                chunk_doc["text"] = chunk
                chunk_doc["chunk_id"] = i
                split_docs.append(chunk_doc)
        
        return split_docs 