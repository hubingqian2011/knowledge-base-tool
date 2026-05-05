# -*- coding: utf-8 -*-
"""
System routers module
包含系统基础功能相关的路由
"""

# 导入并包含各个路由
from .file_router import router as file_router
from .parameter_router import router as parameter_router

# 导出路由以供上级模块使用
__all__ = ["file_router", "parameter_router"]