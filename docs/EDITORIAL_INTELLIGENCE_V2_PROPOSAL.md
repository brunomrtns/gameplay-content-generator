# GPCG — Editorial Intelligence V2

## Sistema de Inteligência Editorial — Arquitetura Revisada e Implementação

> **Status**: Aprovado para implementação
> **Versão**: 2.1 (revisão arquitetural)
> **Data**: 2026-08-06
> **Base**: `IDEA_COLLECTION_ARCHITECTURE.md` (v0.3.9)
> **Princípio**: O GPCG deixa de ser um agregador de notícias e passa a ser um
> sistema de inteligência editorial que age como o editor-chefe de cada canal.

---

## Sumário

1. [A Mudança Filosófica](#1-a-mudança-filosófica)
2. [Três Conceitos: Profile, Intent, Brief](#2-três-conceitos-profile-intent-brief)
3. [Análise Crítica da Arquitetura Atual](#3-análise-crítica-da-arquitetura-atual)
4. [Editorial Profile (quem o canal é)](#4-editorial-profile-quem-o-canal-é)
5. [Editorial Intent (o que produzir agora)](#5-editorial-intent-o-que-produzir-agora)
6. [Editorial Brief (como encontrar o conteúdo)](#6-editorial-brief-como-encontrar-o-conteúdo)
7. [Search Templates como Componentes de Primeira Classe](#7-search-templates-como-componentes-de-primeira-classe)
8. [Gameplay como Direcionador de Coleta](#8-gameplay-como-direcionador-de-coleta)
9. [Coleta Orientada por Objetivo](#9-coleta-orientada-por-objetivo)
10. [Lifecycle Inteligente](#10-lifecycle-inteligente)
11. [Scoring Composto (3 Camadas)](#11-scoring-composto-3-camadas)
12. [Diversidade Editorial](#12-diversidade-editorial)
13. [Feedback Loop e Aprendizado Contínuo](#13-feedback-loop-e-aprendizado-contínuo)
14. [Embeddings de KnowledgeItems](#14-embeddings-de-knowledgeitems)
15. [Modelagem de Dados](#15-modelagem-de-dados)
16. [Novos Componentes](#16-novos-componentes)
17. [Impacto na Experiência do Usuário](#17-impacto-na-experiência-do-usuário)
18. [Riscos e Mitigações](#18-riscos-e-mitigações)
19. [Prioridades de Implementação](#19-prioridades-de-implementação)
20. [O que NÃO Propomos](#20-o-que-não-propomos)

---

## 1. A Mudança Filosófica

O sistema deixa de perguntar **"Quais conteúdos existem?"** e passa a perguntar
**"Quais vídeos fazem sentido para ESTE canal produzir neste momento?"**

```
Pipeline atual (source-driven):
    Fontes RSS → Coletar tudo → Score genérico → Filtrar por canal → Fila

Pipeline V2 (channel-driven):
    Editorial Profile → Editorial Intent → Editorial Brief
    → Busca dirigida → Scoring composto → Fila
    → Feedback → Profile evolui
```

A identidade do canal deixa de ser um filtro tardio e passa a ser o **input
primário** que drive toda a coleta.

---

## 2. Três Conceitos: Profile, Intent, Brief

A V2.1 separa claramente três conceitos que na proposta anterior estavam
fundidos no Editorial Brief.

### 2.1 Editorial Profile — "Quem o canal é"

- **Persistido** no banco (tabela `channel_profiles` evoluída)
- **Objeto vivo** que evolui continuamente
- Contém configurações do usuário **e** conhecimento aprendido
- Inputs: niche, tone, audience, content_type_affinity, editorial_keywords,
  custom_feeds, gameplay_driven_collection, diversity_strictness,
  learned_preferences (de feedback), production_history_summary

### 2.2 Editorial Intent — "O que produzir agora"

- **Temporário** — recalculado a cada ciclo de coleta
- **Não persistido** — é computado em memória
- Responde: "O que queremos produzir agora?"
- Inputs: Profile + gameplay inventory + recent videos + queue state +
  diversity constraints + time context
- Outputs: metas de coleta (ex: "preciso de 8 curiosidades, evitar GTA esta
  semana, priorizar Bully, preencher com evergreen")

### 2.3 Editorial Brief — "Como encontrar o conteúdo"

- **Temporário** — gerado a partir de Profile + Intent
- **Não persistido** — é computado em memória
- Responde: "Como vamos encontrar esse conteúdo?"
- Outputs: feeds a consultar, queries expandidas, search templates a aplicar,
  jogos prioritários, keywords, cooldowns, tipos de conteúdo, estratégias
  de busca, metas de coleta (quota por tipo)

### 2.4 Fluxo dos Três Conceitos

```
┌─────────────────────────────────────────────────────────────────────┐
│                     PIPELINE V2 (CHANNEL-DRIVEN)                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  QUEM É              O QUE QUER         COMO BUSCAR                  │
│  ┌──────────┐        ┌──────────┐       ┌──────────────┐            │
│  │ Editorial│──→     │ Editorial│──→    │ Editorial    │            │
│  │ Profile  │  build │ Intent   │ build │ Brief        │            │
│  │ (persist)│  intent│ (temp)   │ brief │ (temp)       │            │
│  └──────────┘        └──────────┘       └──────┬───────┘            │
│       ↑                                        │                     │
│       │                                ┌───────▼────────┐           │
│       │                                │  Coleta        │           │
│       │                                │  Dirigida      │           │
│       │                                │  (goal-based)  │           │
│       │                                └───────┬────────┘           │
│       │                                        │                     │
│       │                                ┌───────▼────────┐           │
│       │                                │  KIs coletados │           │
│       │                                └───────┬────────┘           │
│       │                                        │                     │
│  ┌────┴──────┐     ┌──────────┐       ┌───────▼────────┐           │
│  │ Feedback  │←─── │ Reconcil.│←──────│ Composite      │           │
│  │ Loop      │    │ + Fila   │       │ Scorer         │           │
│  └───────────┘    └──────────┘       └────────────────┘            │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.5 Por que a separação

| Sem separação (V2.0) | Com separação (V2.1) |
|----------------------|----------------------|
| Brief é god-object com identidade + objetivo + execução | Cada conceito tem responsabilidade única |
| Mudar "o que buscar" requer mexer em "quem o canal é" | Intent muda a cada ciclo sem tocar Profile |
| Feedback alimenta Brief (temporário) → sinal perdido | Feedback alimenta Profile (persistido) → sinal acumula |
| Difcil testar isoladamente | Profile, Intent, Brief são testáveis independentemente |

---

## 3. Análise Crítica da Arquitetura Atual

### 3.1 Pontos Fortes (mantidos)

- **Pipeline editorial em estágios** (StoryFinder → EditorialPlanner →
  CreativeEngine → ScriptCritic) — maduro, baseado em pesquisa acadêmica
- **Gate determinístico** (regex para clickbait/promoção/rumor) — barato e eficaz
- **Modelo híbrido de propriedade** (user_id NULL + is_public) — bem desenhado
- **Feature flags em todo lugar** — permite rollout gradual
- **Embeddings já gerados** para KIs — infraestrutura pronta
- **CuriosityScorer com base teórica** — 5 sub-scores justificados em Loewenstein
- **Reconciliador com exclusões** — evita duplicação de KIs com jobs ativos

### 3.2 Limitações Fundamentais (endereçadas na V2)

| ID | Limitação | Causa | Impacto |
|----|-----------|-------|---------|
| L1 | Coleta é source-driven | `GENERAL_GAMING_FEEDS` hardcoded | Todos os canais recebem as mesmas ideias |
| L2 | ChannelProfile é free-text | Campos são strings não-estruturadas | LLM interpreta mas coleta não usa |
| L3 | Score é absoluto | `editorial_score` unidimensional | KI excelente para canal A é inútil para canal B, mas mesmo score |
| L4 | Busca por nome de jogo apenas | Google News query = `canonical_name` | Canal de curiosidades não encontra curiosidades via RSS |
| L5 | Lifecycle binário | `gpcg_news_retention_days = 30` flat | Notícia velha polui por 30 dias; lore é deletado em 30 dias |
| L6 | Gameplay não influencia coleta | Coleta itera sobre jogos mas não usa inventory como sinal | Sistema busca igualmente para jogos com 50 clips e 0 clips |
| L7 | Sistema não aprende | `editorial_score` é estático | Sistema repete os mesmos erros |
| L8 | Sem diversidade | Apenas `used` previne repetição | Canal pode ficar preso em um jogo/tópico por semanas |

---

## 4. Editorial Profile (quem o canal é)

### 4.1 Diagnóstico

O `ChannelProfile` atual tem 7 campos free-text. Suficiente para prompts LLM,
insuficiente para dirigir coleta determinística.

### 4.2 Proposta: Campos estruturados + free-text mantido

```python
class ChannelProfile(Base):
    # === Campos existentes (mantidos para prompts LLM) ===
    channel_description: str
    niche: str
    target_audience: str
    tone_of_voice: str
    narrative_style: str
    content_goals: str
    special_rules: str

    # === Campos novos estruturados (dirigem coleta determinística) ===
    content_type_affinity: dict   # {news: 0.2, curiosity: 0.8, lore: 0.9, fact: 0.3}
    editorial_keywords: list      # ["hidden", "secrets", "beta", "unused", ...]
    custom_feeds: list            # URLs de feeds RSS específicos do canal
    gameplay_driven_collection: bool  # gameplay inventory drive a coleta
    diversity_strictness: float   # 0.0 (sem cooldown) a 1.0 (agressivo)

    # === Campos aprendidos (populados por feedback, não pelo usuário) ===
    learned_preferences: dict     # {preferred_games: [...], avoided_topics: [...], ...}
    production_history_summary: dict  # {total_videos: N, top_games: [...], avg_performance: X}
```

### 4.3 Justificativa por campo

| Campo | Tipo | Populado por | Default | Resolve |
|-------|------|-------------|---------|---------|
| `content_type_affinity` | dict | Usuário (presets) + feedback | Neutro (0.5 todos) | L2, L3 |
| `editorial_keywords` | list | Usuário (presets) + custom | `[]` (fallback nome do jogo) | L4 |
| `custom_feeds` | list | Usuário | `[]` (fallback GENERAL_GAMING_FEEDS) | L1 |
| `gameplay_driven_collection` | bool | Usuário | `True` | L6 |
| `diversity_strictness` | float | Usuário | `0.5` | L8 |
| `learned_preferences` | dict | Feedback loop (automático) | `{}` | L7 |
| `production_history_summary` | dict | Feedback loop (automático) | `{}` | L7 |

### 4.4 Presets Editoriais

Para simplificar onboarding, 5 presets populam os campos estruturados:

| Preset | content_type_affinity | editorial_keywords | custom_feeds |
|--------|----------------------|--------------------|--------------|
| **Curiosidades** | `{curiosity: 0.9, lore: 0.6, news: 0.1, fact: 0.4}` | hidden, secrets, beta, unused, cancelled, developer, interview, easter egg, glitch, mystery | r/truegaming, r/patientgamers |
| **Notícias** | `{news: 0.9, curiosity: 0.3, lore: 0.1, fact: 0.2}` | update, patch, release, announcement, trailer, delay, review, dlc | IGN, GameSpot, r/games |
| **Lore** | `{lore: 0.9, curiosity: 0.7, news: 0.1, fact: 0.5}` | story, lore, history, behind the scenes, development, documentary, timeline, explained | r/truegaming, r/patientgamers |
| **Nostalgia** | `{curiosity: 0.7, lore: 0.8, news: 0.2, fact: 0.4}` | anniversary, retrospective, evolution, history, classic, retro, nostalgia | r/retrogaming, r/crtgaming |
| **Educacional** | `{fact: 0.8, curiosity: 0.6, lore: 0.5, news: 0.3}` | explained, analysis, guide, tutorial, how to, mechanics, design | r/truegaming, r/gamedesign |

### 4.5 O que NÃO vai no Profile

- **`scoring_weights`**: Derivado de `content_type_affinity`, não configurado
- **`target_games`**: Dinâmico — depende de gameplay + cooldown. Vive no Intent
- **`cooldown_days`**: Derivado de `diversity_strictness`. Parâmetro demais

---

## 5. Editorial Intent (o que produzir agora)

### 5.1 Conceito

O Editorial Intent é computado no início de cada ciclo de coleta. Ele traduz
o Profile (estático) + contexto dinâmico (gameplay, histórico, fila) em
**metas concretas de coleta**.

### 5.2 Estrutura

```python
@dataclass
class EditorialIntent:
    """O que o canal precisa produzir agora."""

    # Metas de coleta por tipo de conteúdo
    collection_targets: dict[str, int]
    # ex: {"curiosity": 8, "lore": 5, "news": 2}

    # Jogos prioritários (com reason para auditoria)
    priority_games: list[GameTarget]
    # ex: [{game_id: 6, name: "Bully", priority: 0.9, reason: "50 clips disponíveis"}]

    # Jogos a evitar (cooldown)
    cooldown_games: dict[int, int]
    # ex: {12: 14}  # RDR2: evitar por 14 dias

    # Estratégia de preenchimento
    fill_strategy: str  # "evergreen_fallback" | "news_priority" | "balanced"

    # Contexto temporal
    time_context: str   # "breaking_news_window" | "normal" | "evergreen_fill"

    # Diversidade
    format_rotation: str  # "prefer_curiosity_short" | "prefer_generate_short" | "balanced"
```

### 5.3 Como é Computado

```python
class EditorialIntentBuilder:
    def build(self, session, user_id, profile) -> EditorialIntent:
        gameplay_inventory = self._get_gameplay_inventory(session, user_id)
        recent_videos = self._get_recent_videos(session, user_id, limit=10)
        queue_state = self._get_queue_state(session, user_id)

        priority_games = self._compute_priority_games(
            gameplay_inventory, recent_videos, profile
        )
        cooldown_games = self._compute_cooldowns(
            recent_videos, profile.diversity_strictness
        )
        collection_targets = self._compute_targets(
            profile, queue_state, priority_games
        )
        fill_strategy = self._determine_fill_strategy(queue_state, profile)
        format_rotation = self._determine_format_rotation(recent_videos)

        return EditorialIntent(
            collection_targets=collection_targets,
            priority_games=priority_games,
            cooldown_games=cooldown_games,
            fill_strategy=fill_strategy,
            time_context="normal",
            format_rotation=format_rotation,
        )
```

### 5.4 Exemplo

**Canal**: "Retro Lore" — preset Curiosidades
**Gameplay**: Bully (50 clips), GTA IV (80 clips), RDR2 (120 clips)
**Últimos 5 vídeos**: 3x RDR2, 1x GTA IV, 1x Bully
**Fila atual**: 2 KIs (1 curiosidade, 1 lore)

```python
EditorialIntent(
    collection_targets={"curiosity": 8, "lore": 5, "news": 1, "fact": 2},
    priority_games=[
        GameTarget(game_id=6, name="Bully", priority=0.9, reason="50 clips, coberto 1x"),
        GameTarget(game_id=8, name="GTA IV", priority=0.7, reason="80 clips, coberto 1x"),
        # RDR2 suprimido por cooldown
    ],
    cooldown_games={12: 14},  # RDR2: 14 dias
    fill_strategy="balanced",
    time_context="normal",
    format_rotation="prefer_curiosity_short",  # últimos 3 foram generate_short
)
```

---

## 6. Editorial Brief (como encontrar o conteúdo)

### 6.1 Conceito

O Brief traduz o Intent em **instruções executáveis de coleta**. É o "plano
de busca" do ciclo.

### 6.2 Estrutura

```python
@dataclass
class EditorialBrief:
    """Como a coleta será executada neste ciclo."""

    # Feeds a consultar (canal-specific + global fallback)
    feeds: list[FeedSpec]

    # Queries expandidas (jogo + keywords editoriais)
    search_queries: list[SearchQuery]

    # Search templates ativos para este ciclo
    active_templates: list[str]  # ["curiosity", "lore"]

    # Jogos prioritários (herdado do Intent)
    target_games: list[GameTarget]

    # Cooldowns (herdado do Intent)
    cooldown_games: dict[int, int]

    # Metas de coleta (herdado do Intent)
    collection_targets: dict[str, int]

    # Pesos de scoring para este canal
    scoring_weights: dict[str, float]

    # Configurações de coleta
    max_queries_per_game: int
    max_total_queries: int
```

### 6.3 Como é Computado

```python
class EditorialBriefBuilder:
    def build(self, session, user_id, profile, intent) -> EditorialBrief:
        # 1. Feeds: custom_feeds do profile + global fallback
        feeds = self._resolve_feeds(profile)

        # 2. Search queries: expandir jogos prioritários com templates
        active_templates = self._select_templates(intent.collection_targets)
        search_queries = self._expand_queries(
            intent.priority_games,
            active_templates,
            profile.editorial_keywords,
        )

        # 3. Scoring weights: derivar de content_type_affinity
        scoring_weights = self._derive_scoring_weights(profile.content_type_affinity)

        return EditorialBrief(
            feeds=feeds,
            search_queries=search_queries,
            active_templates=active_templates,
            target_games=intent.priority_games,
            cooldown_games=intent.cooldown_games,
            collection_targets=intent.collection_targets,
            scoring_weights=scoring_weights,
            max_queries_per_game=5,
            max_total_queries=30,
        )
```

---

## 7. Search Templates como Componentes de Primeira Classe

### 7.1 Conceito

Search Templates são **estratégias editoriais de busca**, não apenas listas
de palavras. Cada template representa uma intenção editorial.

### 7.2 Estrutura

```python
@dataclass(frozen=True)
class SearchTemplate:
    """Estratégia editorial de busca para um tipo de conteúdo."""

    name: str               # "curiosity", "news", "lore", "nostalgia"
    item_type: str          # KnowledgeItemType correspondente
    keywords: tuple[str, ...]  # keywords para combinar com nome do jogo
    description: str        # descrição editorial da estratégia
    decay_category: str     # "fast" | "medium" | "evergreen" — para lifecycle


SEARCH_TEMPLATES: dict[str, SearchTemplate] = {
    "curiosity": SearchTemplate(
        name="curiosity",
        item_type="curiosity",
        keywords=(
            "hidden", "secrets", "beta", "unused", "cancelled",
            "developer", "interview", "easter egg", "glitch",
            "mystery", "cut content", "unused content",
        ),
        description="Curiosidades e segredos ocultos dos jogos",
        decay_category="evergreen",
    ),
    "news": SearchTemplate(
        name="news",
        item_type="news",
        keywords=(
            "update", "patch", "release", "announcement", "trailer",
            "delay", "review", "dlc", "expansion", "remaster",
        ),
        description="Notícias e atualizações atuais",
        decay_category="fast",
    ),
    "lore": SearchTemplate(
        name="lore",
        item_type="lore",
        keywords=(
            "story", "lore", "history", "behind the scenes",
            "development", "documentary", "timeline", "explained",
            "making of", "design",
        ),
        description="História e narrativa dos jogos",
        decay_category="evergreen",
    ),
    "nostalgia": SearchTemplate(
        name="nostalgia",
        item_type="curiosity",
        keywords=(
            "anniversary", "retrospective", "evolution", "history",
            "classic", "retro", "nostalgia", "then vs now",
        ),
        description="Nostalgia e retrospectiva de jogos clássicos",
        decay_category="evergreen",
    ),
    "fact": SearchTemplate(
        name="fact",
        item_type="fact",
        keywords=(
            "trivia", "fact", "did you know", "detail", "analysis",
            "mechanics", "breakdown",
        ),
        description="Fatos e análises detalhadas",
        decay_category="medium",
    ),
}
```

### 7.3 Extensibilidade

Templates são um dict imutável. Para adicionar um novo template:

```python
# Em search_templates.py
SEARCH_TEMPLATES["speedrun"] = SearchTemplate(
    name="speedrun",
    item_type="fact",
    keywords=("speedrun", "world record", "any%", "glitchless", "tas"),
    description="Recordes e técnicas de speedrun",
    decay_category="medium",
)
```

Usuários avançados podem adicionar keywords customizadas via
`ChannelProfile.editorial_keywords` — estas são **mescladas** com os
keywords do template ativo, não substituem.

### 7.4 Seleção de Templates

O Brief seleciona templates ativos baseado em `collection_targets` do Intent:

```python
def _select_templates(self, collection_targets: dict) -> list[str]:
    """Seleciona templates onde a meta de coleta > 0."""
    return [
        name for name, target in collection_targets.items()
        if target > 0 and name in SEARCH_TEMPLATES
    ]
```

### 7.5 Expansão de Queries

```python
def _expand_queries(self, games, templates, custom_keywords):
    queries = []
    for game in games:
        for template_name in templates:
            template = SEARCH_TEMPLATES[template_name]
            # Mesclar keywords do template + custom do canal
            all_keywords = list(template.keywords)
            for ck in custom_keywords:
                if ck not in all_keywords:
                    all_keywords.append(ck)
            # Top N keywords por jogo (limite de volume)
            for kw in all_keywords[:self.max_queries_per_game]:
                queries.append(SearchQuery(
                    text=f"{game.name} {kw}",
                    game_id=game.game_id,
                    template_name=template_name,
                    item_type=template.item_type,
                ))
    return queries[:self.max_total_queries]
```

---

## 8. Gameplay como Direcionador de Coleta

### 8.1 Conceito

Gameplay disponível não é apenas um fator de score — é um **direcionador
primário** da coleta. O investimento computacional da coleta (número de
queries, número de feeds) é proporcional à disponibilidade de gameplay.

### 8.2 Como funciona

1. **Inventory scan**: Brief consulta `GameplaySource` do canal
   - Quais jogos têm gameplay pronto (`processing_status = ready`)?
   - Quantos clips por jogo?
   - Duração total por jogo?

2. **Priority computation**: Jogos com mais clips e menos cobertura recente
   recebem prioridade maior

3. **Query allocation**: Mais queries alocadas para jogos de alta prioridade
   ```python
   queries_for_game = base_queries * priority * (1 + log(clips_count))
   ```
   - Bully (50 clips, priority 0.9): 5 queries
   - GTA IV (80 clips, priority 0.7): 4 queries
   - RDR2 (120 clips, priority 0.3, cooldown): 0 queries

4. **Se `gameplay_driven_collection = False`**: Brief busca para todos os
   jogos do catálogo igualmente (comportamento atual)

### 8.3 Cross-game expansion

Se `content_scope = franchise` ou `developer`, Brief expande `target_games`:
- Gameplay de RDR2 → também buscar para GTA IV (mesmo developer: Rockstar)
- Gameplay de Bully → também buscar para Manhunt (mesmo developer)

Isso já existe no `GameplayRetriever` para clips. A V2 estende para coleta.

### 8.4 Por que isso é editorialmente correto

Um editor-chefe olha para o que tem na gaveta antes de decidir o que produzir.
Se tem 50 clips de Bully e 0 de Minecraft, não faz sentido buscar ideias
sobre Minecraft. O sistema atual busca igualmente — desperdício computacional
e editorial.

---

## 9. Coleta Orientada por Objetivo

### 9.1 Conceito

A coleta não consome todas as fontes indiscriminadamente. Ela tem **metas**
por tipo de conteúdo. Se a meta foi atingida, a coleta pode encerrar
antecipadamente.

### 9.2 Como funciona

```python
class GoalOrientedCollector:
    def collect(self, session, brief, llm) -> CollectionResult:
        collected = defaultdict(list)  # {item_type: [KI, ...]}
        remaining = dict(brief.collection_targets)  # cópia

        # Fase 1: Coletar de feeds (RSS)
        for feed in brief.feeds:
            if self._all_targets_met(remaining):
                break  # encerrar antecipadamente

            items = collect_rss_items(feed.url, ...)
            for item in items:
                ki = self._create_ki(item, brief)
                if ki and remaining.get(ki.item_type, 0) > 0:
                    collected[ki.item_type].append(ki)
                    remaining[ki.item_type] -= 1

        # Fase 2: Coletar de search queries (Google News)
        for query in brief.search_queries:
            if self._all_targets_met(remaining):
                break

            items = collect_google_news(query.text, ...)
            for item in items:
                ki = self._create_ki(item, query, brief)
                if ki and remaining.get(ki.item_type, 0) > 0:
                    collected[ki.item_type].append(ki)
                    remaining[ki.item_type] -= 1

        # Fase 3: Scoring dos KIs coletados
        for item_type, kis in collected.items():
            for ki in kis:
                score_knowledge_item(ki, llm)

        return CollectionResult(
            collected=collected,
            remaining=remaining,
            total=sum(len(v) for v in collected.values()),
        )
```

### 9.3 Benefícios

- **Reduz processamento**: se meta de curiosidades é 8 e encontramos 10,
  paramos de buscar curiosidades
- **Reduz custo LLM**: menos KIs = menos scoring
- **Respeita prioridades**: se meta de lore é 5 e meta de news é 1, sistema
  investe mais em lore
- **Fallback**: se metas não forem atingidas após todas as fontes, sistema
  registra deficit (útil para auditoria)

### 9.4 Quando NÃO encerrar antecipadamente

- Se `remaining` ainda tem tipos não-met (ex: queremos 8 curiosidades mas
  só encontramos 3), continuamos buscando
- Se todas as fontes foram consultadas e metas não atingidas, registramos
  o deficit e seguimos (não bloqueamos o pipeline)

---

## 10. Lifecycle Inteligente

### 10.1 Diagnóstico

Atual: `gpcg_news_retention_days = 30` — tudo deletado em 30 dias.
Notícia perde relevância em 3 dias. Lore é evergreen. Tratar igual é
editorialmente incorreto.

### 10.2 Decay por item_type

```python
LIFECYCLE_DECAY = {
    "news": {
        "half_life_days": 2,        # perde 50% da relevância em 2 dias
        "archive_after_days": 14,
    },
    "curiosity": {
        "half_life_days": 90,
        "archive_after_days": 365,
    },
    "lore": {
        "half_life_days": None,     # evergreen — não decaí
        "archive_after_days": None,
    },
    "fact": {
        "half_life_days": 180,
        "archive_after_days": 365,
    },
}
```

### 10.3 Freshness Score

```python
freshness = 0.5 ** (age_days / half_life_days)
```
- news com 2 dias → 0.5
- curiosity com 90 dias → 0.5
- lore → sempre 1.0

### 10.4 Lifecycle Stage

| Stage | Condição | Visível na fila? |
|-------|----------|-----------------|
| `fresh` | freshness > 0.3 | Sim |
| `aging` | freshness ≤ 0.3 e < archive_threshold | Sim (com penalty) |
| `archived` | age > archive_after_days | Não |
| `used` | virou vídeo | Não |
| `rejected` | rejeitado | Não |

`lifecycle_stage` é **ortogonal** a `status` — um KI pode ser `status=fresh`
e `lifecycle_stage=aging` ao mesmo tempo.

---

## 11. Scoring Composto (3 Camadas)

### 11.1 Nomenclatura Revisada

```
Score Final = Editorial Quality × Production Fit × Editorial Timing
```

| Camada | Nome | Mede | Custo |
|--------|------|------|-------|
| Layer 1 | **Editorial Quality** | Qualidade intrínseca da ideia | 1 chamada LLM (global, 1x por KI) |
| Layer 2 | **Production Fit** | Capacidade real de produzir este vídeo para este canal | Zero LLM (DB + embeddings) |
| Layer 3 | **Editorial Timing** | Momento editorial certo | Zero LLM (cálculo temporal) |

### 11.2 Layer 1: Editorial Quality (intrínseco, global)

**O que mede**: Quão boa é a ideia em si, independente de canal.

**Como**: O sistema atual já faz isso — 5 dimensões via LLM (curiosity,
surprise, retention, familiarity, insight). Mantido como está.

**Quando**: Uma vez por KI, na coleta. Global (não per-canal).

**Output**: `editorial_score` (0-100), normalizado para 0.0-1.0 no composto.

### 11.3 Layer 2: Production Fit (relacional, per-canal)

**O que mede**: Capacidade real de produzir este vídeo para este canal.

**Componentes**:

| Componente | Cálculo | Peso | Justificativa |
|------------|---------|------|---------------|
| `gameplay_availability` | 1.0 se gameplay pronto, 0.5 se mapeado, 0.0 se sem | 0.40 | Sem gameplay, não há vídeo. Constraint mais duro. |
| `content_type_affinity` | Peso do item_type no profile do canal | 0.25 | Relevância editorial do tipo |
| `channel_affinity` | Cosine similarity(embedding KI, embedding canal) | 0.20 | Fit semântico com identidade do canal |
| `source_authority` | Tier list de fontes | 0.15 | Tiebreaker entre KIs similares |

**Fórmula**:
```
fit = (gameplay_availability * 0.40) +
      (content_type_affinity * 0.25) +
      (channel_affinity * 0.20) +
      (source_authority * 0.15)
```

**Custo**: Zero LLM. Queries DB + 1 dot product de embeddings.

### 11.4 Layer 3: Editorial Timing (temporal, per-canal)

**O que mede**: É o momento editorial certo para esta ideia agora?

**Componentes**:

| Componente | Cálculo |
|------------|---------|
| `freshness` | `0.5 ^ (age_days / half_life_days)` por item_type |
| `diversity_penalty` | 1.0 se jogo não coberto recentemente, 0.3 se em cooldown |

**Fórmula**:
```
timing = freshness * diversity_penalty
```

**Custo**: Zero LLM. Cálculo de data + query de history.

### 11.5 Score Final

```
final_score = (editorial_score / 100) * fit * timing
```

Range: 0.0 a 1.0. Reconciliador ordena por `final_score` desc.

### 11.6 Por que multiplicativo

Um KI com qualidade excelente (90) mas sem gameplay (fit = 0) deve ter
score 0, não 45. Multiplicação zerifica naturalmente. Adição permite
"compensar" falta de fit com qualidade alta — editorialmente incorreto.

### 11.7 Exemplo

**KI**: "Bully hidden secrets — chemistry lab easter egg"
- editorial_score: 78, item_type: curiosity, game_id: 6 (Bully)
- published_at: 5 dias atrás, source: Reddit

**Canal**: "Retro Lore" — tem gameplay de Bully, affinity curiosity=0.8,
último vídeo sobre Bully: 12 dias atrás

```
Layer 1 (Editorial Quality): 78/100 = 0.78

Layer 2 (Production Fit):
  gameplay_availability = 1.0 (50 clips prontos)
  content_type_affinity = 0.8 (curiosity)
  channel_affinity = 0.85 (embedding match)
  source_authority = 0.6 (Reddit)
  fit = (1.0*0.40) + (0.8*0.25) + (0.85*0.20) + (0.6*0.15) = 0.86

Layer 3 (Editorial Timing):
  freshness = 0.5^(5/90) = 0.96 (curiosity decai lento)
  diversity_penalty = 1.0 (Bully não coberto há 12 dias)
  timing = 0.96 * 1.0 = 0.96

Final = 0.78 * 0.86 * 0.96 = 0.64
```

**Comparação**: KI sobre "Minecraft speedrun" para o mesmo canal:
```
Layer 1: 0.85 (qualidade alta)
Layer 2: gameplay_availability = 0.0 (sem Minecraft) → fit = 0.26
Layer 3: 0.90
Final = 0.85 * 0.26 * 0.90 = 0.20
```

Bully (0.64) supera Minecraft (0.20) apesar de qualidade menor, porque
production fit é muito maior. **Comportamento editorial correto.**

---

## 12. Diversidade Editorial

### 12.1 Cooldown por jogo

Brief computa cooldowns baseado em cobertura recente:

```python
def _compute_cooldowns(self, recent_videos, strictness):
    cooldowns = {}
    coverage = Counter(v.game_id for v in recent_videos if v.game_id)
    threshold = max(1, int(3 - strictness * 2))  # strictness 1.0 → 1, 0.0 → 3
    cooldown_days = int(7 + strictness * 23)     # strictness 1.0 → 30, 0.0 → 7

    for game_id, count in coverage.items():
        if count >= threshold:
            cooldowns[game_id] = cooldown_days
    return cooldowns
```

### 12.2 Rotação de formatos

Brief rastreia proporção `generate_short` vs `curiosity_short` nos últimos
vídeos. Se > 70% de um tipo, prioriza o outro.

### 12.3 Rotação de creative_style

Brief rastreia `creative_style` dos últimos vídeos. Se 4 dos últimos 5
foram "humor", sinaliza para variar.

### 12.4 Exploration factor

Para evitar filter bubble, 10% das KIs na fila são aleatórias (fora do
niche). Configurável via `gpcg_editorial_exploration_factor` (default: 0.1).

---

## 13. Feedback Loop e Aprendizado Contínuo

### 13.1 Tipos de Aprendizado

A arquitetura prevê múltiplos sinais de feedback, todos alimentando o
**Editorial Profile** (persistido):

| Sinal | Tipo | Quando | Como alimenta Profile |
|-------|------|--------|----------------------|
| **YouTube Analytics** | Assíncrono, opt-in | Job diário | `production_history_summary` + boost em KIs similares |
| **Rejeição manual** | Síncrono, implícito | Usuário rejeita KI | `learned_preferences.avoided_topics` + penalty em similares |
| **Ideias favoritas** | Síncrono, explícito | Usuário marca favorita | `learned_preferences.preferred_topics` + boost em similares |
| **Adição manual** | Síncrono, implícito | Usuário cria KI | `content_type_affinity` reforçado para o item_type criado |
| **Feedback explícito** | Síncrono, explícito | Usuário dá rating em vídeo | `production_history_summary` + ajuste de scoring_weights |
| **Histórico de produção** | Assíncrono, automático | A cada vídeo produzido | `production_history_summary` atualizado |

### 13.2 Implementação por fase

| Sinal | Fase | Complexidade |
|-------|------|-------------|
| Rejeição manual | Fase 4 | Baixa |
| Adição manual | Fase 4 | Baixa |
| Histórico de produção | Fase 4 | Baixa |
| YouTube Analytics | Futuro (V2.2) | Média |
| Ideias favoritas | Futuro (V2.2) | Baixa |
| Feedback explícito | Futuro (V2.3) | Média |

### 13.3 Propagação via Embeddings

Todos os sinais de feedback usam embeddings para propagar:

```python
def propagate_feedback(session, source_ki_id, signal_value, signal_type):
    """Propaga feedback para KIs semanticamente similares."""
    source_embedding = get_knowledge_item_embedding(session, source_ki_id)
    if not source_embedding:
        return

    all_kis = session.query(KnowledgeItem).filter(
        KnowledgeItem.status == KnowledgeItemStatus.fresh.value
    ).all()

    for ki in all_kis:
        if ki.id == source_ki_id:
            continue
        ki_embedding = get_knowledge_item_embedding(session, ki.id)
        if not ki_embedding:
            continue
        similarity = cosine_similarity(source_embedding, ki_embedding)
        if similarity > FEEDBACK_SIMILARITY_THRESHOLD:  # 0.85
            adjustment = signal_value * (similarity - 0.5) * FEEDBACK_BOOST_FACTOR
            ki.editorial_score = max(0, min(100, ki.editorial_score + adjustment))
```

### 13.4 Tabela: editorial_signals

```sql
CREATE TABLE editorial_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ki_id           INTEGER REFERENCES knowledge_items(id) ON DELETE CASCADE,
    user_id         INTEGER REFERENCES users(id),
    signal_type     VARCHAR(50),   -- "rejection_penalty", "manual_add_boost", "performance_boost"
    signal_value    FLOAT,         -- ajuste aplicado ao score
    source_ki_id    INTEGER,       -- KI que originou o sinal (para propagação)
    source_video_id INTEGER,       -- vídeo que originou o sinal (se analytics)
    created_at      DATETIME
);
```

---

## 14. Embeddings de KnowledgeItems

### 14.1 Estado atual

Embeddings de KIs são gerados mas não usados. Capital parado.

### 14.2 Usos na V2

| Uso | Componente | Custo |
|-----|-----------|-------|
| Channel affinity no Production Fit | CompositeScorer | 1 dot product por KI |
| Deduplicação semântica no reconciliador | Reconciler | N dot products (N ≤ 10) |
| Propagação de feedback | FeedbackLoop | M dot products (M = pool size) |

### 14.3 Channel Profile embedding

Novo: gerar embedding de `niche + channel_description + content_goals` do
ChannelProfile. Armazenar em `channel_profile_embeddings` (mesmo padrão de
`knowledge_item_embeddings`).

Usado no cálculo de `channel_affinity` no Layer 2.

---

## 15. Modelagem de Dados

### 15.1 Tabelas novas

#### `video_performance` (futuro — Fase 4+)

```sql
CREATE TABLE video_performance (
    video_id        INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    views           INTEGER DEFAULT 0,
    retention_avg   FLOAT,
    ctr             FLOAT,
    likes           INTEGER DEFAULT 0,
    comments        INTEGER DEFAULT 0,
    performance_score FLOAT,
    fetched_at      DATETIME
);
```

#### `editorial_signals`

```sql
CREATE TABLE editorial_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ki_id           INTEGER REFERENCES knowledge_items(id) ON DELETE CASCADE,
    user_id         INTEGER REFERENCES users(id),
    signal_type     VARCHAR(50),
    signal_value    FLOAT,
    source_ki_id    INTEGER,
    source_video_id INTEGER,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

#### `channel_profile_embeddings`

```sql
CREATE TABLE channel_profile_embeddings (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    embedding   BLOB,
    model       VARCHAR(50),
    created_at  DATETIME
);
```

### 15.2 Colunas novas em tabelas existentes

#### `channel_profiles`

```sql
ALTER TABLE channel_profiles ADD COLUMN content_type_affinity TEXT DEFAULT '{}';
ALTER TABLE channel_profiles ADD COLUMN editorial_keywords TEXT DEFAULT '[]';
ALTER TABLE channel_profiles ADD COLUMN custom_feeds TEXT DEFAULT '[]';
ALTER TABLE channel_profiles ADD COLUMN gameplay_driven_collection BOOLEAN DEFAULT 1;
ALTER TABLE channel_profiles ADD COLUMN diversity_strictness FLOAT DEFAULT 0.5;
ALTER TABLE channel_profiles ADD COLUMN learned_preferences TEXT DEFAULT '{}';
ALTER TABLE channel_profiles ADD COLUMN production_history_summary TEXT DEFAULT '{}';
```

#### `knowledge_items`

```sql
ALTER TABLE knowledge_items ADD COLUMN freshness_score FLOAT DEFAULT 1.0;
ALTER TABLE knowledge_items ADD COLUMN lifecycle_stage VARCHAR(20) DEFAULT 'fresh';
```

### 15.3 O que NÃO muda

- `KnowledgeItem` schema principal (colunas novas são aditivas)
- `Automation.config.idea_queue` (muda como reconciliador escolhe, não a estrutura)
- `KnowledgeItemStatus` enum (`fresh | used | rejected` mantido; `aging` e
  `archived` são `lifecycle_stage`, ortogonal)
- `visibility.py` (modelo híbrido mantido)

---

## 16. Novos Componentes

### 16.1 Módulos novos

| Componente | Arquivo | Responsabilidade |
|-----------|---------|-----------------|
| Search Templates | `src/gpcg/domain/search_templates.py` | Templates de busca editorial |
| Editorial Profile service | `src/gpcg/application/editorial_profile_service.py` | CRUD + presets + feedback update |
| Editorial Intent Builder | `src/gpcg/application/editorial_intent_builder.py` | Computa Intent a cada ciclo |
| Editorial Brief Builder | `src/gpcg/application/editorial_brief_builder.py` | Computa Brief a partir de Profile + Intent |
| Goal-Oriented Collector | `src/gpcg/application/goal_oriented_collector.py` | Coleta com metas e encerramento antecipado |
| Composite Scorer | `src/gpcg/application/composite_scorer.py` | Score de 3 camadas |
| Lifecycle Manager | `src/gpcg/application/lifecycle_manager.py` | Freshness decay + stage transitions |
| Feedback Propagator | `src/gpcg/application/feedback_propagator.py` | Propaga sinais via embeddings |

### 16.2 Dataclasses de domínio

| Dataclass | Arquivo | Propósito |
|-----------|---------|-----------|
| `GameTarget` | `src/gpcg/domain/editorial_types.py` | Jogo prioritário com reason |
| `SearchQuery` | `src/gpcg/domain/editorial_types.py` | Query expandida com metadata |
| `FeedSpec` | `src/gpcg/domain/editorial_types.py` | Feed com source_name e item_type |
| `EditorialIntent` | `src/gpcg/domain/editorial_types.py` | Intent do ciclo |
| `EditorialBrief` | `src/gpcg/domain/editorial_types.py` | Brief do ciclo |
| `CollectionResult` | `src/gpcg/domain/editorial_types.py` | Resultado da coleta |
| `CompositeScore` | `src/gpcg/domain/editorial_types.py` | Score decomposto em 3 camadas |

---

## 17. Impacto na Experiência do Usuário

### 17.1 Onboarding

**Atual**: Usuário preenche niche, tone, audience (free text).
**V2**: Usuário escolhe preset editorial (Curiosidades, Notícias, Lore, etc.)
que popula campos estruturados. Pode customizar depois. Setup de 30 segundos.

### 17.2 Página de Ideias

**Atual**: Lista de KIs com score genérico.
**V2**: Cada KI mostra score composto com breakdown das 3 camadas + razão da
recomendação ("gameplay de Bully disponível", "canal valoriza curiosidades").

### 17.3 Fila de produção

**Atual**: Reconciliador preenche por editorial_score global.
**V2**: Reconciliador preenche por score composto. Fila visivelmente diferente
entre canais.

### 17.4 Feedback visual

**Atual**: Usuário rejeita KI. Sistema não aprende.
**V2**: Usuário rejeita KI. Sistema aplica penalty em similares e mostra
"Entendido — vou priorizar menos ideias similares".

---

## 18. Riscos e Mitigações

| Risco | Mitigação |
|-------|-----------|
| Filter bubble | `exploration_factor` 10% aleatório |
| Cold start | Defaults razoáveis + presets + gameplay como sinal inicial |
| Embeddings ruidosos | Threshold conservador (0.85), penalty pequena |
| Custo de coleta per-canal | Feeds globais coletados 1x, cache RSS, rate limiting |
| YouTube API rate limit | AnalyticsSyncService assíncrono, opt-in |
| Complexidade do Brief | Brief é dataclass imutável; lógica nos builders |
| Usuário não configura | Default = comportamento atual. V2 é aditiva. |

---

## 19. Prioridades de Implementação

### Fase 1: Fundação (Editorial Profile + Intent + Brief + Search Templates + Coleta Dirigida)

1. `search_templates.py` — templates como componentes de primeira classe
2. `editorial_types.py` — dataclasses de domínio
3. ChannelProfile evoluído — 7 colunas novas + presets
4. `editorial_profile_service.py` — CRUD + presets
5. `editorial_intent_builder.py` — computa Intent
6. `editorial_brief_builder.py` — computa Brief
7. `goal_oriented_collector.py` — coleta com metas
8. Integração com `_process_content_collect_job`
9. Feature flag: `gpcg_editorial_brief_enabled`
10. Testes

### Fase 2: Scoring Composto + Lifecycle

1. `composite_scorer.py` — 3 camadas multiplicativas
2. `lifecycle_manager.py` — freshness decay + stages
3. Channel profile embeddings
4. Reconciliador V2 — usa CompositeScorer
5. Colunas `freshness_score` + `lifecycle_stage` em KIs
6. Feature flag: `gpcg_composite_scoring_enabled`
7. Testes

### Fase 3: Diversidade + Gameplay Driver

1. Cooldown por jogo no Intent
2. Rotação de formatos no Intent
3. Gameplay como driver de priority_games
4. Exploration factor
5. Feature flag: `gpcg_diversity_engine_enabled`
6. Testes

### Fase 4: Feedback Loop Básico

1. `editorial_signals` table
2. `feedback_propagator.py` — propagação via embeddings
3. Rejeição manual → penalty em similares
4. Adição manual → boost em content_type_affinity
5. Histórico de produção → production_history_summary
6. Feature flag: `gpcg_feedback_loop_enabled`
7. Testes

### Fase 5 (futuro): YouTube Analytics

1. `video_performance` table
2. `analytics_sync_service.py`
3. Performance score computation
4. Boost/penalty em KIs similares
5. Feature flag: `gpcg_analytics_sync_enabled`

---

## 20. O que NÃO Propomos

- **Knowledge Graph** — já rejeitado em ARCHITECTURE_V2.md (D1)
- **10 sub-scores** — over-engineering; 3 camadas > 10 sub-scores
- **Status machine complexo para lifecycle** — freshness score captura a mesma info
- **A/B testing** — prematuro, precisa de volume
- **Real-time trend monitoring** — custo alto, valor incerto
- **NLP de comentários** — ruidoso, baixo ROI
- **Multi-plataforma** — fora do escopo editorial
- **pgvector** — longo prazo, não é decisão editorial

---

## Resumo Executivo

A V2.1 propõe uma **inversão do pipeline** com **3 conceitos separados**:

1. **Editorial Profile** (quem o canal é) — persistido, evolui com feedback
2. **Editorial Intent** (o que produzir agora) — temporário, recalculado por ciclo
3. **Editorial Brief** (como encontrar) — temporário, traduz Intent em busca

**3 mudanças estruturais:**
- Coleta dirigida pelo Brief (não genérica)
- Scoring composto de 3 camadas (Quality × Fit × Timing)
- Feedback loop que alimenta Profile

**Princípios:**
- Custo LLM não muda (personalização é barata)
- Tudo é feature-flagged (default off)
- Tudo é aditivo (não quebra nada)
- Simplicidade > completude
- Funciona para milhares de canais

---

**Fim do documento. Esta é a referência oficial para implementação.**
