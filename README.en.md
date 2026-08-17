# fashion-inspo (AI Outfit Inspiration Library)

> **[中文](README.md) | English**（Chinese is the source of truth; this English doc is a generated translation. Regenerate with `python scripts/translate_docs.py`）

A personal AI outfit inspiration management tool that uses automated scraping and visual recognition to turn fragmented outfit content into a searchable personal inspiration asset.

## Prerequisites

The following software and environment are **mandatory**; core features will not work without them:

| Software | Version | Purpose | Installation Guide |
| ------ | ---------- | ------ | ---------- |
| Python | 3.12+ | Backend runtime | [python.org](https://www.python.org/downloads/) |
| Node.js | 20+ | Web frontend build | [nodejs.org](https://nodejs.org/en/download) |
| Ollama | latest | AI vision inference engine | [ollama.com](https://ollama.com/download/windows) |
| Qwen3-VL:8B-Instruct | — | Outfit tag recognition model | `ollama pull qwen3-vl:8b-instruct` |

### Additional Scraping Engine Requirements (CDP Mode Only)

| Software | Requirement | Purpose |
|------|------|------|
| **Google Chrome** | Latest stable | Host browser for CDP zero-detection scraping |
| Playwright | 1.40+ | Browser automation driver (`pip install playwright && playwright install chromium`) |

> [Warning] **Important: Google Chrome is required and cannot be replaced!**
>
> CDP (Chrome DevTools Protocol) scraping relies on Google Chrome's native debugging protocol. The following browsers **cannot** be used for CDP scraping:
>
> | Browser | Available | Reason |
> | -------- | :---: | ------ |
> | **Google Chrome** | ✅ | Full CDP protocol support |
> | 360 Speed Browser | ❌ | CDP protocol is crippled and cannot be called properly |
> | Microsoft Edge | ❌ | CDP implementation differs; some interfaces are incompatible |
> | Chromium (open source) | [Warning] | May work, not fully tested |
> | Other Chrome-engine derivatives | ❌ | Most have trimmed the CDP protocol |
>
> If both Google Chrome and other Chromium-based browsers are installed on your system, make sure:
>
> 1. **Fully close** all Google Chrome windows before starting debug mode
> 2. Do not run the `--remote-debugging-port` command with 360 Speed Browser
> 3. You can click "Test Connection" on the scraping page to verify that the connection is to Google Chrome

### Chrome Path Configuration

Chrome's install path varies by device. You can customize it in the following ways:

**Option 1: Environment variable (recommended)**

Set it in `backend/.env`:

```bash
# Chrome executable path
CHROME_EXECUTABLE="C:/Program Files/Google/Chrome/Application/chrome.exe"

# Dedicated user data directory for scraping (isolated from daily Chrome to avoid conflicts)
CHROME_USER_DATA_DIR="C:/Users/Administrator/Desktop/chrome-scraper-profile"

# Debug port (default 9222, usually no change needed)
CHROME_DEBUG_PORT=9222
```

**Option 2: Modify the config file**

Directly edit the default values of the `Settings` class in `backend/app/config.py`:

```python
chrome_executable: str = "C:/Program Files/Google/Chrome/Application/chrome.exe"
chrome_user_data_dir: str = "C:/Users/Administrator/Desktop/chrome-scraper-profile"
chrome_debug_port: int = 9222
```

> **Common Chrome install paths:**
>
> - Windows default: `C:/Program Files/Google/Chrome/Application/chrome.exe`
> - Windows per-user install: `C:/Users/<username>/AppData/Local/Google/Chrome/Application/chrome.exe`
> - macOS: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`
> - Linux: `/usr/bin/google-chrome`

## Tech Stack

| Layer | Technology |
| ------ | ------ |
| Backend | Python 3.12 + FastAPI + SQLAlchemy async + SQLite |
| Web frontend | Vue 3 + Vite + TypeScript + Pinia + Naive UI |
| Mobile | React Native (Expo) + Zustand |
| Browser extension | Chrome Extension Manifest V3 |
| AI inference | Ollama + Qwen3-VL:8B-Instruct (local GPU) |
| Scraping engine | Playwright + CDP connecting to real Chrome (zero-detection scraping) |

## Feature Overview

| Module | Features |
| ------ | ------ |
| **Inspiration Library** | Masonry browsing, multi-dimensional filtering (source/media/status/tag/dominant color), sorting (including random/tag count), density adjustment, paginated loading, batch multi-select operations (favorite/move to trash/add tags/edit metadata), persistent browsing mode/density/items per page |
| **Advanced Search** | Keyword search, tag filtering (AND/OR), co-occurrence suggestions, advanced filters (source/media/date), sorting (match priority), search history, pagination, density adjustment, semantic search (text), image search by image (image upload), `/` to focus and Esc to exit, copy search link, persistent filter state |
| **Upload Inspiration** | Drag-and-drop/paste/URL import, preview queue (videos previewable), upload progress and speed, quick tags, metadata presets, deduplication detection, folder batch upload, queue management (double confirmation to clear), persistent preferences, 500-item limit validation |
| **Inspiration Detail** | Large image preview (lightbox left/right switching/zoom), tag display, outfit master tags (manual select/create + one-click import of AI suggestions), similar inspiration recommendations (favorite/delete), re-analysis, download original image, copy original link, click a tag to jump to search, move to trash with a selectable reason (质量差/重复/不喜欢/隐私/其他/AI生成) |
| **Scraping Management** | Xiaohongshu CDP zero-detection scraping + Douyin standalone-browser scraping, task pagination/platform & status filtering/sorting, cancel/resume (checkpoint)/duplicate and re-scrape, log viewing, funnel visualization, result preview (batch delete/load more/jump to detail), Cookie management (status/expiry/import/delete), Chrome lifecycle management, scheduled scraping (schedule CRUD/enable-disable/run now), stats dashboard (platform distribution/daily trends), URL tombstone table + content MD5 deduplication, persistent filters/sorting/tabs |
| **Tag Management** | Grouped browsing/search/filtering, pinning + custom drag-and-drop sorting, alias normalization (AI detects synonyms and merges automatically), batch category change/rename/merge/delete (double confirmation), duplicate scan, drag to change category, batch tagging, tag notes, co-occurrence graph + usage trends, import/export, inspiration association preview, persistent column width |
| **AI Model Management** | Model list/download/switch, text embedding model management (labeling/one-click download/switch), GPU VRAM monitoring, batch analysis (async task queue), history pagination, multi-select batch operations, analysis result comparison, queue visualization, parameter tuning (per-model isolation + default restore + clear overrides), data reset, quality review (qualified/unqualified binary classification + re-review, async), negative-sample pre-filter (status/metrics/train/rollback), keyboard shortcuts (Enter to download/Ctrl+S to save) |
| **Inspiration Management** | Admin panel partitioned by sub-menus (sub-page state persisted via URL, kept after refresh): overview (stats/distribution/largest files), suspected-AI review (check to batch delete or re-mark as non-AI; hover the card and click 👁 to view details), batch cleanup (no tags/analysis failed), data integrity check, duplicate file detection and deduplication, near-duplicate detection (perceptual hash grouping + side-by-side preview + manual-confirm deletion), vectorization backfill (one-click backfill of missing image vectors), trash (restore/permanent delete/empty for soft-deleted inspirations; no auto-recycling by default), data insights (CSV export/new-trend chart/person frequency ranking/operation audit logs), **phone screenshot cropping** (scan manual-uploaded portrait screenshots → manual check-and-confirm → one-click crop of status-bar/bottom-nav areas: auto black-band detection / fixed-ratio modes + screenshot-feature confidence grading; original backed up automatically + vector backfill; skipped inspirations can be precisely located in the library; when the crop result duplicates an existing inspiration, a side-by-side comparison is shown and the user decides which to keep — the duplicate can be physically deleted) |
| **Person Management** | Person list (name search/content type filter/platform filter/sorting), content type distinction (professional model/blogger badges throughout list/detail/forms), create/edit/delete, popularity ranking, style profile (high-frequency tags/category distribution/trends), inspiration association (search-add/unlink on the detail page), **model photo sets** (select a folder to import the whole set into the chosen person; photo set browsing/lightbox/delete; SHA-256 deduplication within a set) |
| **Browser Extension** | One-click extraction of outfit images from web pages; each scraping session automatically creates a task record, and the scraping management page shows the extension's scraping history, results, and funnel |

## Quick Start

### 1. Install Dependencies

```bash
# Python backend
cd backend
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
# Optional: test dependencies for running automated tests
pip install -r requirements-dev.txt -i https://mirrors.aliyun.com/pypi/simple/

# Node.js frontend
cd ../web
npm install
```

### 2. Install AI Models

```bash
# Install Ollama (download the Windows version from ollama.com)

# Recommended: Qwen3-VL:8B-Instruct (officially maintained, 256K context, 32-language OCR)
ollama pull qwen3-vl:8b-instruct

# Alternative: MiniCPM-V:8b (smaller and faster)
ollama pull minicpm-v:8b
```

After installation, simply switch the active model on the AI Model Management page.

### 3. Start the Services

```bash
# Backend (default port 18888; changeable via the PORT environment variable or .env)
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 18888 --reload

# Web frontend (default port 17777; changeable via the VITE_FRONTEND_PORT environment variable)
cd web
npm run dev

# Task queue worker (handles the async "batch analysis" task; run it in a separate terminal)
cd ../backend
python -m app.worker
```

Open `http://localhost:17777` in your browser.

> One-click restart: `bash scripts/restart.sh` stops old processes and starts the backend + frontend + worker together, verifying readiness.
>
> Auto-start: `bash scripts/ensure-services.sh` performs "health check + start only missing services"; it is idempotent and lock-protected, and is called automatically by Claude Code's SessionStart hook (see `.claude/settings.json`) when a new session starts; safe under concurrent sessions.

**Custom ports:**

```bash
# Backend .env
PORT=18888                   # Backend listen port

# Frontend .env (web/.env)
VITE_FRONTEND_PORT=17777     # Frontend dev server port
VITE_BACKEND_URL=http://localhost:18888  # Backend API URL
```

### 4. Start the Scraping Engine (Optional)

The scraping engine connects to the user's real Chrome via CDP to achieve zero-detection scraping.

> **Prerequisite:** Google Chrome must be installed and [Chrome Path Configuration](#chrome-path-configuration) completed.

**Launch Chrome in debug mode:**

Run the following in the command line, using the paths configured in `.env` (the port and directory must match your config):

```bash
"C:/Program Files/Google/Chrome/Application/chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:/Users/Administrator/Desktop/chrome-scraper-profile"
```

> If Chrome reports "Failed to create user data directory", close all open Chrome windows first and try again.

**Create a scraping task in the Web UI:**

1. Log in to Xiaohongshu (`xiaohongshu.com`) in the debug Chrome window
2. Open the scraping page and confirm CDP mode is enabled
3. Click the "Test Connection" button and confirm it shows "Connected"
4. Enter a keyword and click "Start Scraping"

> **Douyin scraping:** Douyin tasks don't need CDP Chrome — the backend scrapes the web-version search results directly with a standalone Playwright browser (anti-scraping is strict and results may be empty; the page suggests using the browser extension instead).
>
> **Sorting notes:** The "Latest/Hottest" sorting only takes effect in Xiaohongshu search mode; the Douyin web version always uses comprehensive sorting.
>
> **Scheduled scraping:** The "Scraping Management → Scheduled Scraping" tab can create plans that run automatically at intervals (1 hour ~ weekly); the backend scheduler loop checks for triggers every 30 seconds. Xiaohongshu scheduled tasks require the debug Chrome to stay running (you can click "Start Chrome" in the task form to have the backend launch it).

### 5. Install the Browser Extension

1. Open `chrome://extensions` in Chrome
2. Enable "Developer mode"
3. Click "Load unpacked"
4. Select the `browser-extension/` directory

### 6. Start the Mobile App (Optional)

```bash
cd mobile
npx expo start
```

## Project Structure

```
fashion-inspo/
├── CLAUDE.md                     # Coding standards and project conventions
├── README.md                     # This file
├── TODO.md                       # Pending feature list
│
├── backend/                      # Python backend
│   ├── .env                      # Environment variables
│   ├── requirements.txt          # Python dependencies
│   ├── app/
│   │   ├── main.py               # FastAPI entry point
│   │   ├── config.py             # Configuration
│   │   ├── database.py           # Database engine
│   │   ├── worker.py             # Task queue worker (python -m app.worker)
│   │   ├── models/               # Data models
│   │   │   ├── inspiration.py    # Outfit inspiration + AI analysis log
│   │   │   ├── tag.py            # Tags + aliases (incl. source marker)
│   │   │   ├── person.py         # Persons (model/blogger) + person-inspiration links + photo sets/photos
│   │   │   ├── scraper.py        # Scraping tasks + scheduled scraping plans
│   │   │   ├── task.py           # Async task queue
│   │   │   └── audit.py          # Operation audit logs
│   │   ├── schemas/              # Pydantic request/response
│   │   ├── routers/              # API routes
│   │   │   ├── inspirations.py   # Inspiration CRUD
│   │   │   ├── tags.py           # Tag management + batch/stats/scan/sort/alias/co-occurrence/import-export
│   │   │   ├── search.py         # Multi-dimensional search + similar inspirations
│   │   │   ├── persons.py        # Person management + photo sets (model photos)
│   │   │   ├── ai.py             # AI route aggregation (split into ai_*.py)
│   │   │   ├── ai_shared.py      # AI shared state + background tasks
│   │   │   ├── ai_models.py      # Model management + GPU + model stats
│   │   │   ├── ai_analysis.py    # Analysis + queue + history + comparison
│   │   │   ├── ai_quality.py     # Quality review
│   │   │   ├── ai_settings.py    # Prompt + parameter tuning
│   │   │   ├── ai_dashboard.py   # Analysis quality dashboard
│   │   │   ├── ai_outfit.py      # Outfit master tag suggestions
│   │   │   ├── ai_reset.py       # Data reset
│   │   │   ├── scraper.py        # Scraping management
│   │   │   ├── admin.py          # Admin panel (stats, dedup, integrity check)
│   │   │   ├── tasks.py          # Task queue (list/detail/cancel)
│   │   │   ├── files.py          # Static files
│   │   │   └── ws.py             # WebSocket
│   │   ├── services/             # Business logic
│   │   │   ├── ai_service/       # AI orchestration (analyze / quality / outfit_summary / common)
│   │   │   ├── ai_parser.py      # AI response parsing/repair (malformed handling)
│   │   │   ├── ai_tag_saver.py   # Tag normalization/save/link
│   │   │   ├── ai_analysis_service.py  # Analysis/queue/history business logic
│   │   │   ├── inspiration_service.py  # Inspiration CRUD business logic
│   │   │   ├── tag_service.py    # Tag CRUD + merge + preset import + similarity
│   │   │   ├── person_service.py # Person CRUD + style profile + photo sets/photos
│   │   │   ├── scraper_service.py    # Scraping orchestration + scheduled dispatch + extension task records
│   │   │   ├── file_service.py   # File management
│   │   │   ├── audit_service.py  # Operation audit log writes
│   │   │   ├── near_duplicate_service.py  # Near-duplicate detection (perceptual hash grouping)
│   │   │   ├── task_runners/     # Async task runners (batch_analyze / quality_check / batch_delete / deduplicate)
│   │   │   ├── vector/           # Vector search (embedding / store / similarity)
│   │   │   ├── embedding_service.py  # Thin shell → vector.embedding
│   │   │   ├── vector_service.py     # Thin shell → vector.similarity
│   │   │   └── vector_store.py       # Thin shell → vector.store
│   │   ├── scrapers/             # Platform scrapers
│   │   │   ├── base.py           # Abstract base class
│   │   │   ├── xiaohongshu.py    # Xiaohongshu
│   │   │   └── douyin.py         # Douyin
│   │   └── utils/                # Utility functions
│   │       ├── auth.py           # API Key authentication middleware
│   │       ├── file_hash.py      # File MD5/SHA-256 hashing
│   │       ├── image_hash.py     # Perceptual hash (dHash, near-duplicate detection)
│   │       ├── image_utils.py    # Thumbnail/color extraction
│   │       └── tag_normalizer.py # Tag normalization + synonym/alias mapping
│   ├── scripts/                  # Maintenance scripts
│   │   ├── run_scraper.py         # Scraping execution script (Xiaohongshu CDP / Douyin standalone browser, checkpoint resume)
│   │   ├── cleanup_tags.py        # Dirty tag cleanup in database
│   │   ├── validate_tags.py       # Tag validity check
│   │   └── diagnose_scraper.py    # Scraping diagnostic tool
│   └── storage/                  # Local file storage (gitignore)
│       ├── images/
│       ├── thumbnails/
│       ├── videos/
│       ├── trash/                # Trash (soft-deleted files are moved here)
│       ├── person_photos/        # Person photos (model portraits, separate from library images/)
│       ├── person_thumbnails/    # Person photo thumbnails
│       ├── _crop_backup/         # Phone-screenshot crop originals backup (timestamped subdirectories)
│       ├── _crop_dups/           # Crop duplicate-comparison previews (per-batch subdirectories, kept during decisions)
│       └── lancedb/              # Vector store (text/image vectors)
│
├── web/                          # Vue 3 Web frontend
│   ├── src/
│   │   ├── main.ts               # App entry point
│   │   ├── App.vue               # Root component
│   │   ├── router/index.ts       # Route configuration
│   │   ├── api/                  # API client
│   │   │   ├── client.ts         # Axios instance
│   │   │   ├── inspirations.ts   # Inspiration API
│   │   │   ├── tags.ts           # Tag API (complete)
│   │   │   ├── search.ts         # Search API
│   │   │   └── admin.ts          # Admin API (export/trends/person frequency/audit/near-duplicates)
│   │   ├── stores/               # Pinia state
│   │   │   ├── inspirations.ts   # Inspiration state
│   │   │   ├── tags.ts           # Tag filter state
│   │   │   ├── aiModels.ts       # AI model shared state
│   │   │   └── ui.ts             # UI state
│   │   ├── views/                # Page components
│   │   │   ├── HomeView.vue      # Home gallery
│   │   │   ├── UploadView.vue    # Upload inspiration
│   │   │   ├── ModelPhotoUploadView.vue # Add model photos (import whole folder)
│   │   │   ├── SearchView.vue    # Advanced search
│   │   │   ├── DetailView.vue    # Inspiration detail
│   │   │   ├── ScraperView.vue   # Scraping management
│   │   │   ├── TagManageView.vue # Tag management (full-featured)
│   │   │   ├── PersonView.vue    # Person management (list/filter/CRUD/ranking)
│   │   │   ├── PersonDetailView.vue # Person detail (style profile + photo set browsing)
│   │   │   ├── ModelManageView.vue # AI model management (full-featured)
│   │   │   ├── AdminView.vue     # Inspiration management (sub-menu pages: overview/suspected AI/batch cleanup/integrity/duplicate files)
│   │   │   └── TaskManageView.vue # Task management (async task queue list/detail/cancel)
│   │   ├── components/           # Shared components (grouped by domain)
│   │   │   ├── layout/AppLayout.vue
│   │   │   ├── inspiration/      # MasonryGrid, InspirationCard, ImageLightbox, OutfitTagSection, SimilarSection
│   │   │   ├── model/            # ModelListPanel, AnalysisPanel(+subcomponents), SettingsPanel, QualityPanel, ReviewPanel
│   │   │   ├── admin/            # Stats/tasks/suspected-AI review/duplicates/near-duplicates/integrity check/export/trends/person frequency/audit log subcomponents
│   │   │   ├── scraper/          # Scraping task form/table/log/funnel/results/source config/scheduled scraping/stats dashboard subcomponents
│   │   │   ├── search/           # SearchBar, TagFilter + search panel subcomponents
│   │   │   ├── tag/              # Tag list/toolbar/dialog subcomponents
│   │   │   ├── person/           # PersonTypeTag, PersonFormModal, PersonLinkSection
│   │   │   └── upload/           # Upload drag-drop/queue/options subcomponents
│   │   ├── types/                # Shared TS types across components (admin/analysis/scraper/upload)
│   │   ├── utils/                # Utility functions
│   │   │   ├── sourceLabel.ts    # Source type Chinese label mapping
│   │   │   └── format.ts         # Byte/duration/date formatting
│   │   └── composables/          # Vue composables (useWebSocket / useSearch / useOutfitTags / useTagManage / useAdminTask, etc.)
│   ├── package.json
│   └── vite.config.ts
│
├── mobile/                       # React Native mobile app
│   ├── app/
│   │   ├── _layout.tsx           # Root layout
│   │   ├── (tabs)/               # Tab pages
│   │   │   ├── index.tsx         # Gallery
│   │   │   ├── search.tsx        # Search
│   │   │   └── capture.tsx       # Camera upload
│   │   └── detail/[id].tsx       # Detail
│   ├── hooks/useInspirations.ts  # Zustand state
│   ├── services/api.ts           # API client
│   ├── utils/sourceLabel.ts      # Source type Chinese label mapping
│   └── app.json                  # Expo config
│
├── browser-extension/            # Chrome browser extension
│   ├── manifest.json
│   ├── background/service-worker.js
│   ├── content-scripts/extract-images.js
│   ├── popup/
│   │   ├── popup.html
│   │   ├── popup.js
│   │   └── popup.css
│   └── icons/
│
├── shared/types/                 # Shared types between frontend and backend
│   ├── inspiration.ts
│   ├── tag.ts
│   ├── person.ts
│   └── api.ts
│
└── scripts/                      # Utility scripts
    ├── seed_tags.py              # Preset tag import
    ├── batch_import.py           # Batch import local images
    ├── backfill_vectors.py       # Vector backfill for existing inspirations
    ├── restart.sh                # One-click restart of frontend/backend + worker
    ├── ensure-services.sh        # Idempotently ensure services are running (lock + health check, for the SessionStart hook)
    └── generate_icons.py         # Generate extension icons
```

## System Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Local PC (Windows 11)                     │
│                                                               │
│  ┌───────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │ Vue 3 Web App │  │ Browser Extension│  │ React Native │  │
│  │(Desktop browser)│  │ (One-click scrape)│  │ (Mobile)     │  │
│  └───────┬───────┘  └────────┬─────────┘  └──────┬───────┘  │
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
│  │  Scheduler    │ Trash Auto-Cleanup                    │    │
│  └──────────────────────────┬──────────────────────────┘    │
│                              │                               │
│  ┌──────────────────────────┼──────────────────────────┐    │
│  │   SQLite (metadata)        │  Storage/ (image files)   │    │
│  └──────────────────────────┴──────────────────────────┘    │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │          Ollama (GPU: RTX 5060Ti 16GB)               │    │
│  │  Qwen3-VL:8B-Instruct — outfit visual & tag extraction │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │     Chrome CDP (:9222) — scraping engine + real Chrome  │    │
│  │     Xiaohongshu/Douyin    → images auto-saved to library │    │
│  └─────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

## Data Models

| Table | Description | Key Fields |
| ---- | ------ | ---------- |
| `inspirations` | Outfit inspiration | id, source_type, file_path, media_type, dominant_colors, quality_status, quality_reason, is_ai_generated, deleted_at, trash_reason |
| `tags` | Tag | id, name, category, source (seed/ai_generated/manual), pinned, sort_order, description |
| `tag_aliases` | Tag alias | id, tag_id, alias — synonym normalization (aliases detected by AI are automatically merged into the main tag) |
| `inspiration_tags` | Inspiration-tag link | inspiration_id, tag_id, confidence |
| `ai_analysis_log` | AI analysis log | inspiration_id, model_name, log_type, raw_response, processing_time_ms, error |
| `scraper_tasks` | Scraping task | platform, status, items_found/added, diagnostics (scraping funnel log), resume_token (checkpoint resume progress) |
| `scraper_seen_urls` | URL tombstone table | source_url (PK), created_at — prevents re-scraping after deletion |
| `scraper_schedules` | Scheduled scraping plan | platform, keywords, max_count, sort_mode, enabled, interval_minutes, next_run_at, last_task_id, run_count |
| `task_queue` | Async task queue | type (batch_analyze/quality_check/batch_delete/deduplicate), status (pending/running/success/failed/cancelled), progress, total/done, result, error, retry_count, next_retry_at |
| `audit_logs` | Operation audit log | id, action (batch_delete/delete_rejected/cleanup_orphans/empty_trash/batch_trash), target_type, count, freed_bytes, detail, created_at — trail for destructive batch operations |
| `persons` | Person (professional model/outfit blogger) | id, name, person_type (model/blogger), platform, platform_user_id, profile_url, avatar_path, bio, source, created_at, updated_at |
| `inspiration_persons` | Person-inspiration many-to-many link | inspiration_id, person_id, confidence — records AI's confidence about "who is in the image" |
| `person_photo_sets` | Person photo set | id, person_id, name (set name, defaults to the folder name), created_at, updated_at |
| `person_photos` | Person photo | id, set_id, file_path, thumbnail_path, content_hash (SHA-256 dedup within set), sort_order, created_at |

### Tag Category System

| Category | Examples | Description |
| ------ | ------ | ------ |
| `style` | JK制服, 汉服, Y2K, 法式, 新中式 | Style system |
| `item_type` | 百褶裙, 过膝袜, 西装外套, 马丁靴 | Item type |
| `color` | 白色, 海军蓝, 酒红, 格纹 | Color |
| `body_part` | 过膝, 高腰, V领, 拖地 | Wearing style |
| `fit` | 宽松, 修身, Oversized, 直筒 | Fit |
| `season` | 春季, 夏季, 秋季, 冬季 | Season |
| `attribute` | 露脸, 全身, 对镜自拍, 叠穿 | Image attribute |
| `outfit` | 御姐长腿高跟鞋穿搭, 白色系穿搭, 网球穿搭 | Outfit master tag (curated layer: manual + AI summary, quality over quantity) |

### Tag Source Markers

| source | Meaning | Color Marker |
| -------- | ------ | :---: |
| `seed` | Preset tag (imported at system initialization) | Gray |
| `ai_generated` | Auto-extracted by AI analysis | Purple |
| `manual` | Created/imported manually by the user | Blue |

### Database Migrations (Alembic)

The database schema is managed by Alembic (`backend/alembic/`). `run_migrations()` is called automatically at backend startup: a fresh empty database runs the baseline table creation, an existing legacy database is automatically `stamp`ed to the baseline, and a managed database gets incremental upgrades; the worker does not run Alembic (concurrent startups would contend for the SQLite write lock) and only falls back to create_all + `ensure_schema()` (hand-written column patches).

**When adding fields/tables** (no longer hand-append to `_SCHEMA_COLUMNS` in `db_migrations.py`):

```bash
cd backend
# 1. After modifying the ORM models, generate a migration script (compares models against the database)
alembic revision --autogenerate -m "description"

# 2. Apply to the database (or restart the backend to apply it automatically)
alembic upgrade head
```

> When generating the baseline or testing, you can point the `ALEMBIC_DB_URL` environment variable at a temporary database to avoid touching real data.

## API Overview

### Inspiration Management

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/inspirations` | Inspiration list (paginated; filters: source/media/status/quality/tag/dominant color/date, `ids` comma-separated precise targeting; sorting includes `tag_count`) |
| `POST` | `/api/inspirations` | Upload inspiration |
| `POST` | `/api/inspirations/from-url` | Import inspiration from URL (main channel for browser-extension scraping; server-side download avoids CORS; supports `source_platform_id`/`scraper_task_id`) |
| `GET` | `/api/inspirations/{id}` | Inspiration detail |
| `PATCH` | `/api/inspirations/{id}` | Update inspiration |
| `POST` | `/api/inspirations/{id}/trash` | Move to trash (soft delete; `reason` optional: 质量差/重复/不喜欢/隐私/其他/AI生成) |
| `POST` | `/api/inspirations/{id}/restore` | Restore from trash |
| `GET` | `/api/inspirations/trash` | Trashed inspirations list (paginated; filterable by `reason`) |
| `DELETE` | `/api/inspirations/trash` | Empty the trash (`only_expired=true` clears only expired inspirations) |
| `DELETE` | `/api/inspirations/{id}` | Permanently delete (physical, irreversible; use `/trash` for normal deletion) |
| `POST` | `/api/inspirations/{id}/tags` | Manually link tags to an inspiration (look up/create by name, e.g. outfit master tags) |
| `DELETE` | `/api/inspirations/{id}/tags/{tag_id}` | Unlink a tag from an inspiration |
| `POST` | `/api/inspirations/batch-tags` | Batch-link tags to multiple inspirations (look up/create by name) |
| `POST` | `/api/inspirations/batch-favorite` | Batch favorite/unfavorite inspirations |
| `POST` | `/api/inspirations/batch-trash` | Batch move to trash (soft delete) |
| `POST` | `/api/inspirations/batch-update` | Batch edit metadata (source/favorite/review status/suspected-AI flag) |
| `GET` | `/api/inspirations/dominant-colors` | List of dominant colors in the library (hex + count, for color filtering) |

### Search

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/search` | Multi-dimensional search (keyword + tag + color + date + source + media) |
| `GET` | `/api/search/similar/{id}` | Similar inspiration recommendations (image vector + tag weighting; visual/tag/hybrid sources) |
| `GET` | `/api/search/suggestions?q=` | Tag name autocomplete |
| `GET` | `/api/search/tag-cooccurrence?tag_name=` | Tag co-occurrence analysis |
| `POST` | `/api/search/vector` | Semantic search/image search by image (multipart: `text` or `file` + `top_k`) |
| `GET` | `/api/search/vector/status` | Vector search capability status (LanceDB/text/image vectors/existing counts) |
| `POST` | `/api/search/vector/backfill` | Backfill vectors for existing inspirations (`mode`=all/text/image, `limit`=max count) |

> **Vector search setup:** Text semantic search uses Ollama `all-minilm` (zero extra dependencies). Image search by image/visual similarity requires installing CLIP:
>
> ```bash
> pip install sentence-transformers   # includes torch (heavy)
> export HF_ENDPOINT=https://hf-mirror.com   # mirror for downloading the CLIP model in China
> python scripts/backfill_vectors.py --mode all   # backfill existing data (first run downloads clip-ViT-B-32, ~600MB)
> ```
>
> Without CLIP installed, the image-search endpoint returns 503 and similar-inspiration recommendations automatically fall back to pure tag matching; other features are unaffected.

### Tag Management

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/tags` | Tag list (grouped by category) |
| `POST` | `/api/tags` | Create tag |
| `PATCH` | `/api/tags/{id}` | Edit tag (rename/change category/pin/sort/note) |
| `DELETE` | `/api/tags/unused` | Delete all unused tags |
| `POST` | `/api/tags/batch-delete` | Batch delete tags |
| `POST` | `/api/tags/merge` | Merge tags |
| `GET` | `/api/tags/suggestions/{name}` | Deduplication suggestions when creating |
| `PATCH` | `/api/tags/batch-category` | Batch change tag category |
| `PATCH` | `/api/tags/batch-rename` | Batch rename (find and replace) |
| `GET` | `/api/tags/stats` | Tag stats (total/unused/source distribution) |
| `GET` | `/api/tags/duplicates` | Similar tag scan |
| `GET` | `/api/tags/{id}/inspirations` | Inspirations using this tag |
| `POST` | `/api/tags/{id}/inspirations/batch-remove` | Batch unlink tags from inspirations |
| `GET` | `/api/tags/export` | Export all tags as JSON |
| `POST` | `/api/tags/import` | Batch import tags |
| `POST` | `/api/tags/reorder` | Batch update custom sort order |
| `GET` | `/api/tags/aliases` | Tag alias list |
| `POST` | `/api/tags/{id}/aliases` | Add an alias to a tag |
| `DELETE` | `/api/tags/aliases/{id}` | Delete a tag alias |
| `GET` | `/api/tags/cooccurrence-network` | Tag co-occurrence network (nodes + weighted edges) |
| `GET` | `/api/tags/top` | Hot tag ranking |
| `GET` | `/api/tags/{id}/trend` | Tag usage trend (daily/weekly/monthly) |

### Person Management

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/persons` | Person list (pagination / name search / content type / platform filter) |
| `POST` | `/api/persons` | Create person (professional model / outfit blogger) |
| `GET` | `/api/persons/{id}` | Person detail (includes inspiration count and style profile: high-frequency tags / category distribution / trends) |
| `PATCH` | `/api/persons/{id}` | Update person (explicitly passing `null` clears nullable fields) |
| `DELETE` | `/api/persons/{id}` | Delete person (API Key required; inspirations are kept, only links are removed) |
| `GET` | `/api/persons/{id}/inspirations` | List of this person's inspirations (pagination + sorting) |
| `GET` | `/api/persons/top` | Popular person ranking (by inspiration count) |
| `GET` | `/api/persons/suggestions` | Suggest persons by name (for selection deduplication) |
| `POST` | `/api/inspirations/{id}/persons` | Batch-link persons to an inspiration (idempotent; outside the API Key protected list) |
| `DELETE` | `/api/inspirations/{id}/persons/{pid}` | Unlink an inspiration from a person |

> **Content type UI distinction:** Persons are distinguished by `person_type` as "professional model (model)" / "outfit blogger (blogger)", with list filters, type badges, and form selections throughout the frontend; links always use `person_id` (person names are not unique, avoiding ambiguity from identical names).

### Person Photo Sets (Model Photos)

Model photo sets are separate from outfit inspirations: model photos are portrait material of the person; they don't enter the inspiration library, don't participate in AI tagging or retrieval, and are only browsed via "person → photo set → photo". Files are stored separately under `person_photos/` to avoid being misjudged as orphaned files by the integrity check.

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/persons/{id}/photo-sets` | Person photo set list (paginated; includes photo count and cover) |
| `POST` | `/api/persons/{id}/photo-sets` | Create photo set (name defaults to "Unnamed Photo Set") |
| `GET` | `/api/persons/{id}/photo-sets/{set_id}` | Photo set detail (includes paginated photo list) |
| `PATCH` | `/api/persons/{id}/photo-sets/{set_id}` | Rename photo set |
| `DELETE` | `/api/persons/{id}/photo-sets/{set_id}` | Delete photo set (cascades to photos and physical files) |
| `POST` | `/api/persons/{id}/photo-sets/{set_id}/photos` | Upload a single photo to the photo set (SHA-256 content dedup within set) |
| `DELETE` | `/api/persons/{id}/photo-sets/{set_id}/photos/{photo_id}` | Delete a single photo from the photo set |

### AI Analysis

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/ai/status` | AI service status (Ollama connection/version/active vision and embedding models) |
| `GET` | `/api/ai/models` | Installed model list (marked with vision/text-embedding roles) |
| `POST` | `/api/ai/models/pull` | Download model (SSE progress) |
| `PUT` | `/api/ai/models/active` | Switch active vision model |
| `PUT` | `/api/ai/models/embedding-active` | Switch text embedding model (text side of vector search) |
| `DELETE` | `/api/ai/models/{name}` | Delete model |
| `POST` | `/api/ai/analyze/{id}` | Trigger a single analysis |
| `POST` | `/api/ai/batch-analyze` | Batch analysis (creates an async task, returns task_id, executed by the worker) |
| `POST` | `/api/ai/outfit-tags/suggest` | AI-suggested outfit master tags (suggestion only, not stored) |
| `POST` | `/api/ai/retry/{id}` | Retry failed analysis |
| `GET` | `/api/ai/queue` | Analysis queue stats |
| `GET` | `/api/ai/unanalyzed-ids` | IDs of unanalyzed inspirations |
| `GET` | `/api/ai/active-analyses` | Currently running analyses |
| `GET` | `/api/ai/history` | Analysis history (pagination/filter) |
| `GET` | `/api/ai/history/{id}` | Analysis detail (includes tags, structured snapshot, quality review, version info) |
| `DELETE` | `/api/ai/history/{id}` | Delete a single log entry |
| `DELETE` | `/api/ai/history/failed/all` | Delete all failed logs |
| `POST` | `/api/ai/history/batch-delete` | Batch delete analysis records |
| `POST` | `/api/ai/history/batch-retry` | Batch retry analyses |
| `GET` | `/api/ai/history/model-names` | List of historical model names |
| `GET` | `/api/ai/gpu-stats` | GPU VRAM monitoring |
| `POST` | `/api/ai/unload-model` | Unload model to free VRAM |
| `GET` | `/api/ai/queue/pending` | Queued inspirations (with thumbnails) |
| `DELETE` | `/api/ai/queue/{id}` | Cancel queued task |
| `POST` | `/api/ai/queue/pause` | Pause queue |
| `POST` | `/api/ai/queue/resume` | Resume queue |
| `GET` | `/api/ai/compare/{id}` | Analysis result comparison (structured tag differences + duration + version info) |
| `GET` | `/api/ai/quality-dashboard` | Analysis quality dashboard (coverage/trends/problem inspirations) |
| `GET` | `/api/ai/model-stats` | Per-model aggregated usage stats (success rate/average duration/average tag count; tag counts based on structured snapshots) |
| `GET` | `/api/ai/prompt` | Get the current model's Prompt (isolated per model) |
| `PUT` | `/api/ai/prompt` | Update the current model's Prompt |
| `GET` | `/api/ai/prompt/versions` | Prompt version history |
| `POST` | `/api/ai/prompt/save-version` | Save the current Prompt as a version |
| `POST` | `/api/ai/prompt/rollback` | Roll back the Prompt to a specific version |
| `POST` | `/api/ai/test-analyze` | Single-image test analysis (SSE, not persisted) |

> **Structured storage of AI analysis results (multi-version comparison and tracing):**
>
> The structured result of each analysis/review is stored in dedicated tables, decoupled from the inspiration's current state (full tag set, `quality_status`), enabling historical tracing across model/Prompt versions:
>
> | Table | Description |
> | ------ | ------ |
> | `ai_extracted_tags` | Snapshot of "which tags were extracted" for a single analysis (log_id + tag_id + confidence) |
> | `ai_quality_review` | Verdict of a single review (result / reason / reviewed_at) |
>
> `ai_analysis_log` gains `prompt_version` (first 8 chars of the hash of the Prompt content used) and `model_version` fields; `GET /api/ai/history/{id}` returns `structured_tags` / `quality_reviews` / version fields; the tag differences in `GET /api/ai/compare/{id}` are computed precisely from structured snapshots (existing logs automatically fall back to real-time parsing).
>
> Historical data backfill (one-time migration; parses `raw_response` and writes snapshots and version fields):
>
> ```bash
> cd backend
> python scripts/backfill_structured.py            # preview how many records will be processed
> python scripts/backfill_structured.py --apply    # actually write them
> ```


### Quality Review

| Method | Path | Description |
| ------ | ------ | ------ |
| `POST` | `/api/ai/quality-check` | Batch review all pending image inspirations (async task, returns `task_id`) |
| `POST` | `/api/ai/quality-recheck` | Re-review all approved inspirations: reset to pending, then re-judge with the latest standard (async task, returns `task_id`) |
| `GET` | `/api/ai/quality-stats` | Quality review stats (pending/approved/rejected/approval rate) |
| `GET` | `/api/ai/manual-upload-auto-approve` | Get the "manual uploads auto-approved by default" config |
| `PUT` | `/api/ai/manual-upload-auto-approve` | Set "manual uploads auto-approved by default" (`enabled=true/false`; optionally persisted to .env) |
| `DELETE` | `/api/inspirations/quality-rejected` | Move all rejected inspirations to trash (soft delete, restorable) |

> **Review standard:** To be judged "qualified", the photo must be a complete real-person outfit photo where the overall look is clearly visible. Unqualified cases include: no person (flat lay/size chart/advertisement/pure text), single-item close-ups only, partial/cropped close-ups (e.g. only legs/feet/arms/collar), and excessive cropping of the composition.

> **Manual upload auto-approval:** Enabled by default (config item `manual_upload_auto_approve`, corresponding to `MANUAL_UPLOAD_AUTO_APPROVE` in .env). When enabled, manually uploaded inspirations are marked "approved" directly and don't enter the pending-review queue; when disabled, they go back to pending. Toggle it with one click in the "AI Model Management → Quality Review" panel.

### Negative-Sample Pre-Filter (Pre-Screening Before Quality Review)

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/ai/quality-learner/status` | Pre-filter status + current positive/negative sample stats |
| `POST` | `/api/ai/quality-learner/train` | Train/retrain the sklearn classifier with positive/negative samples (returns metrics) |
| `POST` | `/api/ai/quality-learner/reset` | Delete the model and roll back to pure VLM review |

> **Notes:** The pre-filter trains a lightweight logistic regression on CLIP image vectors (512-dim, LanceDB) from "trash `质量差` negative samples + `rejected` inspirations + `approved` positive samples", serving as a pre-screen before quality review: high-confidence junk is rejected directly, while low-confidence cases still go through VLM re-review ("quality over quantity"). The threshold is in `quality_classifier_threshold`, and the manual override mechanism is kept as-is. The "AI Model Management → Quality Review" page has status/metrics/train/rollback panels; you can also operate via the script `python scripts/quality_learner.py status|train|reset`.

### Task Queue

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/tasks` | Task list (paginated; filterable by status/type) |
| `GET` | `/api/tasks/{id}` | Task detail (frontend polls progress) |
| `POST` | `/api/tasks/{id}/cancel` | Cancel a queued task (only pending tasks can be cancelled) |

> The following heavy operations are all implemented as "database-driven async tasks": the endpoint immediately returns a `task_id`, executed serially by a standalone worker process (`python -m app.worker`) with automatic retries (2 times, exponential backoff). If the worker is not running, tasks stay "queued" indefinitely.
>
> | type | Triggering Endpoint | Description |
> | ---- | -------- | ---- |
> | `batch_analyze` | `POST /api/ai/batch-analyze` | Batch AI analysis |
> | `quality_check` | `POST /api/ai/quality-check` / `quality-recheck` | Batch quality review / re-review |
> | `batch_delete` | `POST /api/admin/batch-delete` | Batch delete inspirations |
> | `deduplicate` | `POST /api/admin/deduplicate` | Smart deduplication deletion |

### AI Parameter Tuning

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/ai/settings` | Get analysis parameters (with global defaults) |
| `PUT` | `/api/ai/settings` | Update parameters (confidence threshold persisted globally to .env; timeouts persisted per model) |
| `GET` | `/api/ai/sampling-params` | Get sampling parameters (with global defaults) |
| `PUT` | `/api/ai/sampling-params` | Update sampling parameters (persisted independently per model to model_configs.json) |
| `DELETE` | `/api/ai/model-config` | Clear the current model's custom config and fall back to global defaults |
| `POST` | `/api/ai/retry-all-failed` | Retry all failed analyses (images only) |
| `DELETE` | `/api/ai/reset?confirm=yes` | Reset all data + files (destructive endpoint, API Key required) |

> **Reset scope:** `/api/ai/reset` clears inspirations, tags, inspiration-tag links, analysis logs and their structured snapshots/review results, scraping tasks, and the URL tombstone table, and deletes image/thumbnail/video and vector-store files. It does **not** include "persons", "scheduled scraping plans (scraper_schedules)", "task queue (task_queue)", or "operation audit logs (audit_logs)" — these management data are kept after the reset.
>
> **Note:** Video files don't participate in AI analysis for now. WebP images are automatically converted to JPEG for compatibility with the Qwen3-VL model.

### Scraping Management

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/scraper/sources` | Available scraping sources, status, and tombstone table counts |
| `GET` | `/api/scraper/stats?days=30` | Scraping stats (total/success rate/platform distribution/daily trends) |
| `GET` | `/api/scraper/cdp-check/{port}` | Check whether the Chrome debug port is ready |
| `POST` | `/api/scraper/chrome/start` | Have the backend launch the scraping-dedicated Chrome (debug mode) |
| `POST` | `/api/scraper/chrome/stop` | Stop the scraping-dedicated Chrome |
| `GET` | `/api/scraper/chrome/status` | Connection status of the scraping-dedicated Chrome |
| `GET` | `/api/scraper/cookie-status?platform=` | Cookie status (existence/expiry/validity) |
| `POST` | `/api/scraper/cookie-import` | Import platform cookies (JSON array) |
| `DELETE` | `/api/scraper/cookie/{platform}` | Delete platform cookies |
| `POST` | `/api/scraper/tasks` | Create scraping task (supports `sort_mode`; pre-checks Chrome connection in Xiaohongshu CDP mode) |
| `GET` | `/api/scraper/tasks` | Task list (`platform`/`status` filter + `sort` ordering + `page`/`size` pagination; returns `items`/`total`/`stats`) |
| `DELETE` | `/api/scraper/tasks` | Clear all scraping task records |
| `DELETE` | `/api/scraper/tasks/{id}` | Delete a single task record (inspirations are kept; links are cleared) |
| `POST` | `/api/scraper/tasks/{id}/cancel` | Cancel a running task |
| `POST` | `/api/scraper/tasks/{id}/retry` | Resume a single task (checkpoint resume) |
| `POST` | `/api/scraper/tasks/retry-failed` | Retry all failed tasks |
| `GET` | `/api/scraper/tasks/{id}/log` | Task log (last 200 lines) |
| `GET` | `/api/scraper/tasks/{id}/results` | List of inspirations produced by the task (paginated) |
| `POST` | `/api/scraper/tasks/{id}/results/batch-delete` | Batch delete the task's produced inspirations |
| `POST` | `/api/scraper/extension-tasks` | Extension scraping session start (creates a task record, returns `task_id`) |
| `POST` | `/api/scraper/extension-tasks/{id}/complete` | Extension session end (summarizes found/imported counts and marks complete) |
| `GET` | `/api/scraper/schedules` | Scheduled scraping plan list |
| `POST` | `/api/scraper/schedules` | Create scheduled plan (platform/keywords/count/sorting/interval/enabled) |
| `PATCH` | `/api/scraper/schedules/{id}` | Update plan (enable-disable/change interval/change keywords, etc.) |
| `DELETE` | `/api/scraper/schedules/{id}` | Delete scheduled plan |
| `POST` | `/api/scraper/schedules/{id}/run` | Run a plan immediately |

> **Checkpoint resume:** Failed tasks can be "resumed", continuing from where they left off using the execution plan (keywords × sorting) stored in `resume_token`; images already imported are not scraped again.
>
> **Sorting scope:** `sort_mode` (`general`/`latest`/`popular`) only takes effect in Xiaohongshu search mode; the Douyin web version always uses comprehensive sorting.
>
> **Scheduled scraping:** The backend scheduler loop checks for due plans every 30 seconds and creates tasks; disabling a plan or changing its interval recalculates `next_run_at`, and failed executions advance normally and can be investigated via task records.
>
> **Extension task records:** When the browser extension uploads inspirations, it can carry the `scraper_task_id` form field (`POST /api/inspirations`) to link inspirations to the extension scraping task for result preview and stats; the extension session creates/summarizes task records via the two `extension-tasks` endpoints.

### Admin Panel

| Method | Path | Description |
| ------ | ------ | ------ |
| `GET` | `/api/admin/stats` | Inspiration overview stats (includes tombstone table counts) |
| `GET` | `/api/admin/largest-files` | Top 20 largest files |
| `GET` | `/api/admin/integrity-check` | Data integrity check (missing/orphaned files) |
| `GET` | `/api/admin/duplicates` | File hash duplicate detection |
| `GET` | `/api/admin/check-duplicate?hash=` | Pre-upload deduplication (MD5 check) |
| `POST` | `/api/admin/cleanup-orphans` | Clean up orphaned files |
| `POST` | `/api/admin/batch-delete` | Batch delete inspirations (by ID or criteria; async task, returns `task_id`) |
| `POST` | `/api/admin/batch-unmark-ai` | Batch re-mark suspected-AI inspirations as non-AI (by ID list; synchronously returns `updated`) |
| `POST` | `/api/admin/deduplicate` | Smart deduplication deletion (async task, returns `task_id`) |
| `GET` | `/api/admin/vector-stats` | Vectorization status stats (total inspirations/existing image and text vectors/missing count/LanceDB availability) |
| `POST` | `/api/admin/vector-backfill` | One-click backfill task for inspirations with missing vectors (async, returns `task_id`; returns `count=0` when nothing is missing) |
| `POST` | `/api/admin/crop-phone-screenshots/scan` | Phone-screenshot cropping: scan candidates (read-only; manual-upload portrait screenshots + black-band/screenshot-feature detection + confidence grading) |
| `POST` | `/api/admin/crop-phone-screenshots/apply` | Phone-screenshot cropping: execute for the checked IDs (original backup/crop replace/thumbnail & hash rebuild/vector backfill; on content duplication returns `duplicates` comparison data, previews temporarily stored in `storage/_crop_dups/` for manual decision) |
| `GET` | `/api/admin/export` | Export all inspirations as CSV (includes tags/persons/review status; triggers a browser download) |
| `GET` | `/api/admin/trend?days=` | Daily new-inspiration count trend (last N days) |
| `GET` | `/api/admin/person-frequency?limit=` | Person × inspiration count ranking |
| `GET` | `/api/admin/audit-logs?limit=` | Operation audit logs (reverse chronological) |
| `GET` | `/api/admin/near-duplicates?limit=&threshold=` | Near-duplicate detection (perceptual hash grouping; only returns candidates, doesn't delete) |

### Other

| Method | Path | Description |
|------|------|------|
| `GET` | `/api/health` | Health check (returns `schema_version`; the frontend uses it to verify the frontend-backend contract) |
| `GET` | `/api/files/{path}` | Static file access |
| `WS` | `/ws` | WebSocket real-time push |

> **Schema version handshake:** The `schema_version` returned by `/api/health` is composed of "database schema hash (automatically computed from the column/index inventory in `db_migrations.py`) + API contract version (`API_CONTRACT_VERSION`, manually incremented)". The expected value on the frontend is no longer maintained by hand: at dev startup / build time, `scripts/compute_schema_version.py` calls backend code to compute it automatically and injects it as the global constant `__SCHEMA_VERSION__` (see `web/vite.config.ts`). On mismatch, a notice pops up at the top of the page, preventing "silent failures" caused by backend updates without restart; after backend changes, restarting the frontend dev server / rebuilding aligns automatically.

## Security Hardening (API Key)

The backend does **not** enable authentication by default (development mode). If the service is exposed to a LAN or the internet, it's recommended to enable API Key protection for destructive endpoints.

**Protected scope:** irreversible or batch-destructive endpoints such as data reset, batch delete, empty trash, deduplication deletion, tag delete/merge, model unload, and person deletion; read endpoints and ordinary write operations (upload, favorite, move to trash, etc.) are unaffected.

```bash
# 1. Generate a key and get the enable instructions
python scripts/generate_api_key.py

# 2. Append the printed key to backend/.env (the script prints full instructions)
#    API_KEY=<generated key>

# 3. Restart the backend to apply
bash scripts/restart.sh
```

**Behavior after enabling:**
- Destructive endpoints must carry the `X-API-Key` request header; a missing header returns `401`, a wrong key returns `403`
- Read endpoints don't need a key and remain accessible

**Frontend integration:** run `localStorage.setItem('apiKey', '<key>')` in the browser console and refresh the page; frontend requests will automatically attach the `X-API-Key` header. Alternatively, set the `VITE_API_KEY` environment variable at build time.

**Notes:** `X-API-Key` is simple shared-secret authentication that only guards against accidental/unauthorized calls; it doesn't replace HTTPS or a user system. The destructive endpoint list is maintained in `DESTRUCTIVE_ROUTES` in `backend/app/utils/auth.py`; just append a line there when adding a new destructive endpoint.

## Automated Testing

Regression protection for core paths: backend `pytest` (integration tests + service unit tests) + frontend `vitest` (pure functions / composables / stores).

**Run all tests with one command** (backend + frontend type check + vitest, Git Bash):

```bash
bash scripts/test.sh          # regular run
bash scripts/test.sh --cov    # also output a coverage report for the backend
```

### Backend (pytest, 248 Test Cases)

```bash
# First time: install test dependencies
cd backend
pip install -r requirements-dev.txt -i https://mirrors.aliyun.com/pypi/simple/

# Run all tests (automatically uses a temporary database and temporary storage; never touches real data)
pytest
```

Coverage:
- **Integration tests**: health check, API Key authentication for destructive endpoints (401/403, read endpoints unaffected), inspiration upload/detail/favorite/content deduplication (SHA-256)/platform ID deduplication/**soft delete filtering**/physical deletion, trash move-in/restore/empty/reason filtering/expired cleanup/**state invariant checks** (soft-delete three fields must be all-set or all-clear; R1/R2/R3 violations detected), tag create/conflict/link/idempotency/unlink, combined keyword and tag search, person CRUD/type distinction/linking/style profile/unlink/delete, **batch operations** (batch favorite/move to trash/edit metadata/tag and dominant color filtering), **admin insights** (CSV export/new trends/person frequency/audit logs/near-duplicate detection), **phone screenshot cropping** (candidate scan/black-band detection/screenshot-feature confidence/skip details/duplicate comparison previews/recrop after physically deleting the duplicate/recropping one item keeps other previews), **task runners** (batch delete tasks: delete records + delete files + free space; vector backfill/quality check anti-fake-success: all-failed raises task-level errors, partial-failed completes normally), **AI analysis and quality review** (full analysis saves tags, review binary classification pass/reject, master tag suggestions, quality stats, batch review/re-review task creation — all with mocked Ollama), **scraping module** (full extension session task flow and result batch deletion, task list pagination/filter/sort/stats, scheduled plan CRUD/enable-disable/run now, cookie import/delete/status, stats aggregation)
- **End-to-end journey tests** (`test_journeys.py`, verify the connections between stages rather than single stages in isolation): material full journey (upload → tagging → vectors → trash → restore → re-trash → purge, asserting zero invariant violations plus tombstone/audit trail at every stage), scraping journey (extension session → from-url ingestion → task completion → deletion → tombstone → re-scrape rejected, including the anti-duplication loop where the tombstone persists after restore), failure journey (missing-file self-healing: trash/restore never leaves dangling records), crash journey (worker heartbeat timeout → `_reset_stale_tasks` reset → re-run succeeds, no fake success)
- **Service unit tests**: `tag_normalizer` (synonym normalization/similarity/name validation), `ai_parser` (malformed JSON repair/tag extraction/truncation detection), `quality_learner` (training/insufficient samples/rollback, vectors mocked), `image_hash` (perceptual hash near-invariance/distinctiveness/Hamming distance/invalid files), `deduplicate` (dedup scoring/retention suggestions/ties/missing-file fallback/physical deletion), `csv_safety` (CSV formula-injection escaping)

> **Coverage measurement:** after installing `pytest-cov`, run `pytest --cov --cov-report=term-missing` to produce line-level coverage (`backend/.coveragerc` already configures `source=app` and excludes boilerplate code; currently about 52%). The remaining low-coverage blind spots are concentrated in: real scrapers (`scrapers/`, 0%, require a real browser), deep branches of `vector/similarity`, batch retry/retry-all in `ai_analysis_service`, and `ws.py` (WebSocket).

### Frontend (vitest, 80 Test Cases)

```bash
cd web
npm test
```

Coverage: `format` / `sourceLabel` / `taskLabel` / `browseQuery` pure functions, `useSplitResize` drag / `useBatchSelection` batch multi-select composables, `persons` / `inspirations` / `tags` stores (mocked API + request sequence numbers to prevent out-of-order responses).

### Conventions

- Tests use **temporary databases and temporary storage directories** and never touch the real `storage/` or `fashion_inspo.db`; if test files were accidentally written to real directories, clean them up with `python scripts/clean_test_files.py --delete`
- When adding a destructive endpoint or modifying core paths (soft delete/trash/deduplication/authentication), add test cases in the corresponding `backend/tests/` or `web/src/**/__tests__/` and make sure they pass

## Environment Requirements

| Software | Purpose | Required? |
| ------ | ------ | :---: |
| Python 3.12+ | Backend | ✅ |
| Node.js 20+ | Web + Mobile frontend | ✅ |
| Ollama | AI vision inference | ✅ |
| Qwen3-VL:8B-Instruct | Outfit tag recognition | ✅ |
| Google Chrome | CDP scraping host browser | [Warning] Required when scraping |
| Playwright | Scraping engine driver | [Warning] Required when scraping |
| ffmpeg | Video keyframe extraction | [Failed] Not yet used |

## Open Source License

This project is open-sourced under the **Apache License 2.0**; see [LICENSE](./LICENSE) for details.

- Free to use, modify, and distribute (including commercially), with the copyright notice and a copy of this license retained
- Modified files must indicate changes; derivative works are not required to be open-sourced
- For platform scraping features, comply with the target platform's terms of service and applicable laws, and keep scraping frequency reasonable
