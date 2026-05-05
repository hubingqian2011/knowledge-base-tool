# -*- coding: utf-8 -*-
"""
Pytest 配置文件
提供全局 fixtures 和测试配置
"""
import sys
import os
import uuid
import pytest
import pandas as pd
from pathlib import Path
from typing import Dict, Any, List

# 添加项目根目录到 Python 路径
backend_root = Path(__file__).parent.parent
sys.path.insert(0, str(backend_root))


def pytest_configure(config):
    """Pytest 配置钩子"""
    # 自定义标记
    config.addinivalue_line(
        "markers", "integration: 集成测试标记（需要数据库连接）"
    )
    config.addinivalue_line(
        "markers", "unit: 单元测试标记（不需要数据库连接）"
    )
    config.addinivalue_line(
        "markers", "slow: 慢速测试标记（执行时间较长）"
    )
    config.addinivalue_line(
        "markers", "knowledge: 知识库测试标记"
    )


# ==================== 全局 Fixtures ====================

@pytest.fixture(scope="session")
def test_collection_name():
    """测试用的集合名称"""
    return f"test_workorder_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
def test_metadata():
    """测试用的元数据"""
    return {
        "机型": "测试机型A",
        "系统": "测试系统",
        "明细": "测试工单",
        "测试标记": "test_data"
    }


@pytest.fixture(scope="function")
def sample_fault_data(test_metadata):
    """样本故障数据"""
    return {
        "fault_description": "发动机启动困难，需要多次尝试才能启动",
        "solution": "检查燃油系统，更换燃油滤清器，清洗喷油嘴",
        "metadata": {
            **test_metadata,
            "工单号": "TEST001",
            "创建时间": "2024-01-01"
        }
    }


@pytest.fixture(scope="function")
def sample_fault_data_2(test_metadata):
    """样本故障数据2（用于测试融合）"""
    return {
        "fault_description": "发动机启动困难，需多次尝试启动",  # 与上面相似
        "solution": "更换燃油滤清器并清洗喷油嘴",
        "metadata": {
            **test_metadata,
            "机型": "测试机型B",  # 不同机型，用于测试 applicable_models 融合
            "工单号": "TEST002",
            "创建时间": "2024-01-02"
        }
    }


@pytest.fixture(scope="function")
def sample_excel_file(tmp_path, sample_fault_data):
    """创建测试用的 Excel 文件"""
    file_path = tmp_path / "test_fault_data.xlsx"

    # 创建测试数据
    df = pd.DataFrame([
        {
            "故障描述": sample_fault_data["fault_description"],
            "解决方案": sample_fault_data["solution"],
            **sample_fault_data["metadata"]
        }
    ])

    df.to_excel(file_path, index=False)
    return str(file_path)


def create_test_excel_file(tmp_path, fault_data_list: List[Dict[str, Any]]) -> str:
    """
    创建包含多条故障数据的 Excel 文件

    Args:
        tmp_path: 临时目录路径
        fault_data_list: 故障数据列表

    Returns:
        str: Excel 文件路径
    """
    file_path = tmp_path / f"test_faults_{uuid.uuid4().hex[:8]}.xlsx"

    # 转换为 DataFrame
    rows = []
    for fault_data in fault_data_list:
        row = {
            "故障描述": fault_data["fault_description"],
            "解决方案": fault_data["solution"],
            **fault_data["metadata"]
        }
        rows.append(row)

    df = pd.DataFrame(rows)
    df.to_excel(file_path, index=False)

    return str(file_path)


@pytest.fixture
def multiple_fault_excel_file(tmp_path, sample_fault_data, sample_fault_data_2):
    """包含多条故障数据的 Excel 文件"""
    return create_test_excel_file(
        tmp_path,
        [sample_fault_data, sample_fault_data_2]
    )
