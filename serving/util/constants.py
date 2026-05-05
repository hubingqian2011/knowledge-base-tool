# -*- coding: utf-8 -*-
"""
全局常量定义
"""
from enum import Enum


class ChatType(str, Enum):
    """聊天类型枚举 - 全局统一使用"""
    GENERAL = "GENERAL"
    DATA_ANALYSIS = "AIOT_DATA_ANALYSIS"
    FAULT_QA = "AI_FAULT_QA"
    INDUSTRY_KNOWLEDGE = "INDUSTRY_KNOWLEDGE"

    def __str__(self):
        return self.value

class RoleType(str, Enum):
    """角色类型枚举"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

    def __str__(self):
        return self.value