# Requirements

This document defines what you need to build on top of the existing codebase. Read [README.md](./README.md) first for the codebase overview.

---

## Project Overview

**Goal:** Take the existing prototype and turn it into a production-quality, **configurable**, general-purpose knowledge base management tool.

**What "production-quality" means here:**

- It runs reliably end-to-end (upload → ingest → list → delete) without manual intervention
- Failures are observable and recoverable
- New file types / new metadata schemas can be added via **configuration**, not code changes
- A new developer can deploy and run it locally in under 30 minutes by following the docs

**What "production-quality" does NOT mean here:**

- Not enterprise-scale (no multi-tenant, no SSO, no audit logging)
- Not high-concurrency (single-digit users is fine)
- Not feature-complete (you don't need to add new features beyond what's specified)

**Timeline:** 2 weeks.

**Working language:** Code, comments, and docs in English or Chinese — your choice, just be consistent.

---

## Hard Requirements (Must Have)

### 1. End-to-end stability

The full upload → ingest → list → delete loop must work reliably for all four file types: **Excel, PDF, Word, PPT**.

Note: **PPT support does not exist yet — you need to implement it** following the same pattern as Excel/PDF/Word agents.

Specifically:
- Upload completes without timeout for files up to 10MB
- Failed embedding API calls are retried with exponential backoff
- Network glitches don't kill the entire task
- Frontend shows accurate task progress (not stuck at "processing...")

### 2. Multi-database consistency

When a file is uploaded, data is written to MySQL + MongoDB + Milvus + Elasticsearch. When deleted, data is removed from all four.

Required behavior:
- **On write failure:** the system records which databases were written and which weren't. The `KbFile.status` field reflects the partial state.
- **On delete:** the system attempts to delete from all four databases. Failures are logged and surfaced to the user.
- **No silent data loss:** orphan data in any database can be detected (a reconciliation script is acceptable).

You don't need full distributed transactions. A pragmatic "soft consistency + status field + reconciliation script" approach is fine.

### 3. Configurability (the core engineering challenge)

This is what we want to evaluate. The current code has **hardcoded business logic** in many places. Your job is to extract these into **configuration files** so changing them does not require code changes.

The following must be configurable via files (YAML / JSON / `.env`, your choice):

#### 3.1 Chunking strategy
- `chunk_size`, `overlap`, `splitter_type` (recursive / semantic / fixed)
- Different file types may use different strategies
- Currently hardcoded in chunker — extract it

#### 3.2 Prompt templates
- LLM prompts (e.g., metadata extraction, content cleanup) currently hardcoded in `.py` files
- Extract to external template files
- Support template variables (Jinja2 / f-string style)

#### 3.3 Metadata field schema
- The fields a user fills when uploading (currently hardcoded as `DEFAULT_UPLOAD_METADATA_FIELDS` in `admin/src/pages/knowledge/index.jsx`) must come from a config file or backend API
- Adding a new field (e.g., "department", "language") should require **zero frontend code change**
- Each field's type, label, options, required-or-not are all configurable

#### 3.4 Elasticsearch keyword extraction
- Currently the keyword extraction logic for ES indexing is hardcoded
- Extract: which fields go to ES, what analyzer to use, what fields to index for search

#### 3.5 LLM model selection
- The system should support switching between LLM providers (Qwen / OpenAI / DeepSeek / etc.) without code changes
- Currently only Qwen is wired up
- Implement a thin abstraction layer; provider selected via config

### 4. Frontend ↔ Backend Interaction

The frontend must:
- Show real-time upload progress (not just "loading...")
- Show task status accurately after page refresh (no lost state)
- Show a clear error message when something goes wrong
- Update the file list immediately after delete (no stale cache)

### 5. Deployment

The final deliverable must be runnable via:

```bash
cp .env.example .env
# (user fills in API key)
docker compose -f docker-compose.databases.yml up -d
docker compose -f docker-compose.app.yml up -d
```

After this, opening `http://localhost:10091` should show a working admin UI where the user can upload a file and see it processed.

If you change ports, paths, or anything in the deployment flow, **update the docs accordingly**.

---

## Soft Requirements (Nice to Have)

These add bonus points but are not required:

- Logging: structured logging with log levels (INFO / WARNING / ERROR)
- Reconciliation script: a CLI tool that scans the four databases and reports inconsistencies
- Test data generator: a script that generates synthetic test files for stress-testing
- Health check endpoint: `GET /health` returns DB connection status

---

## Explicitly NOT Required

Don't waste time on these — we will not evaluate them:

- ❌ User authentication / authorization (current `auth_middleware.py` is fine as-is)
- ❌ Rate limiting / DDoS protection
- ❌ HTTPS / TLS configuration
- ❌ CI/CD pipelines
- ❌ Unit test coverage above 30% (basic smoke tests are enough)
- ❌ Performance optimization beyond "doesn't time out"
- ❌ Replacing core architecture (FastAPI / SQLAlchemy / current DB choices)
- ❌ Building a chat / Q&A agent (out of scope)

---

## Deliverables

When you finish, push to your fork / branch and provide:

1. **Source code** — all changes committed
2. **DEPLOYMENT.md** — how to deploy from scratch (your updated version, replacing/supplementing this README's Quick Start)
3. **CHANGES.md** — what you changed and why, organized by topic
4. **CONFIG.md** — documentation of all configuration options you added (with examples)
5. **A working demo** — either a screen recording (5-10 min) or a live walkthrough call

---

## Acceptance Criteria

We will evaluate against these:

### Functional (40%)
- [ ] Excel upload → ingest → list → delete works end-to-end
- [ ] PDF upload → ingest → list → delete works end-to-end
- [ ] Word upload → ingest → list → delete works end-to-end
- [ ] PPT upload → ingest → list → delete works end-to-end
- [ ] Failed uploads are recoverable (retry / clear / re-upload)
- [ ] Multi-database deletion is consistent

### Configurability (40%)
- [ ] Chunking parameters configurable without code changes
- [ ] Prompt templates externalized
- [ ] Metadata schema configurable without code changes
- [ ] LLM model switchable via config
- [ ] ES keyword extraction configurable

### Engineering Quality (20%)
- [ ] Code is readable, with reasonable comments where logic is non-obvious
- [ ] Errors are logged with context (not silent `try/except: pass`)
- [ ] Configuration is documented
- [ ] Deployment docs are accurate (we will follow them step by step)

---

## How to Get Started

1. Read [README.md](./README.md) and run the Quick Start to get the existing system running locally
2. Test an Excel upload — observe what works and what doesn't
3. Read `ingestion/core/base_agent.py` — understand the pipeline
4. Read `serving/api/router/admin/admin_knowledge.py` — understand the API
5. Make a 2-week plan and share it with us before starting heavy work
6. Open issues / questions in this repo as you go

---

## Communication

- **Daily:** brief status update (text, ~3 sentences) — what you did yesterday, what you're doing today, blockers
- **Weekly:** longer update + demo (5-10 min recorded video or live call)
- **Issues:** open in this repo's Issues tab — that's our primary discussion channel
- **Don't:** WeChat for technical details. Keep technical discussions in writing for traceability.

---

## Payment Milestones

| Milestone | Trigger | Payment |
|---|---|---|
| M1 | End of Day 2: read codebase, run it locally, submit a written project plan | 30% |
| M2 | End of Day 7: hard requirements 1, 2, 4, 5 done; configurability partially done | 40% |
| M3 | End of Day 14: all hard requirements done; deliverables submitted; acceptance passed | 30% |

---

## Final Notes

- We have **deliberately stripped business-specific code** from this package. You're working on a generic KB tool. Don't try to reverse-engineer the original business — it's not relevant to your work.
- We've also **deliberately kept some rough edges** in the code (this is the prototype state). Your job is to clean these up. We'll evaluate how cleanly you do it.
- If something looks broken or wrong, **ask first** before making big architectural changes. Open an issue.
- We value **engineering judgment** over speed. A clean, well-documented solution to 80% of requirements beats a hacky solution to 100%.

Good luck.
