# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import jwt
import hmac
from passlib.context import CryptContext
from fastapi import HTTPException, status
import uuid
import pytz

from database.sql.repository.repository_factory import RepositoryFactory
from config.config import AUTH_SECRET_KEY, AUTH_ACCESS_TOKEN_EXPIRE_MINUTES, AUTH_REFRESH_TOKEN_EXPIRE_DAYS, JWT_PROVIDER_CONFIGS
from util.logging.logger import get_logger
from service.knowledge.esb.device_scan_info import device_scan_info_service
from service.auth.knowledge_permission_utils import build_permission_metadata


class AuthService:
    """认证服务 - 简单设计，支持多种认证方式"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """初始化认证服务"""
        self.secret_key = AUTH_SECRET_KEY
        self.algorithm = "HS256"
        self.access_token_expire_minutes = AUTH_ACCESS_TOKEN_EXPIRE_MINUTES
        self.refresh_token_expire_days = AUTH_REFRESH_TOKEN_EXPIRE_DAYS

        self.pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        # 使用RepositoryFactory单例获取repository实例
        self.repository_factory = RepositoryFactory.get_instance()
        # 初始化日志记录器
        self.logger = get_logger(__name__)

    def _ensure_admin_highest_permission(self, user: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """为管理账号兜底补最高权限。"""
        if not isinstance(user, dict):
            return user

        raw_user_info = user.get("user_info") or {}
        user_info = dict(raw_user_info) if isinstance(raw_user_info, dict) else {}
        is_admin = bool(user.get("is_superuser")) or user_info.get("role") == "admin" or user.get("username") == "admin"
        if not is_admin:
            return user

        if user_info.get("role") != "admin":
            user_info["role"] = "admin"
        if user_info.get("permission_level") in (None, ""):
            user_info.update(build_permission_metadata(1))
        user["user_info"] = user_info
        return user

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """验证密码"""
        return self.pwd_context.verify(plain_password, hashed_password)

    def get_password_hash(self, password: str) -> str:
        """生成密码哈希"""
        # 确保密码是正确的字符串格式，避免bcrypt编码问题
        if isinstance(password, bytes):
            password = password.decode('utf-8')
        # 确保密码不为None且是字符串类型
        if password is None:
            password = ""
        return self.pwd_context.hash(password)

    def create_access_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """创建访问令牌"""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(pytz.UTC) + expires_delta
        else:
            expire = datetime.now(pytz.UTC) + timedelta(minutes=self.access_token_expire_minutes)

        to_encode.update({"exp": expire, "type": "access"})

        # Add session_id if provided in data dict
        if "session_id" in data:
            to_encode["session_id"] = data["session_id"]

        encoded_jwt = jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)
        return encoded_jwt

    def create_refresh_token(self, user_id: int, session_id: str = None) -> str:
        """创建刷新令牌"""
        refresh_token = str(uuid.uuid4())
        expires_at = datetime.now(pytz.UTC) + timedelta(days=self.refresh_token_expire_days)

        with self.repository_factory.refresh_token_repository as refresh_token_repo:
            refresh_token_repo.create_refresh_token({
                "token": refresh_token,
                "user_id": user_id,
                "session_id": session_id,
                "expires_at": expires_at
            })

        return refresh_token

    def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """用户名密码认证"""
        with self.repository_factory.user_repository as user_repo:
            user = user_repo.get_user_by_username(username)
            if not user:
                self.logger.warning(f"用户名不存在: {username}")
                return None
            if not user.hashed_password:
                self.logger.warning(f"用户 {username} 未设置密码")
                return None
            if not self.verify_password(password, user.hashed_password):
                self.logger.warning(f"用户 {username} 密码验证失败")
                return None

            self.logger.debug(f"用户 {username} 认证成功")
            return self._ensure_admin_highest_permission(user.to_dict())

    def authenticate_user_by_email(self, email: str, password: str) -> Optional[Dict[str, Any]]:
        """邮箱密码认证"""
        with self.repository_factory.user_repository as user_repo:
            user = user_repo.get_user_by_email(email)
            if not user:
                self.logger.warning(f"邮箱不存在: {email}")
                return None
            if not user.hashed_password:
                self.logger.warning(f"用户 {email} 未设置密码")
                return None
            if not self.verify_password(password, user.hashed_password):
                self.logger.warning(f"用户 {email} 密码验证失败")
                return None

            self.logger.debug(f"邮箱 {email} 认证成功")
            return self._ensure_admin_highest_permission(user.to_dict())

    def ensure_default_admin(self) -> Dict[str, Any]:
        """确保默认 admin 存在，用于关闭鉴权时的首启访问。"""
        with self.repository_factory.user_repository as user_repo:
            existing_user = user_repo.get_user_by_username("admin")
            if existing_user:
                return self._ensure_admin_highest_permission(existing_user.to_dict())

        self.logger.warning("检测到默认 admin 不存在，自动创建 admin 账号用于首启访问")
        return self.create_user(
            username="admin",
            password="admin",
            full_name="管理员",
            permission_level=1,
        )

    def authenticate_sso_user(self, external_id: str, auth_provider: str, user_info: Dict[str, Any]) -> Dict[str, Any]:
        """SSO用户认证 - 自动创建或更新用户"""
        self.logger.info(f"SSO认证尝试: provider={auth_provider}, external_id={external_id}")

        # 直接使用user_repository上下文管理器确保事务一致性
        with self.repository_factory.user_repository as user_repo:
            user = user_repo.get_user_by_external_id(external_id, auth_provider)

            if not user:
                # 首次登录，创建用户
                username = user_info.get("username") or f"{auth_provider}_{external_id}"
                user_data = {
                    "username": username,
                    "email": user_info.get("email"),
                    "full_name": user_info.get("full_name"),
                    "auth_type": "sso",
                    "external_id": external_id,
                    "auth_provider": auth_provider,
                    "is_active": True,
                    "created_at": datetime.now(pytz.UTC)
                }
                user = user_repo.create_user(user_data)
                self.logger.info(f"SSO新用户创建成功: {username} (provider={auth_provider})")
            else:
                # 更新用户信息
                update_data = {
                    "full_name": user_info.get("full_name", user.full_name),
                    "email": user_info.get("email", user.email),
                    "last_login_at": datetime.now(pytz.UTC)
                }
                user = user_repo.update_user(user.id, update_data)
                self.logger.info(f"SSO用户信息更新成功: {user.username} (provider={auth_provider})")

            return self._ensure_admin_highest_permission(user.to_dict())

    def authenticate_token_user(self, token: str, user_info: Dict[str, Any], source: str = None) -> Dict[str, Any]:
        """Token传递认证 - 支持多种Platform Backend系统的token验证"""
        self.logger.debug(f"收到 token 原始(前100字): {token[:100] if token else ''}")
        # 获取source对应的JWT配置
        if not source:
            source = user_info.get("source", "default")

        self.logger.info(f"Token认证尝试: source={source}")

        # 使用RepositoryFactory上下文管理器确保事务一致性
        with self.repository_factory.user_repository as user_repo:

            jwt_config = JWT_PROVIDER_CONFIGS.get(source, JWT_PROVIDER_CONFIGS["default"])
            secret_key = jwt_config["secret_key"]
            algorithm = jwt_config["algorithm"]

            # 根据不同的算法处理token
            if algorithm == "MD5":
                # === 管工厂云：MD5验证 ===
                # token只是MD5签名，用户信息在user_info中
                self._verify_factory_md5_token(token, user_info, jwt_config)
                payload = user_info  # 使用user_info作为payload

            elif algorithm == "HS256":
                if source in ["aftersales", "controller"]:
                    # === 售后系统/设备控制器：Base64 + HMAC验证 ===
                    payload = self._verify_base64_hmac_token(token, secret_key)
                else:
                    # === 默认：标准JWT验证 ===
                    payload = jwt.decode(token, secret_key, algorithms=[algorithm])
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unsupported algorithm: {algorithm}"
                )

            # 从payload中提取用户信息，适配不同系统的字段结构
            username, email, full_name, external_id, user_info_data = self._extract_user_info_from_payload(
                payload, user_info, source
            )

            if not username:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid token payload: missing username"
                )

            # 查找或创建用户
            user = user_repo.get_user_by_username(username)
            if not user:
                # 如果提供了external_id，优先使用external_id查找用户
                if external_id and source != "default":
                    user = user_repo.get_user_by_external_id(external_id, source)

                if not user:
                    # 创建新用户
                    user_data = {
                        "username": username,
                        "email": email,
                        "full_name": full_name,
                        "auth_type": "token",
                        "auth_provider": source,
                        "external_id": external_id,
                        "user_info": user_info_data,
                        "is_active": True,
                        "created_at": datetime.now(pytz.UTC)
                    }
                    user = user_repo.create_user(user_data)
                    self.logger.info(f"Token新用户创建成功: {username} (source={source})")
            else:
                # 更新用户信息和最后登录时间
                update_data = {
                    "last_login_at": datetime.now(pytz.UTC)
                }
                if email:
                    update_data["email"] = email
                if full_name:
                    update_data["full_name"] = full_name
                if user_info_data:
                    # 合并策略：保留老 user_info 里已有的字段（尤其是管理员手动设置过的
                    # permission_level / role），仅用新 token payload 补缺或更新非权限字段，
                    # 避免第三方平台每次登录把后台改过的权限覆盖回默认值。
                    existing_info_raw = getattr(user, "user_info", None) or {}
                    if isinstance(existing_info_raw, str):
                        try:
                            import json as _json
                            existing_info = _json.loads(existing_info_raw)
                        except Exception:
                            existing_info = {}
                    elif isinstance(existing_info_raw, dict):
                        existing_info = dict(existing_info_raw)
                    else:
                        existing_info = {}

                    merged_info = dict(existing_info)
                    merged_info.update(user_info_data)
                    update_data["user_info"] = merged_info

                user = user_repo.update_user(user.id, update_data)
                self.logger.info(f"Token用户信息更新成功: {user.username} (source={source})")
            return self._ensure_admin_highest_permission(user.to_dict())

    def _verify_factory_md5_token(self, token: str, user_info: Dict[str, Any], jwt_config: Dict[str, Any]) -> None:
        """验证管工厂云的MD5 token"""
        import hashlib

        self.logger.debug(f"验证管工厂MD5 token: userId={user_info.get('userId')}, companyId={user_info.get('companyId')}")

        # 管工厂需要的关键参数
        source_id = jwt_config.get("source_id", "003")
        required_fields = ["userId", "companyId", "timestamp"]

        # 检查必填字段
        for field in required_fields:
            if field not in user_info:
                self.logger.error(f"管工厂token缺少必填字段: {field}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Factory token missing required field: {field}"
                )

        # 计算MD5签名
        md5_string = f"{source_id}{jwt_config['secret_key']}{user_info['timestamp']}"
        expected_token = hashlib.md5(md5_string.encode('utf-8')).hexdigest()

        # 验证MD5签名
        if not hmac.compare_digest(token, expected_token):
            self.logger.error("管工厂MD5签名验证失败")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Factory MD5 signature verification failed"
            )

        self.logger.debug("管工厂MD5 token验证成功")

    def _verify_base64_hmac_token(self, token: str, secret_key: str) -> Dict[str, Any]:
        """验证Base64 + HMAC token（售后系统、设备控制器）

        按照文档格式：Base64(payload + "|" + signature)
        URL 传输可能破坏 Base64：+ 变空格；支持 URL-safe Base64（-/_）。
        """
        import base64
        import hmac
        import hashlib
        import json

        self.logger.debug("验证Base64 + HMAC token")

        try:
            # URL 传输中 + 可能变成空格，恢复；URL-safe Base64：- -> +, _ -> /
            token_normalized = (token or "").replace(" ", "+").replace("-", "+").replace("_", "/")
            # 补全 padding（长度为 4 的倍数）
            pad_len = (4 - len(token_normalized) % 4) % 4
            token_normalized = token_normalized + "=" * pad_len

            decoded_bytes = None
            try:
                decoded_bytes = base64.b64decode(token_normalized)
            except Exception:
                # 尝试 URL-safe Base64（原始 token 可能含 -/_，未在上一行被替换时）
                token_urlsafe = (token or "").replace(" ", "+")
                pad_len_u = (4 - len(token_urlsafe) % 4) % 4
                token_urlsafe = token_urlsafe + "=" * pad_len_u
                decoded_bytes = base64.urlsafe_b64decode(token_urlsafe)

            decoded_str = decoded_bytes.decode("utf-8")

            # 按照文档格式解析：payload + "|" + signature
            if "|" not in decoded_str:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Token format error: expected 'payload|signature' format"
                )

            parts = decoded_str.split("|", 1)
            if len(parts) != 2:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Token format error: expected single '|' separator"
                )

            payload_str, signature = parts

            # 解析payload为JSON
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Token payload is not valid JSON"
                )

            # 验证HMAC签名
            expected_signature = hmac.new(
                secret_key.encode('utf-8'),
                payload_str.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()

            if not hmac.compare_digest(signature, expected_signature):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="HMAC signature verification failed"
                )

            return payload

        except Exception as e:
            if isinstance(e, HTTPException):
                raise
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Base64/HMAC token parsing failed: {str(e)}"
            )

    # 第三方平台 token 登录时若未透传 permission_level，默认视为"总部(1)"。
    DEFAULT_TOKEN_PERMISSION_LEVEL = 1

    def _resolve_token_permission_level(self, payload: Dict[str, Any], user_info: Dict[str, Any]) -> Any:
        for source_data in (payload, user_info):
            if not isinstance(source_data, dict):
                continue
            for field_name in ("permission_level", "permissionLevel"):
                raw_permission_level = source_data.get(field_name)
                if raw_permission_level not in (None, ""):
                    return raw_permission_level
        return self.DEFAULT_TOKEN_PERMISSION_LEVEL

    def _merge_permission_level(self, user_info_data: Dict[str, Any], raw_permission_level: Any) -> None:
        try:
            user_info_data.update(build_permission_metadata(raw_permission_level))
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            )

    def _extract_user_info_from_payload(self, payload: Dict[str, Any], user_info: Dict[str, Any], source: str) -> tuple:
        """从payload中提取用户信息，适配不同系统的字段结构"""
        if user_info is None:
            user_info = {}

        user_info_data = {}  # 重命名避免混淆

        if source == "aftersales":
            # 售后系统：loginId/userName 必填；deviceID 为可选独立字段，不 split，有则通过 ESB 补齐机型
            username = payload.get("loginId") or user_info.get("loginId")
            email = user_info.get("email")
            full_name = payload.get("userName") or user_info.get("userName")
            external_id = payload.get("loginId") or user_info.get("loginId")
            device_id = payload.get("deviceID") or user_info.get("deviceID")
            if device_id and not isinstance(device_id, str):
                device_id = str(device_id)
            if device_id:
                try:
                    result = device_scan_info_service.get_profile(device_id)
                    profile = result.get("profile") or {}
                    if profile.get("machine_model") and not user_info_data.get("machine_model"):
                        user_info_data["machine_model"] = profile["machine_model"]
                    if profile.get("product_line") and not user_info_data.get("product_line"):
                        user_info_data["product_line"] = profile["product_line"]
                except Exception as e:
                    self.logger.warning(f"ESB 设备信息查询失败(aftersales): {e}, device_id={device_id}")
            self._merge_permission_level(user_info_data, self._resolve_token_permission_level(payload, user_info))
            return username, email, full_name, external_id, user_info_data

        elif source == "factory":
            # 管工厂云字段映射
            username = payload.get("userId") or user_info.get("userId")
            email = payload.get("email") or user_info.get("email")
            full_name = payload.get("userName") or user_info.get("userName")
            external_id = payload.get("userId") or user_info.get("userId")
            self._merge_permission_level(user_info_data, self._resolve_token_permission_level(payload, user_info))

        elif source == "controller":
            # 设备控制器字段映射
            combines = payload.get("deviceID") or user_info.get("deviceID")
            email = user_info.get("email")  # 设备通常不提供email
            full_name = payload.get("deviceName") or user_info.get("deviceName")

            parts = combines.split("&") if combines else []
            device_id = parts[0] if len(parts) >= 1 else ""
            username = device_id
            external_id = device_id
            user_info_data = {}

            if len(parts) >= 2:
                user_info_data["controller_type"] = parts[1]
            if len(parts) >= 3:
                user_info_data["version"] = parts[2]
            if len(parts) >= 4:
                user_info_data["publish_date"] = parts[3]

            # 兼容前端额外透传字段
            if user_info.get("device_id"):
                user_info_data["device_id"] = user_info.get("device_id")
            if user_info.get("machine_model"):
                user_info_data["machine_model"] = user_info.get("machine_model")
            if user_info.get("product_line"):
                user_info_data["product_line"] = user_info.get("product_line")
            elif user_info.get("newipd"):
                user_info_data["product_line"] = user_info.get("newipd")

            # ESB 补齐机型信息
            if device_id:
                user_info_data["device_id"] = user_info_data.get("device_id") or device_id
                user_info_data["machine_number"] = device_id
                try:
                    result = device_scan_info_service.get_profile(device_id)
                    profile = result.get("profile") or {}
                    if profile.get("machine_model") and not user_info_data.get("machine_model"):
                        user_info_data["machine_model"] = profile["machine_model"]
                    if profile.get("product_line") and not user_info_data.get("product_line"):
                        user_info_data["product_line"] = profile["product_line"]
                except Exception as e:
                    self.logger.warning(f"ESB 设备信息查询失败: {e}, device_id={device_id}")

            self._merge_permission_level(user_info_data, self._resolve_token_permission_level(payload, user_info))
            return username, email, full_name, external_id, user_info_data
            
        else:
            # 默认JWT字段映射
            username = (payload.get("sub") or payload.get("username") or
                       payload.get("name") or user_info.get("username"))
            email = payload.get("email") or user_info.get("email")
            full_name = (payload.get("name") or payload.get("full_name") or
                        user_info.get("full_name"))
            external_id = payload.get("external_id") or user_info.get("external_id")
            user_info_data = dict(user_info or {})
            self._merge_permission_level(user_info_data, self._resolve_token_permission_level(payload, user_info))

        return username, email, full_name, external_id, user_info_data

    def login(self, username: str, password: str, ip_address: str = None, user_agent: str = None) -> Dict[str, Any]:
        """用户登录"""
        self.logger.info(f"用户登录尝试: {username} (IP: {ip_address or 'unknown'})")

        user = self.authenticate_user(username, password)
        if not user:
            self.logger.warning(f"用户登录失败: {username} (IP: {ip_address or 'unknown'})")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not user.get('is_active'):
            self.logger.warning(f"用户已被禁用，登录失败: {username}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Inactive user"
            )

        # 更新最后登录时间
        # 使用RepositoryFactory上下文管理器确保事务一致性
        with self.repository_factory.user_repository as user_repo:
            user_repo.update_last_login(user.get('id'))

        # Deactivate all existing sessions for this user
        with self.repository_factory.user_session_repository as session_repo:
            deactivated_count = session_repo.deactivate_user_sessions(user.get('id'))
            if deactivated_count > 0:
                self.logger.info(f"停用用户旧会话: {username}, count={deactivated_count}")

        # 创建会话
        session_id = str(uuid.uuid4())
        session_expires_at = datetime.now(pytz.UTC) + timedelta(minutes=self.access_token_expire_minutes)
        with self.repository_factory.user_session_repository as session_repo:
            session_repo.create_session({
                "session_id": session_id,
                "user_id": user.get('id'),
                "ip_address": ip_address,
                "user_agent": user_agent,
                "expires_at": session_expires_at
            })

        # 创建令牌 - include session_id in token
        access_token_expires = timedelta(minutes=self.access_token_expire_minutes)
        access_token = self.create_access_token(
            data={"sub": str(user.get('id')), "username": user.get('username'), "session_id": session_id},
            expires_delta=access_token_expires
        )
        refresh_token = self.create_refresh_token(user.get('id'), session_id)

        self.logger.info(f"用户登录成功: {username} (IP: {ip_address or 'unknown'})")
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": self.access_token_expire_minutes * 60,
            "session_id": session_id,
            "user": {
                "id": user.get('id'),
                "username": user.get('username'),
                "email": user.get('email'),
                "full_name": user.get('full_name'),
                "is_active": user.get('is_active'),
                "is_superuser": user.get('is_superuser'),
                "auth_type": user.get('auth_type'),
                "last_login_at": user.get('last_login_at'),
                "created_at": user.get('created_at'),
                "user_info": user.get('user_info'),
            }
        }

    def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """刷新访问令牌 - 自动修复并续期 MySQL 会话状态"""
        self.logger.debug("尝试刷新访问令牌")

        with self.repository_factory.refresh_token_repository as refresh_token_repo:
            # 1. 查找刷新令牌记录
            token_record = refresh_token_repo.get_refresh_token(refresh_token)
            if not token_record or token_record.is_revoked:
                self.logger.warning("刷新令牌无效或已撤销")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid refresh token"
                )

            # 2. 获取关联的会话 ID
            associated_session_id = token_record.session_id

        with self.repository_factory.user_repository as user_repo:
            user = user_repo.get_user_by_id(token_record.user_id)
            if not user or not user.is_active:
                self.logger.warning(f"用户不存在或已禁用: user_id={token_record.user_id}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="User not found or inactive"
                )

        # 3. 核心：查找并【重活】会话
        if associated_session_id:
            with self.repository_factory.user_session_repository as session_repo:
                session = session_repo.get_session(associated_session_id)
                if session:
                    # 会话存在且活跃，续期
                    session.expires_at = datetime.now(pytz.UTC) + timedelta(minutes=self.access_token_expire_minutes)
                    session_repo.session.flush()
                    self.logger.debug(f"会话已续期: session_id={associated_session_id}")
                else:
                    # 会话不存在或不活跃，尝试查找不活跃的会话记录
                    existing_session = session_repo.get_session_by_id(associated_session_id)
                    if existing_session:
                        # 会话存在但不活跃，重新激活并续期
                        existing_session.is_active = True
                        existing_session.expires_at = datetime.now(pytz.UTC) + timedelta(minutes=self.access_token_expire_minutes)
                        session_repo.session.flush()
                        self.logger.info(f"会话已重新激活并续期: session_id={associated_session_id}")
                    else:
                        # 会话记录被物理删除了，重建一个
                        self.logger.warning(f"会话不存在，重建会话: session_id={associated_session_id}")
                        session_repo.create_session({
                            "session_id": associated_session_id,
                            "user_id": token_record.user_id,
                            "is_active": True,
                            "expires_at": datetime.now(pytz.UTC) + timedelta(minutes=self.access_token_expire_minutes)
                        })

        # 4. 生成新的 Access Token，必须包含这个已激活的 session_id
        access_token_expires = timedelta(minutes=self.access_token_expire_minutes)
        access_token = self.create_access_token(
            data={
                "sub": str(user.id),
                "username": user.username,
                "session_id": associated_session_id  # 注入到 JWT 载荷中
            },
            expires_delta=access_token_expires
        )

        self.logger.info(f"访问令牌刷新成功: {user.username}, session_id={associated_session_id}")
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": self.access_token_expire_minutes * 60
        }

    def logout(self, refresh_token: str = None, session_id: str = None, user_id: int = None):
        """用户登出"""
        self.logger.info(f"用户登出: refresh_token={bool(refresh_token)}, session_id={bool(session_id)}, user_id={user_id}")

        # 使用RepositoryFactory上下文管理器确保事务一致性

        if refresh_token:
            with self.repository_factory.refresh_token_repository as refresh_token_repo:
                refresh_token_repo.revoke_refresh_token(refresh_token)

        if session_id:
            with self.repository_factory.user_session_repository as session_repo:
                session_repo.deactivate_session(session_id)

        if user_id:
            with self.repository_factory.refresh_token_repository as refresh_token_repo:
                refresh_token_repo.revoke_user_tokens(user_id)
            with self.repository_factory.user_session_repository as session_repo:
                session_repo.deactivate_user_sessions(user_id)

    def verify_token(self, token: str) -> Dict[str, Any]:
        """验证JWT令牌"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            user_id: str = payload.get("sub")
            token_type: str = payload.get("type")

            if user_id is None or token_type != "access":
                self.logger.warning(f"无效的令牌: user_id={user_id}, token_type={token_type}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token"
                )

            self.logger.debug(f"令牌验证成功: user_id={user_id}")
            return payload
        except jwt.PyJWTError as e:
            self.logger.debug(f"JWT令牌验证失败: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )

    def get_current_user(self, token: str) -> Dict[str, Any]:
        """根据令牌获取当前用户"""
        payload = self.verify_token(token)
        user_id = int(payload.get("sub"))
        session_id = payload.get("session_id")

        # 强制检查 session_id
        if not session_id:
            self.logger.error(f"令牌缺少session_id: user_id={user_id}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing session info"
            )

        with self.repository_factory.user_repository as user_repo:
            user = user_repo.get_user_by_id(user_id)

            if user is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="User not found"
                )

            if not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Inactive user"
                )

            # 在 session 内构建 current_user，确保 user_info 等字段从 DB 正确加载
            current_user = self._ensure_admin_highest_permission(user.to_dict())
            self.logger.debug(f"get_current_user: user_id={user_id}, username={user.username}")

        # 验证 session 必须存在且活跃
        with self.repository_factory.user_session_repository as session_repo:
            session = session_repo.get_session(session_id)

            if not session:
                self.logger.warning(f"会话不存在或已失效: user_id={user_id}, session_id={session_id}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Session not found or inactive"
                )

            # 检查 session 是否被停用 (is_active = False)
            if not session.is_active:
                self.logger.warning(f"会话已被停用: user_id={user_id}, session_id={session_id}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Session deactivated"
                )

            # Check session expiration
            # Ensure both datetimes are timezone-aware for comparison
            expires_at = session.expires_at
            if expires_at.tzinfo is None:
                # If expires_at is naive, assume it's UTC
                expires_at = expires_at.replace(tzinfo=pytz.UTC)

            if expires_at < datetime.now(pytz.UTC):
                self.logger.warning(f"会话已过期: user_id={user_id}, session_id={session_id}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Session expired"
                )

            self.logger.debug(f"会话验证成功: user_id={user_id}, session_id={session_id}")

        return current_user

    def create_user(
        self,
        username: str,
        password: str,
        email: str = None,
        full_name: str = None,
        permission_level: int = None,
    ) -> Dict[str, Any]:
        """创建新用户"""
        self.logger.info(f"创建新用户: {username}")

        # 使用RepositoryFactory上下文管理器确保事务一致性
        with self.repository_factory.user_repository as user_repo:

            # 检查用户名是否已存在
            if user_repo.get_user_by_username(username):
                self.logger.warning(f"用户名已存在: {username}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Username already registered"
                )

            # 检查邮箱是否已存在
            if email and user_repo.get_user_by_email(email):
                self.logger.warning(f"邮箱已存在: {email}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already registered"
                )

            # 创建用户
            user_data = {
                "username": username,
                "email": email,
                "hashed_password": self.get_password_hash(password),
                "full_name": full_name,
                "auth_type": "local",
                "is_active": True,
                "created_at": datetime.now(pytz.UTC)
            }

            effective_permission_level = permission_level
            if effective_permission_level is None and username == "admin":
                effective_permission_level = 1

            if effective_permission_level is not None:
                user_data["user_info"] = build_permission_metadata(effective_permission_level)
                if username == "admin":
                    user_data["user_info"]["role"] = "admin"

            if username == "admin":
                user_data["is_superuser"] = True

            new_user = user_repo.create_user(user_data)
            self.logger.info(f"用户创建成功: {username} (ID: {new_user.id})")
            return self._ensure_admin_highest_permission(new_user.to_dict())

    def sso_login(self, provider: str, external_id: str, user_info: Dict[str, Any],
                  ip_address: str = None, user_agent: str = None) -> Dict[str, Any]:
        """SSO登录 - 完整的登录流程"""
        # 认证SSO用户
        user = self.authenticate_sso_user(external_id, provider, user_info)

        # Deactivate all existing sessions for this user
        with self.repository_factory.user_session_repository as session_repo:
            deactivated_count = session_repo.deactivate_user_sessions(user.get('id'))
            if deactivated_count > 0:
                self.logger.info(f"停用用户旧会话 (SSO): {user.get('username')}, count={deactivated_count}")

        # 创建会话
        session_id = str(uuid.uuid4())
        session_expires_at = datetime.now(pytz.UTC) + timedelta(minutes=self.access_token_expire_minutes)
        with self.repository_factory.user_session_repository as session_repo:
            session_repo.create_session({
                "session_id": session_id,
                "user_id": user.get('id'),
                "ip_address": ip_address,
                "user_agent": user_agent,
                "expires_at": session_expires_at
            })

        # 创建令牌 - include session_id in token
        access_token_expires = timedelta(minutes=self.access_token_expire_minutes)
        access_token = self.create_access_token(
            data={"sub": str(user.get('id')), "username": user.get('username'), "session_id": session_id},
            expires_delta=access_token_expires
        )
        refresh_token = self.create_refresh_token(user.get('id'), session_id)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": self.access_token_expire_minutes * 60,
            "session_id": session_id,
            "user": {
                "id": user.get('id'),
                "username": user.get('username'),
                "email": user.get('email'),
                "full_name": user.get('full_name'),
                "is_active": user.get('is_active'),
                "is_superuser": user.get('is_superuser'),
                "auth_type": user.get('auth_type'),
                "last_login_at": user.get('last_login_at'),
                "created_at": user.get('created_at')
            }
        }

    def token_login(self, token: str, user_info: Dict[str, Any] = None, source: str = None,
                    ip_address: str = None, user_agent: str = None) -> Dict[str, Any]:
        """Token传递登录 - 完整的登录流程"""
        if not user_info:
            user_info = {}

        # 认证token用户，传递source参数
        user = self.authenticate_token_user(token, user_info, source)

        # Deactivate all existing sessions for this user
        with self.repository_factory.user_session_repository as session_repo:
            deactivated_count = session_repo.deactivate_user_sessions(user.get('id'))
            if deactivated_count > 0:
                self.logger.info(f"停用用户旧会话 (Token): {user.get('username')}, count={deactivated_count}")

        # 创建会话
        session_id = str(uuid.uuid4())
        session_expires_at = datetime.now(pytz.UTC) + timedelta(minutes=self.access_token_expire_minutes)
        with self.repository_factory.user_session_repository as session_repo:
            session_repo.create_session({
                "session_id": session_id,
                "user_id": user.get('id'),
                "ip_address": ip_address,
                "user_agent": user_agent,
                "expires_at": session_expires_at
            })

        # 创建令牌 - include session_id in token
        access_token_expires = timedelta(minutes=self.access_token_expire_minutes)
        token_payload = {"sub": str(user.get('id')), "username": user.get('username'), "session_id": session_id}
        access_token = self.create_access_token(
            data=token_payload,
            expires_delta=access_token_expires
        )
        refresh_token = self.create_refresh_token(user.get('id'), session_id)

        response_user = {
            "id": user.get('id'),
            "username": user.get('username'),
            "email": user.get('email'),
            "full_name": user.get('full_name'),
            "is_active": user.get('is_active'),
            "is_superuser": user.get('is_superuser'),
            "auth_type": user.get('auth_type'),
            "last_login_at": user.get('last_login_at'),
            "created_at": user.get('created_at'),
            "user_info": user.get('user_info'),  # 含 device_id、machine_number 等，供前端展示与检索使用
        }
        self.logger.info(f"Token登录成功: user_id={user.get('id')}, username={user.get('username')}")
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": self.access_token_expire_minutes * 60,
            "session_id": session_id,
            "user": response_user,
        }


# 创建全局单例实例
auth_service = AuthService()
