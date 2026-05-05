# -*- coding: utf-8 -*-
from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, ForeignKey, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database_manager import Base


class User(Base):
    """用户表 - 简单设计，支持扩展"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, comment="用户ID")
    username = Column(String(50), unique=True, index=True, nullable=False, comment="用户名")
    email = Column(String(100), unique=True, index=True, nullable=True, comment="邮箱")
    hashed_password = Column(String(255), nullable=True, comment="密码哈希(SSO用户可为空)")
    full_name = Column(String(100), nullable=True, comment="全名")
    is_active = Column(Boolean, default=True, comment="是否激活")
    is_superuser = Column(Boolean, default=False, comment="是否超级用户")

    # 扩展字段 - 支持多种登录方式
    auth_type = Column(String(20), default="local", comment="认证类型：local, sso, token")
    external_id = Column(String(100), nullable=True, index=True, comment="外部系统用户ID")
    auth_provider = Column(String(50), nullable=True, comment="认证提供商")

    # 扩展信息字段 - 存储用户额外信息的JSON字典
    user_info = Column(JSON, nullable=True, comment="用户扩展信息(JSON格式)")

    # 时间戳
    last_login_at = Column(DateTime(timezone=True), nullable=True, comment="最后登录时间")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间")

    def to_dict(self) -> dict:
        """将User对象转换为字典，避免Session问题"""
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "full_name": self.full_name,
            "is_active": self.is_active,
            "is_superuser": self.is_superuser,
            "auth_type": self.auth_type,
            "external_id": self.external_id,
            "auth_provider": self.auth_provider,
            "user_info": self.user_info,
            "last_login_at": self.last_login_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }


class RefreshToken(Base):
    """刷新令牌表"""
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True, comment="ID")
    token = Column(String(500), unique=True, nullable=False, index=True, comment="刷新令牌")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, comment="用户ID")
    session_id = Column(String(100), nullable=True, index=True, comment="关联的会话ID")
    expires_at = Column(DateTime(timezone=True), nullable=False, comment="过期时间")
    is_revoked = Column(Boolean, default=False, comment="是否已撤销")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")


class UserSession(Base):
    """用户会话表 - 支持Redis缓存扩展"""
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, index=True, comment="会话ID")
    session_id = Column(String(100), unique=True, nullable=False, index=True, comment="会话ID")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, comment="用户ID")
    ip_address = Column(String(45), nullable=True, comment="IP地址")
    user_agent = Column(Text, nullable=True, comment="用户代理")
    is_active = Column(Boolean, default=True, comment="是否活跃")
    expires_at = Column(DateTime(timezone=True), nullable=False, comment="过期时间")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")