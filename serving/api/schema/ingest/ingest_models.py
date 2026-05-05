# -*- coding: utf-8 -*-
from pydantic import BaseModel
from typing import Optional, List


class IngestTaskResponse(BaseModel):
    task_id: str
    status: str
    message: str
    collection_type: Optional[str] = None
    rows_ingested: Optional[int] = None


class IngestStatusResponse(BaseModel):
    task_id: str
    status: str           # pending / running / done / failed
    current_step: int
    total_steps: int
    step_name: str
    progress_detail: str
    model: str
    pdf_filename: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    elapsed_seconds: Optional[int] = None
    error: Optional[str] = None


class IngestListItem(BaseModel):
    task_id: str
    status: str
    model: str
    pdf_filename: str
    started_at: Optional[str] = None
    elapsed_seconds: Optional[int] = None


class IngestListResponse(BaseModel):
    tasks: List[IngestListItem]
