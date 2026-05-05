from typing import Optional, BinaryIO
import re
from datetime import datetime
from pathlib import Path

from .base_file_parser import BaseFileParser
from service.system.file_reader_main import file_reader
from service.system.file_reader import docx_reader

from util.logging.logger import get_logger

logger = get_logger(__name__)

class PrincipleFileParser(BaseFileParser):
    """
    原理文档解析器：支持docx原理文档的内容读取、结构化处理和批处理。
    """
    def parse(self, file: BinaryIO, filename: str):
        # 1. 保存文件
        file_path = self._save_file(file, filename)
        if not file_path:
            logger.error(f"文件保存失败: {filename}")
            return {"success": False, "msg": "文件保存失败"}

        # 2. 读取内容
        content = self._read_file(file_path)
        if not content:
            logger.error(f"文件内容读取失败: {file_path}")
            return {"success": False, "msg": "文件内容读取失败"}

        # 3. 结构化处理
        structured = self._structure_content(filename, content)
        description = structured["content"]["description"]
        category = structured["category"]
        metadata = {"category": category}

        # 4. 返回解析结果
        logger.info("原理文档解析成功")
        return {
            "success": True,
            "texts": [description],
            "metadatas": [metadata]
        }

    def _save_file(self, file: BinaryIO, filename: str) -> Optional[str]:
        return file_reader.save_file(file, filename)

    def _read_file(self, file_path: str) -> Optional[str]:
        return file_reader.read_file(file_path=file_path)

    def _structure_content(self, filename: str, content: str) -> dict:
        """
        结构化处理原理文档内容，参考principle_docs_processor的逻辑。
        """
        cleaned_title = self._clean_filename(filename)
        category = self._categorize_principle(cleaned_title)
        keywords = self._extract_keywords(cleaned_title)
        return {
            "title": cleaned_title,
            "original_filename": filename,
            "category": category,
            "content": {
                "description": f"这是关于{cleaned_title}的原理说明文档，属于{category}类别。该文档提供了相关的技术原理、操作方法、故障分析等信息，可用于设备维护、故障诊断和技术支持。",
                "keywords": keywords,
                "type": "原理文档"
            },
            "metadata": {
                "source_file": filename,
                "file_size": None,
                "processing_time": datetime.now().isoformat(),
                "data_type": "principle",
                "language": "zh-CN"
            },
            "vector_ready": {
                "content_for_embedding": f"{cleaned_title} {category} 原理文档 这是关于{cleaned_title}的原理说明文档，属于{category}类别。该文档提供了相关的技术原理、操作方法、故障分析等信息，可用于设备维护、故障诊断和技术支持。",
                "search_keywords": keywords + [category, "原理", "文档"],
                "relevance_score": 1.0
            }
        }

    def _clean_filename(self, filename: str) -> str:
        name_without_ext = Path(filename).stem
        cleaned_name = re.sub(r'^\d+[A-Za-z]*-', '', name_without_ext)
        if cleaned_name == name_without_ext:
            return name_without_ext
        return cleaned_name

    def _categorize_principle(self, title: str) -> str:
        title_lower = title.lower()
        if any(keyword in title_lower for keyword in ['ju', 'ma', '五代机', '全电动', '多组分']):
            return "机器系列"
        elif any(keyword in title_lower for keyword in ['电', '继电器', '接触器', '开关', '电源', '相序', '漏电']):
            return "电气系统"
        elif any(keyword in title_lower for keyword in ['热', '加热', '温度', '热流道', '热电偶']):
            return "加热系统"
        elif any(keyword in title_lower for keyword in ['液压', '油', '换向阀', '压力']):
            return "液压系统"
        elif any(keyword in title_lower for keyword in ['地基', '吊装', '重量', '承重', '结构', '外形']):
            return "机械结构"
        elif any(keyword in title_lower for keyword in ['接线', '接口', '进线', '水气', '连接']):
            return "接口连接"
        elif any(keyword in title_lower for keyword in ['故障', '检测', '检查', '调整', '更换']):
            return "故障诊断"
        elif any(keyword in title_lower for keyword in ['说明', '介绍', '安装', '设定', '操作', '画面']):
            return "操作说明"
        elif any(keyword in title_lower for keyword in ['参数', '规格', '用量', '技术']):
            return "技术参数"
        else:
            return "其他原理"

    def _extract_keywords(self, title: str):
        keywords = [title]
        title_lower = title.lower()
        if 'ju' in title_lower or '五代机' in title:
            keywords.extend(['JU', '五代机', '注塑机'])
        if 'ma' in title_lower:
            keywords.extend(['MA', '注塑机'])
        if any(word in title for word in ['电', '继电器', '接触器']):
            keywords.extend(['电气', '控制', '系统'])
        if any(word in title for word in ['热', '加热', '温度']):
            keywords.extend(['加热', '温控', '系统'])
        if any(word in title for word in ['液压', '油']):
            keywords.extend(['液压', '系统'])
        if any(word in title for word in ['故障', '检测', '检查']):
            keywords.extend(['故障', '诊断', '维修'])
        if any(word in title for word in ['安装', '接线', '连接']):
            keywords.extend(['安装', '连接', '配置'])
        return list(set(keywords))
