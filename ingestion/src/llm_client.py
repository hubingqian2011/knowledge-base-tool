# -*- coding: utf-8 -*-
"""
Agent 1: 文档知识提取 Agent - LLM 客户端

支持：
1. 文本模型调用 (qwen3-max)
2. 视觉模型调用 (qwen3.6-plus)
3. 向量模型调用 (text-embedding-v4)

版本: 1.0
"""

import os
import json
import re
import sys
import time
import base64
import logging
import random
from pathlib import Path
from threading import Lock
from typing import List, Dict, Optional, Union
import openai
from openai import OpenAI

# sys.path 确保 ingestion/core/ 可以被导入（即使独立运行时也有效）
_INGESTION_DIR = Path(__file__).resolve().parent.parent
if str(_INGESTION_DIR) not in sys.path:
    sys.path.insert(0, str(_INGESTION_DIR))

from core.errors import EmbeddingAPIError, FatalError
from .config import LLMConfig

# 获取 logger
logger = logging.getLogger("agent1.llm_client")


# ============================================================
# Token 使用统计
# ============================================================

_token_lock = Lock()
_token_usage: Dict[str, Union[int, Dict[str, Dict[str, int]]]] = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "by_model": {},  # model -> {"prompt_tokens", "completion_tokens", "total_tokens"}
}


def _record_token_usage(model: str, usage: Optional[object], is_embedding: bool = False) -> None:
    """
    记录一次调用的 token 消耗。

    兼容 OpenAI / DashScope 兼容模式的 usage 字段，如果缺失则跳过。
    """
    if usage is None:
        return

    # OpenAI 风格：usage.prompt_tokens / usage.completion_tokens / usage.total_tokens
    prompt_tokens = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", 0)
    total_tokens = getattr(usage, "total_tokens", None)

    # 有些兼容实现只有 total_tokens
    if prompt_tokens is None and hasattr(usage, "total_tokens"):
        # 对于 embedding，我们只统计成 prompt_tokens
        if is_embedding:
            prompt_tokens = getattr(usage, "total_tokens", 0)
            total_tokens = prompt_tokens
            completion_tokens = 0
        else:
            total_tokens = getattr(usage, "total_tokens", 0)

    if prompt_tokens is None and total_tokens is None:
        # 没有任何可用字段，跳过
        return

    if prompt_tokens is None:
        # 兜底：当只有 total_tokens 时，全部记到 prompt 侧
        prompt_tokens = total_tokens or 0
    if total_tokens is None:
        total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)

    with _token_lock:
        _token_usage["prompt_tokens"] = int(_token_usage["prompt_tokens"]) + int(prompt_tokens or 0)
        _token_usage["completion_tokens"] = int(_token_usage["completion_tokens"]) + int(completion_tokens or 0)
        _token_usage["total_tokens"] = int(_token_usage["total_tokens"]) + int(total_tokens or 0)

        by_model: Dict[str, Dict[str, int]] = _token_usage["by_model"]  # type: ignore[assignment]
        if model not in by_model:
            by_model[model] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }
        m = by_model[model]
        m["prompt_tokens"] += int(prompt_tokens or 0)
        m["completion_tokens"] += int(completion_tokens or 0)
        m["total_tokens"] += int(total_tokens or 0)

        logger.info(
            f"Token 使用 | 模型={model} | 本次: prompt={prompt_tokens}, completion={completion_tokens}, total={total_tokens} | "
            f"累计 total={_token_usage['total_tokens']}"
        )


def get_token_usage_summary() -> Dict[str, Union[int, Dict[str, Dict[str, int]]]]:
    """获取当前进程内的 token 使用统计快照。"""
    with _token_lock:
        # 浅拷贝，避免外部修改内部状态
        by_model = {
            model: stats.copy() for model, stats in (_token_usage["by_model"] or {}).items()  # type: ignore[index]
        }
        return {
            "prompt_tokens": int(_token_usage["prompt_tokens"]),
            "completion_tokens": int(_token_usage["completion_tokens"]),
            "total_tokens": int(_token_usage["total_tokens"]),
            "by_model": by_model,
        }


# ============================================================
# 全局客户端
# ============================================================

_client: Optional[OpenAI] = None


def _disable_thinking_kwargs(model: str) -> Dict:
    model_name = (model or "").strip().lower()
    if not model_name.startswith("qwen"):
        return {}
    api_base = (LLMConfig.API_BASE or "").strip().lower()
    if "dashscope" in api_base:
        return {
            "extra_body": {
                "enable_thinking": False
            }
        }
    return {
        "extra_body": {
            "chat_template_kwargs": {
                "enable_thinking": False
            }
        }
    }


def get_client() -> OpenAI:
    """获取或创建 OpenAI 客户端"""
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=LLMConfig.API_KEY,
            base_url=LLMConfig.API_BASE
        )
        logger.info(f"LLM 客户端初始化完成, API Base: {LLMConfig.API_BASE}")
    return _client


# ============================================================
# JSON 解析
# ============================================================

def parse_json_response(content: str) -> Optional[Dict]:
    """
    从 LLM 响应中解析 JSON
    
    支持多种格式：
    1. 纯 JSON
    2. ```json ... ```
    3. ``` ... ```
    4. 文本中嵌入的 JSON
    """
    if not content:
        return None
    
    content = content.strip()
    
    # 方法1: 直接解析
    try:
        return json.loads(content)
    except:
        pass
    
    # 方法2: 提取 ```json ... ```
    match = re.search(r'```json\s*([\s\S]*?)\s*```', content)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except:
            pass
    
    # 方法3: 提取 ``` ... ```
    match = re.search(r'```\s*([\s\S]*?)\s*```', content)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except:
            pass
    
    # 方法4: 提取第一个{到最后一个}
    first = content.find('{')
    last = content.rfind('}')
    if first != -1 and last > first:
        try:
            return json.loads(content[first:last+1])
        except:
            pass
    
    # 方法5: 提取第一个[到最后一个]（数组格式）
    first = content.find('[')
    last = content.rfind(']')
    if first != -1 and last > first:
        try:
            return json.loads(content[first:last+1])
        except:
            pass
    
    logger.warning(f"JSON 解析失败: {content[:200]}...")
    return None


# ============================================================
# 文本模型调用
# ============================================================

def call_llm_text(
    system_prompt: str,
    user_prompt: str,
    model: Optional[str] = None,
    temperature: float = None,
    max_tokens: int = None,
    max_retries: int = None,
    parse_json: bool = True
) -> Optional[Union[Dict, str]]:
    """
    调用文本生成模型
    
    参数:
        system_prompt: 系统提示词
        user_prompt: 用户提示词
        temperature: 温度参数
        max_tokens: 最大 token 数
        max_retries: 最大重试次数
        parse_json: 是否解析 JSON
    
    返回:
        如果 parse_json=True，返回解析后的 JSON Dict
        如果 parse_json=False，返回原始文本
    """
    client = get_client()
    selected_model = model or LLMConfig.MODEL_TEXT
    temperature = temperature or LLMConfig.TEMPERATURE
    max_tokens = max_tokens or LLMConfig.MAX_TOKENS
    max_retries = max_retries or LLMConfig.MAX_RETRIES
    
    logger.debug(f"调用文本模型: {selected_model}")
    logger.debug(f"System Prompt 长度: {len(system_prompt)}")
    logger.debug(f"User Prompt 长度: {len(user_prompt)}")
    request_kwargs = _disable_thinking_kwargs(selected_model)
    
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=selected_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                **request_kwargs,
            )
            
            # 记录 token 使用
            try:
                _record_token_usage(selected_model, getattr(response, "usage", None))
            except Exception as e:  # 防御性：统计失败不能影响主流程
                logger.warning(f"记录文本模型 token 使用失败: {e}")

            content = response.choices[0].message.content.strip()
            logger.debug(f"LLM 响应长度: {len(content)}")
            
            if parse_json:
                result = parse_json_response(content)
                if result is not None:
                    return result
                
                # JSON 解析失败，重试
                if attempt < max_retries - 1:
                    logger.warning(f"JSON 解析失败，重试 ({attempt + 1}/{max_retries})")
                    time.sleep(0.5)
                    continue
                
                logger.error(f"JSON 解析失败: {content[:500]}...")
                return None
            else:
                return content
            
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"LLM 调用失败: {e}，重试 ({attempt + 1}/{max_retries})")
                time.sleep(1)
                continue
            
            logger.error(f"LLM 调用失败: {e}")
            return None
    
    return None


# ============================================================
# 视觉模型调用
# ============================================================

def encode_image_to_base64(image_path: Union[str, Path]) -> str:
    """将图片编码为 base64"""
    image_path = Path(image_path)
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def get_image_media_type(image_path: Union[str, Path]) -> str:
    """获取图片的 MIME 类型"""
    suffix = Path(image_path).suffix.lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp"
    }
    return media_types.get(suffix, "image/png")


def call_llm_visual(
    system_prompt: str,
    user_prompt: str,
    image_paths: List[Union[str, Path]],
    model: Optional[str] = None,
    temperature: float = None,
    max_tokens: int = None,
    max_retries: int = None,
    parse_json: bool = True
) -> Optional[Union[Dict, str]]:
    """
    调用视觉模型分析图片
    
    参数:
        system_prompt: 系统提示词
        user_prompt: 用户提示词
        image_paths: 图片路径列表
        temperature: 温度参数
        max_tokens: 最大 token 数
        max_retries: 最大重试次数
        parse_json: 是否解析 JSON
    
    返回:
        如果 parse_json=True，返回解析后的 JSON Dict
        如果 parse_json=False，返回原始文本
    """
    client = get_client()
    selected_model = model or LLMConfig.MODEL_VISUAL
    temperature = temperature or LLMConfig.TEMPERATURE
    max_tokens = max_tokens or LLMConfig.MAX_TOKENS
    max_retries = max_retries or LLMConfig.MAX_RETRIES
    
    logger.debug(f"调用视觉模型: {selected_model}")
    logger.debug(f"图片数量: {len(image_paths)}")
    request_kwargs = _disable_thinking_kwargs(selected_model)
    
    # 构建消息内容
    user_content = []
    
    # 添加图片
    for img_path in image_paths:
        img_path = Path(img_path)
        if not img_path.exists():
            logger.warning(f"图片不存在: {img_path}")
            continue
        
        base64_image = encode_image_to_base64(img_path)
        media_type = get_image_media_type(img_path)
        
        user_content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:{media_type};base64,{base64_image}"
            }
        })
        logger.debug(f"添加图片: {img_path.name}")
    
    # 添加文本
    user_content.append({
        "type": "text",
        "text": user_prompt
    })
    
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=selected_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                **request_kwargs,
            )
            
            # 记录 token 使用（视觉模型）
            try:
                _record_token_usage(selected_model, getattr(response, "usage", None))
            except Exception as e:  # 防御性：统计失败不能影响主流程
                logger.warning(f"记录视觉模型 token 使用失败: {e}")

            content = response.choices[0].message.content.strip()
            logger.debug(f"视觉模型响应长度: {len(content)}")
            
            if parse_json:
                result = parse_json_response(content)
                if result is not None:
                    return result
                
                if attempt < max_retries - 1:
                    logger.warning(f"JSON 解析失败，重试 ({attempt + 1}/{max_retries})")
                    time.sleep(0.5)
                    continue
                
                logger.error(f"JSON 解析失败: {content[:500]}...")
                return None
            else:
                return content
            
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"视觉模型调用失败: {e}，重试 ({attempt + 1}/{max_retries})")
                time.sleep(1)
                continue
            
            logger.error(f"视觉模型调用失败: {e}")
            return None
    
    return None


# ============================================================
# 向量模型调用
# ============================================================

def get_embedding(text: str, max_retries: int = None) -> Optional[List[float]]:
    """
    获取单个文本的向量
    
    参数:
        text: 输入文本
        max_retries: 最大重试次数
    
    返回:
        向量列表，失败返回 None
    """
    client = get_client()
    max_retries = max_retries or LLMConfig.MAX_RETRIES
    
    for attempt in range(max_retries):
        try:
            response = client.embeddings.create(
                model=LLMConfig.MODEL_EMBEDDING,
                input=text
            )
            # 记录 token 使用（向量模型）
            try:
                _record_token_usage(LLMConfig.MODEL_EMBEDDING, getattr(response, "usage", None), is_embedding=True)
            except Exception as e:  # 防御性：统计失败不能影响主流程
                logger.warning(f"记录向量模型 token 使用失败: {e}")
            return response.data[0].embedding
            
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"向量获取失败: {e}，重试 ({attempt + 1}/{max_retries})")
                time.sleep(1)
                continue
            
            logger.error(f"向量获取失败: {e}")
            return None
    
    return None


def get_embeddings_batch(
    texts: List[str],
    batch_size: int = None,
    show_progress: bool = True,
    progress_callback=None,
) -> Optional[List[List[float]]]:
    """
    批量获取文本向量
    
    参数:
        texts: 文本列表
        batch_size: 每批次大小
        show_progress: 是否显示进度
    
    返回:
        向量列表，失败返回 None
    """
    client = get_client()
    batch_size = batch_size or LLMConfig.EMBEDDING_BATCH_SIZE
    
    all_embeddings = []
    n_batches = (len(texts) + batch_size - 1) // batch_size
    
    if show_progress:
        from tqdm import tqdm
        iterator = tqdm(range(0, len(texts), batch_size), desc="计算向量", total=n_batches)
    else:
        iterator = range(0, len(texts), batch_size)
    
    for batch_idx, i in enumerate(iterator):
        batch_texts = texts[i:i + batch_size]

        for attempt in range(LLMConfig.MAX_RETRIES):
            try:
                response = client.embeddings.create(
                    model=LLMConfig.MODEL_EMBEDDING,
                    input=batch_texts,
                    dimensions=2048,
                    timeout=30,
                )
                try:
                    _record_token_usage(
                        LLMConfig.MODEL_EMBEDDING,
                        getattr(response, "usage", None),
                        is_embedding=True,
                    )
                except Exception as e:  # 防御性：统计失败不能影响主流程
                    logger.warning(f"记录批量向量模型 token 使用失败: {e}")

                # 按原顺序排列
                batch_embeddings = [None] * len(batch_texts)
                for item in response.data:
                    batch_embeddings[item.index] = item.embedding

                all_embeddings.extend(batch_embeddings)

                # 每 5 批或最后一批打印进度
                if (batch_idx + 1) % 5 == 0 or i + batch_size >= len(texts):
                    done = min(i + batch_size, len(texts))
                    logger.info(f"[Embedding] 批次 {batch_idx+1}/{n_batches} | 已完成 {done}/{len(texts)}")
                if progress_callback:
                    done = min(i + batch_size, len(texts))
                    progress_callback(
                        {
                            "batch_index": batch_idx + 1,
                            "total_batches": n_batches,
                            "done": done,
                            "total": len(texts),
                        }
                    )

                break

            except (openai.AuthenticationError, openai.BadRequestError) as e:
                # 认证/参数错误，重试也没用，直接致命
                raise FatalError(
                    f"Embedding API 致命错误 (批次 {batch_idx+1}): {type(e).__name__}: {e}",
                    cause=e,
                )

            except (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError) as e:
                exc = EmbeddingAPIError(
                    f"Embedding API 临时失败 (批次 {batch_idx+1}): {type(e).__name__}: {e}",
                    cause=e,
                )
                if attempt < LLMConfig.MAX_RETRIES - 1:
                    base_sleep = 2 ** attempt  # 1s → 2s → 4s
                    sleep_time = base_sleep * (1 + random.uniform(-0.3, 0.3))
                    logger.warning(
                        f"批次 {i//batch_size + 1} 向量获取失败: {exc}，"
                        f"重试 ({attempt + 1}/{LLMConfig.MAX_RETRIES})，等待 {sleep_time:.1f}s"
                    )
                    time.sleep(sleep_time)
                    continue
                logger.error(
                    f"Embedding 重试耗尽 (批次 {batch_idx+1}/{n_batches}): "
                    f"{type(e).__name__}: {e} | 影响 {len(batch_texts)} 条文本"
                )
                all_embeddings.extend([None] * len(batch_texts))
                if progress_callback:
                    done = min(i + batch_size, len(texts))
                    progress_callback(
                        {
                            "batch_index": batch_idx + 1,
                            "total_batches": n_batches,
                            "done": done,
                            "total": len(texts),
                        }
                    )

            except Exception as e:
                if attempt < LLMConfig.MAX_RETRIES - 1:
                    base_sleep = 2 ** attempt  # 1s → 2s → 4s
                    sleep_time = base_sleep * (1 + random.uniform(-0.3, 0.3))
                    logger.warning(
                        f"批次 {i//batch_size + 1} 向量获取失败: {e}，"
                        f"重试 ({attempt + 1}/{LLMConfig.MAX_RETRIES})，等待 {sleep_time:.1f}s"
                    )
                    time.sleep(sleep_time)
                    continue
                logger.error(
                    f"Embedding 重试耗尽 (批次 {batch_idx+1}/{n_batches}): "
                    f"{type(e).__name__}: {e} | 影响 {len(batch_texts)} 条文本"
                )
                all_embeddings.extend([None] * len(batch_texts))
                if progress_callback:
                    done = min(i + batch_size, len(texts))
                    progress_callback(
                        {
                            "batch_index": batch_idx + 1,
                            "total_batches": n_batches,
                            "done": done,
                            "total": len(texts),
                        }
                    )
        
        time.sleep(0.1)  # 避免限流
    
    # 检查是否有失败的
    if None in all_embeddings:
        logger.warning(f"{all_embeddings.count(None)} 个文本向量获取失败")
    
    return all_embeddings


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    
    from Agent1.src.config import init_config
    
    # 设置日志
    logging.basicConfig(level=logging.DEBUG)
    
    # 初始化
    init_config()
    
    print("\n" + "=" * 60)
    print("测试文本模型")
    print("=" * 60)
    
    result = call_llm_text(
        system_prompt="你是一个助手",
        user_prompt="请返回 JSON 格式: {\"test\": \"hello\", \"status\": \"ok\"}"
    )
    print(f"文本模型结果: {result}")
    
    print("\n" + "=" * 60)
    print("测试向量模型")
    print("=" * 60)
    
    vec = get_embedding("液压安全检测开关无反馈")
    if vec:
        print(f"向量维度: {len(vec)}")
        print(f"向量前5维: {vec[:5]}")
