# -*- coding: utf-8 -*-
from typing import Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, Response, Depends
from pydantic import BaseModel
from pathlib import Path

from service.system.file_reader_main import file_reader
from util.logging.logger import get_logger
from api.middleware.auth_middleware import get_current_user
from datetime import datetime

logger = get_logger(__name__)

router = APIRouter(
    prefix="/file",
    tags=["文件读取"],
    responses={404: {"description": "Not found"}}
)

class FileResponse(BaseModel):
    """文件处理响应模型"""
    content: Optional[str] = None
    file_path: Optional[str] = None
    message: str
    success: bool

class ImageFileResponse(BaseModel):
    id: str
    filename: str
    filepath: str
    upload_time: datetime
    message: str
    success: bool

@router.post("/process", response_model=FileResponse)
async def process_file(file: UploadFile = File(...)) -> FileResponse:
    """
    处理文件：上传并读取内容
    
    Args:
        file: 上传的文件
        
    Returns:
        FileResponse: 文件处理响应，包含文件内容、保存路径和状态信息
    """
    try:
        # 保存文件
        file_path = file_reader.save_file(file.file, file.filename)
        
        if file_path is None:
            return FileResponse(
                content=None,
                file_path=None,
                message="文件上传失败",
                success=False
            )
        
        # 读取文件内容
        content = file_reader.read_file(file_path)
        
        if content is None:
            return FileResponse(
                content=None,
                file_path=file_path,
                message="文件读取失败",
                success=False
            )
            
        return FileResponse(
            content=content,
            file_path=file_path,
            message="文件处理成功",
            success=True
        )
        
    except Exception as e:
        logger.error(f"文件处理失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"文件处理失败: {str(e)}"
        )
    finally:
        file.file.close() 

# @router.post("/upload_image", response_model=ImageFileResponse)
# async def upload_image(file: UploadFile = File(...)) -> ImageFileResponse:
#     """
#     上传图片文件并保存到本地，返回图片元数据
#     """
#     try:
#         image_file = file_reader.upload_image(file.file, file.filename)
#         if image_file is None:
#             return ImageFileResponse(
#                 id="",
#                 filename="",
#                 filepath="",
#                 upload_time=datetime.utcnow(),
#                 message="图片上传失败或类型不支持",
#                 success=False
#             )
#         return ImageFileResponse(
#             id=image_file.id,
#             filename=image_file.filename,
#             filepath=image_file.filepath,
#             upload_time=image_file.upload_time,
#             message="图片上传成功",
#             success=True
#         )
#     except Exception as e:
#         logger.error(f"图片上传失败: {str(e)}")
#         raise HTTPException(status_code=500, detail=f"图片上传失败: {str(e)}")
#     finally:
#         file.file.close()

@router.get("/image/{image_id}")
async def get_image(image_id: str):
    """
    通过image_id下载图片，返回图片二进制流
    """
    file_path = file_reader.get_image_file(image_id)
    if file_path is None:
        raise HTTPException(status_code=404, detail="图片文件不存在")
    ext = Path(file_path).suffix.lower().lstrip('.')
    mime_type = f"image/jpeg" if ext in ['jpg', 'jpeg'] else f"image/{ext}"
    with open(file_path, "rb") as image_file:
        image_data = image_file.read()
    return Response(content=image_data, media_type=mime_type) 