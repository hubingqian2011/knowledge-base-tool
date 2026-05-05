from .base_file_parser import BaseFileParser
import pandas as pd

from util.logging.logger import get_logger
logger = get_logger(__name__)


class ParameterFileParser(BaseFileParser):
    def parse(self, file, filename):
        """
        解析参数Excel文件并返回解析结果
        Args:
            file: 文件流对象
            filename: 文件名
            document_id: 文档ID（可选）
        Returns:
            dict: 解析结果，包含texts和metadatas
        """
        try:
            # 读取Excel内容
            df = pd.read_excel(file)
            df.columns = df.columns.astype(str)
            total_rows = len(df)
            logger.debug(f"[parameter_parser] 入口: filename={filename}, 读取行数={total_rows}")

            texts = []
            metadatas = []
            
            for index, row in df.iterrows():
                text = str(row.get("参数名称", ""))
                serial_no = row.get("序号", "")
                page_value = row.get("页码", "")
                if pd.isna(serial_no):
                    serial_no = ""
                if pd.isna(page_value):
                    page_value = ""
                metadata = {
                    "控制器型号": str(row.get("控制器型号", "")),
                    "版本": str(row.get("版本", "")),
                    "日期": str(row.get("日期", "")),
                    "序号": str(serial_no),
                    "页码": str(page_value),
                    "参数名称": text,
                    "type": "controller_parameter_navigation",
                    "url": f"controller://page/{page_value}",
                    "display_url": f"控制器页面 {page_value}"
                }
                if text:
                    texts.append(text)
                    metadatas.append(metadata)

            if not texts:
                logger.debug(f"[parameter_parser] 出口: filename={filename}, 有效行数=0, 跳过={total_rows}条")
                return {"success": False, "msg": "未从Excel文件中提取到任何有效文本。"}

            skipped = total_rows - len(texts)
            logger.debug(f"[parameter_parser] 出口: filename={filename}, 有效行数={len(texts)}, 跳过={skipped}条")
            return {"success": True, "texts": texts, "metadatas": metadatas}
        except Exception as e:
            return {"success": False, "msg": f"解析Excel数据时发生错误: {e}"}
