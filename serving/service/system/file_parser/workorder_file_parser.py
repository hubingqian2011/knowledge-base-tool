from typing import Optional, BinaryIO

from .base_file_parser import BaseFileParser
from service.system.file_reader_main import file_reader
import pandas as pd
import json

class WorkorderFileParser(BaseFileParser):
    def parse(self, file: BinaryIO, filename: str):
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
            text = f"故障描述: {row_dict.get('故障描述', '')}\n解决方案: {row_dict.get('解决方案', '')}".replace("\xa0", "")
            texts.append(text)
            meta = {k: v for k, v in row_dict.items() if k not in ['故障描述', '解决方案']}
            meta = convert_meta(meta)
            metadatas.append(meta)

        return {"success": True, "texts": texts, "metadatas": metadatas}
    
    # def _save_file(self, file: BinaryIO, filename: str) -> Optional[str]:
    #     return file_reader.save_file(file, filename)

    # def _read_file(self, file_path: str) -> Optional[str]:
    #     return file_reader.read_file(file_path=file_path)
    
    # def _llm_parse_content(self, content: str) -> Optional[dict]:
    #     pass

    # def _save_to_knowledge_base(self, knowledges: dict) -> bool:
    #     pass

    # def _save_to_knowledge_graph(self, knowledges: dict) -> bool:
    #     pass
