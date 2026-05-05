# -*- coding: utf-8 -*-
from typing import Dict, Any, Optional, List
from pydantic import BaseModel
from api.schema.common.common_models import DataResponse

class DiagnosisRequest(BaseModel):
    """诊断请求模型"""
    query: str
    limit: int = 5

class FaultFilesRequest(BaseModel):
    """故障文件请求模型"""
    details: str = ""

class DiagnosisData(BaseModel):
    """诊断数据模型"""
    diagnosis_tree: Dict[str, Any]

class FaultFilesData(BaseModel):
    """故障文件数据模型"""
    knowledge_nodes: List[Dict[str, Any]]

class DiagnosisResponse(DataResponse[DiagnosisData]):
    """诊断响应模型"""
    pass

class FaultFilesResponse(DataResponse[FaultFilesData]):
    """故障文件响应模型"""
    pass 