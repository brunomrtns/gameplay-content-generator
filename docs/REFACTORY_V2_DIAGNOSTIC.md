# Diagnóstico Técnico — REFACTORY_V2

> **Status**: Diagnóstico (primeira entrega). Evidência-first: toda afirmação
> aponta para arquivo/linha/classe/campo. Classificação AS-IS segue a
> disciplina de execução do `REFACTORY_V2` (`CONFIRMED_IN_CODE`,
> `CONFIRMED_IN_DB/MODEL`, `DOCUMENTED_ONLY`, etc.).
> **Autor**: Devin (Staff/Principal Engineer mode).
> **Entrada**: leitura direta do código + docs + 4 subagents de mapeamento
> paralelo (config propagation, content/ideas pool, editorial pipeline,
> multi-user isolation, gameplay selection/reuse).

---

## A. Mapa do fluxo real (AS-IS comprovado)

### A.1 Pipeline de geração — estágios e gates

`GenerationService._run_pipeline()` em
<ref_file file="/home/bruno/Desenvolvimento/brunointegrations/gameplay-content-generator/src/gpcg/application/generation_service.py" />

```
content_planning        (sempre)                linha 360
story_finding           (gpcg_story_finder_enabled)         linha 408
editorial_planning      (gpcg_editorial_planning_enabled)   linha 415
creative_engine         (gpcg_creative_engine_enabled)      linha 424
script                  (sempre)                linha 438
humanization            (gpcg_humanization_enabled)         linha 505
script_review           (gpcg_script_critic_enabled)        linha 510
tts                     (sempre)                linha 517
gameplay_selection      (sempre)                linha 545
music_selection         (sempre)                linha 601
render_plan             (sempre)                linha 614
render                  (sempre)                linha 674
qa                      (sempre)                linha 708
metadata_generation     (gpcg_metadata_generation_enabled)  linha 744
youtube_upload          (gpcg_youtube_upload_enabled)       linha 750
```

**Estado real das feature flags** (`config.py`):

| Flag | Default | Status |
|------|---------|--------|
| `gpcg_editorial_planning_enabled` | `True` | ATIVO |
| `gpcg_script_critic_enabled` | `True` | ATIVO |
| `gpcg_creative_engine_enabled` | `False` | **INATIVO** |
| `gpcg_story_finder_enabled` | `False` | **INATIVO** |
| `gpcg_curiosity_scoring_enabled` | `False` | **INATIVO** |
| `gpcg_humanization_enabled` | `False` | **INATIVO** (definido 2x: linhas 135 e 225) |
| `gpcg_creative_engine_beat_oriented` | `False` | **INATIVO** |
| `gpcg_script_critic_v2_enabled` | `False` | **INATIVO** |
| `gpcg_script_critic_section_based` | `False` | **INATIVO** |
| `gpcg_content_intelligence_enabled` | `False` | **INATIVO** |
| `gpcg_game_enrichment_enabled` | `False` | **INATIVO** |
| `gpcg_cross_game_gameplay_enabled` | `False` | **INATIVO** |
| `gpcg_metadata_generation_enabled` | `True` | ATIVO |
| `gpcg_youtube_upload_enabled` | `False` | **INATIVO** (em config; ativo via .env em prod) |

**Conclusão AS-IS**: dos 5 componentes editoriais V2 (CuriosityScorer,
StoryFinder, CreativeEngine beat-oriented, Humanization, ScriptCritic V2),
**todos estão implementados mas inativos por default**. Apenas
EditorialPlanner (V1) e ScriptCritic (V1) estão ativos. O pipeline que
efetivamente roda em homologação é o legado enriquecido apenas com
editorial_planning + script_review V1.

### A.2 Propagação de configuração (UI → render)

Fluxo comprovado (subagent `932d3702`):

```
frontend/src/pages/automation.tsx
  → POST /api/automation (automation_routes.py:98 — a.config = req.config)
  → Automation.config (JSON em models.py:330)
  → POST /api/jobs/generate (routes.py:676-788)
      _pick() em routes.py:728-731 (explícito > automation_config)
      → GenerationService.create_job() (generation_service.py:124-218)
          sub_cfg dict → artifacts["subtitle_config"] (linha 200)
          artifacts["video_format"], ["scene_duration"], ["voice_path"],
            ["transition_type"], ["transition_duration"], ["creative_style"]
      → Job.artifacts (JSON)
  → GET /api/jobs/{id}/data (worker_routes.py:1276-1425)
      _serialize_job() (linha 541-558) — artifacts incluído no payload
  → local_db_sync.py:317-338 — popula Job.artifacts no DB local do worker
  → GenerationService._run_pipeline() lê artifacts via _get_artifact()
      render_plan: generation_service.py:614-672
          SubtitleConfig construído (linhas 644-657) com defaults de config.py
          → RenderPlanBuilder.build() (render_plan_builder.py:74-217)
              se subtitle_config None → usa settings.gpcg_subtitle_* (linhas 104-112)
              → get_profile_dict(fmt, subtitle_config)
              → request_data["_gpcg_custom_profile"] = {...}
          transition_type/duration vão direto para request_data (linhas 686-693)
  → VideoGenerateAdapter.render_video() (video_generate_adapter.py:299-403)
      custom_profile = request_data.pop("_gpcg_custom_profile")
      injeta código de registro no subprocess (linhas 326-362)
  → subprocess video-generate (python do venv do VG)
      VideoProfileRegistry.register() + process_video_request()
```

### A.3 Seleção de conteúdo (de onde vêm as ideias)

`ContentPlanningService` em
<ref_file file="/home/bruno/Desenvolvimento/brunointegrations/gameplay-content-generator/src/gpcg/application/content_planning_service.py" />

- **Facts**: query em `facts` table por `game_id` (linhas 91-107).
  - Curiosity scoring OFF → ordena por `(quality_score * novelty_score).desc()`.
  - Curiosity scoring ON → ordena por `(curiosity_score * 0.5 + quality_score * 0.3 + novelty_score * 0.2).desc()`.
- **KnowledgeItems**: só consultados quando `gpcg_content_intelligence_enabled=True` (linha 111-112). **Default False**.
- **Unificação**: `_build_unified_candidates(facts, knowledge_items)` (linha 123).
- **Fallback**: se LLM não selecionar, usa o primeiro fact da lista (linha 179-181).

**Pool real consumido**: apenas Facts (extraídos de documentos do usuário).
KnowledgeItems (RSS/Wikipedia) **nunca entram** porque a flag está off.
Mesmo se ligada, itens são criados com `editorial_score=0.0`
(`content_collectors.py:272`) e o filtro exige `>= gpcg_content_min_editorial_score`
(default 50) — **não há processo automático de scoring editorial pós-coleta**.

### A.4 Seleção de gameplay e controle de reutilização

`GameplaySelector` em
<ref_file file="/home/bruno/Desenvolvimento/brunointegrations/gameplay-content-generator/src/gpcg/application/gameplay_selector.py" />

- Recebe `user_id` e `accept_public` (linha 88-89).
- Filtra `GameplaySource.user_id == user_id` (linha 169-171).
- Fallback público: `is_public == True AND user_id != user_id` (linhas 163-168).
- `used_ranges_cache` carregado via `get_used_ranges(session, source_id)` (linha 190).
- `find_available_segment()` em `clip_usage_service.py:74-140` encontra segmento livre.
- Tolerância de overlap: **1.0s** (`is_range_available`, `clip_usage_service.py:50`).
- **Sem cooldown temporal**. **Sem reserva atômica** — seleção é in-memory.
- `GameplayClipUsage` modelo existe (`models.py:571-592`): `video_id`, `source_id`, `start_sec`, `end_sec`, `duration`. **Sem `user_id`** (consumer) — vínculo indireto via `Video.user_id`.
- Registro de uso: **após QA pass** (`generation_service.py:732-739`), não no momento da seleção.
- Sync VPS→worker: `GET /jobs/{id}/data` inclui `clip_usages` por source (`worker_routes.py:1407-1413`); `local_db_sync.py:249-258` popula no DB local.

### A.5 Publicação YouTube

`POST /api/videos/{id}/publish` em `routes.py:1004-1086`.
- Valida ownership: `if v.user_id != user.id: raise 403` (linha 1023).
- `GoogleIntegrationAdapter.upload_to_youtube(user_id=user.id)` (linha 1067).
- Adapter (`google_integration_adapter.py:59-99`): `uid = user_id or self.settings.gpcg_youtube_user_id` (linha 86) — **default global se user_id None**.

### A.6 Deleção de vídeo

`DELETE /api/videos/{id}` em `routes.py:1089-1158`.
- Valida ownership (linha 1111).
- `release_clips: bool = False` (linha 1092) — **default NÃO libera**.
- `release_clip_usage(db, video_id)` só chamado se `release_clips=True` (linha 1117).
- **Não diferencia vídeo pendente de publicado**.

### A.7 Visibilidade de gameplay

`PATCH /api/gameplays/{id}/visibility` em `routes.py:376-401`.
- Toggle direto via query param `is_public`.
- Valida ownership (linha 391-392).
- **Sem aceite explícito** (modal/termo).

---

## B. Causas raiz (por problema observado)

### B.1 Configurações do usuário não são respeitadas

**Causa raiz 1 — Reconstrução tardia de configuração no worker.**
`RenderPlanBuilder.build()` (`render_plan_builder.py:104-112`): se
`subtitle_config` for `None`, reconstrói a partir de `self.settings.gpcg_subtitle_*`
(defaults globais do config.py), **ignorando a configuração do usuário**.
O fluxo correto depende de `GenerationService` sempre construir o
`SubtitleConfig` a partir de `job.artifacts["subtitle_config"]`
(`generation_service.py:621-657`). Se o artifact estiver ausente (job criado
por caminho alternativo, sync incompleto, campo nullable), o default global
substitui silenciosamente a config do usuário.

**Causa raiz 2 — Caminho alternativo `create_job_from_decision` com defaults hardcoded.**
`automation_routes.py:411-429` cria jobs a partir de decisões de automação
usando `config.get("scene_duration", 0)` e `config.get("video_format", "")`
com defaults hardcoded (`0`, `""`) em vez de defaults de `config.py`. Isso
produz artifacts diferentes do caminho principal `POST /api/jobs/generate`.

**Causa raiz 3 — Booleanos tratados como None vs False.**
`routes.py:750-753`: `if subtitle_box_enabled is None: subtitle_box_enabled = auto_cfg.get(...)`.
Se o usuário enviar `False` explícito e a automation config tiver `True`, o
`False` do usuário é respeitado. MAS se o usuário não enviar (None) e a
automation tiver `True`, herda `True` — o que pode não ser a intenção se o
usuário desligou no UI mas o payload não incluiu o campo.

**Causa raiz 4 — `transition_type` e `transition_duration` sem defaults em config.py.**
Esses campos não têm `gpcg_transition_*` em `config.py`. Se não estiverem em
artifacts, o video-generate aplica defaults internos dele, não do GPCG.

**Causa raiz 5 — Snapshot vs config atual não definido.**
O job usa `Job.artifacts` (snapshot no momento de criação). Se o usuário
mudar a automation config depois, jobs já criados mantêm o snapshot. **Isso
é correto para previsibilidade**, mas não está documentado/testado. O risco
é se algum estágio ler `Automation.config` diretamente em vez de
`job.artifacts` — não encontrei isso, mas é um invariant a garantir.

### B.2 Mistura entre usuários (cross-user)

**Causa raiz 6 — Queries de Facts/KnowledgeItems/Documents/ContentPlans sem `user_id`.**
`CONFIRMED_IN_CODE` em múltiplos pontos:

- `ContentPlanningService` (`content_planning_service.py:91-107, 240-249, 357-363`): queries por `game_id` apenas.
- `GET /api/jobs/{id}/data` (`worker_routes.py:1336, 1349`): Facts e KnowledgeItems por `game_id` apenas.
- `ScriptService._get_source_texts()` (`script_service.py:725-742`): Documents e Facts por `game_id` apenas.
- `FactService` dedup/scoring (`fact_service.py:109-180`): por `game_id` apenas.
- `CuriosityScorer` (`curiosity_scorer.py:208-214`): por `game_id` apenas.
- Endpoints `GET /api/facts`, `/api/documents`, `/api/content-plans` (`routes.py:514, 562, 588`): sem `user_id` quando `game_id=None`.

**Consequência**: se dois usuários têm facts do mesmo jogo (ex: Bully), o
job do usuário A pode consumir facts extraídos de documentos do usuário B.
Para `game_id=NULL` (curiosidades gerais), **todos os facts globais são
compartilhados** — qualquer usuário vê e consome facts de qualquer outro.

**Causa raiz 7 — Voices globais (diretório compartilhado).**
`routes.py:1164-1194`: `voices_dir = settings.voices_dir` (global). Todos
os usuários veem e podem usar vozes de outros. `POST /api/voices/upload`
salva em `data/voices/` sem subdiretório por usuário.

**Causa raiz 8 — `gpcg_youtube_user_id` default global no adapter.**
`google_integration_adapter.py:86`: `uid = user_id or self.settings.gpcg_youtube_user_id`.
Se `user_id` não for passado (bug em algum caminho), publica no canal
default global (user 4 = brunointegrations). O caminho principal
(`routes.py:1067`) passa `user_id=user.id` corretamente, mas o adapter
permite o fallback — é uma defesa em profundidade faltante.

### B.3 Banco de ideias praticamente não está sendo usado

**Causa raiz 9 — `gpcg_content_intelligence_enabled = False`.**
`config.py:288`. KnowledgeItems **nunca são consultados** pelo
ContentPlanningService (linha 111-112 é gated).

**Causa raiz 10 — KnowledgeItems não são scored automaticamente.**
`content_collectors.py:272` cria itens com `editorial_score=0.0`. Não há
processo automático de scoring editorial pós-coleta. Mesmo se a flag fosse
ligada, itens com score 0 seriam filtrados pelo threshold (default 50).

**Causa raiz 11 — `gpcg_curiosity_scoring_enabled = False`.**
Facts ranqueados apenas por `quality_score * novelty_score` (legado). Sem
curiosity_gap, surprise_potential, familiarity, insight_quality.

**Causa raiz 12 — `gpcg_story_finder_enabled = False`.**
Facts não são transformados em StoryConcept. Pipeline usa facts "raw" sem
filtragem narrativa. Se um fact é trivia isolado, vira vídeo mesmo assim.

**Consequência**: o pipeline consome apenas Facts de documentos do usuário,
ranqueados por quality*novelty, sem gate narrativo. Por isso volta para
"skate com carros", "guerra de comida", "5 táticas secretas" — são os
facts com quality*novelty mais alto entre os disponíveis, sem curadoria
editorial.

### B.4 Conteúdo e gameplay conceitualmente invertidos

**Causa raiz 13 — `gameplay_strategy` do VideoCreativePlan não é respeitado como constraint.**
`EditorialPlanner` produz `gameplay_strategy` (related, background_filler,
thematic_match) e `gameplay_query` (busca semântica). MAS:
- `GameplayRetriever` faz retrieval semântico só se `_should_use_semantic()`
  retornar True (`gameplay_retriever.py:208-214`), o que depende de
  eventos analisados existirem.
- Se não há eventos mapeados, cai para `GameplaySelector` random
  (legacy), que **ignora `gameplay_strategy`** — seleciona por
  `used_count` apenas.

**Causa raiz 14 — `gpcg_cross_game_gameplay_enabled = False`.**
Expansão por franquia/desenvolvedora desativada. Uma boa ideia sobre
"survival horror" não pode usar gameplay de Resident Evil se o usuário só
tem Silent Hill, mesmo sendo mesma franquia/gênero.

### B.5 Repetição excessiva de gameplay

**Causa raiz 15 — Sem reserva atômica de intervalos.**
Seleção é in-memory. Dois jobs concorrentes do mesmo usuário podem
carregar o mesmo `used_ranges_cache` e selecionar o mesmo intervalo.
Registro só acontece após QA pass — janela grande de race condition.

**Causa raiz 16 — `GameplayClipUsage` sem `user_id` (consumer).**
Vínculo ao consumidor é indireto via `Video.user_id`. Funciona para
isolamento (query via video), mas impede queries diretas de "quais
intervalos o usuário X consumiu" sem JOIN. Mais importante: se um vídeo
for deletado sem liberar clips, os ranges ficam órfãos sem saber de qual
consumidor.

**Causa raiz 17 — Tolerância de overlap = 1s, sem cooldown.**
`clip_usage_service.py:50`: overlap > 1s bloqueia. Mas `13:22→13:40` e
`13:41→13:59` são considerados disponíveis (1s de diferença). Não há
penalização por proximidade temporal (cooldown).

**Causa raiz 18 — Sync de clip_usages entre VPS e worker é por-source, não global.**
`worker_routes.py:1407-1413`: inclui `clip_usages` por source no payload.
`local_db_sync.py:249-258`: popula no DB local. O `source_id` é definido
como `src_data["id"]` (source do loop externo), o que está correto já que
o payload filtra clip_usages por source. **Não há bug aqui** (corrigido
após verificação direta do código — o subagent reportou incorretamente).

### B.6 Repetição editorial

**Mesma causa raiz de B.3** (causas 9-12). Sem CuriosityScorer, StoryFinder,
CreativeEngine, o pipeline cai em facts com quality*novelty alto
repetidamente. Sem Humanization, scripts mantêm padrões de IA. Sem
ScriptCritic V2, não há gate de hook_strength/retention/payoff.

### B.7 Qualidade de conteúdo e densidade editorial

**Causa raiz 19 — Sem gate editorial antes de TTS/render.**
`generation_service.py`: após `script_review` (linha 510-515), o pipeline
vai direto para `tts` (linha 517). **Não há gate que impeça um script
ruim de chegar ao render.**

**Causa raiz 20 — ScriptCritic após max_revisions prossegue silenciosamente.**
`script_critic.py:553-567`: `should_revise()` retorna `False` quando
`current_revisions >= max_revisions`. `generation_service.py:1090-1115`:
loop termina e pipeline continua com o último script, mesmo com veredito
REVISE. **Não há falha explícita nem marcação editorial.**

**Causa raiz 21 — `min_chars` é instrução de prompt, não gate.**
`script_service.py:400-404`: prompt diz "MUST be at least {min} chars".
`script_service.py:445-451`: se final < min_chars, tenta usar draft. Se
ambos abaixo, **o script curto é aceito**. Não há gate que impeça
prosseguimento.

**Causa raiz 22 — QA é permissivo e tardio.**
`qa_service.py:88-101`: vídeo < 10s → score -= 40; < 45s → score -= 30.
Mas `qa_service.py:146-149`: score >= 70 passa, ou score >= 60 sem issues
HIGH passa. Um vídeo de 22s com target 60s pode passar com score 85 se
não tiver outros issues. **QA ocorre APÓS render** — tarde demais para
evitar desperdício.

**Causa raiz 23 — `target_duration` é só referência no prompt.**
`script_service.py:400-401`: `~{plan.target_duration} seconds of TTS
narration`. Não há validação que o script produza narração próxima ao
target. O LLM pode ignorar.

**Causa raiz 24 — Sem mecanismo de enriquecimento antes da rejeição.**
StoryFinder rejeita fatos sem história (`is_story=false`) e tenta outro
fato. **Não existe "enriquecer este fato antes de rejeitar"** — buscar
contexto, consequência, contraste, impacto na fonte original.

---

## C. Problemas sistêmicos (sintomas com mesma origem)

### C.1 "Feature flags V2 desativadas" — causa única de múltiplos sintomas

**Sintomas**: banco de ideias não usado (B.3), repetição editorial (B.6),
densidade editorial baixa (B.7), sem gate narrativo (B.4).

**Origem única**: 5 feature flags V2 (`curiosity_scoring`,
`story_finder`, `creative_engine`, `humanization`, `script_critic_v2`)
+ `content_intelligence` estão em `False`. Os componentes **existem,
estão implementados, têm testes**, mas não rodam.

**Implicação**: grande parte dos problemas de qualidade editorial pode
ser resolvida **ativando flags existentes** + corrigindo o gap de
"KnowledgeItems não são scored" (causa 10) + adicionando gates que hoje
não existem (causas 19-21). **Não é necessário criar arquitetura nova** —
é necessário ativar e fortalecer a existente.

### C.2 "Queries sem user_id" — causa única de múltiplos sintomas

**Sintomas**: cross-user facts (B.2), cross-user knowledge items (B.2),
cross-user documents, cross-user content plans.

**Origem única**: Facts, KnowledgeItems, Documents, ContentPlans não
têm `user_id` em queries de leitura. O modelo `Fact` tem `game_id`
(nullable) mas **não tem `user_id`**. `KnowledgeItem` tem `user_id`
(models.py:1056-1106) mas queries não filtram. `Document` não tem
`user_id`.

**Implicação**: precisa de schema evolution (adicionar `user_id` em
Fact, Document) + revisão de queries. KnowledgeItem já tem `user_id`,
só precisa filtrar.

### C.3 "Config reconstruída tardiaamente" — causa única de múltiplos sintomas

**Sintomas**: legenda não aparece (B.1), formato errado (B.1), transição
errada (B.1), voz errada (B.1).

**Origem única**: múltiplos pontos no fluxo reconstruem config a partir
de defaults globais quando artifacts estão ausens/None, em vez de falhar
explicitamente. `RenderPlanBuilder:104-112` é o exemplo mais claro.

**Implicação**: centralizar defaults em config.py + falhar explicitamente
se artifact crítico estiver ausente, em vez de aplicar default silencioso.

---

## D. Lacunas entre arquitetura documentada e implementação

| Componente | Documentado | Implementado | Gap |
|------------|-------------|--------------|-----|
| CuriosityScorer | `EDITORIAL_REFACTOR_PLAN_V2.md` §3.1, §4.2 | `curiosity_scorer.py` completo | Flag off (default False) |
| StoryFinder | `EDITORIAL_REFACTOR_PLAN_V2.md` §4.1, Fase 2 | `story_finder.py` completo | Flag off (default False) |
| Humanization | `EDITORIAL_REFACTOR_PLAN_V2.md` §4.3, Fase 4 | `humanization.py` completo | Flag off (default False) |
| CreativeEngine beat-oriented | `EDITORIAL_REFACTOR_PLAN_V2.md` §3.4, Fase 3 | `creative_engine.py` com `generate_beat_oriented_material()` | Flag off (default False) |
| ScriptCritic V2 | `EDITORIAL_REFACTOR_PLAN_V2.md` §3.6, Fase 5 | `script_critic.py` com V2 dimensions | Flag off (default False) |
| Content Intelligence (KnowledgeItems) | `ARCHITECTURE_V2.md` §7 | `content_collectors.py`, `knowledge_item_routes.py` | Flag off + **sem scoring automático** |
| Game Enrichment | `ARCHITECTURE_V2.md` §6.5 | `game_enrichment_service.py` | Flag off (default False) |
| Cross-game gameplay | `ARCHITECTURE_V2.md` §8 | `gameplay_retriever.py` com expansão | Flag off (default False) |
| Gate editorial antes de render | `REFACTORY_V2.md` §7 "Gate editorial antes de TTS/render" | **NÃO IMPLEMENTADO** | Gap total |
| Enriquecimento antes da rejeição | `REFACTORY_V2.md` §7 "Expansão inteligente antes da rejeição" | **NÃO IMPLEMENTADO** | Gap total |
| Reserva atômica de intervalos | `REFACTORY_V2.md` §5 "Concorrência entre jobs" | **NÃO IMPLEMENTADO** | Gap total |
| Cooldown temporal | `REFACTORY_V2.md` §5 "Overlap" | **NÃO IMPLEMENTADO** (só tolerância 1s) | Gap total |
| Aceite explícito para gameplay pública | `REFACTORY_V2.md` §5c | **NÃO IMPLEMENTADO** | Gap total |
| Deleção diferencia pendente/publicado | `REFACTORY_V2.md` §5b | **NÃO IMPLEMENTADO** | Gap total |
| `user_id` em Fact/Document | implícito em multi-user | **NÃO IMPLEMENTADO** em schema | Gap total |
| Validação factual de fontes online | `REFACTORY_V2.md` "Validação com IA antes do uso editorial" | **NÃO IMPLEMENTADO** | Gap total |
| Source provenance tracking | `REFACTORY_V2.md` "Rastreabilidade da fonte" | Parcial (KnowledgeItem tem source_type) | Gap parcial |

---

## E. Plano de implementação (menor conjunto coerente)

### E.1 Matriz AS-IS → TO-BE

| Área | AS-IS comprovado | TO-BE exigido | Gap | Mudança mínima |
|------|------------------|---------------|-----|----------------|
| **Multiusuário — Facts** | Queries por `game_id` sem `user_id` (content_planning_service.py:91-107, worker_routes.py:1336, script_service.py:725-742) | Job de A nunca usa facts de B | `Fact` sem `user_id` | Adicionar `user_id` em Fact + Document; filtrar queries; migration com `user_id=NULL` para legados (públicos) |
| **Multiusuário — KnowledgeItems** | Queries por `game_id` sem `user_id` (content_planning_service.py:357-363) | Job de A nunca usa KI de B | `KnowledgeItem` tem `user_id` mas queries não filtram | Adicionar `.where(KnowledgeItem.user_id == user_id)` em todas as queries |
| **Multiusuário — Voices** | Diretório global `data/voices/` (routes.py:1164-1194) | Vozes isoladas por usuário | Sem isolamento filesystem | Migrar para `data/voices/{user_id}/`; migration move arquivos existentes para user 4 (admin) |
| **Multiusuário — YouTube** | Adapter fallback para `gpcg_youtube_user_id` global (google_integration_adapter.py:86) | Falhar se `user_id` None | Defesa em profundidade faltante | Remover fallback global; exigir `user_id` explícito |
| **Config — RenderPlanBuilder** | Se `subtitle_config` None, usa defaults globais (render_plan_builder.py:104-112) | Falhar se artifact crítico ausente | Reconstrução tardia | Se `subtitle_config` None E job tem artifacts, logar warning + usar artifacts; se realmente sem config, usar defaults mas marcar no job |
| **Config — create_job_from_decision** | Defaults hardcoded `0`, `""` (automation_routes.py:411-429) | Defaults de config.py | Caminho alternativo inconsistente | Usar `settings.gpcg_*` como defaults |
| **Config — transition** | Sem defaults em config.py | Defaults explícitos | Gap | Adicionar `gpcg_transition_type`, `gpcg_transition_duration` em config.py |
| **Config — snapshot** | Job.artifacts é snapshot (correto) | Documentar + testar | Implícito | Documentar invariant: job usa snapshot; testar que mudança de Automation não afeta job em andamento |
| **Conteúdo — KnowledgeItems** | Flag off + sem scoring automático (config.py:288, content_collectors.py:272) | KIs scored e consumidos | 2 gaps | (1) Ativar flag ou tornar default True; (2) Adicionar scoring automático pós-coleta (reusar CuriosityScorer adaptado) |
| **Conteúdo — CuriosityScorer** | Flag off (config.py:193) | Ativo | Flag | Ativar `gpcg_curiosity_scoring_enabled=True` |
| **Conteúdo — StoryFinder** | Flag off (config.py:208) | Ativo | Flag | Ativar `gpcg_story_finder_enabled=True` |
| **Editorial — Humanization** | Flag off (config.py:225) | Ativo | Flag | Ativar `gpcg_humanization_enabled=True` |
| **Editorial — CreativeEngine** | Flag off (config.py:109) | Ativo (com beat-oriented) | Flag | Ativar `gpcg_creative_engine_enabled=True` + `gpcg_creative_engine_beat_oriented=True` |
| **Editorial — ScriptCritic V2** | Flag off (config.py:249) | Ativo | Flag | Ativar `gpcg_script_critic_v2_enabled=True` + `gpcg_script_critic_section_based=True` |
| **Editorial — Gate antes de render** | Não existe (generation_service.py:510→517) | Gate que bloqueia script ruim | Gap total | Adicionar estágio `editorial_gate` entre script_review e tts; se ScriptCritic REVISE após max_revisions → falhar job (não prosseguir) |
| **Editorial — Enriquecimento** | Não existe | Enriquecer fato antes de rejeitar | Gap total | Adicionar `fact_enricher.py` que busca contexto em Documents/KIs relacionados antes de StoryFinder rejeitar |
| **Editorial — Anti-padding** | ScriptCritic V1 não detecta padding explicitamente | Detectar e rejeitar padding | Gap | ScriptCritic V2 já tem dimensões melhores; adicionar detecção de redundância semântica no prompt V2 |
| **Editorial — min_chars como diagnóstico** | Instrução de prompt, não gate (script_service.py:400-404) | Sinal de diagnóstico, não regra absoluta | Gap parcial | Manter instrução no prompt; adicionar log/warning se script < min_chars; NÃO bloquear automaticamente |
| **Editorial — target_duration** | Referência no prompt (script_service.py:400-401) | Constraint com tolerância | Gap | Adicionar validação pós-script: se duração estimada < target * 0.5 → trigger enriquecimento ou rejeição |
| **Gameplay — Reserva atômica** | Seleção in-memory, registro pós-QA (gameplay_selector.py, generation_service.py:732-739) | Reserva atômica escopada por consumidor | Gap total | Adicionar `GameplayClipReservation` com `(consumer_user_id, source_id, start_sec, end_sec, job_id, status)`; INSERT atômico; liberação em falha/cancelamento |
| **Gameplay — user_id em usage** | `GameplayClipUsage` sem `user_id` (models.py:571-592) | `consumer_user_id` explícito | Gap | Adicionar coluna; migration com `consumer_user_id = video.user_id` via JOIN |
| **Gameplay — Cooldown** | Tolerância 1s apenas (clip_usage_service.py:50) | Cooldown configurável | Gap | Adicionar `gpcg_gameplay_cooldown_sec` (default 30s); penalizar candidatos dentro de cooldown |
| **Gameplay — Deleção pendente vs publicado** | Não diferencia (routes.py:1089-1158) | Pendente libera automático; publicado mantém | Gap | Verificar `Video.status` antes de liberar; pendente → `release_clips=True` automático; publicado → perguntar |
| **Gameplay — Aceite explícito pública** | Toggle direto (routes.py:376-401) | Modal/termo + persistência do aceite | Gap | Adicionar endpoint `POST /gameplays/{id}/request-public` com termo; `PATCH /visibility` exige aceite prévio |
| **Gameplay — bug local_db_sync** | `source_id=src_data["id"]` (local_db_sync.py:254) | Correto (não é bug) | Falso positivo | Nenhuma mudança necessária |
| **Worker — contratos** | Payload implícito, defaults globais | Payload explícito com user_id, config snapshot | Gap parcial | Garantir que `GET /jobs/{id}/data` inclua `user_id` em todos os sub-payloads; worker não infere user |
| **QA — validação determinística** | LLM + FFprobe (qa_service.py) | Validar config aplicada (formato, legenda) | Gap | Adicionar checks determinísticos: aspect ratio = config, duração dentro de tolerância |
| **Retry — idempotência** | Não testado | Retry seguro (não duplica publish, não duplica clip usage) | Gap | Adicionar idempotency keys; testar failure paths |

### E.2 Fases de implementação

#### Fase 1 — Integridade multiusuário, contratos, propagação de config

**Prioridade**: 1 (integridade > tudo).

**Mudanças**:
1. Adicionar `user_id` em `Fact` e `Document` (migration; legados = NULL = pool público).
2. Filtrar todas as queries de Facts/KnowledgeItems/Documents/ContentPlans por `user_id` (ou permitir NULL como pool público explícito).
3. Isolar voices por usuário (`data/voices/{user_id}/`).
4. Remover fallback global de `gpcg_youtube_user_id` no adapter; exigir `user_id` explícito.
5. Corrigir `create_job_from_decision` para usar defaults de config.py.
6. Adicionar `gpcg_transition_type`, `gpcg_transition_duration` em config.py.
7. Garantir que `RenderPlanBuilder` use artifacts do job, não defaults globais (logar warning se ausente).
8. Garantir que `GET /jobs/{id}/data` inclua `user_id` em todos os sub-payloads.
9. Documentar invariant: job usa snapshot de config (não config atual).
10. Testes: cross-user isolation (A nunca usa facts/voices/canal de B).

#### Fase 2 — Lifecycle de gameplay

**Prioridade**: 2.

**Mudanças**:
1. Adicionar `consumer_user_id` em `GameplayClipUsage` (migration).
2. Criar `GameplayClipReservation` (modelo novo justificado: reserva atômica pré-seleção).
3. Implementar reserva atômica no `GameplaySelector` (INSERT condicional).
4. Liberação automática em falha/cancelamento/STOP (job lifecycle hooks).
5. Transição reservado → utilizado quando Video é persistido.
6. Deleção de vídeo: pendente libera automático; publicado mantém (ou libera com decisão explícita).
7. Cooldown temporal configurável (`gpcg_gameplay_cooldown_sec`).
8. Política de overlap documentada (bloqueio duro > N segundos; penalização em cooldown).
9. Aceite explícito para gameplay pública (modal + persistência do aceite em `metadata_json`).
10. Corrigir bug `local_db_sync.py:254` (`source_id=cu_data["source_id"]`).
11. Fallback configurável: `stop` | `allow_public` (por usuário, em Automation.config).
12. Testes: reserva, concorrência, STOP, retry, fallback, isolamento.

#### Fase 3 — Conteúdo/fontes

**Prioridade**: 3.

**Mudanças**:
1. Ativar `gpcg_content_intelligence_enabled=True`.
2. Implementar scoring automático de KnowledgeItems pós-coleta (adaptar CuriosityScorer).
3. Ativar `gpcg_curiosity_scoring_enabled=True`.
4. Garantir que ContentPlanningService consuma Facts + KnowledgeItems (unified candidates).
5. Validação factual de fontes online antes do Story Finder (gate: clickbait, promoção, rumor).
6. Provenance: rastrear ideia → fonte → fatos → roteiro (em `ContentPlan.metadata_json`).
7. Testes: KIs entram no pipeline; rejeição de clickbait; rastreabilidade.

#### Fase 4 — Editorial

**Prioridade**: 4.

**Mudanças**:
1. Ativar `gpcg_story_finder_enabled=True`.
2. Ativar `gpcg_humanization_enabled=True`.
3. Ativar `gpcg_creative_engine_enabled=True` + `gpcg_creative_engine_beat_oriented=True`.
4. Ativar `gpcg_script_critic_v2_enabled=True` + `gpcg_script_critic_section_based=True`.
5. Adicionar gate editorial antes de TTS: se ScriptCritic REVISE após max_revisions → **falhar job** (não prosseguir).
6. Adicionar `fact_enricher.py`: enriquecer fato antes de rejeitar (contexto, consequência, contraste).
7. Anti-padding: ScriptCritic V2 com detecção de redundância semântica.
8. `target_duration` como constraint com tolerância: validação pós-script.
9. `min_chars` como diagnóstico: log/warning, não gate absoluto.
10. Testes: gate bloqueia script ruim; enriquecimento; anti-padding; duração curta legítima vs subdesenvolvimento.

#### Fase 5 — Homologação end-to-end

**Prioridade**: 5.

**Mudanças**:
1. Gerar vídeos com 2 usuários, configs diferentes, gameplays diferentes.
2. Verificar: config aplicada, canal correto, ideas diferentes, gameplay diverso.
3. Homologação editorial: "Por que este vídeo merece existir?" para cada vídeo.
4. Teste de compressão editorial (padding residual).
5. Compliance matrix contra REFACTORY_V2.

---

## F. Invariantes (devem permanecer verdadeiros)

### Multiusuário
- Job de A nunca usa configuration privada de B. `CONFIRMED_IN_CODE` para config (job.artifacts é snapshot). **GARANTIR** para facts/KIs/documents.
- Job de A nunca usa gameplay privada de B. `CONFIRMED_IN_CODE` (GameplaySelector filtra por user_id).
- Job de A nunca publica no canal de B. `CONFIRMED_IN_CODE` (routes.py:1023 valida ownership). **FORTALECER** removendo fallback global do adapter.
- Credencial de publicação pertence ao mesmo contexto do job. `CONFIRMED_IN_CODE` (user_id passado explicitamente).

### Gameplay
- `selected != used`. `CONFIRMED_IN_CODE` (registro só após QA). **FORTALECER** com reserva explícita.
- Reserva escopada pelo consumidor. **NOT_IMPLEMENTED** → Fase 2.
- Usuários diferentes podem consumir o mesmo intervalo de gameplay pública. `CONFIRMED_IN_CODE` (GameplayClipUsage via video.user_id).
- Dois jobs do mesmo consumidor não podem reservar o mesmo intervalo simultaneamente. **NOT_IMPLEMENTED** → Fase 2.
- STOP/falha/cancelamento antes de vídeo persistido libera reserva. **NOT_IMPLEMENTED** → Fase 2.
- Segmento só vira usado quando existe vídeo persistido no GPCG. `CONFIRMED_IN_CODE` (registro em generation_service.py:732-739 após QA).
- Exclusão de vídeo pendente libera automaticamente seus segmentos. **NOT_IMPLEMENTED** → Fase 2.
- Vídeo publicado mantém segmentos usados. `CONFIRMED_IN_CODE` (release_clips default False).
- Gameplay privada nunca entra no pool público. `CONFIRMED_IN_CODE` (filtro is_public + user_id).
- Gameplay própria tem prioridade sobre pública. `CONFIRMED_IN_CODE` (GameplaySelector tenta próprio primeiro).

### Editorial
- factualidade > completude > densidade > target_duration > comprimento textual. **NOT_IMPLEMENTED** como invariant explícito → Fase 4 (gate).
- `min_chars` não pode gerar padding. **PARTIALLY_IMPLEMENTED** (instrução no prompt) → Fase 4 (não gate absoluto).
- `target_duration` não pode gerar padding. **NOT_IMPLEMENTED** → Fase 4.
- Fonte online não vira roteiro diretamente. `CONFIRMED_IN_CODE` (anti-plágio + script service). **FORTALECER** com validação factual.
- Story Finder só recebe matéria-prima editorial válida. **NOT_IMPLEMENTED** (flag off) → Fase 3+4.
- Roteiro reprovado persistentemente não chega silenciosamente ao render. **NOT_IMPLEMENTED** → Fase 4 (gate).

---

## G. State machines (AS-IS)

### G.1 Job

```
queued → running → (stages) → completed
                  ↘ failed
                  ↘ retrying → running
                  ↘ paused
```

`CONFIRMED_IN_MODEL` (JobStatus enum, models.py). Transições não
validadas explicitamente (qualquer status pode ir para qualquer status
via UPDATE direto).

### G.2 Video

```
pending → ready → qa_passed → published
                 ↘ qa_failed
```

`CONFIRMED_IN_MODEL` (VideoStatus enum, models.py:137). Sem estado
"approved" ou "aguardando publicação" explícito — `qa_passed` funciona
como "aprovado para publicação".

**TO-BE** (proposto):
```
pending → ready → qa_passed → published
                 ↘ qa_failed
                 ↘ deleted (pendente excluído — libera clips)
published → deleted_local (mantém clips)
```

### G.3 Gameplay interval (TO-BE)

```
AVAILABLE → RESERVED → USED
          ↘ AVAILABLE (failure/STOP/cancel/timeout)
USED → AVAILABLE (vídeo pendente excluído)
USED → USED (vídeo publicado — mantém)
```

`NOT_IMPLEMENTED` (não há estados explícitos hoje). Fase 2 implementa.

---

## H. Contratos Control Plane ↔ Worker

### H.1 Payload `GET /api/jobs/{id}/data`

| Campo | Origem | Owner | Obrigatório | Default permitido? | Consumidor |
|-------|--------|-------|-------------|-------------------|------------|
| `job.user_id` | `Job.user_id` | Control Plane | SIM | NÃO | Worker (filtra queries) |
| `job.artifacts` | `Job.artifacts` (snapshot) | Control Plane | SIM | NÃO | GenerationService |
| `job.game_id` | `Job.game_id` | Control Plane | Sim se generate_short | — | ContentPlanning |
| `gameplays[].user_id` | `GameplaySource.user_id` | Control Plane | SIM | NÃO | GameplaySelector |
| `gameplays[].clip_usages` | `GameplayClipUsage` por source | Control Plane | SIM | NÃO | GameplaySelector |
| `facts[]` | `Fact` por game_id | Control Plane | SIM | NÃO | ContentPlanning |
| `knowledge_items[]` | `KnowledgeItem` por game_id | Control Plane | Condicional | — | ContentPlanning |
| `documents[]` | `Document` por game_id | Control Plane | SIM | NÃO | ScriptService (source texts) |
| `content_plans[]` | `ContentPlan` por game_id | Control Plane | SIM | NÃO | GenerationService |
| `automation.config` | `Automation.config` | Control Plane | NÃO (snapshot já em artifacts) | — | Não usado no worker |

**Invariant**: worker **não infere** user_id, channel, voice. Tudo vem
explicitamente no payload. `CONFIRMED_IN_CODE` para user_id (linha 546
do _serialize_job). **GARANTIR** para facts/KIs/documents (hoje sem
user_id no filtro — Fase 1 corrige).

### H.2 Source of truth

```
user ownership       → Job.user_id (snapshot no momento de criação)
job context          → Job.artifacts (snapshot)
video settings       → Job.artifacts["subtitle_config"], ["video_format"], ...
voice                → Job.artifacts["voice_path"]
channel              → User.google_user_id (via google-integration service)
gameplay visibility  → GameplaySource.is_public
gameplay usage       → GameplayClipUsage (via Video.user_id)
selected source/fact → ContentPlan.fact_id / metadata_json["knowledge_item_id"]
publication state    → Video.status + Video.youtube_video_id
```

**Duplicação encontrada**: `gpcg_humanization_enabled` definido 2x em
config.py (linhas 135 e 225) — **CONFLICTING_IMPLEMENTATIONS** (pydantic
usa o último, mas é confuso). Corrigir na Fase 4.

---

## I. Decisões confirmadas com o usuário

### I.1 Facts/KnowledgeItems — modelo híbrido (como gameplay)

**Decisão confirmada**: seguir o mesmo modelo de GameplaySource.

- **Facts/KIs coletados de fontes públicas pelo sistema** (RSS, Wikipedia,
  etc.): pool compartilhado. `user_id = NULL` = system-collected, público.
- **Facts/KIs criados manualmente pelo usuário** (upload de documento,
  ideia solicitada): `user_id` obrigatório. Usuário define `is_public`
  (default: privado). Se público, entra no pool de outros usuários.
- **Usage history por consumidor**: cada usuário mantém histórico
  independente de quais facts/KIs já consumiu (como gameplay clips).
  `Fact.used_count` hoje é global — precisa tornar-se per-consumer
  (tabela `FactUsage` ou similar, como `GameplayClipUsage`).

**Queries**: `(user_id == job.user_id) OR (user_id IS NULL) OR (is_public == True)`.
Para facts privados de terceiros: nunca acessíveis.

### I.2 KnowledgeItems — mesma decisão que I.1

KIs coletados pelo sistema = pool compartilhado. KIs criados manualmente
= privados por default, com `is_public` opcional.

### I.3 Snapshot vs config atual

**Decisão confirmada**: manter snapshot (`Job.artifacts`). Documentar
como invariant. Testar: job criado com config A, usuário muda para B,
job continua com A.

### I.4 Política de fallback (stop vs allow_public)

**Decisão confirmada**: config por usuário na UI, como definido no
REFACTORY_V2 §5 "Comportamento de fallback". Adicionar
`fallback_policy` em `Automation.config`: `"stop"` | `"allow_public"`.
UI apresenta a escolha explicitamente. Default: `"allow_public"` (não
quebra comportamento atual sem ação do usuário).

### I.5 Vídeo publicado excluído — trechos

**Decisão confirmada**: manter trechos utilizados. Exclusão local de
vídeo publicado NÃO libera trechos. Futuramente pode-se implementar
liberação opcional, mas por enquanto permanecem utilizados.

### I.6 Cooldown default

**Decisão proposta** (não confirmada, assumo default seguro):
`gpcg_gameplay_cooldown_sec = 30.0` (30 segundos antes/depois de trecho
usado são penalizados, não bloqueados). Overlap > 5s é bloqueio duro.
Documentar e tornar configurável.

---

## J. Resumo de riscos restantes (após implementação)

1. **LLM não determinístico**: gates editoriais dependem de LLM. Mesmo
   com prompts melhores, LLM pode aprovar conteúdo fraco. Mitigação:
   gates determinísticos (duração, min_chars como sinal) + QA técnico.
2. **Performance**: ativar 5 flags V2 adiciona 5 chamadas LLM por job.
   Mitigação: paralelizar onde possível; cache de CuriosityScorer.
3. **Migration de dados existentes**: adicionar `user_id` em Fact/Document
   requer migration. Legados ficam `NULL` (pool público). Testar upgrade.
4. **Complexidade de reserva**: `GameplayClipReservation` adiciona
   estado. Mitigação: cleanup automático de reservas órfãs (job
   expirado/cancelado).

---

## K. Próximos passos

1. **Confirmar decisões unresolved** (I.1-I.5) com o usuário.
2. **Fase 1**: integridade multiusuário + contratos + config (maior risco).
3. **Fase 2**: lifecycle de gameplay (segundo maior risco).
4. **Fase 3**: conteúdo/fontes (ativação + scoring).
5. **Fase 4**: editorial (ativação + gates).
6. **Fase 5**: homologação end-to-end com evidência.

Cada fase: implementar → testes unitários → testes de integração →
regressões existentes → validar invariantes → seguir.
