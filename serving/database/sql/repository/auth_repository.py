# -*- coding: utf-8 -*-
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from database.sql.model.auth import User, RefreshToken, UserSession
from .base_repository import BaseRepository
from util.logging.logger import get_logger

logger = get_logger(__name__)


class UserRepository(BaseRepository):
    """用户数据访问层，继承BaseRepository"""

    def __init__(self, session: Session = None):
        """初始化用户仓库

        Args:
            session: 数据库会话
        """
        super().__init__(User, session)

    def create_user(self, user_data: dict) -> User:
        """创建用户"""
        db_user = User(**user_data)
        return self.save(db_user)

    def get_user_by_id(self, user_id: int) -> Optional[User]:
        """根据ID获取用户"""
        return self.find_by_id(user_id)

    def get_user_by_username(self, username: str) -> Optional[User]:
        """根据用户名获取用户"""
        criteria = {'username': username}
        results = self.find_by_criteria(criteria)
        return results[0] if results else None

    def get_user_by_email(self, email: str) -> Optional[User]:
        """根据邮箱获取用户"""
        criteria = {'email': email}
        results = self.find_by_criteria(criteria)
        return results[0] if results else None

    def get_user_by_external_id(self, external_id: str, auth_provider: str) -> Optional[User]:
        """根据外部ID和认证提供商获取用户(SSO登录用)"""
        try:
            result = self.session.query(User).filter(
                and_(
                    User.external_id == external_id,
                    User.auth_provider == auth_provider
                )
            ).first()
            return result
        except Exception as e:
            logger.error(f"根据外部ID获取用户失败: external_id={external_id}, auth_provider={auth_provider}, error={str(e)}")
            return None

    def update_user(self, user_id: int, update_data: dict) -> Optional[User]:
        """更新用户信息"""
        return self.update_fields(user_id, update_data)

    def update_last_login(self, user_id: int):
        """更新最后登录时间"""
        try:
            self.session.query(User).filter(User.id == user_id).update(
                {"last_login_at": func.now()}
            )
            self.session.flush()
            logger.debug(f"更新最后登录时间成功: user_id={user_id}")
        except Exception as e:
            logger.error(f"更新最后登录时间失败: user_id={user_id}, error={str(e)}")

    def deactivate_user(self, user_id: int) -> bool:
        """停用用户"""
        return self.update_fields(user_id, {'is_active': False}) is not None

    def find_by_username(self, username: str) -> Optional[User]:
        """根据用户名查找用户（别名方法）"""
        return self.get_user_by_username(username)

    def find_by_email(self, email: str) -> Optional[User]:
        """根据邮箱查找用户（别名方法）"""
        return self.get_user_by_email(email)


class RefreshTokenRepository(BaseRepository):
    """刷新令牌数据访问层，继承BaseRepository"""

    def __init__(self, session: Session = None):
        """初始化刷新令牌仓库

        Args:
            session: 数据库会话
        """
        super().__init__(RefreshToken, session)

    def create_refresh_token(self, token_data: dict) -> RefreshToken:
        """创建刷新令牌"""
        db_token = RefreshToken(**token_data)
        return self.save(db_token)

    def get_refresh_token(self, token: str) -> Optional[RefreshToken]:
        """获取刷新令牌"""
        try:
            result = self.session.query(RefreshToken).filter(
                and_(
                    RefreshToken.token == token,
                    RefreshToken.is_revoked == False
                )
            ).first()
            return result
        except Exception as e:
            logger.error(f"获取刷新令牌失败: token={token[:8]}..., error={str(e)}")
            return None

    def revoke_refresh_token(self, token: str) -> bool:
        """撤销刷新令牌"""
        try:
            db_token = self.get_refresh_token(token)
            if db_token:
                result = self.update_fields(db_token.id, {'is_revoked': True}) is not None
                if result:
                    logger.info(f"撤销刷新令牌成功: token={token[:8]}...")
                return result
            logger.warning(f"刷新令牌不存在或已撤销: token={token[:8]}...")
            return False
        except Exception as e:
            logger.error(f"撤销刷新令牌失败: token={token[:8]}..., error={str(e)}")
            return False

    def revoke_user_tokens(self, user_id: int) -> int:
        """撤销用户的所有刷新令牌"""
        try:
            count = self.session.query(RefreshToken).filter(
                and_(
                    RefreshToken.user_id == user_id,
                    RefreshToken.is_revoked == False
                )
            ).update({"is_revoked": True})
            self.session.flush()
            logger.info(f"撤销用户令牌成功: user_id={user_id}, count={count}")
            return count
        except Exception as e:
            logger.error(f"撤销用户令牌失败: user_id={user_id}, error={str(e)}")
            return 0

    def cleanup_expired_tokens(self) -> int:
        """清理过期的令牌 - 标记为已撤销（软删除）"""
        try:
            from datetime import datetime
            import pytz

            utc = pytz.UTC
            now = datetime.now(utc)

            count = self.session.query(RefreshToken).filter(
                and_(
                    RefreshToken.expires_at < now,
                    RefreshToken.is_revoked == False
                )
            ).update({"is_revoked": True})
            self.session.flush()
            logger.info(f"清理过期令牌成功: count={count}")
            return count
        except Exception as e:
            logger.error(f"清理过期令牌失败: error={str(e)}")
            return 0

    def find_by_token(self, token: str) -> Optional[RefreshToken]:
        """根据令牌查找（别名方法）"""
        return self.get_refresh_token(token)


class UserSessionRepository(BaseRepository):
    """用户会话数据访问层，继承BaseRepository"""

    def __init__(self, session: Session = None):
        """初始化用户会话仓库

        Args:
            session: 数据库会话
        """
        super().__init__(UserSession, session)

    def create_session(self, session_data: dict) -> UserSession:
        """创建用户会话"""
        db_session = UserSession(**session_data)
        return self.save(db_session)

    def get_session(self, session_id: str) -> Optional[UserSession]:
        """获取会话"""
        try:
            result = self.session.query(UserSession).filter(
                and_(
                    UserSession.session_id == session_id,
                    UserSession.is_active == True
                )
            ).first()
            return result
        except Exception as e:
            logger.error(f"获取会话失败: session_id={session_id}, error={str(e)}")
            return None

    def deactivate_session(self, session_id: str) -> bool:
        """停用会话"""
        try:
            # 首先找到对应的会话记录ID
            session_record = self.get_session(session_id)
            if session_record:
                result = self.update_fields(session_record.id, {'is_active': False}) is not None
                if result:
                    logger.info(f"停用会话成功: session_id={session_id}")
                return result
            logger.warning(f"会话不存在或已停用: session_id={session_id}")
            return False
        except Exception as e:
            logger.error(f"停用会话失败: session_id={session_id}, error={str(e)}")
            return False

    def deactivate_user_sessions(self, user_id: int) -> int:
        """停用用户的所有会话"""
        try:
            count = self.session.query(UserSession).filter(
                and_(
                    UserSession.user_id == user_id,
                    UserSession.is_active == True
                )
            ).update({"is_active": False})
            self.session.flush()
            logger.info(f"停用用户会话成功: user_id={user_id}, count={count}")
            return count
        except Exception as e:
            logger.error(f"停用用户会话失败: user_id={user_id}, error={str(e)}")
            return 0

    def cleanup_expired_sessions(self) -> int:
        """清理过期的会话 - 标记为不活跃（软删除）"""
        try:
            from datetime import datetime
            import pytz

            utc = pytz.UTC
            now = datetime.now(utc)

            count = self.session.query(UserSession).filter(
                and_(
                    UserSession.expires_at < now,
                    UserSession.is_active == True
                )
            ).update({"is_active": False})
            self.session.flush()
            logger.info(f"清理过期会话成功: count={count}")
            return count
        except Exception as e:
            logger.error(f"清理过期会话失败: error={str(e)}")
            return 0

    def find_by_session_id(self, session_id: str) -> Optional[UserSession]:
        """根据会话ID查找（别名方法）"""
        return self.get_session(session_id)

    def get_latest_active_session(self, user_id: int) -> Optional[UserSession]:
        """获取用户最新的活跃会话"""
        try:
            result = self.session.query(UserSession).filter(
                and_(
                    UserSession.user_id == user_id,
                    UserSession.is_active == True
                )
            ).order_by(UserSession.created_at.desc()).first()
            return result
        except Exception as e:
            logger.error(f"获取用户最新会话失败: user_id={user_id}, error={str(e)}")
            return None

    def get_session_by_id(self, session_id: str) -> Optional[UserSession]:
        """根据会话ID查找会话（不检查是否激活）"""
        try:
            result = self.session.query(UserSession).filter(
                UserSession.session_id == session_id
            ).first()
            return result
        except Exception as e:
            logger.error(f"获取会话失败（不检查状态）: session_id={session_id}, error={str(e)}")
            return None