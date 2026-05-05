# -*- coding: utf-8 -*-
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from api.schema.common.common_models import DataResponse

class EmbeddingRequest(BaseModel):
    """嵌入向量请求模型"""
    texts: List[str]
    is_query: bool = False

class KnowledgeBaseRequest(BaseModel):
    """知识库请求模型"""
    texts: List[str]
    collection_name: str
    metadatas: Optional[List[Dict[str, Any]]] = None

class EmbeddingResponseData(BaseModel):
    embeddings: list
    text_count: int

class EmbeddingResponse(DataResponse[EmbeddingResponseData]):
    pass

class AddTextsResponseData(BaseModel):
    text_count: int
    vector_ids: list[str]

class AddTextsResponse(DataResponse[AddTextsResponseData]):
    pass

class DocumentsResponseData(BaseModel):
    documents: list
    document_count: int

class DocumentsResponse(DataResponse[DocumentsResponseData]):
    pass

# 搜索请求体
class BaseSearchRequest(BaseModel):
    query: str
    collection_name: str
    top_k: int = 10
    metadata: Optional[Dict[str, Any]] = None

class SimilaritySearchRequest(BaseSearchRequest):
    use_rerank: bool = True

class HybridSearchRequest(BaseSearchRequest):
    similarity_weight: float = 1.0
    full_text_weight: float = 1.0

# 搜索响应体
class SearchResultsData(BaseModel):
    results: list
    result_count: int

class SearchResponse(DataResponse[SearchResultsData]):
    pass

class UploadKnowledgeFileResult(BaseModel):
    document_id: str
    filename: str
    file_path: str | None
    success: bool 

class UploadKnowledgeFilesResponseData(BaseModel):
    results: list[UploadKnowledgeFileResult]

class UploadKnowledgeFilesResponse(DataResponse[UploadKnowledgeFilesResponseData]):
    pass

class UploadKnowledgeFilesAndParseResult(BaseModel):
    document_ids: list[str]
    filename: str
    success: bool
    result: dict | str

class UploadKnowledgeFilesAndParseResponseData(BaseModel):
    results: list[UploadKnowledgeFilesAndParseResult]

class UploadKnowledgeFilesAndParseResponse(DataResponse[UploadKnowledgeFilesAndParseResponseData]):
    pass

class WorkOrderJsonResponseData(BaseModel):
    workorder: dict

class WorkOrderJsonResponse(DataResponse[WorkOrderJsonResponseData]):
    pass

class ManualImagesResponseData(BaseModel):
    image_ids: list[str]

class ManualImagesResponse(DataResponse[ManualImagesResponseData]):
    pass

class DeleteCollectionRequest(BaseModel):
    """删除集合请求模型"""
    collection_name: str

class DeleteCollectionResponseData(BaseModel):
    """删除集合响应数据模型"""
    collection_name: str
    success: bool

class DeleteCollectionResponse(DataResponse[DeleteCollectionResponseData]):
    pass

class DeleteDocumentsRequest(BaseModel):
    """删除文档请求模型"""
    document_ids: List[str]
    collection_name: str

class DeleteDocumentsResponseData(BaseModel):
    """删除文档响应数据模型"""
    deleted_count: int
    total_count: int

class DeleteDocumentsResponse(DataResponse[DeleteDocumentsResponseData]):
    pass