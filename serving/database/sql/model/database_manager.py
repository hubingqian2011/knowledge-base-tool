from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from config.config import DATABASE_URL
from util.logging.logger import get_logger
from typing import Optional
import enum
import os

# 创建基类
Base = declarative_base()

# 配置日志
logger = get_logger(__name__)

# 多 worker 时仅一个进程执行 migrate，避免 MySQL 死锁
_MIGRATE_LOCK_PATH = os.path.join(os.path.dirname(__file__), ".migrate.lock")

class DatabaseManager:
    """数据库管理器
    用于初始化数据库连接和提供会话管理
    """

    def __init__(self, db_url=None):
        """初始化数据库管理器

        Args:
            db_url: 数据库连接URL，默认为None时使用配置中的URL
        """
        if db_url is None:
            # 使用配置中的数据库连接URL
            db_url = DATABASE_URL

        # 配置连接池参数
        self.engine = create_engine(
            db_url,
            pool_size=5,  # 连接池大小
            max_overflow=10,  # 超过pool_size后最多可以创建的连接数
            pool_timeout=30,  # 连接池获取连接的超时时间
            pool_recycle=3600,  # 连接在连接池中重用的时间限制，超过这个时间会被回收
            pool_pre_ping=True,  # 每次连接前ping一下数据库，确保连接有效
            echo=False
        )
        self.Session = sessionmaker(bind=self.engine)
        self.initialize_tables()

    def _ensure_models_loaded(self):
        """确保所有 SQLAlchemy 模型在建表/比表前完成注册。

        部分调用链会直接 import DatabaseManager，而不会先 import model.__init__。
        若此时模型模块尚未加载，Base.metadata 中就缺少对应表定义，create_all()
        会漏建新表，例如后台管理使用的 kb_collections。
        """
        try:
            from . import admin  # noqa: F401
            from . import auth  # noqa: F401
            from . import chat  # noqa: F401
            from . import document_image  # noqa: F401
            from . import image_file  # noqa: F401
            from . import manual_image  # noqa: F401
            from . import parameter  # noqa: F401
        except Exception as e:
            logger.error(f"加载数据库模型失败: {str(e)}")
            raise

    def create_tables(self):
        """创建所有定义的表"""
        try:
            self._ensure_models_loaded()
            Base.metadata.create_all(self.engine)
            logger.info("数据库表创建成功")
            return True
        except Exception as e:
            logger.error(f"创建数据库表失败: {str(e)}")
            return False
    
    def tables_exist(self, table_names=None):
        """检查指定的表是否存在
        
        Args:
            table_names: 要检查的表名列表，如果为None则检查所有模型中定义的表
            
        Returns:
            bool: 如果所有指定的表都存在则返回True，否则返回False
        """
        try:
            self._ensure_models_loaded()
            inspector = inspect(self.engine)
            existing_tables = inspector.get_table_names()
            
            if table_names is None:
                # 获取所有模型中定义的表
                table_names = [table.__tablename__ for table in Base.__subclasses__()]
            
            for table in table_names:
                if table not in existing_tables:
                    logger.info(f"表 {table} 不存在")
                    return False
            
            logger.info("所有表已存在")
            return True
        except Exception as e:
            logger.error(f"检查表是否存在时出错: {str(e)}")
            return False
            
    def initialize_tables(self):
        """初始化数据库表，如果表不存在则创建，如果表存在则检查并迁移结构。
        多 worker 启动时仅一个进程执行 migrate_schema，其余进程跳过，避免 MySQL 死锁。
        """
        if not self.tables_exist():
            return self.create_tables()

        run_migrate = True
        lock_fd = None
        try:
            import fcntl
            lock_fd = os.open(_MIGRATE_LOCK_PATH, os.O_CREAT | os.O_RDWR)
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ImportError, AttributeError):
            # 无 fcntl（如 Windows），单进程环境直接执行迁移
            pass
        except BlockingIOError:
            run_migrate = False
            logger.info("其他进程正在执行数据库迁移，本进程跳过 migrate_schema")

        if run_migrate:
            changes = self.compare_schema()
            if any(changes.values()):
                logger.info("检测到数据库表结构需要更新，开始迁移...")
                self.migrate_schema()
        if lock_fd is not None:
            try:
                import fcntl
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except Exception:
                pass
            os.close(lock_fd)
        return True
    
    def get_session(self):
        """获取数据库会话，每次调用都创建新的session"""
        try:
            session = self.Session()
            # 测试连接是否有效
            session.execute(text("SELECT 1"))
            return session
        except Exception as e:
            logger.error(f"数据库连接失败: {str(e)}")
            # 重新创建会话
            session = self.Session()
            return session

    def close_session(self, session):
        """关闭指定的数据库会话

        Args:
            session: 要关闭的数据库会话
        """
        try:
            if session:
                session.close()
        except Exception as e:
            logger.error(f"关闭会话失败: {str(e)}")

    def compare_schema(self):
        """比较数据库表结构与模型定义的差异

        Returns:
            dict: 包含需要添加、删除、修改的列信息
        """
        try:
            inspector = inspect(self.engine)
            changes = {
                'add_columns': [],
                'drop_columns': [],
                'modify_columns': []
            }

            # 获取所有模型类
            model_classes = [cls for cls in Base.__subclasses__() if hasattr(cls, '__tablename__')]

            for model_class in model_classes:
                table_name = model_class.__tablename__

                # 检查表是否存在
                if not inspector.has_table(table_name):
                    logger.warning(f"表 {table_name} 不存在，跳过比较")
                    continue

                # 获取数据库中的列信息
                db_columns = {col['name']: col for col in inspector.get_columns(table_name)}

                # 获取模型定义的列信息
                model_columns = {
                    col.name: {
                        'type': col.type,
                        'nullable': col.nullable,
                        'default': col.default,
                        'primary_key': col.primary_key
                    }
                    for col in model_class.__table__.columns
                }

                # 检查需要添加的列
                for col_name, col_info in model_columns.items():
                    if col_name not in db_columns:
                        changes['add_columns'].append({
                            'table': table_name,
                            'column': col_name,
                            'info': col_info
                        })

                # 检查需要删除的列
                for col_name in db_columns:
                    if col_name not in model_columns:
                        changes['drop_columns'].append({
                            'table': table_name,
                            'column': col_name
                        })

                # 检查需要修改的列
                for col_name, col_info in model_columns.items():
                    if col_name in db_columns:
                        db_col = db_columns[col_name]

                        # 比较默认值
                        model_default = col_info['default']
                        db_default = db_col.get('default')

                        # 标准化默认值以便比较
                        normalized_model_default = self._normalize_default_value(model_default)
                        normalized_db_default = self._normalize_default_value(db_default)

                        if (str(col_info['type']) != str(db_col['type']) or
                            col_info['nullable'] != db_col['nullable'] or
                            normalized_model_default != normalized_db_default):
                            changes['modify_columns'].append({
                                'table': table_name,
                                'column': col_name,
                                'old_info': db_col,
                                'new_info': col_info
                            })

            return changes

        except Exception as e:
            logger.error(f"比较数据库表结构时出错: {str(e)}")
            return {'add_columns': [], 'drop_columns': [], 'modify_columns': []}

    def _normalize_default_value(self, default_value):
        """标准化默认值以便比较

        Args:
            default_value: 原始默认值

        Returns:
            标准化后的默认值字符串
        """
        if default_value is None:
            return None

        # 如果是可调用对象（函数），跳过比较
        if callable(default_value):
            return "function"

        # 如果是SQLAlchemy默认值对象
        if hasattr(default_value, 'arg'):
            value = default_value.arg
            if callable(value):
                return "function"  # 函数类型跳过比较
            elif isinstance(value, bool):
                return str(value).lower()  # 布尔值转换为小写字符串
            elif isinstance(value, enum.Enum):
                return str(value.value)
            else:
                return str(value)
        elif isinstance(default_value, bool):
            return str(default_value).lower()  # 布尔值转换为小写字符串
        else:
            return str(default_value)

    def migrate_schema(self):
        """根据模型定义自动迁移数据库表结构

        Returns:
            bool: 迁移是否成功
        """
        try:
            changes = self.compare_schema()

            with self.engine.begin() as conn:
                # 处理需要添加的列
                for change in changes['add_columns']:
                    column = change['info']
                    sql = f"ALTER TABLE {change['table']} ADD COLUMN {change['column']} {column['type']}"
                    if not column['nullable']:
                        sql += " NOT NULL"
                    if column['default'] is not None:
                        # 处理默认值
                        default_value = column['default']
                        # 跳过函数类型的默认值，因为不能在SQL中使用
                        if callable(default_value):
                            logger.warning(f"跳过函数类型默认值: {change['table']}.{change['column']}")
                        elif hasattr(default_value, 'arg'):  # 处理SQLAlchemy默认值对象
                            if callable(default_value.arg):
                                logger.warning(f"跳过函数类型默认值: {change['table']}.{change['column']}")
                            elif isinstance(default_value.arg, enum.Enum):
                                default_value = f"'{default_value.arg.value}'"
                                sql += f" DEFAULT {default_value}"
                            elif isinstance(default_value.arg, bool):
                                # 布尔值不需要引号
                                default_value = str(default_value.arg)
                                sql += f" DEFAULT {default_value}"
                            else:
                                default_value = f"'{default_value.arg}'"
                                sql += f" DEFAULT {default_value}"
                        elif isinstance(default_value, bool):
                            # 直接的布尔值不需要引号
                            default_value = str(default_value)
                            sql += f" DEFAULT {default_value}"
                        else:
                            sql += f" DEFAULT '{default_value}'"
                    conn.execute(text(sql))
                    logger.info(f"添加列: {change['table']}.{change['column']}")

                # 处理需要修改的列
                for change in changes['modify_columns']:
                    column = change['new_info']
                    sql = f"ALTER TABLE {change['table']} MODIFY COLUMN {change['column']} {column['type']}"
                    if not column['nullable']:
                        sql += " NOT NULL"
                    if column['default'] is not None:
                        # 处理默认值
                        default_value = column['default']
                        # 跳过函数类型的默认值，因为不能在SQL中使用
                        if callable(default_value):
                            logger.warning(f"跳过函数类型默认值: {change['table']}.{change['column']}")
                        elif hasattr(default_value, 'arg'):  # 处理SQLAlchemy默认值对象
                            if callable(default_value.arg):
                                logger.warning(f"跳过函数类型默认值: {change['table']}.{change['column']}")
                            elif isinstance(default_value.arg, enum.Enum):
                                default_value = f"'{default_value.arg.value}'"
                                sql += f" DEFAULT {default_value}"
                            elif isinstance(default_value.arg, bool):
                                # 布尔值不需要引号
                                default_value = str(default_value.arg)
                                sql += f" DEFAULT {default_value}"
                            else:
                                default_value = f"'{default_value.arg}'"
                                sql += f" DEFAULT {default_value}"
                        elif isinstance(default_value, bool):
                            # 直接的布尔值不需要引号
                            default_value = str(default_value)
                            sql += f" DEFAULT {default_value}"
                        else:
                            sql += f" DEFAULT '{default_value}'"
                    conn.execute(text(sql))
                    logger.info(f"修改列: {change['table']}.{change['column']}")

                # 处理需要删除的列
                for change in changes['drop_columns']:
                    sql = f"ALTER TABLE {change['table']} DROP COLUMN {change['column']}"
                    conn.execute(text(sql))
                    logger.info(f"删除列: {change['table']}.{change['column']}")

            logger.info("数据库表结构迁移成功")
            return True

        except Exception as e:
            logger.error(f"数据库表结构迁移失败: {str(e)}")
            return False

    def __enter__(self):
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        if exc_type is not None:
            logger.error(f"数据库管理器退出时发生异常: {exc_val}")

# 创建全局数据库管理器实例
db_manager = DatabaseManager() 
