from .workorder_file_parser import WorkorderFileParser
from .manual_file_parser import ManualFileParser
from .parameter_file_parser import ParameterFileParser
from .principle_file_parser import PrincipleFileParser
from .excellent_workorder_file_parser import ExcellentWorkorderFileParser
from .semantic_pdf_parser import SemanticPDFParser
from config.config import COLLECTION_MANUAL, COLLECTION_PARAMETER
from .base_file_parser import BaseFileParser

class FileParserFactory:
    @staticmethod
    def get_parser(collection_name: str):
        if collection_name == "workorder":
            return WorkorderFileParser()
        elif collection_name == COLLECTION_MANUAL:
            return ManualFileParser()
        elif collection_name == COLLECTION_PARAMETER:
            return ParameterFileParser()
        elif collection_name == "principle":
            return PrincipleFileParser()
        elif collection_name == "excellent_workorder":
            return ExcellentWorkorderFileParser()
        elif collection_name == "semantic_pdf":
            return SemanticPDFParser()
        # 可扩展更多collection的判断
        return BaseFileParser()
