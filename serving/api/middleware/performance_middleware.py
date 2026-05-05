# -*- coding: utf-8 -*-
"""
API性能监控中间件
监控所有API请求的响应时间，超过阈值时记录告警日志
"""

import time
import json
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from util.logging.logger import get_logger

# 配置阈值
PERFORMANCE_THRESHOLDS = {
    "fast": 0.5,      # 500ms - 快速响应
    "normal": 2.0,    # 2s - 正常响应
    "slow": 5.0,      # 5s - 慢响应
    "critical": 10.0  # 10s - 严重性能问题
}

logger = get_logger(__name__)


class PerformanceMonitoringMiddleware(BaseHTTPMiddleware):
    """性能监控中间件"""

    def __init__(self, app, enable_detailed_logging: bool = True):
        super().__init__(app)
        self.enable_detailed_logging = enable_detailed_logging

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """处理请求并监控性能"""
        start_time = time.time()

        # 获取请求信息
        method = request.method
        url_path = request.url.path
        query_params = str(request.query_params) if request.query_params else ""

        # 获取用户信息（如果有）
        user_id = None
        try:
            user = getattr(request.state, 'user', None)
            if user:
                user_id = user.get('id', 'anonymous')
        except Exception:
            user_id = 'anonymous'

        # 记录请求开始
        request_id = f"{int(start_time * 1000)}-{method}"

        try:
            # 执行请求
            response = await call_next(request)

            # 计算响应时间
            process_time = time.time() - start_time

            # 获取状态码
            status_code = response.status_code

            # 记录性能数据
            self._log_performance(
                request_id=request_id,
                method=method,
                url_path=url_path,
                query_params=query_params,
                status_code=status_code,
                process_time=process_time,
                user_id=user_id
            )

            # 添加性能信息到响应头
            response.headers["X-Process-Time"] = str(process_time)

            return response

        except Exception as e:
            # 计算异常处理的响应时间
            process_time = time.time() - start_time

            # 记录异常和性能
            self._log_error_performance(
                request_id=request_id,
                method=method,
                url_path=url_path,
                query_params=query_params,
                process_time=process_time,
                user_id=user_id,
                error=str(e)
            )

            raise

    def _get_performance_level(self, process_time: float) -> tuple:
        """根据响应时间判断性能等级"""
        if process_time <= PERFORMANCE_THRESHOLDS["fast"]:
            return "FAST", None
        elif process_time <= PERFORMANCE_THRESHOLDS["normal"]:
            return "NORMAL", None
        elif process_time <= PERFORMANCE_THRESHOLDS["slow"]:
            return "SLOW", "WARNING"
        elif process_time <= PERFORMANCE_THRESHOLDS["critical"]:
            return "VERY_SLOW", "ERROR"
        else:
            return "CRITICAL", "CRITICAL"

    def _log_performance(
        self,
        request_id: str,
        method: str,
        url_path: str,
        query_params: str,
        status_code: int,
        process_time: float,
        user_id: str
    ):
        """记录性能日志"""
        performance_level, log_level = self._get_performance_level(process_time)

        # 构建性能数据
        performance_data = {
            "request_id": request_id,
            "method": method,
            "url_path": url_path,
            "query_params": query_params,
            "status_code": status_code,
            "process_time": round(process_time, 4),
            "performance_level": performance_level,
            "user_id": user_id,
            "timestamp": time.time()
        }

        # 根据性能等级选择日志级别
        if log_level == "CRITICAL":
            logger.critical(
                f"🚨 性能严重问题 - {method} {url_path} 耗时 {process_time:.4f}s",
                extra={"performance_data": performance_data}
            )
        elif log_level == "ERROR":
            logger.error(
                f"⚠️  响应缓慢 - {method} {url_path} 耗时 {process_time:.4f}s",
                extra={"performance_data": performance_data}
            )
        elif log_level == "WARNING":
            logger.warning(
                f"🐌 响应较慢 - {method} {url_path} 耗时 {process_time:.4f}s",
                extra={"performance_data": performance_data}
            )
        else:
            if self.enable_detailed_logging:
                logger.info(
                    f"✅ {method} {url_path} - {process_time:.4f}s [{status_code}]",
                    extra={"performance_data": performance_data}
                )

    def _log_error_performance(
        self,
        request_id: str,
        method: str,
        url_path: str,
        query_params: str,
        process_time: float,
        user_id: str,
        error: str
    ):
        """记录错误情况下的性能日志"""
        performance_data = {
            "request_id": request_id,
            "method": method,
            "url_path": url_path,
            "query_params": query_params,
            "process_time": round(process_time, 4),
            "performance_level": "ERROR",
            "user_id": user_id,
            "error": error,
            "timestamp": time.time()
        }

        logger.error(
            f"❌ 请求失败 - {method} {url_path} 耗时 {process_time:.4f}s - 错误: {error}",
            extra={"performance_data": performance_data}
        )


def get_performance_stats() -> dict:
    """获取性能统计信息（可扩展用于API端点）"""
    return {
        "thresholds": PERFORMANCE_THRESHOLDS,
        "description": "性能监控中间件统计数据",
        "note": "详细统计功能可通过数据库或缓存扩展实现"
    }