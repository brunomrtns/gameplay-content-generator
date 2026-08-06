# Convergence Risk Analysis — Editorial Intelligence V2

**Date**: 2025-01-30
**Status**: Analysis complete, fixes in progress

## Executive Summary

The V2 system has **5 critical convergence risks** that would cause
degraded behavior over months/years of continuous operation. All have
simple fixes. No architectural redesign is needed.

---

## Risk 1: Feedback propagation contaminates other users (CRITICAL)

**Location**: `feedback_propagator.py:_propagate_to_similar`

**Problem**: The method fetches ALL fresh KIs globally and adjusts their
`editorial_score`. User A rejecting a KI about Minecraft penalizes the
editorial_score of Minecraft KIs that User B sees. This is cross-user
contamination of the intrinsic quality layer.

**Root cause**: `editorial_score` is Layer 1 (intrinsic, global, LLM-scored).
Feedback is per-channel. Mutating a global field with per-channel feedback
is a category error.

**Fix**: Only propagate to KIs owned by the user (`user_id = user_id`).
Public/shared KIs are never mutated by individual user feedback.

**Severity**: CRITICAL — corrupts the global quality layer.

---

## Risk 2: Learned preferences grow unbounded (CRITICAL)

**Location**: `editorial_profile_service.py:update_learned_preferences`

**Problem**: `preferred_games`, `avoided_topics`, `preferred_styles` use
set union — they only grow, never shrink. After months:
- `avoided_topics` could contain hundreds of entries → filters out everything
- `preferred_games` could contain dozens → signal dilution
- No mechanism to forget old preferences

**Fix**: 
1. Cap each list (preferred_games: 20, avoided_topics: 50, preferred_styles: 10)
2. When cap is reached, evict oldest entries (FIFO)
3. Add periodic decay that removes entries older than 90 days

**Severity**: CRITICAL — progressive signal degradation.

---

## Risk 3: Editorial signals table grows forever (HIGH)

**Location**: `EditorialSignal` model, `feedback_propagator.py`

**Problem**: Every rejection, manual-add, and production creates a row.
After months of operation with thousands of channels, this table could
have millions of rows. They are never cleaned up.

**Fix**: Add `cleanup_old_signals()` that deletes signals older than 90 days.
Call it in the worker after lifecycle updates.

**Severity**: HIGH — storage growth, query slowdown.

---

## Risk 4: Feedback adjustments compound without limit (HIGH)

**Location**: `feedback_propagator.py:_propagate_to_similar`

**Problem**: Each rejection adjusts `editorial_score` by up to -20 points.
If a user rejects 5 similar KIs, a similar KI could lose up to 100 points
(clamped to 0). This creates a permanent penalty zone that's very hard
to escape — the system "gives up" on that topic forever.

**Fix**: Track cumulative feedback adjustment per KI. Cap at ±20 points
total (not per signal). This means 1 rejection or 10 rejections have the
same effect — the penalty is bounded.

**Severity**: HIGH — creates permanent exclusion zones.

---

## Risk 5: Gameplay driver creates collection monopoly (MEDIUM)

**Location**: `editorial_intent_builder.py:_compute_priority_games`

**Problem**: priority = clips_ratio * (1 - coverage_penalty). If one game
has 100 clips and another has 5, the first gets priority 1.0 and the
second gets 0.05. The system collects almost exclusively about the first
game until coverage builds up enough for the penalty to balance things.

**Fix**: Add a minimum priority floor of 0.15. This ensures lightly-clipped
games still receive some collection attention, preventing monopoly.

**Severity**: MEDIUM — temporary imbalance, self-correcting via cooldowns,
but can last weeks.

---

## Risk 6: Composite score multiplicative collapse (LOW — by design)

**Location**: `composite_scorer.py`

**Problem**: `final = quality * fit * timing`. If any layer is 0, final = 0.
For new channels with no gameplay, fit ≈ 0.15 for all KIs.

**Analysis**: This is by design. The relative ordering is preserved — the
reconciler picks the highest-scoring KIs regardless of absolute score.
The exploration factor provides discovery. No fix needed.

**Severity**: LOW — cosmetic (low absolute scores), not behavioral.

---

## Non-risks (validated as safe)

- **Cooldown mechanism**: Self-correcting (games fall out of recent_videos window)
- **Content type affinity frozen**: By design — user controls direction,
  feedback loop provides adaptation
- **Production history top_games**: Capped at 5 entries
- **Search templates immutable**: Correct — custom keywords provide per-channel
  customization without mutating global templates
