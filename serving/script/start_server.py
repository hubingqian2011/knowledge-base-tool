# -*- coding: utf-8 -*-

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import JSONResponse
import os
import sys
import signal
import atexit
import traceback
from config.config import APP_PORT, CORS_ORIGINS, ENABLE_AUTO_CLEANUP, CLEANUP_INTERVAL_HOURS

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from util.logging.logger import get_logger

main_logger = get_logger(__name__)

# 导入自定义异常基类（别名避免与 Python 内置 BaseException 冲突）
from util.exceptions import BaseException as AppBaseException

# 导入主路由和中间件
from api.router import router as main_router
from api.middleware.auth_middleware import AuthMiddleware
from api.middleware.performance_middleware import PerformanceMonitoringMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    if ENABLE_AUTO_CLEANUP:
        from tasks.cleanup_tasks import CleanupTasks
        cleanup_tasks = CleanupTasks(interval_hours=CLEANUP_INTERVAL_HOURS)
        await cleanup_tasks.start_in_background()
        app.state.cleanup_tasks = cleanup_tasks
        main_logger.info("自动清理任务已启动")

    yield

    # 关闭时执行
    if hasattr(app.state, 'cleanup_tasks'):
        app.state.cleanup_tasks.stop_cleanup_scheduler()
        main_logger.info("自动清理任务已停止")


# 创建FastAPI应用
app = FastAPI(title="manubot backend API", lifespan=lifespan)


# ==================== 全局异常处理器 ====================

@app.exception_handler(AppBaseException)
async def app_exception_handler(request: Request, exc: AppBaseException) -> JSONResponse:
    """
    覆盖 util/exceptions.py 中所有自定义异常：
    BusinessException / ValidationException / AuthenticationException /
    AuthorizationException / ResourceNotFoundException / ServerException /
    AudioProcessingException / StorageServiceError 及其所有子类。

    业务异常 message 对用户可见，记 WARNING 级别日志。
    """
    main_logger.warning(
        f"业务异常: {type(exc).__name__} code={exc.code} msg={exc.message} "
        f"path={request.url.path}"
    )
    return JSONResponse(
        status_code=exc.code,
        content={"code": exc.code, "msg": exc.message, "data": None}
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    FastAPI 请求参数校验失败（Pydantic ValidationError）。
    不把详细字段错误暴露给前端，只记日志。
    """
    main_logger.warning(
        f"请求参数校验失败: path={request.url.path}, errors={exc.errors()}"
    )
    return JSONResponse(
        status_code=422,
        content={"code": 422, "msg": "请求参数错误，请检查输入", "data": None}
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """
    FastAPI / Starlette 主动抛出的 HTTPException（如 401/403/404/413 等）。
    统一返回格式，保留原有 status_code 和 detail。
    必须显式注册，否则全局 Exception handler 会将其拦截导致行为变化。
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "msg": exc.detail, "data": None}
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    兜底处理器：捕获所有未预期的 Python 异常（数据库超时、LLM崩溃等）。
    - 完整堆栈写入 ERROR 日志（含 traceback）
    - 前端只返回通用提示，不暴露内部细节
    """
    main_logger.error(
        f"未捕获异常: {type(exc).__name__}: {str(exc)} "
        f"path={request.url.path}\n{traceback.format_exc()}"
    )
    return JSONResponse(
        status_code=500,
        content={"code": 500, "msg": "服务器内部错误，请稍后重试", "data": None}
    )


# ==================== 中间件 ====================

# 添加认证中间件
app.add_middleware(AuthMiddleware)

# 添加性能监控中间件 - 监控所有API请求性能
app.add_middleware(PerformanceMonitoringMiddleware)

# 配置跨域 - 必须最后添加，确保正确处理预检请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,  # 从配置文件读取
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有HTTP方法
    allow_headers=["*"],
)

# 注册路由，使用统一前缀 /api
API_PREFIX = "/api"
app.include_router(main_router, prefix=API_PREFIX)

# 新增：兼容KMS的调用前缀
app.include_router(main_router, prefix="/ai_fault_diagnosis/api")

def cleanup_resources():
    """清理全局资源"""
    main_logger.info("正在优雅关闭服务...")
    # 这里可以添加其他资源清理逻辑，如关闭数据库连接等
    main_logger.info("资源清理完成")

def signal_handler(signum, frame):
    """信号处理函数"""
    main_logger.info(f"接收到信号 {signum}，正在优雅关闭服务...")
    cleanup_resources()
    sys.exit(0)

def initialize_server():
    """初始化服务器"""
    main_logger.info("manubot backend 服务初始化中...")

    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    atexit.register(cleanup_resources)

    # 初始化数据库
    try:
        from database.sql.model.database_manager import DatabaseManager
        db_manager = DatabaseManager()
        if db_manager.create_tables():
            main_logger.info("数据库表初始化成功")
        else:
            main_logger.error("数据库表初始化失败")
    except Exception as e:
        main_logger.error(f"数据库初始化异常: {str(e)}")

    # 这里可以添加其他初始化逻辑
    main_logger.info("manubot backend 服务初始化完成")

if __name__ == '__main__':
    import subprocess
    initialize_server()
    port = APP_PORT
    workers = int(os.environ.get("UVICORN_WORKERS", "4"))
    main_logger.info(f"manubot backend 服务启动在端口 {port}，workers={workers}（通过子进程 uvicorn 以支持多 worker）")
    # uvicorn.run() 的 workers 在程序化调用时不生效，故用命令行方式启动
    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run(
        [sys.executable, "-m", "uvicorn", "script.start_server:app", "--host", "0.0.0.0", "--port", str(port), "--workers", str(workers)],
        cwd=backend_root,
        check=True,
    )
