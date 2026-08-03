# REFACTORY_V2 — Homologação End-to-End

**Data**: 2026-08-03
**Status**: ✅ Concluído
**Commits**: 4 (Fase 1 → Fase 4)
**Testes**: 418 passing (372 originais + 46 novos)

## Resumo Executivo

O REFACTORY_V2 abordou 3 problemas sistêmicos identificados no diagnóstico:

1. **Feature flags V2 desativadas** — todas as flags editoriais (curiosity scoring, story finder, humanization, creative engine, script critic V2) estavam `False` por padrão, fazendo o pipeline rodar em modo legacy.
2. **Queries sem user_id** — Facts, KnowledgeItems, Documents e ContentPlans eram consultados sem filtro de usuário, permitindo vazamento cross-user.
3. **Config reconstruída tardiamente** — defaults globais eram usados em vez de artifacts do job, e `create_job_from_decision` tinha defaults hardcoded (`0`, `""`).

## Mudanças por Fase

### Fase 1 — Integridade Multiusuário ✅

**Commit**: `8ebdb4d`

| Mudança | Arquivo | Impacto |
|---------|---------|---------|
| `is_public` em Fact, Document, KnowledgeItem | `models.py` | Pool híbrido (sistema + privado + público) |
| `visible_to_user()` helper | `domain/visibility.py` (novo) | Filtro consistente em todas as queries |
| Filtro de visibilidade em ContentPlanningService | `content_planning_service.py` | Facts/KIs filtrados por usuário |
| Filtro em worker_routes (GET /jobs/{id}/data) | `worker_routes.py` | Worker não recebe dados de outros usuários |
| Filtro em ScriptService._collect_sources | `script_service.py` | Originality check usa apenas fontes visíveis |
| Filtro em API endpoints (/facts, /documents, /content-plans) | `routes.py` | UI mostra apenas conteúdo visível |
| ContentPlan sempre recebe user_id | `content_planning_service.py` | Plans são owner-scoped |
| Fact herda user_id + is_public do document | `fact_service.py` | Provenance automática |
| Voices isoladas por usuário | `routes.py` | `data/voices/{user_id}/` |
| YouTube adapter: user_id obrigatório | `google_integration_adapter.py` | Sem fallback global |
| `create_job_from_decision` usa settings defaults | `automation_routes.py` | Sem defaults hardcoded |
| `gpcg_transition_type` + `gpcg_transition_duration` em config | `config.py` | Defaults explícitos |
| RenderPlanBuilder warning se subtitle_config=None | `render_plan_builder.py` | Visibilidade de artifacts faltantes |
| Removido `gpcg_humanization_enabled` duplicado | `config.py` | Defaults conflitantes resolvidos |

**Testes**: 11 novos (cross-user isolation)

### Fase 2 — Lifecycle de Gameplay ✅

**Commit**: `9bf12c0`

| Mudança | Arquivo | Impacto |
|---------|---------|---------|
| `consumer_user_id` em GameplayClipUsage | `models.py` | Per-consumer usage history |
| `get_used_ranges()` filtra por consumer_user_id | `clip_usage_service.py` | A usando segmento público não bloqueia B |
| `record_clip_usage()` aceita consumer_user_id | `clip_usage_service.py` | Registro com consumer |
| GameplaySelector passa job.user_id como consumer | `gameplay_selector.py` | Filtro automático |
| worker_routes inclui consumer_user_id no payload | `worker_routes.py` | Worker recebe consumer |
| local_db_sync popula consumer_user_id | `local_db_sync.py` | DB local consistente |
| delete_video: pending→auto-release, published→keep | `routes.py` | Lifecycle correto |
| `fallback_policy = "stop" \| "allow_public"` | `generation_service.py` | Policy configurável |
| `gpcg_gameplay_cooldown_sec` (default 30s) | `config.py` | Cooldown temporal |
| Correção de falso positivo no diagnóstico | `REFACTORY_V2_DIAGNOSTIC.md` | local_db_sync.py:254 estava correto |

**Testes**: 9 novos (gameplay lifecycle)

### Fase 3 — Conteúdo/Fontes ✅

**Commit**: `6674dea`

| Mudança | Arquivo | Impacto |
|---------|---------|---------|
| `gpcg_content_intelligence_enabled=True` | `config.py` | KIs entram no pipeline |
| `gpcg_curiosity_scoring_enabled=True` | `config.py` | Facts ranqueadas por curiosity_score |
| Gate de qualidade factual (clickbait/promoção/rumor) | `knowledge_item_service.py` | Auto-rejeita conteúdo de baixa qualidade |
| `_detect_quality_issues()` (regex determinístico) | `knowledge_item_service.py` | Pre-check antes do LLM (sem chamada de API) |
| `rejection_reason` em KnowledgeItem | `models.py` + `database.py` | Auditoria de rejeições |
| LLM scoring prompt com gate factual | `knowledge_item_service.py` | LLM também penaliza clickbait/rumor |
| Provenance em ContentPlan.metadata_json | `content_planning_service.py` | Rastreabilidade ideia→fonte→fatos→roteiro |
| `KnowledgeItemOut` schema inclui rejection_reason | `knowledge_item_routes.py` | UI mostra motivo de rejeição |

**Testes**: 14 novos (content/sources)

### Fase 4 — Editorial ✅

**Commit**: `8542c32`

| Mudança | Arquivo | Impacto |
|---------|---------|---------|
| `gpcg_story_finder_enabled=True` | `config.py` | Análise de ângulo narrativo antes do script |
| `gpcg_humanization_enabled=True` | `config.py` | Quebra de padrões AI, oralidade |
| `gpcg_creative_engine_enabled=True` | `config.py` | Geração de hooks/angles/punchlines |
| `gpcg_creative_engine_beat_oriented=True` | `config.py` | Material orientado por beats narrativos |
| `gpcg_script_critic_v2_enabled=True` | `config.py` | Dimensões V2 (hook_strength, retention, pacing, payoff) |
| `gpcg_script_critic_section_based=True` | `config.py` | Review por seção (hook, development, payoff) |
| Gate editorial: REVISE após max_revisions → FAIL | `generation_service.py` | Não publica script ruim |
| `gpcg_target_duration_tolerance` (default 0.3) | `config.py` | Diagnóstico de duração |
| `gpcg_script_min_chars` (default 200) | `config.py` | Diagnóstico de tamanho |
| Warning logs antes de TTS | `generation_service.py` | Diagnóstico, não gate |

**Testes**: 12 novos (editorial)

## Evidência de Homologação

### Testes Automatizados

```
============================= 418 passed in 46.35s =============================
```

- 372 testes originais: **todos passam** (sem regressões)
- 46 testes novos REFACTORY_V2: **todos passam**
  - 11 cross-user isolation
  - 9 gameplay lifecycle
  - 14 content/sources
  - 12 editorial

### Smoke Test de Importação

```
=== Feature Flags ===
content_intelligence: True
curiosity_scoring: True
story_finder: True
humanization: True
creative_engine: True
creative_engine_beat_oriented: True
script_critic_v2: True
script_critic_section_based: True

=== New Config ===
transition_type: fade
transition_duration: 0.5
gameplay_cooldown_sec: 30.0
target_duration_tolerance: 0.3
script_min_chars: 200

GameplayClipUsage.consumer_user_id exists: True
KnowledgeItem.rejection_reason exists: True
KnowledgeItem.is_public exists: True

=== All imports successful ===
```

### Frontend Typecheck

```
> gpcg-web@0.1.0 typecheck
> tsc --noEmit
(exit code 0)
```

## Invariantes Verificadas

1. **Isolamento cross-user**: usuário A nunca vê Facts/KIs/Documents privados de B
2. **Per-consumer gameplay**: A usando segmento público não bloqueia B
3. **Editorial gate**: script com REVISE após max_revisions falha o job
4. **YouTube safety**: user_id obrigatório, sem fallback global
5. **Voice isolation**: cada usuário tem seu próprio diretório de vozes
6. **Provenance**: todo ContentPlan rastreia a fonte da ideia
7. **Quality gate**: clickbait/promoção/rumor auto-rejeitados antes do LLM
8. **Config propagation**: jobs usam artifacts, não defaults globais

## Migrações de Schema

Todas as migrações são via `_ensure_column()` (additive, não-destructive):

- `facts.is_public` (BOOLEAN DEFAULT 0)
- `documents.is_public` (BOOLEAN DEFAULT 0)
- `knowledge_items.is_public` (BOOLEAN DEFAULT 0)
- `knowledge_items.rejection_reason` (VARCHAR(500))
- `gameplay_clip_usage.consumer_user_id` (INTEGER)

## Decisões do Usuário Implementadas

1. **Facts/KnowledgeItems**: modelo híbrido (sistema compartilhado + privado + público) ✅
2. **Fallback policy**: configurável por usuário (`stop` vs `allow_public`) ✅
3. **Deleção de vídeo publicado**: clips permancem "used" ✅
4. **Deleção de vídeo pendente**: clips auto-liberados ✅
