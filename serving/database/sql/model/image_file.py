from sqlalchemy import Column, String, DateTime
from .database_manager import Base
import datetime

class ImageFile(Base):
    __tablename__ = "image_file"

    id = Column(String(36), primary_key=True, index=True)  # uuid
    filename = Column(String(255), nullable=False)
    filepath = Column(String(512), nullable=False)
    upload_time = Column(DateTime, default=datetime.datetime.utcnow) 