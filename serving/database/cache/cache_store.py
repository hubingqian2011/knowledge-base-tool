from typing import Any, Optional, Union, List, Dict, Tuple
import json
from datetime import datetime, timedelta
import redis
from config.config import REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_PASSWORD
from util.logging.logger import get_logger

class CacheStore:
    """缓存存储管理类"""
    
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(
        self,
        host: str = REDIS_HOST,
        port: int = REDIS_PORT,
        db: int = REDIS_DB,
        password: Optional[str] = REDIS_PASSWORD,
        default_expire: int = 3600,  # 默认过期时间1小时
        prefix: str = "cache:",  # 键前缀
        decode_responses: bool = True
    ):
        """
        初始化缓存存储
        
        Args:
            host: Redis 服务器地址
            port: Redis 服务器端口
            db: 数据库编号
            password: 密码（可选）
            default_expire: 默认过期时间（秒）
            prefix: 键前缀
            decode_responses: 是否自动解码响应
        """
        # 如果已经初始化过，直接返回
        if hasattr(self, 'client'):
            return
            
        self.logger = get_logger(__name__)
        try:
            self.client = redis.Redis(
                host=host,
                port=port,
                db=db,
                password=password,
                decode_responses=decode_responses
            )
            self.client.ping()  # 测试连接
            self.logger.info("Redis 客户端初始化成功")
        except Exception as e:
            self.logger.error(f"Redis 客户端初始化失败: {str(e)}")
            raise
            
        self.default_expire = default_expire
        self.prefix = prefix
    
    def _get_key(self, key: str) -> str:
        """
        获取带前缀的键
        
        Args:
            key: 原始键
            
        Returns:
            str: 带前缀的键
        """
        return f"{self.prefix}{key}"
    
    def _serialize_value(self, value: Any) -> str:
        """
        序列化值
        
        Args:
            value: 要序列化的值
            
        Returns:
            str: 序列化后的值
        """
        if isinstance(value, (str, int, float)):
            return str(value)
        return json.dumps(value)
    
    def _deserialize_value(self, value: str) -> Any:
        """
        反序列化值
        
        Args:
            value: 要反序列化的值
            
        Returns:
            Any: 反序列化后的值
        """
        if value is None:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    
    def set(
        self,
        key: str,
        value: Any,
        expire: Optional[int] = None,
        nx: bool = False,
        xx: bool = False
    ) -> bool:
        """
        设置缓存
        
        Args:
            key: 键
            value: 值
            expire: 过期时间（秒）
            nx: 仅在键不存在时设置
            xx: 仅在键存在时设置
            
        Returns:
            bool: 是否设置成功
        """
        try:
            return self.client.set(
                self._get_key(key),
                self._serialize_value(value),
                ex=expire or self.default_expire,
                nx=nx,
                xx=xx
            )
        except Exception as e:
            self.logger.error(f"设置缓存失败: {str(e)}")
            return False
    
    def get(self, key: str) -> Optional[Any]:
        """
        获取缓存
        
        Args:
            key: 键
            
        Returns:
            Any: 值
        """
        try:
            value = self.client.get(self._get_key(key))
            return self._deserialize_value(value)
        except Exception as e:
            self.logger.error(f"获取缓存失败: {str(e)}")
            return None
    
    def delete(self, *keys: str) -> int:
        """
        删除缓存
        
        Args:
            keys: 键列表
            
        Returns:
            int: 删除的键数量
        """
        try:
            return self.client.delete(*[self._get_key(k) for k in keys])
        except Exception as e:
            self.logger.error(f"删除缓存失败: {str(e)}")
            return 0
    
    def exists(self, *keys: str) -> int:
        """
        检查缓存是否存在
        
        Args:
            keys: 键列表
            
        Returns:
            int: 存在的键数量
        """
        try:
            return self.client.exists(*[self._get_key(k) for k in keys])
        except Exception as e:
            self.logger.error(f"检查缓存是否存在失败: {str(e)}")
            return 0
    
    def expire(self, key: str, seconds: int) -> bool:
        """
        设置缓存过期时间
        
        Args:
            key: 键
            seconds: 过期时间（秒）
            
        Returns:
            bool: 是否设置成功
        """
        try:
            return bool(self.client.expire(self._get_key(key), seconds))
        except Exception as e:
            self.logger.error(f"设置缓存过期时间失败: {str(e)}")
            return False
    
    def ttl(self, key: str) -> int:
        """
        获取缓存剩余过期时间
        
        Args:
            key: 键
            
        Returns:
            int: 剩余过期时间（秒）
        """
        try:
            return self.client.ttl(self._get_key(key))
        except Exception as e:
            self.logger.error(f"获取缓存剩余过期时间失败: {str(e)}")
            return -2
    
    def hset(
        self,
        name: str,
        key: Optional[str] = None,
        value: Optional[Any] = None,
        mapping: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        设置哈希表缓存
        
        Args:
            name: 哈希表名
            key: 字段名
            value: 字段值
            mapping: 字段映射
            
        Returns:
            int: 新增字段数量
        """
        try:
            if mapping is not None:
                mapping = {
                    k: self._serialize_value(v)
                    for k, v in mapping.items()
                }
                return self.client.hset(self._get_key(name), mapping=mapping)
            else:
                return self.client.hset(
                    self._get_key(name),
                    key,
                    self._serialize_value(value)
                )
        except Exception as e:
            self.logger.error(f"设置哈希表缓存失败: {str(e)}")
            return 0
    
    def hget(self, name: str, key: str) -> Optional[Any]:
        """
        获取哈希表缓存
        
        Args:
            name: 哈希表名
            key: 字段名
            
        Returns:
            Any: 字段值
        """
        try:
            value = self.client.hget(self._get_key(name), key)
            return self._deserialize_value(value)
        except Exception as e:
            self.logger.error(f"获取哈希表缓存失败: {str(e)}")
            return None
    
    def hgetall(self, name: str) -> Dict[str, Any]:
        """
        获取哈希表所有缓存
        
        Args:
            name: 哈希表名
            
        Returns:
            Dict[str, Any]: 字段和值的映射
        """
        try:
            result = self.client.hgetall(self._get_key(name))
            if not result:
                return {}
            return {
                k: self._deserialize_value(v)
                for k, v in result.items()
            }
        except Exception as e:
            self.logger.error(f"获取哈希表所有缓存失败: {str(e)}")
            return {}
    
    def hdel(self, name: str, *keys: str) -> int:
        """
        删除哈希表缓存
        
        Args:
            name: 哈希表名
            keys: 字段名列表
            
        Returns:
            int: 删除的字段数量
        """
        try:
            return self.client.hdel(self._get_key(name), *keys)
        except Exception as e:
            self.logger.error(f"删除哈希表缓存失败: {str(e)}")
            return 0
    
    def lpush(self, name: str, *values: Any) -> int:
        """
        将一个或多个值插入到列表头部
        
        Args:
            name: 列表名
            values: 值列表
            
        Returns:
            int: 列表长度
        """
        try:
            values = [self._serialize_value(v) for v in values]
            return self.client.lpush(self._get_key(name), *values)
        except Exception as e:
            self.logger.error(f"插入列表头部失败: {str(e)}")
            return 0
    
    def rpush(self, name: str, *values: Any) -> int:
        """
        将一个或多个值插入到列表尾部
        
        Args:
            name: 列表名
            values: 值列表
            
        Returns:
            int: 列表长度
        """
        try:
            values = [self._serialize_value(v) for v in values]
            return self.client.rpush(self._get_key(name), *values)
        except Exception as e:
            self.logger.error(f"插入列表尾部失败: {str(e)}")
            return 0
    
    def lrange(self, name: str, start: int, end: int) -> List[Any]:
        """
        获取列表指定范围内的元素
        
        Args:
            name: 列表名
            start: 起始位置
            end: 结束位置
            
        Returns:
            List[Any]: 元素列表
        """
        try:
            values = self.client.lrange(self._get_key(name), start, end)
            return [self._deserialize_value(v) for v in values]
        except Exception as e:
            self.logger.error(f"获取列表元素失败: {str(e)}")
            return []
    
    def llen(self, name: str) -> int:
        """
        获取列表长度
        
        Args:
            name: 列表名
            
        Returns:
            int: 列表长度
        """
        try:
            return self.client.llen(self._get_key(name))
        except Exception as e:
            self.logger.error(f"获取列表长度失败: {str(e)}")
            return 0
    
    def sadd(self, name: str, *values: Any) -> int:
        """
        将一个或多个成员元素加入到集合中
        
        Args:
            name: 集合名
            values: 成员元素列表
            
        Returns:
            int: 新增成员数量
        """
        try:
            values = [self._serialize_value(v) for v in values]
            return self.client.sadd(self._get_key(name), *values)
        except Exception as e:
            self.logger.error(f"添加集合成员失败: {str(e)}")
            return 0
    
    def smembers(self, name: str) -> set:
        """
        获取集合的所有成员
        
        Args:
            name: 集合名
            
        Returns:
            set: 成员集合
        """
        try:
            values = self.client.smembers(self._get_key(name))
            return {self._deserialize_value(v) for v in values}
        except Exception as e:
            self.logger.error(f"获取集合成员失败: {str(e)}")
            return set()
    
    def srem(self, name: str, *values: Any) -> int:
        """
        移除集合中一个或多个成员
        
        Args:
            name: 集合名
            values: 成员元素列表
            
        Returns:
            int: 移除的成员数量
        """
        try:
            values = [self._serialize_value(v) for v in values]
            return self.client.srem(self._get_key(name), *values)
        except Exception as e:
            self.logger.error(f"移除集合成员失败: {str(e)}")
            return 0
    
    def zadd(
        self,
        name: str,
        mapping: Dict[Any, float],
        nx: bool = False,
        xx: bool = False,
        ch: bool = False,
        incr: bool = False
    ) -> int:
        """
        将一个或多个成员元素及其分数值加入到有序集合中
        
        Args:
            name: 有序集合名
            mapping: 成员分数映射
            nx: 仅添加新成员
            xx: 仅更新已存在的成员
            ch: 返回修改的成员数量
            incr: 对成员分数进行增量操作
            
        Returns:
            int: 新增成员数量
        """
        try:
            mapping = {
                self._serialize_value(k): v
                for k, v in mapping.items()
            }
            return self.client.zadd(
                self._get_key(name),
                mapping,
                nx=nx,
                xx=xx,
                ch=ch,
                incr=incr
            )
        except Exception as e:
            self.logger.error(f"添加有序集合成员失败: {str(e)}")
            return 0
    
    def zrange(
        self,
        name: str,
        start: int,
        end: int,
        desc: bool = False,
        withscores: bool = False,
        score_cast_func: Optional[callable] = None
    ) -> List[Any]:
        """
        获取有序集合指定范围内的成员
        
        Args:
            name: 有序集合名
            start: 起始位置
            end: 结束位置
            desc: 是否降序
            withscores: 是否返回分数
            score_cast_func: 分数转换函数
            
        Returns:
            List[Any]: 成员列表
        """
        try:
            values = self.client.zrange(
                self._get_key(name),
                start,
                end,
                desc=desc,
                withscores=withscores,
                score_cast_func=score_cast_func
            )
            if withscores:
                return [
                    (self._deserialize_value(v[0]), v[1])
                    for v in values
                ]
            return [self._deserialize_value(v) for v in values]
        except Exception as e:
            self.logger.error(f"获取有序集合成员失败: {str(e)}")
            return []
    
    def zrem(self, name: str, *values: Any) -> int:
        """
        移除有序集合中一个或多个成员
        
        Args:
            name: 有序集合名
            values: 成员元素列表
            
        Returns:
            int: 移除的成员数量
        """
        try:
            values = [self._serialize_value(v) for v in values]
            return self.client.zrem(self._get_key(name), *values)
        except Exception as e:
            self.logger.error(f"移除有序集合成员失败: {str(e)}")
            return 0 