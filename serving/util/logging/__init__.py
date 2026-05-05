# -*- coding: utf-8 -*-
"""
日志系统模块

提供统一的日志管理服务，基于原有 log.py 功能的面向对象封装。
"""

from .logger import get_logger

__all__ = [
    'get_logger'
]