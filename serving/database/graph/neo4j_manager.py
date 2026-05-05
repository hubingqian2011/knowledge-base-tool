from neo4j import GraphDatabase
from typing import Optional
from config.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
from util.logging.logger import get_logger

class Neo4jManager:
    _instance = None
    _initialized = False
    _connection_failed = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, uri: Optional[str] = None, user: Optional[str] = None,
                 password: Optional[str] = None):
        """
        初始化Neo4j管理器（单例模式）

        Args:
            uri: Neo4j服务器URI，默认使用配置文件中的设置
            user: 用户名，默认使用配置文件中的设置
            password: 密码，默认使用配置文件中的设置
        """
        if not self._initialized:
            self.logger = get_logger(__name__)
            self.uri = uri or NEO4J_URI
            self.user = user or NEO4J_USER
            self.password = password or NEO4J_PASSWORD
            self.driver = None
            self._initialized = True

    def connect(self):
        """建立数据库连接"""
        if Neo4jManager._connection_failed:
            return False
        if self.driver is None:
            try:
                self.driver = GraphDatabase.driver(
                    self.uri,
                    auth=(self.user, self.password)
                )
                Neo4jManager._connection_failed = False
                self.logger.info(f"[Neo4j] 连接成功: {self.uri}")
                return True
            except Exception as e:
                self.logger.error(f"连接Neo4j数据库失败: {str(e)}")
                Neo4jManager._connection_failed = True
                return False
        return True

    def close(self):
        """关闭数据库连接"""
        if self.driver:
            self.driver.close()
            self.driver = None
            Neo4jManager._connection_failed = False

    def verify_connectivity(self):
        """验证数据库连接"""
        if not self.driver:
            return False
        try:
            with self.driver.session() as session:
                result = session.run("RETURN 1")
                return True
        except Exception as e:
            self.logger.error(f"验证连接失败: {str(e)}")
            return False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @classmethod
    def get_instance(cls):
        """获取Neo4jManager单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        # 确保连接已建立（已知连接失败过则不再重试）
        if not cls._instance.driver and not cls._connection_failed:
            cls._instance.connect()
        return cls._instance

    @classmethod
    def is_available(cls) -> bool:
        """返回 Neo4j 是否可用"""
        return (
            cls._instance is not None
            and cls._instance.driver is not None
            and not cls._connection_failed
        )

    def session(self, database: Optional[str] = None):
        """
        创建一个Neo4j会话

        Args:
            database: 数据库名称，如果不指定则使用默认数据库

        Returns:
            Session: Neo4j会话对象；连接不可用时返回 None，调用方需判断
        """
        if Neo4jManager._connection_failed:
            return None
        if not self.driver:
            self.connect()
        return self.driver.session(database=database) if self.driver else None

    def create_database(self, database: str) -> bool:
        """
        创建一个新的Neo4j数据库

        Args:
            database: 数据库名称

        Returns:
            bool: 是否创建成功
        """
        if Neo4jManager._connection_failed:
            return False
        try:
            if not self.driver:
                self.connect()

            with self.driver.session() as session:
                # 检查数据库是否已存在
                check_query = "SHOW DATABASES WHERE name = $database_name"
                result = session.run(check_query, {"database_name": database})
                exists = result.single() is not None

                if exists:
                    self.logger.debug(f"数据库 {database} 已存在")
                    return True

                # 创建新数据库
                create_query = f"CREATE DATABASE `{database}` IF NOT EXISTS"
                session.run(create_query)
                self.logger.debug(f"成功创建数据库 {database}")
                return True

        except Exception as e:
            self.logger.error(f"创建数据库失败: {str(e)}")
            return False

    def database_exists(self, database: str) -> bool:
        """
        检查数据库是否存在

        Args:
            database: 数据库名称

        Returns:
            bool: 数据库是否存在
        """
        if Neo4jManager._connection_failed:
            return False
        try:
            if not self.driver:
                self.connect()

            with self.driver.session() as session:
                check_query = "SHOW DATABASES WHERE name = $database_name"
                result = session.run(check_query, {"database_name": database})
                return result.single() is not None

        except Exception as e:
            self.logger.error(f"检查数据库存在性失败: {str(e)}")
            return False