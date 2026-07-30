# Editorial Pipeline — Creative Planning + Script Critic

The Editorial Pipeline adds two new stages to video generation that make
editorial decisions explicit and observable:

1. **Editorial Planning** — produces a `VideoCreativePlan` before script
   generation (video type, central idea, narrative arc, tone, humor plan,
   model recommendation)
2. **Script Review** — a `ScriptCritic` evaluates the script and may trigger
   revisions (up to 3 attempts)

## Pipeline (Updated)

```
content_planning
    ↓
editorial_planning (NEW)      ← VideoCreativePlan
    ↓
creative_engine (optional)    ← respects HumorPlan
    ↓
script                        ← uses plan + model
    ↓
script_review (NEW)           ← ScriptCritic → may revise
    ↓
tts
    ↓
gameplay_selection            ← GameplayRetriever (semantic)
    ↓
music_selection
    ↓
render_plan → render → qa → output
```

## Editorial Planner

The `EditorialPlanner` (`src/gpcg/application/editorial_planner.py`) analyzes
the topic, available gameplay, and produces a `VideoCreativePlan`.

### What the plan decides

| Field | Description |
|-------|-------------|
| `video_type` | `GAME_RELATED` or `GENERAL_TOPIC` |
| `central_idea` | The thesis of the video (not a list of facts) |
| `narrative_beats` | hook → context → development → escalation → payoff → conclusion |
| `tone` | Weighted dimensions: informative, casual, sarcastic, comedic, dramatic, etc. |
| `humor` | `HumorPlan`: enabled, intensity, styles, frequency |
| `gameplay_strategy` | `related`, `background_filler`, or `thematic_match` |
| `gameplay_query` | Semantic query for gameplay retrieval (e.g. "character being chased") |
| `model` | `gemma3:12b` (serious) or `qwen3:14b` (personality) with reasoning |

### Humor Plan

The core principle: **"Ser engraçado" não significa "fazer uma piada a cada 20 segundos"**.

```python
@dataclass
class HumorPlan:
    enabled: bool = False
    intensity: str = "none"  # none, low, medium-low, medium, high
    styles: list[str] = []   # observation, sarcasm, wording, etc.
    frequency: str = "sparse"  # sparse, occasional, frequent
```

- `enabled=false` → NO jokes. Zero. Informative and natural only.
- `intensity=low` → occasional natural observations. NOT jokes.
  A low-intensity observation is "isso é meio irônico" said naturally,
  not "prepare-se para rir!".
- `intensity=medium-low` → a few well-placed comments, still mostly informative.

### Bad Humor Patterns (NEVER used)

The planner and script prompts explicitly forbid these AI humor patterns:

- "Já imaginou se..." / "Imagine um jogo onde..."
- "Isso é mais X do que Y" / "É como se X encontrasse Y"
- "O jogo basicamente disse: agora é guerra!"
- "E aí você percebe que..."
- "Prepare-se para..." / "Você não vai acreditar..."
- Consecutive rhetorical questions
- Forced punchlines

### Good Humor comes from

- **observation**: noticing something genuinely curious
- **sarcasm**: saying something seriously when context makes it funny
- **wording**: a normal sentence made funny by construction
- **understatement**: treating something absurd as completely normal
- **dry_commentary**: a short observation beats an elaborate punchline
- **contextual**: humor that depends on what was just said/shown

### Model Selection

- `gemma3:12b` — for serious, informative, documental, neutral tone videos
- `qwen3:14b` — when there's space for personality, commentary, sarcasm

**Qwen3 does NOT mean "make jokes"** — it means "more creative capacity for
natural language". `qwen3` with `humor.intensity=low` = use creativity for
observations, NOT comedy.

## Script Critic

The `ScriptCritic` (`src/gpcg/application/script_critic.py`) evaluates scripts
across 5 dimensions:

| Dimension | What it checks |
|-----------|----------------|
| `structure` | Clear beginning, development, conclusion, central idea |
| `naturalness` | Sounds like speech, no AI-isms, no over-explanation |
| `humor` | Jokes work, not forced, remove bad humor (don't replace) |
| `coherence` | Consistent tone, doesn't abandon central idea |
| `gameplay` | Narration matches visuals (when applicable) |

### Verdict

- **PASS** — overall score >= 70, no high-severity issues
- **REVISE** — score < 70, or high-severity issue, or structure/naturalness problems

### Revision Rule (CRITICAL)

When the critic flags bad humor, the instruction is **"REMOVE this passage"**,
NOT "replace with another joke". **Silence > bad joke.**

The revision prompt explicitly says:
> "For humor issues: if the critic flagged a joke as forced or bad, REMOVE it.
> Do NOT try to write a 'better' joke. Just say the thing normally."

### Revision Loop

```
script (draft)
    ↓
ScriptCritic.review() → ScriptReview
    ↓
verdict == REVISE && revisions < max?
    ↓ yes
ScriptService.generate_script(critic_feedback=..., previous_script=...)
    ↓
ScriptCritic.review() → ScriptReview
    ↓
... (up to GPCG_SCRIPT_CRITIC_MAX_REVISIONS = 3)
    ↓
final script
```

## Observability

All editorial decisions are persisted to `job.artifacts`:

```json
{
  "creative_plan": { /* VideoCreativePlan */ },
  "creative_material": { /* CreativeEngine output */ },
  "script_reviews": [ /* list of ScriptReview */ ],
  "script_review_count": 2,
  "script_review_final_verdict": "PASS"
}
```

## Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `GPCG_EDITORIAL_PLANNING_ENABLED` | true | Master switch for planner |
| `GPCG_EDITORIAL_TEMPERATURE` | 0.4 | Planner LLM temperature |
| `GPCG_EDITORIAL_MAX_TOKENS` | 2048 | Planner response max tokens |
| `GPCG_EDITORIAL_GEMMA_MODEL` | gemma3:12b | Gemma model tag |
| `GPCG_EDITORIAL_QWEN_MODEL` | qwen3:14b | Qwen model tag |
| `GPCG_SCRIPT_CRITIC_ENABLED` | true | Master switch for critic |
| `GPCG_SCRIPT_CRITIC_MAX_REVISIONS` | 3 | Max revision attempts |
| `GPCG_SCRIPT_CRITIC_MODEL` | (empty) | Critic LLM model (empty = default) |
| `GPCG_SCRIPT_CRITIC_TEMPERATURE` | 0.3 | Critic LLM temperature |

## Testing

```bash
# Run editorial pipeline tests (19 tests, uses FakeLLMClient — no Ollama needed)
.venv/bin/pytest tests/test_editorial_pipeline.py -q
```

Tests 1-6 cover:
1. Serious video (no humor, gemma3 model)
2. Informal video (low humor, qwen3 model)
3. General topic video (GENERAL_TOPIC type)
4. Game-related video (GAME_RELATED type)
5. No humor → creative engine skipped
6. Script revision (critic flags bad humor, script revised)
