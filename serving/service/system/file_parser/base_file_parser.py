from abc import ABC, abstractmethod

class BaseFileParser(ABC):
    @abstractmethod
    def parse(self, file, filename):
        """
        解析文件并返回处理结果
        """
        pass 