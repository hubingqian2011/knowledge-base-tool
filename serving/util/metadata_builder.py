"""
工具函数：构建消息元数据
用于标准化知识库引用信息的存储格式
"""

from typing import List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field
from config.config import COLLECTION_MANUAL

class Source(BaseModel):
    """知识源模型"""
    collection_name: str = Field(..., description="知识库集合名称")
    document_id: str = Field(..., description="文档ID")
    source_name: str = Field(..., description="来源名称")
    title: str = Field(..., description="文档标题")
    content: str = Field(..., description="文档内容")

    class Config:
        json_schema_extra = {
            "example": {
                "collection_name": COLLECTION_MANUAL,
                "document_id": "a26680f0-16d1-4d48-8714-fe4ec585c79b",
                "source_name": "操作手册",
                "title": "安全注意事项",
                "content": "介绍注塑机操作过程中的各项安全注意事项，旨在保障操作人员安全、避免设备损坏及数据丢失等问题。"
            }
        }

def build_knowledge_metadata(
    sources: Optional[List[Union[Source, Dict[str, Any]]]] = None,
    followup_questions: Optional[List[str]] = None,
    additional_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    构建包含知识库引用信息的metadata

    Args:
        sources: 知识库来源列表，可以是Source对象或字典
        followup_questions: 推荐的后续问题
        additional_data: 额外的元数据

    Returns:
        Dict[str, Any]: 标准化的metadata字典
    """
    if sources is None or sources == []:
        sources = []
    if followup_questions is None or followup_questions == []:
        followup_questions = []
    if not isinstance(sources, list):
        sources = []
    if not isinstance(followup_questions, list):
        followup_questions = []

    metadata = {
        "sources": [],
        "followup_questions": [],
    }

    # 处理知识库来源
    if sources:
        processed_sources = []
        for source in sources:
            if isinstance(source, Source):
                # 处理Source对象
                processed_sources.append({
                    "collection_name": source.collection_name,
                    "document_id": source.document_id,
                    "source_name": source.source_name,
                    "title": source.title,
                    "content": source.content[:200] + "..." if len(source.content) > 200 else source.content
                })
            elif isinstance(source, dict):
                # 处理字典对象（保留 workorder_number 供多轮 rejected_workorder_ids 使用）
                content = source.get("content", "")
                source_metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
                item = {
                    "collection_name": source.get("collection_name", ""),
                    "document_id": source.get("document_id") or source.get("id") or "",
                    "source_name": source.get("source_name") or source_metadata.get("source_file") or "",
                    "title": source.get("title") or source_metadata.get("title") or "",
                    "content": content[:200] + "..." if len(content) > 200 else content
                }
                if source.get("workorder_number") is not None and str(source.get("workorder_number", "")).strip():
                    item["workorder_number"] = str(source.get("workorder_number", "")).strip()
                image_ids = source.get("image_ids") or source_metadata.get("image_ids")
                steps = source.get("steps") or source_metadata.get("steps")
                notes = source.get("notes") or source_metadata.get("notes")
                if isinstance(image_ids, list) and image_ids:
                    item["image_ids"] = [str(image_id) for image_id in image_ids if str(image_id).strip()]
                if isinstance(steps, list) and steps:
                    item["steps"] = steps
                    item["step_count"] = int(source.get("step_count") or source_metadata.get("step_count") or len(steps))
                if isinstance(notes, list) and notes:
                    item["notes"] = notes
                if source.get("url") is not None and str(source.get("url", "")).strip():
                    item["url"] = str(source.get("url", "")).strip()
                # 保留工单 metadata 供多轮工具决策展示
                for key in ("product_model", "component_category", "component_subcategory", "fault_type", "handling_method"):
                    val = source.get(key)
                    if val is not None and str(val).strip():
                        item[key] = str(val).strip()            
                processed_sources.append(item)

        metadata["sources"] = processed_sources

    # 处理后续问题
    if followup_questions:
        metadata["followup_questions"] = followup_questions
    
    # 添加额外数据
    if additional_data:
        metadata.update(additional_data)
    
    return metadata
