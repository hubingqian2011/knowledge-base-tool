from sqlalchemy import Column, String, Integer, ForeignKey

from .database_manager import Base


class DocumentImage(Base):
    __tablename__ = "document_image"

    id = Column(String(36), primary_key=True, index=True)
    document_id = Column(String(64), nullable=False, index=True)
    image_id = Column(String(36), ForeignKey("image_file.id"), nullable=False)
    sort_order = Column(Integer, nullable=False)
