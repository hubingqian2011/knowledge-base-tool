from sqlalchemy import Column, String, Integer, ForeignKey
from .database_manager import Base

class ManualImage(Base):
    __tablename__ = "manual_image"

    id = Column(String(36), primary_key=True, index=True)  # uuid
    document_id = Column(String(64), nullable=False, index=True)
    image_id = Column(String(36), ForeignKey('image_file.id'), nullable=False)
    page_num = Column(Integer, nullable=False) 