# Knowledge Base Tool

A general-purpose document ingestion and knowledge base management system. Supports multi-format file uploads (Excel, PDF, Word) and writes to a four-database backend (MySQL + MongoDB + Milvus + Elasticsearch + Redis).

This repository is a working baseline. See [REQUIREMENTS.md](./REQUIREMENTS.md) for what needs to be built on top of it.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11 + FastAPI + SQLAlchemy |
| Relational DB | MySQL 8.0 |
| Document DB | MongoDB |
| Vector DB | Milvus 2.x |
| Search | Elasticsearch 8.x |
| Cache & Task Queue | Redis |
| LLM | Qwen (DashScope) |
| Frontend | React 18 + Ant Design + Vite |
| Deployment | Docker + Docker Compose |

---

## Architecture

```
┌────────────┐
│  Admin UI  │  (React + Ant Design)
└──────┬─────┘
       │ HTTP
       ▼
┌────────────────────────────────────────────────┐
│  Backend (FastAPI)                             │
│                                                │
│  ┌──────────────────────────────────────────┐  │
│  │  Ingestion Pipeline                      │  │
│  │                                          │  │
│  │  Parser → Chunker → LLM Extract          │  │
│  │      → Embedding → 4-DB Writer           │  │
│  └──────────────────────────────────────────┘  │
└────────────────────────────────────────────────┘
       │
       ├──────────┬──────────┬──────────┬──────────┐
       ▼          ▼          ▼          ▼          ▼
   ┌──────┐  ┌────────┐  ┌────────┐  ┌──────┐  ┌──────┐
   │MySQL │  │Milvus  │  │MongoDB │  │  ES  │  │Redis │
   └──────┘  └────────┘  └────────┘  └──────┘  └──────┘
   metadata    vectors    chunk text   keyword   tasks
```

**Data flow per upload:**

1. User uploads file via Admin UI
2. Backend receives file, creates `KbFile` row in MySQL (status=`pending`)
3. Background task: parse → chunk → embed → write to Milvus + MongoDB + ES
4. For each chunk, create a `KbRecord` row in MySQL with the unified ID
5. Update `KbFile.status` to `active` / `partial` / `failed`

---

## Repository Structure

```
.
├── ingestion/                     # Document ingestion pipeline
│   ├── core/                      # Base agent, errors, retry, progress
│   ├── db/                        # Milvus / MongoDB / ES writers
│   ├── parsers/                   # File-type-specific parsers
│   ├── storage/                   # Image storage helpers
│   ├── src/                       # LLM client, config
│   ├── excel_ingest_agent.py      # Excel ingestion (most complete reference)
│   ├── manual_ingest_agent.py     # PDF ingestion
│   ├── word_ingest_agent.py       # Word ingestion
│   └── chunker.py                 # Text chunking
│
├── serving/                       # Backend API server
│   ├── api/
│   │   ├── router/admin/          # Admin API (upload, list, delete)
│   │   ├── router/ingest/         # Ingestion task API
│   │   ├── router/knowledge/      # Knowledge retrieval API
│   │   └── middleware/            # Auth middleware
│   ├── service/
│   │   ├── knowledge/             # Knowledge service (handlers, retrieval)
│   │   ├── auth/                  # Permission utils
│   │   └── system/                # File parsers, LLM service
│   ├── database/
│   │   ├── sql/                   # MySQL ORM (KbCollection / KbFile / KbRecord)
│   │   ├── document/              # MongoDB
│   │   ├── vector/                # Milvus
│   │   ├── search/                # Elasticsearch
│   │   └── cache/                 # Redis
│   └── config/                    # Configuration
│
├── admin/                         # Admin frontend (React + Ant Design)
│   ├── src/
│   │   ├── pages/knowledge/       # Knowledge management page (only page)
│   │   ├── layouts/               # AdminLayout
│   │   ├── api/                   # Backend API client
│   │   └── ...
│   └── nginx.conf
│
├── shared/                        # Shared schema and config
│
├── infrastructure/                # Database deployment configs
│   ├── mysql/
│   ├── mongodb/
│   ├── milvus/
│   ├── elasticsearch/
│   └── redis/
│
├── docker-compose.app.yml         # Backend + admin + ingestion
├── docker-compose.databases.yml   # All databases
├── .env.example                   # Environment variable template
│
├── REQUIREMENTS.md                # 👈 What you need to build
└── README.md                      # This file
```

---

## Database Schema

Three core MySQL tables (defined in `serving/database/sql/model/admin.py`):

### `KbCollection`
Collection-level metadata (a "collection" groups files of similar type).

### `KbFile`
File-level metadata. One row per uploaded file.

Key fields: `id` (UUID), `filename`, `collection_name`, `file_type`, `status` (pending/ingesting/active/partial/failed), `total_records`, `success_records`, `metadata_json`, `batch_id`, `task_id`.

### `KbRecord`
Record-level metadata. For Excel: one row per spreadsheet row. For PDF/Word: one row per chunk.

Key fields: `id` (UUID), `file_id` (FK to KbFile), `mongo_doc_id`, `milvus_id`, `es_id` (all three are the same UUID — unified ID across databases), `status`, `is_indexed_milvus/mongo/es`.

**Unified ID design:** When a chunk is generated, a deterministic UUID5 is computed from `(collection_name, filename, file_sha256, chunk_local_key)`. The same UUID is used as the primary key in MongoDB, Milvus, and ES — so `mongo_doc_id == milvus_id == es_id == record["id"]`.

---

## Core Module Walk-through

If you're new to the codebase, read in this order:

### 1. `ingestion/core/base_agent.py`
The template-method base class for all ingestion agents. Defines the 5-step pipeline: `parse → chunk → embed → write → finalize`. Subclasses override step methods.

Key methods:
- `ingest(file_path)` — main entry, orchestrates the 5 steps
- `_process_one_batch(batch)` — embed + write to Milvus/Mongo/ES
- `_write_kb_records(valid_batch)` — V2: write to MySQL kb_records
- `_finalize_kb_file(result)` — V2: update kb_files status

### 2. `ingestion/excel_ingest_agent.py`
The most complete reference implementation. Read this to understand how all the pieces connect. Excel ingestion is row-based (one Excel row → one chunk → one record).

### 3. `serving/api/router/admin/admin_knowledge.py`
The admin backend. Read these endpoints in order:
- `POST /upload-batch` — upload entry
- `_submit_upload_batch_item` — task dispatch
- `_run_admin_excel_ingest_task` — backend task that calls ingestion
- `_create_kb_file_record` — INSERT KbFile before task starts
- `GET /files` — list files (V2 reads from `kb_files`)
- `DELETE /files/{document_id}` — delete file (with multi-DB cleanup)

### 4. `serving/database/sql/model/admin.py`
The 3 ORM definitions. Small file (~216 lines). Read this to understand the data model.

### 5. `admin/src/pages/knowledge/index.jsx`
The frontend (single-page React component, ~1400 lines). Handles upload, file listing, deletion.

---

## Quick Start (Local Development)

### Prerequisites

- Docker + Docker Compose
- Python 3.11+ (for running scripts outside Docker)
- Node.js 18+ (for admin frontend development)

### Start infrastructure (databases)

```bash
docker compose -f docker-compose.databases.yml up -d
```

Wait ~30 seconds for all databases to initialize. Verify:

```bash
docker ps   # should see mysql, mongodb, milvus, elasticsearch, redis containers
```

### Configure environment

```bash
cp .env.example .env
# Edit .env to set your DashScope API key and any other secrets
```

Required environment variables:
- `DASHSCOPE_API_KEY` — your Qwen / embedding API key
- Database connection settings (defaults usually work for local Docker)

### Start backend + admin

```bash
docker compose -f docker-compose.app.yml up -d
```

Verify:
- Backend: `http://localhost:10090/docs` (FastAPI swagger)
- Admin UI: `http://localhost:10091`

### Test an upload

1. Open admin UI in browser
2. Go to "Knowledge Management"
3. Click "Upload"
4. Select a small Excel / PDF / Word file
5. Watch the progress bar
6. After completion, check the file appears in the list

---

## What's NOT Included

This is a stripped-down evaluation package. The following are deliberately removed:

- ❌ Chat / Q&A agent (we only ingest, you build query side separately if needed)
- ❌ User management, login, RBAC dashboards
- ❌ Other business modules unrelated to ingestion
- ❌ Real test data (place your own files in `ingestion/Input/`)
- ❌ Production credentials (all `.env.example` use placeholders)

---

## License

Proprietary. Do not redistribute. See [REQUIREMENTS.md](./REQUIREMENTS.md) for collaboration terms.

---

## Contact

Open issues directly in this repository's Issues tab.
