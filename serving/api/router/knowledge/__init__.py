# -*- coding: utf-8 -*-
"""
Knowledge routers module
包含知识库相关的路由
"""

# 导入并包含各个路由
from .diagnosis_router import router as diagnosis_router
from .knowledge_base_router import router as knowledge_base_router

# 导出路由以供上级模块使用
__all__ = ["diagnosis_router", "knowledge_base_router"]