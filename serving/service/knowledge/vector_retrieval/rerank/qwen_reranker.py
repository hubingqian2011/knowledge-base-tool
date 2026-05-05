import torch
from typing import List, Dict, Any
from modelscope import AutoModelForCausalLM, AutoTokenizer
from .base import BaseReranker

class QwenReranker(BaseReranker):
    """基于Qwen模型的重排序器实现"""
    
    def __init__(self, model_name: str = "Qwen/Qwen3-Reranker-0.6B", **kwargs):
        """
        初始化Qwen重排序器
        
        Args:
            model_name: 模型名称或路径
            **kwargs: 其他初始化参数
        """
        self.tokenizer = AutoTokenizer.from_pretrained(model_name, padding_side='left')
        self.model = AutoModelForCausalLM.from_pretrained(model_name).eval()
        
        # 获取特殊token的ID
        self.token_false_id = self.tokenizer.convert_tokens_to_ids("no")
        self.token_true_id = self.tokenizer.convert_tokens_to_ids("yes")
        
        # 设置最大长度
        self.max_length = 8192
        
        # 设置提示模板
        self.prefix = "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n<|im_start|>user\n"
        self.suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
        self.prefix_tokens = self.tokenizer.encode(self.prefix, add_special_tokens=False)
        self.suffix_tokens = self.tokenizer.encode(self.suffix, add_special_tokens=False)
        
        # 默认指令
        self.default_instruction = 'Given a web search query, retrieve relevant passages that answer the query'
    
    def _format_instruction(self, query: str, doc: str, instruction: str = None) -> str:
        """格式化输入指令"""
        if instruction is None:
            instruction = self.default_instruction
        return f"<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {doc}"
    
    def _process_inputs(self, pairs: List[str]) -> Dict[str, torch.Tensor]:
        """处理输入文本"""
        inputs = self.tokenizer(
            pairs, 
            padding=False, 
            truncation='longest_first',
            return_attention_mask=False, 
            max_length=self.max_length - len(self.prefix_tokens) - len(self.suffix_tokens)
        )
        
        for i, ele in enumerate(inputs['input_ids']):
            inputs['input_ids'][i] = self.prefix_tokens + ele + self.suffix_tokens
            
        inputs = self.tokenizer.pad(inputs, padding=True, return_tensors="pt", max_length=self.max_length)
        
        for key in inputs:
            inputs[key] = inputs[key].to(self.model.device)
            
        return inputs
    
    @torch.no_grad()
    def _compute_scores(self, inputs: Dict[str, torch.Tensor]) -> List[float]:
        """计算相关性分数"""
        batch_scores = self.model(**inputs).logits[:, -1, :]
        true_vector = batch_scores[:, self.token_true_id]
        false_vector = batch_scores[:, self.token_false_id]
        batch_scores = torch.stack([false_vector, true_vector], dim=1)
        batch_scores = torch.nn.functional.log_softmax(batch_scores, dim=1)
        return batch_scores[:, 1].exp().tolist()
    
    def rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int = None
    ) -> List[Dict[str, Any]]:
        """
        对文档进行重排序
        
        Args:
            query: 查询文本
            documents: 待重排序的文档列表，每个文档必须包含text字段，可选包含id字段
            top_k: 返回结果数量，如果为None则返回所有结果
            
        Returns:
            List[Dict[str, Any]]: 重排序后的文档列表，每个文档包含id、text和score字段
        """
        # 准备输入对
        pairs = [self._format_instruction(query, doc['text']) for doc in documents]
        
        # 处理输入
        inputs = self._process_inputs(pairs)
        
        # 计算分数
        scores = self._compute_scores(inputs)
        
        # 将分数添加到文档中
        for doc, score in zip(documents, scores):
            doc['score'] = score * doc.get("weight", 1.0)
        
        # 按分数降序排序
        sorted_docs = sorted(documents, key=lambda x: x['score'], reverse=True)
        
        # 如果指定了top_k，则只返回前k个结果
        if top_k is not None:
            sorted_docs = sorted_docs[:top_k]
            
        return sorted_docs 