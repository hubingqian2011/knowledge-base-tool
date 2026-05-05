from mongoengine import Document, StringField, DictField, connect, EnumField, DateTimeField, BooleanField
from config.config import MONGO_URI
from datetime import datetime
from enum import Enum

def initialize_database():
    """初始化MongoDB数据库连接"""
    connect(host=MONGO_URI, alias='document')

class RoleEnum(Enum):
    USER = 'user'
    ASSISTANT = 'assistant'
    CREATE = 'create'

class DocumentModel(Document):
    """文档数据对象"""
    
    meta = {
        'db_alias': 'document',
        'collection': 'documents',
        'indexes': [
            'document_id',
            'collection_name'
        ]
    }
    
    content = StringField(required=True)
    metadata = DictField(default={})
    document_id = StringField(required=True, max_length=64)  # 向量数据库中的ID
    collection_name = StringField(required=True, max_length=64)  # 向量数据库集合名称

class ConversationModel(Document):
    """对话数据对象 - 消息层存储"""

    meta = {
        'db_alias': 'document',
        'collection': 'conversations',
        'indexes': [
            'message_id',
            'chat_id',
            'role',
            'created_at',
            'updated_at',
            'is_deleted'
        ]
    }

    message = StringField(required=True)  # 对话内容
    role = EnumField(required=True, enum=RoleEnum)  # 对话角色
    message_id = StringField(required=True, max_length=64)  # 消息ID
    chat_id = StringField(required=True, max_length=64)  # 会话ID（外键关联MySQL chats表）
    created_at = DateTimeField(default=datetime.now)  # 创建时间
    updated_at = DateTimeField(default=datetime.now)  # 更新时间
    is_deleted = BooleanField(default=False)  # 是否删除
    meta_data = DictField(default={})  # 额外元数据

# 初始化数据库连接
initialize_database() 