# -*- coding: utf-8 -*-
"""
基于 Loguru 的简化日志系统

提供简单的 get_logger() 接口，使用 Loguru 作为底层实现。
"""

import os
import sys
import time
from typing import Optional
from loguru import logger

# 尝试导入项目配置
try:
    from config.config import (
        ENABLE_ALIBABA_CLOUD_LOG, ALIBABA_CLOUD_ACCESS_KEY_ID, ALIBABA_CLOUD_ACCESS_KEY_SECRET,
        ALIBABA_CLOUD_LOG_ENDPOINT, ALIBABA_CLOUD_LOG_PROJECT,
        ALIBABA_CLOUD_LOG_LOGSTORE, ALIBABA_CLOUD_LOG_LEVEL,
        ALIBABA_CLOUD_LOG_BUFFER_SIZE, LOG_LEVEL, LOG_DIR, LOG_FILE_NAME,
        LOG_FILE_MAX_SIZE, LOG_FILE_RETENTION, LOG_FILE_BACKUP_COUNT
    )
except ImportError:
    # 如果导入失败，设置为None
    ENABLE_ALIBABA_CLOUD_LOG = False
    ALIBABA_CLOUD_ACCESS_KEY_ID = None
    ALIBABA_CLOUD_ACCESS_KEY_SECRET = None
    ALIBABA_CLOUD_LOG_ENDPOINT = None
    ALIBABA_CLOUD_LOG_PROJECT = None
    ALIBABA_CLOUD_LOG_LOGSTORE = None
    ALIBABA_CLOUD_LOG_LEVEL = "INFO"
    ALIBABA_CLOUD_LOG_BUFFER_SIZE = 100
    # 设置默认日志配置
    LOG_DIR = "logs"
    LOG_FILE_NAME = "app.log"
    LOG_FILE_MAX_SIZE = "10 MB"
    LOG_FILE_RETENTION = 10
    LOG_FILE_BACKUP_COUNT = 10


class AliyunLogHandler:
    """阿里云日志服务处理器 - loguru sink实现"""

    def __init__(self):
        self.client = None
        self.project_name = None
        self.logstore_name = None
        self.initialized = False
        self.buffer = []
        self.buffer_size = 100
        self.last_flush_time = time.time()
        self.level_priority = {
            'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50
        }

        # 检查是否启用了阿里云日志服务
        if not ENABLE_ALIBABA_CLOUD_LOG:
            return

        # 检查必要的配置项
        if not all([ALIBABA_CLOUD_LOG_PROJECT, ALIBABA_CLOUD_LOG_LOGSTORE]):
            return

        config = {
            'access_key_id': ALIBABA_CLOUD_ACCESS_KEY_ID or os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_ID'),
            'access_key_secret': ALIBABA_CLOUD_ACCESS_KEY_SECRET or os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_SECRET'),
            'endpoint': ALIBABA_CLOUD_LOG_ENDPOINT or 'cn-hangzhou.log.aliyuncs.com',
            'project_name': ALIBABA_CLOUD_LOG_PROJECT,
            'logstore_name': ALIBABA_CLOUD_LOG_LOGSTORE,
            'level': ALIBABA_CLOUD_LOG_LEVEL or 'INFO',
            'buffer_size': ALIBABA_CLOUD_LOG_BUFFER_SIZE or 100
        }

        try:
            from aliyun.log import LogClient

            access_key_id = config.get('access_key_id')
            access_key_secret = config.get('access_key_secret')
            endpoint = config.get('endpoint', 'cn-hangzhou.log.aliyuncs.com')
            self.project_name = config.get('project_name')
            self.logstore_name = config.get('logstore_name')
            self.buffer_size = config.get('buffer_size', 100)
            min_level = config.get('level', 'INFO')
            self.min_level_priority = self.level_priority.get(min_level, 20)

            if not all([access_key_id, access_key_secret, self.project_name, self.logstore_name]):
                print("阿里云日志服务配置不完整")
                return

            self.client = LogClient(endpoint, access_key_id, access_key_secret)

            # 创建索引
            self._create_index()

            self.initialized = True
            print(f"阿里云日志服务初始化成功: {self.project_name}/{self.logstore_name}")

        except ImportError:
            print("未安装阿里云日志服务SDK，请运行: pip install aliyun-log-python-sdk")
        except Exception as e:
            print(f"阿里云日志服务初始化失败: {e}")

    def _create_index(self) -> None:
        """创建日志索引"""
        try:
            from aliyun.log import IndexConfig
            from aliyun.log.logexception import LogException

            # 定义日志索引配置
            logstore_index = {
                'line': {
                    'token': [',', ' ', "'", '"', ';', '=', '(', ')', '[', ']', '{', '}', '?', '@', '&', '<', '>', '/', ':', '\n', '\t', '\r'],
                    'caseSensitive': False,
                    'chn': False
                },
                'keys': {
                    'level': {'type': 'text', 'token': [',', ' ', "'", '"', ';', '=', '(', ')', '[', ']', '{', '}', '?', '@', '&', '<', '>', '/', ':', '\n', '\t', '\r'], 'caseSensitive': False, 'alias': 'level', 'doc_value': True, 'chn': False},
                    'message': {'type': 'text', 'token': [',', ' ', "'", '"', ';', '=', '(', ')', '[', ']', '{', '}', '?', '@', '&', '<', '>', '/', ':', '\n', '\t', '\r'], 'caseSensitive': False, 'alias': 'message', 'doc_value': True, 'chn': True},
                    'module': {'type': 'text', 'token': [',', ' ', "'", '"', ';', '=', '(', ')', '[', ']', '{', '}', '?', '@', '&', '<', '>', '/', ':', '\n', '\t', '\r'], 'caseSensitive': False, 'alias': 'module', 'doc_value': True, 'chn': False},
                    'function': {'type': 'text', 'token': [',', ' ', "'", '"', ';', '=', '(', ')', '[', ']', '{', '}', '?', '@', '&', '<', '>', '/', ':', '\n', '\t', '\r'], 'caseSensitive': False, 'alias': 'function', 'doc_value': True, 'chn': False},
                    'line': {'type': 'long', 'alias': 'line', 'doc_value': True},
                    'file': {'type': 'text', 'token': [',', ' ', "'", '"', ';', '=', '(', ')', '[', ']', '{', '}', '?', '@', '&', '<', '>', '/', ':', '\n', '\t', '\r'], 'caseSensitive': False, 'alias': 'file', 'doc_value': True, 'chn': False},
                    'process': {'type': 'long', 'alias': 'process', 'doc_value': True},
                    'thread': {'type': 'long', 'alias': 'thread', 'doc_value': True},
                    'logger_name': {'type': 'text', 'token': [',', ' ', "'", '"', ';', '=', '(', ')', '[', ']', '{', '}', '?', '@', '&', '<', '>', '/', ':', '\n', '\t', '\r'], 'caseSensitive': False, 'alias': 'logger_name', 'doc_value': True, 'chn': False}
                }
            }

            # 创建索引配置对象
            index_config = IndexConfig()
            index_config.from_json(logstore_index)

            # 尝试创建索引
            self.client.create_index(self.project_name, self.logstore_name, index_config)
            print(f"成功为 {self.logstore_name} 创建索引")  # 使用print避免递归调用logger

        except LogException as e:
            # 如果索引已存在，记录但不中断初始化
            if e.get_error_code() == "IndexAlreadyExist":
                print(f"索引已存在，跳过创建: {self.logstore_name}")
            else:
                print(f"创建索引失败: {e}")
        except Exception as e:
            # 其他错误，记录但不中断初始化
            print(f"创建索引失败: {e}")

    def __call__(self, message):
        """
        Loguru sink 调用方法

        Args:
            message: Loguru 记录对象
        """
        if not self.initialized:
            return

        try:
            # 检查日志级别
            record = message.record
            level = record['level'].name
            if self.level_priority.get(level, 20) < self.min_level_priority:
                return

            # 格式化日志数据
            log_data = {
                'timestamp': int(record['elapsed'].total_seconds()),
                'level': level,
                'message': record['message'],
                'module': record['module'],
                'function': record['function'],
                'line': record['line'],
                # 'file': os.path.basename(record['file']),
                'process': record['process'].id if record['process'] else 0,
                'thread': record['thread'].id if record['thread'] else 0,
                'logger_name': record['name'],
                'extra': str(record.get('extra', {}))
            }

            # 添加到缓冲区
            self.buffer.append(log_data)

            # 检查是否需要刷新缓冲区
            if len(self.buffer) >= self.buffer_size or \
               time.time() - self.last_flush_time > 30:  # 30秒强制刷新
                self.flush()

        except Exception as e:
            print(f"处理日志消息失败: {e}")

    def flush(self) -> None:
        """刷新缓冲区，发送日志到阿里云"""
        if not self.buffer or not self.initialized:
            return

        try:
            from aliyun.log import PutLogsRequest, LogItem

            log_group = []
            for log_data in self.buffer:
                log_item = LogItem()
                log_item.set_time(int(time.time()))  # 使用当前时间

                contents = [(k, str(v)) for k, v in log_data.items() if k != 'timestamp']
                log_item.set_contents(contents)
                log_group.append(log_item)

            request = PutLogsRequest(
                self.project_name,
                self.logstore_name,
                "", "",
                log_group,
                compress=True
            )

            self.client.put_logs(request)
            self.buffer.clear()
            self.last_flush_time = time.time()

        except Exception as e:
            print(f"发送日志到阿里云失败: {e}")

    def close(self) -> None:
        """关闭处理器"""
        self.flush()
        self.client = None
        self.initialized = False


def _inject_request_id(record: dict) -> None:
    """Loguru patch：将当前请求的 request_id 注入 record.extra，便于整链路日志串联。"""
    try:
        from util.request_context import get_request_id
        record["extra"].setdefault("request_id", get_request_id() or "-")
    except Exception:
        record["extra"].setdefault("request_id", "-")


class LoggerManager:
    """日志管理器单例"""

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._setup_logger()

    def _setup_logger(self, level: str = None, backup: int = 10) -> None:
        """配置loguru日志处理器"""
        # 使用带 request_id 注入的 logger，便于请求级日志串联
        self._logger = logger.patch(_inject_request_id)
        self._logger.remove()

        # 使用环境变量中的日志级别，如果没有传入level参数
        if level is None:
            try:
                level = LOG_LEVEL
            except NameError:
                level = 'INFO'  # 默认级别

        # 统一日志格式，开头带 request_id 便于 grep
        unified_format = (
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<cyan>{process}</cyan> | "
            "<level>{level: <8}</level> | "
            "<dim>{extra[request_id]}</dim> | "
            "<yellow>{module}</yellow>.<blue>{function}</blue> "
            "(<magenta>{file.path}</magenta>:<red>{line}</red>) | "
            "<bold>{name}</bold> | "
            "{message}"
        )

        # 控制台输出
        self._logger.add(
            sys.stdout,
            format=unified_format,
            level=level,
            colorize=True
        )

        # 文件输出
        if os.path.isabs(LOG_DIR):
            log_dir = LOG_DIR
        else:
            log_dir = os.path.join(os.getcwd(), LOG_DIR)

        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        self._logger.add(
            os.path.join(log_dir, LOG_FILE_NAME),
            format=unified_format,
            level=level,
            rotation=LOG_FILE_MAX_SIZE,
            retention=LOG_FILE_RETENTION,
            compression="gz",
            encoding="utf-8"
        )

        # 阿里云日志服务输出（自动配置）- 仅ERROR级别
        self._aliyun_handler = AliyunLogHandler()
        if self._aliyun_handler.initialized:
            self._logger.add(
                self._aliyun_handler,
                format="{message}",
                serialize=False,
                level="ERROR"
            )

        self._initialized = True

        import atexit
        atexit.register(self._cleanup)

    def _cleanup(self) -> None:
        """程序退出时的清理函数，确保缓冲区被刷新"""
        try:
            if hasattr(self, '_aliyun_handler') and self._aliyun_handler:
                self._aliyun_handler.close()
        except Exception:
            pass

    def get_logger(self, name: Optional[str] = None):
        """获取带名称的日志记录器"""
        logger_name = name or "ROOT"
        return self._logger.bind(name=logger_name)


# 全局日志管理器实例
_logger_manager = LoggerManager()


def get_logger(name: Optional[str] = None):
    """
    获取日志记录器

    用法示例：
        logger = get_logger(__name__)
        logger.info("这是一条信息")
        logger.error("这是一条错误")

    Args:
        name: 日志名称 (用于日志文件命名)

    Returns:
        loguru logger 实例

    Note:
        阿里云日志服务配置会自动从config.py读取，无需手动传入
    """
    return _logger_manager.get_logger(name)


# ================================================================
#  主题日志过滤系统
# ================================================================
# LOG_TOPICS 环境变量控制哪些主题的 DEBUG/INFO 输出
# 示例：LOG_TOPICS=perf,llm 或 LOG_TOPICS=all
# 主题列表：perf / retrieval / llm / lane_b / flow
# ERROR/WARNING 始终输出，不受主题控制

_RAW_TOPICS = os.getenv("LOG_TOPICS", "flow,perf,lane_b")
_ALL_TOPICS = "all" in _RAW_TOPICS.lower()
_ACTIVE_TOPICS = set(t.strip() for t in _RAW_TOPICS.split(",") if t.strip())

# 缓存已创建的主题 logger
_TOPIC_LOGGERS = {}


class _TopicLogger:
    """
    主题 Logger 包装器。
    未启用的主题：DEBUG/INFO 调用为空操作（不输出）；WARNING/ERROR 正常输出。
    已启用的主题：所有级别正常输出。
    """

    def __init__(self, topic: str, base_logger, enabled: bool):
        self._topic = topic
        self._base = base_logger.bind(topic=topic)
        self._enabled = enabled

    def debug(self, msg, *args, **kwargs):
        if self._enabled:
            self._base.debug(msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        if self._enabled:
            self._base.info(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        # WARNING 始终输出
        self._base.warning(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        # ERROR 始终输出
        self._base.error(msg, *args, **kwargs)

    @property
    def enabled(self) -> bool:
        return self._enabled


def get_topic_logger(topic: str):
    """
    获取主题 Logger。

    只有 LOG_TOPICS 包含该 topic（或 all）时才输出 DEBUG/INFO。
    ERROR/WARNING 始终输出，不受主题控制。

    用法：
        flow_logger = get_topic_logger("flow")
        flow_logger.info("主流程启动")   # 仅 LOG_TOPICS 含 flow 时输出
        flow_logger.warning("超时")      # 始终输出
    """
    if topic in _TOPIC_LOGGERS:
        return _TOPIC_LOGGERS[topic]

    enabled = _ALL_TOPICS or topic in _ACTIVE_TOPICS
    base = _logger_manager.get_logger(f"topic.{topic}")
    tl = _TopicLogger(topic, base, enabled)
    _TOPIC_LOGGERS[topic] = tl
    return tl


# 如果需要，也可以直接使用logger实例
__all__ = ['get_logger', 'get_topic_logger', 'logger']