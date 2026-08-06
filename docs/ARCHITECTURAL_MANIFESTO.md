# GPCG Architectural Manifesto

## The Core Idea

**The GPCG does not collect content.**

**The GPCG makes editorial decisions about which content makes sense to
produce for each channel.**

This is the fundamental shift from V1 to V2. The system is no longer a
generic news aggregator that dumps everything into a queue. It is an
editorial intelligence that understands each channel's identity, learns
from feedback, and makes deliberate decisions about what to collect,
what to prioritize, and what to produce.

---

## Editorial Intelligence, Not Content Aggregation

A human editor-in-chief does not publish every piece of news that arrives.
They decide what fits their publication's identity, what their audience
cares about, and what they have the resources to produce. The GPCG does
the same thing, per channel, at scale.

This means:

1. **Collection is goal-oriented, not source-driven.** The system does not
   collect everything from all feeds. It builds an editorial brief that
   specifies what the channel needs, then collects to meet those targets.

2. **Scoring is relative, not absolute.** A KI's quality score is global
   (how good is this content?), but its composite score is per-channel
   (can THIS channel produce it well? is NOW the right time?). The
   reconciler ranks by composite score, not quality alone.

3. **Learning is bounded and decays.** The system learns from feedback
   (rejections, manual additions, production history), but learned
   preferences are capped and decay over time. This prevents the system
   from getting stuck in a self-reinforcing niche.

4. **Diversity is a first-class concern.** Cooldowns, format rotation,
   and exploration factor ensure the system does not produce eternally
   about the same game or the same content type.

---

## Design Principles

### 1. Separation of Concerns

The editorial pipeline is split into three distinct artifacts:

```
Editorial Profile (persisted) → Editorial Intent (per-cycle) → Editorial Brief (per-cycle)
```

Each has a clear responsibility:

- **Profile**: WHO the channel is (identity, configuration, learned preferences)
- **Intent**: WHAT the channel needs to produce now (targets, priorities, cooldowns)
- **Brief**: HOW to find it (feeds, queries, templates, scoring weights)

This separation exists because the three concerns change at different
rates: the Profile changes rarely (user-driven), the Intent changes every
cycle (context-driven), and the Brief is an execution plan that is
consumed immediately and discarded.

### 2. Deterministic Collection, LLM-Only Scoring

The collection pipeline (Profile → Intent → Brief → Collector) is entirely
deterministic. No LLM calls are made during collection. This makes
collection fast, cheap, and predictable.

LLM is used only for scoring (editorial_score) and content generation
(script, narration). This is the expensive, creative part — and it's
where LLM adds the most value.

### 3. Multiplicative Scoring

The composite score is multiplicative, not additive:

```
Final = Editorial Quality × Production Fit × Editorial Timing
```

This means all three dimensions must be present for a KI to rank high.
A high-quality KI about a game with no gameplay will score low. A
low-quality KI about a game with lots of gameplay will also score low.
This prevents the system from producing videos it cannot execute well.

### 4. Bounded Learning

All learning mechanisms have caps and decay:

- Learned preferences: capped at 20/50/10 entries, decay over time
- Feedback adjustments: capped at ±20 points per KI, decay 5% per cycle
- Editorial signals: cleaned up after 90 days

This ensures the system can always re-explore and adapt. No decision is
permanent. No preference is forever.

### 5. Graceful Degradation

The system is designed to degrade gracefully:

- No gameplay → composite score is low but relative ordering is preserved
- Feeds unavailable → collection completes with 0 items (no crash)
- No embeddings → channel affinity is neutral (0.5)
- No LLM → scoring is skipped, KIs get default score

The system never crashes because a dependency is missing. It always
produces the best result it can with what it has.

---

## What the GPCG Is Not

- **Not a content aggregator.** It does not dump everything into a queue.
- **Not a recommendation engine.** It does not recommend content to users.
  It decides what to produce.
- **Not a single-scorer.** It uses a 3-layer composite score that is
  relative to each channel.
- **Not a static system.** It learns, decays, and re-explores.
- **Not a black box.** Every decision is auditable (GameTarget.reason,
  CompositeScore breakdown, EditorialSignal records).

---

## Reference

This manifesto is the authoritative reference for the system's philosophy.
When making architectural decisions, ask:

1. Does this preserve editorial intelligence over content aggregation?
2. Does this maintain separation of concerns?
3. Does this keep learning bounded and decaying?
4. Does this degrade gracefully?
5. Is this decision auditable?

If the answer to any of these is no, the decision should be reconsidered.
