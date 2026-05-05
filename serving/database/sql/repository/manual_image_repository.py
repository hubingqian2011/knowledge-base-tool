from sqlalchemy.orm import Session
from ..model.manual_image import ManualImage
from ..model.image_file import ImageFile
from .base_repository import BaseRepository
from util.logging.logger import get_logger
import uuid

logger = get_logger(__name__)

class ManualImageRepository(BaseRepository):
    """手动图片仓库类，继承BaseRepository并提供特定的手动图片管理功能"""

    def __init__(self, session: Session = None):
        """初始化手动图片仓库

        Args:
            session: 数据库会话
        """
        super().__init__(ManualImage, session)

    def add_manual_image(self, document_id: str, image_id: str, page_num: int):
        """添加手动图片记录"""
        try:
            record = ManualImage(id=str(uuid.uuid4()), document_id=document_id, image_id=image_id, page_num=page_num)
            self.save(record)
            logger.debug(f"添加手动图片记录成功: document_id={document_id}, image_id={image_id}, page_num={page_num}")
            return record
        except Exception as e:
            logger.error(f"添加手动图片记录失败: document_id={document_id}, image_id={image_id}, page_num={page_num}, error={str(e)}")
            raise

    def get_images_by_document_id(self, document_id: str):
        """根据文档ID获取所有图片记录（按页码排序）"""
        try:
            criteria = {'document_id': document_id}
            # 先查询，再手动排序
            results = self.find_by_criteria(criteria)
            sorted_results = sorted(results, key=lambda x: x.page_num)
            logger.debug(f"根据文档ID获取图片记录成功: document_id={document_id}, count={len(sorted_results)}")
            return sorted_results
        except Exception as e:
            logger.error(f"根据文档ID获取图片记录失败: document_id={document_id}, error={str(e)}")
            return []

    def find_by_document_id(self, document_id: str):
        """根据文档ID查找图片记录（别名方法）"""
        return self.get_images_by_document_id(document_id)

    def get_images_by_page_range_global(self, start_page: int, end_page: int):
        """按页码范围查图片（不限 document_id），JOIN image_file 确认图片真实存在，每页只返回一张"""
        try:
            results = (
                self.session.query(ManualImage)
                .join(ImageFile, ManualImage.image_id == ImageFile.id)
                .filter(ManualImage.page_num >= start_page, ManualImage.page_num <= end_page)
                .order_by(ManualImage.page_num)
                .all()
            )
            seen_pages = set()
            deduped = []
            for r in results:
                if r.page_num not in seen_pages:
                    seen_pages.add(r.page_num)
                    deduped.append(r)
            return deduped
        except Exception as e:
            logger.error(f"按页码范围查图片失败: {start_page}-{end_page}, error={str(e)}")
            return []

    def get_images_by_document_page_range(self, document_id: str, start_page: int, end_page: int):
        """按 document_id 和原始 PDF 页码范围查图片，JOIN image_file 确认图片记录存在。"""
        try:
            return (
                self.session.query(ManualImage)
                .join(ImageFile, ManualImage.image_id == ImageFile.id)
                .filter(
                    ManualImage.document_id == document_id,
                    ManualImage.page_num >= start_page,
                    ManualImage.page_num <= end_page,
                )
                .order_by(ManualImage.page_num)
                .all()
            )
        except Exception as e:
            logger.error(
                f"按文档和页码范围查图片失败: document_id={document_id}, page={start_page}-{end_page}, error={str(e)}"
            )
            return []

    def get_images_by_page_range(self, document_id: str, start_page: int, end_page: int):
        """根据文档ID和页码范围获取图片记录"""
        try:
            criteria = {'document_id': document_id}
            results = self.find_by_criteria(criteria)
            # 过滤页码范围并排序
            filtered_results = [r for r in results if start_page <= r.page_num <= end_page]
            sorted_results = sorted(filtered_results, key=lambda x: x.page_num)
            logger.debug(f"根据页码范围获取图片记录成功: document_id={document_id}, start_page={start_page}, end_page={end_page}, count={len(sorted_results)}")
            return sorted_results
        except Exception as e:
            logger.error(f"根据页码范围获取图片记录失败: document_id={document_id}, start_page={start_page}, end_page={end_page}, error={str(e)}")
            return [] 
