# -*- coding: utf-8 -*-
from typing import Generic, TypeVar, Optional, Any
from pydantic import BaseModel

T = TypeVar('T')

class BaseResponse(BaseModel):
    """基础响应模型"""
    code: int = 200
    msg: str = "success"

class DataResponse(BaseResponse, Generic[T]):
    """带数据的响应模型"""
    data: Optional[T] = None

class ErrorResponse(BaseResponse):
    """错误响应模型"""
    code: int = 500
    msg: str = "error"
    detail: Optional[str] = None