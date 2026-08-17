# SQLite Database Capacity Assessment

> Assessment date: 2026-08-13
> Database: `backend/fashion_inspo.db` (SQLite + SQLAlchemy async + aiosqlite)

## 1. Current Status

| Metric | Value |
| ------ | ------ |
| Database file | 3.69 MB (945 pages × 4KB) |
| Inspirations `inspirations` | 993 rows |
| Tags `tags` | 1,687 rows |
| Tag links `inspiration_tags` | 14,028 rows (avg 14.1 tags per inspiration) |
| Analysis log `ai_analysis_log` | 1,039 rows (`raw_response` avg 599 bytes, max 3.2KB) |
| Scraped URLs `scraper_seen_urls` | 814 rows |
| Journal mode | `delete` (rollback journal, not WAL) |

Estimated data size per table:

| Table | Data bytes | Notes |
| ---- | --------- | ------ |
| `ai_analysis_log` | ≈ 665 KB | One row written per analysis/retry, includes full model output |
| `inspiration_tags` | ≈ 571 KB | Many-to-many links, grows ~14× faster than the inspirations table itself |
| `inspirations` | ≈ 268 KB | Main inspirations table |
| `scraper_seen_urls` | ≈ 129 KB | Scraper deduplication set |
| `tags` | ≈ 74 KB | Tag dictionary |

The data is concentrated in the last 2 days (08-11 ~ 08-13), growing at roughly **500 inspirations/day**.

## 2. Marginal Cost per Inspiration

Roughly **3–4 KB per inspiration** (including indexes); the bulk comes from two linearly growing link tables:

- `ai_analysis_log`: one row written per analysis; retries stack up;
- `inspiration_tags`: 14.1 links per inspiration, growing faster than the inspirations main table.

## 3. Three Independent "Breaking Point" Thresholds

SQLite's bottleneck is **not file size**, but the following three factors, in order:

### 1. Storage Capacity — the Least of Your Worries

SQLite has a hard limit of 281 TB; in practice it stays smooth up to tens of GB.

| Inspirations | Estimated file size |
| -------- | --------- |
| 10K | ~37 MB |
| 100K | ~370 MB |
| 1M | ~3.7 GB (starts getting heavy; backup/VACUUM slow down) |
| 10M | ~37 GB (not recommended) |

At the storage level, **SQLite can handle up to 1 million inspirations**.

### 2. Concurrent Writes — the Real Ceiling, and It Arrives Before Size ⚠️

SQLite is a **single-writer** database: only one write transaction is allowed at a time. Current configuration:

- `journal_mode = delete`: writes block even reads during a write transaction;
- Concurrent writers in the background: AI analysis (semaphore ~16 concurrent, each writes log + tags), scraper batch inserts, user uploads/tagging — all competing for the same write lock;
- `timeout=30` seconds; if the lock can't be acquired it directly raises `database is locked`.

**This one degrades with "concurrent write load" rather than "data volume".** Raising analysis concurrency or speeding up scraping could trigger it right now.

### 3. Query Performance — Degrades with Data Volume

- **Filter by tag**: `inspiration_tags` only has a composite primary key index `(inspiration_id, tag_id)` and lacks a standalone `tag_id` index → "find all inspirations containing a tag" does a full index scan, and gets noticeably slower once the link table reaches ~500K rows (currently 14K);
- **Pagination** `ORDER BY created_at DESC LIMIT/OFFSET`: deep pagination slows down past 100K+ rows;
- **`LIKE '%keyword%'`**: full table scan, slows down past 100K rows, and FTS5 full-text indexing is not enabled.

## 4. Conclusion

| Stage | Inspirations | Status |
| ------ | -------- | ------ |
| Now | ~1K | ✅ No pressure at all |
| Long-term personal use | 10K ~ 100K | ✅ Comfortable, but enabling WAL first is recommended |
| Critical zone | 500K ~ 1M | ⚠️ Query/concurrency issues become noticeable; needs indexes + WAL |
| Switch to PostgreSQL | 1M+ or multi-user/high-concurrency writes | ❌ Migrate |

**What bites first is not "inspiration count" but "concurrent writes".**

## 5. Recommendations (by Priority)

1. **Immediately (zero cost)**: switch to WAL mode so reads don't block writes and improve write concurrency.
   Add one line in the connect event of `backend/app/database.py`:

   ```python
   cursor.execute("PRAGMA journal_mode=WAL")
   ```

2. **Before inspirations reach the hundreds of thousands**: add a standalone index on `inspiration_tags.tag_id` to optimize "filter by tag" queries.
3. **Once inspirations reach 1M or multi-user/high-concurrency writes appear**: migrate to PostgreSQL.
