# -*- coding: utf-8 -*-
"""
基于OpenAI库的通用大模型服务封装
支持多种模型、流式和非流式调用、异步处理等功能
兼容OpenAI、火山引擎、Azure OpenAI等多种API
"""

import os
import json
from typing import Dict, Any, Optional, List, AsyncGenerator, Callable

# OpenAI库导入
from openai import OpenAI, AsyncOpenAI

# 配置导入
from config import config
from config.config import OPENAI_MODEL

# 配置日志
from util.logging.logger import get_logger
logger = get_logger(__name__)


class OpenAIService:
    """
    通用OpenAI服务封装类
    基于OpenAI SDK实现与多种API的兼容调用
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, model: Optional[str] = None):
        """
        初始化OpenAI服务实例

        Args:
            api_key: API密钥，如果为None则从环境变量获取
            base_url: API基础URL，如果为None则使用默认OpenAI URL
            model: 默认模型名称
        """
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        self.default_model = model or OPENAI_MODEL

        # 初始化同步和异步客户端
        self.sync_client = None
        self.async_client = None
        self._init_clients()

    def _init_clients(self):
        """初始化同步和异步OpenAI客户端"""
        try:
            # 创建同步客户端
            client_kwargs = {"api_key": self.api_key}
            if self.base_url:
                client_kwargs["base_url"] = self.base_url

            self.sync_client = OpenAI(**client_kwargs)
            self.async_client = AsyncOpenAI(**client_kwargs)

            logger.info("OpenAI客户端初始化成功")

        except Exception as e:
            logger.error(f"OpenAI客户端初始化失败: {str(e)}")
            raise

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = "auto",
        **kwargs
    ) -> Any:
        """
        同步聊天补全接口

        Args:
            messages: 对话消息列表
            model: 模型名称，如果为None则使用默认模型
            temperature: 温度参数
            max_tokens: 最大token数
            stream: 是否流式返回
            tools: 工具定义列表
            tool_choice: 工具选择策略，默认为"auto"
            **kwargs: 其他参数

        Returns:
            OpenAI ChatCompletion对象或流式生成器
        """
        try:
            # 使用默认模型
            model = model or self.default_model

            # 构建请求参数
            params = {
                "model": model,
                "messages": messages,
                "extra_body":{
                    "thinking": {"type": "disabled"}
                },
                "stream": stream
            }

            if temperature is not None:
                params["temperature"] = temperature
            if max_tokens is not None:
                params["max_tokens"] = max_tokens
            if tools is not None:
                params["tools"] = tools
                if tool_choice is not None:
                    params["tool_choice"] = tool_choice

            # 添加其他参数
            params.update(kwargs)

            # 调用API
            completion = self.sync_client.chat.completions.create(**params)

            if stream:
                # 返回流式生成器
                return completion
            else:
                # 返回完整响应
                return completion

        except Exception as e:
            logger.error(f"同步聊天补全失败: {str(e)}")
            raise

    async def chat_completion_async(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = "auto",
        call_tag: Optional[str] = None,
        **kwargs
    ) -> Any:
        """
        异步聊天补全接口

        Args:
            messages: 对话消息列表
            model: 模型名称，如果为None则使用默认模型
            temperature: 温度参数
            max_tokens: 最大token数
            stream: 是否流式返回
            tools: 工具定义列表
            tool_choice: 工具选择策略，默认为"auto"
            **kwargs: 其他参数

        Returns:
            OpenAI ChatCompletion对象或异步流式生成器
        """
        try:
            import uuid as _uuid
            from service.system.llm_logger import log_llm_call, log_llm_output, LLM_DEBUG

            call_id = str(_uuid.uuid4())[:8]

            # 使用默认模型（在日志之前解析，这样日志能记录实际模型名）
            model = model or self.default_model

            # 记录完整 LLM 输入（含实际模型名）
            phase = call_tag if call_tag else ("stream" if stream else "sync")
            log_llm_call(
                call_id=call_id,
                phase=phase,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                model=model,
            )

            # 构建请求参数
            params = {
                "model": model,
                "messages": messages,
                "extra_body":{
                    "thinking": {"type": "disabled"}
                },
                "stream": stream
            }

            if temperature is not None:
                params["temperature"] = temperature
            if max_tokens is not None:
                params["max_tokens"] = max_tokens
            if tools is not None:
                params["tools"] = tools
                if tool_choice is not None:
                    params["tool_choice"] = tool_choice

            # 添加其他参数
            params.update(kwargs)

            # 调用API
            completion = await self.async_client.chat.completions.create(**params)

            if stream:
                # 流式：用 wrapper 收集完整输出后记录日志
                async def _logged_stream(raw_stream, _call_id=call_id, _phase=phase):
                    full_content = ""
                    all_tool_calls = {}  # index -> {name, arguments}
                    try:
                        async for chunk in raw_stream:
                            # 累积 content
                            if chunk.choices and chunk.choices[0].delta.content:
                                full_content += chunk.choices[0].delta.content
                            # 累积 tool_calls
                            if chunk.choices and chunk.choices[0].delta.tool_calls:
                                for tc_delta in chunk.choices[0].delta.tool_calls:
                                    idx = tc_delta.index
                                    if idx not in all_tool_calls:
                                        all_tool_calls[idx] = {"name": "", "arguments": ""}
                                    if tc_delta.function:
                                        if tc_delta.function.name:
                                            all_tool_calls[idx]["name"] = tc_delta.function.name
                                        if tc_delta.function.arguments:
                                            all_tool_calls[idx]["arguments"] += tc_delta.function.arguments
                            yield chunk
                    finally:
                        # 流结束后记录完整输出
                        if LLM_DEBUG:
                            tc_list = [all_tool_calls[i] for i in sorted(all_tool_calls)] if all_tool_calls else None
                            log_llm_output(
                                call_id=_call_id,
                                phase=f"{_phase}_complete",
                                content=full_content if full_content else None,
                                tool_calls=tc_list,
                            )

                return _logged_stream(completion)
            else:
                # 非流式：记录完整输出
                if LLM_DEBUG:
                    choice = completion.choices[0]
                    tc_list = None
                    if choice.message.tool_calls:
                        tc_list = [
                            {"name": tc.function.name, "arguments": tc.function.arguments}
                            for tc in choice.message.tool_calls
                        ]
                    log_llm_output(
                        call_id=call_id,
                        phase=f"{phase}_response",
                        content=choice.message.content,
                        tool_calls=tc_list,
                    )
                return completion

        except Exception as e:
            logger.error(f"异步聊天补全失败: {str(e)}")
            raise

    def close(self):
        """关闭客户端连接"""
        if self.sync_client:
            self.sync_client.close()
        if self.async_client:
            # 对于异步客户端，需要通过上下文管理器或其他方式关闭
            pass


# 创建全局默认服务实例
openai_service = OpenAIService()


def chat_completion(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[str] = "auto",
    **kwargs
) -> Any:
    """
    便捷的聊天补全函数

    Args:
        messages: 对话消息列表
        model: 模型名称
        tools: 工具定义列表
        tool_choice: 工具选择策略，默认为"auto"
        **kwargs: 其他参数

    Returns:
        OpenAI ChatCompletion对象或流式生成器
    """
    return openai_service.chat_completion(
        messages=messages,
        model=model,
        tools=tools,
        tool_choice=tool_choice,
        **kwargs
    )


async def async_chat_completion(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[str] = "auto",
    **kwargs
) -> Any:
    """
    便捷的异步聊天补全函数

    Args:
        messages: 对话消息列表
        model: 模型名称
        tools: 工具定义列表
        tool_choice: 工具选择策略，默认为"auto"
        **kwargs: 其他参数

    Returns:
        OpenAI ChatCompletion对象或异步流式生成器
    """
    return await openai_service.chat_completion_async(
        messages=messages,
        model=model,
        tools=tools,
        tool_choice=tool_choice,
        **kwargs
    )


# 创建特定配置的服务实例
def create_service(
    api_key: str,
    base_url: str,
    default_model: str
) -> OpenAIService:
    """
    创建具有特定配置的OpenAI服务实例

    Args:
        api_key: API密钥
        base_url: API基础URL
        default_model: 默认模型名称

    Returns:
        配置好的OpenAIService实例
    """
    return OpenAIService(api_key=api_key, base_url=base_url, model=default_model)