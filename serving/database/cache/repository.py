from typing import List, Optional
from datetime import datetime
from redis.commands.search.query import Query

from .model import ConversationModel
from .cache_store import CacheStore

from util.logging.logger import get_logger

class ConversationRepository:
    """对话数据仓库"""
    
    def __init__(self):
        self.cache = CacheStore()
        ConversationModel.create_index()
        self.logger = get_logger(__name__)
    
    def save(self, conversation: ConversationModel, expire: Optional[int] = None) -> bool:
        """
        保存对话数据
        
        Args:
            conversation: 对话模型实例
            expire: 过期时间（秒），None表示使用默认过期时间
            
        Returns:
            bool: 是否保存成功
        """
        try:
            # 更新updated_at字段
            conversation.updated_at = datetime.now()
            
            key = ConversationModel.get_key(conversation.message_id)
            data = conversation.to_dict()
            result = bool(self.cache.client.hset(key, mapping=data))
            if result and expire is not None:
                self.cache.client.expire(key, expire)
            return result
        except Exception as e:
            self.logger.error(f"保存对话数据失败: {str(e)}")
            return False
    
    def get_by_message_id(self, message_id: str) -> Optional[ConversationModel]:
        """
        通过消息ID获取对话数据
        
        Args:
            message_id: 消息ID
            
        Returns:
            Optional[ConversationModel]: 对话模型实例
        """
        try:
            key = ConversationModel.get_key(message_id)
            data = self.cache.client.hgetall(key)
            if not data:
                return None
            return ConversationModel.from_dict(data)
        except Exception as e:
            self.logger.error(f"获取对话数据失败: {str(e)}")
            return None
    
    def get_by_session_id(self, session_id: str, include_deleted: bool = False) -> List[ConversationModel]:
        """
        获取会话中的所有对话
        
        Args:
            session_id: 会话ID
            include_deleted: 是否包含已删除的对话
            
        Returns:
            List[ConversationModel]: 对话模型实例列表
        """
        try:
            query_str = f"@chat_id:{{{session_id}}}"
            if not include_deleted:
                query_str += " @is_deleted:{0}"
            query = Query(query_str).sort_by("updated_at", asc=False)
            
            result = self.cache.client.ft(ConversationModel.INDEX_NAME).search(query)
            
            conversations = []
            for doc in result.docs:
                data = {k: v for k, v in doc.__dict__.items() if not k.startswith('_')}
                conversations.append(ConversationModel.from_dict(data))
            return conversations
        except Exception as e:
            self.logger.error(f"获取会话对话失败: {str(e)}")
            return []
    
    def search(self, 
              role: Optional[str] = None,
              session_type: Optional[str] = None,
              user_id: Optional[str] = None,
              session_name: Optional[str] = None,
              start_time: Optional[datetime] = None,
              end_time: Optional[datetime] = None,
              include_deleted: bool = False) -> List[ConversationModel]:
        """
        搜索对话数据
        
        Args:
            role: 角色过滤
            session_type: 会话类型过滤
            user_id: 用户ID过滤
            session_name: 会话名称过滤
            start_time: 开始时间
            end_time: 结束时间
            include_deleted: 是否包含已删除的对话
            
        Returns:
            List[ConversationModel]: 对话模型实例列表
        """
        try:
            # 构建查询条件
            conditions = []
            if role:
                conditions.append(f"@role:{{{role}}}")
            if session_type:
                conditions.append(f"@chat_type:{{{session_type}}}")
            if user_id:
                conditions.append(f"@user_id:{{{user_id}}}")
            if session_name:
                conditions.append(f"@chat_name:{session_name}")
            if start_time:
                start_timestamp = int(start_time.timestamp())
                conditions.append(f"@created_at:[{start_timestamp} +inf]")
            if end_time:
                end_timestamp = int(end_time.timestamp())
                conditions.append(f"@created_at:[-inf {end_timestamp}]")
            if not include_deleted:
                conditions.append("@is_deleted:{0}")
            
            query_str = " ".join(conditions) if conditions else "*"
            query = Query(query_str).sort_by("created_at", asc=False)
            
            result = self.cache.client.ft(ConversationModel.INDEX_NAME).search(query)
            
            conversations = []
            for doc in result.docs:
                data = {k: v for k, v in doc.__dict__.items() if not k.startswith('_')}
                conversations.append(ConversationModel.from_dict(data))
            return conversations
        except Exception as e:
            self.logger.error(f"搜索对话数据失败: {str(e)}")
            return []
    
    def delete(self, message_id: str, soft_delete: bool = True) -> bool:
        """
        删除对话数据
        
        Args:
            message_id: 消息ID
            soft_delete: 是否软删除（标记is_deleted为True）
            
        Returns:
            bool: 是否删除成功
        """
        try:
            if soft_delete:
                conversation = self.get_by_message_id(message_id)
                if conversation:
                    conversation.is_deleted = True
                    return self.save(conversation)
                return False
            else:
                key = ConversationModel.get_key(message_id)
                return bool(self.cache.client.delete(key))
        except Exception as e:
            self.logger.error(f"删除对话数据失败: {str(e)}")
            return False 