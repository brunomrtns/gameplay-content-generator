# Kids Idea System — Progress

**Implementation progress for the Kids Idea System.**

See `docs/KIDS_IDEA_SYSTEM_PROPOSAL.md` for the full architectural proposal.

---

## Phase 1 — Foundation ✅ COMPLETED

### Implemented

- **KidsIdea model** (`src/gpcg/domains/kids/models.py`)
  - `KidsIdea` ORM entity with content, scoring, safety, lifecycle, dedup, and traceability fields
  - `KidsIdeaStatus` enum: `discovered → evaluated → queued → converted` (with `rejected`, `expired` terminal states)
  - `KidsIdeaSource` enum: `ai_ideation`, `topic_library`, `seasonal`, `manual`, `research`
  - `KidsTopic` extended with `idea_id` FK, `editorial_intent`, `educational_goal` for traceability

- **Database migrations** (`src/gpcg/infrastructure/database.py`)
  - `kids_ideas` table created via `Base.metadata.create_all()`
  - `kids_topics` additive columns: `idea_id`, `editorial_intent`, `educational_goal`

- **Idea service** (`src/gpcg/domains/kids/idea_service.py`)
  - `create_idea()` with content hash + similarity-based deduplication
  - `compute_content_hash()` — SHA256 of normalized title (lowercase, no accents, digits→words, no punctuation)
  - `title_similarity()` — Jaccard word similarity with stop word removal (catches paraphrases)
  - `is_similar_to_existing()` — checks against existing ideas
  - `is_duplicate_topic()` — checks against existing topics (hash + similarity)
  - Lifecycle: `can_transition()`, `is_terminal()`, `update_status()`, `reject_idea()`
  - `convert_to_topic()` — creates KidsTopic from idea, links them, marks converted
  - `expire_old_ideas()` — expires evaluated ideas older than N days
  - `get_stats()` — counts by status

- **Safety filter** (`src/gpcg/domains/kids/safety_filter.py`)
  - Two-layer: hard rules (keyword blocklist) + LLM classification
  - `_BLOCKED_KEYWORDS` — violence, adult themes, scary, dangerous, political/religious
  - `_SENSITIVE_KEYWORDS` — flags but doesn't auto-reject (morte, guerra, medo, etc.)
  - `KidsSafetyFilter.review()` — returns `SafetyResult(safe, safety_score, flags, age_suitability)`
  - Conservative fallback when LLM fails
  - Configurable strictness threshold per channel

- **Scorer** (`src/gpcg/domains/kids/scorer.py`)
  - `KidsScorer.score()` — 6 dimensions: editorial_quality, age_fit, educational_value, curiosity, visual_potential, simplicity
  - `final_score` = weighted geometric mean of core dimensions + capped modifier bonus
  - Extensible: new dimensions can be added without changing the formula
  - Neutral fallback when LLM fails

- **Prompts** (`src/gpcg/domains/kids/prompts.py`)
  - `SAFETY_FILTER_SYSTEM` — LLM prompt for safety review
  - `IDEA_SCORER_SYSTEM` — LLM prompt for editorial scoring
  - `IDEATION_SYSTEM` — LLM prompt for AI ideation (for Phase 2)

- **API routes** (`src/gpcg/api/kids_idea_routes.py`)
  - `GET /api/kids/ideas` — list with filters (status, category)
  - `GET /api/kids/ideas/{id}` — detail
  - `GET /api/kids/ideas/stats` — statistics
  - `POST /api/kids/ideas` — create manual idea (with dedup check)
  - `POST /api/kids/ideas/{id}/reject` — reject
  - `POST /api/kids/ideas/{id}/score` — trigger safety + scoring
  - `POST /api/kids/ideas/{id}/convert` — convert to KidsTopic
  - `GET /api/kids/idea-queue` — get queue
  - `POST /api/kids/idea-queue/add` — add to queue
  - `POST /api/kids/idea-queue/remove` — remove from queue
  - `POST /api/kids/idea-queue/reorder` — reorder queue
  - Domain guard: all endpoints require Kids domain

- **Tests** (`tests/test_kids_idea_system.py`)
  - 68 tests covering: model, lifecycle, deduplication, safety filter, scorer, service, API routes
  - All LLM calls mocked in tests

### Test results

```
860 passed, 4 warnings in 71.00s
```

No regressions in Games or existing Kids tests.

### Architectural decisions

1. **No UniqueConstraint on content_hash** — removed because it prevents `skip_dedup` (needed for bulk import/testing). Dedup is enforced at the application level via `create_idea()`.

2. **Jaccard word similarity instead of embeddings** — lightweight, no infrastructure needed. Catches the user's example ("Por que o polvo tem três corações?" vs "Você sabia que o polvo possui três corações?" → 0.75 similarity). Embeddings can be added later via `EmbeddingService` without changing the interface.

3. **StaticPool for in-memory SQLite tests** — required because FastAPI TestClient runs route handlers in a thread pool, and in-memory SQLite creates a separate database per connection.

4. **Safety filter: hard rules + LLM** — hard rules catch obvious cases deterministically (no prompt injection risk), LLM catches subtle contextual issues. Conservative fallback when LLM fails.

5. **Scorer: weighted geometric mean** — multiplicative core (quality × age_fit × educational × curiosity) ensures all dimensions matter. Additive modifiers (visual_potential, simplicity) provide bonus/penalty without zeroing the score.

---

## Phase 2 — Discovery ✅ COMPLETED

### Implemented

- **Topic Library** (`src/gpcg/domains/kids/topic_library.py`)
  - 14 categories: animals, science, space, dinosaurs, nature, ocean, human_body, history, geography, vehicles, food, colors, numbers, curiosity
  - Each category has display_name, description, and 3-5 seed topics
  - Extensible structure (add categories/seeds without code changes)
  - API: `GET /api/kids/topic-library`

- **Seasonal Calendar** (`src/gpcg/domains/kids/seasonal_calendar.py`)
  - 12 seasonal entries: Dia das Crianças, Natal, Ano Novo, Dia do Planeta Terra, Dia das Mães, Dia dos Pais, Férias de Verão, Volta às Aulas, Páscoa, Dia do Meio Ambiente, Festa Junina, Independência do Brasil
  - `get_active_seasonal()` — returns entries within lead_days + lookahead window
  - Handles year-wrap (entries in January visible from December)
  - API: `GET /api/kids/seasonal-calendar`

- **KidsIdeaDiscovery** (`src/gpcg/domains/kids/discovery.py`)
  - Three sources: AI ideation, topic library seeds, seasonal themes
  - AI ideation: LLM generates ideas per category using `IDEATION_SYSTEM` prompt
  - Topic library: seeds are directly created as ideas (source=topic_library)
  - Seasonal: LLM generates ideas for active seasonal entries
  - Automatic deduplication via `create_idea()` (hash + similarity)
  - Graceful LLM failure handling (returns empty list, continues with other sources)
  - API: `POST /api/kids/ideas/discover`

- **New job types** (`src/gpcg/core/models.py`)
  - `JobType.kids_idea_discovery` — for automated discovery runs
  - `JobType.kids_idea_score` — for batch safety + scoring

- **Prompts** (`src/gpcg/domains/kids/prompts.py`)
  - `IDEATION_SYSTEM` — LLM prompt for creative idea generation with safety rules

- **Tests** — 22 new tests (90 total in test file)
  - Topic Library: 8 tests (categories, seeds, structure)
  - Seasonal Calendar: 5 tests (entries, active lookup, month filter)
  - Discovery: 5 tests (AI ideation, topic library, dedup, LLM failure, result repr)
  - Discovery API: 4 tests (topic library endpoint, seasonal endpoint, discover endpoint)

### Test results

```
882 passed, 4 warnings in 68.35s
```

No regressions.

### Architectural decisions

1. **Topic library is intentionally small** — 14 categories with 3-5 seeds each. The LLM expands on these seeds during ideation. Adding thousands of manual topics would be counterproductive.

2. **Seasonal calendar uses MM-DD format** — recurring yearly dates. No complex year-specific date calculation (e.g. "second Sunday of May") for MVP. Approximate dates are sufficient for ideation.

3. **Discovery is a service, not a job** — the `KidsIdeaDiscovery` class is called by the API endpoint directly. The `kids_idea_discovery` job type exists for future automated scheduling (Phase 4).

4. **Factuality separation** — AI-generated ideas are editorial prompts, NOT verified facts. The script pipeline handles fact validation. This service does NOT validate facts.

## Phase 3 — Queue + Curation ✅ COMPLETED

### Implemented

- **Queue reconciliation** (`src/gpcg/domains/kids/idea_service.py`)
  - `reconcile_kids_queue()` — auto-fills queue with top-scored evaluated ideas
  - Only runs when `kids_queue_mode == "automatic"` and `kids_auto_fill_queue == True`
  - Respects `kids_max_queue_size` (default 10)
  - Picks highest `final_score` ideas first
  - Skips ideas with duplicate topics (prevents re-producing same concept)
  - Transitions ideas from `evaluated → queued` when added to queue

- **Queue cleaning** (`src/gpcg/domains/kids/idea_service.py`)
  - `clean_kids_queue()` — removes invalid entries from queue
  - Removes: rejected, expired, converted, nonexistent, wrong-user ideas
  - Returns count of removed entries

- **API endpoints** (`src/gpcg/api/kids_idea_routes.py`)
  - `GET /api/kids/idea-queue` — now triggers clean + reconcile on every call
  - `POST /api/kids/idea-queue/reconcile` — manual trigger for clean + reconcile
  - Returns `{removed, added, message}`

- **Tests** — 12 new tests (102 total in test file)
  - Reconcile: 6 tests (no automation, manual mode, auto-fill disabled, fill from evaluated, max size, skip non-evaluated)
  - Clean queue: 4 tests (remove rejected, remove converted, remove nonexistent, no changes)
  - API: 2 tests (reconcile endpoint, GET queue triggers clean+reconcile)

### Test results

```
894 passed, 4 warnings in 66.87s
```

No regressions.

### Architectural decisions

1. **Queue stored in `Automation.config["kids_idea_queue"]`** — same pattern as Games idea queue, but with `kids_` prefix to avoid collision. This allows independent queue management per domain.

2. **Reconcile runs on GET queue** — when the user opens the ideas page, the queue is automatically cleaned and filled. This ensures the user always sees a valid, up-to-date queue without manual intervention.

3. **Reconcile only picks evaluated ideas** — discovered ideas must be scored first (safety + editorial) before they can enter the queue. This ensures only safe, scored ideas are queued for production.

## Phase 4 — Domain-aware Automation ✅ COMPLETED

### Implemented

- **Automation strategies** (`src/gpcg/domains/automation_strategies.py`)
  - `GamesAutomationStrategy` — thin wrapper preserving existing Games behavior
  - `KidsAutomationStrategy` — new Kids automation path
  - `get_strategy(domain)` — dispatch by domain
  - `get_user_domain(db, user_id)` — reads domain from ChannelProfile

- **Domain dispatch in check_automation** (`src/gpcg/api/automation_routes.py`)
  - Kids users: `KidsAutomationStrategy.check()` — checks YouTube, StoryAssets, queue
  - Games users: existing behavior (unchanged)
  - Kids check: reconciles queue, returns pending dict with `domain: "kids"`

- **Domain dispatch in create_job_from_automation** (`src/gpcg/api/automation_routes.py`)
  - Kids users: `KidsAutomationStrategy.create_job()` — consumes queue, converts idea to topic, creates job
  - Games users: existing behavior (unchanged)
  - Kids job creation: picks first valid idea from queue, converts to KidsTopic, creates `generate_short` job with `domain=kids`

- **KidsAutomationStrategy.check()** conditions:
  - User is active
  - YouTube connected (`google_user_id`)
  - At least 1 ready StoryAsset
  - No active generation job
  - Queue has ideas (after reconcile)

- **KidsAutomationStrategy.create_job()** flow:
  1. Validate user, automation, YouTube, StoryAssets
  2. Pick first valid idea from `kids_idea_queue`
  3. Convert idea to KidsTopic (if not already converted)
  4. Create `generate_short` job with `domain=kids`, artifacts include `topic_id`, `idea_id`
  5. Remove idea from queue

- **Tests** — 15 new tests (117 total in test file)
  - Strategy dispatch: 5 tests (games, kids, unknown, user domain lookup)
  - Kids check: 4 tests (no YouTube, no assets, no queue, all conditions met)
  - Kids create_job: 4 tests (no automation, no YouTube, no assets, empty queue)
  - Games regression: 2 tests (strategy returns Games, check is no-op marker)

### Test results

```
909 passed, 4 warnings in 65.86s
```

No Games regressions. All existing Games automation, editorial, queue, and KnowledgeItem tests pass unchanged.

### Architectural decisions

1. **Games strategy is a thin wrapper** — the actual Games logic stays in `check_automation()` and `create_job_from_automation()`. The `GamesAutomationStrategy` class exists only for interface symmetry. This ensures zero risk of Games regression.

2. **Domain dispatch at the top** — the domain check happens at the very beginning of `check_automation` and `create_job_from_automation`. Games users never enter the Kids code path, and vice versa.

3. **Kids uses StoryAsset instead of GameplaySource** — the `GameplaySource` check that blocks Kids users in the old code is now bypassed entirely. Kids users only need StoryAssets (images) as visual material.

4. **Kids jobs use `generate_short` type** — the same job type as Games, but with `domain=kids` and `artifacts.topic_id` pointing to a KidsTopic. The generation pipeline can dispatch based on domain.

## Phase 5 — Production Integration ✅ COMPLETED

### Implemented

- **One-step produce endpoint** (`src/gpcg/api/kids_idea_routes.py`)
  - `POST /api/kids/ideas/{id}/produce` — full flow: KidsIdea → KidsTopic → generate_short job
  - Converts idea to topic (if not already converted)
  - Verifies topic has ready StoryAssets
  - Creates generation job with `domain=kids` and full traceability artifacts
  - Allows production from any non-rejected, non-expired status (including `converted`)

- **Provenance/traceability endpoint** (`src/gpcg/api/kids_idea_routes.py`)
  - `GET /api/kids/ideas/{id}/provenance` — full chain: idea → topic → jobs → videos
  - Returns idea details, topic info (asset counts), job list (status, stage, idea_id), video list (YouTube URL)
  - Enables "which idea produced this video?" queries

- **Traceability in existing generate endpoint** (`src/gpcg/api/kids_routes.py`)
  - `POST /kids/generate` now includes `idea_id` in job artifacts when the topic has an `idea_id`
  - This ensures traceability even when using the manual generate endpoint

- **Job artifacts for traceability**
  - All Kids generation jobs now include: `topic_id`, `topic_title`, `idea_id` (when available), `source`
  - Source values: `kids_idea_queue` (automation), `kids_idea_produce` (one-step produce), manual (no idea_id)

- **Tests** — 11 new tests (128 total in test file)
  - End-to-end flow: 4 tests (idea→topic→job, bidirectional link, duplicate prevention, no topic)
  - Produce API: 4 tests (no assets, with assets, already converted, rejected idea)
  - Provenance API: 2 tests (full chain, no topic)
  - Generate traceability: 1 test (idea_id in job artifacts)

### Test results

```
920 passed, 4 warnings in 68.04s
```

No regressions. All Games tests pass unchanged.

### Architectural decisions

1. **Produce endpoint allows `converted` status** — an idea that was already converted to a topic can still be produced (it just creates a new job for the existing topic). Only `rejected` and `expired` statuses block production.

2. **Provenance uses linear scan** — SQLite JSON queries (`Job.artifacts["topic_id"]`) are not always supported, so the provenance endpoint falls back to a linear scan of Kids-domain jobs. This is acceptable for the MVP (low job count per user).

3. **Traceability via job artifacts** — the `idea_id` is stored in `job.artifacts`, not in a separate foreign key. This is consistent with the existing pattern (Games jobs store `topic_id` in artifacts too).

---

## Summary

All 5 phases of the Kids Idea System are complete:

| Phase | Status | Tests Added | Total Tests |
|-------|--------|-------------|-------------|
| 1 — Foundation | ✅ | 68 | 860 |
| 2 — Discovery | ✅ | 22 | 882 |
| 3 — Queue + Curation | ✅ | 12 | 894 |
| 4 — Domain-aware Automation | ✅ | 15 | 909 |
| 5 — Production Integration | ✅ | 11 | 920 |

**Final test count: 920 passed, 0 failed, 4 warnings.**

### Files created

- `src/gpcg/domains/kids/idea_service.py` — idea lifecycle, dedup, conversion, queue
- `src/gpcg/domains/kids/safety_filter.py` — hard rules + LLM safety review
- `src/gpcg/domains/kids/scorer.py` — multi-dimensional editorial scoring
- `src/gpcg/domains/kids/topic_library.py` — 14 categories with seed topics
- `src/gpcg/domains/kids/seasonal_calendar.py` — 12 seasonal entries
- `src/gpcg/domains/kids/discovery.py` — AI ideation + topic library + seasonal
- `src/gpcg/domains/automation_strategies.py` — domain-aware automation dispatch
- `src/gpcg/api/kids_idea_routes.py` — all Kids idea API endpoints
- `tests/test_kids_idea_system.py` — 128 tests covering all phases
- `docs/KIDS_IDEA_SYSTEM_PROGRESS.md` — this file

### Files modified

- `src/gpcg/core/models.py` — new job types (kids_idea_discovery, kids_idea_score)
- `src/gpcg/domains/kids/models.py` — KidsIdea model, KidsTopic traceability fields
- `src/gpcg/domains/kids/prompts.py` — safety, scoring, ideation prompts
- `src/gpcg/infrastructure/database.py` — schema evolution for new columns
- `src/gpcg/api/app.py` — router registration
- `src/gpcg/api/kids_routes.py` — idea_id traceability in generate endpoint
- `src/gpcg/api/automation_routes.py` — domain dispatch in check/create_job
- `tests/test_architecture.py` — updated table count (27)

### Games regression check

All existing Games tests pass unchanged:
- Editorial system tests ✅
- Automation tests ✅
- Queue tests ✅
- KnowledgeItem tests ✅
- Full suite: 920 passed, 0 failed ✅
