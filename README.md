# Knowledge Base Tool

通用文档入库与知识库管理系统。支持多种格式文件上传（Excel、PDF、Word），并将数据写入四数据库后端（MySQL + MongoDB + Milvus + Elasticsearch + Redis）。

本仓库是可运行的基础代码。需要在此基础上构建的内容，请参阅 [REQUIREMENTS.md](./REQUIREMENTS.md)。

---

## 技术栈

| 层级 | 技术 |
|---|---|
| 后端 | Python 3.11 + FastAPI + SQLAlchemy |
| 关系型数据库 | MySQL 8.0 |
| 文档数据库 | MongoDB |
| 向量数据库 | Milvus 2.x |
| 搜索引擎 | Elasticsearch 8.x |
| 缓存与任务队列 | Redis |
| LLM | Qwen（DashScope） |
| 前端 | React 18 + Ant Design + Vite |
| 部署 | Docker + Docker Compose |

---

## 架构

```
┌────────────┐
│  Admin UI  │  (React + Ant Design)
└──────┬─────┘
       │ HTTP
       ▼
┌────────────────────────────────────────────────┐
│  后端（FastAPI）                               │
│                                                │
│  ┌──────────────────────────────────────────┐  │
│  │  入库流水线                              │  │
│  │                                          │  │
│  │  解析 → 分块 → LLM 提取                  │  │
│  │      → Embedding → 四库写入             │  │
│  └──────────────────────────────────────────┘  │
└────────────────────────────────────────────────┘
       │
       ├──────────┬──────────┬──────────┬──────────┐
       ▼          ▼          ▼          ▼          ▼
   ┌──────┐  ┌────────┐  ┌────────┐  ┌──────┐  ┌──────┐
   │MySQL │  │Milvus  │  │MongoDB │  │  ES  │  │Redis │
   └──────┘  └────────┘  └────────┘  └──────┘  └──────┘
   元数据      向量        chunk 原文   关键词    任务队列
```

**单次上传的数据流：**

1. 用户通过 Admin UI 上传文件
2. 后端接收文件，在 MySQL 中创建 `KbFile` 记录（status=`pending`）
3. 后台任务：解析 → 分块 → embedding → 写入 Milvus + MongoDB + ES
4. 每个 chunk 在 MySQL 中创建一条 `KbRecord` 记录，记录统一 ID
5. 更新 `KbFile.status` 为 `active` / `partial` / `failed`

---

## 仓库结构

```
.
├── ingestion/                     # 文档入库流水线
│   ├── core/                      # 基础 Agent、错误处理、重试、进度
│   ├── db/                        # Milvus / MongoDB / ES 写入器
│   ├── parsers/                   # 按文件类型的解析器
│   ├── storage/                   # 图片存储辅助工具
│   ├── src/                       # LLM 客户端、配置
│   ├── excel_ingest_agent.py      # Excel 入库（最完整的参考实现）
│   ├── manual_ingest_agent.py     # PDF 入库
│   ├── word_ingest_agent.py       # Word 入库
│   └── chunker.py                 # 文本分块
│
├── serving/                       # 后端 API 服务
│   ├── api/
│   │   ├── router/admin/          # Admin API（上传、列表、删除）
│   │   ├── router/ingest/         # 入库任务 API
│   │   ├── router/knowledge/      # 知识检索 API
│   │   └── middleware/            # 认证中间件
│   ├── service/
│   │   ├── knowledge/             # 知识服务（handlers、检索）
│   │   ├── auth/                  # 权限工具
│   │   └── system/                # 文件解析器、LLM 服务
│   ├── database/
│   │   ├── sql/                   # MySQL ORM（KbCollection / KbFile / KbRecord）
│   │   ├── document/              # MongoDB
│   │   ├── vector/                # Milvus
│   │   ├── search/                # Elasticsearch
│   │   └── cache/                 # Redis
│   └── config/                    # 配置
│
├── admin/                         # Admin 前端（React + Ant Design）
│   ├── src/
│   │   ├── pages/knowledge/       # 知识库管理页面（唯一页面）
│   │   ├── layouts/               # AdminLayout
│   │   ├── api/                   # 后端 API 客户端
│   │   └── ...
│   └── nginx.conf
│
├── shared/                        # 共享 schema 与配置
│
├── infrastructure/                # 数据库部署配置
│   ├── mysql/
│   ├── mongodb/
│   ├── milvus/
│   ├── elasticsearch/
│   └── redis/
│
├── docker-compose.app.yml         # 后端 + admin + 入库服务
├── docker-compose.databases.yml   # 所有数据库
├── .env.example                   # 环境变量模板
│
├── REQUIREMENTS.md                # 👈 需要构建的内容
└── README.md                      # 本文件
```

---

## 数据库 Schema

三张核心 MySQL 表（定义于 `serving/database/sql/model/admin.py`）：

### `KbCollection`
collection 级别的元数据（一个 collection 将同类型文件归为一组）。

### `KbFile`
文件级别的元数据。每个上传文件对应一行记录。

关键字段：`id`（UUID）、`filename`、`collection_name`、`file_type`、`status`（pending/ingesting/active/partial/failed）、`total_records`、`success_records`、`metadata_json`、`batch_id`、`task_id`。

### `KbRecord`
记录级别的元数据。Excel 文件：每个表格行对应一条记录；PDF/Word 文件：每个 chunk 对应一条记录。

关键字段：`id`（UUID）、`file_id`（KbFile 的外键）、`mongo_doc_id`、`milvus_id`、`es_id`（三者相同，均为统一 UUID）、`status`、`is_indexed_milvus/mongo/es`。

**统一 ID 设计：** 生成 chunk 时，系统基于 `(collection_name, filename, file_sha256, chunk_local_key)` 计算确定性的 UUID5。该 UUID 同时作为 MongoDB、Milvus、ES 的主键，因此 `mongo_doc_id == milvus_id == es_id == record["id"]`。

---

## 核心模块导读

建议按以下顺序阅读代码：

### 1. `ingestion/core/base_agent.py`
所有入库 Agent 的模板方法基类。定义了五步流水线：`解析 → 分块 → embedding → 写入 → 收尾`。子类通过覆盖各步骤方法实现具体逻辑。

关键方法：
- `ingest(file_path)` — 主入口，协调五个步骤
- `_process_one_batch(batch)` — embedding + 写入 Milvus/Mongo/ES
- `_write_kb_records(valid_batch)` — V2：写入 MySQL kb_records
- `_finalize_kb_file(result)` — V2：更新 kb_files 状态

### 2. `ingestion/excel_ingest_agent.py`
最完整的参考实现。通读此文件可以理解各模块如何协作。Excel 入库以行为单位（一行 → 一个 chunk → 一条记录）。

### 3. `serving/api/router/admin/admin_knowledge.py`
Admin 后端。建议按以下顺序阅读各接口：
- `POST /upload-batch` — 上传入口
- `_submit_upload_batch_item` — 任务分发
- `_run_admin_excel_ingest_task` — 调用入库逻辑的后台任务
- `_create_kb_file_record` — 任务启动前创建 KbFile 记录
- `GET /files` — 文件列表（V2 从 `kb_files` 读取）
- `DELETE /files/{document_id}` — 删除文件（含多库清理）

### 4. `serving/database/sql/model/admin.py`
三张表的 ORM 定义，文件较小（约 216 行）。通过此文件理解数据模型。

### 5. `admin/src/pages/knowledge/index.jsx`
前端（单页面 React 组件，约 1400 行）。处理上传、文件列表、删除。

---

## 快速开始（本地开发）

### 前置条件

- Docker + Docker Compose
- Python 3.11+（在 Docker 外运行脚本时需要）
- Node.js 18+（admin 前端开发时需要）

### 启动基础设施（数据库）

```bash
docker compose -f docker-compose.databases.yml up -d
```

等待约 30 秒，让所有数据库完成初始化。验证：

```bash
docker ps   # 应能看到 mysql、mongodb、milvus、elasticsearch、redis 容器
```

### 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 DashScope API key 及其他密钥
```

必填环境变量：
- `DASHSCOPE_API_KEY` — Qwen / embedding API key
- 数据库连接配置（本地 Docker 默认值通常无需修改）

### 启动后端与 Admin

```bash
docker compose -f docker-compose.app.yml up -d
```

验证：
- 后端：`http://localhost:10090/docs`（FastAPI Swagger）
- Admin UI：`http://localhost:10091`

### 测试上传

1. 在浏览器打开 Admin UI
2. 进入"知识库管理"
3. 点击"上传"
4. 选择一个较小的 Excel / PDF / Word 文件
5. 观察进度条
6. 上传完成后，确认文件出现在列表中

---

## 不包含的内容

本仓库是精简后的评估包，以下内容已被有意移除：

- ❌ Chat / 问答 Agent（本仓库仅负责入库，查询侧可按需另行构建）
- ❌ 用户管理、登录、RBAC 控制台
- ❌ 与入库无关的其他业务模块
- ❌ 真实测试数据（请将自己的文件放入 `ingestion/Input/`）
- ❌ 生产凭证（所有 `.env.example` 均使用占位符）

---

## License

专有代码，禁止再分发。合作条款见 [REQUIREMENTS.md](./REQUIREMENTS.md)。

---

## 联系方式

请直接在本仓库的 Issues 页面提交问题。
