# Project Overview (AI Fashion Inspiration Library)

> This document is intended for people approaching the project for the first time (or collaborators who need a quick grasp of the current state). It covers the project's positioning, tech stack, feature completion, data scale, recent progress, technical debt, and next steps. For detailed installation, usage and API docs, see [README](../README.en.md); the TODO list is in [TODO](../TODO.md); coding standards are in [CLAUDE](../CLAUDE.md).

---

## 1. Project Positioning

**AI Fashion Inspiration Library (fashion-inspo)** is a personal outfit inspiration management tool: through automated scraping (Xiaohongshu / Douyin / browser extension) and local vision AI recognition, it turns fragmented outfit content into a personal inspiration asset that supports intelligent retrieval (keywords / tags / colors / semantics / reverse image search).

Core features: **fully local operation** (no cloud dependency), **AI tagging + quality review loop**, **multi-channel scraping**, and **creator/blogger dimension management**.

---

## 2. Tech Stack

| Layer | Technology | Notes |
| ------ | ------ | ------ |
| Backend | Python 3.12 + FastAPI + SQLAlchemy async + SQLite | REST API + WebSocket, port 18888 |
| Task queue | Standalone worker process (`python -m app.worker`) | Batch analysis/review/delete/dedup run asynchronously |
| Web frontend | Vue 3 + Vite + TypeScript + Pinia + Naive UI | Port 17777 |
| Mobile | React Native (Expo) + Zustand | Gallery / search / camera upload |
| Browser extension | Chrome Extension Manifest V3 | One-click extraction of outfit images from web pages |
| AI inference | Ollama + Qwen3-VL:8B-Instruct (local GPU, RTX 5060Ti 16GB) | Vision tagging + quality review |
| Vector retrieval | LanceDB + all-minilm (text, 384-dim) / CLIP ViT-B/32 (image, 512-dim) | Semantic search + reverse image search + similar recommendations |
| Scraping engine | Playwright + CDP connecting to a real Chrome | Xiaohongshu zero-detection, Douyin in a separate browser |
| Database migration | Alembic | Schema version management |

---

## 3. Feature Modules and Completion

### ✅ Completed (usable)

| Module | Notes |
| ------ | ------ |
| Inspiration library | Waterfall browsing, multi-dimensional filtering, sorting, density adjustment, batch operations |
| Advanced search | Keywords / tags (AND/OR) / color / date, semantic search, reverse image search, co-occurrence recommendations, search history |
| Upload inspirations | Drag-and-drop / paste / URL, preview queue, duplicate detection, folder batch, 500-item limit check |
| Inspiration detail | Lightbox, tags, outfit master tags (manual + AI suggestions), similar recommendations, re-analysis |
| Scraping management | Xiaohongshu CDP + Douyin scraping, scheduled scraping, resumable scraping, Cookie management, stats dashboard, URL tombstone dedup |
| Tag management | Grouping / sorting / alias normalization / merging / co-occurrence network / import-export / trends |
| AI model management | Model download / switching, GPU monitoring, batch analysis, parameter tuning, quality review, negative-sample pre-filter |
| Inspiration management | Suspected-AI review, duplicate / near-duplicate detection, integrity checks, trash (soft delete), data insights |
| Creator management | Creator CRUD, content-type distinction (professional model / fashion blogger), style profile, inspiration association, ranking |
| Model photo groups | Import an entire folder into a creator, photo-group browsing / lightbox / deletion (**newest**) |
| Browser extension | One-click extraction; scraping sessions automatically create task records |
| Security | API Key protection for destructive endpoints, upload whitelist, audit logs |

### 🚧 In progress

| Item | Status |
| ------ | ------ |
| Xiaohongshu multi-image and video scraping | Accessing detail pages triggers risk control; rolled back to scraping covers only; retry after a cooldown |
| Video analysis | Video upload / storage supported; keyframe extraction and AI analysis not yet implemented |

### 📋 Planned / long-term

| Item | Priority |
| ------ | ------ |
| Data backup and disaster recovery (export/import + scheduled backups + foolproofing) | High (not started) |
| Scrape by blogger (Phase 4) | Medium |
| AI creator recognition (Phase 5) | Medium |
| LoRA fine-tuning MiniCPM-V (requires ≥3–5k images) | Long-term |

---

## 4. Data Scale and Runtime Environment

- **Inspirations**: 3000+ items (images/videos; tag taxonomy with 8 top-level categories: style / item / color / way of wearing / fit / season / image attribute / outfit master tag)
- **Vectors**: text + image vectors stored in LanceDB (local files on disk, portable with the project)
- **Runtime**: Windows 11, Python 3.12+, Node.js 20+, Ollama, ffmpeg (video keyframes, reserved), Google Chrome (CDP scraping)
- **Storage layout**: under `backend/storage/`: images / thumbnails / videos / trash (trash bin) / person_photos (creator photos) / person_thumbnails / lancedb (vectors)

---

## 5. Recent Progress Roadmap

| Stage | Details |
| ------ | ------ |
| Code audit hardening | Completed a full-stack code audit (security / correctness / quality); 4 batches of fixes landed (upload whitelist, auth alerts, vector write lock, deletion transaction ordering, timezone, etc.). Post-audit governance: 5 review fixes, soft-delete state machine convergence + trash invariant checks + audit trail completion, path-walkthrough review convention (see audit report §8) |
| Quality loop | Trash soft delete + negative-sample pre-filter (CLIP vectors + sklearn logistic regression for pre-screening before review) |
| Service supervision | Process supervision + heartbeat lease + health checks (`scripts/supervisor.py`, `ensure-services.sh`) |
| Frontend engineering | ESLint + Prettier adopted (flat config, 0 errors); vitest unit tests added |
| Creator module | Completed the "creator/blogger" entity migration (Phase 1–3) and shipped content-type distinction |
| Model photo groups | Added "creator photo group" capability: import an entire folder of model photos, separate on-disk storage, photo-group browsing |

---

## 6. Testing and Quality Status

- **Backend**: pytest 248 cases (integration tests + service unit tests + end-to-end journey tests), ~52% line coverage; blind spots are the real scrapers (0%, depend on a real browser), deep branches of vector similarity, and WebSocket.
- **Frontend**: 80 vitest cases (pure functions / composables / stores), ESLint 0 errors.
- **Convention**: tests use a temporary database and temporary storage and never touch real data; changes to core paths (soft delete / dedup / auth / creators) must add test cases.

---

## 7. Known Technical Debt and Risks

> See [code audit report](./code-audit-report.en.md) for details.

| Category | Item | Status |
| ------ | ------ | ------ |
| Data safety | Core data (db / storage / vectors / .env) excluded by .gitignore — no version control or backup; accidental deletion / disk corruption is irreversible | High risk, pending the "backup plan" |
| Security | The destructive-endpoint inventory has gaps (H1) | To be completed |
| Correctness | `_parse_iso_dt` does not convert timezone to UTC (M1), `batch_add_tags` lacks SAVEPOINT (M9), `content_hash` has no unique constraint (M6) | To be addressed |
| Code quality | Log/DB paths derived twice (H9), type annotations not enforced (H10), several duplicated implementations (M13) | To be consolidated |
| Scraping | Xiaohongshu risk control is strict; detail-page scraping was rolled back | Needs cooldown + pacing control |
| Testing | 0% coverage of real scrapers | Depends on a real browser; hard to automate |

---

## 8. Next Steps (Suggested Priorities)

1. **Data backup and disaster recovery** (highest priority): one-click export/import + scheduled backup scripts + confirmation prompt for destructive operations.
2. Close the remaining high/medium-severity items from the code audit (destructive-endpoint inventory, timezone, SAVEPOINT, unique index).
3. Video analysis (keyframe extraction + reuse of the image analysis pipeline).
4. Scrape by blogger (Phase 4), to proceed after the Xiaohongshu risk-control cooldown.
5. Evaluate LoRA fine-tuning once the data volume target is met.

---

## 9. Documentation Index

| Document | Purpose |
| ------ | ------ |
| [README](../README.en.md) | Installation, startup, feature overview, data model, full API docs |
| [TODO](../TODO.md) | Pending feature list (by priority) |
| [CLAUDE](../CLAUDE.md) | Coding standards, project standards, tech stack conventions |
| [Code audit report](./code-audit-report.en.md) | Full-stack code audit findings and fix progress |
| [System assessment report — open-source component adoption](./open-source-components-assessment.en.md) | Evaluation of open-source component selection |
| [CLIP encoding notes](./clip-encoding-notes.en.md) | Notes on the image vector encoding approach |
