from typing import Optional, Dict, Any, List, Union
from .model import DocumentModel, ConversationModel
from datetime import datetime
from util.logging.logger import get_logger


class BaseRepository:
    """基础仓储类，提供通用功能"""

    def __init__(self):
        self.logger = get_logger(__name__)

class DocumentRepository(BaseRepository):
    """文档仓储类"""

    def __init__(self):
        super().__init__()

    def get_documents(
        self,
        document_id: Optional[Union[str, List[str]]] = None,
        collection_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        获取文档
        
        Args:
            document_id: 向量数据库中的ID（可选，可以是单个ID或ID列表）
            collection_name: 向量数据库集合名称（可选）
            
        Returns:
            List[Dict[str, Any]]: 文档列表
        """
        try:
            query = {}
            if document_id:
                if isinstance(document_id, list):
                    query['document_id__in'] = document_id
                else:
                    query['document_id'] = document_id
            if collection_name:
                query['collection_name'] = collection_name
                
            docs = DocumentModel.objects(**query)
            documents = [{
                "content": doc.content,
                "metadata": doc.metadata,
                "document_id": doc.document_id,
                "collection_name": doc.collection_name
            } for doc in docs]
            
            # 过滤掉已标记为删除的文档
            filtered_documents = [
                doc for doc in documents 
                if not doc.get("metadata", {}).get("is_deleted", False)
            ]
            return filtered_documents
        except Exception as e:
            self.logger.error(f"获取文档失败: {str(e)}")
            return []

    def get_document_ids_by_metadata(
        self,
        collection_name: str,
        metadata_filters: Dict[str, Any]
    ) -> List[str]:
        """
        根据metadata过滤条件获取文档ID列表。
        metadata_filters支持MongoEngine的操作符后缀（如"批次ID__ne"）。
        """
        try:
            query = {"collection_name": collection_name}
            for key, value in metadata_filters.items():
                query[f"metadata__{key}"] = value
            docs = DocumentModel.objects(**query).only("document_id")
            return [doc.document_id for doc in docs]
        except Exception as e:
            self.logger.error(f"根据metadata获取文档ID失败: {str(e)}")
            return []
    
    def save_document(self, content: str, document_id: str, collection_name: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        保存文档

        Args:
            content: 文档内容
            document_id: 向量数据库中的ID
            collection_name: 向量数据库集合名称
            metadata: 元数据

        Returns:
            bool: 是否保存成功
        """
        try:
            doc = DocumentModel(
                content=content,
                document_id=document_id,
                collection_name=collection_name,
                metadata=metadata or {}
            )
            doc.save()
            return True
        except Exception as e:
            self.logger.error(f"保存文档失败: {str(e)}")
            return False

    def update_document(self, document_id: str, collection_name: str, content: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        更新已存在的文档

        Args:
            document_id: 文档ID
            collection_name: 集合名
            content: 新的文档内容（可选）
            metadata: 新的元数据（可选）

        Returns:
            bool: 是否成功
        """
        try:
            doc = DocumentModel.objects(document_id=document_id, collection_name=collection_name).first()
            if not doc:
                self.logger.warning(f"未找到文档: {document_id} in {collection_name}")
                return False

            if content is not None:
                doc.content = content
            if metadata is not None:
                doc.metadata = metadata

            doc.save()
            self.logger.info(f"更新文档成功: {document_id}")
            return True
        except Exception as e:
            self.logger.error(f"更新文档失败: {str(e)}")
            return False

    def set_file_path(self, document_id: str, collection_name: str, file_path: str) -> bool:
        """
        为指定文档设置唯一文件路径到metadata['file_path']（覆盖式）
        Args:
            document_id: 文档ID
            collection_name: 集合名
            file_path: 文件保存路径
        Returns:
            bool: 是否成功
        """
        try:
            doc = DocumentModel.objects(document_id=document_id, collection_name=collection_name).first()
            if not doc:
                self.logger.warning(f"未找到文档: {document_id} in {collection_name}")
                return False
            doc.metadata['file_path'] = file_path
            doc.save()
            return True
        except Exception as e:
            self.logger.error(f"设置文件路径失败: {str(e)}")
            return False

    def delete_documents_by_collection(self, collection_name: str) -> bool:
        """
        删除指定集合中的所有文档
        Args:
            collection_name: 集合名
        Returns:
            bool: 是否删除成功
        """
        try:
            # 删除指定集合中的所有文档
            deleted_count = DocumentModel.objects(collection_name=collection_name).delete()
            self.logger.info(f"成功删除集合 {collection_name} 中的 {deleted_count} 个文档")
            return True
        except Exception as e:
            self.logger.error(f"删除集合 {collection_name} 中的文档失败: {str(e)}")
            return False
    
    def soft_delete_documents(self, document_ids: List[str], collection_name: str) -> int:
        """
        软删除指定集合中的特定文档（标记为已删除而不是物理删除）
        Args:
            document_ids: 要删除的文档ID列表
            collection_name: 集合名
        Returns:
            int: 成功标记为删除的文档数量
        """
        try:
            deleted_count = 0
            for doc_id in document_ids:
                try:
                    doc = DocumentModel.objects(document_id=doc_id, collection_name=collection_name).first()
                    if doc:
                        # 更新元数据，添加is_deleted标记
                        if not doc.metadata:
                            doc.metadata = {}
                        doc.metadata["is_deleted"] = True
                        doc.save()
                        deleted_count += 1
                    else:
                        self.logger.warning(f"在MongoDB中未找到文档 {doc_id}")
                except Exception as e:
                    self.logger.error(f"在MongoDB中标记文档 {doc_id} 为已删除失败: {str(e)}")
            self.logger.info(f"成功在集合 {collection_name} 中软删除 {deleted_count} 个文档")
            return deleted_count
        except Exception as e:
            self.logger.error(f"在集合 {collection_name} 中软删除文档失败: {str(e)}")
            return 0


class ConversationRepository(BaseRepository):
    """对话仓储类"""

    def __init__(self):
        super().__init__()

    def get_conversations(
        self,
        chat_id: Optional[str] = None,
        message_id: Optional[Union[str, List[str]]] = None,
        role: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        include_deleted: bool = False,
        limit: Optional[int] = None,
        skip: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        获取对话记录

        Args:
            chat_id: 会话ID（可选）
            message_id: 消息ID（可选，可以是单个ID或ID列表）
            role: 对话角色（可选，'user'或'assistant'）
            start_time: 开始时间（可选）
            end_time: 结束时间（可选）
            include_deleted: 是否包含已删除的对话（可选）
            limit: 返回记录数量限制（可选）
            skip: 跳过记录数量（可选）

        Returns:
            List[Dict[str, Any]]: 对话记录列表
        """
        try:
            query = {}
            if chat_id:
                query['chat_id'] = chat_id
            if message_id:
                if isinstance(message_id, list):
                    query['message_id__in'] = message_id
                else:
                    query['message_id'] = message_id
            if role:
                query['role'] = role
            if start_time:
                query['created_at__gte'] = start_time
            if end_time:
                query['created_at__lte'] = end_time
            if not include_deleted:
                query['is_deleted'] = False

            conversations = ConversationModel.objects(**query).order_by('updated_at')

            if skip:
                conversations = conversations.skip(skip)
            if limit:
                conversations = conversations.limit(limit)

            return [{
                "message": conv.message,
                "role": conv.role,
                "message_id": conv.message_id,
                "chat_id": conv.chat_id,
                "created_at": conv.created_at,
                "updated_at": conv.updated_at,
                "is_deleted": conv.is_deleted,
                "meta_data": conv.meta_data
            } for conv in conversations]
        except Exception as e:
            self.logger.error(f"获取对话记录失败: {str(e)}")
            return []
    
    def get_chat_messages(
        self,
        chat_id: str,
        include_deleted: bool = False,
        limit: Optional[int] = None,
        skip: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        获取指定会话的所有消息

        Args:
            chat_id: 会话ID
            include_deleted: 是否包含已删除的对话（可选）
            limit: 返回记录数量限制（可选）
            skip: 跳过记录数量（可选）

        Returns:
            List[Dict[str, Any]]: 消息列表
        """
        return self.get_conversations(
            chat_id=chat_id,
            include_deleted=include_deleted,
            limit=limit,
            skip=skip
        )
    
    
    def save_conversation(
        self,
        message: str,
        role: str,
        message_id: str,
        chat_id: str,
        meta_data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        保存对话记录

        Args:
            message: 对话内容
            role: 对话角色（'user'或'assistant'）
            message_id: 消息ID
            chat_id: 会话ID
            meta_data: 元数据（可选）

        Returns:
            bool: 是否保存成功
        """
        try:
            conv = ConversationModel(
                message=message,
                role=role,
                message_id=message_id,
                chat_id=chat_id,
                updated_at=datetime.now(),
                meta_data=meta_data or {}
            )
            conv.save()
            return True
        except Exception as e:
            self.logger.error(f"保存对话记录失败: {str(e)}")
            return False
    
    def delete_conversation(self, message_id: str, soft_delete: bool = True) -> bool:
        """
        删除对话记录
        
        Args:
            message_id: 消息ID
            soft_delete: 是否软删除（标记is_deleted为True）
            
        Returns:
            bool: 是否删除成功
        """
        try:
            if soft_delete:
                conv = ConversationModel.objects(message_id=message_id).first()
                if conv:
                    conv.is_deleted = True
                    conv.updated_at = datetime.now()
                    conv.save()
                    return True
                return False
            else:
                conv = ConversationModel.objects(message_id=message_id).first()
                if conv:
                    conv.delete()
                    return True
                return False
        except Exception as e:
            self.logger.error(f"删除对话记录失败: {str(e)}")
            return False


# ════════════════════════════════════════════════════════════════
# TaskStateRepository（故障任务状态仓库）
# ────────────────────────────────────────────────────────────────
# 所有方法都是同步的，调用方在 async context 里用 asyncio.to_thread() 包裹
# ════════════════════════════════════════════════════════════════

from database.document.task_state_doc import FaultTaskState


class TaskStateRepository(BaseRepository):
    """故障任务状态仓库。"""

    def upsert(self, chat_id: str, task_data: dict, status: str = 'ongoing') -> bool:
        """
        upsert 一条 TaskState。chat_id 已存在则更新，不存在则创建。

        Args:
            chat_id: 会话 ID
            task_data: TaskState.model_dump(mode='json') 的完整结果
            status: ongoing / resolved / escalated / abandoned

        Returns:
            True 成功 / False 失败
        """
        if not chat_id:
            self.logger.warning("[TaskStateRepository] upsert called with empty chat_id")
            return False
        try:
            now = datetime.now()
            FaultTaskState.objects(chat_id=chat_id).update_one(
                set__task_data=task_data or {},
                set__status=status,
                set__updated_at=now,
                set_on_insert__chat_id=chat_id,
                set_on_insert__created_at=now,
                upsert=True,
            )
            return True
        except Exception as e:
            self.logger.exception(f"[TaskStateRepository] upsert failed for chat_id={chat_id}: {e}")
            return False

    def get_by_chat_id(self, chat_id: str) -> Optional[Dict[str, Any]]:
        """
        按 chat_id 查询。

        Returns:
            含 task_data / status / created_at / updated_at 的 dict，
            None 表示不存在或查询失败
        """
        if not chat_id:
            return None
        try:
            doc = FaultTaskState.objects(chat_id=chat_id).first()
            if doc is None:
                return None
            return {
                "chat_id": doc.chat_id,
                "task_data": dict(doc.task_data) if doc.task_data else {},
                "status": doc.status,
                "created_at": doc.created_at,
                "updated_at": doc.updated_at,
            }
        except Exception as e:
            self.logger.exception(f"[TaskStateRepository] get_by_chat_id failed for chat_id={chat_id}: {e}")
            return None

    def delete(self, chat_id: str) -> bool:
        """
        硬删除（直接从 collection 删除）。

        Returns:
            True 成功 / False 失败
        """
        if not chat_id:
            return False
        try:
            FaultTaskState.objects(chat_id=chat_id).delete()
            return True
        except Exception as e:
            self.logger.exception(f"[TaskStateRepository] delete failed for chat_id={chat_id}: {e}")
            return False

    def exists(self, chat_id: str) -> bool:
        """快速检查是否存在（不返回数据）。"""
        if not chat_id:
            return False
        try:
            return FaultTaskState.objects(chat_id=chat_id).count() > 0
        except Exception as e:
            self.logger.warning(f"[TaskStateRepository] exists check failed for chat_id={chat_id}: {e}")
            return False
