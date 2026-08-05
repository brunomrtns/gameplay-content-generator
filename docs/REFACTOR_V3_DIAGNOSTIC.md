# GPCG V3 Refactor — Diagnóstico Arquitetural

## 1. Arquitetura Mapeada (com evidência)

### Control Plane (VPS)
- **API:** FastAPI em `src/gpcg/api/app.py`, routers em `api/*.py`
- **Auth:** BI Identity SSO (cookie `bi_auth`), validado em `infrastructure/auth.py:30-67`
- **DB:** SQLite em `/app/data/gpcg.db`, models em `domain/models.py`
- **Workers:** Registry em `Worker` table, heartbeat/claim em `api/worker_routes.py`
- **Jobs:** Fila com prioridade + capabilities, claim atômico em `worker_routes.py:438-538`
- **Automação:** `Automation` table (1 por usuário), config em JSON
- **Idea Queue:** Armazenada em `Automation.config["idea_queue"]` (lista de dicts)
- **Vídeos:** `Video` table com status, storage_key, youtube fields
- **Endpoints SSO:** `/api/automation/*`, `/api/knowledge-items/*`, `/api/videos/*`, etc
- **Endpoints Worker:** `/api/jobs/*`, `/api/workers/*`, `/api/automation/check`

### Compute Plane (Worker Local)
- **RemoteWorker:** `src/gpcg/worker/remote_worker.py`, poll a cada 5s
- **Registro:** `POST /api/workers/register` com capabilities + GPU info
- **Heartbeat:** Thread background a cada 10s
- **Job claim:** `POST /api/jobs/claim` com capability matching
- **Sync DB:** `local_db_sync.py` cria SQLite temp com dados da VPS
- **Pipeline:** `GenerationService._run_pipeline()` (15+ stages)
- **Upload vídeo:** `POST /api/jobs/{id}/upload-video`
- **Sync resultados:** `POST /api/jobs/{id}/sync` (ContentPlan, Script, Video, clip_usages)

### Contrato VPS → Worker (job_data)
Endpoint: `GET /api/jobs/{id}/data` (`worker_routes.py:1377-1580`)

| Campo | Origem | Persistência | Transporte | Consumidor | Obrigatório |
|-------|--------|-------------|-----------|-----------|-------------|
| job.id/type/user_id | Job table | jobs | job_data.job | local_db_sync, pipeline | SIM |
| job.artifacts | Job.artifacts (JSON) | jobs | job_data.job.artifacts | pipeline (gameplay_preference, reuse_override, queued_ki_id) | SIM |
| game | Game table | games | job_data.game | local_db_sync, pipeline | SIM (se game_id) |
| background_game | Game table | games | job_data.background_game | local_db_sync, pipeline | SIM (se curiosity) |
| content_plan | ContentPlan table | content_plans | job_data.content_plan | local_db_sync | NÃO (se novo) |
| scripts | Script table | scripts | job_data.scripts | local_db_sync | NÃO |
| facts | Fact table | facts | job_data.facts | local_db_sync, pipeline | NÃO (só generate_short) |
| knowledge_items | KnowledgeItem table | knowledge_items | job_data.knowledge_items | local_db_sync, pipeline | NÃO (V2) |
| gameplay_sources | GameplaySource table | gameplay_sources | job_data.gameplay_sources | local_db_sync, retriever | SIM |
| automation | Automation table | automations | job_data.automation | local_db_sync | SIM |
| **ChannelProfile** | **ChannelProfile table** | **channel_profiles** | **NÃO ENVIADO** | **pipeline espera no DB local** | **SIM (GAP)** |

### Contrato Worker → VPS (result/sync)
| Endpoint | Payload | Propósito |
|----------|---------|-----------|
| `POST /jobs/{id}/status` | status, stage, progress, error, artifacts | Update progresso |
| `POST /jobs/{id}/upload-video` | multipart file | Upload vídeo renderizado |
| `POST /jobs/{id}/sync` | content_plan, script, video, artifacts, clip_usages | Sync registros |
| `POST /jobs/{id}/result` | status, error, artifacts, video | Resultado final |
| `POST /jobs/{id}/sync-knowledge-items` | items, cleaned_count | Sync KIs coletadas |

## 2. Descobertas Classificadas

### CONFIRMED_IN_CODE — Gaps Críticos

#### G1: ChannelProfile NÃO é sincronizado para o worker
- **Evidência:** `local_db_sync.py` não importa nem copia `ChannelProfile`
- **Evidência:** `generation_service.py:376-379` tenta `session.query(ChannelProfile)...` no DB local
- **Impacto:** ChannelProfile é sempre `None` no worker → contexto do canal é ignorado em content_planning, story_finding, editorial_planning
- **Arquivos:** `src/gpcg/worker/local_db_sync.py`, `src/gpcg/application/generation_service.py:362-384`

#### G2: NÃO existe reconciliador de fila automático
- **Evidência:** Busca por "reconcile", "refill", "auto_fill", "target", "queue_mode" = 0 matches
- **Evidência:** `Automation.config` não tem campos `queue_mode` nem `queue_target`
- **Impacto:** Fila é puramente manual. Sistema não abastece fila automaticamente.
- **Arquivos:** `src/gpcg/api/automation_routes.py`, `src/gpcg/domain/models.py:315-344`

#### G3: NÃO existe modo manual/automático
- **Evidência:** Sem campos `queue_mode` no model nem na UI
- **Impacto:** Quando fila está vazia, automação faz decisão editorial autônoma (via LLM) e gera vídeo fora da fila
- **Arquivos:** `src/gpcg/api/automation_routes.py:698-987` (create_job_from_automation)

#### G4: Job NÃO carrega snapshot de config
- **Evidência:** `create_job_from_automation` lê `auto.config` no momento da criação e passa como kwargs
- **Evidência:** `job.artifacts` guarda params de vídeo mas NÃO guarda snapshot completo da config
- **Impacto:** Se usuário muda config durante execução, job já criado usa params antigos (OK), mas retry pode ler config diferente
- **Arquivos:** `src/gpcg/api/automation_routes.py:775-805`

#### G5: Ideia é marcada como "used" apenas no sync final
- **Evidência:** `worker_routes.py:1641-1648, 1671-1678` marca KI como `used` durante sync
- **Evidência:** Em falha, KI volta para fila (`worker_routes.py:674-680, 818-824`)
- **Status:** Lifecycle está correto — KI só é marcada used quando Video é persistido
- **Arquivos:** `src/gpcg/api/worker_routes.py`, `src/gpcg/application/knowledge_item_service.py`

### CONFIRMED_IN_CODE — Funcionando

#### F1: gameplay_preference é respeitado pelo worker
- **Evidência:** `generation_service.py:677-699` lê `gameplay_preference` de `job.artifacts`
- **Evidência:** Log confirmou: `gameplay_preference: user chose game #6, switching`

#### F2: reuse_override é respeitado pelo worker
- **Evidência:** `generation_service.py:680-688` aplica `effective_max_uses` baseado em override

#### F3: Reordenação persiste no DB
- **Evidência:** `knowledge_item_routes.py:411-440` reescreve `Automation.config["idea_queue"]`
- **Evidência:** Preserva metadata existente (gameplay_preference, reuse_override)

#### F4: Claim de job é atômico
- **Evidência:** `worker_routes.py:504-520` usa UPDATE condicional (`WHERE status='queued'`)

#### F5: Heartbeat detecta worker offline
- **Evidência:** `worker_routes.py:224-236` timeout check, `worker_routes.py:395-432` auto-mark offline

#### F6: Re-queue em falha funciona
- **Evidência:** `worker_routes.py:674-680` e `818-824` re-inserem KI no topo da fila

### PARTIALLY_IMPLEMENTED

#### P1: Dashboard vídeos recentes — só display
- **Evidência:** `dashboard.tsx:212-266` mostra thumbnails, clique abre player
- **Gap:** Não tem ação de publicar do Dashboard, não tem link para YouTube
- **Arquivos:** `frontend/src/pages/dashboard.tsx`

#### P2: Tela de Conteúdo — sem tabs
- **Evidência:** `content.tsx:64` tem `tab` state mas só "media"
- **Gap:** ChannelProfile está no final da página, não em tab separada
- **Arquivos:** `frontend/src/pages/content.tsx`

#### P3: Workers — pode ter duplicidade
- **Evidência:** Worker se registra por `worker_id` (string única)
- **Risco:** Restart com novo ID cria registro novo, antigo fica stale
- **Arquivos:** `src/gpcg/api/worker_routes.py:280-328`

### NOT_IMPLEMENTED

#### N1: Seleção automática de ideias para fila
- Não existe função que selecione candidatas baseado em editorial_score, relevância, etc

#### N2: Associação semântica ideia ↔ gameplay
- GameplayRetriever tem `_score_source_fit` mas não usa mapeamento semântico de eventos
- Não usa embeddings de KnowledgeItem vs GameplayEvent

#### N3: Política de uma GameplaySource por vídeo
- GameplayRetriever pode misturar sources em GENERAL_TOPIC
- Não há regra centralizada que force uma única source

#### N4: Config snapshot no job
- Job não guarda snapshot da config de automação
- Retry pode usar config diferente

## 3. Matriz AS-IS → TO-BE

| Domínio | AS-IS (comprovado) | TO-BE | Gap | Menor mudança coerente | Teste |
|---------|-------------------|-------|-----|----------------------|-------|
| **Dashboard vídeos** | Display only, abre player | Assistir + publicar + link YT | Sem ação de publicar | Reusar handler de videos.tsx no dashboard | E2E: publicar do dashboard |
| **Dashboard workers** | Mostra todos workers | Mostra apenas workers ativos, com tipo | Stale workers aparecem | Cleanup de workers offline > 24h | Unit: list_workers filtra stale |
| **Conteúdo tabs** | Sem tabs, tudo em uma página | Tabs: Gravações + Identidade | ChannelProfile escondido | Adicionar Tab component | E2E: switch tabs preserva form |
| **Fila como fonte** | Fila vazia → decisão editorial autônoma | Fila vazia → não gera | Modo manual não existe | Adicionar queue_mode="manual" | Unit: manual + fila vazia = 0 jobs |
| **Modo automático** | Não existe | Sistema mantém target de ideias | Reconciliador não existe | Implementar reconcile_idea_queue() | Unit: target=5, fila=4 → insere 1 |
| **Reconciliador** | Não existe | Idempotente, respeita manuais | — | Função em automation_routes ou service | Unit: 2 ciclos não ultrapassam target |
| **Reordenação** | Funciona, persiste | Manter + drag-and-drop | Já funciona | Adicionar DnD no frontend | E2E: reorder → próximo job usa nova ordem |
| **Lifecycle ideia** | fresh→used, re-queue em falha | Manter | Já funciona | — | — |
| **ChannelProfile no worker** | NÃO é sincronizado | Incluir no job_data | Gap crítico | Adicionar ChannelProfile ao payload + local_db_sync | Unit: pipeline tem acesso ao profile |
| **Config snapshot** | Lê config atual na criação | Snapshot no job.artifacts | Retry não é determinístico | Adicionar config_snapshot aos artifacts | Unit: job carrega snapshot |
| **Associação semântica** | Retriever usa _score_source_fit | Usar embeddings KI↔Event | Não usa embeddings | Adicionar busca semântica no retriever | Unit: ideia sobre bicicleta → evento de bike |
| **Uma source por vídeo** | Retriever pode misturar | Forçar uma source | Sem política central | Adicionar regra no retriever | Unit: todos clips da mesma source |
| **Disponibilidade UI** | getGameplayAvailability existe | Validar mesma regra no worker | UI pode mostrar disponível mas worker não achar | Usar mesma função de eligibility | Unit: UI e worker concordam |
| **Estados de loading** | Básico | Estados ricos (salvando, reconciliando) | Spinners genéricos | Adicionar estados específicos | Visual |

## 4. Source of Truth

| Conceito | Fonte de verdade (AS-IS) | Fonte de verdade (TO-BE) | Gap |
|----------|-------------------------|-------------------------|-----|
| Ordem da fila | `Automation.config["idea_queue"]` | Manter | — |
| Ideia reservada | `Job.artifacts["queued_knowledge_item_id"]` | Manter | — |
| Ideia usada | `KnowledgeItem.status == "used"` + `KnowledgeItemUsage` | Manter | — |
| Config de automação | `Automation.config` (JSON) | Manter + snapshot no job | Adicionar snapshot |
| Identidade do canal | `ChannelProfile` table (VPS) | Manter + sincronizar para worker | Adicionar sync |
| Preferência de gameplay | `Job.artifacts["gameplay_preference"]` | Manter | — |
| GameplaySource escolhida | `Job.gameplay_source_id` (set durante pipeline) | Manter | — |
| Intervalos selecionados | `GameplayClipUsage` table | Manter | — |
| Status do worker | `Worker` table + heartbeat | Manter + cleanup stale | Adicionar cleanup |
| Status do vídeo | `Video.status` | Manter | — |
| Estado de publicação | `Video.status` + `youtube_video_id` | Manter | — |
| Modo da fila | NÃO EXISTE | `Automation.config["queue_mode"]` | Criar |
| Target da fila | NÃO EXISTE | `Automation.config["queue_target"]` | Criar |

## 5. Plano de Implementação por Fases (ajustado)

### Fase 1 — Contratos, contexto e determinismo
1. **Sincronizar ChannelProfile para o worker** (G1)
   - Adicionar ChannelProfile ao payload de `/api/jobs/{id}/data`
   - Adicionar ChannelProfile ao `local_db_sync.py`
   - Pipeline já carrega do DB local (`generation_service.py:376`) — só precisa existir no DB
   - Validar: profile correto pertence ao usuário correto, sem fallback indevido

2. **Config snapshot no job** (G4)
   - Adicionar `config_snapshot` aos `job.artifacts` na criação (apenas campos necessários)
   - NÃO copiar secrets, tokens, credenciais
   - Pipeline lê snapshot em vez de config atual
   - Retry usa snapshot → mesma intenção editorial

3. **Consumo de ideias públicas por usuário** (1.3)
   - `KnowledgeItem.status == "used"` NÃO pode fazer ideia pública desaparecer para outros
   - Já existe `KnowledgeItemUsage` com `consumer_user_id` — usar como autoridade de consumo
   - Validação de elegibilidade na fila já usa `is_used_by_consumer` — verificar consistência
   - Status global `used` só vale para KIs privadas (user_id != NULL)

4. **GameplaySource como fonte de verdade** (1.4)
   - Validar cadeia: seleção → Job.gameplay_source_id → worker → RenderPlan → SelectedClips → sync → Video
   - Garantir uma única source por vídeo
   - Retry não escolhe outra source silenciosamente

5. **Validação de contratos VPS ↔ worker**
   - Confirmar todos os campos críticos são transportados
   - Documentar contrato atualizado

### Fase 2 — Dashboard e Workers
1. **Vídeos recentes com ações**
   - Reusar handlers de videos.tsx (publicar, player, link YT)
   - Ownership validada pelo backend
   - Publicação idempotente

2. **Representação correta de workers**
   - Diferenciar: active, offline, stale, reiniciado, duplicado, processo lógico, máquina física
   - NÃO auto-deletar após 24h — diferenciar por heartbeat, identidade, comportamento
   - Corrigir causa raiz de duplicidade (restart com novo ID)
   - UI mostra: nome, tipo, responsabilidade, capabilities, status, última atividade, job atual
   - Worker stale aparece como stale (não como ativo)

### Fase 3 — Tela de Conteúdo
1. **Tab Gravações** — upload, lista, mapeamento, eventos, status
2. **Tab Identidade do Canal** — ChannelProfile (nome, descrição, estilo, tom, preferências)
3. **Preservação** — upload, drag-and-drop, mapeamento, profile salvando
4. **Estados** — loading, erro, refresh preserva, sem requests duplicadas
5. **Knowledge base** — não reaparece

### Fase 4 — Fila e Automação
1. **queue_mode** (manual/automatic) + **queue_target** (1-10)
   - Persistir em `Automation.config`
   - Backward-compatible (default: manual)
   - Migração de configs legadas

2. **Modo manual** — fila vazia = nenhum job automático
3. **Modo automático** — reconciliador mantém target
4. **Reconciliador idempotente** — `reconcile_idea_queue(user_id)`
   - Conta elegíveis, compara com target, seleciona candidatas, insere sem duplicar
   - Respeita concorrência, não ultrapassa limite, não mexe em reservados
   - Gatilhos: ativar automático, aumentar target, após consumo, após remoção, antes de gerar, periódico

5. **Seleção automática de candidatas**
   - Usar: ownership, visibility, uso por consumidor, relevância, qualidade, novidade,
     similaridade, histórico, saturação, identidade do canal, viabilidade
   - NÃO randomizar. NÃO enviar tudo para LLM sem pré-filtro

6. **Fila como programação explícita** — UI destaca como "próximos vídeos programados"
7. **Reordenação** — persiste, controla próximo job, DnD ou equivalente
8. **Preservação de manuais** — redução de target não remove itens manuais
9. **Remoção/rejeição** — ideia removida não volta imediatamente
10. **Concorrência** — dois reconciliadores não duplicam

### Fase 5 — Seleção editorial e ideia ↔ gameplay
1. **Associação semântica** — PRIMEIRO provar se metadados atuais são insuficientes:
   - event_type, description, tags, actions, interesting_score, visual_confidence
   - Se insuficientes, documentar limitação concreta ANTES de ativar embeddings
   - Preferir menor mudança coerente

2. **Uma GameplaySource por vídeo** — política centralizada
   - Todos SelectedClips da mesma source
   - Retry não escolhe outra source

3. **Disponibilidade consistente UI ↔ worker**
   - Mesma função de eligibility
   - Trecho inelegível permanece bloqueado mesmo com fit semântico alto

4. **Preferência manual** — usuário escolhe jogo, sistema escolhe melhor source daquele jogo
5. **Explicabilidade** — selection_reason em todos os clips

### Fase 6 — Homologação completa
1. Fluxo E2E: config → fila abastecida → UI mostra → reserva → job → worker → pipeline →
   gameplay → clips → render → sync → Video → ideia used (por consumidor) → fila reposta →
   Dashboard → assistir/publicar
2. Failure paths: worker morre em vários pontos, timeout, cancel, retry, callback duplicado,
   config muda durante job, ChannelProfile muda, reorder durante reserva, reconciliadores concorrentes
3. Testes negativos: A não vê vídeo de B, ideia privada não aparece, ideia pública não desaparece,
   fila vazia não gera, target não ultrapassado, retry não escolhe outra ideia/source
4. Compliance matrix final
