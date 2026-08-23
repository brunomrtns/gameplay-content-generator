# GPCG KIDS — Sistema de Descoberta de Ideias e Automação Editorial

**Documento técnico de proposta — Fase de análise (sem implementação)**

---

## 1. ARQUITETURA ATUAL

### 1.1 Games — Sistema completo de ideias

O Games possui um sistema editorial V2 completo, organizado em camadas:

```
ChannelProfile (Editorial Profile persistido)
        ↓
EditorialIntentBuilder (computa Intent por ciclo)
        ↓
EditorialBriefBuilder (traduz Intent → Brief executável)
        ↓
GoalOrientedCollector (executa coleta: RSS + search queries)
        ↓
KnowledgeItem (persistido com editorial_score, lifecycle, embeddings)
        ↓
CompositeScorer (3 camadas: Quality × Fit × Timing)
        ↓
_reconcile_idea_queue (auto-preenche fila com KIs fresh)
        ↓
Idea Queue (em automation.config.idea_queue)
        ↓
check_automation (worker poll) → consume-queue ou editorial decision
        ↓
Job (generate_short / curiosity_short)
```

**Componentes do Games e seus acoplamentos:**

| Componente | Arquivo | Acoplamento Games |
|-----------|---------|-------------------|
| `EditorialIntent` | `domain/editorial_types.py:86` | `priority_games: list[GameTarget]`, `cooldown_games: dict[int,int]` |
| `EditorialBrief` | `domain/editorial_types.py:111` | `target_games`, `cooldown_games`, `search_queries` (game+keyword) |
| `GameTarget` | `domain/editorial_types.py:27` | `game_id`, `clips_ready` — conceito exclusivo de Games |
| `SearchQuery` | `domain/editorial_types.py:49` | `game_id`, `template_name` — game+keyword expansion |
| `FeedSpec` | `domain/editorial_types.py:66` | `item_type` (news/curiosity/lore/fact) — genérico |
| `CompositeScore` | `domain/editorial_types.py:163` | **Genérico** — 3 camadas multiplicativas |
| `EditorialBriefBuilder` | `application/editorial_brief_builder.py:56` | `_global_feeds()` importa `GENERAL_GAMING_FEEDS`, `_expand_queries()` usa `game.name + keyword` |
| `EditorialIntentBuilder` | `application/editorial_intent_builder.py:57` | Queries `GameplaySource` + `GameplayAsset` + `Game` para priorizar jogos |
| `GoalOrientedCollector` | `application/goal_oriented_collector.py:44` | RSS feeds + search queries baseadas em jogos |
| `CompositeScorer` | `application/composite_scorer.py:76` | `_gameplay_availability()` checa `GameplayAsset`/`GameplaySource`, `_diversity_penalty()` usa `cooldown_games` |
| `EditorialStrategyService` | `application/editorial_strategy.py:127` | `GameInventory` com `gameplay_sources_ready`, `facts_available`, etc. |
| `LifecycleManager` | `application/lifecycle_manager.py:126` | **Genérico** — decay por `item_type` (news/curiosity/lore/fact) |
| `KnowledgeItem` | `core/models.py:776` | `game_id` FK para `games.id` (nullable), `franchise`, `developer` |
| `KnowledgeItemUsage` | `core/models.py:881` | **Genérico** — per-consumer tracking |
| `reconcile_user_queue` | `api/automation_routes.py:556` | Filtra KIs fresh visíveis ao usuário — **genérico** |
| `check_automation` | `api/automation_routes.py:309` | **Bloqueia Kids** — checa `GameplaySource` ready_count |
| `create_job_from_automation` | `api/automation_routes.py:1163` | **Bloqueia Kids** — checa `GameplaySource` ready_count |
| `create_job_from_decision` | `api/automation_routes.py:1044` | Cria job via `GenerationService.create_job(game_id)` — Games-only |
| `_process_content_collect_job` | `application/worker.py:359` | `_collect_with_editorial_brief` filtra usuários com `GameplaySource` |

### 1.2 Kids — Pipeline de geração sem descoberta

O Kids tem apenas o pipeline de produção:

```
KidsTopic (criado manualmente pelo usuário)
        ↓
StoryAsset (upload manual de imagens)
        ↓
POST /kids/generate { topic_id }
        ↓
Job (domain=kids, type=generate_short)
        ↓
KidsGenerationService._run_pipeline():
    content_planning → script → tts → visual_selection →
    music_selection → render_plan → render → output
```

**O que existe no Kids:**

| Componente | Arquivo | Estado |
|-----------|---------|--------|
| `KidsTopic` | `domains/kids/models.py:43` | Implementado — title, category, age_range, description |
| `StoryAsset` | `domains/kids/models.py:70` | Implementado — filename, storage_key, processing_status |
| `KidsGenerationService` | `domains/kids/pipeline.py:66` | Implementado — pipeline completo de geração |
| `kids_routes.py` | `api/kids_routes.py` | Implementado — CRUD de topics, upload de assets, generate |
| `_require_kids_domain` | `api/kids_routes.py:61` | Guard — exige domain=kids |
| Domain reset | `application/domain_reset_service.py:437` | Implementado — limpa topics + assets |

**O que NÃO existe no Kids:**

- Descoberta automática de ideias
- Coletor de conteúdo (RSS, AI ideation, research)
- KnowledgeItems para Kids (ou equivalente)
- Scoring editorial para Kids
- Safety / age suitability filter
- Idea queue para Kids
- Automação (check_automation bloqueia Kids)
- Decisão editorial via LLM para Kids
- Transição Idea → KidsTopic
- Deduplicação de ideias
- Lifecycle de ideias

### 1.3 O bloqueio da automação

Dois pontos no código bloqueiam Kids:

**Ponto 1 — `check_automation` (linha 345-350):**
```python
ready_count = db.query(GameplaySource).filter(
    GameplaySource.user_id == auto.user_id,
    GameplaySource.ingestion_status == IngestionStatus.ready.value,
).count()
if ready_count == 0:
    continue  # Kids nunca tem GameplaySource → sempre skip
```

**Ponto 2 — `create_job_from_automation` (linha 1191-1196):**
```python
ready_count = session.query(GameplaySource).filter(
    GameplaySource.user_id == user_id,
    GameplaySource.ingestion_status == IngestionStatus.ready.value,
).count()
if ready_count == 0:
    return None  # Kids nunca passa daqui
```

**Ponto 3 — `_collect_with_editorial_brief` (worker.py:466-470):**
```python
user_ids = session.execute(
    sa_select(distinct(GameplaySource.user_id)).where(
        GameplaySource.user_id.isnot(None)
    )
).scalars().all()
# Kids users nunca aparecem aqui → content_collect nunca roda para Kids
```

---

## 2. GAPS PARA KIDS

### Gap 1: Descoberta de ideias
Não existe nenhum mecanismo de descoberta. O usuário precisa pensar no tema e criar manualmente.

### Gap 2: Safety / Age Suitability
Não existe filtro de adequação infantil. Fundamental para o domínio Kids.

### Gap 3: Scoring editorial específico
O `CompositeScorer` é Games-specific (`_gameplay_availability` checa GameplayAsset). Kids precisa de um Fit que considere age_fit, educational_value, visual_potential.

### Gap 4: Idea Queue
Não existe fila de ideias. A idea_queue atual é uma lista de `ki_id` em `automation.config` — acoplada a KnowledgeItem.

### Gap 5: Automação domain-aware
`check_automation` e `create_job_from_automation` checam `GameplaySource`. Precisam de dispatch por domínio.

### Gap 6: Transição Idea → KidsTopic
Não existe fluxo de "ideia selecionada → KidsTopic criado automaticamente".

### Gap 7: Deduplicação
Não existe verificação de similaridade entre ideias novas e KidsTopics já produzidos.

### Gap 8: Lifecycle de ideias
KnowledgeItem tem lifecycle (fresh/used/rejected + freshness_score). Kids não tem nada equivalente.

---

## 3. ARQUITETURA PROPOSTA

### 3.1 Princípios

1. **Não copiar lógica de Games para Kids** — descoberta de Kids é fundamentalmente diferente (RSS de games vs AI ideation + topic library)
2. **Não alterar comportamento do Games** — nenhuma regressão
3. **Reutilizar infraestrutura genérica** — CompositeScore (3 camadas), lifecycle, deduplication hash, idea queue storage pattern
4. **Domain dispatch para automação** — strategy pattern em vez de if/else espalhado
5. **Preparar para futuros domínios** — fronteiras claras, não hardcode Games+Kids

### 3.2 Fluxo proposto para Kids

```
ChannelProfile (domain=kids, age_range, categories)
        ↓
KidsIdeaDiscovery (AI ideation + topic library + seasonal)
        ↓
KidsIdea (nova entidade — candidata editorial)
        ↓
Safety Filter (LLM: age suitability, sensitive content)
        ↓
KidsScoring (Quality × Fit × Timing — adaptado)
        ↓
Kids Idea Queue (em automation.config.kids_idea_queue)
        ↓
check_automation (domain-aware dispatch)
        ↓
Seleção (manual ou automática)
        ↓
KidsTopic (criado a partir da ideia selecionada)
        ↓
[upload de assets]
        ↓
KidsGenerationService (pipeline existente — sem mudanças)
```

### 3.3 Diagrama de responsabilidades

```
                    ┌─────────────────────────┐
                    │   ChannelProfile         │
                    │   (domain, age_range,    │
                    │    categories, tone)     │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   DomainAutomation       │
                    │   Strategy Registry      │
                    └──┬──────────────────┬───┘
                       │                  │
              ┌────────▼──────┐  ┌───────▼────────┐
              │ GamesStrategy │  │ KidsStrategy   │
              │ (existente)   │  │ (novo)         │
              │               │  │                │
              │ • KI discovery│  │ • AI ideation  │
              │ • RSS collect │  │ • Topic library│
              │ • CompositeSc.│  │ • Safety filter│
              │ • KI queue    │  │ • Kids scoring │
              │ • Editorial   │  │ • Kids queue   │
              │   decision    │  │ • Idea→Topic   │
              └───────────────┘  └────────────────┘
                       │                  │
                       └────────┬─────────┘
                                │
                    ┌───────────▼───────────┐
                    │   Job (domain-aware)   │
                    └───────────┬───────────┘
                                │
                    ┌───────────▼───────────┐
                    │ GenerationService     │
                    │ (Games)               │
                    │   OR                  │
                    │ KidsGenerationService │
                    │ (Kids)                │
                    └───────────────────────┘
```

---

## 4. MODELO DE DADOS

### 4.1 Nova entidade: `KidsIdea`

```python
class KidsIdeaStatus(str, enum.Enum):
    """Lifecycle de uma ideia Kids."""
    discovered = "discovered"    # recém-coletada, aguardando scoring
    fresh = "fresh"              # scored e disponível para curadoria
    queued = "queued"            # adicionada à fila pelo usuário ou auto-fill
    converted = "converted"      # transformada em KidsTopic
    rejected = "rejected"        # rejeitada pelo usuário ou safety filter
    expired = "expired"          # arquivada por idade sem uso

class KidsIdea(Base):
    """Candidata editorial para conteúdo Kids.

    Diferente de KnowledgeItem (que é conteúdo externo coletado),
    KidsIdea é uma OPORTUNIDADE editorial — uma pergunta, curiosidade,
    ou tema que pode virar um vídeo educativo.

    Lifecycle: discovered → fresh → queued → converted
                     ↓              ↓
                  rejected       rejected
    """
    __tablename__ = "kids_ideas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)

    # Conteúdo da ideia
    title: Mapped[str] = mapped_column(String(500))  # "Por que os polvos têm 3 corações?"
    description: Mapped[str] = mapped_column(Text, default="")  # contexto/explicação
    category: Mapped[str] = mapped_column(String(50), default="general", index=True)  # animals, science, space...
    suggested_age_range: Mapped[str] = mapped_column(String(20), default="3-6")  # "3-6", "7-10", "all"

    # Origem
    source: Mapped[str] = mapped_column(String(30), default="ai_ideation")  # ai_ideation, topic_library, seasonal, manual, research
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)  # prompt usado, seed, etc.

    # Scoring (0-100, adaptado de CompositeScore)
    editorial_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    safety_score: Mapped[float] = mapped_column(Float, default=1.0)  # 0-1, 1=safe
    age_fit_score: Mapped[float] = mapped_column(Float, default=0.5)  # 0-1
    educational_value: Mapped[float] = mapped_column(Float, default=0.5)  # 0-1
    curiosity_score: Mapped[float] = mapped_column(Float, default=0.5)  # 0-1
    visual_potential: Mapped[float] = mapped_column(Float, default=0.5)  # 0-1
    final_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)  # composite

    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)  # auditabilidade

    # Safety
    safety_flags: Mapped[list] = mapped_column(JSON, default=list)  # ["violence", "complex", ...]
    safety_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)

    # Lifecycle
    status: Mapped[str] = mapped_column(String(20), default=KidsIdeaStatus.discovered.value, index=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Deduplicação
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    # SHA256(normalize(title)) — para detectar "Polvos têm 3 corações" vs "Polvo possui três corações"

    # Relação com KidsTopic (quando convertida)
    topic_id: Mapped[Optional[int]] = mapped_column(ForeignKey("kids_topics.id"), nullable=True, index=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
```

### 4.2 Modificações em `ChannelProfile`

Adicionar campos específicos de Kids (opcionais, usados apenas quando domain=kids):

```python
# Em ChannelProfile, grupo Configuration:
kids_age_range: Mapped[str] = mapped_column(String(20), default="3-6")
kids_categories: Mapped[list] = mapped_column(JSON, default=list)  # ["animals", "science", "space"]
kids_educational_focus: Mapped[str] = mapped_column(String(200), default="")  # "STEM", "nature", "general"
kids_safety_strictness: Mapped[float] = mapped_column(Float, default=0.8)  # 0-1, quão conservador
```

### 4.3 Modificações em `Automation`

A idea queue do Kids é separada da do Games:

```python
# Em automation.config (JSON):
{
    "idea_queue": [...],           # Games (existente — KI IDs)
    "kids_idea_queue": [...],      # Kids (novo — KidsIdea IDs)
    "kids_queue_mode": "automatic", # "manual" | "automatic"
    "kids_auto_fill_queue": true,
    "kids_max_queue_size": 10
}
```

### 4.4 Modificações em `KidsTopic`

Adicionar relação reversa com KidsIdea:

```python
# Em KidsTopic:
idea_id: Mapped[Optional[int]] = mapped_column(ForeignKey("kids_ideas.id"), nullable=True, index=True)
editorial_intent: Mapped[str] = mapped_column(String(50), default="curiosity")  # curiosity, educational, story
educational_goal: Mapped[str] = mapped_column(String(50), default="general")  # science, nature, math...
```

### 4.5 Nenhuma mudança em `KnowledgeItem`

KnowledgeItem permanece exclusivo de Games. Kids tem sua própria entidade (`KidsIdea`) porque a semântica é diferente: KI é conteúdo externo coletado; KidsIdea é uma oportunidade editorial gerada/curada.

---

## 5. APIs

### 5.1 Novos endpoints — Kids Ideas

```
# Descoberta
POST   /api/kids/ideas/discover          # Trigger AI ideation (cria KidsIdeas)
GET    /api/kids/ideas                    # Listar ideias (filtros: status, category, age_range)
GET    /api/kids/ideas/{id}               # Detalhe de uma ideia
POST   /api/kids/ideas/{id}/reject       # Rejeitar ideia
POST   /api/kids/ideas/{id}/approve      # Aprovar (safety review manual)

# Curadoria
POST   /api/kids/ideas                   # Criar ideia manual
POST   /api/kids/ideas/{id}/convert      # Converter ideia → KidsTopic

# Fila
GET    /api/kids/idea-queue              # Listar fila de ideias Kids
POST   /api/kids/idea-queue/add          # Adicionar à fila
POST   /api/kids/idea-queue/remove       # Remover da fila
POST   /api/kids/idea-queue/reorder      # Reordenar fila

# Stats
GET    /api/kids/ideas/stats             # Estatísticas (fresh, queued, converted, rejected)
```

### 5.2 Modificações em endpoints existentes

```
# automation_routes.py — check_automation
# MUDANÇA: dispatch por domínio em vez de checar GameplaySource diretamente

# automation_routes.py — create_job_from_automation
# MUDANÇA: dispatch por domínio para criar job

# kids_routes.py — POST /kids/generate
# MUDANÇA: aceitar idea_id opcional (além de topic_id) para auto-criar topic
```

### 5.3 Endpoints NÃO modificados

```
# knowledge_item_routes.py — todos permanecem para Games
# Nenhuma mudança nos endpoints de KI existentes
```

---

## 6. WORKER / JOBS

### 6.1 Novo job type: `kids_idea_discovery`

```python
# Em JobType enum:
kids_idea_discovery = "kids_idea_discovery"  # coletar ideias via AI + topic library
```

**Comportamento:**
- Roda no VPS (Control Plane) — não precisa de GPU
- Usa LLM (Ollama) para gerar ideias
- Consulta topic library + seasonal calendar
- Cria KidsIdeas com status=discovered
- Agenda safety review + scoring

### 6.2 Novo job type: `kids_idea_score`

```python
# Em JobType enum:
kids_idea_score = "kids_idea_score"  # scoring + safety review de KidsIdeas
```

**Comportamento:**
- Roda no VPS
- Para cada KidsIdea com status=discovered:
  - LLM avalia safety (age suitability, sensitive content)
  - LLM pontua editorial_score, age_fit, educational_value, curiosity, visual_potential
  - Calcula final_score (composite adaptado)
  - Se safety_score < threshold → status=rejected
  - Senão → status=fresh

### 6.3 Job existente `content_collect` — sem mudanças

Permanece para Games. Kids usa `kids_idea_discovery` separado.

### 6.4 Scheduler do worker

```
Remote worker poll loop:
  ├── check_automation (domain-aware)
  │     ├── Games: checa GameplaySource → KI queue → editorial decision
  │     └── Kids: checa KidsTopic ready → kids_idea_queue → idea→topic→generate
  ├── _auto_content_collection (Games — existente)
  └── _auto_kids_idea_discovery (Kids — novo, intervalo configurável)
```

---

## 7. AUTOMAÇÃO DOMAIN-AWARE

### 7.1 Strategy pattern

```python
# src/gpcg/application/automation_strategy.py (novo)

class AutomationStrategy(ABC):
    """Estratégia de automação por domínio."""

    @abstractmethod
    def can_automate(self, session, user_id) -> bool:
        """Verifica se o domínio tem recursos para automatizar."""

    @abstractmethod
    def create_job(self, session, user_id) -> Optional[int]:
        """Cria um job a partir da automação do domínio."""

    @abstractmethod
    def get_stats(self, session, user_id) -> dict:
        """Retorna stats específicas do domínio."""


class GamesAutomationStrategy(AutomationStrategy):
    """Estratégia existente de Games — encapsula lógica atual."""

    def can_automate(self, session, user_id) -> bool:
        # Lógica atual: checa GameplaySource ready_count
        ...

    def create_job(self, session, user_id) -> Optional[int]:
        # Lógica atual: consume KI queue ou editorial decision
        ...


class KidsAutomationStrategy(AutomationStrategy):
    """Estratégia de Kids — nova."""

    def can_automate(self, session, user_id) -> bool:
        # Checa se há KidsTopics com assets prontos
        # OU se há KidsIdeas na fila que podem ser convertidas
        ...

    def create_job(self, session, user_id) -> Optional[int]:
        # Consome kids_idea_queue:
        #   1. Pega primeira ideia da fila
        #   2. Cria KidsTopic a partir da ideia
        #   3. Cria job generate_short com domain=kids
        ...
```

### 7.2 Registro de estratégias

```python
# src/gpcg/domains/registry.py (extender)

def get_automation_strategy(domain: str) -> AutomationStrategy:
    if domain == ContentDomain.games.value:
        from gpcg.application.games_automation_strategy import GamesAutomationStrategy
        return GamesAutomationStrategy()
    if domain == ContentDomain.kids.value:
        from gpcg.application.kids_automation_strategy import KidsAutomationStrategy
        return KidsAutomationStrategy()
    raise ValueError(f"No automation strategy for domain '{domain}'")
```

### 7.3 Modificação no `check_automation`

```python
# ANTES (linha 345-350):
ready_count = db.query(GameplaySource).filter(...).count()
if ready_count == 0:
    continue

# DEPOIS:
profile = db.query(ChannelProfile).filter(ChannelProfile.user_id == auto.user_id).first()
domain = profile.domain if profile else ContentDomain.games.value
strategy = get_automation_strategy(domain)
if not strategy.can_automate(db, auto.user_id):
    continue
```

**Impacto no Games:** Zero. `GamesAutomationStrategy.can_automate()` encapsula a mesma lógica de `GameplaySource` que existe hoje. O comportamento é idêntico.

### 7.4 Modificação no `create_job_from_automation`

```python
# ANTES (linha 1191-1196):
ready_count = session.query(GameplaySource).filter(...).count()
if ready_count == 0:
    return None

# DEPOIS:
profile = session.query(ChannelProfile).filter(ChannelProfile.user_id == user_id).first()
domain = profile.domain if profile else ContentDomain.games.value
strategy = get_automation_strategy(domain)
if not strategy.can_automate(session, user_id):
    return None
# ... resto do fluxo delega para strategy.create_job()
```

---

## 8. UI

### 8.1 Página `/ideas` — domain-aware

A página de ideias atual (`frontend/src/pages/ideas.tsx`) é totalmente Games-specific (mostra jogos, gameplay preference, etc.).

**Proposta:**

- Quando `domain === "games"`: mostra a página atual (KnowledgeItems, KI queue)
- Quando `domain === "kids"`: mostra uma nova view de Kids Ideas
  - Lista de KidsIdeas com filtros (category, age_range, status)
  - Safety badge para cada ideia
  - Score breakdown visual
  - Botões: aprovar, rejeitar, adicionar à fila, converter para topic
  - Fila de ideias Kids (drag-to-reorder)
  - Botão "Descobrir ideias" (trigger AI ideation)

### 8.2 Dashboard — stats domain-aware

Já é parcialmente domain-aware (linha 1745-1762 de automation_routes.py). Adicionar:

```python
result["kids"] = {
    "total_topics": total_topics,
    "total_assets": total_assets,
    "ready_assets": ready_assets,
    "total_ideas": total_ideas,         # NOVO
    "fresh_ideas": fresh_ideas,         # NOVO
    "queued_ideas": queued_ideas,       # NOVO
}
```

### 8.3 Automation page — config domain-aware

Adicionar configurações de Kids na página de automação:

- Queue mode: manual / automatic
- Auto-fill queue: on/off
- Max queue size
- Discovery interval (horas)
- Safety strictness slider
- Categories preferidas

---

## 9. REUTILIZAÇÃO

### REUTILIZAR (infraestrutura genérica)

| Componente | Por quê |
|-----------|---------|
| `CompositeScore` (dataclass) | Estrutura 3-camadas é genérica — Quality × Fit × Timing |
| `LifecycleManager` (freshness decay) | Conceito de decay por tipo é reutilizável |
| `content_hash` dedup pattern | SHA256(normalize(title)) — mesmo pattern do KI |
| `KnowledgeItemUsage` pattern | Per-consumer tracking — adaptar para KidsIdeaUsage se necessário |
| `reconcile_user_queue` pattern | Auto-fill queue com fresh items — mesmo pattern |
| `idea_queue` storage em `automation.config` | JSON blob na automation — mesmo pattern (chave separada) |
| `DomainRegistry` dispatch | Já existe para generation service — extender para automation |
| `ChannelProfile` | Estrutura já é domain-agnostic — adicionar campos Kids |
| `to_prompt_context()` | Já funciona para qualquer domínio |
| `Job` model com `domain` field | Já suporta domain-aware dispatch |

### GENERALIZAR (extrair abstração com backward compat)

| Componente | Como |
|-----------|------|
| `check_automation` | Extrair `can_automate()` para strategy — Games encapsula lógica atual |
| `create_job_from_automation` | Extrair `create_job()` para strategy — Games encapsula lógica atual |
| `reconcile_user_queue` | Generalizar para aceitar entity type + queue key (ou criar `reconcile_kids_queue` separado) |
| Automation stats | Já é parcialmente domain-aware — completar com strategy.get_stats() |

### MANTER EXCLUSIVO DE GAMES

| Componente | Por quê |
|-----------|---------|
| `KnowledgeItem` | Semântica diferente (conteúdo externo coletado vs oportunidade editorial) |
| `GoalOrientedCollector` | RSS de games — não aplica a Kids |
| `EditorialBriefBuilder` | Feeds + search queries baseadas em jogos |
| `EditorialIntentBuilder` | Priority games baseado em gameplay inventory |
| `EditorialStrategyService` | GameInventory com gameplay_sources_ready |
| `CompositeScorer` (implementação) | `_gameplay_availability` é Games-specific |
| `GameTarget`, `SearchQuery`, `FeedSpec` | Conceitos de games |
| `SEARCH_TEMPLATES` | Templates de search para games |
| `GENERAL_GAMING_FEEDS` | Feeds de games |
| `content_collect` job | Coleta RSS de games |
| `curiosity_short` job type | Formato de Games (curiosidade + gameplay background) |

### CRIAR EXCLUSIVO PARA KIDS

| Componente | Por quê |
|-----------|---------|
| `KidsIdea` (entidade) | Oportunidade editorial — semântica diferente de KI |
| `KidsIdeaDiscovery` | AI ideation + topic library + seasonal — descoberta própria |
| `KidsSafetyFilter` | Filtro de adequação infantil — não existe em Games |
| `KidsScorer` | Scoring adaptado: age_fit, educational_value, visual_potential |
| `KidsAutomationStrategy` | Lógica de automação específica (KidsTopic + assets em vez de GameplaySource) |
| `kids_idea_discovery` job | Job de descoberta — diferente de content_collect |
| `kids_idea_score` job | Job de scoring + safety — diferente de score_all_fresh |
| `kids_idea_queue` | Fila separada em automation.config |
| `KidsTopicLibrary` | Biblioteca de categorias/assuntos para seed de ideias |
| `SeasonalCalendar` | Datas comemorativas + temas sazonais |

---

## 10. FLUXO DETALHADO — IDEA → KIDSTOPIC

### 10.1 Descoberta

```
1. kids_idea_discovery job roda (a cada N horas)
2. KidsIdeaDiscovery.collect():
   a. Consulta KidsTopicLibrary (categorias do canal)
   b. Consulta SeasonalCalendar (datas próximas)
   c. LLM gera N ideias por categoria
   d. Para cada ideia:
      - content_hash = SHA256(normalize(title))
      - Dedup check: já existe KidsIdea com mesmo hash? → skip
      - Dedup check: já existe KidsTopic com título similar? → skip
      - Cria KidsIdea com status=discovered
3. Job completa
```

### 10.2 Scoring + Safety

```
1. kids_idea_score job roda (após discovery)
2. Para cada KidsIdea com status=discovered:
   a. KidsSafetyFilter.review(idea, age_range):
      - LLM avalia: violence, complex, sensitive, language
      - safety_score = 0-1
      - safety_flags = [...]
      - Se safety_score < threshold (configurável): status=rejected
   b. KidsScorer.score(idea, channel_profile):
      - editorial_quality: LLM pontua (0-100)
      - age_fit: LLM avalia adequação para age_range (0-1)
      - educational_value: LLM pontua (0-1)
      - curiosity: LLM pontua (0-1)
      - visual_potential: LLM pontua (0-1)
      - final_score = quality/100 * age_fit * educational_value * curiosity
   c. status = fresh
```

### 10.3 Curadoria

```
Manual:
  Usuário vê /kids/ideas
  → aprova / rejeita / adiciona à fila

Automático (reconcile_kids_queue):
  Se queue_mode=automatic e auto_fill_queue=true
  → pega top-N KidsIdeas fresh por final_score
  → adiciona em kids_idea_queue
```

### 10.4 Seleção → Produção

```
check_automation (Kids):
  1. strategy.can_automate():
     - Tem KidsTopics com assets ready? OU
     - Tem ideias na kids_idea_queue?
  2. strategy.create_job():
     a. Se kids_idea_queue não vazia:
        - Pega primeira ideia
        - Cria KidsTopic (title=idea.title, category=idea.category, age_range=idea.suggested_age_range)
        - Marca KidsIdea.status=converted, topic_id=novo_topic.id
        - Cria job generate_short com domain=kids, artifacts={topic_id, idea_id}
     b. Se kids_idea_queue vazia + modo automático:
        - Pega melhor KidsIdea fresh
        - Mesmo fluxo
     c. Se não há ideias:
        - Agenda kids_idea_discovery job
```

### 10.5 Assets — o gargalo

O Kids precisa de imagens para produzir. A automação não pode gerar um vídeo sem assets.

**Opções:**

A. **Automação só converte ideia → topic** — usuário adiciona assets depois, geração fica manual
B. **Automação pula ideias sem assets** — só gera para topics que já têm assets
C. **Geração automática de assets** — AI image generation (SDXL/DALL-E) para o topic

**Recomendação para MVP:** Opção A. A automação cria o KidsTopic a partir da ideia, mas a geração só acontece quando o usuário adiciona assets e clica "Gerar". Isso separa descoberta/seleção de produção visual.

**Futuro:** Opção C — gerar imagens automaticamente via SDXL/ComfyUI (já disponível no ecossistema).

---

## 11. DEDUPLICAÇÃO

### 11.1 Camadas de dedup

```
Camada 1: content_hash (exact match)
  - SHA256(normalize(title))
  - Detecta "Polvos têm 3 corações" == "polvos tem 3 coracoes"
  - Não detecta paráfrases

Camada 2: Topic similarity (fuzzy)
  - Ao criar KidsIdea, comparar title com:
    a. KidsIdeas existentes (qualquer status)
    b. KidsTopics existentes
    c. Videos já produzidos (via ContentPlan.topic)
  - Usar:
    - Embedding cosine similarity (se disponível)
    - Ou: normalização + Jaccard / Levenshtein
  - Threshold: 0.85 → marcar como duplicata

Camada 3: Production history
  - Ao converter ideia → topic, registrar no ContentPlan
  - Ao gerar vídeo, registrar topic no history
  - reconcile_kids_queue exclui ideias com topic já produzido
```

### 11.2 Implementação MVP

Para o MVP, usar Camada 1 (content_hash) + Camada 3 (production history). Embeddings (Camada 2) podem ser adicionados depois reutilizando o `EmbeddingService` já existente.

---

## 12. LIFECYCLE

### 12.1 Estados do KidsIdea

```
discovered → fresh → queued → converted
               ↓        ↓
           rejected  rejected
               ↓
            expired
```

| Estado | Significado | Transição |
|--------|-------------|-----------|
| `discovered` | Recém-criada, aguardando scoring | → fresh (após score) ou → rejected (safety) |
| `fresh` | Scored e disponível | → queued (add to queue) ou → rejected (user) |
| `queued` | Na fila de produção | → converted (vira KidsTopic) ou → rejected |
| `converted` | Virou KidsTopic | Terminal |
| `rejected` | Rejeitada | Terminal |
| `expired` | Arquivada por idade | Terminal |

### 12.2 Freshness decay

Reutilizar conceito do `LifecycleManager`:

- KidsIdeas são evergreen (não decaem rápido como news)
- half_life = 90 dias (configurável)
- Após 180 dias sem uso → expired

---

## 13. RISCOS

### Risco 1: Regressão no Games
**Mitigação:** `GamesAutomationStrategy` encapsula lógica atual sem mudanças. Testes existentes (611+) devem passar sem modificação.

### Risco 2: Acoplamento prematuro
**Mitigação:** KidsIdea é separada de KnowledgeItem. Não generalizar KI para servir dois domínios com semânticas diferentes.

### Risco 3: Custo de LLM para descoberta
**Mitigação:** Batch de N ideias por ciclo (ex: 10 ideias por categoria). Intervalo configurável (ex: 24h). Usar modelo leve (gemma3:8b) para ideation.

### Risco 4: Safety filter imperfeito
**Mitigação:** Safety score + flags + human review. Configurável strictness. Rejeição conservadora por padrão.

### Risco 5: Fila sem assets
**Mitigação:** Automação cria topic mas não gera sem assets. Usuário é notificado de que há topics aguardando assets.

### Risco 6: Duplicação de ideias
**Mitigação:** content_hash + production history check. Embeddings no futuro.

---

## 14. PLANO DE IMPLEMENTAÇÃO

### Fase 1 — Fundação (sem automação)

**Objetivo:** Criar a entidade KidsIdea e APIs básicas de curadoria manual.

1. Criar `KidsIdea` model + migration
2. Criar `KidsIdeaStatus` enum
3. Adicionar campos Kids em `ChannelProfile`
4. Criar `kids_idea_routes.py` (CRUD: list, get, create manual, reject, approve)
5. Criar `KidsSafetyFilter` (LLM-based)
6. Criar `KidsScorer` (adaptado de CompositeScore)
7. Frontend: view de Kids Ideas na página /ideas
8. Testes: model, routes, safety, scoring

**Entrega:** Usuário pode criar ideias manualmente, ver, rejeitar, aprovar. Safety + scoring funcionam.

### Fase 2 — Descoberta automática

**Objetivo:** AI ideation + topic library.

1. Criar `KidsTopicLibrary` (categorias + seeds)
2. Criar `SeasonalCalendar` (datas comemorativas)
3. Criar `KidsIdeaDiscovery` service
4. Criar `kids_idea_discovery` job type
5. Adicionar trigger no worker (intervalo configurável)
6. Criar `kids_idea_score` job type (scoring batch)
7. Deduplicação: content_hash + production history
8. Frontend: botão "Descobrir ideias"
9. Testes: discovery, dedup, scoring batch

**Entrega:** Sistema descobre ideias automaticamente. Usuário vê ideias fresh na /ideas.

### Fase 3 — Fila e curadoria

**Objetivo:** Idea queue para Kids + reconcile.

1. Criar `kids_idea_queue` em automation.config
2. Criar `reconcile_kids_queue` (auto-fill)
3. Endpoints: add/remove/reorder queue
4. Frontend: fila de ideias Kids (drag-to-reorder)
5. Testes: queue operations, reconcile

**Entrega:** Usuário pode curar fila de ideias. Auto-fill funciona.

### Fase 4 — Automação domain-aware

**Objetivo:** Strategy pattern + automação Kids.

1. Criar `AutomationStrategy` ABC
2. Criar `GamesAutomationStrategy` (encapsula lógica atual)
3. Criar `KidsAutomationStrategy`
4. Modificar `check_automation` (dispatch por domínio)
5. Modificar `create_job_from_automation` (dispatch por domínio)
6. Implementar Idea→Topic conversion na strategy
7. Testes: strategy dispatch, Kids automation, Games regression
8. Validar: todos os testes Games existentes passam

**Entrega:** Automação Kids funciona. Automação Games não regrediu.

### Fase 5 — Produção integrada

**Objetivo:** Conectar automação ao pipeline de geração existente.

1. Modificar `POST /kids/generate` para aceitar `idea_id`
2. Auto-criar KidsTopic a partir de KidsIdea na automação
3. Notificar usuário sobre topics aguardando assets
4. Dashboard: stats de ideas + queue
5. Testes: end-to-end idea → topic → generate
6. Documentação

**Entrega:** Fluxo completo: discovery → idea → queue → topic → generate.

---

## 15. IMPACTO

### 15.1 Impacto no Games

| Componente | Mudança | Risco |
|-----------|---------|-------|
| `check_automation` | Dispatch por domínio (Games encapsulado em strategy) | Baixo — lógica movida, não alterada |
| `create_job_from_automation` | Dispatch por domínio | Baixo — mesma lógica |
| `KnowledgeItem` | Nenhuma mudança | Zero |
| `GoalOrientedCollector` | Nenhuma mudança | Zero |
| `EditorialBriefBuilder` | Nenhuma mudança | Zero |
| `CompositeScorer` | Nenhuma mudança | Zero |
| `EditorialStrategyService` | Nenhuma mudança | Zero |
| `content_collect` job | Nenhuma mudança | Zero |
| Testes do Games | Devem passar sem mudança | Validação obrigatória |

### 15.2 Impacto no Kids

| Componente | Mudança | Risco |
|-----------|---------|-------|
| `KidsTopic` | Adicionar idea_id, editorial_intent, educational_goal | Baixo — campos opcionais |
| `KidsGenerationService` | Nenhuma mudança | Zero |
| `kids_routes.py` | Adicionar endpoints de ideas | Baixo — novos endpoints |
| `domain_reset_service` | Adicionar cleanup de KidsIdeas | Baixo — novo delete |

### 15.3 Impacto em código compartilhado

| Componente | Mudança | Risco |
|-----------|---------|-------|
| `ChannelProfile` | Adicionar campos Kids opcionais | Baixo — defaults neutros |
| `Automation.config` | Adicionar chaves kids_* | Baixo — JSON extensible |
| `domains/registry.py` | Adicionar `get_automation_strategy()` | Baixo — nova função |
| `JobType` enum | Adicionar kids_idea_discovery, kids_idea_score | Baixo — novos valores |
| `JobStage` enum | Nenhuma mudança necessária | Zero |

---

## 16. RECOMENDAÇÃO FINAL

### Aprovo a implementação por fases.

A Fase 1 (fundação) é de baixo risco e cria a base. A Fase 4 (automação domain-aware) é a mais sensível porque toca em código compartilhado, mas o strategy pattern com `GamesAutomationStrategy` encapsulando a lógica atual minimiza o risco de regressão.

### Pontos críticos:

1. **NÃO generalizar KnowledgeItem** — KidsIdea é uma entidade separada com semântica diferente
2. **Strategy pattern para automação** — evita if/else espalhado e prepara para Movies/Anime/etc
3. **Safety filter é parte da arquitetura** — não é um filtro depois
4. **MVP separa descoberta de produção visual** — automação cria topic, usuário adiciona assets
5. **Validar regressão do Games após Fase 4** — todos os 611+ testes devem passar

### Ordem recomendada:

```
Fase 1 (fundação) → Fase 2 (descoberta) → Fase 3 (fila) → Fase 4 (automação) → Fase 5 (produção)
```

Cada fase é independentemente testável e deployable. Pode parar após qualquer fase e o sistema funciona parcialmente.
