# -*- coding: utf-8 -*-
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# 加载.env文件
load_dotenv()

# shared 层：项目根目录下的 shared/ 包
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.config.settings import (
    EMBEDDING_DIMENSION as _SHARED_EMBEDDING_DIMENSION,
    COLLECTION_MANUAL as _SHARED_COLLECTION_MANUAL,
    COLLECTION_MANUAL_TOTAL as _SHARED_COLLECTION_MANUAL_TOTAL,
    COLLECTION_MANUAL_CHUNKS as _SHARED_COLLECTION_MANUAL_CHUNKS,
    COLLECTION_PRINCIPLE as _SHARED_COLLECTION_PRINCIPLE,
    COLLECTION_PRINCIPLE_TOTAL as _SHARED_COLLECTION_PRINCIPLE_TOTAL,
    COLLECTION_PRINCIPLE_CHUNKS as _SHARED_COLLECTION_PRINCIPLE_CHUNKS,
    COLLECTION_REPAIR as _SHARED_COLLECTION_REPAIR,
    COLLECTION_VIDEO as _SHARED_COLLECTION_VIDEO,
    COLLECTION_WORKORDER as _SHARED_COLLECTION_WORKORDER,
    COLLECTION_PARAMETER as _SHARED_COLLECTION_PARAMETER,
    COLLECTION_WORKORDER_GRAPH as _SHARED_WORKORDER_GRAPH,
    VECTOR_SEARCH_SCORE_THRESHOLD as _SHARED_SCORE_THRESHOLD,
    ENTITY_BOOST_FACTOR,
)

# 数据库配置
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '3306')
DB_NAME = os.getenv('DB_NAME', 'ai_fault_diagnosis')
DB_USER = os.getenv('DB_USER', 'root')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'password')

# 构建数据库URL
DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Neo4j配置
NEO4J_URI = os.getenv('NEO4J_URI', 'bolt://localhost:7687')
NEO4J_USER = os.getenv('NEO4J_USER', 'neo4j')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD', 'password')
NEO4J_DATABASE = os.getenv('NEO4J_DATABASE', 'neo4j')

# Redis配置
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = os.getenv('REDIS_PORT', '6379')
REDIS_DB = os.getenv('REDIS_DB', '0')
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', '')

# 构建Redis URL
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"

# MongoDB配置
MONGO_HOST = os.getenv('MONGO_HOST', 'localhost')
MONGO_PORT = int(os.getenv('MONGO_PORT', '27017'))
MONGO_USER = os.getenv('MONGO_USER', '')
MONGO_PASSWORD = os.getenv('MONGO_PASSWORD', '')
MONGO_DATABASE = os.getenv('MONGO_DATABASE', 'ai_fault_diagnosis')
MONGO_AUTH_SOURCE = os.getenv('MONGO_AUTH_SOURCE', 'admin')

# 构建MongoDB URI
if MONGO_USER and MONGO_PASSWORD:
    MONGO_URI = f"mongodb://{MONGO_USER}:{MONGO_PASSWORD}@{MONGO_HOST}:{MONGO_PORT}/{MONGO_DATABASE}?authSource={MONGO_AUTH_SOURCE}"
else:
    MONGO_URI = f"mongodb://{MONGO_HOST}:{MONGO_PORT}/{MONGO_DATABASE}"

# Elasticsearch配置
ES_HOST = os.getenv('ES_HOST', 'localhost')
ES_PORT = os.getenv('ES_PORT', '9200')
ES_USER = os.getenv('ES_USER', '')
ES_PASSWORD = os.getenv('ES_PASSWORD', '')

# 构建Elasticsearch URL
ES_SCHEME = os.getenv('ES_SCHEME', 'http')
ES_URL = f"{ES_SCHEME}://{ES_HOST}:{ES_PORT}"

# Milvus配置
MILVUS_HOST = os.getenv('MILVUS_HOST', 'localhost')
MILVUS_PORT = os.getenv('MILVUS_PORT', '19530')
MILVUS_USER = os.getenv('MILVUS_USER', 'admin')
MILVUS_PASSWORD = os.getenv('MILVUS_PASSWORD', '')

# 向量存储配置
VECTOR_DIMENSION = _SHARED_EMBEDDING_DIMENSION
EMBEDDING_BATCH_SIZE = int(os.getenv('EMBEDDING_BATCH_SIZE', '10'))
VECTOR_CHUNK_SIZE = int(os.getenv('VECTOR_CHUNK_SIZE', '1000'))
VECTOR_CHUNK_OVERLAP = int(os.getenv('VECTOR_CHUNK_OVERLAP', '200'))
VECTOR_COLLECTION_PREFIX = os.getenv('VECTOR_COLLECTION_PREFIX', 'ai_fault_')

# 重排序配置
RERANK_SEARCH_MULTIPLIER = 2  # 重排序时的搜索倍率，即 Milvus 召回量 = top_k * 2

# 内容生成模式：True=卡片模式（结构化组件），False=纯文字流式模式（Markdown 输出）
# 环境变量控制，无需重新构建镜像：USE_CARD_MODE=0 切换为文字模式，USE_CARD_MODE=1 为卡片模式（默认）
USE_CARD_MODE = os.getenv("USE_CARD_MODE", "1") == "1"

# 向量检索相似度阈值（基于 Milvus 原始 IP/COSINE distance，0~1；非加权分）
# 仅 Milvus 阶段过滤使用，ES 不参与分数计算
VECTOR_SEARCH_SCORE_THRESHOLD = _SHARED_SCORE_THRESHOLD

# 火山引擎配置
VOLCENGINE_API_KEY = os.getenv('VOLCENGINE_API_KEY', '')
VOLCENGINE_EMBEDDING_MODEL_NAME = os.getenv(
    'VOLCENGINE_EMBEDDING_MODEL_NAME', "doubao-embedding-large-text-250515")

# 知识库配置
ENABLE_RERANKER = os.getenv('ENABLE_RERANKER', "0") == "1"
RERANK_SCORE_THRESHOLD = float(os.getenv("RERANK_SCORE_THRESHOLD", "0.1"))

# 故障诊断知识库配置
FAULT_DIAGNOSIS_COLLECTION = os.getenv('FAULT_DIAGNOSIS_COLLECTION', 'fault_diagnosis')

# === 模型接口工具配置 ===
# 模型API配置（使用字典支持多个模型）
LLM_API_CONFIG = {
    # 默认模型指向豆包
    "default": {
        "api_key": os.getenv("DOUBAO_API_KEY"),
        "api_base": os.getenv("DOUBAO_API_BASE")
    },
    # 豆包模型配置
    "doubao": {
        "api_key": os.getenv("DOUBAO_API_KEY"),
        "api_base": os.getenv("DOUBAO_API_BASE")
    },
    # DeepSeek模型配置（作为降级模型）
    "deepseek": {
        "api_key": os.getenv("DEEPSEEK_API_KEY"),
        "api_base": os.getenv("DEEPSEEK_API_BASE")
    },
    # 可以添加更多模型配置
    "other_model": {
        "api_key": os.getenv("OTHER_MODEL_API_KEY"),
        "api_base": os.getenv("OTHER_MODEL_API_BASE")
    }
}

# 模型ID配置
DOUBAO_MODEL = os.getenv("DOUBAO_MODEL")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL")

# 默认降级模型名称
DEFAULT_FALLBACK_MODEL = "deepseek"


# 模型参数
DEFAULT_TEMPERATURE = float(os.getenv("DEFAULT_TEMPERATURE", "0.7"))
DEFAULT_MAX_TOKENS = int(os.getenv("DEFAULT_MAX_TOKENS", "2048"))

# === 提示词管理工具配置 ===
DEFAULT_PROMPT_VERSION = os.getenv("DEFAULT_PROMPT_VERSION", "v1.0")
DEFAULT_LANGUAGE = os.getenv("DEFAULT_LANGUAGE", "zh")
DEFAULT_TOP_P = float(os.getenv("DEFAULT_TOP_P", "0.9"))

# 提示词模板功能类型映射
AVAILABLE_FUNCTION_TYPES = {
    "en": ["fault_diagnosis", "conversation_topic", "multi_fault_split"],
    "zh": ["故障诊断", "对话主题", "多故障拆分"]
}

CONTEXT_WINDOW_MAX_MESSAGES = int(os.getenv("CONTEXT_WINDOW_MAX_MESSAGES", "30"))

# === 响应处理工具配置 ===
DEFAULT_FORMAT_TYPE = os.getenv("DEFAULT_FORMAT_TYPE", "json")
DEFAULT_SUMMARY_LENGTH = int(os.getenv("DEFAULT_SUMMARY_LENGTH", "200"))
ENABLE_AUTO_SUMMARY = os.getenv(
    "ENABLE_AUTO_SUMMARY", "false").lower() == "true"

# === 错误处理工具配置 ===
DEFAULT_MAX_RETRIES = int(os.getenv("DEFAULT_MAX_RETRIES", "3"))
DEFAULT_RETRY_DELAY = float(os.getenv("DEFAULT_RETRY_DELAY", "1.0"))

# === 成本统计工具配置 ===
COST_TRACKING_ENABLED = os.getenv(
    "COST_TRACKING_ENABLED", "true").lower() == "true"
USAGE_STATS_FILE = os.getenv("USAGE_STATS_FILE", "usage_stats.json")
TOKEN_PRICE_PER_1K = float(os.getenv("TOKEN_PRICE_PER_1K", "0.001"))

# === 上下文管理配置 ===
DEFAULT_CHAT_TYPE = os.getenv("DEFAULT_CHAT_TYPE", "GENERAL")

K_USE_WORKFLOW = os.getenv("KNOWLEDGE_USE_WORKFLOW", "1") == "1"

# === 日志配置 ===
# 日志级别配置 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
# log_mode设置为0表示不保存任何请求数据
# 设置为1表示只在返回code不为0时保存请求数据
# 设置为2表示保存所有的请求数据(正式环境中不要设置为2)
LOG_MODE = int(os.getenv('LOG_MODE', '1'))

# 常规日志允许的最大磁盘空间, 单位MB
LOG_MAX_BYTES = int(os.getenv('LOG_MAX_BYTES', '1024'))
# 存储算法输入输出内容允许的最大磁盘空间, 单位MB
MSG_MAX_BYTES = int(os.getenv('MSG_MAX_BYTES', '1024'))
# 日志清理间隔, 单位天
CLEAN_INTERVAL = int(os.getenv('CLEAN_INTERVAL', '1'))

# 日志文件路径配置
LOG_DIR = os.getenv('LOG_DIR', 'logs')
LOG_FILE_NAME = os.getenv('LOG_FILE_NAME', 'app.log')
LOG_FILE_MAX_SIZE = os.getenv('LOG_FILE_MAX_SIZE', '10 MB')
LOG_FILE_RETENTION = int(os.getenv('LOG_FILE_RETENTION', '10'))
LOG_FILE_BACKUP_COUNT = int(os.getenv('LOG_FILE_BACKUP_COUNT', '10'))

# === 阿里云日志服务配置 ===
# 启用阿里云日志服务
ENABLE_ALIBABA_CLOUD_LOG = os.getenv('ENABLE_ALIBABA_CLOUD_LOG', 'false').lower() == 'true'
# 阿里云日志服务配置
ALIBABA_CLOUD_ACCESS_KEY_ID = os.getenv('ALIBABA_CLOUD_ACCESS_KEY_ID', '')
ALIBABA_CLOUD_ACCESS_KEY_SECRET = os.getenv('ALIBABA_CLOUD_ACCESS_KEY_SECRET', '')
ALIBABA_CLOUD_LOG_ENDPOINT = os.getenv('ALIBABA_CLOUD_LOG_ENDPOINT', 'cn-hangzhou.log.aliyuncs.com')
ALIBABA_CLOUD_LOG_PROJECT = os.getenv('ALIBABA_CLOUD_LOG_PROJECT', '')
ALIBABA_CLOUD_LOG_LOGSTORE = os.getenv('ALIBABA_CLOUD_LOG_LOGSTORE', '')
ALIBABA_CLOUD_LOG_LEVEL = os.getenv('ALIBABA_CLOUD_LOG_LEVEL', 'INFO')
ALIBABA_CLOUD_LOG_BUFFER_SIZE = int(os.getenv('ALIBABA_CLOUD_LOG_BUFFER_SIZE', '100'))

# === 项目配置 ===
# 当前启用的项目编码，若为0，则认为只使用基础内容，无项目内容
USE_PROJECT = os.getenv('USE_PROJECT', '0')

# === 应用程序端口配置 ===
APP_PORT = int(os.getenv('APP_PORT', '10090'))

# === 管工厂API配置 ===
GLOBAL_QUERY_KEY = os.getenv("GLOBAL_QUERY_KEY", "eff41702-ed17-4ef6-9160-d7f83a5cc504")
GLOBAL_QUERY_FACTORY_ID = os.getenv("GLOBAL_QUERY_FACTORY_ID", "2c9380827e293e30017e5635960b01f3")
GLOBAL_QUERY_PHONE = os.getenv("GLOBAL_QUERY_PHONE", "18868818425")
GLOBAL_QUERY_LANGUAGE = os.getenv("GLOBAL_QUERY_LANGUAGE", "cn")
GLOBAL_QUERY_TYPE = os.getenv("GLOBAL_QUERY_TYPE", "android")
GLOBAL_PAGE_SIZE = int(os.getenv("GLOBAL_PAGE_SIZE", "200"))
API_BASE_URL = os.getenv("API_BASE_URL", "https://cloud-aws.gomake.cn/highnet_light")
# 前端根地址（用于 token_login GET 重定向等，无法从 request 推断时使用）
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:9097").rstrip("/")

# === 扩展模型配置 ===
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "gpt-4o-mini")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "qwen3-max")
# Lane A - 第一阶段：快速决策（追求速度，<2s）
LANE_A_DECISION_MODEL = os.getenv("LANE_A_DECISION_MODEL", "qwen3.5-flash")
# Knowledge Step1 - 工具选择决策（与 Lane A 同级）
KNOWLEDGE_STEP1_MODEL = os.getenv("KNOWLEDGE_STEP1_MODEL", "qwen3.5-flash")
# Lane A - 第二阶段：内容生成（流式输出，追求质量与速度平衡）
LANE_A_GENERATION_MODEL = os.getenv("LANE_A_GENERATION_MODEL", "qwen3-max")
LANE_A_GENERATION_MAX_TOKENS = int(os.getenv("LANE_A_GENERATION_MAX_TOKENS", "600"))
# Lane A 决策模式：true=Function Calling，false=JSON 解析
LANE_A_USE_FC = os.getenv("LANE_A_USE_FC", "true").lower() == "true"
# Lane A LLM 超时配置
LANE_A_LLM_TIMEOUT = float(os.getenv("LANE_A_LLM_TIMEOUT", "60.0"))
LANE_A_GENERATION_TIMEOUT = float(os.getenv("LANE_A_GENERATION_TIMEOUT", "120.0"))
# Lane 2 - 对话深度摘要
# false 时完全回滚（不触发 worker、不加载 summary、不注入），行为与未引入 Lane 2 时完全一致
LANE2_ENABLED = os.getenv('LANE2_ENABLED', 'true').lower() == 'true'
# Lane 2 worker 的 LLM 模型；主线模型不受影响
LANE2_MODEL = os.getenv('FAULT_DIAGNOSE_LANE2_MODEL', 'qwen3-max')
# 知识库 Lane 2 模型
KNOWLEDGE_LANE2_MODEL = os.getenv('KNOWLEDGE_LANE2_MODEL', 'qwen3-max')

# Lane 3 - 用户级画像 Worker（跨 session 聚合）
LANE3_ENABLED = os.getenv('LANE3_ENABLED', 'false').lower() == 'true'
LANE3_MODEL = os.getenv('FAULT_DIAGNOSE_LANE3_MODEL', 'qwen3-max')
LANE3_SKIP_USERNAMES_STR = os.getenv('LANE3_SKIP_USERNAMES', 'whitelist')
LANE3_SKIP_USERNAMES = {u.strip() for u in LANE3_SKIP_USERNAMES_STR.split(',') if u.strip()}
# 单 user 参与聚合的 chat 上限（最近 N 条）
LANE3_MAX_CHATS_PER_USER = int(os.getenv('LANE3_MAX_CHATS_PER_USER', '30'))
# 最少 summary 样本数：低于这个数不生成 profile（数据太少无意义）
LANE3_MIN_SUMMARIES = int(os.getenv('LANE3_MIN_SUMMARIES', '2'))
OPENAI_EMBEDDING_URL = os.getenv("OPENAI_EMBEDDING_URL", "")

# ===== 知识库 Collection 名称（逗号分隔，支持多 collection 并行检索）=====
COLLECTION_MANUAL = _SHARED_COLLECTION_MANUAL        # "manual_total,manual_chunks"
COLLECTION_MANUAL_TOTAL = _SHARED_COLLECTION_MANUAL_TOTAL
COLLECTION_MANUAL_CHUNKS = _SHARED_COLLECTION_MANUAL_CHUNKS
COLLECTION_PRINCIPLE = _SHARED_COLLECTION_PRINCIPLE  # "principle_total,principle_chunks"
COLLECTION_PRINCIPLE_TOTAL = _SHARED_COLLECTION_PRINCIPLE_TOTAL
COLLECTION_PRINCIPLE_CHUNKS = _SHARED_COLLECTION_PRINCIPLE_CHUNKS
COLLECTION_REPAIR = _SHARED_COLLECTION_REPAIR        # "repair_doc_chunks"
COLLECTION_VIDEO = _SHARED_COLLECTION_VIDEO          # "video_description"
COLLECTION_WORKORDER = _SHARED_COLLECTION_WORKORDER  # "workorder_graph,workorder,special_workorder"
COLLECTION_WORKORDER_GRAPH_IDENTIFIER = _SHARED_WORKORDER_GRAPH  # 工单图谱集合标识（用于区分 Neo4j 路径）
COLLECTION_PARAMETER = _SHARED_COLLECTION_PARAMETER       # 逗号分隔，空 = 不检索
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-v4")
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_API_URL = os.getenv("DASHSCOPE_API_URL", "https://dashscope-intl.aliyuncs.com/api/v1")

# === 模块配置 ===
MODULE_BASE_URL = os.getenv("MODULE_BASE_URL")


# === 豆包语音配置 ===
DOUBAO_APP_ID = os.getenv("DOUBAO_APP_ID")
DOUBAO_ACCESS_TOKEN = os.getenv("DOUBAO_ACCESS_TOKEN")

# === 鉴权配置 ===
AUTH_SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "your-secret-key-here-change-in-production")
AUTH_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("AUTH_ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
AUTH_REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("AUTH_REFRESH_TOKEN_EXPIRE_DAYS", "7"))
AUTH_ALGORITHM = os.getenv("AUTH_ALGORITHM", "HS256")  # 统一JWT生成算法（不分source）

# 路由认证开关
ENABLE_AUTHENTICATION = os.getenv("ENABLE_AUTHENTICATION", "true").lower() == "true"

# IP白名单配置（这些IP无需认证即可访问）
# 从环境变量读取，用逗号分隔
AUTH_IP_WHITELIST_STR = os.getenv("AUTH_IP_WHITELIST", "")
AUTH_IP_WHITELIST = [ip.strip() for ip in AUTH_IP_WHITELIST_STR.split(",") if ip.strip()] if AUTH_IP_WHITELIST_STR else []

# === CORS跨域配置 ===
# 从环境变量读取允许的源，用逗号分隔
CORS_ORIGINS_STR = os.getenv("CORS_ORIGINS", "")
CORS_ORIGINS = [origin.strip() for origin in CORS_ORIGINS_STR.split(",") if origin.strip()] if CORS_ORIGINS_STR else [
    "http://localhost:9097",
    "http://127.0.0.1:9097",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:10090",
    "http://127.0.0.1:10090",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "http://localhost:9098"
]

# JWT Provider配置（用于解析外部Token）
JWT_PROVIDER_CONFIGS = {
    "default": {
        "secret_key": os.getenv("JWT_DEFAULT_SECRET_KEY", AUTH_SECRET_KEY),
        "algorithm": os.getenv("JWT_DEFAULT_ALGORITHM", AUTH_ALGORITHM)
    },
    # === Platform Backend适配配置 ===
    # 售后系统配置（使用HMAC签名验证）
    "aftersales": {
        "secret_key": os.getenv("AFTERSALES_SECRET_KEY", "aftersales_secret_key_2025_change_in_production"),
        "algorithm": "HS256"
    },
    # 管工厂云配置（使用MD5签名验证）
    "factory": {
        "secret_key": os.getenv("FACTORY_SECRET_KEY", "your-secret-key-change-me"),
        "algorithm": "MD5",
        "source_id": "003"  # 管工厂到AI的固定sourceid
    },
    # 设备控制器配置（使用HMAC签名验证）
    "controller": {
        "secret_key": os.getenv("CONTROLLER_SECRET_KEY", "your-secret-key-change-me"),
        "algorithm": "HS256"
    }
}

# 图片文件前缀
IMAGE_URL_PREFIX = os.getenv("IMAGE_URL_PREFIX", "")

# === 会话清理配置 ===
# 启用自动清理过期会话和令牌
ENABLE_AUTO_CLEANUP = os.getenv('ENABLE_AUTO_CLEANUP', 'true').lower() == 'true'
# 清理任务执行间隔（小时）
CLEANUP_INTERVAL_HOURS = int(os.getenv('CLEANUP_INTERVAL_HOURS', '24'))

# === ESB(DeviceScanInfo) 接口配置 ===
ESB_BASE_URL = os.getenv("ESB_BASE_URL", "").rstrip("/")
ESB_EXECUTE_PATH = os.getenv("ESB_EXECUTE_PATH", "/api/esb/execute")
ESB_APPKEY = os.getenv("ESB_APPKEY", "cfdef7b5-b8d6-42bf-8707-d01c2c153206").strip()
ESB_USERNAME = os.getenv("ESB_USERNAME", "").strip()
ESB_PASSWORD = os.getenv("ESB_PASSWORD", "").strip()
ESB_CREDENTIAL_ENCRYPTION = os.getenv("ESB_CREDENTIAL_ENCRYPTION", "").strip()
ESB_SECRET_KEY = os.getenv("ESB_SECRET_KEY", "").strip()
ESB_TIMEOUT_SECONDS = float(os.getenv("ESB_TIMEOUT_SECONDS", "30"))
