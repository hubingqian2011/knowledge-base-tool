#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
设备控制器Token生成工具

用于生成符合系统认证要求的设备控制器token
"""

import json
import hmac
import hashlib
import base64
import time
from typing import Dict, Any
from config.config import JWT_PROVIDER_CONFIGS


class ControllerTokenGenerator:
    """设备控制器Token生成器"""

    def __init__(self, secret_key: str = None):
        """
        初始化Token生成器

        Args:
            secret_key: HMAC签名密钥，如不提供则使用config.py中的controller密钥
        """
        # 优先使用传入的密钥，否则使用config.py中controller的密钥
        if secret_key:
            self.secret_key = secret_key
        else:
            # 从config.py读取controller配置的密钥
            self.secret_key = JWT_PROVIDER_CONFIGS["controller"]["secret_key"]

    def generate_device_info(self, device_id: str, device_name: str = None,
                           controller_type: str = None, version: str = None,
                           publish_date: str = None, additional_info: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        生成设备信息

        Args:
            device_id: 设备ID（组合设备编号，如 "HT-ZSJ-A001"）
            device_name: 设备名称
            controller_type: 控制器类型（如 "TM"）
            version: 版本号（如 "Q7031"）
            publish_date: 发布日期（如 "24AV"）
            additional_info: 额外信息

        Returns:
            设备信息字典
        """
        timestamp = int(time.time() * 1000)  # 毫秒时间戳

        # 构建combines字段，格式为 "device_id&controller_type&version&publish_date"
        combines_parts = [device_id]
        if controller_type:
            combines_parts.append(controller_type)
        if version:
            combines_parts.append(version)
        if publish_date:
            combines_parts.append(publish_date)

        combines = "&".join(combines_parts)

        device_info = {
            "deviceID": combines,  # 注意：这里使用大写的 deviceID 以匹配 auth_service.py
            "deviceName": device_name or f"设备_{device_id}",
            "timestamp": timestamp,
            "source": "controller"
        }

        # 添加额外信息
        if additional_info:
            device_info.update(additional_info)

        return device_info

    def generate_signature(self, payload_str: str) -> str:
        """
        生成HMAC-SHA256签名

        Args:
            payload_str: 待签名的payload字符串

        Returns:
            十六进制签名字符串
        """
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            payload_str.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return signature

    def generate_token(self, device_info: Dict[str, Any]) -> str:
        """
        生成认证Token

        按照文档说明的格式生成token：Base64(payload + "|" + signature)

        Args:
            device_info: 设备信息字典

        Returns:
            Base64编码的token字符串
        """
        # 复制device_info，避免修改原始数据
        payload = device_info.copy()

        # 生成规范化的payload字符串（用于签名和编码）
        payload_str = json.dumps(payload, separators=(',', ':'), sort_keys=True)

        # 生成签名
        signature = self.generate_signature(payload_str)

        # 按照文档格式组合：payload + "|" + signature
        token_data = f"{payload_str}|{signature}"

        # Base64编码
        token_bytes = token_data.encode('utf-8')
        token = base64.b64encode(token_bytes).decode('utf-8')

        return token

    def generate_complete_login_data(self, device_id: str, device_name: str = None,
                                  controller_type: str = None, version: str = None,
                                  publish_date: str = None, additional_info: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        生成完整的登录数据

        Args:
            device_id: 设备ID（组合设备编号，如 "HT-ZSJ-A001"）
            device_name: 设备名称
            controller_type: 控制器类型（如 "TM"）
            version: 版本号（如 "Q7031"）
            publish_date: 发布日期（如 "24AV"）
            additional_info: 额外信息

        Returns:
            包含token和user_info的登录数据字典
        """
        # 生成设备信息
        device_info = self.generate_device_info(device_id, device_name, controller_type, version, publish_date, additional_info)

        # 生成token
        token = self.generate_token(device_info)

        # 准备user_info（用于token_login接口）
        user_info = {
            "deviceID": device_info["deviceID"],  # 注意：这里使用大写的 deviceID
            "deviceName": device_info["deviceName"],
            "source": "controller"
        }

        # 添加额外信息到user_info
        if additional_info:
            user_info.update(additional_info)

        return {
            "source": "controller",
            "token": token,
            "user_info": user_info,
            "device_info": device_info
        }


def main():
    """主函数 - 示例用法"""
    print("设备控制器Token生成工具")
    print("=" * 50)

    # 创建Token生成器
    generator = ControllerTokenGenerator()

    # 示例1：基本用法
    print("\n1. 基本示例：")
    login_data = generator.generate_complete_login_data(
        device_id="CTRL001",
        device_name="主控制器"
    )

    print(f"Source: {login_data['source']}")
    print(f"Token: {login_data['token']}")
    print(f"User Info: {json.dumps(login_data['user_info'], ensure_ascii=False, indent=2)}")
    print(f"最终用户名: {login_data['user_info']['deviceID'].split('&')[0]}")

    # 示例2：完整的combines格式示例
    print("\n2. 完整的combines格式示例：")
    login_data2 = generator.generate_complete_login_data(
        device_id="HT-ZSJ-A001",
        device_name="PLC控制器",
        controller_type="TM",
        version="Q7031",
        publish_date="24AV"
    )

    print(f"Source: {login_data2['source']}")
    print(f"Token: {login_data2['token']}")
    print(f"User Info: {json.dumps(login_data2['user_info'], ensure_ascii=False, indent=2)}")
    print(f"Combines: {login_data2['user_info']['deviceID']}")
    print(f"最终用户名: {login_data2['user_info']['deviceID'].split('&')[0]}")

    # 示例3：带额外信息
    print("\n3. 带额外信息的示例：")
    login_data3 = generator.generate_complete_login_data(
        device_id="CTRL002",
        device_name="备用控制器",
        controller_type="PLC",
        version="2.1.0",
        additional_info={
            "location": "车间A",
            "type": "PLC",
            "version": "2.1.0"
        }
    )

    print(f"Source: {login_data3['source']}")
    print(f"Token: {login_data3['token']}")
    print(f"User Info: {json.dumps(login_data3['user_info'], ensure_ascii=False, indent=2)}")

    # 示例4：生成多个设备的token
    print("\n4. 批量生成示例：")
    devices = [
        {"device_id": "DEV001", "device_name": "温度传感器", "controller_type": "TM"},
        {"device_id": "DEV002", "device_name": "压力传感器", "controller_type": "TM"},
        {"device_id": "DEV003", "device_name": "流量控制器", "controller_type": "PLC"}
    ]

    for device in devices:
        login_data = generator.generate_complete_login_data(
            device_id=device["device_id"],
            device_name=device["device_name"],
            controller_type=device["controller_type"]
        )
        print(f"\n设备: {device['device_name']}")
        print(f"Combines: {login_data['user_info']['deviceID']}")
        print(f"最终用户名: {login_data['user_info']['deviceID'].split('&')[0]}")

    print("\n" + "=" * 50)
    print("Token生成完成！")
    print("\n使用说明：")
    print("1. 使用生成的token和user_info调用 /auth/token_login 接口")
    print("2. source参数设置为 'controller'")
    print("3. token格式：Base64(payload + '|' + signature)")
    print("4. payload是JSON格式的设备信息，包含deviceID、deviceName、timestamp等字段")
    print("5. signature是使用HMAC-SHA256对payload字符串计算的签名")
    print("\n重要变化：")
    print("- 例如：'HT-ZSJ-A001&TM&Q7031&24AV'")
    print("- 最终用户名将从combines的第一部分提取（如 'HT-ZSJ-A001'）")


if __name__ == "__main__":
    main()