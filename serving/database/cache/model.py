from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional, Dict, Any
from redis.commands.search.field import TextField, TagField, NumericField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from .cache_store import CacheStore
import json

@dataclass
class ConversationModel:
    """对话数据模型"""
    message: str
    role: Literal["user", "assistant"]
    chat_type: str
    message_id: str
    chat_id: str
    user_id: str
    chat_name: str
    created_at: datetime = datetime.now()
    updated_at: datetime = field(default_factory=datetime.now)
    is_deleted: bool = False
    meta_data: Dict[str, Any] = field(default_factory=dict)
    
    # 索引相关常量
    INDEX_NAME = "idx:conversations"
    KEY_PREFIX = "conversation:"
    
    @classmethod
    def create_index(cls) -> None:
        """创建Redisearch索引"""
        try:
            # 定义索引字段
            schema = (
                TextField("message"),  # 消息内容
                TagField("role"),      # 角色（user/assistant）
                TagField("chat_type"),  # 会话类型
                TagField("message_id"), # 消息ID
                TagField("chat_id"), # 会话ID
                TagField("user_id"),    # 用户ID
                TextField("chat_name"), # 会话名称
                NumericField("created_at"), # 创建时间
                NumericField("updated_at"), # 更新时间
                TagField("is_deleted"), # 是否删除
                TextField("meta_data")  # 元数据（JSON字符串）
            )
            
            # 创建索引
            cache = CacheStore()
            cache.client.ft(cls.INDEX_NAME).create_index(
                schema,
                definition=IndexDefinition(
                    prefix=[cls.KEY_PREFIX],
                    index_type=IndexType.HASH
                )
            )
        except Exception as e:
            # 如果索引已存在，忽略错误
            if "Index already exists" not in str(e):
                raise
    
    @classmethod
    def get_key(cls, message_id: str) -> str:
        """获取Redis键"""
        return f"{cls.KEY_PREFIX}{message_id}"
    
    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "message": self.message,
            "role": self.role,
            "chat_type": self.chat_type,
            "message_id": self.message_id,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "chat_name": self.chat_name,
            "created_at": str(int(self.created_at.timestamp())),
            "updated_at": str(int(self.updated_at.timestamp())),
            "is_deleted": str(int(self.is_deleted)),
            "meta_data": json.dumps(self.meta_data)
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ConversationModel":
        """从字典创建实例"""
        # 处理meta_data字段
        meta_data = {}
        if "meta_data" in data:
            try:
                meta_data = json.loads(data["meta_data"])
            except (json.JSONDecodeError, TypeError):
                meta_data = {}

        return cls(
            message=data["message"],
            role=data["role"],
            chat_type=data["chat_type"],
            message_id=data["message_id"],
            chat_id=data["chat_id"],
            user_id=data["user_id"],
            chat_name=data["chat_name"],
            created_at=datetime.fromtimestamp(int(data["created_at"])),
            updated_at=datetime.fromtimestamp(int(data["updated_at"])),
            is_deleted=bool(int(data["is_deleted"])),
            meta_data=meta_data
        )