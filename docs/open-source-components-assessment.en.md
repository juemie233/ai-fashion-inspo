# System Assessment Report: Whether to Introduce External Open-Source Components (Redis, etc.)

> Assessment subject: AI fashion inspiration library (fashion-inspo)
> Assessment date: 2026-08-16
> Assessment purpose: determine whether the current system needs to adopt non-in-house open-source components such as Redis, PostgreSQL, and Celery, in order to improve the system's usability and robustness.

---

## 0. Positioning Assessment (Premise of the Evaluation)

| Fact | Value |
|---|---|
| Deployment form | **Standalone, Windows 11, local personal use** |
| Data scale | SQLite 21.5MB / 3178 inspirations / 4581 analysis log entries |
| External dependencies | **Zero** heavy components (requirements has no redis/celery/kafka/pg) |
| Already in-house | Task queue (`task_queue` table + dedicated worker), vector search (embedded LanceDB), AI pipeline (local Ollama) |
| Lightweight tools already adopted | Alembic (baseline generated), pytest/vitest, API Key authentication |

**This is an application architecture that is "local-first, single-process, with data at the thousand-item scale"**, not a high-concurrency service. The yardstick for evaluating any component should be: *Which real pain point does adopting it solve today? And once adopted, who bears the cost of guarding/backing up/operating it?*

---

## 1. Conclusion First

**At the current stage, introducing heavy components such as Redis, PostgreSQL, Celery, and MinIO is not recommended** — the complexity is disproportionate to the benefit, and it would introduce new resident processes, backup targets, and troubleshooting surfaces.

What is truly worth investing in, instead, is **robustness engineering that can be done without introducing new components** (backup, process guarding, log rotation, health probes) — these are exactly the two items in TODO's high-priority list that remain unfinished (① data backup, ④ service guarding and monitoring), with priority far above introducing Redis.

---

## 2. Component-by-Component Assessment of Candidates

### 2.1 Not Recommended for Introduction (At the Current Stage)

| Component | Current substitute | Reasons not to introduce |
|---|---|---|
| **Redis** | Cache = in-process/on-disk; queue = in-house `task_queue` + worker | ① For read-heavy/write-light scenarios (tag grouping, model lists, config, inspiration lists), an in-process cache is enough, and cache consistency is even easier to control; ② The in-house queue is fully sufficient at personal scale (batch analysis/review/deduplication tasks); ③ Introducing it = one more **resident process that needs guarding, backup, and memory**, which actually adds failure points on Windows |
| **PostgreSQL / MySQL** | SQLite + SQLAlchemy async | 21.5MB / thousand-scale data — SQLite's single file means backup is just copying (a huge advantage); concurrent write locks (`database is locked`) are extremely unlikely in a **single-user local** scenario, and WAL + retries have already mitigated it. The concurrency/online-backup benefits of switching to PG are imperceptible on a single machine, at the cost of operational complexity and redoing the backup scheme |
| **Celery / RQ** | In-house `task_queue` + `app/worker.py` dispatch table | Celery's distributed-worker advantage is meaningless on a single machine; RQ depends on Redis. The existing queue already supports four task types — batch analysis, quality review, deduplication, and vector backfill — and has retry/pause/progress mechanisms |
| **MinIO / object storage** | Local file system + `storage/` | Inspirations are local files; object storage solves multi-machine sharing, for which there is currently no need |
| **Prometheus + Grafana** | `/api/health` + logs | The full monitoring stack is overkill for a single-machine app; a lightweight probe + a frontend health card is enough |

### 2.2 Worth Introducing (Lightweight Tools/Practices, Not Heavy Components)

| Item | Approach | Real pain point addressed |
|---|---|---|
| **Process guarding and auto-restart** | Register `uvicorn` and `worker` as Windows services via NSSM / WinSW (or scheduled tasks + a watchdog script) | The core of TODO high-priority item ④: **the worker is a single point of failure — after a crash, tasks are permanently stuck** (the `--reload` crash event has already exposed this problem) |
| **Log rotation** | Python `RotatingFileHandler` (one for backend and one for worker, split by size/date) | Logs grow unboundedly and become hard to troubleshoot; zero new dependencies |
| **Automatic backup** | Backup script (DB snapshot + `storage/` incremental) + Windows scheduled task, keeping multiple historical copies | TODO high-priority item ①: all 3,000+ inspirations rely on local disk with no backup at all; `ai/reset` can wipe everything in one click |
| **Alembic migrations** | ✅ **Already adopted** (baseline `1aef95ac59` generated) | Replaces hand-written `db_migrations`; future field additions go through revisions |
| **In-process cache** (optional) | Add TTL caching to read-heavy, write-light endpoints such as tag grouping, model lists, and config | Currently every request re-queries the DB; the benefit at personal scale is small — nice to have |

### 2.3 A Real Boundary of the Current Architecture (For Decision-Making Reference)

`_active_analyses` / `_pending_queue` in `ai_shared.py` are **in-process memory state** — not shared between the API process and the worker process. The current single-worker architecture is fine, but in the future: ① adding a second worker instance, ② an API process restart would lose the in-memory view of the queue. **This is the only motivation point that could make Redis (shared queue / pub-sub) reasonable in the future** — but the trigger condition is "multiple workers / frequent process restarts", and at personal scale the status quo is acceptable.

---

## 3. Recommended Priority Roadmap

```
Phase A (strongly recommended, zero/minimal new dependencies, maps to TODO high-priority ①④)
  ├─ Backup script + scheduled task (data reversibility, most urgent)
  ├─ Process guard: register uvicorn + worker as services via NSSM/WinSW
  ├─ Log rotation: RotatingFileHandler
  └─ Health probe / frontend health card (/api/health already exists, add the display layer)

Phase B (optional, clear benefit and lightweight)
  ├─ ruff static checks + black formatting (code quality, usable in CI)
  ├─ uv unified package management (optional)
  └─ In-process TTL cache (read-heavy endpoints)

Phase C (only re-evaluate Redis/PostgreSQL/Celery when these signals appear)
  ├─ Multi-user / multi-instance / server deployment
  ├─ Inspiration volume reaches hundreds of thousands, vector search becomes a bottleneck
  ├─ Scraping/AI concurrency rises significantly, cross-process shared task state needed
```

---

## 4. One-Sentence Summary

**The "weak spot" of the current architecture is not the lack of Redis/message queue, but the lack of guarding and backup** — the former is for systems that scale, while the latter is the real risk point for a single-machine app with 3,000+ inspirations. Implementing "data backup" and "service guarding and monitoring" from the TODO (both lightweight scripts/tools) will improve robustness far more than introducing Redis.

---

## Appendix: Decision Signal Checklist (When to Re-evaluate Heavy Components)

| Signal | Trigger condition | What to introduce |
|---|---|---|
| Multi-instance deployment | Deployment to a server / multi-user access | PostgreSQL + redo the migration scheme |
| Cross-process shared task state | Multiple workers / frequent API restarts causing loss of queue view | Redis (queue + pub/sub) |
| High-concurrency scraping/AI | Concurrent task volume rises significantly | RQ/ARQ (Redis queue) or Celery |
| Inspiration scale of hundreds of thousands | Vector search / list queries become a bottleneck | Standalone vector database + cache layer |
