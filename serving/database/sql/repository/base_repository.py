"""
基础仓库类
提供通用的CRUD操作，类似于Spring Boot中的JpaRepository
"""
from typing import TypeVar, Generic, Type, List, Optional, Dict, Any, Union
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import desc
from util.logging.logger import get_logger

logger = get_logger(__name__)

# 定义泛型类型变量，表示任意SQLAlchemy模型类
T = TypeVar('T')

class BaseRepository(Generic[T]):
    """基础仓库类
    实现通用的CRUD操作，可被具体仓库继承
    """

    def __init__(self, model_class: Type[T], session: Session):
        """初始化基础仓库

        Args:
            model_class: 模型类，如VoiceRecord, Ticket等
            session: 数据库会话实例
        """
        self.model_class = model_class
        self.session = session
        self.model_name = model_class.__name__

    def find_all(self, limit: int = None, offset: int = None, order_by_desc: bool = False) -> List[T]:
        """查询所有记录

        Args:
            limit: 限制返回记录数
            offset: 偏移量
            order_by_desc: 是否按ID降序排列

        Returns:
            所有记录的列表

        Raises:
            SQLAlchemyError: 数据库操作异常
        """
        try:
            query = self.session.query(self.model_class)

            # 获取主键字段名用于排序
            primary_key = self.model_class.__mapper__.primary_key[0].name

            if order_by_desc:
                query = query.order_by(desc(getattr(self.model_class, primary_key)))

            if offset is not None:
                query = query.offset(offset)

            if limit is not None:
                query = query.limit(limit)

            return query.all()
        except SQLAlchemyError as e:
            logger.error(f"查询所有{self.model_name}记录失败: {str(e)}")
            raise

    def find_by_id(self, id_value: Any) -> Optional[T]:
        """根据ID查询记录

        Args:
            id_value: ID值

        Returns:
            找到的记录，如果不存在则返回None

        Raises:
            SQLAlchemyError: 数据库操作异常
        """
        try:
            return self.session.query(self.model_class).get(id_value)
        except SQLAlchemyError as e:
            logger.error(f"根据ID查询{self.model_name}记录失败: {str(e)}")
            raise

    def find_by_criteria(self, criteria: Dict[str, Any]) -> List[T]:
        """根据条件查询记录

        Args:
            criteria: 查询条件字典，键为字段名，值为字段值

        Returns:
            符合条件的记录列表

        Raises:
            SQLAlchemyError: 数据库操作异常
        """
        try:
            query = self.session.query(self.model_class)
            if criteria:
                query = query.filter_by(**criteria)
            return query.all()
        except SQLAlchemyError as e:
            logger.error(f"根据条件查询{self.model_name}记录失败: {str(e)}")
            raise

    def save(self, entity: T) -> T:
        """保存记录（新增或更新）

        Args:
            entity: 要保存的实体对象

        Returns:
            保存后的实体对象

        Raises:
            SQLAlchemyError: 数据库操作异常
        """
        try:
            self.session.add(entity)
            # 使用flush而不是commit，让上下文管理器统一处理事务
            self.session.flush()
            return entity
        except SQLAlchemyError as e:
            logger.error(f"保存{self.model_name}记录失败: {str(e)}")
            # 确保在异常时回滚
            self.session.rollback()
            raise

    def save_all(self, entities: List[T]) -> List[T]:
        """批量保存记录

        Args:
            entities: 要保存的实体对象列表

        Returns:
            保存成功的实体对象列表

        Raises:
            SQLAlchemyError: 数据库操作异常
        """
        try:
            for entity in entities:
                self.session.add(entity)
            self.session.flush()
            return entities
        except SQLAlchemyError as e:
            logger.error(f"批量保存{self.model_name}记录失败: {str(e)}")
            raise

    def update_fields(self, id_value: Any, fields: Dict[str, Any]) -> Optional[T]:
        """批量更新指定字段

        Args:
            id_value: ID值
            fields: 需要更新的字段字典

        Returns:
            更新后的实体对象，如果不存在则返回None

        Raises:
            SQLAlchemyError: 数据库操作异常
        """
        try:
            entity = self.find_by_id(id_value)
            if not entity:
                return None

            for key, value in fields.items():
                if hasattr(entity, key):
                    setattr(entity, key, value)

            self.session.flush()
            return entity
        except SQLAlchemyError as e:
            logger.error(f"更新{self.model_name}字段失败: {str(e)}")
            raise

    def delete(self, id_value: Any) -> bool:
        """删除记录（简化方法名）

        Args:
            id_value: ID值

        Returns:
            是否删除成功

        Raises:
            SQLAlchemyError: 数据库操作异常
        """
        return self.delete_by_id(id_value)

    def delete_by_id(self, id_value: Any) -> bool:
        """根据ID删除记录

        Args:
            id_value: ID值

        Returns:
            是否删除成功

        Raises:
            SQLAlchemyError: 数据库操作异常
        """
        try:
            entity = self.find_by_id(id_value)
            if entity:
                self.session.delete(entity)
                self.session.flush()
                return True
            return False
        except SQLAlchemyError as e:
            logger.error(f"删除{self.model_name}记录失败: {str(e)}")
            raise

    def count(self, criteria: Dict[str, Any] = None) -> int:
        """计算符合条件的记录数量

        Args:
            criteria: 查询条件字典，默认为None表示计算所有记录

        Returns:
            记录数量

        Raises:
            SQLAlchemyError: 数据库操作异常
        """
        try:
            query = self.session.query(self.model_class)
            if criteria:
                query = query.filter_by(**criteria)
            return query.count()
        except SQLAlchemyError as e:
            logger.error(f"计算{self.model_name}记录数量失败: {str(e)}")
            raise

    def exists_by_id(self, id_value: Any) -> bool:
        """判断指定ID的记录是否存在

        Args:
            id_value: ID值

        Returns:
            是否存在

        Raises:
            SQLAlchemyError: 数据库操作异常
        """
        try:
            return self.find_by_id(id_value) is not None
        except SQLAlchemyError:
            raise

    def __enter__(self):
        """上下文管理器入口，支持with语句使用

        Returns:
            self: 返回仓库实例本身
        """
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口，处理事务提交或回滚

        Args:
            exc_type: 异常类型，如果没有异常则为None
            exc_val: 异常值，如果没有异常则为None
            exc_tb: 异常追踪，如果没有异常则为None

        Returns:
            bool: 返回False表示不抑制异常
        """
        try:
            if self.session:
                if exc_type is None:
                    # 没有异常时提交事务
                    self.session.commit()
                    # logger.debug(f"{self.model_name}事务已提交")  # 关闭：每次请求10+次，噪音
                else:
                    # 有异常时回滚事务
                    self.session.rollback()
                    logger.debug(f"{self.model_name}事务已回滚，异常: {exc_val}")
        except Exception as e:
            logger.error(f"处理{self.model_name}事务时出错: {str(e)}")
            # 尝试回滚以防万一
            try:
                if self.session:
                    self.session.rollback()
            except Exception:
                pass
        finally:
            # 确保连接归还连接池，避免连接泄漏
            if self.session:
                try:
                    self.session.close()
                except Exception as e:
                    logger.error(f"关闭{self.model_name}会话失败: {str(e)}")

        # 不抑制异常，让调用者处理
        return False