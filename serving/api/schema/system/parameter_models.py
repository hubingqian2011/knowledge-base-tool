# -*- coding: utf-8 -*-
from typing import Dict, Any, Optional
from pydantic import BaseModel
from api.schema.common.common_models import DataResponse

class ParameterTreeRequest(BaseModel):
    name: str
    description: Optional[str] = None
    data: Dict[str, Any]

class ParameterTreeData(BaseModel):
    id: int
    name: str
    description: Optional[str]
    tree: Dict[str, Any]

class ParameterTreeResponse(DataResponse[ParameterTreeData]):
    pass

class ParameterTreeDescriptionData(BaseModel):
    description: str

class ParameterTreeDescriptionResponse(DataResponse[ParameterTreeDescriptionData]):
    pass 