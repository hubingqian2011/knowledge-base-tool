# -*- coding: utf-8 -*-
import base64
from fastapi import HTTPException, Depends, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Optional

from service.auth.auth_service import auth_service
from config.config import ENABLE_AUTHENTICATION, AUTH_IP_WHITELIST
from typing import Dict, Any
from util.logging.logger import get_logger

logger = get_logger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """JWT认证中间件 - 可选的全局认证"""

    def __init__(self, app, exclude_paths: list = None):
        super().__init__(app)
        self.exclude_paths = exclude_paths or [
            "/auth/login",
            "/auth/register",
            "/auth/sso_login",
            "/auth/token_login",
            "/auth/refresh",
            "/auth/health",
            "/docs",
            "/redoc",
            "/openapi.json"
        ]

    def _get_client_ip(self, request: Request) -> str:
        """获取客户端真实IP地址，支持代理"""
        # 检查是否通过代理
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # X-Forwarded-For可能包含多个IP，取第一个
            return forwarded_for.split(",")[0].strip()

        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()

        # 直接连接，使用客户端地址
        if request.client:
            return request.client.host

        return ""

    def _is_ip_whitelisted(self, ip: str) -> bool:
        """检查IP是否在白名单中"""
        if not ip or not AUTH_IP_WHITELIST:
            return False
        return ip in AUTH_IP_WHITELIST

    async def dispatch(self, request: Request, call_next):
        """处理请求"""
        # 检查是否在排除路径中
        if request.url.path in self.exclude_paths or "file/image" in request.url.path:
            return await call_next(request)

        # 检查客户端IP是否在白名单中
        client_ip = self._get_client_ip(request)

        if self._is_ip_whitelisted(client_ip):
            # IP在白名单中，设置默认whitelist用户，跳过token验证
            logger.info(f"IP {client_ip} 在白名单中，自动使用whitelist用户")
            try:
                # 为白名单IP设置默认用户
                request.state.user = auth_service.authenticate_user("whitelist", "whitelist")
                logger.info(f"白名单IP {client_ip} 已设置为whitelist用户")
            except Exception as e:
                logger.error(f"为白名单IP设置用户失败: {str(e)}")
                request.state.user = None
            return await call_next(request)

        token_user = None

        # 首先尝试从Authorization头获取认证信息
        authorization = request.headers.get("Authorization")

        if authorization:
            # === 情况 A: 处理 JWT Bearer Token ===
            if authorization.startswith("Bearer "):
                token = authorization.split(" ")[1]
                if token:
                    try:
                        token_user = auth_service.get_current_user(token)
                    except HTTPException:
                        token_user = None

            # === 情况 B: 处理 Basic Auth (Swagger UI) ===
            elif authorization.startswith("Basic "):
                try:
                    # 解码: "Basic YWRtaW46YWRtaW4=" → "admin:admin"
                    base64_str = authorization.split(" ")[1]
                    decoded_bytes = base64.b64decode(base64_str)
                    decoded_str = decoded_bytes.decode('utf-8')
                    username, password = decoded_str.split(':', 1)

                    # 验证用户名密码
                    token_user = auth_service.authenticate_user(username, password)
                    if token_user:
                        logger.debug(f"Basic Auth 认证成功: {username}")
                except Exception as e:
                    logger.warning(f"Basic Auth 解析失败: {str(e)}")
                    token_user = None

        # 如果 Header 认证成功，直接使用
        if token_user:
            request.state.user = token_user
            return await call_next(request)

        # 如果Authorization头中没有认证信息，尝试从Cookie中获取access_token
        cookies = request.headers.get("Cookie")
        if cookies:
            # 解析cookies，查找access_token
            cookie_dict = {}
            for cookie in cookies.split(';'):
                if '=' in cookie:
                    key, value = cookie.strip().split('=', 1)
                    cookie_dict[key] = value

            if 'access_token' in cookie_dict:
                token = cookie_dict['access_token']
                try:
                    # 验证token并获取用户
                    user = auth_service.get_current_user(token)
                    request.state.user = user
                    return await call_next(request)
                except HTTPException:
                    # Token无效，继续处理
                    pass

        # 可选认证：如果没有token，继续处理请求但设置user为None
        request.state.user = None
        return await call_next(request)


# ==================== 依赖注入工具 ====================

def get_current_user(
    request: Request,
    jwt_token: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False)),
    basic_credentials: Optional[HTTPBasicCredentials] = Depends(HTTPBasic(auto_error=False))
) -> Dict[str, Any]:
    """获取当前用户 - 强制要求认证，支持JWT和Basic Auth两种认证方式"""
    # 优先检查中间件是否已设置用户（包括白名单IP的默认用户）
    if hasattr(request.state, 'user') and request.state.user is not None:
        return request.state.user

    # 如果认证被禁用，返回默认admin用户
    if not ENABLE_AUTHENTICATION:
        user = auth_service.ensure_default_admin()
        request.state.user = user
        return user

    if jwt_token:
        # 优先使用JWT认证（与 token_login / login 生成的 access_token 一致）
        user = auth_service.get_current_user(jwt_token.credentials)
        request.state.user = user  # 同步到 state，供直接读 request.state.user 的路由使用
        return user
    elif basic_credentials:
        user = auth_service.authenticate_user(basic_credentials.username, basic_credentials.password)
        if user:
            request.state.user = user
            return user
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    else:
        # 获取客户端IP用于日志
        client_ip = request.client.host if request.client else ""
        logger.warning(f"未提供认证凭据，客户端IP: {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_optional_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """获取当前用户 - 可选认证"""
    return getattr(request.state, 'user', None)


def require_superuser(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """要求超级用户权限"""
    if not current_user.get('is_superuser', False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    return current_user


def require_active_user(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """要求活跃用户"""
    if not current_user.get('is_active', False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )
    return current_user
