# -*- coding: utf-8 -*-
from typing import Optional, BinaryIO
import os
import shutil
from pathlib import Path
import uuid
import io
import mimetypes

from util.logging.logger import get_logger
from .file_reader import (
    pdf_reader,
    docx_reader,
    doc_reader,
    excel_reader,
    pptx_reader,
    csv_reader,
    text_reader
)
from database.sql.repository.repository_factory import RepositoryFactory

logger = get_logger(__name__)

class FileReader:
    """文件读取器主类，负责根据文件类型分发读取任务"""
    _instance = None
    
    # 文件类型映射表
    _file_type_mapping = {
        '.pdf': pdf_reader,
        '.docx': docx_reader,
        '.doc': doc_reader,
        '.xlsx': excel_reader,
        '.xls': excel_reader,
        '.pptx': pptx_reader,
        '.csv': csv_reader,
        '.json': text_reader
    }
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        """初始化文件读取类"""
        # 创建文件保存目录
        self.save_dir = Path("temp_files")
        self.save_dir.mkdir(exist_ok=True)
        self.repository_factory = RepositoryFactory.get_instance()
    
    def save_file(self, file: BinaryIO, filename: str) -> Optional[str]:
        """
        保存上传的文件
        
        Args:
            file: 文件流
            filename: 文件名
            
        Returns:
            str: 保存后的文件路径，如果保存失败则返回None
        """
        try:
            # 获取文件扩展名
            file_ext = os.path.splitext(filename)[1].lower()
            
            # 检查文件类型是否支持
            if file_ext not in self._file_type_mapping:
                logger.error(f"不支持的文件类型: {file_ext}")
                return None
            
            # 生成保存路径
            save_path = self.save_dir / filename
            
            # 保存文件
            with open(save_path, "wb") as f:
                shutil.copyfileobj(file, f)
            
            logger.info(f"文件保存成功: {save_path}")
            return str(save_path)
            
        except Exception as e:
            logger.error(f"文件保存失败: {str(e)}")
            return None
    
    def read_file(self, file_path: str) -> Optional[str]:
        """
        读取文件内容，根据文件类型自动选择对应的读取器
        
        Args:
            file_path: 文件路径
            
        Returns:
            str: 文件内容，如果读取失败则返回None
        """
        try:
            # 获取文件扩展名
            file_ext = os.path.splitext(file_path)[1].lower()
            
            # 检查文件类型是否支持
            if file_ext not in self._file_type_mapping:
                logger.error(f"不支持的文件类型: {file_ext}")
                return None
            
            # 获取对应的读取器并调用read_text方法
            reader = self._file_type_mapping[file_ext]
            return reader.read_text(file_path)
                
        except Exception as e:
            logger.error(f"读取文件失败: {str(e)}")
            return None

    def upload_image(self, file: BinaryIO, filename: str) -> Optional[str]:
        """
        上传图片文件并保存到本地，并写入数据库
        Args:
            file: 图片文件流
            filename: 图片文件名
        Returns:
            str: 图片ID，失败返回None
        """
        try:
            allowed_exts = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
            file_ext = os.path.splitext(filename)[1].lower()
            if file_ext not in allowed_exts:
                logger.error(f"不支持的图片类型: {file_ext}")
                return None
            save_path = self.save_dir / filename
            with open(save_path, "wb") as f:
                shutil.copyfileobj(file, f)
            logger.info(f"图片保存成功: {save_path}")
            # 生成uuid，写入数据库
            image_id = str(uuid.uuid4())
            with self.repository_factory.image_file_repository as image_repo:
                image_repo.create_image_file(id=image_id, filename=filename, filepath=str(save_path))
            return image_id
        except Exception as e:
            logger.error(f"图片保存失败: {str(e)}")
            return None

    def save_document_images(self, document_id: str, images: list, replace_existing: bool = False) -> list[str]:
        """
        保存文档关联图片并建立 document_id -> image_id 映射。

        Args:
            document_id: 文档ID
            images: 图片列表，每项至少包含 data/filename/mime/sort_order
            replace_existing: 是否先删除旧映射及旧图片文件
        """
        if not images:
            return []

        from database.sql.repository.image_file_repository import ImageFileRepository
        from database.sql.repository.document_image_repository import DocumentImageRepository

        allowed_exts = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
        base_dir = Path(__file__).parent.parent.parent
        session = self.repository_factory.db_manager.get_session()
        image_repo = ImageFileRepository(session)
        document_image_repo = DocumentImageRepository(session)

        image_ids = []
        new_file_paths: list[Path] = []
        old_file_paths: list[Path] = []
        old_records = []

        try:
            if replace_existing:
                old_records = document_image_repo.get_images_by_document_id(document_id)
                for record in old_records:
                    image_file = image_repo.get_image_file(record.image_id)
                    if image_file and image_file.filepath:
                        stored_path = Path(image_file.filepath)
                        physical_path = stored_path if stored_path.is_absolute() else (base_dir / stored_path)
                        old_file_paths.append(physical_path)

            for index, image in enumerate(images, start=1):
                data = image.get("data")
                if not data:
                    raise ValueError(f"图片数据为空: document_id={document_id}, index={index}")

                sort_order = int(image.get("sort_order") or index)
                original_name = str(image.get("filename") or f"image_{sort_order}.bin")
                mime = str(image.get("mime") or "").strip().lower()
                suffix = Path(original_name).suffix.lower()
                if not suffix:
                    suffix = (mimetypes.guess_extension(mime) or "").lower()
                if suffix not in allowed_exts:
                    raise ValueError(f"不支持的图片类型: document_id={document_id}, suffix={suffix or '<empty>'}")

                image_id = str(uuid.uuid4())
                safe_filename = f"document_{document_id}_image_{sort_order}{suffix}"
                relative_save_path = self.save_dir / safe_filename
                physical_save_path = base_dir / relative_save_path
                physical_save_path.parent.mkdir(parents=True, exist_ok=True)

                with open(physical_save_path, "wb") as f:
                    f.write(data)
                new_file_paths.append(physical_save_path)

                image_repo.create_image_file(
                    id=image_id,
                    filename=safe_filename,
                    filepath=str(relative_save_path),
                )
                document_image_repo.add_document_image(document_id, image_id, sort_order)
                image_ids.append(image_id)

            if replace_existing:
                for record in old_records:
                    document_image_repo.delete(record.id)
                    image_repo.delete(record.image_id)

            if len(image_ids) != len(images):
                raise RuntimeError(
                    f"图片保存数量异常: document_id={document_id}, expected={len(images)}, actual={len(image_ids)}"
                )

            session.commit()

        except Exception:
            session.rollback()
            for file_path in new_file_paths:
                try:
                    file_path.unlink(missing_ok=True)
                except OSError as e:
                    logger.warning(f"回滚时删除新图片文件失败: path={file_path}, error={e}")
            raise
        finally:
            session.close()

        for file_path in old_file_paths:
            try:
                file_path.unlink(missing_ok=True)
            except OSError as e:
                logger.warning(f"删除旧图片文件失败: document_id={document_id}, path={file_path}, error={e}")

        return image_ids

    def get_document_image_ids(self, document_id: str) -> list[str]:
        with self.repository_factory.document_image_repository as document_image_repo:
            records = document_image_repo.get_images_by_document_id(document_id)
            return [record.image_id for record in records]

    def delete_document_images(self, document_id: str) -> int:
        """
        删除文档关联图片及映射。
        """
        from database.sql.repository.image_file_repository import ImageFileRepository
        from database.sql.repository.document_image_repository import DocumentImageRepository

        base_dir = Path(__file__).parent.parent.parent
        session = self.repository_factory.db_manager.get_session()
        image_repo = ImageFileRepository(session)
        document_image_repo = DocumentImageRepository(session)

        file_paths_to_delete: list[Path] = []

        try:
            records = document_image_repo.get_images_by_document_id(document_id)
            if not records:
                session.close()
                return 0

            image_ids = []
            for record in records:
                image_ids.append(record.image_id)
                image_file = image_repo.get_image_file(record.image_id)
                if image_file and image_file.filepath:
                    stored_path = Path(image_file.filepath)
                    physical_path = stored_path if stored_path.is_absolute() else (base_dir / stored_path)
                    file_paths_to_delete.append(physical_path)

            deleted_count = document_image_repo.delete_by_document_id(document_id)

            for image_id in image_ids:
                image_repo.delete(image_id)

            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        for file_path in file_paths_to_delete:
            try:
                file_path.unlink(missing_ok=True)
            except OSError as e:
                logger.warning(f"删除图片文件失败: document_id={document_id}, path={file_path}, error={e}")

        return deleted_count

    def get_image_file(self, image_id: str) -> Optional[str]:
        """
        通过image_id获取本地图片文件路径
        Args:
            image_id: 图片唯一ID
        Returns:
            str: 图片文件的本地路径，若不存在返回None
        """
        with self.repository_factory.image_file_repository as image_repo:
            image_file = image_repo.get_image_file(image_id)
            if image_file and image_file.filepath:
                BASE_DIR = Path(__file__).parent.parent.parent
                return str(BASE_DIR / image_file.filepath)
            else:
                logger.error(f"图片文件不存在, image_id: {image_id}")
                return None

    def pdf_to_images_and_upload(self, pdf_path: str, document_id: str,
                                  page_start: int = None, page_end: int = None) -> Optional[list]:
        """
        将pdf每页转为图片并上传，返回图片id列表
        Args:
            pdf_path: PDF文件路径
            document_id: 文档id
            page_start: 起始页（1-based，含），None表示从第1页开始
            page_end: 结束页（1-based，含），None表示到最后一页
        Returns:
            list: 图片id列表
        """
        from .file_reader.pdf_reader import pdf_reader
        import os
        image_ids = []
        images = pdf_reader.pdf_to_images(pdf_path, page_start=page_start, page_end=page_end)
        if not images:
            return None
        with self.repository_factory.manual_image_repository as manual_image_repo:
            for idx, img in enumerate(images):
                img_filename = f"manual_{document_id}_page_{idx+1}.png"
                img_byte_arr = io.BytesIO()
                img.save(img_byte_arr, format='PNG')
                img_byte_arr.seek(0)
                image_id = self.upload_image(img_byte_arr, img_filename)
                if image_id:
                    image_ids.append(image_id)
                    manual_image_repo.add_manual_image(document_id, image_id, idx+1)
            return image_ids

# 创建全局单例实例
file_reader = FileReader() 
