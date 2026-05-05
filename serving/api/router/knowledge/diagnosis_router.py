# -*- coding: utf-8 -*-
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends
from typing import Dict, Any, Optional, List
from pydantic import BaseModel

from service.system.file_reader_main import file_reader
from service.knowledge.knowledge_service import knowledge_service
from api.middleware.auth_middleware import get_current_user
from api.schema.common.common_models import ErrorResponse
from api.schema.knowledge.diagnosis_models import (
    DiagnosisRequest, FaultFilesRequest, DiagnosisData, FaultFilesData,
    DiagnosisResponse, FaultFilesResponse
)

# 获取工单处理器
def get_workorder_handler():
    """获取工单知识库处理器"""
    return knowledge_service._get_handler("workorder")

router = APIRouter(
    prefix="/diagnosis",
    tags=["故障诊断"],
    dependencies=[Depends(get_current_user)]
)

@router.post("/diagnose", summary="进行故障诊断", response_model=DiagnosisResponse)
async def diagnose(data: DiagnosisRequest):
    """
    - 请求体参数:
        - query: str，故障描述
        - limit: int，返回结果数量（可选，默认5）
    - 返回:
        - DiagnosisResponse，包含诊断树结构
        - 若未找到相关知识，返回 ErrorResponse
    """
    try:
        handler = get_workorder_handler()
        result = handler.diagnose(
            query=data.query,
            limit=data.limit
        )
        if result is None:
            return ErrorResponse(
                code=404,
                msg="未找到相关故障知识"
            )
        return DiagnosisResponse(
            data=DiagnosisData(diagnosis_tree=result)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/files", summary="处理故障文件列表", response_model=FaultFilesResponse)
async def process_fault_files(files: List[UploadFile] = File(...), details: str = ""):
    """
    - 请求参数:
        - files: List[UploadFile]，上传的文件列表
        - details: str，明细信息，用于筛选Excel中的"明细"字段
    - 返回:
        - FaultFilesResponse，包含知识节点列表
        - 若未成功保存任何文件，返回 ErrorResponse
    """
    try:
        handler = get_workorder_handler()

        # 保存上传的文件
        file_paths = []
        for file in files:
            file_path = file_reader.save_file(file.file, file.filename)
            if file_path:
                file_paths.append(file_path)

        if not file_paths:
            return ErrorResponse(
                code=400,
                msg="没有成功保存任何文件"
            )

        # 处理文件列表，支持details参数筛选
        knowledge_nodes = handler.process_fault_files(file_paths, details)

        return FaultFilesResponse(
            data=FaultFilesData(knowledge_nodes=knowledge_nodes)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
