# Architecture Decision Records (ADRs)

This document records the architectural decisions made during the design
and hardening of the Editorial Intelligence V2 system. Each ADR explains
WHY a decision was made, not just WHAT was decided.

---

## ADR-001: Three-Artifact Pipeline (Profile → Intent → Brief)

**Status**: Accepted
**Date**: 2025-01-30

### Context

The V1 system had a single monolithic configuration object that drove
collection. As the system evolved to be channel-aware, this object would
have grown to contain identity, dynamic context, and execution details —
violating single responsibility.

### Decision

Split the editorial pipeline into three distinct artifacts:

1. **Editorial Profile** (persisted, user-owned): Channel identity and
   configuration. Changes rarely (when user edits settings or applies a
   preset). Contains Configuration, Learning, Statistics, and Caches groups.

2. **Editorial Intent** (per-cycle, ephemeral): What the channel needs to
   produce right now. Computed from Profile + dynamic context (gameplay
   inventory, recent videos, queue state). Discarded after each cycle.

3. **Editorial Brief** (per-cycle, ephemeral): How to find the content.
   Built from Profile + Intent. Contains feeds, expanded search queries,
   active templates, scoring weights. Drives the GoalOrientedCollector.

### Rationale

The three concerns change at fundamentally different rates:

- Identity changes rarely (user-driven, months)
- Needs change every cycle (context-driven, hours/days)
- Execution is immediate (consumed and discarded)

Combining them would create a god object with unclear ownership. Splitting
them allows each to be tested independently, cached at different levels,
and evolved without affecting the others.

### Consequences

- Three dataclasses instead of one (slightly more code)
- Clearer test boundaries (each artifact has its own test suite)
- Profile is the only persisted state; Intent and Brief are recomputed
- Future dynamic template generation can replace the Brief without
  touching the Profile or Intent

---

## ADR-002: Multiplicative Composite Score

**Status**: Accepted
**Date**: 2025-01-30

### Context

The V1 system ranked KnowledgeItems by a single `editorial_score`
(0-100) computed by an LLM. This score is intrinsic to the content — it
does not consider whether a specific channel can produce it well or
whether now is the right time.

### Decision

Replace the single score with a 3-layer multiplicative composite score:

```
Final = Editorial Quality × Production Fit × Editorial Timing
```

- **Layer 1 (Quality)**: The existing `editorial_score` (0-100),
  normalized to 0.0-1.0. Intrinsic, global, LLM-scored. Does not change
  per channel.

- **Layer 2 (Fit)**: How well this KI can be produced as a video for
  THIS channel. Components: gameplay_availability (0.40),
  content_type_affinity (0.25), channel_affinity (0.20),
  source_authority (0.15). All computed without LLM.

- **Layer 3 (Timing)**: Is NOW the right time? Components: freshness
  (decay by item_type) × diversity_penalty (cooldown). All computed
  without LLM.

### Rationale

Multiplicative scoring ensures all three dimensions must be present. An
additive score would allow a high-quality KI about a game with no gameplay
to rank high — but the channel cannot produce it. Multiplication prevents
this: if any layer is 0, the final score is 0.

The weights in Layer 2 (0.40/0.25/0.20/0.15) prioritize gameplay
availability because without gameplay, a video cannot be produced. Content
type affinity is second because it reflects the channel's editorial
direction. Channel affinity (embeddings) is third as a tie-breaker. Source
authority is last as a quality signal.

### Consequences

- For new channels (no gameplay), all KIs get fit ≈ 0.15. Relative
  ordering is preserved (quality × timing dominates), but absolute scores
  are low. This is acceptable — the reconciler picks the best available.
- The multiplicative nature means the system naturally avoids content it
  cannot produce well, without explicit exclusion rules.
- Layer 1 remains pristine (LLM-scored, global). Layers 2 and 3 are
  per-channel and recomputed each cycle.

---

## ADR-003: Feedback Scope is Per-User, Not Global

**Status**: Accepted
**Date**: 2025-01-30

### Context

The initial feedback propagator fetched ALL fresh KnowledgeItems and
adjusted their `editorial_score` based on a user's rejection or manual
addition. This caused cross-user contamination: User A's rejection of a
Minecraft KI penalized the score of Minecraft KIs that User B sees.

### Decision

Feedback propagation is scoped to the user's own KIs only
(`user_id = user_id`). Public/shared KIs (user_id = NULL) are never
mutated by individual user feedback.

### Rationale

`editorial_score` is Layer 1 of the composite score — it represents
intrinsic, global quality. Per-channel feedback is a Layer 2/3 concern.
Mixing them is a category error: one user's editorial preference should
not affect another user's quality assessment.

### Consequences

- Users with only public/shared KIs see no feedback propagation (their
  KIs are in the shared pool). This is acceptable — they can still
  reject/accept KIs in their queue, which affects their own queue state.
- Per-user KIs (collected via search queries) receive feedback propagation.
- The `feedback_adjustment` field tracks the cumulative per-KI adjustment,
  which is per-user (since the KI itself is per-user).

---

## ADR-004: Bounded Learning with Decay

**Status**: Accepted
**Date**: 2025-01-30

### Context

Without bounds, learned preferences grow forever (set union, no removal).
After months of operation, `avoided_topics` could contain hundreds of
entries, filtering out everything. `preferred_games` could contain dozens,
diluting the signal. The system would become progressively more
restrictive and less able to explore.

### Decision

All learning mechanisms have caps and decay:

1. **Learned preferences**: FIFO-capped (preferred_games: 20,
   avoided_topics: 50, preferred_styles: 10). When cap is exceeded,
   oldest entries are evicted.

2. **Feedback adjustments**: Capped at ±20 points per KI. Multiple
   rejections of similar KIs have the same effect as one rejection.

3. **Feedback decay**: `feedback_adjustment` decays by 5% per cycle
   (0.95 factor). After ~14 cycles, half the adjustment is gone.

4. **Learned preference decay**: `decay_learned_preferences()` trims
   each list by 1 entry if it has more than 5 items. Called periodically.

5. **Editorial signals cleanup**: Signals older than 90 days are deleted.

### Rationale

The system must be able to re-explore topics that were previously
penalized. Without decay, a topic that a user rejected once would be
permanently excluded. With decay, the system gradually forgets old
preferences and can rediscover them.

The caps prevent signal dilution: 20 preferred games is enough to express
strong preferences without becoming a meaningless list.

### Consequences

- The system naturally cycles through topics over time.
- No preference is permanent — the system can always adapt.
- The decay rate (5% per cycle) is slow enough to be meaningful but fast
  enough to allow adaptation within weeks.

---

## ADR-005: Gameplay as Primary Driver with Priority Floor

**Status**: Accepted
**Date**: 2025-01-30

### Context

The gameplay driver prioritizes games with more ready clips. Without a
floor, a game with 100 clips would get priority 1.0 while a game with 5
clips would get priority 0.05. The system would collect almost exclusively
about the heavily-clipped game for weeks before cooldowns balance things.

### Decision

Add a minimum priority floor of 0.15. This ensures lightly-clipped games
still receive some collection attention.

### Rationale

The floor is low enough that heavily-clipped games still dominate, but
high enough that lightly-clipped games are not invisible. Combined with
the exploration factor (10% of queue slots reserved for random KIs),
this ensures the system continuously discovers content about all games
with gameplay, not just the most-clipped one.

### Consequences

- Collection is more balanced across games with gameplay.
- The cooldown mechanism still prevents over-production of any single game.
- The floor does not apply when `gameplay_driven_collection` is False
  (all games get equal 0.5 priority in that mode).

---

## ADR-006: Search Templates as Immutable First-Class Components

**Status**: Accepted
**Date**: 2025-01-30

### Context

Search strategies (find curiosities, find news, find lore) were previously
implicit — hardcoded in the collector. This made them impossible to
customize per channel or to evolve independently.

### Decision

Search Templates are first-class immutable dataclasses registered in a
module-level dict. Each template bundles: name, item_type, keywords,
description, and decay_category. Channel customization is via
`editorial_keywords` which are MERGED with template keywords at query
expansion time (never replace).

### Rationale

Templates represent editorial strategies that are stable across all
channels ("find curiosities" means the same thing everywhere). Per-channel
customization is additive (more keywords), not subtractive (removing
template keywords). This preserves the curated baseline while allowing
channels to extend it.

### Consequences

- The architecture supports future dynamic template generation: a function
  could replace the module-level dict with dynamically-generated templates.
  No consumer would need to change (they all use `SEARCH_TEMPLATES.get()`).
- Adding a new template is a one-line change (register in the dict).
- Removing a template is safe (consumers check for existence).

---

## ADR-007: Editorial Profile Organized into Four Conceptual Groups

**Status**: Accepted
**Date**: 2025-01-30

### Context

The Editorial Profile accumulated fields from multiple phases (V1
free-text, V2 structured, V2 learned, V2 statistics). Without clear
grouping, it was becoming a god object with unclear ownership semantics.

### Decision

Organize the Profile into four conceptual groups with distinct lifecycles:

1. **Configuration** (user-defined, permanent): niche, tone, affinity,
   feeds, keywords, gameplay_driven, diversity_strictness. The system
   NEVER modifies these automatically.

2. **Learning** (system-acquired, adaptive with decay): learned_preferences.
   Populated by feedback loop, capped, decays over time.

3. **Statistics** (aggregated, continuously recomputed):
   production_history_summary. Derived from source data, not authoritative.

4. **Caches** (ephemeral, can be dropped): metadata_json. Not critical.

### Rationale

The groups have fundamentally different ownership and lifecycle semantics.
Mixing them makes it unclear who writes what and when. The separation
makes the contract explicit: Configuration is user-owned, Learning is
system-owned-but-bounded, Statistics are derived, Caches are disposable.

### Consequences

- The model docstring includes a table documenting the four groups.
- Future fields can be placed in the appropriate group by checking the
  ownership and lifecycle semantics.
- The `apply_preset` function only touches Configuration fields, leaving
  Learning and Statistics untouched.
