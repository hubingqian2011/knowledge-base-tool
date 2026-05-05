#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
excel_ingest_agent.py - Excel 工单入库

【设计】
继承 BaseIngestAgent，只实现 Excel 特有的解析逻辑。
通用的 5 步流程、批次原子性、三库一致性、错误分类、进度报告等
都由 BaseIngestAgent 统一处理。

【数据语义】
每一行 Excel = 1 条 Chunk = 1 条 record
content_columns 拼成 text；其他列作为 metadata。

【向后兼容】
保留 ingest_excel() 公开函数，签名与旧版完全一致：
  ingest_excel(
      file_path, content_columns, collection_name,
      knowledge_type='workorder', model='', embedding_dim=2048,
      progress_callback=None, **device_tags,
  ) -> dict

【CLI 用法】
  python excel_ingest_agent.py --file workorders.xlsx \
      --content-columns "故障描述,解决方案" \
      --collection-name workorder
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ── 路径设置 ──────────────────────────────────────────────────
_AGENT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _AGENT_DIR.parent
for _p in (_AGENT_DIR, _PROJECT_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pandas as pd
from dotenv import load_dotenv

load_dotenv(_AGENT_DIR / ".env")

from core.base_agent import BaseIngestAgent, Chunk, IngestResult
from core.errors import (
    FileNotFoundError_,
    InvalidContentError,
    ParseError,
)
from core.progress import (
    CallbackProgressReporter,
    NoOpProgressReporter,
    ProgressReporter,
)
from shared.config.settings import EMBEDDING_DIMENSION

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 默认值
# ════════════════════════════════════════════════════════════════

DEFAULT_CONTENT_COLUMNS = ["故障描述", "解决方案"]

# device_tags 标准字段（只接受这些，防止脏数据）
DEVICE_TAG_KEYS = ("series", "generation", "controller", "product_line", "tonnage")


# ════════════════════════════════════════════════════════════════
# Excel 工具函数（保留旧版完整逻辑）
# ════════════════════════════════════════════════════════════════

def _normalize_excel_metadata_value(val: Any) -> Any:
    """
    将 Excel 行值清洗为可安全写入三库的 Python 值。

    重点处理 pandas 的空值体系，避免 NaT / NaN 以字符串形式落到 ES，
    触发 date / numeric 字段解析异常。
    """
    try:
        if pd.isna(val):
            return None
    except Exception:
        pass

    if isinstance(val, pd.Timestamp):
        return val.isoformat()

    if hasattr(val, "item"):
        try:
            scalar_val = val.item()
            try:
                if pd.isna(scalar_val):
                    return None
            except Exception:
                pass
            return scalar_val
        except Exception:
            pass

    try:
        json.dumps(val)
        return val
    except (TypeError, ValueError):
        return str(val)


# ════════════════════════════════════════════════════════════════
# ExcelIngestAgent 主类
# ════════════════════════════════════════════════════════════════

class ExcelIngestAgent(BaseIngestAgent):
    """
    Excel 文件入库 Agent。

    使用方式：
        agent = ExcelIngestAgent(
            collection_name="workorder",
            knowledge_type="workorder",
            content_columns=["故障描述", "解决方案"],
        )
        result = agent.ingest("workorders.xlsx")
    """

    def __init__(
        self,
        collection_name: str,
        knowledge_type: str = "workorder",
        content_columns: Optional[List[str]] = None,
        embedding_dim: int = EMBEDDING_DIMENSION,
        model: str = "",
        device_tags: Optional[Dict[str, Any]] = None,
        progress_reporter: Optional[ProgressReporter] = None,
        file_id: Optional[str] = None,    # V2 新增
    ):
        super().__init__(
            collection_name=collection_name,
            knowledge_type=knowledge_type,
            embedding_dim=embedding_dim,
            progress_reporter=progress_reporter,
            file_id=file_id,              # V2 新增
        )
        self.content_columns = content_columns or list(DEFAULT_CONTENT_COLUMNS)
        self.model = model
        self.device_tags = device_tags or {}

    # ── 子类必须实现：解析 + 校验 ────────────────────────────

    def _parse_file(self, file_path: str, **kwargs) -> List[Chunk]:
        """
        Step 1: 用 pandas 读 Excel，每行转换为一个 Chunk。

        - text = "故障描述: ...\n解决方案: ..."
        - metadata = 其他所有列（normalized）
        - local_key = "row:{row_idx}"（1-based，用于确定性 ID）
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError_(
                f"Excel 文件不存在: {file_path}",
                context={"file_path": str(file_path)},
            )

        try:
            df = pd.read_excel(file_path)
        except Exception as e:
            raise ParseError(
                f"Excel 解析失败: {e}",
                context={"file_path": str(file_path)},
                cause=e,
            )

        if df.empty:
            raise InvalidContentError(
                "Excel 文件无任何内容",
                context={"file_path": str(file_path)},
            )

        missing_cols = [c for c in self.content_columns if c not in df.columns]
        if missing_cols:
            raise InvalidContentError(
                f"Excel 缺少必填列: {missing_cols}",
                context={
                    "file_path": str(file_path),
                    "missing_columns": missing_cols,
                    "available_columns": list(df.columns),
                },
            )

        # 内容列以外的全部当 metadata
        meta_columns = [c for c in df.columns if c not in self.content_columns]

        chunks = []
        for row_idx, (_, row) in enumerate(df.iterrows(), start=1):
            # 拼接 content（对齐旧版行为：col: val\ncol: val）
            parts = []
            for col in self.content_columns:
                val = row.get(col, "")
                try:
                    if pd.isna(val):
                        val = ""
                except Exception:
                    pass
                parts.append(f"{col}: {val}")
            text = "\n".join(parts).replace("\xa0", "").strip()

            # 构造 metadata
            metadata: Dict[str, Any] = {}
            for col in meta_columns:
                metadata[col] = _normalize_excel_metadata_value(row.get(col))

            # 加 model 字段（旧版有，保持一致）
            if self.model:
                metadata["model"] = self.model

            chunks.append(Chunk(
                text=text,
                metadata=metadata,
                local_key=f"row:{row_idx}",
            ))

        logger.info(
            f"[ExcelIngestAgent] 解析完成: {len(chunks)} 行 "
            f"(content_columns={self.content_columns})"
        )
        return chunks

    def _validate(self, chunks: List[Chunk]) -> List[Chunk]:
        """
        Step 2: 过滤 text 为空（或只含列名标签）的 chunk。

        Excel 可能有空白行，"故障描述: \n解决方案: " 这种 text 实际只有标签
        没有内容，需要过滤掉。
        """
        valid = []
        for chunk in chunks:
            content_only = chunk.text
            for col in self.content_columns:
                content_only = content_only.replace(f"{col}:", "")
            content_only = content_only.replace("\n", "").strip()

            if content_only:
                valid.append(chunk)

        skipped = len(chunks) - len(valid)
        if skipped > 0:
            logger.info(
                f"[ExcelIngestAgent] 校验过滤: 跳过 {skipped} 条空白行，"
                f"保留 {len(valid)}/{len(chunks)} 条"
            )
        return valid

    # ── 钩子方法：加 device_tags ──────────────────────────────

    def _enrich_metadata(self, chunks: List[Chunk], **kwargs) -> List[Chunk]:
        """
        Step 3 钩子：为每个 chunk 的 metadata 加 device_tags。

        device_tags 来自 admin 前端表单的下拉选择（机型/代次等）。
        """
        if not self.device_tags:
            return chunks

        for chunk in chunks:
            for tag_key, tag_value in self.device_tags.items():
                if tag_value not in (None, ""):
                    chunk.metadata[tag_key] = tag_value

        return chunks


# ════════════════════════════════════════════════════════════════
# 公开门面函数（向后兼容，签名 100% 保持旧版）
# ════════════════════════════════════════════════════════════════

def ingest_excel(
    file_path: str,
    content_columns: List[str],
    collection_name: str,
    knowledge_type: str = "workorder",
    model: str = "",
    embedding_dim: int = EMBEDDING_DIMENSION,
    progress_callback: Optional[Callable] = None,
    file_id: Optional[str] = None,    # V2 新增
    **device_tags: Any,
) -> Dict[str, Any]:
    """
    公开门面函数。签名 100% 兼容旧版。

    内部用 ExcelIngestAgent 完成实际工作，返回 IngestResult.to_dict() + 兼容字段。

    Args:
        file_path:        Excel 文件路径（.xlsx / .xls）
        content_columns:  内容列名列表（用于拼接 embedding 文本）
        collection_name:  Milvus / MongoDB / ES 的 collection / index 名称
        knowledge_type:   知识类型标签（默认 "workorder"）
        model:            机型名称（可选，会进 metadata）
        embedding_dim:    向量维度
        progress_callback: 进度回调 callback(step, name, detail, current, total)
        **device_tags:    机型扩展标签（series/generation/controller/product_line/tonnage）

    Returns:
        dict，包含 success/total_rows/valid_rows/success_rows/failed_rows/
              milvus/mongodb/es/collection_name/elapsed_seconds/errors/msg 等字段
    """
    # 包装进度回调
    reporter = (
        CallbackProgressReporter(progress_callback)
        if progress_callback is not None
        else NoOpProgressReporter()
    )

    # 只保留标准 device_tags 字段，过滤空值（防止脏数据）
    filtered_device_tags = {
        k: v for k, v in device_tags.items()
        if k in DEVICE_TAG_KEYS and v not in (None, "")
    }

    agent = ExcelIngestAgent(
        collection_name=collection_name,
        knowledge_type=knowledge_type,
        content_columns=content_columns,
        embedding_dim=embedding_dim,
        model=model,
        device_tags=filtered_device_tags,
        progress_reporter=reporter,
        file_id=file_id,              # V2 新增
    )

    result: IngestResult = agent.ingest(file_path)

    # to_dict() 已包含核心字段，补充旧版兼容字段
    report = result.to_dict()
    report["milvus"] = result.written_rows
    report["mongodb"] = result.written_rows
    report["es"] = result.written_rows
    report["success_rows"] = result.written_rows
    report["failed_rows"] = max(0, result.valid_rows - result.written_rows)

    return report


# ════════════════════════════════════════════════════════════════
# CLI 入口（保留旧版完整 CLI）
# ════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(
        description="Excel 结构化数据入库 Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python excel_ingest_agent.py \\
    --file input/workorders.xlsx \\
    --content-columns "故障描述,解决方案" \\
    --collection-name workorder_v2

  python excel_ingest_agent.py \\
    --file input/cases.xlsx \\
    --content-columns "故障描述,解决方案,处理建议" \\
    --collection-name excellent_workorder \\
    --knowledge-type excellent_workorder \\
    --model MA1600 --series MA --generation 三代
        """,
    )
    p.add_argument("--file", required=True, help="Excel 文件路径（.xlsx / .xls）")
    p.add_argument("--content-columns", required=True,
                   help="内容列名，逗号分隔（如 '故障描述,解决方案'）")
    p.add_argument("--collection-name", required=True,
                   help="Milvus/MongoDB/ES 的 collection 名称")
    p.add_argument("--knowledge-type", default="workorder",
                   help="知识类型标签（默认 workorder）")
    p.add_argument("--model", default="", help="机型名称（可选）")
    p.add_argument("--series", default=None, help="机型系列（可选）")
    p.add_argument("--generation", default=None, help="代次（可选）")
    p.add_argument("--controller", default=None, help="控制器型号（可选）")
    p.add_argument("--product-line", default=None, help="产品线（可选）")
    p.add_argument("--tonnage", default=None, help="锁模力/吨（可选）")
    args = p.parse_args()

    content_columns = [c.strip() for c in args.content_columns.split(",") if c.strip()]
    if not content_columns:
        p.error("--content-columns 不能为空")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    result = ingest_excel(
        file_path=args.file,
        content_columns=content_columns,
        collection_name=args.collection_name,
        knowledge_type=args.knowledge_type,
        model=args.model,
        series=args.series,
        generation=args.generation,
        controller=args.controller,
        product_line=args.product_line,
        tonnage=args.tonnage,
    )

    print(f"\n{'=' * 50}")
    print(f"Excel 入库完成 | {args.file}")
    print(f"{'=' * 50}")
    for key, value in result.items():
        if key == "errors" and not value:
            continue
        print(f"  {key}: {value}")

    if not result.get("success"):
        sys.exit(1)


# ════════════════════════════════════════════════════════════════
# 公共导出
# ════════════════════════════════════════════════════════════════

__all__ = [
    "ExcelIngestAgent",
    "ingest_excel",
    "DEFAULT_CONTENT_COLUMNS",
    "DEVICE_TAG_KEYS",
]


if __name__ == "__main__":
    main()
