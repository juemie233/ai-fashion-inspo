# AI Outfit Material Library

> **[English](README.en.md) | Chinese** (The English document is a translation; the Chinese version is the source and can be regenerated using `python scripts/translate_docs.py`)

A personalized AI outfit material management tool designed to automatically collect and visually recognize fragmented outfit content, transforming it into searchable, intelligent material assets tailored to your needs.

## Prerequisites

The following software and environments are **required**; otherwise, core functionalities will not operate:

| Software | Version Requirement | Purpose | Installation Guide |
| -------- | ------------------- | ------- | ------------------ |
| Python | 3.12+ | Backend runtime | [python.org](https://www.python.org/downloads/) |
| Node.js | 20+ | Web frontend build | [nodejs.org](https://nodejs.org/en/download) |
| Ollama | latest | AI visual inference engine | [ollama.com](https://ollama.com/download/windows) |
| Qwen3-VL:8B-Instruct | — | Outfit tag recognition model | `ollama pull qwen3-vl:8b-instruct` |

### Additional Requirements for Scraping Engine (Only Required in CDP Mode)

| Software | Requirement | Purpose |
| -------- | ----------- | ------- |
| **Google Chrome** | Latest stable version | Host browser for CDP zero-detection scraping |
| Playwright | 1.40+ | Browser automation driver (`pip install playwright && playwright install chromium`) |

> ⚠️ **Important: Google Chrome must be used — no substitutes allowed!**
>
> CDP (Chrome DevTools Protocol) scraping relies on Google Chrome’s native debugging protocol. The following browsers **cannot** be used for CDP scraping:
>
> | Browser | Usable? | Reason |
> | -------- | :---: | ------ |
> | **Google Chrome** | ✅ | Fully supports CDP protocol |
> | 360 Speed Browser | ❌ | CDP protocol is stripped, cannot be properly invoked |
> | Microsoft Edge | ❌ | CDP implementation differs; some interfaces are incompatible |
> | Chromium Open Source Version | ⚠️ | May work, but not fully tested |
> | Other Chromium-based browsers | ❌ | Most have trimmed CDP protocol support |
>
> If Google Chrome and other Chromium-based browsers are installed simultaneously on your system, ensure:
>
> 1. Close **all Chrome windows** before launching debugging mode.
> 2. Do **not** use 360 Speed Browser to execute the `--remote-debugging-port` command.
> 3. You can click “Test Connection” on the scraping page to verify whether the connected browser is Google Chrome.

### Chrome Path Configuration

Chrome’s installation path may vary depending on the device. You can customize it via the following methods:

**Method One: Environment Variables (Recommended)**

Set in `backend/.env`:

```bash
# Path to the Chrome executable
CHROME_EXECUTABLE="C:/Program Files/Google/Chrome/Application/chrome.exe"

# Dedicated user data directory for scraping (isolated from daily Chrome usage to avoid conflicts)
CHROME_USER_DATA_DIR="C:/Users/Administrator/Desktop/chrome-scraper-profile"

# Debugging port (default: 9222; usually no need to change)
CHROME_DEBUG_PORT=9222
```

**Method Two: Modify Configuration File**

Directly edit the default values in the `Settings` class within `backend/app/config.py`:

```python
chrome_executable: str = "C:/Program Files/Google/Chrome/Application/chrome.exe"
chrome_user_data_dir: str = "C:/Users/Administrator/Desktop/chrome-scraper-profile"
chrome_debug_port: int = 9222
```

> **Common Chrome Installation Paths:**
>
> - Windows Default: `C:/Program Files/Google/Chrome/Application/chrome.exe`
> - Windows User Installation: `C:/Users/<username>/AppData/Local/Google/Chrome/Application/chrome.exe`
> - macOS: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`
> - Linux: `/usr/bin/google-chrome`

## Technology Stack

| Layer | Technology |
| ------ | ------ |
| Backend | Python 3.12 + FastAPI + SQLAlchemy async + SQLite |
| Web Frontend | Vue 3 + Vite + TypeScript + Pinia + Naive UI |
| Mobile | React Native (Expo) + Zustand |
| Browser Extension | Chrome Extension Manifest V3 |
| AI Inference | Ollama + Qwen3-VL:8B-Instruct (local GPU) |
| Scraping Engine | Playwright + CDP connection to real Chrome (undetectable scraping) |

| Module | Function |
| ------ | ------ |
| **Material Library** | Waterfall browsing, multi-dimensional filtering (source/media/status/tags/main color), sorting (including random/tag count), density adjustment, pagination, batch multi-selection operations (favorite/move to trash/add tags/edit metadata), persistent browsing mode/density/number per page |
| **Advanced Search** | Keyword search, tag filtering (AND/OR), co-occurrence recommendations, advanced filtering (source/media/date), sorting (match priority), search history, pagination, density adjustment, semantic search (text), image search by upload, `/` focus and Esc exit, copy search link, persistent filter status |
| **Upload Materials** | Drag-and-drop/paste/URL import, preview queue (video previews available), upload progress and speed, quick tagging, metadata presets, deduplication detection, folder batch upload, queue management (clear with confirmation), persistent preference settings, 500 upload limit validation |
| **Material Details** | Large image preview (lightbox left/right navigation/zoom), tag display, outfit mega-tags (manual selection/new + AI suggestions one-click import), similar material recommendations (can be favorited/deleted), re-analysis, download original image, copy original link, tag click-to-search, move to trash (with optional deletion reason: poor quality/duplicate/dislike/privacy/other/AI-generated), five-star rating (alongside favorites, filterable/sortable in list), **Face Recognition (Blogger Feature Library Matching)** (detect & match → auto-associate fashion bloggers / suspected unknown faces → manually assign or unlink; requires registering the blogger's face first on the blogger detail page) |
| **Collection Management** | Xiaohongshu CDP zero-detection scraping + Douyin standalone browser scraping, task pagination/platform/status filtering/sorting, cancel/resume (resume from breakpoint)/copy and re-scrape, log viewing, funnel visualization, result preview (batch delete/load more/view details), cookie management (status/expiration/import/delete), Chrome lifecycle management, scheduled scraping (CRUD plan/start/stop/immediate execution), dashboard statistics (platform distribution/daily trends), URL tombstone table + content MD5 deduplication, persistent filtering/sorting/tab state |
| **Tag Management** | Group browsing/search/filtering, pin + custom drag-and-drop sorting, alias normalization (AI identifies synonyms and auto-merges), batch category change/rename/merge/delete (with confirmation), duplicate scan, drag-and-drop category change, batch tagging, tag notes, co-occurrence relationship graph + usage trends, import/export, material association preview, persistent column width |
| **AI Model Management** | Model list/download/switch, text embedding model management (annotation/one-click download/switch), GPU memory monitoring, batch analysis (asynchronous task queue), history pagination, multi-select batch operations, analysis result comparison, queue visualization, parameter tuning (model-isolated + default value restore + clear override), data reset, quality review (binary classification: qualified/unqualified + re-review, asynchronous), negative sample initial screener (status/metrics/training/rollback), shortcuts (Enter to download/Ctrl+S to save) |
| **Material Management** | Backend management dashboard partitioned by small menus (sub-page states persisted via URL, retained on refresh): Overview (statistics/distribution/largest file), AI suspicion re-review (select to batch delete or re-label as non-AI, hover card click 👁 to view details), batch cleanup (untagged/analysis failed), data integrity check, duplicate file detection and deduplication, near-duplicate detection (perceptual hash grouping + full-library random sampling + side-by-side preview + manual confirmation for deletion, hash cache gradually filled for near-instant scanning), vector backfill (one-click complete missing image vectors), Trash (soft-delete material recovery/permanent deletion/clear, default not auto-recycle), Data Insights (CSV export/new trend charts/person frequency ranking/operation audit logs), **Mobile Image Cropping** (scan manually uploaded vertical screenshots → manual selection confirmation → one-click crop status bar/bottom navigation area: auto black border detection / fixed ratio dual mode + screenshot feature confidence grading; original image auto-backup + vector backfill; skip materials support precise location jump in material library; when cropped result matches existing material, side-by-side comparison shown, user decides which to keep — physical deletion of duplicates allowed) |
| **Person Management** | **Dedicated Tab Management for Fashion Bloggers / Professional Models** (two types physically split into separate tables and APIs, business logic independently evolved): List (name search/platform filtering/sorting), create/edit/delete (deletion only allowed if no associated materials), popularity ranking, style profile (high-frequency tags/category distribution/trends), material association (add/remove materials by blogger/model in detail page sections), **Blogger CSV Import** (Upsert by Xiaohongshu ID), **Model Photo Groups** (select folder to import entire group to selected model, photo group browsing/lightbox/delete, SHA-256 deduplication within group), **Blogger Face Registration** (upload 1~5 clear frontal photos to register/re-register; the material face auto-matching depends on this feature library; professional models do not have this face capability) |
| **Browser Extension** | One-click extraction of fashion images from web pages; each scraping session automatically generates task records, viewable in Collection Management page for plugin history, results, and funnel |

## Quick Start

### 1. Install Dependencies

```bash
# Backend (Python)
cd backend
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
# Optional: test dependencies for automated testing
pip install -r requirements-dev.txt -i https://mirrors.aliyun.com/pypi/simple/
```

# Node.js Frontend
cd ../web
npm install

### 2. Install AI Models

```bash
# Install Ollama (download Windows version from ollama.com)

# Recommended: Qwen3-VL:8B-Instruct (officially maintained, 256K context, 32-language OCR)
ollama pull qwen3-vl:8b-instruct

# Alternative: MiniCPM-V:8b (smaller size, faster speed)
ollama pull minicpm-v:8b
```

After installation, switch the active model on the AI model management page.

### 3. Start Services

```bash
# Backend (default port 18888, can be modified via PORT environment variable or .env)
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 18888 --reload

# Web Frontend (default port 17777, can be modified via VITE_FRONTEND_PORT environment variable)
cd web
npm run dev

# Task Queue Worker (handles asynchronous "batch analysis" tasks, needs to be started in a separate terminal)
cd ../backend
python -m app.worker

# Face recognition sub-service face-service (standalone Python 3.10 environment running InsightFace, port 18889;
# provides blogger face registration / material face matching; face features degrade gracefully when not running)
cd ../face-service
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 18889
```

Open `http://localhost:17777` in your browser.

> One-click restart: `bash scripts/restart.sh` will automatically stop old processes and simultaneously launch the backend, frontend, worker, and face recognition sub-service, with readiness checks.
>
> Auto-start: run `bash scripts/ensure-services.sh` manually — "health checks + only starts missing services," idempotent and lock-protected. Service start/restart is manual: the coding agent will not run these scripts automatically and will prompt you when a restart is needed.

**Custom Ports:**

```bash
# Backend .env
PORT=18888                   # Backend listening port

# Frontend .env (web/.env)
VITE_FRONTEND_PORT=17777     # Frontend development server port
VITE_BACKEND_URL=http://localhost:18888  # Backend API address
```

### 4. Start Scraping Engine (Optional)

The scraping engine connects to real Chrome browsers via CDP to achieve undetectable scraping.

> **Prerequisite:** Must first install Google Chrome and complete [Chrome Path Configuration](#chrome-Path-Configuration).

**Start Debugging Chrome:**

Run the following command in the terminal (port and directory must match your `.env` configuration):

```bash
"C:/Program Files/Google/Chrome/Application/chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:/Users/Administrator/Desktop/chrome-scraper-profile"
```

> If Chrome prompts "Cannot create user data in this directory," please close all open Chrome windows first, then retry.

**Create a Scraping Task in the Web Interface:**

1. Log in to Xiaohongshu (`xiaohongshu.com`) in the debugging Chrome window.
2. Open the scraping page and confirm CDP mode is enabled.
3. Click the "Test Connection" button to confirm "Connected" is displayed.
4. Enter keywords and click "Start Scraping."

> **Douyin Scraping:** Douyin tasks do not require CDP Chrome — the backend uses an independent Playwright browser to scrape the web version search results (strict anti-crawling, results may be empty, page recommends browser extensions).
>
> **Sorting Notes:** "Latest/Hot" sorting only applies to Xiaohongshu search mode; Douyin web version uses fixed comprehensive sorting.
>
> **Scheduled Scraping:** Go to the "Scraping Management → Scheduled Scraping" tab to create plans that run at intervals (1 hour to weekly), scheduled by the backend to check for triggers every 30 seconds; Xiaohongshu scheduled tasks depend on the debugging Chrome remaining active (you can click "Start Chrome" in the task form, which will be pulled up by the backend).

### 5. Install Browser Extension

1. Open `chrome://extensions` in Chrome.
2. Enable "Developer mode."
3. Click "Load unpacked extension."
4. Select the `browser-extension/` directory.

### 6. Start Mobile App (Optional)

```bash
cd mobile
npx expo start
```

## Project Structure

```
fashion-inspo/
├── CLAUDE.md                     # Coding standards and project guidelines
├── README.md                     # This file
├── TODO.md                       # Feature todo list
│
├── backend/                      # Python backend
│   ├── .env                      # Environment variables
│   ├── requirements.txt          # Python dependencies
│   ├── app/
│   │   ├── main.py               # FastAPI entry point
│   │   ├── config.py             # Configuration management
│   │   ├── database.py           # Database engine
│   │   ├── worker.py             # Task queue worker (python -m app.worker)
│   │   ├── models/               # Data models
│   │   │   ├── inspiration.py    # Fashion inspiration + AI analysis logs
│   │   │   ├── tag.py            # Tags + aliases (including source identifiers)
│   │   │   ├── person.py         # Person model (Blogger/Model dual tables + inspiration association + model photo sets/photos + face feature relationships)
│   │   │   ├── face.py           # Face feature library (blogger face embeddings + inspiration face detections)
│   │   │   ├── scraper.py        # Scraping tasks + scheduled scraping plans
│   │   │   ├── task.py           # Async task queue
│   │   │   └── audit.py          # Operation audit logs
│   │   ├── schemas/              # Pydantic request/response schemas
│   │   ├── routers/              # API routes
│   │   │   ├── inspirations.py   # Inspiration CRUD
│   │   │   ├── tags.py           # Tag management + batch/statistics/scanning/sorting/alias/import/export
│   │   │   ├── search.py         # Multi-dimensional search + similar inspirations
│   │   │   ├── bloggers.py       # Blogger management (includes CSV import and blogger face registration)
│   │   │   ├── models.py         # Professional model management + photo sets (model photoshoots)
│   │   │   ├── ai.py             # AI route aggregation (split into ai_*.py)
│   │   │   ├── ai_shared.py      # AI shared state + background tasks
│   │   │   ├── ai_models.py      # Model management + GPU + model statistics
│   │   │   ├── ai_analysis.py    # Analysis + queue + history + comparison
│   │   │   ├── ai_quality.py     # Quality review
│   │   │   ├── ai_settings.py    # Prompt + parameter tuning
│   │   │   ├── ai_dashboard.py   # Analysis quality dashboard
│   │   │   ├── ai_outfit.py      # Large tag suggestions for outfits
│   │   │   ├── ai_reset.py       # Data reset
│   │   │   ├── scraper.py        # Scraping management
│   │   │   ├── admin.py          # Admin dashboard (statistics, deduplication, completeness checks)
│   │   │   ├── tasks.py          # Task queue (list/detail/cancel)
│   │   │   ├── files.py          # Static files
│   │   │   └── ws.py             # WebSocket
│   │   ├── services/             # Business logic
│   │   │   ├── ai_service/       # AI orchestration (analyze / quality / outfit_summary / common)
│   │   │   ├── ai_parser.py      # AI response parsing/repair (malformed handling)
│   │   │   ├── ai_tag_saver.py   # Tag standardization/save/association
│   │   │   ├── ai_analysis_service.py  # Analysis/queue/history business logic
│   │   │   ├── inspiration_service.py  # Inspiration CRUD business logic
│   │   │   ├── tag_service.py    # Tag CRUD + merge + preset import + similarity
│   │   │   ├── person_service.py # Base class for blogger/model services (PersonServiceBase) + style profile + photo sets/photos
│   │   │   ├── blogger_face.py   # Blogger face registration (average pooling) + inspiration face detection & matching
│   │   │   ├── face_client.py    # Face recognition sub-service HTTP client (face-service)
│   │   │   ├── scraper_service.py    # Scraping orchestration + scheduled scheduling + plugin task logging
│   │   │   ├── file_service.py   # File management
│   │   │   ├── audit_service.py

## System Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Local PC (Windows 11)                     │
│                                                               │
│  ┌───────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │ Vue 3 Web App │  │ Browser Extension│  │ React Native │  │
│  │(Desktop browser)  │  │ (One-click Scraping)        │  │  │
│  │  └───────┬───────┘  └────────┬─────────┘  └──────┬───────┘  │
│          │                   │                    │          │
│          └───────────────────┼────────────────────┘          │
│                              │ HTTP/WebSocket                │
│                              ▼                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │               FastAPI Backend (:18888)                │    │
│  │                                                       │    │
│  │  REST API │ WebSocket │ Background Tasks             │    │
│  │  ─────────────────────────────────────────────        │    │
│  │  Scraper Mgr │ AI Service │ File Service            │    │
│  │  Tag Service │ Auth Middleware                       │    │
│  │  Scheduled Scraping │ Automatic Trash Cleanup        │    │
│  └──────────────────────────┬──────────────────────────┘    │
│                              │                               │
│  ┌──────────────────────────┼──────────────────────────┐    │
│  │   SQLite (Metadata)          │  Storage/ (Image Files)      │    │
│  └──────────────────────────┴──────────────────────────┘    │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │          Ollama (GPU: RTX 5060Ti 16GB)               │    │
│  │  Qwen3-VL:8B-Instruct — Fashion Visual Analysis & Tag Extraction │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Face-service (:18889) — InsightFace on Python 3.10  │    │
│  │  Blogger Face Registration & Material Face Matching  │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │     Chrome CDP (:9222) — Connection to Real Browser for Scraping │    │
│  │     Zero-detection Search on Xiaohongshu/Douyin → Auto-download & Store Images │    │
│  └─────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

## Data Model

| Table | Description | Key Fields |
| ---- | ------ | ---------- |
| `inspirations` | Outfit inspiration materials | id, source_type, file_path, media_type, dominant_colors, rating (user rating 0~5), quality_status, quality_reason, is_ai_generated, deleted_at, trash_reason |
| `tags` | Tags | id, name, category, source (seed/ai_generated/manual), pinned, sort_order, description |
| `tag_aliases` | Tag aliases | id, tag_id, alias — synonym normalization (AI recognizes aliases and auto-merges to main tag) |
| `inspiration_tags` | Inspiration-tag associations | inspiration_id, tag_id, confidence |
| `ai_analysis_log` | AI analysis logs | inspiration_id, model_name, log_type, raw_response, processing_time_ms, error |
| `scraper_tasks` | Scraping tasks | platform, status, items_found/added, diagnostics (scraping funnel logs), resume_token (resumption progress) |
| `scraper_seen_urls` | URL tombstone table | source_url (PK), created_at — prevents duplicate scraping after deletion |
| `scraper_schedules` | Scheduled scraping plans | platform, keywords, max_count, sort_mode, enabled, interval_minutes, next_run_at, last_task_id, run_count |
| `task_queue` | Async task queue | type (batch_analyze/quality_check/batch_delete/deduplicate/vector_backfill), status (pending/running/success/failed/cancelled), progress, total/done, result, error, retry_count, next_retry_at |
| `pending_vector_backfills` | Pending vector backfill batching table | inspiration_id, type (image/text), status, attempts — enqueue when inspiration is uploaded or tags change; worker batches and rebuilds vectors (avoids creating small tasks per item) |
| `audit_logs` | Operation audit logs | id, action (batch_delete/delete_rejected/cleanup_orphans/empty_trash/batch_trash), target_type, count, freed_bytes, detail, created_at — traces destructive batch operations |
| `bloggers` | Outfit bloggers | id, name, platform, platform_user_id, xhs_id (unique), ip_location, profile_url, avatar_path, bio, source, created_at, updated_at |
| `models` | Professional models | Same as bloggers (no person_type field — table is the type) |
| `inspiration_bloggers` | Inspiration-blogger many-to-many associations | inspiration_id, blogger_id, confidence — records AI confidence for "who is in the picture" recognition |
| `inspiration_models` | Inspiration-model many-to-many associations | inspiration_id, model_id, confidence |
| `model_photo_sets` | Model photo sets | id, model_id, name (set name, default is folder name), created_at, updated_at |
| `model_photos` | Model photos | id, set_id, file_path, thumbnail_path, content_hash (SHA-256 deduplication within set), sort_order, created_at |
| `blogger_face_embeddings` | Blogger face feature library | id, blogger_id (unique), embedding (512-dim float32, average pooling), updated_at — the material face auto-matching depends on this library (models have no face capability) |
| `inspiration_face_detections` | Inspiration face detections | id, inspiration_id, face_index (index within image), embedding, matched_blogger_id (matched blogger; null = suspected unknown face), confidence (cosine similarity), created_at |

### Tag Category System

| Category | Example | Description |
| -------- | -------- | ------------ |
| `style` | JK制服, 汉服, Y2K, 法式, 新中式 | Style system |
| `item_type` | 百褶裙, 过膝袜, 西装外套, 马丁靴 | Item type |
| `color` | 白色, 海军蓝, 酒红, 格纹 | Color |
| `body_part` | 过膝, 高腰, V领, 拖地 | Wear style |
| `fit` | 宽松, 修身, Oversized, 直筒 | Fit type |
| `season` | 春季, 夏季, 秋季, 冬季 | Season |
| `attribute` | 露脸, 全身, 对镜自拍, 叠穿 | Image attributes |
| `outfit` | 御姐长腿高跟鞋穿搭, 白色系穿搭, 网球穿搭 | Outfit tags (curated layer: manual + AI summary, "宁缺毋滥") |

### Tag Source Indicators

| source | Meaning | Color Marker |
| -------- | -------- | :---: |
| `seed` | Pre-set tags (imported during system initialization) | Gray |
| `ai_generated` | Automatically extracted by AI analysis | Purple |
| `manual` | Created or imported manually by user | Blue |

### Database Migration (Alembic)

The database schema is managed by Alembic (`backend/alembic/`). When the backend starts, it automatically calls `run_migrations()`: for a completely new empty database, it executes baseline table creation; for historical databases, it automatically `stamp`s to the baseline; for already-managed databases, it performs incremental upgrades. Workers do not run Alembic (concurrent startup would compete for SQLite write locks); instead, they only perform `create_all` + `ensure_schema()` (hand-written column additions) as a fallback.

**When adding new fields or tables** (no longer manually appending to `_SCHEMA_COLUMNS` in `db_migrations.py`):

```bash
cd backend
# 1. Modify ORM models and generate migration scripts (compare model differences with the database)
alembic revision --autogenerate -m "description"

# 2. Apply to the database (or restart backend to auto-execute)
alembic upgrade head
```

> For generating baseline or testing, use the environment variable `ALEMBIC_DB_URL` to point to a temporary database, avoiding touching real data.

## API Overview

### Material Management

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/inspirations` | Inspiration list (pagination; supports filtering by source/media/status/quality/tags/main color/date; `ids` comma-separated for exact targeting; sorting includes `tag_count`) |
| `POST` | `/api/inspirations` | Upload inspiration |
| `POST` | `/api/inspirations/from-url` | Import inspiration from URL (main channel for browser extension scraping; server-side download to bypass CORS; supports `source_platform_id`/`scraper_task_id`) |
| `GET` | `/api/inspirations/{id}` | Inspiration detail |
| `PATCH` | `/api/inspirations/{id}` | Update inspiration |
| `POST` | `/api/inspirations/{id}/trash` | Move to trash (soft delete; optional `reason`: quality issue/duplicate/dislike/privacy/other/AI-generated) |
| `POST` | `/api/inspirations/{id}/restore` | Restore from trash |
| `GET` | `/api/inspirations/trash` | Trash inspiration list (pagination; supports filtering by `reason`) |
| `DELETE` | `/api/inspirations/trash` | Clear trash (with `only_expired=true` to clear only expired inspirations) |
| `DELETE` | `/api/inspirations/{id}` | Permanent delete (hard delete, irreversible; use `/trash` for soft delete) |
| `POST` | `/api/inspirations/{id}/tags` | Manually associate tags with inspiration (search/create by name, e.g., "Fashion" as a major tag) |
| `DELETE` | `/api/inspirations/{id}/tags/{tag_id}` | Remove tag association from inspiration |
| `POST` | `/api/inspirations/batch-tags` | Batch associate tags with multiple inspirations (search/create by name) |
| `POST` | `/api/inspirations/batch-favorite` | Batch favorite/unfavorite inspirations |
| `POST` | `/api/inspirations/batch-trash` | Batch move to trash (soft delete) |
| `POST` | `/api/inspirations/batch-update` | Batch edit metadata (source/favorite/audit status/suspicious AI flag) |
| `GET` | `/api/inspirations/dominant-colors` | List of dominant colors in library (hex + count, for color filtering) |
| `POST` | `/api/inspirations/{id}/face-detect` | Detect faces in an inspiration and match against the blogger feature library (re-detection overwrites old results; requires face-service running) |
| `GET` | `/api/inspirations/{id}/face-detections` | List of face detections for an inspiration (with matched blogger and confidence) |
| `PUT` | `/api/inspirations/{id}/face-detections/{det_id}` | Manually assign/unlink the blogger association for a face detection (body: `{"blogger_id": 5}` or `{"blogger_id": null}`) |
| `DELETE` | `/api/inspirations/{id}/face-detections/{det_id}` | Delete a single face detection record |

### Search

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/search` | Multi-dimensional search (keywords + tags + colors + date + source + media) |
| `GET` | `/api/search/similar/{id}` | Similar inspiration recommendations (image vector + tag weighting, visual/tag/mixed source) |
| `GET` | `/api/search/suggestions?q=` | Auto-complete for tag names |
| `GET` | `/api/search/tag-cooccurrence?tag_name=` | Tag co-occurrence analysis |
| `POST` | `/api/search/vector` | Semantic search / image search (multipart: `text` or `file` + `top_k`) |
| `GET` | `/api/search/vector/status` | Vector search capability status (LanceDB/text/image vectors/total count) |
| `POST` | `/api/search/vector/backfill` | Backfill vector embeddings for existing inspirations (`mode`=all/text/image, `limit`=maximum number of items) |

> **Vector Search Configuration:** For semantic text search, use Ollama's `all-minilm` (zero additional dependencies). For image-to-image search or visual similarity, install CLIP additionally:
>
> ```bash
> pip install sentence-transformers   # includes torch, heavier
> export HF_ENDPOINT=https://hf-mirror.com   # use mirror for downloading CLIP models in China
> python scripts/backfill_vectors.py --mode all   # bulk backfill (first-time download of clip-ViT-B-32 ~600MB)
> ```
>
> Without CLIP installed, the image-to-image search API returns 503, and similar recommendations degrade to pure tag matching; all other features remain unaffected.

### Tag Management

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/tags` | List of tags (grouped by category) |
| `POST` | `/api/tags` | Create a new tag |
| `PATCH` | `/api/tags/{id}` | Edit tag (rename, change category, pin, reorder, add note) |
| `DELETE` | `/api/tags/unused` | Delete all unused tags |
| `POST` | `/api/tags/batch-delete` | Batch delete tags |
| `POST` | `/api/tags/merge` | Merge tags |
| `GET` | `/api/tags/suggestions/{name}` | Suggest deduplication when creating |
| `PATCH` | `/api/tags/batch-category` | Batch update tag categories |
| `PATCH` | `/api/tags/batch-rename` | Batch rename (find and replace) |
| `GET` | `/api/tags/stats` | Tag statistics (total, unused, source distribution) |
| `GET` | `/api/tags/duplicates` | Scan for similar tags |
| `GET` | `/api/tags/{id}/inspirations` | Materials associated with this tag |
| `POST` | `/api/tags/{id}/inspirations/batch-remove` | Batch remove association between tag and materials |
| `GET` | `/api/tags/export` | Export all tags as JSON |
| `POST` | `/api/tags/import` | Batch import tags |
| `POST` | `/api/tags/reorder` | Batch update custom sorting |
| `GET` | `/api/tags/aliases` | List of tag aliases |
| `POST` | `/api/tags/{id}/aliases` | Add an alias to a tag |
| `DELETE` | `/api/tags/aliases/{id}` | Delete a tag alias |
| `GET` | `/api/tags/cooccurrence-network` | Tag co-occurrence network (nodes + weighted edges) |
| `GET` | `/api/tags/top` | Popular tags ranking |
| `GET` | `/api/tags/{id}/trend` | Usage trend of tag (daily/weekly/monthly) |

### Person Management (Dress Bloggers / Professional Models Split)

> **Splitting Note:** The original single table `persons` (distinguished by `person_type`) has been physically split into two independent tables and APIs: `bloggers` and `models`. Material associations have also been split into `inspiration_bloggers` and `inspiration_models`. Model photo sets are now managed under `model_photo_sets` (see "Data Models"). The frontend person management page features dual tabs: "Dress Bloggers" and "Professional Models". All associations use IDs (names are not unique to avoid ambiguity from duplicates).

#### Dress Bloggers `/api/bloggers`

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/bloggers` | Blogger list (pagination / name search / platform filter / sorting) |
| `POST` | `/api/bloggers` | Create a fashion blogger |
| `GET` | `/api/bloggers/{id}` | Blogger details (includes material count and style profile: frequent tags / category distribution / trends) |
| `PATCH` | `/api/bloggers/{id}` | Update blogger (explicitly passing `null` clears nullable fields) |
| `DELETE` | `/api/bloggers/{id}` | Delete blogger (requires API Key; deletion only allowed if no associated materials) |
| `GET` | `/api/bloggers/{id}/inspirations` | List of materials for this blogger (pagination + sorting) |
| `GET` | `/api/bloggers/top` | Top bloggers ranking (by material count) |
| `GET` | `/api/bloggers/suggestions` | Suggest bloggers by name (for deduplication selection) |
| `POST` | `/api/bloggers/import-csv` | Upload CSV to batch import bloggers (upsert by xhs_id, nickname and Xiaohongshu ID required) |
| `POST` | `/api/bloggers/{id}/face` | Register/re-register a blogger's face (1~5 frontal photos; re-registration overwrites old features; requires face-service running) |
| `GET` | `/api/bloggers/{id}/face` | Query a blogger's face registration status |
| `POST` | `/api/inspirations/{id}/bloggers` | Batch associate bloggers with material (idempotent) |
| `DELETE` | `/api/inspirations/{id}/bloggers/{bid}` | Remove blogger association from material |

#### Professional Models `/api/models`

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/models` | Model list (pagination / name search / platform filter / sorting) |
| `POST` | `/api/models` | Create a professional model |
| `GET` | `/api/models/{id}` | Model details (includes material count and style profile) |
| `PATCH` | `/api/models/{id}` | Update model (explicitly passing `null` clears nullable fields) |
| `DELETE` | `/api/models/{id}` | Delete model (requires API Key; deletion only allowed if no associated materials) |
| `GET` | `/api/models/{id}/inspirations` | List of materials for this model (pagination + sorting) |
| `GET` | `/api/models/top` | Top models ranking (by material count) |
| `GET` | `/api/models/suggestions` | Suggest models by name (for deduplication selection) |
| `POST` | `/api/inspirations/{id}/models` | Batch associate models with material (idempotent) |
| `DELETE` | `/api/inspirations/{id}/models/{mid}` | Remove model association from material |

### Model Photo Sets (Model Photo Collections)

Model photo sets are separated from fashion materials: model photos are considered photo portfolios and do not enter the material library, nor participate in AI tagging or search. They are browsed only via “Model → Photo Set → Photo”. Files are stored independently in `person_photos/` to avoid being mistakenly flagged as orphaned files by integrity checks.

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/models/{id}/photo-sets` | Photo set list (paginated, includes photo count and cover) |
| `POST` | `/api/models/{id}/photo-sets` | Create a photo set (defaults to "Unnamed Photo Set" if name is missing) |
| `GET` | `/api/models/{id}/photo-sets/{set_id}` | Photo set details (includes paginated photo list) |
| `PATCH` | `/api/models/{id}/photo-sets/{set_id}` | Rename the photo set |
| `DELETE` | `/api/models/{id}/photo-sets/{set_id}` | Delete the photo set (cascading deletion of photos and physical files) |
| `POST` | `/api/models/{id}/photo-sets/{set_id}/photos` | Upload a single photo to the photo set (SHA-256 content deduplication within the set) |
| `DELETE` | `/api/models/{id}/photo-sets/{set_id}/photos/{photo_id}` | Delete a single photo from within the photo set |

### AI Analysis

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/ai/status` | AI service status (Ollama connection/version/active vision and embedding models) |
| `GET` | `/api/ai/models` | List of installed models (annotated vision/text embedding roles) |
| `POST` | `/api/ai/models/pull` | Download model (SSE progress) |
| `PUT` | `/api/ai/models/active` | Switch active vision model |
| `PUT` | `/api/ai/models/embedding-active` | Switch active text embedding model (vector retrieval text side) |
| `DELETE` | `/api/ai/models/{name}` | Delete model |
| `POST` | `/api/ai/analyze/{id}` | Trigger single analysis |
| `POST` | `/api/ai/batch-analyze` | Batch analysis (create async task, return task_id, executed by worker) |
| `POST` | `/api/ai/outfit-tags/suggest` | AI suggests outfit macro-tags (only suggestions, not stored) |
| `POST` | `/api/ai/retry/{id}` | Retry failed analysis |
| `GET` | `/api/ai/queue` | Analysis queue statistics |
| `GET` | `/api/ai/unanalyzed-ids` | List of unanalyzed material IDs |
| `GET` | `/api/ai/active-analyses` | Currently active tasks |
| `GET` | `/api/ai/history` | Analysis history (pagination/filtering) |
| `GET` | `/api/ai/history/{id}` | Analysis details (including tags, structured snapshot, quality review, version info) |
| `DELETE` | `/api/ai/history/{id}` | Delete single log entry |
| `DELETE` | `/api/ai/history/failed/all` | Delete all failed logs |
| `POST` | `/api/ai/history/batch-delete` | Batch delete analysis records |
| `POST` | `/api/ai/history/batch-retry` | Batch retry analysis |
| `GET` | `/api/ai/history/model-names` | List of historical model names |
| `GET` | `/api/ai/gpu-stats` | GPU memory monitoring |
| `POST` | `/api/ai/unload-model` | Unload model to free GPU memory |
| `GET` | `/api/ai/queue/pending` | Pending materials (with thumbnails) |
| `DELETE` | `/api/ai/queue/{id}` | Cancel queued task |
| `POST` | `/api/ai/queue/pause` | Pause queue |
| `POST` | `/api/ai/queue/resume` | Resume queue |
| `GET` | `/api/ai/compare/{id}` | Analysis result comparison (structured tag differences + duration + version info) |
| `GET` | `/api/ai/quality-dashboard` | Analysis quality dashboard (coverage/trends/problematic materials) |
| `GET` | `/api/ai/model-stats` | Usage statistics grouped by model (success rate/average duration/average tag count, tags by structured snapshot metric) |
| `GET` | `/api/ai/prompt` | Get current model's prompt (model-isolated) |
| `PUT` | `/api/ai/prompt` | Update current model's prompt |
| `GET` | `/api/ai/prompt/versions` | Prompt version history |
| `POST` | `/api/ai/prompt/save-version` | Save current prompt as a version |
| `POST` | `/api/ai/prompt/rollback` | Rollback prompt to specified version |
| `POST` | `/api/ai/test-analyze` | Single-image test analysis (SSE, not persisted) |

> **Structured Storage of AI Analysis Results (Multi-Version Comparison & Traceability):**
>
> Each analysis/review's structured result is stored as an independent data table, decoupled from the current state of the material (full tag set, `quality_status`), supporting historical traceability across different models/Prompt versions:
>
> | Table | Description |
> | ------ | ------ |
> | `ai_extracted_tags` | Snapshot of which tags were extracted during a single analysis (log_id + tag_id + confidence) |
> | `ai_quality_review` | Judgment from a single review (result / reason / reviewed_at) |
>
> `ai_analysis_log` now includes `prompt_version` (first 8 characters of the Prompt content hash) and `model_version` fields. `GET /api/ai/history/{id}` returns `structured_tags` / `quality_reviews` / version fields; `GET /api/ai/compare/{id}` calculates tag differences based on structured snapshots precisely (existing logs automatically fall back to real-time parsing).
>
> Historical data backfill (one-time migration, parsed from `raw_response` and written into snapshots and version fields):
>
> ```bash
> cd backend
> python scripts/backfill_structured.py            # Preview how many records will be processed
> python scripts/backfill_structured.py --apply    # Actually write the data
> ```
>

### Quality Review

| Method | Path | Description |
| ------ | ------ | ------ |
| `POST` | `/api/ai/quality-check` | Batch review all pending image materials (asynchronous task, returns `task_id`) |
| `POST` | `/api/ai/quality-recheck` | Re-review all approved materials: reset to pending and rejudge with latest standards (asynchronous task, returns `task_id`) |
| `GET` | `/api/ai/quality-stats` | Quality review statistics (pending / approved / rejected / approval rate) |
| `GET` | `/api/ai/manual-upload-auto-approve` | Get the "manual upload auto-approve" configuration |
| `PUT` | `/api/ai/manual-upload-auto-approve` | Set "manual upload auto-approve" (`enabled=true/false`, optionally persist to .env) |
| `DELETE` | `/api/inspirations/quality-rejected` | Move all rejected materials into the trash (soft delete, recoverable) |

> **Review Criteria:** A material is deemed "qualified" only if it is a complete, clear photo of a real person wearing an outfit. Unqualified materials include: no person (flat lays, size charts, ads, pure text), single-item close-ups, partial/cropped close-ups (e.g., only legs/feet/arms/necks), overly cropped compositions.

> **Manual Upload Auto-Approval:** Default is enabled (configuration item `manual_upload_auto_approve`, corresponding to .env's `MANUAL_UPLOAD_AUTO_APPROVE`). When enabled, manually uploaded materials are directly marked as "approved" and do not enter the pending queue; when disabled, they revert to pending status. Can be toggled with one click in the "AI Model Management → Quality Review" panel.

### Negative Sample Pre-filter (Quality Review Pre-screening)

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/ai/quality-learner/status` | Pre-filter status + current positive/negative sample statistics |
| `POST` | `/api/ai/quality-learner/train` | Train/retrain sklearn classifier using positive/negative samples (returns metrics) |
| `POST` | `/api/ai/quality-learner/reset` | Delete the model and roll back to pure VLM review |

> **Note:** The initial screening tool trains a lightweight logistic regression model using CLIP image vectors (512-dimensional, stored in LanceDB) derived from negative samples labeled "quality poor" in the trash, rejected materials, and approved positive samples. This serves as a pre-screening step for quality review: high-confidence junk is directly rejected, while low-confidence items proceed to VLM re-review ("宁缺毋滥" — better to miss than to include falsely). Thresholds are defined in `quality_classifier_threshold`, and the manual override mechanism remains unchanged. The "AI Model Management → Quality Review" page includes panels for status, metrics, training, and rollback, and can also be operated via the script `python scripts/quality_learner.py status|train|reset`.

### Task Queue

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET` | `/api/tasks` | Task list (paginated, filterable by status/type) |
| `GET` | `/api/tasks/{id}` | Task details (for frontend polling) |
| `POST` | `/api/tasks/{id}/cancel` | Cancel a task in the queue (only pending tasks can be canceled) |

> All heavy operations have been refactored into **database-driven asynchronous tasks**: the API immediately returns a `task_id`, which is then executed serially by an independent worker process (`python -m app.worker`), with automatic retries (2 times, exponential backoff). If the worker is not running, the task remains stuck in "Queued".
>
> | type | Trigger API | Description |
> | ---- | ----------- | ----------- |
> | `batch_analyze` | `POST /api/ai/batch-analyze` | Batch AI analysis |
> | `quality_check` | `POST /api/ai/quality-check` / `quality-recheck` | Batch quality review / re-review |
> | `batch_delete` | `POST /api/admin/batch-delete` | Batch deletion of materials |
> | `deduplicate` | `POST /api/admin/deduplicate` | Intelligent deduplication and deletion |
> | `vector_backfill` | Auto-enqueued on material upload or tag change | Vector backfill (batching mechanism: `pending_vector_backfills` table aggregates, worker rebuilds in batches, no longer creates small tasks per material) |

### AI Parameter Tuning

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET` | `/api/ai/settings` | Retrieve analysis parameters (with global defaults) |
| `PUT` | `/api/ai/settings` | Update parameters (confidence thresholds persist globally to `.env`; timeouts persist per model) |
| `GET` | `/api/ai/sampling-params` | Retrieve sampling parameters (with global defaults) |
| `PUT` | `/api/ai/sampling-params` | Update sampling parameters (persist per model to `model_configs.json`) |
| `DELETE` | `/api/ai/model-config` | Clear custom configuration for current model, revert to global defaults |
| `POST` | `/api/ai/retry-all-failed` | Retry all failed tasks (only for images) |
| `DELETE` | `/api/ai/reset?confirm=yes` | Reset all data and files (destructive endpoint, requires API Key) |

> **Reset Scope Note:** `/api/ai/reset` clears materials, tags, material-tag associations, analysis logs, structured snapshots/review results, scraping tasks, and URL tombstone tables, and deletes image thumbnails, videos, and vector database files. **Excludes** "People (dressing bloggers / professional models)", "scheduled scrapers (scraper_schedules)", "task queue (task_queue)", and "audit logs" — these management data persist after reset.
>
> **Note:** Video files are not currently processed by AI analysis. WebP images are automatically converted to JPEG for compatibility with the Qwen3-VL model.

### Scraping Management

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/scraper/sources` | Available scraping sources, status, and tombstone table counts |
| `GET` | `/api/scraper/stats?days=30` | Scraping statistics (total volume, success rate, platform distribution, daily trends) |
| `GET` | `/api/scraper/cdp-check/{port}` | Check if Chrome Debug Port is ready |
| `POST` | `/api/scraper/chrome/start` | Start scraping-specific Chrome (debug mode) via backend |
| `POST` | `/api/scraper/chrome/stop` | Stop scraping-specific Chrome |
| `GET` | `/api/scraper/chrome/status` | Connection status of scraping-specific Chrome |
| `GET` | `/api/scraper/cookie-status?platform=` | Cookie status (existence, expiration, validity) |
| `POST` | `/api/scraper/cookie-import` | Import platform cookies (JSON array) |
| `DELETE` | `/api/scraper/cookie/{platform}` | Delete platform cookies |
| `POST` | `/api/scraper/tasks` | Create scraping task (supports `sort_mode`; pre-check Chrome connection for Xiaohongshu CDP mode) |
| `GET` | `/api/scraper/tasks` | Task list (filter by `platform`/`status`, sort by `sort`, paginate with `page`/`size`, returns `items`/`total`/`stats`) |
| `DELETE` | `/api/scraper/tasks` | Clear all scraping task records |
| `DELETE` | `/api/scraper/tasks/{id}` | Delete a single task record (retain materials, clear associated references) |
| `POST` | `/api/scraper/tasks/{id}/cancel` | Cancel running task |
| `POST` | `/api/scraper/tasks/{id}/retry` | Retry single task (resume from breakpoint) |
| `POST` | `/api/scraper/tasks/retry-failed` | Retry all failed tasks |
| `GET` | `/api/scraper/tasks/{id}/log` | Task log (last 200 lines) |
| `GET` | `/api/scraper/tasks/{id}/results` | List of task output materials (paginated) |
| `POST` | `/api/scraper/tasks/{id}/results/batch-delete` | Batch delete task output materials |
| `POST` | `/api/scraper/extension-tasks` | Start plugin scraping session (create task record, return `task_id`) |
| `POST` | `/api/scraper/extension-tasks/{id}/complete` | End plugin session (aggregate discovered items, store in DB, mark as complete) |
| `GET` | `/api/scraper/schedules` | List of scheduled scraping plans |
| `POST` | `/api/scraper/schedules` | Create scheduled plan (platform, keyword, quantity, sort, interval, enabled) |
| `PATCH` | `/api/scraper/schedules/{id}` | Update plan (enable/disable, modify interval, keyword, etc.) |
| `DELETE` | `/api/scraper/schedules/{id}` | Delete scheduled plan |
| `POST` | `/api/scraper/schedules/{id}/run` | Execute scheduled plan immediately |

> **Resume Scraping:** Failed tasks can be "resumed," continuing from the execution plan stored in `resume_token` (keywords × sort order) at the point where they left off, without re-scraping already-included images.
>
> **Sort Scope:** `sort_mode` (`general`/`latest`/`popular`) only applies to Xiaohongshu search mode; Douyin web version always uses comprehensive sorting.
>
> **Scheduled Scraping:** Backend scheduler checks for expired plans every 30 seconds and creates tasks; disabling or changing intervals recalculates `next_run_at`; failed executions still proceed and can be traced via task records.
>
> **Plugin Task Records:** When browser plugins upload materials, they can include the `scraper_task_id` form field (`POST /api/inspirations`) to link materials to plugin scraping tasks, enabling preview and statistics; plugin sessions create and aggregate task records via the `extension-tasks` endpoints.

### Admin Backend

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/admin/stats` | Material overview statistics (including tombstone table counts) |
| `GET` | `/api/admin/largest-files` | Top 20 largest files |
| `GET` | `/api/admin/integrity-check` | Data integrity check (missing/isolated files) |
| `GET` | `/api/admin/duplicates` | File hash duplicate detection |
| `GET` | `/api/admin/check-duplicate?hash=` | Pre-upload deduplication (MD5 detection) |
| `POST` | `/api/admin/cleanup-orphans` | Clean up orphaned files |
| `POST` | `/api/admin/batch-delete` | Batch delete materials (by ID or condition, async task, returns `task_id`) |
| `POST` | `/api/admin/batch-unmark-ai` | Batch re-mark suspected AI materials as non-AI (by ID list, synchronous return `updated`) |
| `POST` | `/api/admin/deduplicate` | Smart deduplication deletion (async task, returns `task_id`) |
| `GET` | `/api/admin/vector-stats` | Vectorization status statistics (total materials / existing image and text vectors / missing count / LanceDB availability) |
| `POST` | `/api/admin/vector-backfill` | One-click create backfill tasks for materials missing vectors (async, returns `task_id`; returns `count=0` if no missing vectors) |
| `POST` | `/api/admin/crop-phone-screenshots/scan` | Phone screenshot cropping: scan candidates (read-only, manually upload portrait-mode screenshots + black border/detection features + confidence grading) |
| `POST` | `/api/admin/crop-phone-screenshots/apply` | Phone screenshot cropping: execute on selected IDs (original backup / crop replacement / thumbnail & hash rebuild / vector backfill; returns `duplicates` comparison data when content is duplicated, preview images temporarily stored in `storage/_crop_dups/` for manual decision) |
| `GET` | `/api/admin/export` | Export all materials as CSV (including tags / associated creators / models / audit status, triggers browser download) |
| `GET` | `/api/admin/trend?days=` | Daily material addition trend (last N days) |
| `GET` | `/api/admin/person-frequency?limit=` | Person × material count ranking |
| `GET` | `/api/admin/audit-logs?limit=` | Operation audit logs (sorted by time descending) |
| `POST` | `/api/admin/near-duplicates` | Near-duplicate detection (random sampling across entire database + perceptual hash grouping, returns only candidates, no deletion; hashes are cached to `inspirations.phash` after first calculation, progressively recalculating missing hashes per request) |

### Others

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Health check (returns `schema_version`, frontend uses this to validate frontend-backend contract) |
| `GET` | `/api/files/{path}` | Static file access |
| `WS` | `/ws` | WebSocket real-time push |

> **Schema Version Handshake:** The `schema_version` returned by `/api/health` is composed of the concatenation of the "database structure hash" (automatically calculated from the columns/indexes list in `db_migrations.py`) and the "API contract version" (`API_CONTRACT_VERSION`, manually incremented). The frontend expected value is no longer manually maintained: during dev startup or build, `scripts/compute_schema_version.py` invokes backend code to automatically compute and inject the value as the global constant `__SCHEMA_VERSION__` (see `web/vite.config.ts`). In case of inconsistency, a prompt will appear at the top of the page to avoid "silent failures" caused by backend updates without frontend restart; after backend changes, restarting the frontend dev server or rebuilding will automatically align the versions.

## Security Hardening (API Key)

Backend defaults to **not enabling** authentication (development mode). If the service is exposed on LAN or WAN, it is recommended to enable API Key protection for destructive endpoints.

**Protection Scope**: Endpoints involving data reset, bulk deletion, emptying the trash, deduplication deletion, tag deletion/merging, model unloading, deletion of bloggers/models, deletion of model photo sets/photos, etc. — **irreversible or bulk destructive** operations. Read endpoints and ordinary write operations (upload, favorites, move to trash, etc.) are unaffected.

```bash
# 1. Generate key and get enablement instructions
python scripts/generate_api_key.py

# 2. Append the generated key to backend/.env (script prints full instructions)
#    API_KEY=<generated key>

# 3. Restart backend to take effect
bash scripts/restart.sh
```

**Behavior After Activation**:
- Destructive endpoints require the `X-API-Key` header; missing it returns `401`, invalid key returns `403`
- Read endpoints do not require a key and remain accessible normally

**Frontend Integration**: In browser console, execute `localStorage.setItem('apiKey', '<key>')` then refresh the page — frontend requests will automatically append the `X-API-Key` header; or set the `VITE_API_KEY` environment variable during build.

**Note**: `X-API-Key` is a simple shared key authentication mechanism, designed to prevent accidental or unauthorized invocations; it does not replace HTTPS or user authentication systems. The list of destructive endpoints is maintained in `backend/app/utils/auth.py` under `DESTRUCTIVE_ROUTES`; to add a new destructive endpoint, simply append a line to this list.

## Automated Testing

Core flow regression protection: backend `pytest` (integration tests + service unit tests) + frontend `vitest` (pure functions / composable / store).

**Run All Tests in One Click** (backend + frontend type checking + vitest, Git Bash):

```bash
bash scripts/test.sh          # Normal run
bash scripts/test.sh --cov    # Backend coverage report (extra)
```

### Backend (pytest, 311 test cases)

```bash
# First time: install test dependencies
cd backend
pip install -r requirements-dev.txt -i https://mirrors.aliyun.com/pypi/simple/

# Run all tests (automatically uses temporary database and temporary storage directory, does not touch real data)
pytest
```

Scope:
- **Integration Testing**: Health checks, destructive API key authentication (401/403, read APIs unaffected), material upload/detail/favorite/content deduplication (SHA-256)/platform ID deduplication/**soft delete filtering**/physical deletion, trash move-in/restore/clear with reason filtering/expiry cleanup/**invariant state validation** (soft delete three fields same truth value, R1/R2/R3 violation detection), tag creation/conflict/association/idempotency/unassociation, keyword and tag combination search, person module (dual CRUD for blogger/model after split, material association, style profiling, deletion restrictions, CSV import, photo group, and person frequency aggregation statistics), **blogger face** (registration average pooling/re-registration overwrite/no-face rejection/over 5 photos rejection/blogger not found 404, inspiration face detection hit and miss/manual assign and unlink/delete detection — all with mocked face_client), **batch operations** (batch favorite/move to trash/edit metadata/tag and dominant color filtering), **management dashboard insights** (CSV export/new trend/person frequency/audit log/approximate duplicate detection), **mobile image cropping** (candidate scanning/black border detection/screenshot feature confidence/skip details/content duplicate preview/physical deletion of duplicate materials followed by re-cropping/re-cropping without clearing other groups preview), **task executor** (batch delete tasks: delete records + delete files + release space; vector refill batching/quality review anti-fake success: all failures throw task-level exceptions, partial failures complete normally), **AI analysis and quality review** (full analysis save tags, binary classification pass/reject, large tag suggestions, quality statistics, batch review/re-review task creation — all simulated Ollama), **scraping module** (plugin session task full lifecycle and result batch deletion, task list pagination/filtering/sorting/statistics, scheduled task CRUD/start/stop/immediate execution, cookie import/delete/status, statistics aggregation).

- **End-to-end journey testing** (`test_journeys.py`, validating link connections rather than internal single-step logic): material full journey (upload → tagging → vector → trash → restore → delete again → clear, invariant zero violations and tombstone/audit trail at each step), scraping journey (plugin session → from-url ingestion → task completion → deletion → tombstone → re-scrape rejected, including anti-redundancy loop with tombstone still present after restore), failure journey (file missing self-healing: trash/restore do not produce dangling records), crash journey (worker heartbeat timeout → `_reset_stale_tasks` reset → retry succeeds, no false success).

- **Service unit tests**: `tag_normalizer` (synonym normalization/similarity/name validation), `ai_parser` (malformed JSON repair/tag extraction/truncation detection), `quality_learner` (training/sample insufficiency/rollback, vector mocked), `image_hash` (perceptual hash approximate invariance/discrimination/Hamming distance/illegal files), `deduplicate` (deduplication scoring/retention suggestions/tie-breaker/file missing fallback/physical deletion), `csv_safety` (CSV formula injection escaping).

> **Coverage Measurement**: Install `pytest-cov` and run `pytest --cov --cov-report=term-missing` to generate line-level coverage (`backend/.coveragerc` configured with `source=app` and excludes boilerplate code, currently ~52%). Remaining low-coverage blind spots are concentrated in: real scrapers (`scrapers/`, 0%, depends on real browsers), deep branches in `vector/similarity`, batch retry/retry-all in `ai_analysis_service`, `ws.py` (WebSocket).

### Frontend (vitest, 97 test cases)

```bash
cd web
npm test
```

Coverage: pure functions `format` / `sourceLabel` / `taskLabel` / `browseQuery`, composable `useSplitResize` (drag-and-drop) / `useBatchSelection` (batch multi-select), `persons` (blogger/model dual store instances + request sequence anti-chaos), `inspirations` / `tags` store (mock API), assertion of task type/icon mapping for `taskLabel`.

### Conventions

- Tests use **temporary databases and temporary storage directories**, will not read/write real `storage/` or `fashion_inspo.db`; if test files are mistakenly written to real directories, clean them with `python scripts/clean_test_files.py --delete`
- When adding destructive APIs or modifying core chains (soft delete/trash/deduplication/authentication), please supplement test cases in corresponding `backend/tests/` or `web/src/**/__tests__/` and ensure they pass.

| Software | Purpose | Required? |
| -------- | ------- | :-------: |
| Python 3.12+ | Backend | ✅ |
| Node.js 20+ | Web + Mobile Frontend | ✅ |
| Ollama | AI Visual Reasoning | ✅ |
| Qwen3-VL:8B-Instruct | Outfit Tag Recognition | ✅ |
| Google Chrome | Host Browser for CDP Collection | ⚠️ Required during collection |
| Playwright | Collection Engine Driver | ⚠️ Required during collection |
| ffmpeg | Video Keyframe Extraction | ❌ Not yet used |

## Open Source License

This project is open-sourced under the **Apache License 2.0**, see [LICENSE](./LICENSE).

- Free to use, modify, and redistribute (including for commercial purposes), with attribution to copyright notices and this license included.
- Modified files must indicate changes; derivative works are not required to be open-sourced.
- For platform collection capabilities, comply with the service terms and laws of the target platforms, and reasonably control collection frequency.
