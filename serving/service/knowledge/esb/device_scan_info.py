# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Dict

import httpx

from config.config import (
    ESB_BASE_URL, ESB_EXECUTE_PATH, ESB_APPKEY,
    ESB_USERNAME, ESB_PASSWORD, ESB_CREDENTIAL_ENCRYPTION,
    ESB_SECRET_KEY, ESB_TIMEOUT_SECONDS,
)

from util.logging.logger import get_logger

logger = get_logger(__name__)


class ESBError(Exception):
    """ESB 服务调用异常"""
    def __init__(self, message: str, code: str = None, details: dict = None):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(self.message)


def _hash_text(algorithm: str, text: str) -> str:
    algo = (algorithm or "").strip().upper()
    data = (text or "").encode("utf-8")
    if algo == "MD5":
        return hashlib.md5(data).hexdigest().upper()
    if algo == "SHA1":
        return hashlib.sha1(data).hexdigest().upper()
    raise ValueError(f"不支持的加密算法: {algorithm!r}（仅支持 MD5/SHA1）")


def _sign_hmac_md5(params: Dict[str, Any], secret: str) -> str:
    """
    按 ESB 文档的签名规则:
    1) 排除 sign 本身
    2) key 按 ASCII 升序
    3) 拼接 key + value（空值跳过）
    4) HMAC_MD5(secret, 拼接结果) -> HEX(大写)
    """
    secret_bytes = (secret or "").encode("utf-8")
    keys = sorted([k for k in params.keys() if k and k != "sign"])
    query = []
    for key in keys:
        value = params.get(key)
        if value is None:
            continue
        value_str = str(value)
        if not value_str:
            continue
        query.append(f"{key}{value_str}")
    payload = "".join(query).encode("utf-8")
    digest = hmac.new(secret_bytes, payload, hashlib.md5).digest()
    return digest.hex().upper()


def _build_esb_params(machine_number: str, query_type: str) -> Dict[str, Any]:
    # 对齐服务器侧脚本 test_api.sh：默认走 https（很多环境下 http:80 会被拒绝）
    
    base_url = ESB_BASE_URL
    execute_path = ESB_EXECUTE_PATH
    timeout_s = ESB_TIMEOUT_SECONDS
    appkey = ESB_APPKEY
    eventkey = "DeviceScanInfo"
    timestamp = str(int(time.time() * 1000))
    username = ESB_USERNAME
    password = ESB_PASSWORD
    encryption = ESB_CREDENTIAL_ENCRYPTION
    secret = ESB_SECRET_KEY
    
    if encryption and (username or password):
        if username:
            username = _hash_text(encryption, username + timestamp)
        if password:
            password = _hash_text(encryption, password + timestamp)

    params_obj = {"machineNumber": machine_number, "queryType": query_type}
    params_str = json.dumps(params_obj, ensure_ascii=False, separators=(",", ":"))

    # 使用 application/x-www-form-urlencoded（等价于 curl --data-urlencode），避免超长 query 参数被网关/代理拦截
    form_fields: Dict[str, Any] = {
        "timestamp": timestamp,
        "format": "json",
        "eventkey": eventkey,
        "params": params_str,
    }
    if appkey:
        form_fields["appkey"] = appkey
    if username:
        form_fields["username"] = username
    if password:
        form_fields["password"] = password

    if secret:
        form_fields["sign"] = _sign_hmac_md5(form_fields, secret)

    return {
        "url": f"{base_url}{execute_path}",
        "timeout_s": timeout_s,
        "form_fields": form_fields,
    }


def fetch_device_scan_info(machine_number: str, query_type: str = "0") -> Dict[str, Any]:
    """
    同步调用 ESB 事件 DeviceScanInfo，返回原始 JSON。
    
    Raises:
        ESBError: ESB 调用失败（超时、HTTP 错误、网络错误等）
    """
    built = _build_esb_params(machine_number=machine_number, query_type=query_type)
    try:
        with httpx.Client(timeout=built["timeout_s"], trust_env=True) as client:
            resp = client.post(
                built["url"],
                data=built["form_fields"],
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException as e:
        raise ESBError(
            f"ESB 请求超时: machine_number={machine_number}",
            code="ESB_TIMEOUT",
            details={"machine_number": machine_number, "url": built["url"]}
        ) from e
    except httpx.HTTPStatusError as e:
        raise ESBError(
            f"ESB 返回错误状态: {e.response.status_code}",
            code="ESB_HTTP_ERROR",
            details={"status_code": e.response.status_code, "machine_number": machine_number}
        ) from e
    except httpx.RequestError as e:
        raise ESBError(
            f"ESB 网络请求失败: {str(e)}",
            code="ESB_NETWORK_ERROR",
            details={"machine_number": machine_number, "error": str(e)}
        ) from e
    except Exception as e:
        raise ESBError(
            f"ESB 未知错误: {str(e)}",
            code="ESB_UNKNOWN",
            details={"machine_number": machine_number, "error": str(e)}
        ) from e


async def fetch_device_scan_info_async(machine_number: str, query_type: str = "0") -> Dict[str, Any]:
    """
    异步调用 ESB 事件 DeviceScanInfo，返回原始 JSON。
    
    Raises:
        ESBError: ESB 调用失败（超时、HTTP 错误、网络错误等）
    """
    built = _build_esb_params(machine_number=machine_number, query_type=query_type)
    try:
        async with httpx.AsyncClient(timeout=built["timeout_s"], trust_env=True) as client:
            resp = await client.post(
                built["url"],
                data=built["form_fields"],
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException as e:
        raise ESBError(
            f"ESB 请求超时: machine_number={machine_number}",
            code="ESB_TIMEOUT",
            details={"machine_number": machine_number, "url": built["url"]}
        ) from e
    except httpx.HTTPStatusError as e:
        raise ESBError(
            f"ESB 返回错误状态: {e.response.status_code}",
            code="ESB_HTTP_ERROR",
            details={"status_code": e.response.status_code, "machine_number": machine_number}
        ) from e
    except httpx.RequestError as e:
        raise ESBError(
            f"ESB 网络请求失败: {str(e)}",
            code="ESB_NETWORK_ERROR",
            details={"machine_number": machine_number, "error": str(e)}
        ) from e
    except Exception as e:
        raise ESBError(
            f"ESB 未知错误: {str(e)}",
            code="ESB_UNKNOWN",
            details={"machine_number": machine_number, "error": str(e)}
        ) from e


class DeviceScanInfoService:
    """
    统一封装 DeviceScanInfo 的调用与字段映射，供 API / 业务调用。
    """

    def get_profile(self, machine_number: str, query_type: str = "0") -> Dict[str, Any]:
        resp = fetch_device_scan_info(machine_number=machine_number, query_type=query_type)
        return self._normalize(machine_number=machine_number, raw=resp)

    async def get_profile_async(self, machine_number: str, query_type: str = "0") -> Dict[str, Any]:
        resp = await fetch_device_scan_info_async(machine_number=machine_number, query_type=query_type)
        return self._normalize(machine_number=machine_number, raw=resp)

    def _normalize(self, machine_number: str, raw: Any) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            raise RuntimeError(f"DeviceScanInfo 返回格式异常: {type(raw).__name__}")

        # 文档示例 data 是对象；但实际网关/实现里也可能返回 data=[{...}]（列表包一层）
        data = raw.get("data")
        if isinstance(data, list):
            data = data[0] if data else None

        if not isinstance(data, dict):
            msg = raw.get("msg") or raw.get("message") or ""
            code = raw.get("code")
            raise RuntimeError(f"DeviceScanInfo 未返回可用 data（code={code}, msg={msg}）")

        machine_model = str(data.get("machineModel") or "").strip() or None
        # 新需求：仅需要 newipd（产品线），不再从 xipd/XIPD/generation 等衍生字段推导
        product_line = str(data.get("newipd") or "").strip() or None

        profile = {
            "device_id": machine_number,
            "machine_number": machine_number,
            "machine_model": machine_model,
            "product_line": product_line,
        }

        return {
            "profile": profile,
            "raw": raw,
        }


device_scan_info_service = DeviceScanInfoService()
