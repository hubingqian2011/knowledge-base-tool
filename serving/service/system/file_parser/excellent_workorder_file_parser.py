from typing import Optional, BinaryIO
import pandas as pd
import json
from datetime import datetime

from .base_file_parser import BaseFileParser
from util.logging.logger import get_logger

logger = get_logger(__name__)

class ExcellentWorkorderFileParser(BaseFileParser):
    """
    优秀工单解析器：专门处理优秀工单Excel文件
    """
    def parse(self, file: BinaryIO, filename: str):
        try:
            # 读取Excel内容
            df = pd.read_excel(file)

            texts = []
            metadatas = []

            def convert_meta(meta):
                for k, v in meta.items():
                    if isinstance(v, float) and pd.isna(v):
                        meta[k] = None
                    elif isinstance(v, pd.Timestamp):
                        meta[k] = v.isoformat()
                    elif hasattr(v, 'item'):
                        meta[k] = v.item()
                    elif isinstance(v, (dict, list)):
                        meta[k] = convert_meta(v) if isinstance(v, dict) else [
                            convert_meta(i) if isinstance(i, dict) else i for i in v
                        ]
                    else:
                        try:
                            json.dumps(v)
                        except TypeError:
                            meta[k] = str(v)
                return meta

            for idx, row in df.iterrows():
                row_dict = row.to_dict()
                # 构造文本内容
                text = f"故障描述: {row_dict.get('故障描述', '')}\n解决方案: {row_dict.get('解决方案', '')}\n处理建议: {row_dict.get('处理建议', '')}".replace("\xa0", "")
                # 提取元数据
                meta = {k: v for k, v in row_dict.items() if k not in ['故障描述', '解决方案', '处理建议']}
                meta = convert_meta(meta)
                meta["import_time"] = datetime.now().isoformat()
                
                texts.append(text)
                metadatas.append(meta)

            logger.info(f"优秀工单解析成功，共解析{len(texts)}条记录")
            return {"texts": texts, "metadatas": metadatas, "success": True}
        except Exception as e:
            logger.error(f"优秀工单解析失败: {str(e)}")
            return {"success": False, "msg": f"优秀工单解析失败: {e}"}