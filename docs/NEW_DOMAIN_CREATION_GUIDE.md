# GPCG — Guia de Criação de Novos Domínios

**Versão:** 1.0
**Data:** 2026-08-23
**Status:** Referência oficial para criação de novos domínios de conteúdo.

---

## Sumário

1. [O que é um domínio](#1-o-que-é-um-domínio)
2. [Arquitetura Control Plane / Compute Plane](#2-arquitetura-control-plane--compute-plane)
3. [Erros cometidos no Kids — NÃO REPITA](#3-erros-cometidos-no-kids--não-repita)
4. [Padrões obrigatórios](#4-padrões-obrigatórios)
5. [Checklist de implementação](#5-checklist-de-implementação)
6. [Estrutura de arquivos](#6-estrutura-de-arquivos)
7. [Modelo de dados](#7-modelo-de-dados)
8. [API](#8-api)
9. [Worker / Jobs](#9-worker--jobs)
10. [Frontend](#10-frontend)
11. [Automação](#11-automação)
12. [Testes](#12-testes)
13. [Deploy](#13-deploy)
14. [Validação pós-deploy](#14-validação-pós-deploy)

---

## 1. O que é um domínio

Um domínio é uma vertente de produção de conteúdo com seu próprio:

- **Sistema de descoberta de ideias** (como ideias surgem)
- **Sistema de scoring** (como ideias são avaliadas)
- **Sistema de fila** (como ideias são priorizadas para produção)
- **Pipeline de geração** (como vídeos são produzidos)
- **Recursos visuais** (gameplays, story assets, ou outro material)
- **Automação** (como o sistema decide o que produzir sozinho)

Domínios existentes:

| Domínio | Estado | Descoberta | Recursos visuais |
|---------|--------|------------|------------------|
| `games` | Completo | RSS + editorial | GameplaySource (gameplays mapeadas) |
| `kids` | Completo | AI ideation + topic library + seasonal | StoryAsset (biblioteca de mídias: imagens + vídeos) |
| `movies` | Frontend only | — | — |
| `conspiracy` | Não implementado | — | — |
| `technology` | Não implementado | — | — |

Cada usuário tem **um canal** com **um domínio ativo**. Trocar de domínio é destrutivo (reseta o ambiente de produção do canal). YouTube é conectado independentemente do domínio.

---

## 2. Arquitetura Control Plane / Compute Plane

```
VPS (Control Plane)                    Local PC (Compute Plane)
┌──────────────────────┐              ┌──────────────────────┐
│ • FastAPI API        │              │ • remote_worker.py    │
│ • React Frontend     │              │ • Ollama/LLM          │
│ • Database (SQLite)  │              │ • GPU (VLM, ASR)      │
│ • Job queue          │  ← poll →    │ • TTS                 │
│ • Worker registry    │              │ • FFmpeg              │
│ • Orchestration      │  ← sync →    │ • video-generate      │
│                      │              │ • Geração local       │
│ NUNCA roda LLM       │              │ NUNCA persiste dados  │
└──────────────────────┘              └──────────────────────┘
```

**Regra absoluta:** A VPS (Control Plane) **NUNCA** roda LLM, Ollama, GPU, ou qualquer processamento pesado. Todo trabalho que precisa de LLM/GPU é despachado como job para o remote worker processar localmente.

O worker pode estar offline. Jobs ficam na fila até um worker ficar disponível.

---

## 3. Erros cometidos no Kids — NÃO REPITA

### Erro 1: LLM síncrono na API da VPS

**O que aconteceu:** A primeira implementação do Kids chamava `KidsIdeaDiscovery.discover()`, `KidsSafetyFilter.review()`, e `KidsScorer.score()` diretamente nos handlers da API na VPS. Essas classes podiam chamar `get_llm()` dentro do processo da API.

**Por que é errado:** A VPS não tem Ollama. A arquitetura é Control Plane / Compute Plane. A VPS orquestra, o worker computa.

**Como evitar:** Toda operação que precisa de LLM deve ser despachada como job. A API cria o job (queued), retorna imediatamente, e o worker processa quando estiver disponível.

```python
# ❌ ERRADO
@router.post("/domain/ideas/discover")
def discover(user, db):
    discovery = DomainDiscovery()  # pode chamar get_llm()
    ideas = discovery.discover(...)  # síncrono na VPS
    return ideas

# ✅ CERTO
@router.post("/domain/ideas/discover")
def discover(user, db):
    job = Job(
        type="domain_idea_discovery",
        status="queued",
        stage="discovery",
        domain="domain",
        user_id=user.id,
        artifacts={...},
    )
    db.add(job)
    db.commit()
    return {"job_id": job.id, "status": "queued"}
```

### Erro 2: Discovery sem scoring no mesmo job

**O que aconteceu:** O discovery job só descobria ideias (status=discovered). O usuário tinha que clicar "Avaliar" em cada ideia individualmente para rodar safety + scoring.

**Por que é errado:** Nos Games, o `content_collect` job coleta E pontua KIs no mesmo job. O usuário nunca precisa clicar "avaliar" em cada ideia. O fluxo deve ser automático: descobrir → avaliar → colocar na fila.

**Como evitar:** O job de discovery deve fazer discovery + safety + scoring em uma única passagem. As ideias chegam na VPS já avaliadas (status=evaluated) e o auto-fill queue as pega automaticamente.

### Erro 3: Scorer com fórmula que infla todos os scores para o máximo

**O que aconteceu:** O `KidsScorer` usava média geométrica + bônus aditivo capped em 0.15. Com scores individuais 0.85-0.95 do LLM, o resultado era sempre 1.0 (capped). Todas as ideias apareciam com Score: 100.

**Como evitar:** Use média ponderada simples. Teste com scores reais do LLM antes de deployar. Verifique que scores diferentes produzem final_scores diferentes.

### Erro 4: Frontend serializando `undefined` como string

**O que aconteceu:** `new URLSearchParams({status: undefined}).toString()` produz `status=undefined` (a string literal). A API recebia `status=undefined` e retornava 0 ideias.

**Como evitar:** Sempre limpar parâmetros undefined antes de serializar:

```typescript
// ❌ ERRADO
const qs = new URLSearchParams(params).toString();

// ✅ CERTO
const clean: Record<string, string> = {};
if (params?.status) clean.status = params.status;
const qs = new URLSearchParams(clean).toString();
```

### Erro 5: Fila carregando IDs em vez de objetos

**O que aconteceu:** `GET /domain/idea-queue` retorna `{queue: [1,2,3], items: [{id:1,title:...},...]}`. O frontend fazia `setQueue(queueRes.queue)` (IDs) em vez de `setQueue(queueRes.items)` (objetos). Resultado: fila aparecia vazia, reorder mandava `null` nos IDs.

**Como evitar:** O frontend deve sempre usar `items` (objetos completos), nunca `queue` (lista de IDs).

### Erro 6: `queue_mode` com valores inconsistentes

**O que aconteceu:** O reconcile verificava `queue_mode == "automatic"` mas o frontend salvava `"auto"`. A fila nunca auto-preenchia.

**Como evitar:** Padronize os valores em todo o stack (backend, frontend, tests). Use um enum ou constantes compartilhadas. Se precisar aceitar múltiplos valores, documente explicitamente.

### Erro 7: Sem botão "Nova Ideia" manual

**O que aconteceu:** O Kids só tinha botão "Descobrir" (IA). Não tinha botão para criar ideia manualmente, igual ao Games tem.

**Como evitar:** Todo domínio deve ter ambos: descoberta automática (IA) E criação manual. O usuário sempre pode adicionar uma ideia à mão.

### Erro 8: Score display mostrando campo errado

**O que aconteceu:** O frontend mostrava `final_score` (0.0-1.0) em vez de `editorial_score` (0-100). Aparecia "Score: 1" em vez de "Score: 87".

**Como evitar:** O frontend deve sempre mostrar `editorial_score` (0-100), nunca `final_score` (0.0-1.0). `final_score` é interno para ordenação.

### Erro 9: Mídia vinculada ao tópico em vez de biblioteca do canal

**O que aconteceu:** `StoryAsset.topic_id` era `NOT NULL`. Cada mídia era
propriedade de um tópico. O usuário tinha que fazer upload de mídias dentro
de cada tópico. Não existia uma biblioteca reutilizável de mídias do canal.

**Por que é errado:** Nos Games, `GameplaySource` é uma **biblioteca do
canal** — o pipeline busca gameplays por jogo, não por ideia. O `GameplayRetriever`
faz seleção semântica: pega o `gameplay_query` do plano editorial e busca
clips que condizem com o script. No Kids, a mídia deveria seguir o mesmo
padrão: biblioteca do canal + seleção semântica pelo conteúdo do vídeo.

**Como evitar:** Mídia é do **canal**, não do tópico/ideia. O modelo de
assets deve ter `topic_id` **nullable** (opcional). O pipeline seleciona
mídias da biblioteca que condizem com o conteúdo (tags, descrição,
título do tópico, keywords do script). Ver `docs/KIDS_MEDIA_REFACTOR_PLAN.md`
para o padrão completo.

### Erro 10: Botão "Gerar" no card do tópico/ideia

**O que aconteceu:** O frontend Kids tinha um botão "Gerar" no card de cada
tópico. O usuário clicava e criava um job de geração manualmente.

**Por que é errado:** Nos Games, o usuário **nunca** clica "Gerar" numa
gameplay. A automação consome a fila de ideias, cria jobs, e o pipeline
seleciona gameplays automaticamente. O botão "Gerar" duplica o que a
`DomainAutomationStrategy.create_job()` já faz. O fluxo correto é:
fila de ideias → automação → job → pipeline → vídeo.

**Como evitar:** NUNCA colocar botão "Gerar" em cards de tópico/ideia/gameplay.
A geração é disparada pela **automação** (consume-queue), não manualmente.
O usuário gerencia a **fila** (adicionar, remover, reordenar ideias), não
a geração. Se quiser geração manual, deve ser um botão na fila ("produzir
próxima"), não no card do recurso.

### Erro 11: Upload de mídia sem drag-and-drop, sem progresso, sem pipeline de status

**O que aconteceu:** O upload de mídia Kids usava um `<input type="file">`
escondido num card de tópico. Sem drag-and-drop, sem barra de progresso,
sem upload de múltiplos arquivos, sem pipeline de status visual.

**Por que é errado:** Nos Games, `content.tsx` tem uma zona de drag-and-drop
com `useUploadStore` para tracking de uploads, progresso por arquivo, toast
de sucesso/erro, e `PROCESSING_STATUS_CONFIG` mapeando cada status → cor +
label + barra de progresso animada. O upload de mídia de qualquer domínio
deve replicar este padrão.

**Como evitar:** Replicar o padrão de `content.tsx` (Games):
- `useUploadStore` para tracking de uploads em andamento
- Zona de drag-and-drop com múltiplos arquivos
- Progresso por arquivo (0-100%)
- Toast de sucesso/erro
- `PROCESSING_STATUS_CONFIG` com cor + label para cada status
- Barra de progresso animada durante processamento
- Lista de mídias com cards mostrando status, dimensões, duração, thumbnail

### Erro 12: Sem seleção semântica de mídia durante geração

**O que aconteceu:** O pipeline de geração Kids não selecionava mídias por
relevância. A mídia era apenas um arquivo estático que aparecia no vídeo
sem critério. Não existia equivalente ao `GameplayRetriever`.

**Por que é errado:** Nos Games, o `GameplayRetriever` faz busca semântica:
pega o `gameplay_query` do `VideoCreativePlan`, busca eventos mapeados que
match o script, e seleciona clips que condizem com o que está sendo narrado.
Sem seleção semântica, o vídeo pode mostrar uma imagem de dinossauro enquanto
o script fala sobre sistema solar.

**Como evitar:** Todo domínio que usa mídia visual deve ter um retriever
(`DomainMediaRetriever`) que:
1. Recebe o plano editorial (ou título do tópico + script)
2. Extrai keywords/query do conteúdo
3. Busca mídias da biblioteca cujas tags/descrição/eventos match a query
4. Respeita clip usage (não repete segmentos de vídeo)
5. Faz fallback random quando não há matches semânticos
6. Retorna `SelectedMedia[]` com `selection_reason` para auditabilidade

### Erro 13: GenerationService sem branch por domínio

**O que aconteceu:** O `GenerationService._run_pipeline` não tinha branch
por domínio. O estágio `gameplay_selection` sempre usava `GameplayRetriever`,
mesmo para jobs Kids. O `RenderPlanBuilder` só sabia extrair clips de gameplay
(vídeo), não aplicar Ken Burns em imagens.

**Por que é errado:** Cada domínio tem seu próprio tipo de mídia visual
(gameplays, imagens, vídeos curtos, etc). O pipeline de geração precisa
branchar por domínio no estágio de seleção de mídia e no estágio de render
plan.

**Como evitar:** O `_run_pipeline` deve verificar `job.domain` e usar o
retriever e render plan builder corretos:
```python
if job_domain == "kids":
    clips = self.kids_media_retriever.retrieve(...)
    rp = self.kids_render_plan_builder.build(...)
else:
    clips = self.gameplay_retriever.retrieve(...)
    rp = self.plan_builder.build(...)
```

### Erro 14: Endpoint de geração manual redundante

**O que aconteceu:** `POST /api/kids/generate` criava um job de geração
manualmente, duplicando o que `KidsAutomationStrategy.create_job()` já faz.

**Por que é errado:** A automação já cria jobs a partir da fila de ideias.
Um endpoint de geração manual cria um caminho paralelo que não passa pela
fila, não respeita cooldown, e não tem traceabilidade editorial.

**Como evitar:** NUNCA criar endpoint de geração manual. A geração é
disparada pela automação (consume-queue). Se o usuário quer gerar um
vídeo específico, ele coloca a ideia na fila e a automação consome.

### Erro 15: Omissão do pipeline de mapeamento VLM+ASR (ERRO GRAVE)

**O que aconteceu:** A primeira implementação do pipeline de mídia Kids
processava vídeos apenas com FFprobe (metadados técnicos) + thumbnail
(FFmpeg) + tags manuais. **NÃO** rodava VLM (análise visual) nem ASR
(transcrição). **NÃO** criava eventos semânticos com timestamps. O
`KidsMediaRetriever` selecionava mídias apenas por tags manuais e
descrição, sem indexação semântica do conteúdo real do vídeo.

A justificativa na época foi: "vídeos Kids são curtos, não precisam de
mapeamento". **Isso estava errado.**

**Por que é errado:** Nos Games, o `GameplayAnalyzer` roda VLM + ASR em
cada vídeo para produzir `GameplayEvent[]` — eventos semânticos com
timestamps, descrição, tags, transcript, visual_confidence, e
interesting_score. O `GameplayRetriever` usa esses eventos para seleção
semântica de clips. Sem mapeamento, o Kids ficava sem:
- Indexação semântica do conteúdo visual
- Seleção de clips baseada no que aparece no vídeo
- Transcrição de áudio para matching
- Timestamps para seleção de segmentos específicos
- Provenance de quais eventos informaram cada clip

**O usuário disse explicitamente:**
> "EU PEDI PRA USAR A MESMA LOGICA DA PORRA DOS GAMES, ENTÃO ERA PRA TER
> O MAPEAMENTO, O NEGOCIO DE USO DE CENAS DUPLICADAS QUE EXISTE LA PRA
> CASO O USUARIO DESEJE OS VIDEOS NAO GERAREM VIDEOS REPETIDOS COM AS
> MESMAS CENAS"

**Como evitar:** TODO domínio que aceita vídeo como mídia DEVE usar o
mesmo pipeline de mapeamento que Games:
1. Upload → `kids_asset_process` job (ou equivalente do domínio)
2. Worker baixa o vídeo
3. Worker roda FFprobe (metadados técnicos)
4. Worker roda thumbnail (FFmpeg)
5. **Worker roda VLM + ASR (mapeamento semântico)** ← NÃO PULAR
6. Worker cria eventos semânticos com timestamps (`KidsMediaEvent` ou
   equivalente)
7. Worker sincroniza eventos para a VPS
8. Retriever usa eventos para seleção semântica de clips
9. Clip usage previne reuso de segmentos

**Regra absoluta:** Um domínio NUNCA deve omitir mapeamento, indexação,
usage tracking, public/private semantics, ou provenance merely porque
sua mídia é "esperada ser mais curta ou simples". A capability matrix
do domínio deve ser comparada contra Games ANTES de implementar. Se
Games tem mapeamento, o novo domínio tem mapeamento.

**Implementação correta:** Ver `src/gpcg/application/kids_media_analyzer.py`
(`KidsMediaAnalyzer` reutiliza `GameplayAnalyzer`) e
`src/gpcg/domains/kids/models.py` (`KidsMediaEvent` — equivalente a
`GameplayEvent`).

---

## 4. Padrões obrigatórios

### 4.1 Nomenclatura

- **Tabelas:** `domain_ideas`, `domain_topics`, `domain_assets` (prefixo do domínio)
- **Job types:** `domain_idea_discovery`, `domain_idea_score` (prefixo do domínio)
- **Queue keys:** `domain_idea_queue` em `automation.config` (prefixo do domínio)
- **Queue config:** `domain_queue_mode`, `domain_auto_fill_queue`, `domain_max_queue_size`
- **Rotas API:** `/api/domain/ideas`, `/api/domain/idea-queue`
- **Arquivos:** `src/gpcg/domains/domain/` (diretório do domínio)

### 4.2 Control Plane / Compute Plane

- **VPS (API):** Cria jobs, persiste dados, serve frontend. NUNCA chama LLM.
- **Worker:** Processa jobs localmente com LLM/Ollama. Sincroniza resultados estruturados de volta.
- **Jobs são persistentes:** Se o worker está offline, jobs ficam na fila.
- **Múltiplos workers:** Podem consumir a mesma fila. Claim atômico.

### 4.3 Padrão de sync (worker → VPS)

Seguir exatamente o padrão de `game_enrich` e `content_collect`:

```
1. Worker claima job
2. Worker busca dados necessários da VPS (GET /api/jobs/{id}/data ou endpoint worker-auth)
3. Worker processa localmente (LLM, GPU, etc)
4. Worker sincroniza resultados estruturados via POST /api/jobs/{id}/sync-domain-*
5. Worker submete resultado final via POST /api/jobs/{id}/result
```

### 4.4 Frontend

- **Polling de jobs:** O frontend faz polling de `GET /api/jobs/{id}` a cada 3 segundos quando há jobs ativos.
- **Toasts:** Success e error em todas as operações.
- **Estados:** `discovering`/`scoring` com spinner, `queued` com indicador de fila.
- **Botões:** "Nova Ideia" (manual) + "Descobrir" (IA) sempre presentes.
- **Fila:** Drag-to-reorder, mostrar `items` (objetos), não `queue` (IDs).
- **Scores:** Mostrar `editorial_score` (0-100), nunca `final_score` (0.0-1.0).
- **URLSearchParams:** Sempre limpar undefined/null antes de serializar.

### 4.5 Testes

- **LLM sempre mockado** em testes (usar `MagicMock` para `LLMClient`).
- **Testar:** models, lifecycle, dedup, safety, scorer, service, API routes, worker dispatch, sync endpoints, ownership.
- **Rodar suite completa:** `.venv/bin/pytest tests/ -q` — deve passar sem regressões.
- **Contar tests:** Atualizar `AGENTS.md` com o número correto de tests.

### 4.6 Deploy

- **Deploy script:** `./scripts/deploy.sh` (roda tests, rsync, docker build, nginx reload).
- **Working tree limpa:** Commit antes de deployar.
- **Version bump:** Automático pelo script.
- **Health check:** Verificar `https://brunointegrations.com/gpcg/api/health` após deploy.
- **Worker restart:** `systemctl --user restart gpcg-worker` após deploy de mudanças no worker.

---

## 5. Checklist de implementação

### Backend

- [ ] `src/gpcg/domains/domain/` — diretório do domínio
- [ ] `src/gpcg/domains/domain/models.py` — DomainIdea, DomainTopic, DomainAsset
- [ ] `src/gpcg/domains/domain/idea_service.py` — CRUD, dedup, lifecycle, queue reconcile
- [ ] `src/gpcg/domains/domain/discovery.py` — descoberta (AI, library, seasonal, RSS, etc)
- [ ] `src/gpcg/domains/domain/safety_filter.py` — safety review (se aplicável)
- [ ] `src/gpcg/domains/domain/scorer.py` — scoring editorial
- [ ] `src/gpcg/domains/domain/prompts.py` — prompts LLM
- [ ] `src/gpcg/domains/domain/pipeline.py` — pipeline de geração
- [ ] `src/gpcg/core/models.py` — adicionar `JobType.domain_idea_discovery`, `JobType.domain_idea_score`
- [ ] `src/gpcg/core/models.py` — adicionar `ContentDomain.domain` no enum
- [ ] `src/gpcg/infrastructure/database.py` — schema evolution (create_all)
- [ ] `src/gpcg/api/domain_idea_routes.py` — todos os endpoints
- [ ] `src/gpcg/api/worker_routes.py` — sync endpoints (`sync-domain-ideas`, `sync-domain-score`)
- [ ] `src/gpcg/api/worker_routes.py` — worker-auth endpoints para buscar dados (`/workers/domain-ideas/{id}`, `/workers/channel-profile/{user_id}`)
- [ ] `src/gpcg/api/routes.py` — `GET /jobs/{id}` para polling (já existe)
- [ ] `src/gpcg/api/app.py` — registrar novos routers
- [ ] `src/gpcg/domains/automation_strategies.py` — `DomainAutomationStrategy`
- [ ] `src/gpcg/api/automation_routes.py` — domain dispatch em `check_automation` e `create_job_from_automation`
- [ ] `src/gpcg/application/worker.py` — adicionar `domain_idea_*` em `remote_only_types`
- [ ] `src/gpcg/application/domain_reset_service.py` — reset do domínio

### Remote Worker

- [ ] `src/gpcg/worker/remote_worker.py` — dispatch para `domain_idea_discovery` e `domain_idea_score`
- [ ] `_process_domain_idea_discovery_job` — discovery + safety + scoring em uma passagem
- [ ] `_process_domain_idea_score_job` — scoring individual (se necessário)
- [ ] Usar `LLMClient` local explícito: `DomainDiscovery(llm=llm)`, `DomainSafetyFilter(llm=llm)`, `DomainScorer(llm=llm)`
- [ ] Sync via `POST /api/jobs/{id}/sync-domain-ideas` e `POST /api/jobs/{id}/sync-domain-score`
- [ ] Result final via `POST /api/jobs/{id}/result`

### Frontend

- [ ] `frontend/src/lib/api.ts` — todos os métodos API
- [ ] `frontend/src/pages/domain-ideas.tsx` — página de ideias
- [ ] Botão "+ Nova Ideia" (manual) + formulário
- [ ] Botão "Descobrir" (IA) + painel de configuração
- [ ] Lista de ideias com filtros (status, categoria)
- [ ] Fila de produção com drag-to-reorder
- [ ] Polling de job status a cada 3s
- [ ] Toasts de success/error
- [ ] Scores mostrando `editorial_score` (0-100)
- [ ] URLSearchParams limpo (sem undefined)
- [ ] `frontend/src/App.tsx` — rota para a página
- [ ] `frontend/src/components/Navbar.tsx` — link na navbar

### Testes

- [ ] `tests/test_domain_idea_system.py` — todos os testes do domínio
- [ ] LLM mockado em todos os testes
- [ ] Testar: models, lifecycle, dedup, safety, scorer, service, API, worker dispatch, sync, ownership
- [ ] Suite completa passa sem regressões
- [ ] Atualizar `AGENTS.md` com contagem de tests

### Deploy

- [ ] Frontend typecheck passa
- [ ] Frontend build passa
- [ ] Suite completa passa
- [ ] Commit
- [ ] `./scripts/deploy.sh`
- [ ] Health check OK
- [ ] `systemctl --user restart gpcg-worker`
- [ ] Worker online (journalctl)
- [ ] Testar criação de ideia manual
- [ ] Testar discovery (job queued → worker processa → ideias avaliadas → fila auto-preenchida)

---

## 6. Estrutura de arquivos

```
src/gpcg/domains/domain/
├── __init__.py
├── models.py              # DomainIdea, DomainTopic, DomainAsset + enums
├── idea_service.py        # CRUD, dedup, lifecycle, queue reconcile
├── discovery.py           # Descoberta (AI, library, seasonal, RSS, etc)
├── safety_filter.py       # Safety review (se aplicável)
├── scorer.py              # Scoring editorial
├── prompts.py             # Prompts LLM
├── pipeline.py            # Pipeline de geração
└── topic_library.py       # Biblioteca de tópicos (opcional)

src/gpcg/api/
├── domain_idea_routes.py  # Endpoints de ideias
├── domain_routes.py       # Endpoints de topics/assets/generate
├── worker_routes.py       # Sync endpoints + worker-auth endpoints
├── automation_routes.py   # Domain dispatch
└── app.py                 # Registrar routers

src/gpcg/worker/
└── remote_worker.py       # Dispatch + _process_domain_*_job

src/gpcg/domains/
└── automation_strategies.py  # DomainAutomationStrategy

frontend/src/
├── pages/domain-ideas.tsx    # Página de ideias
├── pages/domain.tsx          # Página de topics/assets (se aplicável)
├── lib/api.ts                # Métodos API
└── App.tsx                   # Rotas

tests/
└── test_domain_idea_system.py  # Todos os testes
```

---

## 7. Modelo de dados

### 7.1 DomainIdea

```python
class DomainIdeaStatus(str, enum.Enum):
    discovered = "discovered"    # recém-descoberta, aguardando scoring
    evaluated = "evaluated"      # safety + scoring aplicados, pronta para fila
    queued = "queued"            # na fila de produção
    converted = "converted"      # transformada em DomainTopic
    rejected = "rejected"        # rejeitada (safety ou manual)
    expired = "expired"          # expirada por idade

class DomainIdea(Base):
    __tablename__ = "domain_ideas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)

    # Conteúdo
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(50), default="general", index=True)

    # Origem
    source: Mapped[str] = mapped_column(String(30), default="ai_ideation")
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)

    # Scoring (editorial_score = 0-100 para display, final_score = 0.0-1.0 para ordenação)
    editorial_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    safety_score: Mapped[float] = mapped_column(Float, default=1.0)
    final_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)

    # Safety
    safety_flags: Mapped[list] = mapped_column(JSON, default=list)
    safety_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)

    # Lifecycle
    status: Mapped[str] = mapped_column(String(20), default=DomainIdeaStatus.discovered.value, index=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Deduplicação
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    # Relação com topic
    topic_id: Mapped[Optional[int]] = mapped_column(ForeignKey("domain_topics.id"), nullable=True, index=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
```

### 7.2 Queue em Automation.config

```python
# automation.config (JSON):
{
    "idea_queue": [...],              # Games (KI IDs) — existente
    "domain_idea_queue": [...],       # Domain (DomainIdea IDs) — novo
    "domain_queue_mode": "automatic", # "automatic" | "manual"
    "domain_auto_fill_queue": true,
    "domain_max_queue_size": 10
}
```

### 7.3 Job

```python
# JobType enum:
domain_idea_discovery = "domain_idea_discovery"
domain_idea_score = "domain_idea_score"

# Job creation:
job = Job(
    type=JobType.domain_idea_discovery.value,
    status=JobStatus.queued.value,
    stage="discovery",
    domain=ContentDomain.domain.value,
    priority="normal",
    user_id=user.id,
    artifacts={
        "categories": [...],
        "ideas_per_category": N,
        # outras opções
    },
)
```

---

## 8. API

### 8.1 Endpoints de ideias (user-auth)

```
GET    /api/domain/ideas              # Listar (filtros: status, category, limit)
GET    /api/domain/ideas/{id}         # Detalhe
POST   /api/domain/ideas              # Criar manual
POST   /api/domain/ideas/{id}/reject  # Rejeitar
POST   /api/domain/ideas/{id}/produce # Idea → Topic → Job (one-step)
GET    /api/domain/ideas/{id}/provenance  # Provenance (idea → topic → jobs → videos)
GET    /api/domain/ideas/stats        # Estatísticas
```

### 8.2 Endpoints de descoberta (user-auth, cria job)

```
POST   /api/domain/ideas/discover     # Cria job de discovery (NÃO processa síncrono)
POST   /api/domain/ideas/{id}/score   # Cria job de scoring individual (NÃO processa síncrono)
```

### 8.3 Endpoints de fila (user-auth)

```
GET    /api/domain/idea-queue         # Listar fila (triggers clean + reconcile)
POST   /api/domain/idea-queue/add     # Adicionar à fila
POST   /api/domain/idea-queue/remove  # Remover da fila
POST   /api/domain/idea-queue/reorder # Reordenar fila
POST   /api/domain/idea-queue/reconcile  # Manual reconcile
```

### 8.4 Endpoints de sync (worker-auth)

```
POST   /api/jobs/{id}/sync-domain-ideas   # Worker sincroniza ideias descobertas
POST   /api/jobs/{id}/sync-domain-score   # Worker sincroniza scoring individual
GET    /api/workers/channel-profile/{user_id}  # Worker busca profile
GET    /api/workers/domain-ideas/{idea_id}     # Worker busca ideia
```

### 8.5 Job polling (user-auth)

```
GET    /api/jobs/{id}                  # Status do job (para frontend polling)
```

### 8.6 Response pattern

```json
// POST /domain/ideas/discover
{
  "job_id": 123,
  "status": "queued",
  "message": "Discovery job queued. Will be processed when a worker is available."
}

// GET /domain/idea-queue
{
  "queue": [1, 2, 3],
  "items": [
    {"id": 1, "title": "...", "editorial_score": 87.5, "status": "queued", ...},
    ...
  ]
}
```

---

## 9. Worker / Jobs

### 9.1 Dispatch

```python
# src/gpcg/worker/remote_worker.py — _process_job
elif job_type == "domain_idea_discovery":
    self._process_domain_idea_discovery_job(job)
elif job_type == "domain_idea_score":
    self._process_domain_idea_score_job(job)
```

### 9.2 Discovery job (faz discovery + safety + scoring em uma passagem)

```python
def _process_domain_idea_discovery_job(self, job):
    job_id = job["id"]
    artifacts = job.get("artifacts", {})

    # 1. Buscar profile da VPS
    profile = self._get_domain_profile(job["user_id"])

    # 2. Inicializar LLM local
    from gpcg.infrastructure.llm import LLMClient
    llm = LLMClient()

    # 3. Discovery local
    discovery = DomainDiscovery(llm=llm)
    ideas = discovery.discover(...)

    # 4. Safety + Scoring local (MESMO JOB, mesma passagem)
    safety_filter = DomainSafetyFilter(llm=llm)
    scorer = DomainScorer(llm=llm)

    evaluated_ideas = []
    for idea_data in ideas:
        safety = safety_filter.review(...)
        if not safety.safe:
            continue  # rejeitada

        score = scorer.score(...)
        idea_data["editorial_score"] = score.editorial_score_0_100
        idea_data["final_score"] = score.final_score
        idea_data["evaluated"] = True
        # ... outros campos
        evaluated_ideas.append(idea_data)

    # 5. Sync para VPS
    self.client.post(f"/api/jobs/{job_id}/sync-domain-ideas", json={"ideas": evaluated_ideas})

    # 6. Result final
    self.submit_job_result(job_id, status="completed", artifacts={...})
```

### 9.3 VPS worker (legacy) deve pular jobs do domínio

```python
# src/gpcg/application/worker.py
remote_only_types = {
    "mapping",
    "generate_short",
    "curiosity_short",
    "knowledge_index",
    "game_enrich",
    "content_collect",
    "kids_idea_discovery",   # ← adicionar
    "kids_idea_score",       # ← adicionar
    "domain_idea_discovery", # ← adicionar
    "domain_idea_score",     # ← adicionar
}
```

---

## 10. Frontend

### 10.1 Página de ideias

A página deve ter (igual ao Games e Kids):

- **Header** com botões "+ Nova Ideia" (manual) e "Descobrir" (IA)
- **Formulário de criação manual** (title, description, category)
- **Painel de descoberta** (categories, count, options)
- **Stats** (total, na fila, avaliadas, descobertas)
- **Fila de produção** com drag-to-reorder
- **Filtros** (status)
- **Lista de ideias** com badges (status, source, category, score, safety)
- **Botões por ideia:** Avaliar (se discovered), + Fila (se evaluated), Remover da fila (se queued), Produzir, Rejeitar
- **Polling** de job status a cada 3s quando há jobs ativos
- **Toasts** de success/error

### 10.2 API client

```typescript
// frontend/src/lib/api.ts
listDomainIdeas: (params?: { status?: string; category?: string; limit?: number }) => {
  const clean: Record<string, string> = {};
  if (params?.status) clean.status = params.status;
  if (params?.category) clean.category = params.category;
  if (params?.limit) clean.limit = String(params.limit);
  const qs = new URLSearchParams(clean).toString();
  return request<{ ideas: any[]; total: number }>(`/domain/ideas${qs ? `?${qs}` : ""}`);
},
createDomainIdea: (data: { title: string; description?: string; category?: string }) =>
  request<any>("/domain/ideas", { method: "POST", body: JSON.stringify(data) }),
discoverDomainIdeas: (data: { categories?: string[]; ideas_per_category?: number }) =>
  request<any>("/domain/ideas/discover", { method: "POST", body: JSON.stringify(data) }),
getDomainIdeaQueue: () => request<{ queue: any[]; items: any[] }>("/domain/idea-queue"),
// ... etc
```

### 10.3 Score display

```tsx
// ✅ CERTO: editorial_score (0-100)
{idea.editorial_score !== null && idea.editorial_score !== undefined && (
  <span className={`text-sm font-bold ${scoreColor(idea.editorial_score)}`}>
    Score: {idea.editorial_score.toFixed(0)}
  </span>
)}

// ❌ ERRADO: final_score (0.0-1.0)
{idea.final_score !== null && idea.final_score !== undefined && (
  <span>Score: {idea.final_score.toFixed(0)}</span>  // mostra "1"
)}
```

### 10.4 Queue display

```tsx
// ✅ CERTO: items (objetos completos)
setQueue(queueRes.items || []);

// ❌ ERRADO: queue (lista de IDs)
setQueue(queueRes.queue || []);
```

---

## 11. Automação

### 11.1 Strategy pattern

```python
# src/gpcg/domains/automation_strategies.py
class DomainAutomationStrategy:
    @staticmethod
    def check(auto, db) -> Optional[dict]:
        # Condições: user ativo, YouTube conectado, recursos visuais prontos,
        # sem job ativo, fila tem ideias (após reconcile)
        ...

    @staticmethod
    def create_job(user_id) -> Optional[int]:
        # 1. Pegar primeira ideia da fila
        # 2. Converter idea → topic
        # 3. Criar generate_short job com domain="domain"
        # 4. Remover idea da fila
        ...
```

### 11.2 Domain dispatch

```python
# src/gpcg/api/automation_routes.py — check_automation
for auto in autos:
    domain = get_user_domain(db, auto.user_id)
    if domain == "domain":
        pending = DomainAutomationStrategy.check(auto, db)
        if pending:
            pending_list.append(pending)
        continue
    # ... Games path

# create_job_from_automation
if domain == "domain":
    return DomainAutomationStrategy.create_job(user_id)
```

### 11.3 Worker automation check

```python
# src/gpcg/worker/remote_worker.py — _check_automations
for item in pending:
    if item.get("domain_idea_queue"):
        self._consume_domain_idea_queue(user_id)
    elif item.get("domain") == "domain":
        # Domain-specific automation
        ...
```

---

## 12. Testes

### 12.1 Estrutura

```python
# tests/test_domain_idea_system.py

class TestDomainIdeaModel:
    # CRUD, defaults, relationships
    ...

class TestDomainIdeaLifecycle:
    # discovered → evaluated → queued → converted
    # rejected, expired
    # can_transition, is_terminal
    ...

class TestDomainDeduplication:
    # content_hash, similarity, is_duplicate_topic
    ...

class TestDomainSafetyFilter:
    # hard rules, LLM review, fallback
    ...

class TestDomainScorer:
    # dimensions, final_score, fallback
    # test_compute_final_high_scores
    # test_compute_final_zero_dimension
    # test_compute_final_clamped
    ...

class TestDomainIdeaService:
    # create_idea, convert_to_topic, reconcile_queue, clean_queue
    ...

class TestDomainDiscovery:
    # AI ideation, topic library, seasonal, LLM failure
    ...

class TestDomainIdeaAPI:
    # all endpoints, domain guard, ownership
    ...

class TestDomainWorkerSync:
    # sync-domain-ideas, sync-domain-score, worker auth
    ...

class TestDomainAutomation:
    # strategy check, create_job, domain dispatch
    ...
```

### 12.2 LLM mockado

```python
mock_llm = MagicMock()
mock_llm.chat_json.return_value = {
    "editorial_quality": 85,
    "age_fit": 90,
    ...
}
scorer = DomainScorer(llm=mock_llm)
result = scorer.score(title="Test", age_range="3-6")
assert result.editorial_score_0_100 > 0
```

### 12.3 Suite completa

```bash
.venv/bin/pytest tests/ -q
# Deve passar sem regressões
# Atualizar AGENTS.md com o número correto de tests
```

---

## 13. Deploy

```bash
# 1. Frontend
cd frontend && npm run typecheck && npm run build

# 2. Tests
.venv/bin/pytest tests/ -q

# 3. Commit
git add -A
git commit -m "feat(domain): add Domain idea system with queue + automation"

# 4. Deploy
./scripts/deploy.sh

# 5. Worker restart (se mudanças no worker)
systemctl --user restart gpcg-worker

# 6. Health check
curl -s https://brunointegrations.com/gpcg/api/health
```

---

## 14. Validação pós-deploy

### 14.1 Manual

1. Abrir UI na página do domínio
2. Clicar "+ Nova Ideia" → criar ideia manual → deve aparecer na lista
3. Clicar "Descobrir" → job na fila → worker processa → ideias avaliadas aparecem
4. Fila deve auto-preencher com as melhores ideias (se queue_mode=automatic)
5. Scores devem variar (não todos iguais)
6. Reorder da fila deve funcionar (sem erro 422)
7. "Produzir" deve criar job de generation

### 14.2 Worker offline

1. Parar o worker: `systemctl --user stop gpcg-worker`
2. Clicar "Descobrir" → job fica `queued`
3. Verificar: `GET /api/jobs/{id}` retorna `status: "queued"`
4. Iniciar worker: `systemctl --user start gpcg-worker`
5. Worker claima o job e processa
6. Job completa, ideias aparecem

### 14.3 Provenance

1. Produzir uma ideia
2. `GET /api/domain/ideas/{id}/provenance` deve retornar: idea → topic → jobs → videos

### 14.4 Games regression

1. Trocar para domínio Games
2. Verificar: fila de KIs, automação, geração — tudo funciona como antes

---

## Referências

- `docs/ARCHITECTURAL_MANIFESTO.md` — filosofia editorial
- `docs/ARCHITECTURE_V2.md` — arquitetura completa
- `docs/KIDS_IDEA_SYSTEM_PROPOSAL.md` — proposta do Kids (referência de implementação)
- `docs/KIDS_IDEA_SYSTEM_PROGRESS.md` — progresso do Kids (fases e decisões)
- `AGENTS.md` — padrões do projeto, comandos, arquitetura
