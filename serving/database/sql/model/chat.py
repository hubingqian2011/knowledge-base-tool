from sqlalchemy import Column, String, DateTime, Boolean, Text, Index
from sqlalchemy.sql import func
from .database_manager import Base
from datetime import datetime, timezone, timedelta


def china_now():
    """获取中国时区的当前时间"""
    china_tz = timezone(timedelta(hours=8))  # 中国时区 UTC+8
    return datetime.now(china_tz)


class ChatModel(Base):
    """会话数据模型 - MySQL存储"""
    
    __tablename__ = "chats"
    
    # 主键字段
    chat_id = Column(String(64), primary_key=True, index=True, comment="会话ID")
    
    # 基本信息字段
    chat_name = Column(String(255), nullable=False, comment="会话名称")
    chat_type = Column(String(50), nullable=False, index=True, comment="会话类型")
    user_id = Column(String(64), nullable=False, index=True, comment="用户ID")
    
    # 时间字段 - 使用中国时区
    created_at = Column(DateTime, nullable=False, default=china_now, comment="创建时间")
    updated_at = Column(DateTime, nullable=False, default=china_now, onupdate=china_now, comment="更新时间")
    
    # 状态字段
    is_deleted = Column(Boolean, nullable=False, default=False, index=True, comment="是否删除")
    
    # 扩展字段
    meta_data = Column(Text, nullable=True, comment="扩展元数据(JSON格式)")
    
    # 复合索引
    __table_args__ = (
        Index('idx_user_updated', 'user_id', 'updated_at'),
        Index('idx_type_updated', 'chat_type', 'updated_at'),
        Index('idx_user_type', 'user_id', 'chat_type'),
        Index('idx_deleted_updated', 'is_deleted', 'updated_at'),
        {'comment': '会话信息表'}
    )
    
    def to_dict(self):
        """转换为字典格式"""
        return {
            'chat_id': self.chat_id,
            'chat_name': self.chat_name,
            'chat_type': self.chat_type,
            'user_id': self.user_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'is_deleted': self.is_deleted,
            'meta_data': self.meta_data
        }
    
    def __repr__(self):
        return f"<ChatModel(chat_id='{self.chat_id}', chat_name='{self.chat_name}', user_id='{self.user_id}')>"
