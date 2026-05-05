# -*- coding: utf-8 -*-
"""
Agent 1: 文档知识提取 Agent - 配置管理

从 .env 文件读取配置，管理所有配置项。

版本: 1.0
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# DASHSCOPE_API_KEY → LLM_API_KEY 兼容映射（统一入口，其他模块不再重复）
if not os.getenv("LLM_API_KEY") and not os.getenv("OPENAI_API_KEY"):
    _dashscope_key = os.getenv("DASHSCOPE_API_KEY")
    if _dashscope_key:
        os.environ["LLM_API_KEY"] = _dashscope_key


def configure_console_encoding():
    """尽量将标准输出切到 UTF-8，避免 Windows 默认编码导致打印失败。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


configure_console_encoding()


def safe_stdout_flush():
    """安全刷新 stdout，避免后台子进程中断。"""
    stream = sys.stdout
    if stream is None:
        return
    try:
        stream.flush()
    except (OSError, ValueError):
        pass


def safe_console_print(*args, **kwargs):
    """安全打印到控制台，打印失败时不影响主流程。"""
    flush = kwargs.pop("flush", False)
    try:
        print(*args, **kwargs)
    except (OSError, ValueError):
        return
    if flush:
        safe_stdout_flush()


# ============================================================
# TOC 解析 & 章节切分配置
# ============================================================

# TOC 解析兜底切片大小（页数）
TOC_FALLBACK_CHUNK_SIZE = int(os.getenv("TOC_FALLBACK_CHUNK_SIZE", "5"))

# 长章节二次切分阈值
CHAPTER_SPLIT_THRESHOLD = int(os.getenv("CHAPTER_SPLIT_THRESHOLD", "15"))  # 超过15页则切分
TABLE_SPLIT_THRESHOLD = int(os.getenv("TABLE_SPLIT_THRESHOLD", "8"))       # 表格页超过8页则切分
CHAPTER_SPLIT_SIZE = int(os.getenv("CHAPTER_SPLIT_SIZE", "10"))            # 切分粒度（页）
TABLE_SPLIT_SIZE = int(os.getenv("TABLE_SPLIT_SIZE", "5"))                 # 表格切分粒度（页）


# ============================================================
# LLM 配置
# ============================================================

class LLMConfig:
    """LLM 配置"""
    
    # API 配置（兼容 LLM_API_KEY 与 OPENAI_API_KEY，便于复用同一 .env）
    API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    API_BASE = os.getenv("LLM_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    
    # 模型配置
    MODEL_TEXT = os.getenv("LLM_MODEL_text", "qwen3-max")
    MODEL_EMBEDDING = os.getenv("LLM_MODEL_embedding", "text-embedding-v4")
    MODEL_VISUAL = os.getenv("LLM_MODEL_VISUAL", "qwen3.6-plus")
    MODEL_MULTIMODAL = os.getenv("LLM_MODEL_MULTIMODAL", MODEL_VISUAL)
    
    # 调用参数
    TEMPERATURE = 0.1
    MAX_TOKENS = 4000
    MAX_RETRIES = 3
    
    # 批量处理
    EMBEDDING_BATCH_SIZE = 10  # 阿里云限制
    MAX_CONCURRENT_VISUAL_REQUESTS = int(os.getenv("AGENT1_MAX_CONCURRENT_VISUAL_REQUESTS", "4"))
    MAX_CONCURRENT_TEXT_REQUESTS = int(os.getenv("AGENT1_MAX_CONCURRENT_TEXT_REQUESTS", "8"))
    MAX_CONCURRENT_DOCUMENTS = int(os.getenv("AGENT1_MAX_CONCURRENT_DOCUMENTS", "2"))
    MAX_CONCURRENT_CHUNK_TASKS = int(os.getenv("AGENT1_MAX_CONCURRENT_CHUNK_TASKS", "16"))
    PDF_GRAPHRAG_MAX_WORKERS = int(os.getenv("PDF_GRAPHRAG_MAX_WORKERS", "6"))

# ============================================================
# 文档处理配置
# ============================================================

class DocumentConfig:
    """文档处理配置"""
    
    # 支持的文档格式
    SUPPORTED_FORMATS = ['.pdf', '.pptx', '.ppt', '.docx', '.doc']
    
    # 图片格式
    IMAGE_FORMATS = ['.png', '.jpg', '.jpeg', '.gif', '.bmp']
    
    # 文本分片配置
    CHUNK_SIZE = 1500  # 每个片段的最大字符数
    CHUNK_OVERLAP = 200  # 片段之间的重叠字符数
    
    # 图片处理
    MAX_IMAGE_SIZE = (1920, 1080)  # 最大图片尺寸
    IMAGE_QUALITY = 85  # JPEG 压缩质量
    
    # 内容过滤
    MIN_CONTENT_LENGTH = 50  # 最小有效内容长度
    
    # PDF 处理
    PDF_DPI = 150  # PDF 转图片的 DPI


# ============================================================
# 知识抽取配置
# ============================================================

class ExtractionConfig:
    """知识抽取配置"""
    
    # 知识类型
    KNOWLEDGE_TYPES = [
        "部件说明",
        "位置描述",
        "参数规格",
        "工作原理",
        "操作步骤",
        "故障诊断",
        "注意事项",
        "报警说明",
        "图纸示意",
        "其他"
    ]
    
    # 置信度阈值
    CONFIDENCE_THRESHOLD = 0.6  # 低于此值需要人工审核

    # 实体融合：同名实体描述冲突时是否调用 LLM 融合
    MERGE_USE_LLM = os.getenv("AGENT1_MERGE_USE_LLM", "true").lower() in ("1", "true", "yes")
    # 实体语义融合：是否启用 embedding + LLM 二级判定
    ENTITY_SEMANTIC_MERGE_ENABLE = os.getenv("AGENT1_ENTITY_SEMANTIC_MERGE_ENABLE", "true").lower() in ("1", "true", "yes")
    ENTITY_SEMANTIC_LLM_ENABLE = os.getenv("AGENT1_ENTITY_SEMANTIC_LLM_ENABLE", "true").lower() in ("1", "true", "yes")
    ENTITY_SEMANTIC_HIGH_SIM = float(os.getenv("AGENT1_ENTITY_SEMANTIC_HIGH_SIM", "0.90"))
    ENTITY_SEMANTIC_LOW_SIM = float(os.getenv("AGENT1_ENTITY_SEMANTIC_LOW_SIM", "0.78"))
    ENTITY_SEMANTIC_MAX_LLM_PAIRS = int(os.getenv("AGENT1_ENTITY_SEMANTIC_MAX_LLM_PAIRS", "40"))
    
    # 送入 Text LLM 的单次最大字符数（超长会截断，可能漏掉后半段信息；可调大或配合「按长度再分子块」）
    EXTRACTION_MAX_TEXT_CHARS = int(os.getenv("AGENT1_EXTRACTION_MAX_TEXT_CHARS", "3000"))
    
    # 单次 Visual LLM 调用最多图片数；超过则分多批调用再合并，避免漏图或超 API 限制
    EXTRACTION_MAX_IMAGES_PER_VISUAL_CALL = int(os.getenv("AGENT1_EXTRACTION_MAX_IMAGES_PER_VISUAL_CALL", "6"))
    EXTRACTION_CONTINUE_ROUND_ENABLE = os.getenv("AGENT1_EXTRACTION_CONTINUE_ROUND_ENABLE", "true").lower() in ("1", "true", "yes")
    EXTRACTION_CONTINUE_DRAWING_ONLY = os.getenv("AGENT1_EXTRACTION_CONTINUE_DRAWING_ONLY", "true").lower() in ("1", "true", "yes")
    EXTRACTION_CONTINUE_MIN_ANCHORS = int(os.getenv("AGENT1_EXTRACTION_CONTINUE_MIN_ANCHORS", "3"))
    EXTRACTION_CONTINUE_MAX_STABLE_ENTITIES = int(os.getenv("AGENT1_EXTRACTION_CONTINUE_MAX_STABLE_ENTITIES", "2"))
    EXTRACTION_LOOP_THIRD_ROUND_ENABLE = os.getenv("AGENT1_EXTRACTION_LOOP_THIRD_ROUND_ENABLE", "false").lower() in ("1", "true", "yes")
    SAVE_DEBUG_JSON = os.getenv("AGENT1_SAVE_DEBUG_JSON", "false").lower() in ("1", "true", "yes")
    # 文档切分是否启用“特化切分配置”（tools/manual_slice_test/file_config.json）
    # true: 命中特化配置则优先使用；false: 强制回到项目原始分页切分链路（2页+重叠1页）
    PARSER_USE_SPECIAL_SLICE = os.getenv("AGENT1_PARSER_USE_SPECIAL_SLICE", "true").lower() in ("1", "true", "yes")
    
    # 抽取优先级权重
    PRIORITY_WEIGHTS = {
        "故障诊断": 1.0,
        "操作步骤": 0.9,
        "参数规格": 0.85,
        "注意事项": 0.8,
        "报警说明": 0.8,
        "工作原理": 0.7,
        "位置描述": 0.6,
        "部件说明": 0.5,
        "图纸示意": 0.7,
        "其他": 0.3
    }


# ============================================================
# 输出配置
# ============================================================

# Agent1 根目录（src/ 的上一级），output/logs 默认放在该目录下
_AGENT1_DIR = Path(__file__).resolve().parent.parent


class OutputConfig:
    """输出配置"""
    
    # 输出目录（默认在 Agent1 目录下）
    OUTPUT_DIR = _AGENT1_DIR / "output"
    
    # 子目录
    PARSED_DIR = OUTPUT_DIR / "parsed"           # 解析后的文档
    CLASSIFIED_DIR = OUTPUT_DIR / "classified"   # 分类后的片段
    EXTRACTED_DIR = OUTPUT_DIR / "extracted"     # 抽取的知识
    AGGREGATED_DIR = OUTPUT_DIR / "aggregated"   # 聚合后的知识库
    IMAGES_DIR = OUTPUT_DIR / "images"           # 提取的图片
    DEBUG_DIR = OUTPUT_DIR / "debug"             # 旁路调试输出
    
    # 日志目录（默认在 Agent1 目录下）
    LOG_DIR = _AGENT1_DIR / "logs"
    
    # 检查点
    CHECKPOINT_DIR = OUTPUT_DIR / "checkpoints"
    
    @classmethod
    def set_output_root(cls, root: Path):
        """设置输出根目录并同步更新所有子目录（供 main --output_dir 使用）"""
        cls.OUTPUT_DIR = Path(root)
        cls.PARSED_DIR = cls.OUTPUT_DIR / "parsed"
        cls.CLASSIFIED_DIR = cls.OUTPUT_DIR / "classified"
        cls.EXTRACTED_DIR = cls.OUTPUT_DIR / "extracted"
        cls.AGGREGATED_DIR = cls.OUTPUT_DIR / "aggregated"
        cls.IMAGES_DIR = cls.OUTPUT_DIR / "images"
        cls.DEBUG_DIR = cls.OUTPUT_DIR / "debug"
        cls.CHECKPOINT_DIR = cls.OUTPUT_DIR / "checkpoints"
    
    @classmethod
    def ensure_dirs(cls):
        """确保所有目录存在"""
        for dir_path in [
            cls.OUTPUT_DIR,
            cls.PARSED_DIR,
            cls.CLASSIFIED_DIR,
            cls.EXTRACTED_DIR,
            cls.AGGREGATED_DIR,
            cls.IMAGES_DIR,
            cls.DEBUG_DIR,
            cls.LOG_DIR,
            cls.CHECKPOINT_DIR
        ]:
            dir_path.mkdir(parents=True, exist_ok=True)


# ============================================================
# 日志配置
# ============================================================

class LogConfig:
    """日志配置"""
    
    # 日志级别
    LEVEL = "DEBUG"
    
    # 日志格式
    FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s"
    DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
    
    # 文件日志
    FILE_LOG = True
    CONSOLE_LOG = False  # 控制台只输出简要信息
    
    # 日志文件大小限制
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
    BACKUP_COUNT = 5


# ============================================================
# 初始化
# ============================================================

def init_config():
    """初始化配置"""
    # 检查必要配置
    if not LLMConfig.API_KEY:
        raise ValueError("未设置 LLM_API_KEY 或 OPENAI_API_KEY 环境变量，且当前工作目录下未读取到可用 .env")
    
    # 创建输出目录
    OutputConfig.ensure_dirs()
    
    safe_console_print("配置初始化完成")
    safe_console_print(f"   文本模型: {LLMConfig.MODEL_TEXT}")
    safe_console_print(f"   视觉模型: {LLMConfig.MODEL_VISUAL}")
    safe_console_print(f"   向量模型: {LLMConfig.MODEL_EMBEDDING}")
    safe_console_print(f"   输出目录: {OutputConfig.OUTPUT_DIR}")


if __name__ == "__main__":
    init_config()
