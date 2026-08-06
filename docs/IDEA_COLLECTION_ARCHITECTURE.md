# Arquitetura: Coleta de Ideias & Inteligência Editorial

> **Status**: Documento de referência para expansão do sistema de coleta de ideias
> **Versão**: v0.3.9 (2026-08-05)
> **Escopo**: Pipeline completo desde fontes externas até fila de produção, com visão de evolução por canal

---

## 1. Visão Geral

O GPCG é um produtor de conteúdo autônomo para YouTube Shorts. O pipeline de ideias
é o "cérebro editorial" do sistema: decide **o que** produzir, **por que**, e **como**.

```
┌─────────────────────────────────────────────────────────────────────┐
│                     PIPELINE DE COLETA DE IDEIAS                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  FONTES EXTERNAS          COLETA              SCORING                │
│  ┌──────────┐            ┌──────────┐       ┌──────────────┐        │
│  │ RSS      │──→ collect │ KI criado │──→    │ Gate regex   │        │
│  │ Feeds    │            │ (fresh)  │       │ (clickbait?) │        │
│  │ Manual   │            └──────────┘       └──────┬───────┘        │
│  └──────────┘                                      │                │
│                                          ┌─────────▼──────────┐    │
│                                          │ LLM Scoring (0-100)│    │
│                                          │ 5 dimensões editor.│    │
│                                          └─────────┬──────────┘    │
│                                                    │                │
│  FILA DE PRODUÇÃO        CONSUMO             EDITORIAL              │
│  ┌──────────┐           ┌──────────┐       ┌──────────────┐        │
│  │ Reconcil.│←──────────│ Fila     │──→    │ ContentPlan  │        │
│  │ auto-fill│           │ (FIFO)   │       │ + StoryFinder│        │
│  └──────────┘           └──────────┘       │ + Editorial  │        │
│                                              │   Planner    │        │
│                                              └──────────────┘        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Fontes de Coleta (Estado Atual)

### 2.1 RSS — Principal

**Arquivo**: `src/gpcg/application/content_collectors.py`

#### Feeds gerais de gaming (coletados em toda execução)

| Fonte | URL | Tipo | Via |
|-------|-----|------|-----|
| IGN | `feeds.feedburner.com/ign/games-all` | news | RSS nativo |
| GameSpot | `gamespot.com/feeds/mashup/` | news | RSS nativo |
| Polygon | `polygon.com/rss/index.xml` | news | RSS nativo |
| Eurogamer | `eurogamer.net/feed` | news | RSS nativo |
| Rock Paper Shotgun | `rockpapershotgun.com/feed` | news | RSS nativo |
| Kotaku | `rsshub.app/kotaku/story/news` | news | RSSHub |
| r/games | `rsshub.app/reddit/subreddit/games` | news | RSSHub |
| r/gaming | `rsshub.app/reddit/subreddit/gaming` | news | RSSHub |
| r/truegaming | `rsshub.app/reddit/subreddit/truegaming` | curiosity | RSSHub |
| r/patientgamers | `rsshub.app/reddit/subreddit/patientgamers` | curiosity | RSSHub |

**Constante**: `GENERAL_GAMING_FEEDS` (linhas 41-55)

#### Google News RSS (por jogo)

- **URL template**: `https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en`
- **Config**: `gpcg_rss_feed_url` em `config.py` (linha 318)
- **Query**: nome do jogo (canonical_name) com URL-encoding
- **Trigger**: para cada Game com gameplay disponível

### 2.2 Manual (Usuário)

**Arquivo**: `src/gpcg/api/knowledge_item_routes.py` (linhas 211-263)

- Endpoint: `POST /api/knowledge-items`
- `source_type = "manual"`, `item_type = "curiosity"`
- Usuário cria ideia via UI com título + conteúdo

### 2.3 Fontes Previstas (NÃO Implementadas)

| Fonte | Enum | Status | Notas |
|-------|------|--------|-------|
| Wikipedia | `KnowledgeItemSource.wikipedia` | Previsto | Lore/curiosidades evergreen |
| Steam | `KnowledgeItemSource.steam` | Previsto | News, updates, reviews |
| Reddit (direto) | `KnowledgeItemSource.reddit` | Previsto | Via RSSHub atualmente |
| IGDB | `KnowledgeItemSource.igdb` | Previsto | Metadados de jogos |

---

## 3. Modelo de Dados: KnowledgeItem

**Arquivo**: `src/gpcg/domain/models.py` (linhas 1112-1180)

```python
class KnowledgeItem(Base):
    # Identificação
    id: int
    user_id: Optional[int]     # NULL = global pool, set = privado
    game_id: Optional[int]     # NULL = conteúdo geral
    is_public: bool            # compartilhamento entre usuários

    # Conteúdo
    title: str (max 500)
    content: Text

    # Classificação
    item_type: str             # news | curiosity | lore | fact
    source_type: str           # rss | wikipedia | steam | reddit | igdb | user_doc | manual

    # Proveniência
    source_url: Optional[str]
    source_name: Optional[str]
    published_at: Optional[datetime]
    collected_at: datetime

    # Qualidade
    editorial_score: float (0-100, indexado)

    # Estado
    status: str                # fresh | used | rejected
    rejection_reason: Optional[str]

    # Denormalização
    franchise: Optional[str]
    developer: Optional[str]
    tags: list (JSON)

    # Deduplicação
    content_hash: str (SHA256, unique)
```

### Enums

| Enum | Valores | Uso |
|------|---------|-----|
| `KnowledgeItemType` | news, curiosity, lore, fact | Classificação editorial |
| `KnowledgeItemSource` | rss, wikipedia, steam, reddit, igdb, user_doc, manual | Origem da coleta |
| `KnowledgeItemStatus` | fresh, used, rejected | Ciclo de vida |

### Modelo Híbrido de Propriedade

**Arquivo**: `src/gpcg/domain/visibility.py` (linhas 25-45)

```
user_id IS NULL         → global (visível a todos)
user_id == X, public=F  → privado do usuário X
user_id == X, public=T  → compartilhado por X
```

```python
def visible_to_user(user_id_col, is_public_col, consumer_user_id):
    return or_(
        user_id_col.is_(None),           # global
        user_id_col == consumer_user_id, # próprio
        is_public_col.is_(True),         # compartilhado
    )
```

---

## 4. Pipeline de Scoring Editorial

### 4.1 Gate Determinístico (regex, sem LLM)

**Arquivo**: `src/gpcg/application/knowledge_item_service.py` (linhas 273-289)

```python
_detect_quality_issues(item)
```

Detecta padrões e aplica penalidade fixa + marca como `rejected`:

| Padrão | Score | Status |
|--------|-------|--------|
| Clickbait | 15.0 | rejected |
| Promoção | 10.0 | rejected |
| Rumor | 20.0 | rejected |

### 4.2 LLM Scoring (0-100)

**Arquivo**: `src/gpcg/application/knowledge_item_service.py` (linhas 292-328)

Se passou no gate, o LLM avalia 5 dimensões editoriais:

1. **Curiosidade** — cria gap de informação?
2. **Surpresa** — quebra expectativa comum?
3. **Retenção** — segura atenção por ~60s?
4. **Familiaridade** — reconhecível para público gaming?
5. **Insight** — perspectiva nova/reveladora?

Gates factuais no prompt:
- CLICKBAIT → score ≤ 15
- PROMOÇÃO → score ≤ 10
- RUMOR → score ≤ 20
- LEAK não confirmado → score ≤ 25

**Batch scoring**: `score_all_fresh(session, llm, limit=50)` — pontua até 50 KIs por execução.

### 4.3 CuriosityScorer (para Facts, não KIs)

**Arquivo**: `src/gpcg/application/curiosity_scorer.py` (linhas 182-291)

Score de curiosidade para Facts com 5 sub-scores editoriais + 1 técnico:

| Sub-score | Peso | Descrição |
|-----------|------|-----------|
| curiosity_gap | 0.30 | Cria gap de conhecimento? |
| surprise_potential | 0.25 | Quebra expectativa? |
| retention_potential | 0.20 | Segura atenção? |
| familiarity | 0.15 | Conecta ao conhecido? (Loewenstein) |
| insight_quality | 0.10 | Insight ou trivia? |
| visual_potential | — | Técnico, excluído da média |

**Feature flag**: `gpcg_curiosity_scoring_enabled` (default: true)

---

## 5. Pipeline Editorial (Ideia → Vídeo)

### 5.1 Fluxo Completo

```
RSS/Manual
    ↓
KnowledgeItem (fresh, editorial_score)
    ↓
ContentPlanningService._get_knowledge_items()
    ↓ (unificado com Facts)
LLM escolhe melhor ideia
    ↓
ContentPlan (fact_id OU knowledge_item_id)
    ↓
StoryFinder.find_story()          [opcional, V2]
    ↓ → StoryConcept (angle, hook, frame)
EditorialPlanner                  [opcional, V2]
    ↓ → VideoCreativePlan (video_type, gameplay_strategy, beats)
CreativeEngine                    [opcional, V2]
    ↓ → CreativeMaterial (hooks, angles, punchlines)
ScriptService
    ↓ → Script (final, originality_score)
HumanizationService               [opcional, V2]
    ↓ → Script humanizado
ScriptCritic                      [opcional, V2]
    ↓ → REVISE/ACCEPT (até 3 revisões)
TTS
    ↓ → narration.wav
GameplayRetriever
    ↓ (busca semântica via embeddings)
SelectedClips
    ↓
RenderPlanBuilder → video-generate → Video
    ↓
MetadataGeneration → social_title, social_description, social_tags
    ↓
Upload to VPS → YouTube publish (manual ou auto)
```

### 5.2 StoryFinder

**Arquivo**: `src/gpcg/application/story_finder.py` (linhas 105-241)

Transforma um fact em história encontrando o **ângulo editorial**.

```python
@dataclass
class StoryConcept:
    fact_claim: str
    angle: str              # ângulo editorial específico
    curiosity_gap: str      # gap que o vídeo preenche
    narrative_hook: str     # primeira linha do vídeo
    frame: str              # framing (Kahneman)
    is_insight: bool        # insight ou trivia?
    is_story: bool          # tem potencial narrativo?
    confidence: float       # 0.0-1.0
```

**Gate**: `is_acceptable(concept)` → `is_story=true` E `confidence >= 0.5`

**Feature flag**: `gpcg_story_finder_enabled` (default: true)

### 5.3 EditorialPlanner

**Arquivo**: `src/gpcg/application/editorial_planner.py` (linhas 188-192)

Decide `video_type`:
- `GAME_RELATED` — vídeo sobre um jogo específico (job_type = generate_short)
- `GENERAL_TOPIC` — tópico geral com gameplay de fundo (job_type = curiosity_short)

Produz `VideoCreativePlan` com:
- `gameplay_strategy`: "related" | "background_filler" | "thematic_match"
- `gameplay_query`: query semântica para busca de clips
- `narrative_beats`: beats para orientar creative engine
- `HumorPlan`: enabled, intensity, styles, frequency
- `model_recommendation`: gemma3 vs qwen3

### 5.4 EditorialStrategyService (Decisão Autônoma)

**Arquivo**: `src/gpcg/application/editorial_strategy.py` (linhas 120-697)

O "cérebro editorial" do canal. Antes de criar um job, analisa:

1. Quais jogos têm gameplay pronto (importado + mapeado)
2. Quais jogos têm conhecimento (facts, document chunks, KIs)
3. Que tópicos já foram cobertos (ContentPlan history)
4. Quais facts foram usados recentemente
5. **ChannelProfile** (tone, niche, audience)

Usa LLM para decidir: game + fact + formato que maximiza variedade.

```python
@dataclass
class EditorialDecision:
    job_type: str             # "generate_short" | "curiosity_short"
    game_id: Optional[int]
    background_game_id: Optional[int]
    fact_id: Optional[int]
    topic_hint: str
    reason: str
```

---

## 6. Fila de Produção (Idea Queue)

### 6.1 Estrutura

**Arquivo**: `src/gpcg/api/automation_routes.py`

A fila vive dentro de `Automation.config.idea_queue` (JSON):

```python
[
  {
    "ki_id": 95,
    "gameplay_preference": 2,     # game_id específico ou null=auto
    "reuse_override": null         # null | "allow_reuse" | "skip"
  },
  ...
]
```

### 6.2 Endpoints

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/api/idea-queue` | GET | Retorna fila + items |
| `/api/idea-queue/add` | POST | Adiciona KI à fila |
| `/api/idea-queue/remove` | POST | Remove da fila |
| `/api/idea-queue/reorder` | POST | Reordena fila |

### 6.3 Reconciliador (V3)

**Arquivo**: `src/gpcg/api/automation_routes.py` (linhas 400-450)

```python
reconcile_user_queue(db, user_id)
```

Auto-preenche a fila quando:
- `queue_mode == "automatic"`
- `auto_fill_queue == True`
- `len(queue) < max_queue_size`

**Triggers**:
- Worker poll (`check_automation`)
- Após coleta de novos KIs (`sync_knowledge_items`)
- Quando usuário abre a página de ideias (`GET /idea-queue`)

**Exclusões** (V3.8):
- KIs já na fila (`exclude_ids`)
- KIs com jobs ativos (`queued`/`running`) — evita re-adicionar KI sendo processada

### 6.4 Consumo

**Arquivo**: `src/gpcg/api/automation_routes.py` (linhas 930-1126)

1. Worker chama `check_automation`
2. Se fila não vazia: consome primeiro (FIFO)
3. Se `ki.game_id`: cria `generate_short` (vídeo sobre o jogo)
4. Se `ki.game_id IS NULL`: cria `curiosity_short` (curiosidade geral)
5. Se `gameplay_preference` setado: usa o jogo escolhido como background
6. KI é removida da fila
7. KI vira `used` quando o Video é persistido

---

## 7. Sistema de Embeddings

**Arquivo**: `src/gpcg/application/embedding_service.py`

### 7.1 Tabelas

| Tabela | Chave | Conteúdo |
|--------|-------|----------|
| `knowledge_item_embeddings` | item_id | Embedding do KI (title + content[:500]) |
| `gameplay_event_embeddings` | event_id | Embedding do evento (VLM description) |

### 7.2 Serialização

```python
serialize_embedding(vector) → struct.pack(f"{len(vector)}f", *vector)
deserialize_embedding(data)  → struct.unpack(f"{count}f", data)
```

Modelo: `nomic-embed-text` via Ollama.

### 7.3 Uso Atual

| Componente | Usa Embeddings? | Como |
|------------|-----------------|------|
| KnowledgeItems | **Não** (preparado) | Futuro: busca semântica, clustering |
| GameplayEvents | **Sim** | Busca semântica de clips por query textual |

### 7.4 GameplayRetriever

**Arquivo**: `src/gpcg/application/gameplay_retriever.py`

 Usa `GameplayIndexService.search_events()` para busca semântica:

1. Gera embedding da query (ex: "character being chased")
2. Compara com embeddings de eventos do source
3. Filtra por `min_similarity` (default: 0.3)
4. Ordena por similarity + interesting_score
5. Fallback: busca ILIKE se embeddings indisponíveis

**Para GENERAL_TOPIC**: scores cada source por fit narrativo:
- Event type coverage (0-40 pts)
- Interesting score médio (0-20 pts)
- Clips disponíveis (0-20 pts)
- Game relevance bonus (+20 pts)
- Visual confidence (0-10 pts)
- Diversity penalty (-10 por aparição recente)

---

## 8. Configuração por Canal

### 8.1 ChannelProfile

**Arquivo**: `src/gpcg/domain/models.py` (linhas 959-1058)

```python
class ChannelProfile(Base):
    channel_description: str    # descrição free-form
    niche: str                  # ex: "FPS competitivo"
    target_audience: str        # ex: "Jogadores casuais"
    tone_of_voice: str          # ex: "educativo, analítico"
    narrative_style: str        # ex: "storytelling"
    content_goals: str          # objetivos do canal
    special_rules: str          # regras específicas para IA
    metadata_json: dict
```

### 8.2 Injeção no Pipeline por Estágio

**Arquivo**: `src/gpcg/domain/models.py` (linhas 1023-1058)

```python
def to_stage_context(self, stage: str) -> str:
```

| Estágio | Campos injetados |
|---------|-----------------|
| `content_planning` | niche, content_goals, target_audience |
| `story_finding` | tone_of_voice, narrative_style |
| `editorial_planning` | niche, target_audience, tone_of_voice, special_rules |
| `script` | contexto completo |

### 8.3 Automation Config (por usuário)

**Arquivo**: `src/gpcg/domain/models.py` (linhas 315-344)

```python
class Automation(Base):
    config: dict (JSON)     # tudo: formato, legendas, transições, voice, fila
    upload_config: dict     # YouTube: privacy, category, auto-publish
```

Campos relevantes para coleta de ideias:

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `queue_mode` | "manual" \| "automatic" | Modo da fila |
| `idea_queue` | list[dict] | Fila de KIs |
| `max_queue_size` | int | Tamanho máximo (default: 10) |
| `auto_fill_queue` | bool | Auto-preencher fila |
| `content_scope` | "game" \| "franchise" \| "developer" | Escopo de gameplay |
| `creative_style` | str | Preset criativo (humor, storytelling, etc.) |

### 8.4 Presets Criativos

**Arquivo**: `src/gpcg/application/creative_engine.py` (linhas 56-164)

8 presets: `humor`, `absurd`, `sarcastic`, `storytelling`, `curiosity`, `nostalgia`, `dark_humor`, `high_energy`

Cada preset define: `energy`, `absurdity`, `sarcasm`, `informality`, `creativity`

---

## 9. Configuração Global (Feature Flags)

**Arquivo**: `src/gpcg/config.py`

| Flag | Default | Descrição |
|------|---------|-----------|
| `gpcg_content_intelligence_enabled` | True | Habilita coleta + scoring |
| `gpcg_rss_feed_url` | Google News | URL template para RSS por jogo |
| `gpcg_content_collection_interval_hours` | 6 | Intervalo entre coletas |
| `gpcg_content_min_editorial_score` | 0 | Score mínimo para usar |
| `gpcg_news_retention_days` | 30 | Dias antes de cleanup |
| `gpcg_curiosity_scoring_enabled` | True | CuriosityScorer para Facts |
| `gpcg_curiosity_min_threshold` | 40.0 | Score mínimo de curiosidade |
| `gpcg_story_finder_enabled` | True | StoryFinder no pipeline |
| `gpcg_story_finder_min_confidence` | 0.5 | Confiança mínima |
| `gpcg_editorial_planning_enabled` | True | EditorialPlanner |
| `gpcg_script_critic_enabled` | True | ScriptCritic |
| `gpcg_creative_engine_enabled` | True | CreativeEngine |
| `gpcg_cross_game_gameplay_enabled` | False | Gameplay cross-game |
| `gpcg_public_gameplay_fallback_enabled` | True | Fallback para gameplay público |

---

## 10. Referências e Influências

### 10.1 Obras Acadêmicas (Fundação Editorial)

O sistema editorial do GPCG é baseado no estudo de quatro obras fundamentais,
registradas em `docs/EDITORIAL_RESEARCH_JOURNAL.md`:

| Obra | Autor | Influência no GPCG |
|------|-------|---------------------|
| **The Psychology of Curiosity** | George Loewenstein | Information-gap theory; curva invertida U (familiarity); insight vs. trivia. Base do `CuriosityScorer` (5 sub-scores editoriais). |
| **Thinking, Fast and Slow** | Daniel Kahneman | System 1/2 (processabilidade); peak-end rule (payoff + retention); duration neglect (remoção do `retention_plan`); framing effect (`StoryConcept.frame`). |
| **Made to Stick** | Chip & Dan Heath | Framework SUCCESs; Maldição do Conhecimento (humanização); Teoria Velcro; curiosity gap vs. surpresa. |
| **Building a StoryBrand** | Donald Miller | SB7; espectador como herói; transformação vs. informação; stakes. |

### 10.2 Fontes de Dados Externas (Coleta)

| Fonte | Tipo | Status no GPCG |
|-------|------|----------------|
| **Google News RSS** | Notícias por jogo | ✅ Implementado (`content_collectors.py`) |
| **RSSHub** | Agregador (Kotaku, Reddit, etc.) | ✅ Implementado (10 feeds) |
| **Wikidata** (SPARQL) | Identidade canônica de jogos | Previsto (ARCHITECTURE_V2 §5.2) |
| **Wikipedia** (REST API) | Lore/descrição de jogos | Previsto (ARCHITECTURE_V2 §5.3) |
| **Steam Web API** | News, updates, reviews | Previsto (não implementado) |
| **IGDB/Twitch API** | Metadados + popularidade | Previsto (não implementado) |
| **Reddit API** (direto) | Discussões curatoriais | Previsto (via RSSHub atualmente) |

### 10.3 Ecossistema Bruno Integrations (Repos Irmãos)

O GPCG é parte de um ecossistema de serviços que se integram:

| Repo | Função no GPCG |
|------|----------------|
| `video-generate` | Renderização de vídeos (subprocess). TTS (XTTS), legendas, FFmpeg. |
| `ai-media-core` | Utilidades de TTS e mídia (chunking, merge WAV). |
| `google-integration` | OAuth YouTube + upload (BullMQ queue, retry). |
| `videoclip-generator` | Projeto irmão — padrão de subprocess + bridge copiado dele. |
| `portfolio-v2` | Design system do frontend (dark theme, teal accent). |
| `BI Identity` | SSO cookie-based (`bi_auth`). |
| `trivestia-nginx` | Reverse proxy na VPS (`/gpcg/` path prefix). |

### 10.4 Documentos de Arquitetura Internos

| Documento | Status | Descrição |
|-----------|--------|-----------|
| `ARCHITECTURE_EVOLUTION.md` | Proposta v1 (substituída) | Blueprint original: Game Registry, Knowledge Graph, 5 conectores |
| `ARCHITECTURE_V2.md` | **Definitivo** | V2: simplificação da v1 — 4 tabelas novas, 1 conector (RSS), strings vs tabelas |
| `ARCHITECTURE_V2_READINESS_REVIEW.md` | Revisão crítica | Análise de prontidão da V2 |
| `EDITORIAL_REFACTOR_PLAN_V2.md` | Plano técnico | CuriosityScorer, StoryFinder, Humanization, ScriptCritic V2 |
| `EDITORIAL_PRINCIPLES.md` | Pesquisa | Princípios editoriais vivos |
| `EDITORIAL_RESEARCH_JOURNAL.md` | Pesquisa | Estudo das 4 obras fundamentais |
| `EDITORIAL_MANIFESTO.md` | Identidade | "O GPCG existe para criar descobertas, não fatos" |
| `EDITORIAL_EVALUATION.md` | Metodologia | Avaliação editorial + 18 hipóteses |

---

## 11. Lacunas e Oportunidades de Expansão

### 11.1 Personalização por Canal (PRIORIDADE ALTA)

**Problema atual**: A coleta de RSS é **global** — todos os canais recebem as
mesmas fontes (IGN, GameSpot, r/games). O ChannelProfile existe mas **não influencia
a coleta**, apenas o planejamento editorial.

**Oportunidades**:

1. **Feeds RSS por canal**: Cada canal configura suas próprias fontes
   - Canal de FPS: r/Competitiveoverwatch, r/LearnCSGO, IGN FPS
   - Canal de retro: r/retrogaming, r/crtgaming, Nintendo Life
   - Canal de lore: r/truegaming, r/AskHistorians (games), Wikipedia

2. **Keywords de coleta por canal**: Além do nome do jogo, coletar por tópicos
   - Canal educativo: "how to", "guide", "tutorial", "explained"
   - Canal de humor: "funny", "fail", "moment", "clip"
   - Canal de lore: "story", "lore", "history", "behind the scenes"

3. **Scoring editorial por canal**: O `editorial_score` é genérico. Deveria
   considerar o ChannelProfile:
   - Canal educativo: favorecer facts/insights (familiarity + insight_quality)
   - Canal de humor: favorecer surpresa/absurdo (surprise_potential)
   - Canal de nostalgia: favorecer familiarity/nostalgia

4. **Fila auto-fill por canal**: O reconciliador preenche por `editorial_score`
   global. Deveria priorizar KIs que match o niche/tone do canal.

### 11.2 Fontes de Coleta (PRIORIDADE MÉDIA)

| Fonte | Esforço | Valor | Notas |
|-------|---------|-------|-------|
| **YouTube Trends API** | Médio | Alto | Trends por região/categoria |
| **Google Trends** | Baixo | Médio | Trends de busca |
| **Steam News** | Baixo | Médio | News/updates oficiais |
| **IGDB/Twitch API** | Médio | Médio | Metadados + popularidade |
| **Reddit (direto)** | Baixo | Médio | Sem depender de RSSHub |
| **Wikipedia API** | Baixo | Alto | Lore/curiosidades evergreen |
| **YouTube Analytics** | Alto | Alto | Performance history → próximos tópicos |
| **TikTok Creative Center** | Médio | Alto | Trends de áudio/hashtag |

### 11.3 Embeddings de KnowledgeItems (PRIORIDADE MÉDIA)

**Estado atual**: Embeddings de KIs são gerados mas **não usados**.

**Oportunidades**:

1. **Busca semântica na fila**: "quero vídeos sobre sobrevivência" → encontra KIs
   similares mesmo sem keyword match
2. **Clustering de ideias**: agrupar KIs similares para evitar redundância na fila
3. **Recomendação**: "baseado no que performou bem, aqui estão ideias similares"
4. **Deduplicação semântica**: detectar KIs que cobrem o mesmo tópico mesmo com
   texto diferente (além do content_hash)

### 11.4 Feedback Loop (PRIORIDADE ALTA)

**Problema atual**: O sistema **não aprende** com a performance dos vídeos
publicados. O `editorial_score` é estático (calculado na coleta, nunca revisado).

**Oportunidades**:

1. **YouTube Analytics integration**: puxar views, retention, CTR, likes
2. **Performance score**: combinar métricas do YouTube em um score de performance
3. **Feedback no scoring**: KIs similares a vídeos que performaram bem → score boost
4. **Feedback no editorial**: ChannelProfile ajustado com base no que funciona
5. **A/B testing editorial**: testar diferentes angles/hooks para mesmo tópico

### 11.5 Diversidade Editorial (PRIORIDADE MÉDIA)

**Problema atual**: O sistema pode produzir muitos vídeos sobre o mesmo jogo/tópico
se for o que tem mais gameplay disponível.

**Oportunidades**:

1. **Cooldown por tópico**: não produzir sobre mesmo jogo por N dias
2. **Rotação de formatos**: alternar generate_short / curiosity_short
3. **Rotação de creative_style**: variar humor/storytelling/curiosity
4. **Balanceamento de niche**: garantir mix de tópicos (não só FPS, por exemplo)

### 11.6 Coleta Proativa (PRIORIDADE BAIXA)

**Problema atual**: A coleta é reativa — busca RSS quando triggered.

**Oportunidades**:

1. **Monitoramento de trends**: coletar quando um tópico começa a trend
2. **Alertas de breaking news**: coletar imediatamente quando news relevante
3. **Scheduling inteligente**: coletar antes de horários de pico do canal
4. **Pre-fetch por gameplay**: se usuário tem gameplay de jogo X, coletar sobre X

---

## 12. Arquivos de Referência

### Núcleo do pipeline de ideias

| Componente | Arquivo |
|------------|---------|
| RSS collection | `src/gpcg/application/content_collectors.py` |
| KnowledgeItem service (scoring) | `src/gpcg/application/knowledge_item_service.py` |
| ContentPlanningService | `src/gpcg/application/content_planning_service.py` |
| EditorialStrategyService | `src/gpcg/application/editorial_strategy.py` |
| CuriosityScorer | `src/gpcg/application/curiosity_scorer.py` |
| StoryFinder | `src/gpcg/application/story_finder.py` |
| EditorialPlanner | `src/gpcg/application/editorial_planner.py` |
| CreativeEngine | `src/gpcg/application/creative_engine.py` |
| EmbeddingService | `src/gpcg/application/embedding_service.py` |
| GameplayRetriever | `src/gpcg/application/gameplay_retriever.py` |
| GameplayIndexService | `src/gpcg/application/gameplay_index_service.py` |

### Models

| Componente | Arquivo | Linhas |
|------------|---------|--------|
| KnowledgeItem | `src/gpcg/domain/models.py` | 1112-1180 |
| ChannelProfile | `src/gpcg/domain/models.py` | 959-1058 |
| Automation | `src/gpcg/domain/models.py` | 315-344 |
| GameplaySource | `src/gpcg/domain/models.py` | 443-538 |
| KIEmbedding | `src/gpcg/domain/models.py` | 1182-1198 |
| EventEmbedding | `src/gpcg/domain/models.py` | 1226-1242 |

### API

| Componente | Arquivo |
|------------|---------|
| KnowledgeItem routes | `src/gpcg/api/knowledge_item_routes.py` |
| Automation routes (fila, reconciliador) | `src/gpcg/api/automation_routes.py` |
| ChannelProfile routes | `src/gpcg/api/knowledge_routes.py` |
| Video routes (publish, metadata) | `src/gpcg/api/routes.py` |

### Config & Infra

| Componente | Arquivo |
|------------|---------|
| Config (feature flags) | `src/gpcg/config.py` |
| Visibility (hybrid pool) | `src/gpcg/domain/visibility.py` |
| Worker (content collect job) | `src/gpcg/application/worker.py` |

### Documentos de arquitetura e pesquisa editorial

| Componente | Caminho |
|------------|---------|
| Arquitetura V2 (definitiva) | `docs/ARCHITECTURE_V2.md` |
| Arquitetura v1 (proposta original) | `docs/ARCHITECTURE_EVOLUTION.md` |
| Plano editorial V2 | `docs/EDITORIAL_REFACTOR_PLAN_V2.md` |
| Princípios editoriais | `docs/EDITORIAL_PRINCIPLES.md` |
| Diário de pesquisa editorial | `docs/EDITORIAL_RESEARCH_JOURNAL.md` |
| Manifesto editorial | `docs/EDITORIAL_MANIFESTO.md` |
| Avaliação editorial | `docs/EDITORIAL_EVALUATION.md` |
| Consolidação editorial | `docs/EDITORIAL_CONSOLIDATION_REPORT.md` |

---

## 13. Próximos Passos Sugeridos

### Curto prazo (1-2 sprints)

1. **Feeds RSS por canal**: Adicionar campo `rss_feeds` no ChannelProfile,
   fallback para `GENERAL_GAMING_FEEDS` se vazio
2. **Scoring editorial por canal**: Passar `channel_profile.to_prompt_context()`
   para o `score_knowledge_item()` — LLM considera niche/tone ao pontuar
3. **Reconciliador por canal**: Ordenar por fit com ChannelProfile (similaridade
   semântica entre KI e niche/audience)

### Médio prazo (3-4 sprints)

4. **YouTube Analytics feedback loop**: Puxar métricas dos vídeos publicados,
   ajustar scoring de KIs similares
5. **Embeddings de KIs ativos**: Busca semântica na UI ("buscar ideias sobre X")
6. **Fontes novas**: Steam News API, Wikipedia API, YouTube Trends
7. **Diversidade editorial**: Cooldown por tópico, rotação de formatos/estilos

### Longo prazo (6+ sprints)

8. **Coleta proativa**: Monitoramento de trends, breaking news alerts
9. **A/B testing editorial**: Testar diferentes angles para mesmo tópico
10. **Multi-plataforma**: Cross-posting para TikTok/Instagram
11. **Migração para pgvector**: Embeddings em PostgreSQL para escala
