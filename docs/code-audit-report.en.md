# Code Audit Report

> This report documents a comprehensive code audit of the fashion-inspo full-stack project (backend / web frontend / mobile app / browser extension / scripts), covering three dimensions: security, correctness, and code quality. The audit was conducted through parallel subagents performing in-depth reads per domain, plus independent manual verification of critical paths.
>
> Per user request, **all network connectivity issues are ignored** (Ollama connection failures, scraper network timeouts, CDP ports, external API reachability, etc.); however, security flaws involving URLs — such as SSRF, protocol bypass, and cookie leakage — are still recorded as-is and labeled "Network-related · Ignored".

---

## 1. Summary

| Severity | Count | Fixed | Pending |
| ------ | ------ | ------ | ------ |
| Critical | 7 | 5 | 2 (both network-related, ignored) |
| High | 14 | 8 | 6 (including 3 network-related, ignored) |
| Medium | ~27 | 6 | ~21 |
| Low | ~40 | 0 | ~40 |

Overall quality is good: fully parameterized SQL, no shell command injection, correct path traversal protection on the file service main path, proper transaction rollback and SAVEPOINT race handling, no v-html injection in the frontend, ESLint 0 errors, and a complete Alembic migration chain.

---

## 2. Critical Issues (7 items)

### S1 Authentication fail-open: destructive endpoints exposed under default config ✅ Fixed (loud warning)
- `config.py:116`: api_key defaults to empty + `main.py` middleware skips authentication when empty; destructive endpoints have no authentication when bound to a non-loopback address.
- Fix: emit a loud warning at startup when binding a non-loopback address without an API_KEY configured (warning chosen over hard rejection to avoid breaking existing `HOST=0.0.0.0` deployments).

### S2 Stored XSS: no upload extension whitelist + same-origin amplification via frontend proxy ✅ Fixed
- `file_service.py:31` preserves the original extension + `files.py` serves files by extension + Vite proxy makes it same-origin. A polyglot image named `.html` can pass PIL validation and execute on the 17777 origin, stealing `localStorage.apiKey`.
- Fix: extension whitelist in `_generate_filename` (non-image/video extensions fall back to `.jpg`) + `/api/files` serves only whitelisted media MIME types.

### S3 SSRF: from-url import has no protocol/intranet validation ⛔ Network-related · Ignored
- `inspiration_service.py:185-258`: arbitrary URLs can probe the intranet and read back its content.

### S4 Data loss: "analysis failed" batch deletion uses any semantics and deletes recovered items ✅ Fixed
- `admin.py:216-228` uses an "any failed log" check, which physically deletes items that "failed historically but succeeded in the latest run".
- Fix: unified to the "latest log entry failed" check (`latest_analysis_log_subquery`).

### S5 Zombie task cleanup and seed tag import silently no-op at startup ✅ Fixed
- `main.py:105-120`: two UPDATE/seed operations lack a commit; they are rolled back when `async_session` exits.
- Fix: added `await db.commit()` to each.

### S6 Extension CDN domain not authorized ⛔ Network-related · Ignored
- `browser-extension/manifest.json` is missing the CDN domain; SW cross-origin fetches are blocked by CORS.

### S7 API key generation script path mismatches backend .env ✅ Fixed
- `scripts/generate_api_key.py` writes to the project root, while `config.py` reads `backend/.env`.
- Fix: script aligned to `backend/.env`.

---

## 3. High-Risk Issues (14 items)

| # | Issue | Location | Status |
| --- | ------ | ------ | ------ |
| H1 | Destructive endpoint list gap (single log deletion etc. not covered) | `utils/auth.py:33-60`, `ai_analysis.py:271` | ⏳ Pending |
| H2 | Scraper download sends full cookies to third-party image hosts | `run_scraper.py:346-354` | ⛔ Network · Ignored |
| H3 | delete_inspiration deletes files/vectors before commit, failure leaves dangling rows | `inspiration_service.py:866-890` | ✅ Fixed |
| H4 | analysis_status filter still uses any semantics, contradicting the card's latest | `inspiration_service.py:371`, `search.py:112` | ✅ Fixed |
| H5 | delete_failed_logs wrongly deletes quality review logs | `ai_analysis_service.py:362` | ✅ Fixed |
| H6 | Vector batch delete bypasses the cross-process write lock | `vector/store.py:426` | ✅ Fixed |
| H7 | Scraper still marks run as completed when all network calls fail, distorting success rate | `run_scraper.py:1168` | ⛔ Network · Ignored |
| H8 | 5 `except: pass` silently swallow exceptions | `inspiration_service.py`, `scraper_service.py` | ✅ Fixed |
| H9 | Inconsistent log directories + dual derivation of DB path | `health_service.py:35`, `db_migrations.py` | ⏳ Pending |
| H10 | No enforcement of the type annotation system (89 defs lack return annotations) | codebase-wide | ⏳ Pending |
| H11 | Frontend source_url has no protocol whitelist + missing rel | `DetailView.vue` | ✅ Fixed |
| H12 | Frontend profile_url has no protocol whitelist | `PersonDetailView.vue`, `PersonFormModal.vue` | ✅ Fixed |
| H13 | getFileUrl does no URL encoding | `api/inspirations.ts:78` | ✅ Fixed |
| H14 | Alembic fallback missing task_queue.claimed_by/heartbeat_at | `db_migrations.py:27-54` | ✅ Fixed |

---

## 4. Medium-Risk Issues (key items)

| # | Issue | Location | Status |
| --- | ------ | ------ | ------ |
| M1 | `_parse_iso_dt` drops timezone without converting to UTC (filter offset by 8h) | `ai_analysis_service.py:171` | ⏳ Pending |
| M2 | Quality review pseudo-CAS can override manual reversals | `ai_service/quality.py:119` | ✅ Fixed (conditional UPDATE) |
| M3 | cleanup-orphans TOCTOU with uploads deletes in-flight files | `admin.py:129` | ✅ Fixed (mtime grace period) |
| M4 | save_upload doesn't clean up orphan files on validation failure | `file_service.py:195` | ✅ Fixed |
| M5 | Trash restore hits a unique index conflict → 500 | `inspiration_service.py` | ✅ Fixed (returns 409) |
| M6 | content_hash dedup has no unique constraint (concurrent duplicate inserts) | `inspiration_service.py`, `models/inspiration.py` | ⏳ Pending |
| M7 | Dashboard stats mix any/latest semantics | `admin_stats_service.py` | ✅ Fixed |
| M8 | Chrome idle detection relies on in-process state (multiple workers may wrongly shut it down) | `chrome_manager.py:166` | ⏳ Pending |
| M9 | batch_add_tags has no SAVEPOINT (concurrent 500s) | `inspiration_service.py:663` | ⏳ Pending |
| M10 | Retry targets don't exclude trashed items | `ai_analysis_service.py:304` | ⏳ Pending |
| M11 | record_audit_log/_write_analysis_log are not independent transactions | `audit_service.py`, `analyze.py` | ⏳ Pending |
| M12 | Synchronous disk I/O / N+1 inside the event loop | `admin.py:109/132/164`, `search.py:300` | ⏳ Pending |
| M13 | Platform names hardcoded in 11+ places, time formatting in 13+ places, 15 groups of duplicated implementations | codebase-wide | ⏳ Pending |
| M14 | Frontend: UploadView auto-analysis silently fails, inspirations store has no error state, SettingsPanel can save 0 values and overwrite config, useSearch keeps stale results after clearing tags | frontend | ⏳ Pending |

---

## 5. Low-Risk Issues (summary, ~40 items)

- **Frontend**: immediate revoke compatibility in blob downloads, bare fetch bypassing apiClient, pagination without upper bound, 6 unused variables, unreachable 204 branch in `deleteSingleTask`, magic numbers in polling, API Key `localStorage` guidance practice.
- **Backend**: plaintext API key comparison (timing), test-analyze reads memory without limits, WS without authentication, CSV formula injection, unused import in `admin.py`, UUID substring matching with `.contains()`, three-level nested ternary, ~25 long lines, ~150 single quotes, redundant in-function imports.
- **Mobile/scripts**: `clean_test_files.py` deletion risk, `popup.js` innerHTML attribute injection, three-layer type drift (mobile doesn't use shared/types), extension autoAnalyze toggle ineffective, `batch_import.py` reads whole file into memory, `restart.sh:41` port substring false kill, `ensure-services.sh:38` arithmetic hazard.

---

## 6. Fix Progress

| Commit | Contents |
| ------ | ------ |
| `2bd602c` | Scraper-managed result deletion changed to moving into trash (soft delete) |
| `010dbc8` | Analysis status wrongly marked as failed fixed (latest semantics + historical tag columns) |
| `a8e9e64` | Audit batch 1: authentication warning, upload whitelist, analysis status latest, deletion transaction order, frontend XSS/URL encoding |
| `0541aa6` | Audit batch 2: vector write lock, silently swallowed exceptions, TOCTOU, pseudo-CAS, restore conflict 409, db_migrations fallback |

**Suggested priority for pending items**:
1. H1 destructive endpoint list gap (add single-delete endpoints such as `ai_analysis.py:271`).
2. M1 `_parse_iso_dt` timezone fix (one-line `astimezone`).
3. M9 add SAVEPOINT to batch_add_tags; M6 partial unique index on content_hash.
4. H9 single-source the log/DB paths; H10 establish a mypy/ruff type baseline.
5. M13 quality consolidation (duplicated implementations such as time formatting, soft-delete file moves, Ollama calls).

---

## 7. Positive Confirmations

- Fully parameterized SQL (including `?` placeholders in `run_scraper.py`), no `shell=True`, no ReDoS.
- Correct path traversal protection on the file service main path (`files.py` uses `resolve()` + `is_relative_to()`).
- `expire_on_commit=False` consistent globally, `get_db` auto-rollback, atomic conditional-UPDATE claiming for worker/scheduled/cancelled tasks, correct SAVEPOINT race handling in `get_or_create_tag`/`link_tag`, idempotent `seal_urls` with OR IGNORE, trash "commit-first-then-move-files" self-healing design.
- Complete Alembic migration chain (head=51841a0c0163), sensible WAL + busy_timeout configuration.
- No v-html injection in the frontend, model `raw_response` rendered as text via `<n-code>`, cookies never stored in localStorage, polling generation number prevents re-entrancy, ESLint 0 errors.

---

## 8. Post-Audit Governance (added 2026-08-17)

Landing record for the "reduce hidden bugs in core paths" plan (Plans A/B/C):

### Plan A: Defense Baseline (done)

| Commit | Content |
| ------ | ------ |
| `ce6c94b` | 5 review fixes: `file_sha256` moved to thread pool; task anti-fake-success check moved before writing the completed state (quality_check/vector_backfill); photo deletion ownership check; crop backup filename timestamp; arrow-key navigation disabled while modal open |
| `15fe94d` | Soft-delete state machine: `trash_state` property + `_mark_trashed`/`_mark_restored` single-point writes (with transition legality assertions); `verify_trash_invariants` (rules R1/R2/R3, the three soft-delete fields must be all-set or all-clear) wired into the admin integrity check; audit trail added for single trash/restore; also fixed scraper-result deletion missing `trash_source` |
| `9d4bd1f` | CLAUDE.md gained a "path-walkthrough review" convention: core paths are reviewed end-to-end (router → service → worker → frontend) |

### Plan B: End-to-End Journey Tests (done)

| Commit | Content |
| ------ | ------ |
| `139a026` | `tests/test_journeys.py` with 4 journey tests: material full journey (upload → tagging → vectors → trash → restore → re-trash → purge, asserting zero invariant violations plus tombstone/audit trail throughout), scraping journey (extension session → from-url ingestion → tombstone → re-scrape rejected, including the anti-duplication loop after restore), missing-file self-healing, worker crash (heartbeat timeout → reset → re-run succeeds). Vector generation uses a fake to stay independent of real CLIP/LanceDB |

### Plan C: Idempotency and DB Unique Constraints (registered in TODO, pending)

- Add UNIQUE on the tombstone table `scraper_seen_urls`; partial unique index on `source_platform_id` (non-deleted only, preserving the "trash releases platform ID" semantics) — evaluate existing duplicate rows before migrating; add idempotency assertions to task runners.
