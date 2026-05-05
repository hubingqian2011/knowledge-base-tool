# -*- coding: utf-8 -*-
"""
知识库处理器模块
采用策略模式,为不同类型的知识库提供特化的处理逻辑
"""
from .base_handler import BaseKnowledgeHandler
from .workorder_handler import WorkorderKnowledgeHandler
from .manual_handler import ManualKnowledgeHandler
from .generic_handler import GenericKnowledgeHandler
from .parameter_handler import ParameterKnowledgeHandler
from .handler_factory import KnowledgeHandlerFactory

__all__ = [
    "BaseKnowledgeHandler",
    "WorkorderKnowledgeHandler",
    "ManualKnowledgeHandler",
    "GenericKnowledgeHandler",
    "ParameterKnowledgeHandler",
    "KnowledgeHandlerFactory"
]
