# -*- coding: utf-8 -*-
"""
Parameter knowledge handler with batch overwrite.
"""
from typing import List, Dict, Any, Optional, Set
from datetime import datetime
import uuid

from util.logging.logger import get_logger
from .generic_handler import GenericKnowledgeHandler

logger = get_logger(__name__)


class ParameterKnowledgeHandler(GenericKnowledgeHandler):
    """
    Parameter knowledge handler.

    - Always ingests a new batch with a batch ID.
    - Soft-deletes previous data for the same controller type before ingest.
    """

    def __init__(self, collection_name: str, collection_type: str = None):
        super().__init__(collection_name, collection_type)

    def add_texts(
        self,
        texts: List,
        collection_name: str,
        metadatas: Optional[List] = None
    ) -> List[str]:
        import uuid
        import numpy as np
        from config.config import VECTOR_DIMENSION

        try:
            # 确保集合存在
            self.milvus_client.create_collection(
                collection_name=collection_name,
                dimension=VECTOR_DIMENSION
            )

            document_ids = []
            new_documents = []

            # 确保所有元数据都包含is_deleted=False标志
            processed_metadatas = []
            if metadatas:
                for metadata in metadatas:
                    processed_metadata = metadata or {}
                    if "is_deleted" not in processed_metadata:
                        processed_metadata["is_deleted"] = False
                    processed_metadatas.append(processed_metadata)
            else:
                processed_metadatas = [{"is_deleted": False} for _ in texts]

            # 对每个文本进行相似度检测
            for i, text in enumerate(texts):
                # 生成当前文本的嵌入向量
                embedding = self.generate_embeddings([text])

                search_metadata = {"is_deleted": False}
                controller_value = (
                    processed_metadatas[i].get("控制器型号")
                    or processed_metadatas[i].get("controller_type")
                )
                if controller_value:
                    search_metadata["控制器型号"] = str(controller_value)

                # 在集合中搜索相似文本
                search_results = self.milvus_client.search_vectors(
                    collection_name=collection_name,
                    query_vectors=embedding.tolist(),
                    top_k=1,
                    metadata_filter=search_metadata
                )

                # 如果找到相似文本且余弦相似度大于阈值
                if search_results and search_results[0] and search_results[0][0]["distance"] > 0.99:
                    existing_id = str(search_results[0][0]["id"])
                    document_ids.append(existing_id)
                    logger.info(f"相似度距离满足阈值要求 使用已存在ID: {existing_id}")
                else:
                    # 生成新的UUID
                    new_id = str(uuid.uuid4())
                    document_ids.append(new_id)
                    new_documents.append({
                        "id": new_id,
                        "text": text,
                        "embedding": embedding[0],
                        "metadata": processed_metadatas[i]
                    })

            # 如果有需要添加的新文档
            if new_documents:
                embeddings = np.array([doc["embedding"] for doc in new_documents])
                new_ids = [doc["id"] for doc in new_documents]
                metadatas_list = [doc["metadata"] for doc in new_documents]

                # 添加向量到Milvus
                self.milvus_client.insert_vectors(
                    collection_name=collection_name,
                    vectors=embeddings.tolist(),
                    metadata_list=metadatas_list,
                    ids=new_ids
                )

                # 将新文档内容保存到MongoDB
                for doc in new_documents:
                    self.document_repository.save_document(
                        content=doc["text"],
                        document_id=doc["id"],
                        collection_name=collection_name,
                        metadata=doc["metadata"]
                    )

            logger.info(f"成功处理{len(texts)}个文本，其中{len(new_documents)}个为新添加")
            return document_ids
        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error(f"添加文本到知识库失败: {str(e)}")
            return []

    def ingest(
        self,
        files: List[Any],
        filenames: List[str],
        metadata: Optional[Dict] = None
    ) -> List[str]:
        from io import BytesIO

        file_contents = self._read_file_contents(files)
        controller_types = self._collect_controller_types_from_contents(
            file_contents, filenames
        )
        if not controller_types:
            logger.error("Parameter ingest aborted: no controller types extracted.")
            return []

        self._soft_delete_previous_batches(controller_types)

        batch_id = str(uuid.uuid4())
        uploaded_at = datetime.utcnow().isoformat()

        merged_metadata = dict(metadata) if metadata else {}
        merged_metadata.setdefault("批次ID", batch_id)
        merged_metadata.setdefault("导入时间", uploaded_at)

        file_streams = [BytesIO(content) for content in file_contents]
        parse_results = self._standard_ingest_flow(
            files=file_streams,
            filenames=filenames,
            collection_name=self.collection_name,
            collection_type=self.collection_type,
            metadata=merged_metadata
        )

        document_ids: List[str] = []
        for result in parse_results:
            if result.get("success") and "document_ids" in result:
                document_ids.extend(result["document_ids"])

        logger.info(
            "Parameter ingest done: batch_id=%s, controllers=%s, docs=%s",
            batch_id,
            sorted(controller_types),
            len(document_ids)
        )
        return document_ids

    def _read_file_contents(self, files: List[Any]) -> List[bytes]:
        contents = []
        for file in files:
            content = file.read()
            if isinstance(content, str):
                content = content.encode("utf-8")
            contents.append(content)
        return contents

    def _collect_controller_types_from_contents(
        self,
        contents: List[bytes],
        filenames: List[str]
    ) -> Set[str]:
        from io import BytesIO
        from service.system.file_parser.file_parser_factory import FileParserFactory

        controller_types: Set[str] = set()
        parser = FileParserFactory.get_parser(self.collection_type)
        for content, filename in zip(contents, filenames):
            parse_result = parser.parse(BytesIO(content), filename)
            if not parse_result.get("success") or "metadatas" not in parse_result:
                logger.error(
                    "Parameter pre-parse failed: filename=%s, result=%s",
                    filename,
                    parse_result
                )
                return set()
            metadatas = parse_result.get("metadatas") or []
            for meta in metadatas:
                if not isinstance(meta, dict):
                    continue
                controller_value = meta.get("控制器型号") or meta.get("controller_type")
                if controller_value:
                    controller_types.add(str(controller_value))
        return controller_types

    def _soft_delete_previous_batches(
        self,
        controller_types: Set[str]
    ) -> None:
        if not controller_types:
            return

        for controller_type in controller_types:
            old_ids = self.document_repository.get_document_ids_by_metadata(
                collection_name=self.collection_name,
                metadata_filters={
                    "控制器型号": controller_type,
                    "is_deleted__ne": True
                }
            )
            if not old_ids:
                continue
            self._standard_delete_documents(old_ids)
