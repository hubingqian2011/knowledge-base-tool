# -*- coding: utf-8 -*-
"""
管理后台 MySQL 模型。

- KbCollection: 知识库 collection 元信息
- KbFile:       知识库文件级元数据 (V2)
- KbRecord:     知识库记录级元数据 (V2)
"""

import uuid

from sqlalchemy import BigInteger, Boolean, Column, ForeignKey, Index, Integer, SmallInteger, String, DateTime, Text, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database_manager import Base


class KbCollection(Base):
    """知识库 collection 元信息表。"""

    __tablename__ = "kb_collections"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键")
    name = Column(String(100), unique=True, nullable=False, index=True, comment="collection 名称")
    display_name = Column(String(100), nullable=True, comment="展示名称")
    type = Column(String(50), nullable=True, comment="类型")
    description = Column(Text, nullable=True, comment="描述")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "type": self.type,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }



# ─────────────────────────────────────────────────────────────────────────────
# V2 知识库双表：KbFile / KbRecord
# 主键策略：String(36) UUID（有意例外，不用 Integer autoincrement）
#   原因：mongo_doc_id 必须字符串；三库统一字符串 ID；业务代码直接持有 ID
# ─────────────────────────────────────────────────────────────────────────────


class KbFile(Base):
    """知识库文件级元数据 (V2)。

    每行代表一个上传的物理文件。
    PDF/Word/PPT/MP4: 1 file → 1 record
    Excel:           1 file → N records (每行 Excel 一个 record)
    """

    __tablename__ = "kb_files"

    # 主键：UUID 字符串（有意例外，见文件头注释）
    id = Column(String(36), primary_key=True,
                default=lambda: str(uuid.uuid4()), comment="主键 UUID")

    filename         = Column(String(512),  nullable=False, comment="原始文件名")
    collection_type  = Column(String(32),   nullable=False, comment="collection 类型（前端 collection_type）")
    collection_name  = Column(String(128),  nullable=False, comment="Milvus/Mongo collection 名称")
    file_type        = Column(String(16),   nullable=False, comment="文件扩展名，如 xlsx/pdf/mp4")
    file_size_bytes  = Column(BigInteger,   nullable=True,  comment="文件字节数")
    permission_level = Column(SmallInteger, nullable=False, comment="权限等级：1=内部 2=内部代理 3=客户")
    sub_category     = Column(String(64),   nullable=True,  comment="子分类，对应 selected_category")

    # SQLAlchemy 内置属性名 metadata 已被占用，DB 列名保持 metadata
    metadata_json    = Column("metadata",   JSON,           nullable=True,  comment="上传时附带的 metadata 字段（JSON）")

    status           = Column(String(16),   nullable=False, default="pending", comment="文件处理状态：pending/processing/done/failed/archived/deleted")
    total_records    = Column(Integer,      nullable=False, default=0,      comment="总记录数（Excel 为行数）")
    success_records  = Column(Integer,      nullable=False, default=0,      comment="成功入库记录数")
    failed_records   = Column(Integer,      nullable=False, default=0,      comment="入库失败记录数")

    uploaded_at      = Column(DateTime(timezone=True), server_default=func.now(), comment="文件上传时间")
    uploaded_by      = Column(String(64),   nullable=True,  comment="上传人用户 ID")
    ingested_at      = Column(DateTime(timezone=True), nullable=True,  comment="入库完成时间")
    archived_at      = Column(DateTime(timezone=True), nullable=True,  comment="归档时间")
    deleted_at       = Column(DateTime(timezone=True), nullable=True,  comment="软删除时间")

    batch_id         = Column(String(64),   nullable=True,  comment="关联的上传批次 ID（Redis batch_id）")
    task_id          = Column(String(64),   nullable=True,  comment="关联的后台任务 ID")
    source_file_path = Column(String(1024), nullable=True,  comment="服务器端临时文件路径")

    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )

    records = relationship("KbRecord", back_populates="file", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_kb_files_collection_status", "collection_name", "status"),
        Index("idx_kb_files_collection_type",   "collection_type"),
        Index("idx_kb_files_filename",          "filename"),
        Index("idx_kb_files_uploaded_at",       "uploaded_at"),
        Index("idx_kb_files_status",            "status"),
        Index("idx_kb_files_batch_id",          "batch_id"),
        {"comment": "V2 知识库文件级元数据"},
    )

    def to_dict(self) -> dict:
        return {
            "id":               self.id,
            "filename":         self.filename,
            "collection_type":  self.collection_type,
            "collection_name":  self.collection_name,
            "file_type":        self.file_type,
            "file_size_bytes":  self.file_size_bytes,
            "permission_level": self.permission_level,
            "sub_category":     self.sub_category,
            "metadata_json":    self.metadata_json,
            "status":           self.status,
            "total_records":    self.total_records,
            "success_records":  self.success_records,
            "failed_records":   self.failed_records,
            "uploaded_at":      self.uploaded_at.isoformat() if self.uploaded_at else None,
            "uploaded_by":      self.uploaded_by,
            "ingested_at":      self.ingested_at.isoformat() if self.ingested_at else None,
            "archived_at":      self.archived_at.isoformat() if self.archived_at else None,
            "deleted_at":       self.deleted_at.isoformat() if self.deleted_at else None,
            "batch_id":         self.batch_id,
            "task_id":          self.task_id,
            "source_file_path": self.source_file_path,
            "created_at":       self.created_at.isoformat() if self.created_at else None,
            "updated_at":       self.updated_at.isoformat() if self.updated_at else None,
        }


class KbRecord(Base):
    """知识库记录级元数据 (V2)。

    每行代表文件内的一个独立业务单元。
    与 MongoDB DocumentModel 一一对应，由 mongo_doc_id 关联。
    """

    __tablename__ = "kb_records"

    # 主键：UUID 字符串（有意例外，见文件头注释）
    id = Column(String(36), primary_key=True,
                default=lambda: str(uuid.uuid4()), comment="主键 UUID")

    file_id         = Column(String(36),   ForeignKey("kb_files.id", ondelete="CASCADE"),
                             nullable=False, index=True, comment="所属文件 ID（外键）")
    collection_name = Column(String(128),  nullable=False, comment="Milvus/Mongo collection 名称")
    record_no       = Column(Integer,      nullable=False, default=1, comment="文件内序号（Excel 行号）")

    mongo_doc_id    = Column(String(64),   nullable=True,  index=True, comment="MongoDB document_id")
    milvus_id       = Column(String(64),   nullable=True,  index=True, comment="Milvus 向量 ID")
    es_id           = Column(String(64),   nullable=True,              comment="Elasticsearch 文档 ID")

    status          = Column(String(16),   nullable=False, default="active", comment="记录状态：active/deleted")

    is_indexed_milvus = Column(Boolean, nullable=False, default=False, comment="是否已写入 Milvus")
    is_indexed_mongo  = Column(Boolean, nullable=False, default=False, comment="是否已写入 MongoDB")
    is_indexed_es     = Column(Boolean, nullable=False, default=False, comment="是否已写入 Elasticsearch")

    fault_description   = Column(Text,       nullable=True, comment="故障描述（工单专用）")
    solution            = Column(Text,       nullable=True, comment="解决方案（工单专用）")
    related_signal      = Column(String(256),nullable=True, comment="关联信号码（工单专用）")
    chunk_text_preview  = Column(String(500),nullable=True, comment="chunk 文本预览（前 500 字符）")

    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )

    file = relationship("KbFile", back_populates="records")

    __table_args__ = (
        Index("idx_kb_records_file_status",       "file_id",        "status"),
        Index("idx_kb_records_collection_status", "collection_name","status"),
        Index("idx_kb_records_mongo_doc_id",      "mongo_doc_id"),
        Index("idx_kb_records_milvus_id",         "milvus_id"),
        Index("idx_kb_records_record_no",         "file_id",        "record_no"),
        {"comment": "V2 知识库记录级元数据"},
    )

    def to_dict(self) -> dict:
        return {
            "id":                 self.id,
            "file_id":            self.file_id,
            "collection_name":    self.collection_name,
            "record_no":          self.record_no,
            "mongo_doc_id":       self.mongo_doc_id,
            "milvus_id":          self.milvus_id,
            "es_id":              self.es_id,
            "status":             self.status,
            "is_indexed_milvus":  self.is_indexed_milvus,
            "is_indexed_mongo":   self.is_indexed_mongo,
            "is_indexed_es":      self.is_indexed_es,
            "fault_description":  self.fault_description,
            "solution":           self.solution,
            "related_signal":     self.related_signal,
            "chunk_text_preview": self.chunk_text_preview,
            "created_at":         self.created_at.isoformat() if self.created_at else None,
            "updated_at":         self.updated_at.isoformat() if self.updated_at else None,
        }
