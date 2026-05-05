from typing import Optional, Dict, Type, TypeVar, Generic
from sqlalchemy.orm import Session
from config.config import COLLECTION_PARAMETER
from .parameter_repository import ParameterRepository
from .document_image_repository import DocumentImageRepository
from .image_file_repository import ImageFileRepository
from .manual_image_repository import ManualImageRepository
from .auth_repository import UserRepository, RefreshTokenRepository, UserSessionRepository
from .chat_repository import ChatRepository
from ..model.database_manager import DatabaseManager
from util.logging.logger import get_logger

logger = get_logger(__name__)

# 仓库类型变量
R = TypeVar('R')

class RepositoryFactory(Generic[R]):
    """仓库工厂（单例模式）
    负责创建、缓存和管理仓库实例
    """
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, database_manager: DatabaseManager = None, session: Session = None):
        """初始化仓库工厂

        Args:
            database_manager: 数据库管理器，如果为None则会创建一个新的实例
            session: 数据库会话，如果为None则会从db_manager获取
        """
        # 避免重复初始化
        if RepositoryFactory._initialized:
            return

        if database_manager is None:
            database_manager = DatabaseManager()

        self.db_manager = database_manager
        self._repositories: Dict[str, object] = {}
        self._session: Optional[Session] = session
        RepositoryFactory._initialized = True

    def _get_session(self) -> Session:
        """获取或创建数据库会话

        Returns:
            SQLAlchemy会话
        """
        # 每次都创建新的session，避免并发操作问题
        return self.db_manager.get_session()

    def get_repository(self, repository_class: Type[R]) -> R:
        """获取指定类型的仓库实例
        每次都创建新的实例，避免session共享问题

        Args:
            repository_class: 仓库类类型

        Returns:
            仓库实例
        """
        repo_name = repository_class.__name__
        # logger.debug(f"创建仓库实例: {repo_name}")  # 关闭：每次请求10+次，噪音

        # 每次都创建新的仓库实例和session，避免并发操作问题
        session = self._get_session()
        repository = repository_class(session)

        return repository

    @property
    def parameter_repository(self) -> ParameterRepository:
        """获取参数Repository实例

        Returns:
            ParameterRepository: 参数Repository实例
        """
        return self.get_repository(ParameterRepository)

    @property
    def image_file_repository(self) -> ImageFileRepository:
        """获取图片文件Repository实例

        Returns:
            ImageFileRepository: 图片文件Repository实例
        """
        return self.get_repository(ImageFileRepository)

    @property
    def document_image_repository(self) -> DocumentImageRepository:
        """获取通用文档图片映射 Repository 实例"""
        return self.get_repository(DocumentImageRepository)

    @property
    def manual_image_repository(self) -> ManualImageRepository:
        """获取手动图片Repository实例

        Returns:
            ManualImageRepository: 手动图片Repository实例
        """
        return self.get_repository(ManualImageRepository)

    @property
    def user_repository(self) -> UserRepository:
        """获取用户Repository实例

        Returns:
            UserRepository: 用户Repository实例
        """
        return self.get_repository(UserRepository)

    @property
    def refresh_token_repository(self) -> RefreshTokenRepository:
        """获取刷新令牌Repository实例

        Returns:
            RefreshTokenRepository: 刷新令牌Repository实例
        """
        return self.get_repository(RefreshTokenRepository)

    @property
    def user_session_repository(self) -> UserSessionRepository:
        """获取用户会话Repository实例

        Returns:
            UserSessionRepository: 用户会话Repository实例
        """
        return self.get_repository(UserSessionRepository)

    @property
    def chat_repository(self) -> ChatRepository:
        """获取聊天Repository实例

        Returns:
            ChatRepository: 聊天Repository实例
        """
        return self.get_repository(ChatRepository)

    def get_repository_by_name(self, repository_name: str):
        """获取指定名称的Repository实例

        Args:
            repository_name: Repository名称

        Returns:
            Repository: Repository实例

        Raises:
            ValueError: 当指定的Repository不存在时抛出
        """
        if repository_name == COLLECTION_PARAMETER:
            return self.parameter_repository
        elif repository_name == 'document_image':
            return self.document_image_repository
        elif repository_name == 'image_file':
            return self.image_file_repository
        elif repository_name == 'manual_image':
            return self.manual_image_repository
        elif repository_name == 'user':
            return self.user_repository
        elif repository_name == 'refresh_token':
            return self.refresh_token_repository
        elif repository_name == 'user_session':
            return self.user_session_repository
        elif repository_name == 'chat':
            return self.chat_repository
        else:
            raise ValueError(f"Repository '{repository_name}' 不存在")

    def close(self):
        """关闭会话和清理资源"""
        if self._session:
            try:
                self._session.close()
                logger.debug("数据库会话已关闭")
            except Exception as e:
                logger.error(f"关闭会话失败: {str(e)}")
            finally:
                self._session = None
        self._repositories.clear()
        logger.debug("仓库实例缓存已清理")

    @classmethod
    def get_instance(cls, database_manager: DatabaseManager = None, session: Session = None) -> 'RepositoryFactory':
        """获取RepositoryFactory单例实例

        Args:
            database_manager: 数据库管理器（仅在首次创建时使用）
            session: 数据库会话（仅在首次创建时使用）

        Returns:
            RepositoryFactory单例实例
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.__init__(database_manager, session)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """重置RepositoryFactory单例，用于测试或重新初始化"""
        if cls._instance:
            cls._instance.close()
        cls._instance = None
        cls._initialized = False

    def __del__(self):
        """析构函数，确保资源被释放"""
        try:
            self.close()
        except:
            pass  # 析构时忽略错误 
