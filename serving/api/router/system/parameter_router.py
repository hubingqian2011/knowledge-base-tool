# -*- coding: utf-8 -*-
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, Optional
from pydantic import BaseModel

from service.system.parameter_management_main import parameter_management
from api.middleware.auth_middleware import get_current_user
from api.schema.system.parameter_models import (
    ParameterTreeRequest, ParameterTreeResponse, ParameterTreeData,
    ParameterTreeDescriptionResponse, ParameterTreeDescriptionData
)

router = APIRouter(
    prefix="/parameter",
    tags=["参数管理"],
    dependencies=[Depends(get_current_user)]
)

@router.post("/tree", summary="创建树状结构参数集", response_model=ParameterTreeResponse)
async def create_parameter_tree(data: ParameterTreeRequest):
    """
    - 请求体参数:
        - name: str，参数集名称
        - description: str，参数集描述（可选）
        - data: dict，参数树结构
    - 请求示例:
    ```json
    {
        "name": "测试参数集5",
        "description": "这是一个测试参数集",
        "data": {
            "字符串参数": "这是字符串参数",
            "字典参数": {
                "子参数": "这是子参数"
            },
            "列表参数": [
                {"列表子参数1": "这是列表子参数1"},
                {"列表子参数2": "这是列表子参数2"}
            ]
        }
    }
    ```
    - 返回:
        - ParameterTreeResponse，包含参数集信息和树状结构
    """
    try:
        parameter_set = parameter_management.create_parameter_tree(
            name=data.name,
            description=data.description,
            data=data.data
        )
        return ParameterTreeResponse(
            data=ParameterTreeData(
                id=parameter_set.id,
                name=parameter_set.name,
                description=parameter_set.description,
                tree=parameter_management.get_parameter_tree(parameter_set.id)["tree"]
            )
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/set/{parameter_set_id}/tree", summary="获取参数集的树状结构", response_model=ParameterTreeResponse)
async def get_parameter_tree(parameter_set_id: int):
    """
    - 请求参数:
        - parameter_set_id: int，参数集ID
    - 返回:
        - ParameterTreeResponse，包含参数集信息和树状结构
    """
    try:
        result = parameter_management.get_parameter_tree(parameter_set_id)
        if not result:
            raise HTTPException(status_code=404, detail="参数集不存在")
        return ParameterTreeResponse(
            data=ParameterTreeData(
                id=result["id"],
                name=result["name"],
                description=result["description"],
                tree=result["tree"]
            )
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/set/{parameter_set_id}/description", summary="获取参数集的描述性文本", response_model=ParameterTreeDescriptionResponse)
async def get_parameter_tree_description(parameter_set_id: int):
    """
    - 请求参数:
        - parameter_set_id: int，参数集ID
    - 返回:
        - ParameterTreeDescriptionResponse，包含参数集的描述性文本
    - 响应示例：
        "测试参数集5"包含以下字段
        - "字符串参数"字段是一个字符串，表示"这是字符串参数"
        - "字典参数"字段是一个字典，包含以下字段：
            - "子参数"字段是一个字符串，表示"这是子参数"
        - "列表参数"字段是一个列表，每个元素包含以下属性:
            - "列表子参数1"字段是一个字符串，表示"这是列表子参数1"
    """
    try:
        result = parameter_management.get_parameter_tree_description(parameter_set_id)
        if not result:
            raise HTTPException(status_code=404, detail="参数集不存在")
        return ParameterTreeDescriptionResponse(
            data=ParameterTreeDescriptionData(description=result)
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) 