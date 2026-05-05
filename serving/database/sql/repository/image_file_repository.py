from sqlalchemy.orm import Session
from ..model.image_file import ImageFile
from .base_repository import BaseRepository
from util.logging.logger import get_logger

logger = get_logger(__name__)

class ImageFileRepository(BaseRepository):
    """图片文件仓库类，继承BaseRepository并提供特定的图片文件管理功能"""

    def __init__(self, session: Session = None):
        """初始化图片文件仓库

        Args:
            session: 数据库会话
        """
        super().__init__(ImageFile, session)

    def create_image_file(self, id: str, filename: str, filepath: str):
        """创建图片文件记录"""
        try:
            image_file = ImageFile(id=id, filename=filename, filepath=filepath)
            self.save(image_file)
            logger.debug(f"创建图片文件记录成功: id={id}, filename={filename}")
            return image_file
        except Exception as e:
            logger.error(f"创建图片文件记录失败: id={id}, filename={filename}, error={str(e)}")
            raise

    def get_image_file(self, id: str):
        """根据ID获取图片文件记录"""
        return self.find_by_id(id)

    def get_image_file_by_filename(self, filename: str):
        """根据文件名获取图片文件记录"""
        try:
            criteria = {'filename': filename}
            results = self.find_by_criteria(criteria)
            if results:
                logger.debug(f"根据文件名获取图片文件记录成功: filename={filename}")
                return results[0]
            logger.debug(f"未找到图片文件记录: filename={filename}")
            return None
        except Exception as e:
            logger.error(f"根据文件名获取图片文件记录失败: filename={filename}, error={str(e)}")
            return None

    def find_by_filename(self, filename: str):
        """根据文件名查找图片文件（别名方法）"""
        return self.get_image_file_by_filename(filename) 