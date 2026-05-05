from .repository_factory import RepositoryFactory

# 使用RepositoryFactory单例来获取具体的repository实例

# 使用示例：
# factory = RepositoryFactory.get_instance()  # 获取单例实例
# user_repo = factory.user_repository

__all__ = [
    'RepositoryFactory'
]