# -*- coding: utf-8 -*-
"""
管理员路由模块

所有 /api/admin/* 路由在此注册。
"""

from fastapi import APIRouter
from .admin_knowledge import router as knowledge_router

router = APIRouter(prefix="/admin", tags=["管理后台"])

router.include_router(knowledge_router, prefix="/knowledge")
