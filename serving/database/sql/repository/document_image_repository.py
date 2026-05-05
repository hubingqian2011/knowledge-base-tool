from sqlalchemy.orm import Session
import uuid

from ..model.document_image import DocumentImage
from .base_repository import BaseRepository
from util.logging.logger import get_logger

logger = get_logger(__name__)


class DocumentImageRepository(BaseRepository):
    """通用文档图片映射仓库。"""

    def __init__(self, session: Session = None):
        super().__init__(DocumentImage, session)

    def add_document_image(self, document_id: str, image_id: str, sort_order: int):
        try:
            record = DocumentImage(
                id=str(uuid.uuid4()),
                document_id=document_id,
                image_id=image_id,
                sort_order=sort_order,
            )
            self.save(record)
            logger.debug(
                "添加文档图片映射成功: document_id=%s, image_id=%s, sort_order=%s",
                document_id,
                image_id,
                sort_order,
            )
            return record
        except Exception as e:
            logger.error(
                "添加文档图片映射失败: document_id=%s, image_id=%s, sort_order=%s, error=%s",
                document_id,
                image_id,
                sort_order,
                str(e),
            )
            raise

    def get_images_by_document_id(self, document_id: str):
        try:
            results = self.find_by_criteria({"document_id": document_id})
            return sorted(results, key=lambda x: x.sort_order)
        except Exception as e:
            logger.error(
                "根据文档ID获取图片映射失败: document_id=%s, error=%s",
                document_id,
                str(e),
            )
            return []

    def delete_by_document_id(self, document_id: str) -> int:
        try:
            records = self.find_by_criteria({"document_id": document_id})
            count = 0
            for record in records:
                self.session.delete(record)
                count += 1
            self.session.flush()
            return count
        except Exception as e:
            logger.error(
                "删除文档图片映射失败: document_id=%s, error=%s",
                document_id,
                str(e),
            )
            raise
