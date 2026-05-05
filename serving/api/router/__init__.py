# -*- coding: utf-8 -*-
"""
API Router 入口文件
统一管理所有子模块的路由
"""

from fastapi import APIRouter
from api.router.knowledge import knowledge_base_router, diagnosis_router
from api.router.system import file_router, parameter_router
from api.router.ingest import ingest_router
from api.router.admin import router as admin_router

# 创建主路由器
router = APIRouter()

# 注册各个子模块路由器
router.include_router(knowledge_base_router)
router.include_router(diagnosis_router)
router.include_router(file_router)
router.include_router(parameter_router)
router.include_router(ingest_router.router, prefix="/ingest", tags=["入库管理"])
router.include_router(admin_router)

# 导出主路由器供外部使用
__all__ = ["router"]