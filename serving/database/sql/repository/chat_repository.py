from typing import Optional, List, Dict, Any
from sqlalchemy import and_, desc
from datetime import datetime
import json

from ..model.chat import ChatModel
from .base_repository import BaseRepository
from util.logging.logger import get_logger

logger = get_logger(__name__)


class ChatRepository(BaseRepository):
    """会话数据仓库 - MySQL存储，继承BaseRepository"""

    def __init__(self, session=None):
        """初始化聊天仓库

        Args:
            session: 数据库会话
        """
        super().__init__(ChatModel, session)
    
    def create_chat(
        self,
        chat_id: str,
        chat_name: str,
        chat_type: str,
        user_id: str,
        meta_data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        创建会话

        Args:
            chat_id: 会话ID
            chat_name: 会话名称
            chat_type: 会话类型
            user_id: 用户ID
            meta_data: 扩展元数据

        Returns:
            bool: 是否创建成功
        """
        try:
            # 检查会话是否已存在
            existing_chat = self.session.query(ChatModel).filter(
                ChatModel.chat_id == chat_id
            ).first()

            if existing_chat:
                logger.warning(f"会话已存在: chat_id={chat_id}")
                return False

            # 创建新会话
            chat = ChatModel(
                chat_id=chat_id,
                chat_name=chat_name,
                chat_type=chat_type,
                user_id=user_id,
                meta_data=json.dumps(meta_data) if meta_data else None
            )

            self.save(chat)

            logger.info(f"会话创建成功: chat_id={chat_id}")
            return True

        except Exception as e:
            logger.error(f"创建会话失败: chat_id={chat_id}, error={str(e)}")
            return False
    
    def update_chat(
        self,
        chat_id: str,
        chat_name: Optional[str] = None,
        chat_type: Optional[str] = None,
        user_id: Optional[str] = None,
        meta_data: Optional[Dict[str, Any]] = None,
        use_soft_delete: bool = True
    ) -> bool:
        """
        更新会话信息（支持软删除方式保持数据留痕）

        Args:
            chat_id: 会话ID
            chat_name: 新的会话名称
            chat_type: 新的会话类型
            user_id: 新的用户ID
            metadata: 新的扩展元数据
            use_soft_delete: 是否使用软删除方式（默认True）

        Returns:
            bool: 是否更新成功
        """
        if use_soft_delete:
            return self._update_chat_with_soft_delete(
                chat_id, chat_name, chat_type, user_id, meta_data
            )
        else:
            return self._update_chat_direct(
                chat_id, chat_name, chat_type, user_id, meta_data
            )

    def _update_chat_direct(
        self,
        chat_id: str,
        chat_name: Optional[str] = None,
        chat_type: Optional[str] = None,
        user_id: Optional[str] = None,
        meta_data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """直接更新方式（不推荐，不保留历史）"""
        try:
            chat = self.session.query(ChatModel).filter(
                and_(
                    ChatModel.chat_id == chat_id,
                    ChatModel.is_deleted == False
                )
            ).first()

            if not chat:
                logger.warning(f"会话不存在: chat_id={chat_id}")
                return False

            # 更新字段
            update_fields = {}
            if chat_name is not None:
                update_fields['chat_name'] = chat_name
            if chat_type is not None:
                update_fields['chat_type'] = chat_type
            if user_id is not None:
                update_fields['user_id'] = user_id
            if meta_data is not None:
                update_fields['meta_data'] = json.dumps(meta_data)

            # 使用基础类的更新方法
            self.update_fields(chat.id, update_fields)

            logger.info(f"会话直接更新成功: chat_id={chat_id}")
            return True

        except Exception as e:
            logger.error(f"直接更新会话失败: chat_id={chat_id}, error={str(e)}")
            return False

    def _update_chat_with_soft_delete(
        self,
        chat_id: str,
        chat_name: Optional[str] = None,
        chat_type: Optional[str] = None,
        user_id: Optional[str] = None,
        meta_data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """软删除更新方式（推荐，保留历史记录）"""
        try:
            # 获取当前会话
            current_chat = self.session.query(ChatModel).filter(
                and_(
                    ChatModel.chat_id == chat_id,
                    ChatModel.is_deleted == False
                )
            ).first()

            if not current_chat:
                logger.warning(f"会话不存在: chat_id={chat_id}")
                return False

            # 保存原始数据用于历史记录
            original_data = {
                'chat_name': current_chat.chat_name,
                'chat_type': current_chat.chat_type,
                'user_id': current_chat.user_id,
                'meta_data': current_chat.meta_data,
                'updated_at': current_chat.updated_at.isoformat() if current_chat.updated_at else None
            }

            # 处理元数据
            current_metadata = {}
            if current_chat.meta_data:
                try:
                    current_metadata = json.loads(current_chat.meta_data)
                except json.JSONDecodeError:
                    current_metadata = {}

            # 添加更新历史信息
            if 'update_history' not in current_metadata:
                current_metadata['update_history'] = []

            update_record = {
                "timestamp": datetime.now().isoformat(),
                "reason": "soft_delete_update",
                "original_data": original_data
            }
            current_metadata['update_history'].append(update_record)

            # 合并新的元数据
            if meta_data:
                current_metadata.update(meta_data)

            # 直接更新当前记录（不创建新记录）
            update_fields = {}
            if chat_name is not None:
                update_fields['chat_name'] = chat_name
            if chat_type is not None:
                update_fields['chat_type'] = chat_type
            if user_id is not None:
                update_fields['user_id'] = user_id
            update_fields['meta_data'] = json.dumps(current_metadata)
            update_fields['updated_at'] = datetime.now()

            # 使用基础类的更新方法
            self.update_fields(current_chat.id, update_fields)

            logger.info(f"会话软删除更新成功: chat_id={chat_id}")
            return True

        except Exception as e:
            logger.error(f"软删除更新会话失败: chat_id={chat_id}, error={str(e)}")
            return False
    
    def get_chat(self, chat_id: str, include_deleted: bool = False) -> Optional[Dict[str, Any]]:
        """
        获取单个会话信息（获取最新的未删除记录）

        Args:
            chat_id: 会话ID
            include_deleted: 是否包含已删除的记录

        Returns:
            Optional[Dict[str, Any]]: 会话信息，不存在则返回None
        """
        try:
            query = self.session.query(ChatModel).filter(ChatModel.chat_id == chat_id)

            if not include_deleted:
                query = query.filter(ChatModel.is_deleted == False)

            # 按更新时间降序排列，获取最新记录
            chat = query.order_by(desc(ChatModel.updated_at)).first()

            if not chat:
                return None

            result = chat.to_dict()
            # 解析meta_data
            if result['meta_data']:
                try:
                    result['meta_data'] = json.loads(result['meta_data'])
                except json.JSONDecodeError:
                    result['meta_data'] = {}
            else:
                result['meta_data'] = {}

            return result

        except Exception as e:
            logger.error(f"获取会话失败: chat_id={chat_id}, error={str(e)}")
            return None
    
    def list_chats(
        self,
        user_id: Optional[str] = None,
        chat_type: Optional[str] = None,
        include_deleted: bool = False,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        列出会话（每个chat_id只返回最新的记录）

        Args:
            user_id: 用户ID过滤，None表示所有用户
            chat_type: 会话类型过滤
            include_deleted: 是否包含已删除的会话
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            List[Dict[str, Any]]: 会话列表，按更新时间降序排列
        """
        session = self.session
        try:
            # 使用子查询获取每个chat_id的最新记录
            from sqlalchemy import func as sql_func

            # 子查询：获取每个chat_id的最新updated_at时间
            subquery = session.query(
                ChatModel.chat_id,
                sql_func.max(ChatModel.updated_at).label('max_updated_at')
            ).group_by(ChatModel.chat_id).subquery()

            # 主查询：关联子查询获取完整记录
            query = session.query(ChatModel).join(
                subquery,
                and_(
                    ChatModel.chat_id == subquery.c.chat_id,
                    ChatModel.updated_at == subquery.c.max_updated_at
                )
            )

            # 构建过滤条件
            conditions = []
            if user_id and user_id != "-1":
                conditions.append(ChatModel.user_id == user_id)
            if chat_type:
                conditions.append(ChatModel.chat_type == chat_type)
            if not include_deleted:
                conditions.append(ChatModel.is_deleted == False)

            if conditions:
                query = query.filter(and_(*conditions))

            # 排序
            query = query.order_by(desc(ChatModel.updated_at))

            # 分页
            if offset:
                query = query.offset(offset)
            if limit:
                query = query.limit(limit)

            chats = query.all()

            # 转换为字典格式
            results = []
            for chat in chats:
                result = chat.to_dict()
                # 解析meta_data
                if result['meta_data']:
                    try:
                        result['meta_data'] = json.loads(result['meta_data'])
                    except json.JSONDecodeError:
                        result['meta_data'] = {}
                else:
                    result['meta_data'] = {}
                results.append(result)

            logger.debug(f"获取会话列表成功: user_id={user_id}, 数量={len(results)}")
            return results

        except Exception as e:
            logger.error(f"列出会话失败: user_id={user_id}, error={str(e)}")
            return []
        finally:
            session.close()
    
    def delete_chat(self, chat_id: str, soft_delete: bool = True) -> bool:
        """
        删除会话
        
        Args:
            chat_id: 会话ID
            soft_delete: 是否软删除
            
        Returns:
            bool: 是否删除成功
        """
        session = self.session
        try:
            if soft_delete:
                # 软删除：只删除未删除的记录
                chat = session.query(ChatModel).filter(
                    and_(
                        ChatModel.chat_id == chat_id,
                        ChatModel.is_deleted == False
                    )
                ).first()

                if not chat:
                    logger.warning(f"会话不存在: chat_id={chat_id}")
                    return True  # 不存在也算删除成功

                chat.is_deleted = True
                # updated_at会自动更新
                logger.info(f"会话软删除成功: chat_id={chat_id}")
            else:
                # 物理删除：删除所有相关记录
                deleted_count = session.query(ChatModel).filter(
                    ChatModel.chat_id == chat_id
                ).delete()

                if deleted_count == 0:
                    logger.warning(f"会话不存在: chat_id={chat_id}")
                    return True  # 不存在也算删除成功

                logger.info(f"会话物理删除成功: chat_id={chat_id}, 删除记录数={deleted_count}")

            session.commit()
            return True
            
        except Exception as e:
            session.rollback()
            logger.error(f"删除会话失败: chat_id={chat_id}, error={str(e)}")
            return False
        finally:
            session.close()
    
    def chat_exists(self, chat_id: str) -> bool:
        """
        检查会话是否存在（检查最新的未删除记录）

        Args:
            chat_id: 会话ID

        Returns:
            bool: 是否存在
        """
        session = self.session
        try:
            # 获取最新的记录
            latest_chat = session.query(ChatModel).filter(
                ChatModel.chat_id == chat_id
            ).order_by(desc(ChatModel.updated_at)).first()

            # 检查最新记录是否存在且未删除
            exists = latest_chat is not None and not latest_chat.is_deleted

            return exists

        except Exception as e:
            logger.error(f"检查会话存在性失败: chat_id={chat_id}, error={str(e)}")
            return False
        finally:
            session.close()

    def update_updated_at(self, chat_id: str) -> bool:
        """
        仅更新会话的 updated_at 为当前时间（用于消息保存后刷新排序时间戳）

        Args:
            chat_id: 会话ID

        Returns:
            bool: 是否更新成功
        """
        session = self.session
        try:
            updated_count = session.query(ChatModel).filter(
                ChatModel.chat_id == chat_id,
                ChatModel.is_deleted == False
            ).update({"updated_at": datetime.now()}, synchronize_session=False)
            session.commit()
            if updated_count == 0:
                logger.warning(f"update_updated_at: 未找到会话或已删除: chat_id={chat_id}")
                return False
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"update_updated_at 失败: chat_id={chat_id}, error={str(e)}")
            return False
        finally:
            session.close()

    def get_chat_history(self, chat_id: str) -> List[Dict[str, Any]]:
        """
        获取会话的完整历史记录（包括所有版本）

        Args:
            chat_id: 会话ID

        Returns:
            List[Dict[str, Any]]: 会话历史记录，按时间降序排列
        """
        session = self.session
        try:
            chats = session.query(ChatModel).filter(
                ChatModel.chat_id == chat_id
            ).order_by(desc(ChatModel.updated_at)).all()

            results = []
            for chat in chats:
                result = chat.to_dict()
                # 解析meta_data
                if result['meta_data']:
                    try:
                        result['meta_data'] = json.loads(result['meta_data'])
                    except json.JSONDecodeError:
                        result['meta_data'] = {}
                else:
                    result['meta_data'] = {}
                results.append(result)

            logger.debug(f"获取会话历史成功: chat_id={chat_id}, 版本数={len(results)}")
            return results

        except Exception as e:
            logger.error(f"获取会话历史失败: chat_id={chat_id}, error={str(e)}")
            return []
        finally:
            session.close()
