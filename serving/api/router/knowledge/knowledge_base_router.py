# -*- coding: utf-8 -*-
"""
知识库路由 - 重构版
移除业务逻辑判断,统一调用 knowledge_service
"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query, Depends
from typing import List, Dict, Any, Optional
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os

from config.config import COLLECTION_PRINCIPLE

_principle_cols = [c.strip() for c in COLLECTION_PRINCIPLE.split(",")]
COLLECTION_PRINCIPLE_TOTAL = _principle_cols[0] if _principle_cols else "principle_total"
COLLECTION_PRINCIPLE_CHUNKS = _principle_cols[1] if len(_principle_cols) > 1 else "principle_chunks"
from service.knowledge.knowledge_service import knowledge_service
from api.middleware.auth_middleware import get_current_user
from service.auth.knowledge_permission_utils import (
    apply_permission_filter,
    build_permission_metadata,
    extract_user_info,
)
from api.schema.knowledge.knowledge_base_models import (
    EmbeddingRequest, KnowledgeBaseRequest,
    EmbeddingResponse, EmbeddingResponseData,
    AddTextsResponse, AddTextsResponseData,
    DocumentsResponse, DocumentsResponseData,
    SearchResultsData, SearchResponse,
    BaseSearchRequest, SimilaritySearchRequest, HybridSearchRequest,
    UploadKnowledgeFilesResponse, UploadKnowledgeFilesResponseData, UploadKnowledgeFileResult,
    UploadKnowledgeFilesAndParseResponse, UploadKnowledgeFilesAndParseResponseData, UploadKnowledgeFilesAndParseResult,
    WorkOrderJsonResponse, WorkOrderJsonResponseData,
    ManualImagesResponse, ManualImagesResponseData,
    DeleteCollectionRequest, DeleteCollectionResponse, DeleteCollectionResponseData,
    DeleteDocumentsRequest, DeleteDocumentsResponse, DeleteDocumentsResponseData
)

router = APIRouter(
    prefix="/knowledge_base",
    tags=["知识库"],
    dependencies=[Depends(get_current_user)]
)

@router.post("/embedding/generate", summary="生成文本嵌入向量", response_model=EmbeddingResponse)
async def generate_embeddings(data: EmbeddingRequest):
    """
    生成文本的嵌入向量

    - 请求体参数:
        - texts: List[str]，待生成嵌入的文本列表
        - is_query: bool，是否为查询（可选）
    - 返回:
        - EmbeddingResponse，包含嵌入向量和文本数量的结构化响应
    """
    try:
        embeddings = knowledge_service.generate_embeddings(
            texts=data.texts,
            is_query=data.is_query
        )
        return EmbeddingResponse(
            data=EmbeddingResponseData(
                embeddings=embeddings.tolist() if hasattr(embeddings, 'tolist') else embeddings,
                text_count=len(data.texts)
            )
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/texts/add", summary="添加文本到知识库", response_model=AddTextsResponse)
async def add_texts(data: KnowledgeBaseRequest):
    """
    添加文本到知识库

    - 请求体参数:
        - texts: List[str]，待添加的文本列表
        - collection_name: str，集合名称
        - metadatas: List[dict]，每个文本的元数据（可选）
    - 返回:
        - AddTextsResponse，包含插入向量的ID列表和文本数量
    """
    try:
        vector_ids = knowledge_service.add_texts(
            texts=data.texts,
            collection_name=data.collection_name,
            metadatas=data.metadatas
        )
        return AddTextsResponse(
            code=0 if vector_ids else 1,
            msg="success" if vector_ids else "failed",
            data=AddTextsResponseData(
                text_count=len(data.texts),
                vector_ids=[str(id) for id in vector_ids]
            )
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/collection/delete", summary="删除知识库集合", response_model=DeleteCollectionResponse)
async def delete_collection(data: DeleteCollectionRequest):
    """
    删除知识库集合

    - 请求体参数:
        - collection_name: str，要删除的集合名称
    - 返回:
        - DeleteCollectionResponse，包含删除结果信息
    """
    try:
        success = knowledge_service.delete_collection(
            collection_name=data.collection_name
        )
        return DeleteCollectionResponse(
            data=DeleteCollectionResponseData(
                collection_name=data.collection_name,
                success=success
            )
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/documents", summary="获取文档", response_model=DocumentsResponse)
async def get_documents(
    vector_id: Optional[str] = None,
    collection_name: Optional[str] = None
):
    """
    获取文档

    - 请求参数:
        - vector_id: str，向量数据库中的ID（可选）
        - collection_name: str，集合名称（可选）
    - 返回:
        - DocumentsResponse，包含文档列表和数量
    """
    try:
        if not vector_id and not collection_name:
            raise HTTPException(status_code=400, detail="At least one of vector_id or collection_name must be provided")

        documents = knowledge_service.get_documents(
            document_id=vector_id,
            collection_name=collection_name
        )

        return DocumentsResponse(
            data=DocumentsResponseData(
                documents=documents,
                document_count=len(documents)
            )
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/similarity_search", summary="相似度搜索", response_model=SearchResponse)
async def similarity_search(
    data: SimilaritySearchRequest,
    current_user = Depends(get_current_user),
):
    """
    相似度搜索 (自动适配不同知识库类型)

    - 请求体参数:
        - query: str，查询文本
        - collection_name: str，集合名称
        - top_k: int，返回结果数量（可选，默认10）
        - use_rerank: bool，是否使用重排序（可选，默认True）
        - metadata: dict，元数据过滤条件（可选）
    - 返回:
        - SearchResponse，包含搜索结果列表和数量

    注意:
        - 工单知识库: 如果 metadata 中包含 enable_diagnosis="true" 或查询包含 [DIAGNOSIS] 标记，
          会自动触发诊断搜索
        - 说明书知识库: 搜索结果会自动包含 image_ids 字段
        - 其他知识库: 使用标准向量搜索
    """
    try:
        search_metadata = data.metadata
        if data.collection_name not in {"parameter_updated", "parameter"}:
            search_metadata = apply_permission_filter(
                search_metadata,
                extract_user_info(current_user),
            )
        # 统一调用 knowledge_service，Handler 会自动处理不同类型的逻辑
        results = knowledge_service.similarity_search(
            query=data.query,
            collection_name=data.collection_name,
            top_k=data.top_k,
            use_rerank=data.use_rerank,
            metadata=search_metadata
        )

        # 确保 id 是字符串类型
        for result in results:
            result['id'] = str(result['id'])

        return SearchResponse(
            data=SearchResultsData(
                results=results,
                result_count=len(results)
            )
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/full_text_search", summary="全文搜索", response_model=SearchResponse)
async def full_text_search(
    data: BaseSearchRequest,
    current_user = Depends(get_current_user),
):
    """
    全文搜索

    - 请求体参数:
        - query: str，查询文本
        - collection_name: str，集合名称
        - top_k: int，返回结果数量（可选，默认10）
        - metadata: dict，元数据过滤条件（可选）
    - 返回:
        - SearchResponse，包含搜索结果列表和数量
    """
    try:
        search_metadata = data.metadata
        if data.collection_name not in {"parameter_updated", "parameter"}:
            search_metadata = apply_permission_filter(
                search_metadata,
                extract_user_info(current_user),
            )
        results = knowledge_service.full_text_search(
            query=data.query,
            collection_name=data.collection_name,
            top_k=data.top_k,
            metadata=search_metadata
        )
        return SearchResponse(
            data=SearchResultsData(
                results=results,
                result_count=len(results)
            )
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/hybrid_search", summary="混合搜索", response_model=SearchResponse)
async def hybrid_search(
    data: HybridSearchRequest,
    current_user = Depends(get_current_user),
):
    """
    混合搜索

    - 请求体参数:
        - query: str，查询文本
        - collection_name: str，集合名称
        - similarity_weight: float，相似度搜索结果的权重（可选，默认1.0）
        - full_text_weight: float，全文搜索结果的权重（可选，默认1.0）
        - top_k: int，返回结果数量（可选，默认10）
        - metadata: dict，元数据过滤条件（可选）
    - 返回:
        - SearchResponse，包含搜索结果列表和数量
    """
    try:
        search_metadata = data.metadata
        if data.collection_name not in {"parameter_updated", "parameter"}:
            search_metadata = apply_permission_filter(
                search_metadata,
                extract_user_info(current_user),
            )
        results = knowledge_service.hybrid_search(
            query=data.query,
            collection_name=data.collection_name,
            similarity_weight=data.similarity_weight,
            full_text_weight=data.full_text_weight,
            top_k=data.top_k,
            metadata=search_metadata
        )
        for result in results:
            result['id'] = str(result['id'])
        return SearchResponse(
            data=SearchResultsData(
                results=results,
                result_count=len(results)
            )
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/upload_knowledge_files", summary="为知识库文本上传文件并保存路径", response_model=UploadKnowledgeFilesResponse)
async def upload_knowledge_files(
    document_ids: List[str] = Form(...),
    collection_name: str = Form(...),
    files: List[UploadFile] = File(...)
):
    """
    为知识库文本上传文件并保存路径

    - 请求体参数:
        - document_ids: List[str]，文本对应的ID列表
        - collection_name: str，集合名称
        - files: List[UploadFile]，文件流列表
    - 返回:
        - UploadKnowledgeFilesResponse，包含每个文件的保存结果
    """
    try:
        filenames = [f.filename for f in files]
        file_objs = [f.file for f in files]
        results = knowledge_service.upload_knowledge_files(
            files=file_objs,
            filenames=filenames,
            document_ids=document_ids,
            collection_name=collection_name
        )
        resp_results = [UploadKnowledgeFileResult(**r) for r in results]
        return UploadKnowledgeFilesResponse(
            data=UploadKnowledgeFilesResponseData(results=resp_results)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/upload_knowledge_files_and_parse", summary="上传知识库文件并自动解析入库", response_model=UploadKnowledgeFilesAndParseResponse)
async def upload_knowledge_files_and_parse(
    collection_name: str = Form(...),
    permission_level: Optional[str] = Form(None),
    collection_type: Optional[str] = Form(None),
    metadata: Optional[str] = Form(None),
    files: List[UploadFile] = File(...)
):
    """
    上传知识库文件并自动解析入库 (自动适配不同知识库类型)

    - 请求体参数:
        - collection_name: str，知识库集合名称（实际存储的集合名）
        - collection_type: str，知识库类型（用于选择解析器，如workorder/manual等，可选）
        - metadata: str，额外的元数据JSON字符串（可选，如：{"key": "value"}）
        - files: List[UploadFile]，文件流列表
    - 返回:
        - UploadKnowledgeFilesAndParseResponse，包含每个文件的解析和入库结果

    注意:
        - 系统会根据 collection_name 自动选择合适的处理器
        - 工单知识库: 支持故障诊断和图谱融合
        - 说明书知识库: 支持PDF切片和图片提取
        - 其他知识库: 使用标准解析流程
    """
    try:
        if (
            collection_type == "principle"
            or collection_name == "principle"
            or collection_name in {COLLECTION_PRINCIPLE_TOTAL, COLLECTION_PRINCIPLE_CHUNKS}
        ):
            raise HTTPException(
                status_code=400,
                detail="principle must use /api/ingest/upload for dual-collection GraphRAG ingest",
            )
        is_parameter_collection = collection_name in {"parameter_updated", "parameter"}
        if not is_parameter_collection and not permission_level:
            raise HTTPException(status_code=400, detail="permission_level is required")

        permission_metadata = None
        if not is_parameter_collection:
            try:
                permission_metadata = build_permission_metadata(permission_level)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error))
        # 解析metadata JSON字符串
        parsed_metadata = None
        if metadata:
            try:
                import json
                parsed_metadata = json.loads(metadata)
            except json.JSONDecodeError as e:
                raise HTTPException(status_code=400, detail=f"Invalid metadata JSON format: {str(e)}")
        parsed_metadata = dict(parsed_metadata or {})
        if permission_metadata:
            parsed_metadata.update(permission_metadata)

        # 统一调用 knowledge_service，Handler 会自动处理不同类型的逻辑
        filenames = [f.filename for f in files]
        file_objs = [f.file for f in files]

        import asyncio
        results = await asyncio.to_thread(
            knowledge_service.upload_knowledge_files_and_parse,
            files=file_objs,
            filenames=filenames,
            collection_name=collection_name,
            collection_type=collection_type,
            metadata=parsed_metadata
        )

        resp_results = [UploadKnowledgeFilesAndParseResult(**r) for r in results]
        return UploadKnowledgeFilesAndParseResponse(
            data=UploadKnowledgeFilesAndParseResponseData(results=resp_results)
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/download_knowledge_file", summary="根据document_id下载知识库文件")
async def download_knowledge_file(document_id: str, collection_name: str):
    """
    根据document_id下载知识库文件

    - 请求参数:
        - document_id: str，文档ID
        - collection_name: str，集合名称
    - 返回:
        - 文件流，适配前端渲染/下载
    """
    file_path = knowledge_service.get_knowledge_file_by_id(document_id=document_id, collection_name=collection_name)
    if not file_path:
        raise HTTPException(status_code=404, detail="未找到文件路径")
    return FileResponse(path=file_path, filename=os.path.basename(file_path), media_type="application/octet-stream")

@router.get("/workorder_json", summary="根据document_id获取工单json内容", response_model=WorkOrderJsonResponse)
async def get_workorder_json(document_id: str, collection_name: str):
    """
    根据document_id获取工单json内容

    - 请求参数:
        - document_id: str，文档ID
        - collection_name: str，集合名称（必须为workorder）
    - 返回:
        - WorkOrderJsonResponse，包含工单json内容
    """
    if collection_name != "workorder" and collection_name != "workorder_total":
        raise HTTPException(status_code=400, detail="仅支持workorder类型")
    workorder_json = knowledge_service.get_workorder_json_by_id(document_id=document_id, collection_name=collection_name)
    if not workorder_json:
        raise HTTPException(status_code=404, detail="未找到工单json内容")
    return WorkOrderJsonResponse(data=WorkOrderJsonResponseData(workorder=workorder_json))

@router.get("/manual_images", summary="获取manual的图片id列表", response_model=ManualImagesResponse)
async def get_manual_images(
    document_id: str = Query(..., description="文档ID"),
    collection_name: str = Query(..., description="集合名称")
):
    """
    获取manual的图片id列表

    - 请求参数:
        - document_id: str，文档ID
        - collection_name: str，集合名称
    - 返回:
        - image_ids: List[str]，图片id列表
    """
    image_ids = knowledge_service.get_manual_images_by_id(document_id=document_id, collection_name=collection_name)
    if image_ids is None:
        raise HTTPException(status_code=404, detail="未找到图片")
    return ManualImagesResponse(
        code=200,
        msg="success",
        data=ManualImagesResponseData(image_ids=image_ids)
    )

@router.post("/documents/delete", summary="删除文档", response_model=DeleteDocumentsResponse)
async def delete_documents(data: DeleteDocumentsRequest):
    """
    删除文档 (自动适配不同知识库类型)

    - 请求体参数:
        - document_ids: List[str]，要删除的文档ID列表
        - collection_name: str，集合名称
    - 返回:
        - DeleteDocumentsResponse，包含删除结果信息

    注意:
        - 系统会根据 collection_name 自动选择合适的处理器
        - 工单知识库: 会同时删除图谱和向量数据
        - 其他知识库: 使用标准删除流程
    """
    try:
        # 统一调用 knowledge_service，Handler 会自动处理不同类型的删除逻辑
        result = knowledge_service.delete_documents(
            document_ids=data.document_ids,
            collection_name=data.collection_name
        )
        return DeleteDocumentsResponse(
            data=DeleteDocumentsResponseData(
                deleted_count=result.get("deleted_count", 0),
                total_count=result.get("total_count", len(data.document_ids))
            )
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
