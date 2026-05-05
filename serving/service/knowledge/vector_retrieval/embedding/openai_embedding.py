from openai import OpenAI
import numpy as np
from typing import Optional, List
import time
from .base import BaseEmbedding
from config.config import OPENAI_API_KEY, OPENAI_EMBEDDING_URL, OPENAI_EMBEDDING_MODEL, VECTOR_DIMENSION
from util.logging.logger import get_logger

class OpenaiEmbedding(BaseEmbedding):
    """火山引擎文本嵌入生成器"""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        初始化火山引擎embedding客户端
        
        Args:
            api_key: 火山引擎API密钥，如果为None则从配置中获取
        """
        self.api_key = api_key or OPENAI_API_KEY
        if not self.api_key:
            raise ValueError("API key must be provided either directly or through VOLCENGINE_API_KEY environment variable")
        
        self.client = OpenAI(
            api_key=self.api_key,  # 如果您没有配置环境变量，请在此处用您的API Key进行替换
            base_url=OPENAI_EMBEDDING_URL  # 百炼服务的base_url
        )
        self.model_name = OPENAI_EMBEDDING_MODEL
        self.logger = get_logger(__name__)
    
    def encode(
        self,
        inputs: List[str],
        is_query: bool = False,
        mrl_dim: Optional[int] = VECTOR_DIMENSION,
        max_retries: int = 3,
        base_delay: float = 1.0,
        **kwargs
    ) -> np.ndarray:
        """
        生成文本的嵌入向量
        
        Args:
            inputs: 输入文本列表
            is_query: 是否为查询文本，如果是则添加特定的指令前缀
            mrl_dim: 输出向量的维度，可选值为2048、1024、512、256
            max_retries: 最大重试次数
            base_delay: 初始重试延迟（秒）
            **kwargs: 其他参数
            
        Returns:
            np.ndarray: 归一化后的嵌入向量
        """
        if is_query:
            # 为查询添加指令前缀以优化性能
            inputs = [
                f"Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: {i}"
                for i in inputs
            ]
            
        # 实现指数退避重试机制
        for attempt in range(max_retries + 1):
            try:
                resp = self.client.embeddings.create(
                    model=self.model_name,
                    input=inputs,
                    dimensions=mrl_dim,
                    encoding_format="float",
                )
                return resp.data
            except Exception as e:
                if attempt == max_retries:
                    # 最后一次尝试仍然失败，抛出异常
                    self.logger.error(f"Embedding encode failed after {max_retries} retries: {str(e)}")
                    raise
                else:
                    # 计算延迟时间（指数退避）
                    delay = base_delay * (2 ** attempt) + np.random.uniform(0, 1)
                    self.logger.warning(f"Embedding encode attempt {attempt + 1} failed: {str(e)}. Retrying in {delay:.2f} seconds...")
                    time.sleep(delay)
        
        # # 转换为tensor并处理维度
        # embedding = torch.tensor([d.embedding for d in resp.data], dtype=torch.bfloat16)
        # if mrl_dim is not None:
        #     assert mrl_dim in [2048, 1024, 512, 256], f"mrl_dim must be one of [2048, 1024, 512, 256], got {mrl_dim}"
        #     embedding = embedding[:, :mrl_dim]
            
        # # 归一化以计算余弦相似度
        # embedding = torch.nn.functional.normalize(embedding, dim=1, p=2).float().numpy()
        # return embedding 