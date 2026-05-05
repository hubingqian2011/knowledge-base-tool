# -*- coding: utf-8 -*-
"""
说明书知识库处理器
处理说明书类型的知识库,包括:
- PDF 目录切片
- 图片自动提取和预览
- 图片关联和检索
"""
import os
from typing import List, Dict, Any, Optional
from pathlib import Path

from util.logging.logger import get_logger
from config.config import COLLECTION_MANUAL

from .base_handler import BaseKnowledgeHandler

logger = get_logger(__name__)
INGESTION_OUTPUT_ROOT = Path("/app/ingestion/output")


class ManualKnowledgeHandler(BaseKnowledgeHandler):
    """
    说明书知识库处理器

    特化功能:
    1. 支持PDF目录切片
    2. 自动生成图片预览并存入SQL
    3. 搜索时自动补充图片URL
    4. 支持章节级别的检索
    """

    # ==================== 钩子方法重写 ====================

    def _should_save_file(self) -> bool:
        result = self.collection_type in [COLLECTION_MANUAL, "semantic_pdf"]
        logger.debug(f"[manual] _should_save_file: collection_type='{self.collection_type}', COLLECTION_MANUAL='{COLLECTION_MANUAL}', result={result}")
        return result
        
    def _should_save_slice_files(self) -> bool:
        """
        Manual处理器需要保存PDF切片文件

        Returns:
            bool: True - 保存切片文件
        """
        return self.collection_type in [COLLECTION_MANUAL, "semantic_pdf"]

    # ==================== 必须实现的抽象方法 ====================

    def ingest(
        self,
        files: List[Any],
        filenames: List[str],
        metadata: Optional[Dict] = None
    ) -> List[str]:
        """
        说明书数据入库（GraphRAG 架构）

        通过 ingestion/ 的 ingest_manual() 或 ingest_word() 执行 GraphRAG 9步入库，
        替代旧版 ManualFileParser 的简单切片。

        Args:
            files: 文件流列表
            filenames: 文件名列表
            metadata: 额外的元数据（可含 model, window, overlap 等）

        Returns:
            List[str]: 占位（GraphRAG 入库通过 ingestion/ 独立写三库，
                       不走 serving 的 add_texts）
        """
        import sys
        import os
        for p in ["/app/ingestion", "/app"]:
            if p not in sys.path:
                sys.path.insert(0, p)

        results = []
        meta = metadata or {}

        for file_stream, filename in zip(files, filenames):
            ext = Path(filename).suffix.lower()
            tmp_path = os.path.join(os.getenv("TEMP_FILES_DIR", "/tmp"), filename)

            try:
                content = file_stream.read() if hasattr(file_stream, "read") else file_stream
                with open(tmp_path, "wb") as f:
                    f.write(content)

                model = meta.get("model", Path(filename).stem)
                window = int(meta.get("window", 2))
                overlap = int(meta.get("overlap", 1))

                if ext in (".docx", ".doc"):
                    from word_ingest_agent import ingest_word
                    result = ingest_word(
                        docx_path=tmp_path, model_name=model,
                        window=window, overlap=overlap,
                    )
                else:
                    from manual_ingest_agent import ingest_manual
                    result = ingest_manual(
                        pdf_path=tmp_path, model_name=model,
                        window=window, overlap=overlap,
                    )

                if result.get("success"):
                    logger.info(f"GraphRAG 入库完成: {filename}")
                    results.append(filename)
                else:
                    logger.error(f"GraphRAG 入库失败: {filename}, {result.get('msg')}")

            except Exception as e:
                logger.error(f"GraphRAG 入库异常: {filename}, {e}", exc_info=True)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

        logger.info(f"说明书入库完成（GraphRAG），处理 {len(results)} 个文件")
        # 返回空列表：GraphRAG 通过 ingestion/ 独立写三库，
        # 不经过 serving 的 add_texts()，没有 serving 格式的 document_ids
        return []

    def search(
        self,
        query: str,
        top_k: int,
        metadata: Optional[Dict] = None,
        use_rerank: bool = True,
        rejected_workorder_ids: Optional[List[str]] = None,
        query_vector: Optional[List[float]] = None
    ) -> List[Dict[str, Any]]:
        """
        说明书搜索

        搜索后自动补充图片ID列表

        Args:
            query: 查询文本
            top_k: 返回结果数量
            metadata: 元数据过滤条件
            use_rerank: 是否使用重排序
            rejected_workorder_ids: 要排除的派工单号（说明书集合一般不用，接口统一）

        Returns:
            List[Dict[str, Any]]: 搜索结果列表,每个结果包含image_ids
        """
        # 先执行标准向量搜索
        results = self.similarity_search(
            query=query,
            collection_name=self.collection_name,
            top_k=top_k,
            use_rerank=use_rerank,
            metadata=metadata,
            rejected_workorder_ids=rejected_workorder_ids,
            query_vector=query_vector,
            #min_score=0.01  # manual 检索阈值单独降低
        )

        # 图片查找已移至 knowledge_tools._fetch_images_parallel（page_range + SQL JOIN，不触发文件检查）
        return results

    def delete_documents(self, document_ids: List[str]) -> Dict[str, int]:
        """
        删除说明书文档

        除了标准删除外,还需要删除关联的图片记录

        Args:
            document_ids: 要删除的文档ID列表

        Returns:
            Dict[str, int]: 删除结果统计
        """
        # TODO: 实现说明书特有的删除逻辑
        # 1. 删除manual_image表中的记录
        # 2. 调用标准删除流程

        # 临时使用标准删除
        return self._standard_delete_documents(document_ids)

    # ==================== 说明书特有的方法 ====================

    def get_manual_images(self, document_id: str) -> Optional[List[str]]:
        """
        获取文档关联的图片ID列表

        如果数据库中不存在,则自动生成

        Args:
            document_id: 文档ID

        Returns:
            Optional[List[str]]: 图片ID列表,如果没有图片则返回None
        """
        from database.sql.repository.repository_factory import RepositoryFactory

        with RepositoryFactory.get_instance().manual_image_repository as manual_repo:
            # 首先尝试从数据库获取
            records = manual_repo.get_images_by_document_id(document_id)
            if records:
                return [r.image_id for r in records]

            # 如果没有映射,尝试获取PDF路径并生成图片
            pdf_path = self._get_pdf_path(document_id)
            if not pdf_path:
                return None

            # 读取该切片的页码范围（page_start/page_end 均为1-based，含）
            docs = self.get_documents(document_id=document_id, collection_name=self.collection_name)
            meta = docs[0].get("metadata", {}) if docs else {}
            page_start = meta.get("page_start")
            page_end = meta.get("page_end")

            # 生成图片（仅转换该切片对应的页码范围）
            from service.system.file_reader_main import file_reader
            image_ids = file_reader.pdf_to_images_and_upload(
                pdf_path, document_id,
                page_start=page_start, page_end=page_end
            )
            return image_ids

    def _get_pdf_path(self, document_id: str) -> Optional[str]:
        """
        获取文档对应的PDF文件路径

        Args:
            document_id: 文档ID

        Returns:
            Optional[str]: PDF文件路径,如果不存在则返回None
        """
        # 从MongoDB获取文档信息
        docs = self.get_documents(document_id=document_id, collection_name=self.collection_name)
        if not docs:
            return None

        doc = docs[0]
        metadata = doc.get("metadata", {})
        file_path = metadata.get("slice_pdf_path") or metadata.get("file_path")

        if not file_path or not isinstance(file_path, str) or not file_path.strip():
            return None

        # 转换为绝对路径
        if str(file_path).startswith("slices/"):
            if INGESTION_OUTPUT_ROOT.exists():
                return str(INGESTION_OUTPUT_ROOT / file_path)
            base_dir = Path(__file__).parent.parent.parent.parent
            return str(base_dir / "ingestion" / "output" / file_path)
        BASE_DIR = Path(__file__).parent.parent.parent.parent
        return str(BASE_DIR / file_path)

