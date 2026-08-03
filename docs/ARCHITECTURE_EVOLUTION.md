# GPCG — Reestruturação Completa do Sistema de Conhecimento, Game Registry e Content Intelligence

## Documento Técnico de Arquitetura (Blueprint de Engenharia)

**Versão:** 1.0
**Data:** 2026-08-02
**Status:** Planejamento — não implementar

---

## Sumário

1. [Visão Geral da Solução](#1-visão-geral-da-solução)
2. [Estado Atual e Gaps Identificados](#2-estado-atual-e-gaps-identificados)
3. [Decisões Técnicas](#3-decisões-técnicas)
4. [Game Registry Canônico](#4-game-registry-canônico)
5. [Game Knowledge](#5-game-knowledge)
6. [Game Knowledge Enrichment](#6-game-knowledge-enrichment)
7. [Gameplay Intelligence](#7-gameplay-intelligence)
8. [Content Intelligence — Conectores de Fontes](#8-content-intelligence--conectores-de-fontes)
9. [Knowledge Item](#9-knowledge-item)
10. [Knowledge Graph](#10-knowledge-graph)
11. [Seleção Inteligente de Gameplay](#11-seleção-inteligente-de-gameplay)
12. [Gameplays Públicas](#12-gameplays-públicas)
13. [Configuração das Automações](#13-configuração-das-automações)
14. [Novos Módulos e Serviços](#14-novos-módulos-e-serviços)
15. [Alterações de Banco de Dados](#15-alterações-de-banco-de-dados)
16. [Alterações de API](#16-alterações-de-api)
17. [Alterações de Frontend](#17-alterações-de-frontend)
18. [Alterações de Worker](#18-alterações-de-worker)
19. [Pipeline Editorial Revisado](#19-pipeline-editorial-revisado)
20. [Impacto Arquitetural Completo](#20-impacto-arquitetural-completo)
21. [Riscos e Mitigações](#21-riscos-e-mitigações)
22. [Plano de Implementação](#22-plano-de-implementação)
23. [Extensibilidade](#23-extensibilidade)

---

## 1. Visão Geral da Solução

### 1.1 Problema

O GPCG hoje é um sistema que **conhece apenas vídeos**. Ele entende gameplays (via VLM/ASR), extrai facts de documentos enviados manualmente, e gera roteiros baseados nesse conhecimento limitado. O sistema não entende o **universo dos games** — não conhece franquias, empresas, personagens, gêneros, eventos de mercado, lore, ou notícias. A seleção de gameplay é intra-jogo (não cross-game), e o conteúdo depende exclusivamente do que o usuário envia.

### 1.2 Solução Proposta

Transformar o GPCG em uma plataforma que **entende o universo dos games** e pode **produzir conteúdo autonomamente** a partir de múltiplas fontes de conhecimento público, utilizando gameplays compatíveis como mídia de fundo — não apenas do jogo citado, mas de jogos relacionados por franquia, empresa, gênero ou tema.

### 1.3 Pilares da Evolução

| Pilar | Descrição |
|-------|-----------|
| **Game Registry Canônico** | Catálogo central de jogos com IDs canônicos, aliases, slugs, identificadores externos |
| **Game Knowledge** | Modelo rico de conhecimento por jogo (developer, publisher, franquia, gêneros, personagens, etc.) |
| **Game Knowledge Enrichment** | Pipeline automático de enriquecimento via fontes públicas (Wikidata, Wikipedia, Steam, IGDB) |
| **Content Intelligence** | Sistema de conectores que alimentam continuamente um banco de ideias (RSS, Google News, Reddit, blogs oficiais) |
| **Knowledge Item** | Entidade normalizada que unifica notícias, curiosidades, eventos, lore, changelogs em um formato comum |
| **Knowledge Graph** | Grafo de relacionamentos entre jogos, franquias, empresas, personagens, gêneros — implementado sobre banco relacional |
| **Seleção Inteligente de Gameplay** | Algoritmo editorial que seleciona gameplay por compatibilidade semântica (empresa, franquia, gênero), não apenas por nome do jogo |
| **Gameplays Públicas** | Sistema opt-in de compartilhamento de gameplays entre usuários |

### 1.4 Princípios de Design

1. **Banco relacional, não grafo dedicado** — SQLite/PostgreSQL com tabelas de junção para o Knowledge Graph. Avaliação técnica detalhada na seção 10.
2. **Fontes gratuitas e públicas como prioridade** — RSS, Wikipedia, Wikidata, Steam. APIs pagas apenas como enriquecimento opcional.
3. **Extensibilidade por conectores** — Novas fontes de conteúdo = novo conector, sem alterar o núcleo.
4. **Compatibilidade retroativa** — Usuários existentes continuam funcionando. Todas as mudanças de schema são aditivas.
5. **Multi-tenant preservado** — Conhecimento canônico (Game Registry, Knowledge Graph) é global; Knowledge Items e gameplays são por usuário com opt-in para público.
6. **Sem dependência de APIs pagas para funcionar** — O sistema deve ser totalmente funcional usando apenas fontes gratuitas.

---

## 2. Estado Atual e Gaps Identificados

### 2.1 Arquitetura Atual (Resumo)

```
Upload de Gameplay → Game Resolver (filename + VLM) → Mapping (VLM + ASR) → GameplayEvents
Upload de Documento → Knowledge Index (chunk + embed) → KnowledgeChunks
Upload de Documento → Fact Extraction (LLM) → Facts → Curiosity Scoring
                                    ↓
Editorial Strategy (decide jogo + fact) → Content Planning (LLM decide tópico)
                                    ↓
Story Finder → Editorial Planner → Creative Engine → Script → Humanization
                                    ↓
Script Critic → TTS → Gameplay Retriever → Render → QA → YouTube
```

### 2.2 Gaps Identificados

| # | Gap | Impacto |
|---|-----|---------|
| G1 | **Game Registry não-canônico** — `Game` tem `canonical_name` + `aliases` mas sem slug, sem IDs externos, sem deduplicação robusta. Múltiplos Games podem representar o mesmo jogo. | Bully, Bully PS2, Bully Scholarship Edition podem ser 3 registros diferentes |
| G2 | **Game Knowledge mínimo** — `Game` tem apenas `canonical_name`, `aliases`, `platforms`, `capture_sources`, `camera_type`, `metadata_json`. Sem developer, publisher, franquia, gêneros, personagens, data de lançamento, descrição. | Pipeline não sabe que Bully é da Rockstar, que é da franquia Bully, que é open-world |
| G3 | **Sem enriquecimento automático** — Quando um novo jogo surge (via upload), nada enriquece seus metadados. | Jogos ficam com conhecimento mínimo para sempre |
| G4 | **Conteúdo depende 100% do usuário** — Facts só existem se o usuário enviar documentos. Sem fontes externas. | Canal não tem ideias novas sem input manual constante |
| G5 | **Sem Knowledge Items normalizados** — Facts são a única entidade de conhecimento. Notícias, eventos, lore não têm representação. | Não é possível produzir "notícia" ou "evento" como conteúdo |
| G6 | **Sem Knowledge Graph** — Sem relacionamentos entre jogos, franquias, empresas. | Conteúdo sobre Bethesda não sabe que Skyrim é relacionado |
| G7 | **Seleção de gameplay intra-jogo** — `GameplayRetriever` busca eventos apenas dentro do `game_id` do job. Sem cross-game. | Vídeo sobre Capcom não pode usar gameplay de Resident Evil ou Monster Hunter |
| G8 | **Sem gameplays públicas** — Gameplays são isoladas por usuário. | Usuário sem gameplay de Skyrim não pode produzir vídeo sobre Elder Scrolls |
| G9 | **Automação sem configurações de Content Intelligence** — Config não tem toggles para notícias, curiosidades, evergreen, escopo (jogo/franquia/empresa) | Automação só produz vídeos do jogo enviado, sem variedade |
| G10 | **Sem busca semântica por embedding em gameplay** — `search_events` usa SQL LIKE, não embeddings | Baixa recall em matching de gameplay com tópico |
| G11 | **Editorial Strategy não considera Knowledge Graph** — Decisão editorial baseada apenas em inventário por jogo, não por franquia/empresa | Não consegue decidir "vídeo sobre franquia Resident Evil" usando gameplay de RE4 |

---

## 3. Decisões Técnicas

### 3.1 Banco de Dados: Relacional com Tabelas de Junção (não Neo4j)

**Decisão:** Implementar o Knowledge Graph sobre SQLite/PostgreSQL usando tabelas de junção, não um banco de grafos dedicado.

**Justificativa:**
- O GPCG já usa SQLite com WAL mode e SQLAlchemy 2.0. Introduzir Neo4j adicionaria complexidade operacional significativa (outro container, outra query language, sync entre bancos).
- O grafo de jogos é **relativamente raso** (jogo → franquia → empresa, jogo → gênero, jogo → personagem). Não requer traversals de profundidade arbitrária.
- Queries como "encontre gameplays de jogos da mesma franquia que X" são facilmente expressas em SQL com 1-2 JOINs.
- Se o grafo crescer para milhões de nós, migração para PostgreSQL com `pg_trgm` + índices GIN é mais simples que migrar para Neo4j.
- Embeddings vetoriais (para busca semântica) já são armazenados como JSON em SQLite. PostgreSQL com `pgvector` é o caminho de evolução natural.

**Trade-off:** Queries de traversals profundos (4+ hops) seriam ineficientes em SQL. Mas o domínio de jogos raramente precisa disso.

### 3.2 Fontes de Conteúdo: Gratuitas e Públicas como Padrão

**Decisão:** Priorizar RSS, Google News RSS, Wikipedia API, Wikidata SPARQL, Steam Web API (gratuita), Reddit API (gratuita com rate limit). APIs pagas (IGDB, RAWG) como enriquecimento opcional.

**Justificativa:**
- Google News RSS é gratuito e cobre notícias em tempo real.
- Wikipedia/Wikidata são gratuitos, completos, e têm APIs estáveis.
- Steam Web API é gratuita (requer key gratuita) e tem dados ricos de jogos.
- Reddit API é gratuita para uso não-comercial (rate limit generoso).
- IGDB/RAWG têm tiers gratuitos limitados; podem ser adicionados depois.

### 3.3 Knowledge Item: Entidade Unificada

**Decisão:** Criar `KnowledgeItem` como entidade normalizada que subsume `Fact` para conteúdo externo. `Fact` continua existindo para facts extraídos de documentos do usuário, mas `KnowledgeItem` é a entidade que o pipeline editorial consulta.

**Justificativa:**
- `Fact` está acoplado a `Document` (user-uploaded). Notícias de RSS não têm "document".
- `KnowledgeItem` pode ter `source_type` (news, curiosity, event, lore, changelog, fact) e `source_ref` flexível.
- Permite que o pipeline editorial tenha uma interface unificada: "dê-me os melhores knowledge items para este jogo/franquia/empresa".

### 3.4 Game Registry: Global com Isolamento de User em Gameplay

**Decisão:** O Game Registry (catálogo canônico) é **global** — compartilhado entre todos os usuários. Gameplays, Knowledge Items e Videos continuam por usuário.

**Justificativa:**
- "Bully" é o mesmo jogo para todos os usuários. Não faz sentido cada usuário ter seu próprio registro de Bully.
- O enriquecimento automático precisa ser feito uma vez por jogo, não por usuário.
- O Knowledge Graph é global por natureza (franquias, empresas são universais).
- Gameplays são pessoais (cada usuário grava as suas).

**Migração:** O campo `Game.user_id` atual será deprecated. Jogos existentes serão migrados para o registry global. `GameplaySource.user_id` permanece.

### 3.5 Embeddings para Gameplay Events

**Decisão:** Adicionar embeddings aos `GameplayEvent` (usando `nomic-embed-text` já disponível no Ollama) para busca semântica.

**Justificativa:**
- O sistema já tem Ollama com `nomic-embed-text` para KnowledgeChunks.
- A busca atual (SQL LIKE) tem baixa recall.
- Embeddings permitem "combat in a school" match com "fight in classroom".

### 3.6 Schema Evolution: Continuar com `_ensure_column()`

**Decisão:** Manter a abordagem aditiva via `_ensure_column()` em vez de introduzir Alembic.

**Justificativa:**
- O sistema já funciona assim. Introduzir Alembic agora adicionaria complexidade.
- Todas as mudanças propostas são aditivas (novas tabelas, novas colunas).
- Quando migrar para PostgreSQL, Alembic pode ser introduzido.

---

## 4. Game Registry Canônico

### 4.1 Problema Atual

O modelo `Game` atual:
- `canonical_name`: String(200) — nome canônico
- `aliases`: JSON list — nomes alternativos
- `platforms`: JSON list — plataformas
- `capture_sources`: JSON list — fontes de captura
- `camera_type`: String(32) — tipo de câmera
- `metadata_json`: JSON — metadados flexíveis
- `user_id`: Integer — **dono do jogo (problema!)**

**Problemas:**
1. Sem slug canônico para URLs e deduplicação.
2. Sem IDs externos (IGDB, Steam, Wikidata) para enriquecimento.
3. `user_id` faz cada usuário ter seu próprio registro do mesmo jogo.
4. Sem validação de duplicação na criação.
5. `aliases` é JSON não-indexável (não busca eficiente).

### 4.2 Modelo Proposto

#### Entidade: `Game` (evolução da tabela existente)

```sql
-- Colunas EXISTENTES (mantidas):
id              INTEGER PRIMARY KEY
canonical_name  VARCHAR(200) NOT NULL
aliases         JSON         -- mantido para compatibilidade, mas GameAlias é a fonte de verdade
platforms       JSON         -- mantido para compatibilidade
capture_sources JSON         -- mantido para compatibilidade
camera_type     VARCHAR(32)  DEFAULT 'unknown'
metadata_json   JSON         DEFAULT '{}'
created_at      DATETIME
updated_at      DATETIME

-- Colunas NOVAS:
slug            VARCHAR(200) UNIQUE NOT NULL  -- "bully", "resident-evil-4", etc.
description     TEXT                          -- resumo/overview do jogo
release_date    DATE                          -- data de lançamento (primeira plataforma)
developer_id    INTEGER REFERENCES game_entities(id)  -- estúdio desenvolvedor
publisher_id    INTEGER REFERENCES game_entities(id)  -- publisher
franchise_id    INTEGER REFERENCES franchises(id)     -- franquia (nullable)
series_id       INTEGER REFERENCES series(id)         -- série (nullable)
genres          JSON          -- ["action", "adventure", "open-world"]
themes          JSON          -- ["school", "rebellion", "bullying"]
engine          VARCHAR(200)  -- "RenderWare", "Unreal Engine 5"
keywords        JSON          -- ["skateboarding", "pranks", "classes"]
external_ids    JSON          -- {"igdb": 1234, "steam": 123456, "wikidata": "Q123456"}
enrichment_status VARCHAR(20) DEFAULT 'pending'  -- pending|enriching|enriched|failed|manual
enrichment_source VARCHAR(50)  -- "wikidata"|"steam"|"igdb"|"manual"|"wikipedia"
enrichment_at   DATETIME
is_canonical    BOOLEAN DEFAULT TRUE  -- FALSE se for um registro duplicado mesclado
merged_into_id  INTEGER REFERENCES games(id)  -- se mesclado, aponta para o canônico
```

#### Entidade: `GameAlias` (nova tabela)

```sql
CREATE TABLE game_aliases (
    id          INTEGER PRIMARY KEY,
    game_id     INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    alias       VARCHAR(200) NOT NULL,
    alias_type  VARCHAR(30) DEFAULT 'alternative',  -- alternative|abbreviation|regional|platform_specific|typo
    confidence  FLOAT DEFAULT 1.0,  -- 1.0 = manual, <1.0 = automático
    source      VARCHAR(50) DEFAULT 'manual',  -- manual|wikidata|steam|igdb|resolver
    created_at  DATETIME
);

CREATE UNIQUE INDEX idx_game_aliases_alias_lower ON game_aliases(LOWER(alias));
CREATE INDEX idx_game_aliases_game_id ON game_aliases(game_id);
```

**Justificativa para tabela separada:** Permite indexar aliases individualmente, buscar por alias em O(log n) em vez de scanar JSON arrays, e rastrear a proveniência de cada alias.

#### Entidade: `GameEntity` (nova tabela — empresas, estúdios, pessoas)

```sql
CREATE TABLE game_entities (
    id            INTEGER PRIMARY KEY,
    entity_type   VARCHAR(30) NOT NULL,  -- company|developer|publisher|person|platform|engine
    canonical_name VARCHAR(200) NOT NULL,
    slug          VARCHAR(200) NOT NULL,
    description   TEXT,
    founded_year  INTEGER,  -- para empresas
    country       VARCHAR(100),
    parent_entity_id INTEGER REFERENCES game_entities(id),  -- subsidiary (e.g., Rockstar → Take-Two)
    external_ids  JSON,     -- {"wikidata": "Q123456"}
    metadata_json JSON DEFAULT '{}',
    created_at    DATETIME,
    updated_at    DATETIME
);

CREATE UNIQUE INDEX idx_game_entities_type_slug ON game_entities(entity_type, slug);
CREATE INDEX idx_game_entities_type ON game_entities(entity_type);
```

#### Entidade: `Franchise` (nova tabela)

```sql
CREATE TABLE franchises (
    id              INTEGER PRIMARY KEY,
    canonical_name  VARCHAR(200) NOT NULL,
    slug            VARCHAR(200) NOT NULL,
    description     TEXT,
    parent_franchise_id INTEGER REFERENCES franchises(id),  -- sub-franquias
    external_ids    JSON,     -- {"wikidata": "Q123"}
    metadata_json   JSON DEFAULT '{}',
    created_at      DATETIME,
    updated_at    DATETIME
);

CREATE UNIQUE INDEX idx_franchises_slug ON franchises(slug);
```

#### Entidade: `Series` (nova tabela)

```sql
CREATE TABLE series (
    id              INTEGER PRIMARY KEY,
    canonical_name  VARCHAR(200) NOT NULL,
    slug            VARCHAR(200) NOT NULL,
    franchise_id    INTEGER REFERENCES franchises(id),
    description     TEXT,
    external_ids    JSON,
    metadata_json   JSON DEFAULT '{}',
    created_at      DATETIME,
    updated_at      DATETIME
);

CREATE UNIQUE INDEX idx_series_slug ON series(slug);
```

#### Entidade: `Character` (nova tabela)

```sql
CREATE TABLE characters (
    id              INTEGER PRIMARY KEY,
    canonical_name  VARCHAR(200) NOT NULL,
    slug            VARCHAR(200) NOT NULL,
    description     TEXT,
    game_id         INTEGER REFERENCES games(id),  -- jogo principal
    franchise_id    INTEGER REFERENCES franchises(id),  -- franquia (para personagens recorrentes)
    external_ids    JSON,     -- {"wikidata": "Q123"}
    metadata_json   JSON DEFAULT '{}',
    created_at      DATETIME,
    updated_at      DATETIME
);

CREATE UNIQUE INDEX idx_characters_slug ON characters(slug);
```

### 4.3 Migração de Dados Existentes

1. Para cada `Game` existente:
   - Gerar `slug` a partir de `canonical_name` (slugify)
   - Se houver duplicatas (mesmo slug), mesclar: marcar duplicatas com `is_canonical=FALSE`, `merged_into_id=X`
   - Migrar `aliases` JSON para registros individuais em `game_aliases`
   - Setar `enrichment_status='pending'` para enriquecimento futuro
2. Remover `user_id` de `Game` (deprecated — jogos são globais):
   - Mover `Game.user_id` para `metadata_json.legacy_user_id` (preservar histórico)
   - `GameplaySource.user_id` permanece — gameplays continuam por usuário
3. Criar `GameEntity` para developers/publishers conhecidos a partir de `metadata_json` existente

### 4.4 Algoritmo de Deduplicação

```
1. Normalizar nome: lowercase, remover acentos, remover sufixos de plataforma
   ("Bully PS2" → "bully", "Bully Scholarship Edition" → "bully scholarship edition")
2. Buscar por slug exato
3. Se encontrado: mesclar aliases, marcar duplicata
4. Se não: buscar por alias (tabela game_aliases)
5. Se encontrado: mesclar
6. Se não: buscar por similaridade (Levenshtein distance ≤ 2)
7. Se encontrado: requer confirmação (marcar needs_review)
8. Se não: criar novo Game canônico
```

---

## 5. Game Knowledge

### 5.1 Modelo Rico de Conhecimento

Cada `Game` terá os seguintes campos de conhecimento (já especificados na seção 4.2):

| Campo | Tipo | Fonte de Enriquecimento | Exemplo (Bully) |
|-------|------|------------------------|-----------------|
| `description` | TEXT | Wikipedia/Wikidata | "Bully é um jogo de ação-aventura open-world desenvolvido pela Rockstar Vancouver..." |
| `release_date` | DATE | Wikidata | 2006-10-17 |
| `developer_id` | FK → GameEntity | Wikidata | Rockstar Vancouver |
| `publisher_id` | FK → GameEntity | Wikidata | Rockstar Games |
| `franchise_id` | FK → Franchise | Wikidata | Bully (franquia) |
| `series_id` | FK → Series | Wikidata | NULL (jogo único) |
| `genres` | JSON | IGDB/Wikidata | ["action", "adventure", "open-world"] |
| `themes` | JSON | IGDB/Wikidata | ["school", "rebellion", "bullying"] |
| `engine` | VARCHAR | Wikidata | RenderWare |
| `keywords` | JSON | LLM + Wikipedia | ["skateboarding", "pranks", "classes", "dormitory"] |
| `external_ids` | JSON | Múltiplas | {"steam": 28000, "wikidata": "Q1019291"} |

### 5.2 Campos Adicionais Propostos

| Campo | Tipo | Justificativa |
|-------|------|---------------|
| `platforms_detailed` | JSON | Lista de objetos: `{"platform": "PS2", "release_date": "2006-10-17"}` |
| `esrb_rating` | VARCHAR | "T", "M", etc — útil para filtrar conteúdo |
| `metacritic_score` | INTEGER | Qualidade crítica do jogo |
| `steam_review_pct` | INTEGER | Percentual positivo no Steam |
| `historical_context` | TEXT | Contexto histórico gerado por LLM (significado do jogo na época) |
| `trivia` | JSON | Lista de curiosidades estruturadas |
| `lore_summary` | TEXT | Resumo da lore/história do jogo |
| `notable_characters` | JSON | IDs de personagens notáveis |
| `reception_summary` | TEXT | Resumo da recepção crítica |
| `development_history` | TEXT | História do desenvolvimento |
| `content_warnings` | JSON | ["violence", "language"] — para filtrar conteúdo |

### 5.3 Conhecimento por Jogo vs Conhecimento Global

- **Por jogo:** description, release_date, engine, reception, development_history, trivia
- **Por franquia:** lore transversal, cronologia, personagens recorrentes
- **Por empresa:** história da empresa, outros jogos, cultura corporativa
- **Por gênero:** convenções, tropos, mecânicas típicas

O pipeline editorial deve poder acessar todos esses níveis dependendo do tipo de vídeo.

---

## 6. Game Knowledge Enrichment

### 6.1 Pipeline de Enriquecimento

```
Novo Game criado (via upload ou import)
    ↓
enrichment_status = 'pending'
    ↓
Job de enriquecimento criado (tipo: game_enrich)
    ↓
    ┌────────────────────────────────────┐
    │  FASE 1: Resolução de Identidade   │
    │  1. Buscar no Wikidata por nome    │
    │  2. Confirmar match (LLM ou score) │
    │  3. Obter QID (Wikidata ID)        │
    └────────────────────────────────────┘
    ↓
    ┌────────────────────────────────────┐
    │  FASE 2: Coleta de Dados           │
    │  1. Wikidata SPARQL: developer,    │
    │     publisher, release_date, genre │
    │  2. Wikipedia API: descrição,      │
    │     história, contexto             │
    │  3. Steam (se appid encontrado):   │
    │     descrição, gêneros, rating     │
    │  4. IGDB (se key disponível):      │
    │     metacritic, temas, keywords    │
    └────────────────────────────────────┘
    ↓
    ┌────────────────────────────────────┐
    │  FASE 3: Normalização              │
    │  1. Mapear developer/publisher     │
    │     para GameEntity (get_or_create)│
    │  2. Mapear franquia para Franchise │
    │  3. Normalizar gêneros (taxonomia) │
    │  4. Traduzir descrição para pt-BR  │
    │     (LLM, não Google Translate)    │
    └────────────────────────────────────┘
    ↓
    ┌────────────────────────────────────┐
    │  FASE 4: Enriquecimento LLM        │
    │  1. Gerar keywords a partir da     │
    │     descrição + Wikipedia          │
    │  2. Gerar historical_context       │
    │  3. Gerar lore_summary             │
    │  4. Gerar trivia (a partir de      │
    │     Wikipedia + Wikidata)          │
    └────────────────────────────────────┘
    ↓
    ┌────────────────────────────────────┐
    │  FASE 5: Persistência              │
    │  1. Atualizar Game com todos os    │
    │     campos                         │
    │  2. Criar/atualizar GameEntity     │
    │     para developer/publisher       │
    │  3. Criar/atualizar Franchise      │
    │  4. Criar GameRelationships no     │
    │     Knowledge Graph                │
    │  5. enrichment_status = 'enriched' │
    └────────────────────────────────────┘
```

### 6.2 Resolução de Identidade (Wikidata)

```sparql
# Exemplo: buscar "Bully" no Wikidata
SELECT ?item ?itemLabel ?itemDescription WHERE {
  ?item rdfs:label "Bully"@en .
  ?item wdt:P31 wd:Q7889 .  # instância de video game
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
```

**Confirmação de match:** O LLM recebe o nome do jogo + descrição do Wikidata e confirma se é o jogo correto (evita ambiguidade — "Bully" pode ser o jogo ou o filme).

### 6.3 Atualização Incremental

- **Trigger:** Novo jogo criado → `enrichment_status='pending'`
- **Re-enriquecimento:** Manual (botão na UI) ou quando `external_ids` é atualizado
- **Cache:** Resultados do Wikidata/Wikipedia cacheados em `metadata_json.enrichment_cache` com timestamp
- **Versionamento:** `enrichment_at` + `enrichment_source` rastreiam quando e de onde veio cada enriquecimento
- **Qualidade:** `enrichment_status` pode ser `manual` (sobrescrito pelo usuário) — enriquecimento automático não sobrescreve dados manuais

### 6.4 Prevenção de Duplicidade

1. Antes de enriquecer, verificar se já existe `Game` com mesmo `external_ids.wikidata`
2. Se sim: mesclar (marcar este como duplicata, transferir gameplays/facts)
3. Se não: proceder com enriquecimento

### 6.5 Novo Job Type

```python
# Adicionar a JobType:
class JobType(str, enum.Enum):
    ...
    game_enrich = "game_enrich"  # NOVO
```

**Worker capability:** `enrichment` (pode rodar no VPS — não precisa de GPU, apenas HTTP + LLM)

### 6.6 Novo Serviço: `GameEnrichmentService`

```python
# src/gpcg/application/game_enrichment_service.py

class GameEnrichmentService:
    """Enriquece jogos com dados de fontes públicas."""
    
    def __init__(self, llm: LLMClient, wikidata: WikidataClient, 
                 wikipedia: WikipediaClient, steam: SteamClient):
        ...
    
    def enrich_game(self, game_id: int) -> EnrichmentResult:
        """Pipeline completo de enriquecimento."""
        # 1. Resolver identidade no Wikidata
        # 2. Coletar dados
        # 3. Normalizar
        # 4. Enriquecer com LLM
        # 5. Persistir + atualizar Knowledge Graph
        ...
    
    def re_enrich(self, game_id: int) -> EnrichmentResult:
        """Re-enriquecer (manual trigger)."""
        ...
```

---

## 7. Gameplay Intelligence

### 7.1 Herança de Conhecimento

Quando uma gameplay é vinculada a um Game, ela herda automaticamente todo o conhecimento do jogo:

```
GameplaySource (Bully.mp4)
    → Game (Bully)
        → GameEntity (Rockstar Vancouver) [developer]
        → GameEntity (Rockstar Games) [publisher]
        → Franchise (Bully)
        → genres: [action, adventure, open-world]
        → themes: [school, rebellion, bullying]
        → characters: [Jimmy Hopkins, Gary Smith]
        → engine: RenderWare
        → release_date: 2006-10-17
```

### 7.2 Implementação

**Não duplicar dados na GameplaySource.** A herança é computada em tempo de query via JOINs:

```sql
-- Exemplo: obter conhecimento completo de uma gameplay
SELECT 
    gs.id as gameplay_id,
    g.canonical_name, g.genres, g.themes,
    dev.canonical_name as developer,
    pub.canonical_name as publisher,
    f.canonical_name as franchise
FROM gameplay_sources gs
JOIN games g ON gs.game_id = g.id
LEFT JOIN game_entities dev ON g.developer_id = dev.id
LEFT JOIN game_entities pub ON g.publisher_id = pub.id
LEFT JOIN franchises f ON g.franchise_id = f.id
WHERE gs.id = ?;
```

### 7.3 GameplayEvent Enhancement

Adicionar campos semânticos aos GameplayEvents para permitir cross-game matching:

```sql
-- Novas colunas em gameplay_events:
event_embedding   JSON     -- embedding vetorial da descrição (nomic-embed-text)
game_id           INTEGER  -- denormalizado de source.game_id para queries diretas
franchise_id      INTEGER  -- denormalizado para cross-game matching
```

**Justificativa para denormalização:** Evita JOINs em queries de busca semântica cross-game. `game_id` e `franchise_id` são estáveis (não mudam após mapeamento).

### 7.4 Integração com Pipeline Existente

**No `GameplayRetriever.retrieve()`:**

Atualmente:
```python
# Busca apenas dentro do game_id do job
clips = self._retrieve_semantic(session, game_id, ...)
```

Proposto:
```python
# Busca em jogos compatíveis (via Knowledge Graph)
compatible_game_ids = self._find_compatible_games(session, game_id, scope)
# scope = "game" | "franchise" | "company" | "genre"
clips = self._retrieve_semantic_cross_game(session, compatible_game_ids, ...)
```

---

## 8. Content Intelligence — Conectores de Fontes

### 8.1 Arquitetura de Conectores

```
                    ┌──────────────────┐
                    │ ConnectorManager │
                    │   (orchestrator) │
                    └────────┬─────────┘
                             │
           ┌─────────────────┼─────────────────┐
           │                 │                 │
    ┌──────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐
    │ RSSConnector│  │WikiConnector │  │SteamConnector│
    │ (Google News│  │ (Wikipedia + │  │ (Store API)  │
    │  + blogs)   │  │  Wikidata)   │  │              │
    └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
           │                │                │
           └─────────────────┼─────────────────┘
                             │
                    ┌────────▼─────────┐
                    │  KnowledgeItem   │
                    │  (normalizado)   │
                    └──────────────────┘
```

### 8.2 Interface de Conector

```python
# src/gpcg/application/content_connectors/base.py

class ContentConnector(ABC):
    """Interface base para todos os conectores de conteúdo."""
    
    @abstractmethod
    def name(self) -> str:
        """Identificador do conector (e.g., 'rss', 'wikipedia')."""
        ...
    
    @abstractmethod
    def fetch(self, query: ConnectorQuery) -> list[RawContentItem]:
        """Busca conteúdo baseado na query.
        
        ConnectorQuery contém:
        - game_id (opcional)
        - franchise_id (opcional)
        - company_id (opcional)
        - keywords (lista)
        - max_items (int)
        - since (datetime, opcional — para incremental)
        """
        ...
    
    @abstractmethod
    def normalize(self, raw: RawContentItem) -> KnowledgeItemCreate:
        """Normaliza um item bruto para o formato KnowledgeItem."""
        ...
    
    def rate_limit(self) -> dict:
        """Retorna configuração de rate limit para este conector."""
        return {"requests_per_minute": 60, "burst": 10}
```

### 8.3 Conectores Propostos

#### 8.3.1 RSS Connector (Google News + Blogs)

```python
# src/gpcg/application/content_connectors/rss_connector.py

class RSSConnector(ContentConnector):
    """Busca notícias via RSS feeds (Google News, blogs oficiais)."""
    
    FEEDS = {
        "google_news_games": "https://news.google.com/rss/search?q=gaming+games&hl=pt-BR&gl=BR",
        "google_news_specific": "https://news.google.com/rss/search?q={game_name}&hl=pt-BR&gl=BR",
        # Blogs oficiais podem ser adicionados por config
    }
    
    def fetch(self, query: ConnectorQuery) -> list[RawContentItem]:
        # 1. Construir URL RSS baseada na query
        # 2. Fazer parse do feed (feedparser)
        # 3. Retornar items brutos
        ...
    
    def normalize(self, raw) -> KnowledgeItemCreate:
        # title → title
        # summary → content
        # published → published_at
        # link → source_url
        # source_type = "news"
        ...
```

**Dependências:** `feedparser` (pip install feedparser)

#### 8.3.2 Wikipedia/Wikidata Connector

```python
# src/gpcg/application/content_connectors/wikipedia_connector.py

class WikipediaConnector(ContentConnector):
    """Busca conhecimento estruturado na Wikipedia e Wikidata."""
    
    def fetch(self, query: ConnectorQuery) -> list[RawContentItem]:
        # 1. Se game_id: buscar artigo do jogo na Wikipedia
        # 2. Extrair seções: "Plot", "Development", "Reception", "Trivia"
        # 3. Se franchise_id: buscar artigo da franquia
        # 4. Se company_id: buscar artigo da empresa
        # 5. Para cada seção: criar RawContentItem
        ...
    
    def normalize(self, raw) -> KnowledgeItemCreate:
        # section_title → title
        # section_text → content
        # source_type = "lore" | "history" | "trivia" (baseado na seção)
        ...
```

**Dependências:** `httpx` (já existe), API REST da Wikipedia (gratuita, sem key)

#### 8.3.3 Steam Connector

```python
# src/gpcg/application/content_connectors/steam_connector.py

class SteamConnector(ContentConnector):
    """Busca dados de jogos na Steam Web API."""
    
    def fetch(self, query: ConnectorQuery) -> list[RawContentItem]:
        # 1. Se game.external_ids.steam existe: buscar app details
        # 2. Extrair: descrição, gêneros, tags, news (Steam News)
        # 3. Steam News: notícias oficiais do jogo (changelogs, updates)
        ...
    
    def normalize(self, raw) -> KnowledgeItemCreate:
        # news_item → source_type = "changelog" | "update"
        # app_description → source_type = "description"
        ...
```

**Dependências:** `httpx`, Steam Web API key (gratuita)

#### 8.3.4 Reddit Connector

```python
# src/gpcg/application/content_connectors/reddit_connector.py

class RedditConnector(ContentConnector):
    """Busca discussões relevantes no Reddit (r/gaming, r/{game_name})."""
    
    SUBREDDITS = ["gaming", "games", "truegaming", "patientgamers"]
    
    def fetch(self, query: ConnectorQuery) -> list[RawContentItem]:
        # 1. Buscar posts populares em subreddits relevantes
        # 2. Filtrar por keywords do jogo/franquia
        # 3. Apenas posts com score > threshold (evitar baixa qualidade)
        ...
    
    def normalize(self, raw) -> KnowledgeItemCreate:
        # post_title → title
        # post_text → content
        # source_type = "discussion"
        ...
```

**Dependências:** `httpx`, Reddit API (OAuth2, gratuito para uso não-comercial)

#### 8.3.5 IGDB Connector (opcional, futuro)

```python
# src/gpcg/application/content_connectors/igdb_connector.py

class IGDBConnector(ContentConnector):
    """Busca dados estruturados no IGDB (requer API key)."""
    # Opcional — apenas se o usuário configurar IGDB_API_KEY
    ...
```

### 8.4 Agendamento de Coleta

```python
# src/gpcg/application/content_intelligence_service.py

class ContentIntelligenceService:
    """Orquestra conectores e mantém o banco de ideias atualizado."""
    
    def run_collection_cycle(self):
        """Ciclo periódico de coleta (rodado por job ou cron)."""
        # 1. Para cada jogo com gameplay disponível:
        #    a. Rodar RSSConnector (notícias recentes)
        #    b. Rodar WikipediaConnector (se não enriquecido ainda)
        #    c. Rodar SteamConnector (se Steam ID disponível)
        # 2. Para cada franquia com jogos:
        #    a. Rodar WikipediaConnector (artigo da franquia)
        # 3. Para cada empresa com jogos:
        #    a. Rodar WikipediaConnector (artigo da empresa)
        # 4. Normalizar todos os items
        # 5. Deduplicar (hash de título + conteúdo)
        # 6. Persistir como KnowledgeItems
        # 7. Score de relevância editorial
        ...
    
    def get_ideas(self, user_id: int, scope: str, 
                  game_id: int = None, limit: int = 20) -> list[KnowledgeItem]:
        """Retorna as melhores ideias de conteúdo para o usuário."""
        # Filtra por escopo (game/franchise/company/general)
        # Ordena por score de relevância + frescor
        # Exclui topics já produzidos
        ...
```

### 8.5 Novo Job Type

```python
class JobType(str, enum.Enum):
    ...
    content_collect = "content_collect"  # NOVO — coleta periódica de conteúdo
```

**Worker capability:** `content_intelligence` (roda no VPS — apenas HTTP, sem GPU)

### 8.6 Rate Limiting e Respeito a Fontes

- Cada conector declara seu rate limit
- `ConnectorManager` respeita rate limits com backoff exponencial
- Cache de resultados em `metadata_json` para evitar re-fetch
- User-Agent identificável em todas as requisições
- Respeito a robots.txt e termos de uso

---

## 9. Knowledge Item

### 9.1 Entidade: `KnowledgeItem` (nova tabela)

```sql
CREATE TABLE knowledge_items (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id),  -- NULL = global (de enriquecimento)
    game_id         INTEGER REFERENCES games(id),  -- nullable para conteúdo geral
    franchise_id    INTEGER REFERENCES franchises(id),
    company_id      INTEGER REFERENCES game_entities(id),
    
    -- Identificação
    title           VARCHAR(500) NOT NULL,
    content         TEXT NOT NULL,
    summary         TEXT,  -- resumo gerado por LLM (para preview)
    
    -- Classificação
    item_type       VARCHAR(30) NOT NULL,  -- news|curiosity|event|lore|history|changelog|release|article|trivia|discussion
    source_type     VARCHAR(30) NOT NULL,  -- rss|wikipedia|steam|reddit|igdb|user_doc|llm_generated
    
    -- Proveniência
    source_url      VARCHAR(1000),
    source_name     VARCHAR(200),  -- "Google News", "Wikipedia", "Steam", etc.
    source_author   VARCHAR(200),
    published_at    DATETIME,  -- data de publicação original (se aplicável)
    collected_at    DATETIME NOT NULL,  -- quando o GPCG coletou
    
    -- Qualidade editorial
    relevance_score FLOAT DEFAULT 0.0,  -- 0-100, quão relevante para o canal
    freshness_score FLOAT DEFAULT 0.0,  -- 0-100, quão atual
    editorial_score FLOAT DEFAULT 0.0,  -- 0-100, potencial editorial (curiosity, surprise)
    composite_score FLOAT DEFAULT 0.0,  -- score combinado para ranking
    
    -- Estado
    status          VARCHAR(20) DEFAULT 'fresh',  -- fresh|used|expired|rejected|hidden
    used_in_video_id INTEGER REFERENCES videos(id),  -- se já virou vídeo
    
    -- Embedding para busca semântica
    embedding       JSON,  -- vetor nomic-embed-text
    
    -- Metadados
    tags            JSON DEFAULT '[]',
    keywords        JSON DEFAULT '[]',
    language        VARCHAR(10) DEFAULT 'pt-BR',
    metadata_json   JSON DEFAULT '{}',
    
    -- Deduplicação
    content_hash    VARCHAR(64),  -- SHA256 do título + conteúdo normalizado
    
    created_at      DATETIME,
    updated_at      DATETIME
);

CREATE INDEX idx_ki_user ON knowledge_items(user_id);
CREATE INDEX idx_ki_game ON knowledge_items(game_id);
CREATE INDEX idx_ki_franchise ON knowledge_items(franchise_id);
CREATE INDEX idx_ki_company ON knowledge_items(company_id);
CREATE INDEX idx_ki_type ON knowledge_items(item_type);
CREATE INDEX idx_ki_status ON knowledge_items(status);
CREATE INDEX idx_ki_composite ON knowledge_items(composite_score DESC);
CREATE UNIQUE INDEX idx_ki_hash ON knowledge_items(content_hash);
```

### 9.2 Relação com Fact

**Estratégia:** `Fact` continua existindo para facts extraídos de documentos do usuário. `KnowledgeItem` é a entidade unificada que o pipeline editorial consulta.

- Facts existentes são migrados para KnowledgeItems com `source_type='user_doc'`, `item_type='fact'`
- Novos facts extraídos de documentos continuam sendo Facts E são espelhados como KnowledgeItems
- O pipeline editorial consulta KnowledgeItems (unificado), não Facts diretamente

**Migração:**
```sql
-- Para cada Fact existente, criar um KnowledgeItem correspondente
INSERT INTO knowledge_items (user_id, game_id, title, content, item_type, source_type, ...)
SELECT user_id, game_id, claim, claim, 'fact', 'user_doc', ...
FROM facts;
```

### 9.3 Scoring de Knowledge Items

```python
def compute_composite_score(item: KnowledgeItem) -> float:
    """Score composto para ranking de ideias."""
    relevance = item.relevance_score  # 0-100
    freshness = item.freshness_score  # 0-100
    editorial = item.editorial_score  # 0-100
    
    # Pesos dependem do tipo
    if item.item_type == "news":
        # Notícias valorizam frescor
        return relevance * 0.3 + freshness * 0.4 + editorial * 0.3
    elif item.item_type == "curiosity":
        # Curiosidades valorizam editorial (surpresa, curiosity gap)
        return relevance * 0.3 + freshness * 0.1 + editorial * 0.6
    elif item.item_type == "lore":
        # Lore é evergreen — frescor não importa
        return relevance * 0.4 + freshness * 0.0 + editorial * 0.6
    else:
        return relevance * 0.33 + freshness * 0.33 + editorial * 0.34
```

### 9.4 Deduplicação

- `content_hash` = SHA256(normalize(title) + normalize(content_first_500_chars))
- UNIQUE constraint previne duplicatas
- Se um conector trazer o mesmo item: update dos scores, não duplicar

---

## 10. Knowledge Graph

### 10.1 Avaliação Técnica: Grafo Relacional vs Banco de Grafos

| Critério | Relacional (SQL) | Banco de Grafos (Neo4j) |
|----------|------------------|------------------------|
| Complexidade operacional | Baixa (já temos SQLite/Postgres) | Alta (outro container, outra linguagem) |
| Queries típicas | 1-3 JOINs (jogo→franquia→empresa) | Traversals de profundidade arbitrária |
| Volume de dados | ~10K nós, ~50K arestas (estimativa) | Neo4j brilha em milhões |
| Sync com DB principal | Nativo (mesmo banco) | Requer sync entre bancos |
| Backup/Restore | Unificado | Separado |
| Evolução | Adicionar coluna/tabela | Alterar schema de grafo |
| Team knowledge | SQL é universal | Cypher é nicho |

**Decisão:** **Grafo relacional.** O domínio de jogos é raso (raramente 3+ hops), o volume é pequeno, e a complexidade de adicionar Neo4j não se justifica.

### 10.2 Modelo de Grafo Relacional

#### Entidade: `GameRelationship` (nova tabela)

```sql
CREATE TABLE game_relationships (
    id              INTEGER PRIMARY KEY,
    from_entity_type VARCHAR(30) NOT NULL,  -- game|franchise|company|character|genre|platform
    from_entity_id  INTEGER NOT NULL,
    to_entity_type  VARCHAR(30) NOT NULL,
    to_entity_id    INTEGER NOT NULL,
    relationship    VARCHAR(50) NOT NULL,  -- ver lista abaixo
    weight          FLOAT DEFAULT 1.0,
    source          VARCHAR(50) DEFAULT 'manual',  -- manual|wikidata|steam|inferred
    metadata_json   JSON DEFAULT '{}',
    created_at      DATETIME
);

CREATE INDEX idx_rel_from ON game_relationships(from_entity_type, from_entity_id);
CREATE INDEX idx_rel_to ON game_relationships(to_entity_type, to_entity_id);
CREATE INDEX idx_rel_type ON game_relationships(relationship);
CREATE UNIQUE INDEX idx_rel_unique ON game_relationships(
    from_entity_type, from_entity_id, to_entity_type, to_entity_id, relationship
);
```

### 10.3 Tipos de Relacionamento

| Relacionamento | from → to | Exemplo |
|----------------|-----------|---------|
| `developed_by` | game → company | Bully → Rockstar Vancouver |
| `published_by` | game → company | Bully → Rockstar Games |
| `part_of_franchise` | game → franchise | RE4 → Resident Evil |
| `part_of_series` | game → series | RE4 → Resident Evil (main series) |
| `features_character` | game → character | Bully → Jimmy Hopkins |
| `available_on_platform` | game → platform | Bully → PS2 |
| `uses_engine` | game → engine | Bully → RenderWare |
| `belongs_to_genre` | game → genre | Bully → action-adventure |
| `has_theme` | game → theme | Bully → school |
| `subsidiary_of` | company → company | Rockstar Games → Take-Two |
| `parent_franchise` | franchise → franchise | (sub-franquias) |
| `similar_to` | game → game | Bully → GTA: San Andreas |
| `inspired_by` | game → game | Bully → Canis Canem Edit |
| `spinoff_of` | game → game | (spin-offs) |
| `sequel_to` | game → game | RE5 → RE4 |
| `prequel_to` | game → game | RE0 → RE1 |
| `remake_of` | game → game | RE4 Remake → RE4 |
| `same_universe` | game → game | (jogos no mesmo universo) |
| `competes_with` | game → game | (jogos concorrentes) |

### 10.4 Queries do Knowledge Graph

```sql
-- Encontrar todos os jogos da mesma franquia
SELECT g2.id, g2.canonical_name 
FROM games g1
JOIN game_relationships r ON r.from_entity_type='game' AND r.from_entity_id=g1.id
JOIN games g2 ON r.to_entity_type='game' AND r.to_entity_id=g2.id
WHERE g1.id = ? AND r.relationship = 'part_of_franchise'
   OR r.relationship = 'sequel_to' OR r.relationship = 'prequel_to'
   OR r.relationship = 'spinoff_of';

-- Encontrar jogos da mesma empresa (developer)
SELECT g2.id, g2.canonical_name
FROM games g1
JOIN game_relationships r1 ON r1.from_entity_type='game' AND r1.from_entity_id=g1.id
    AND r1.relationship = 'developed_by'
JOIN game_relationships r2 ON r2.from_entity_type='game' AND r2.to_entity_id=r1.to_entity_id
    AND r2.relationship = 'developed_by'
JOIN games g2 ON r2.from_entity_id=g2.id
WHERE g1.id = ? AND g2.id != g1.id;

-- Encontrar jogos do mesmo gênero
SELECT g2.id, g2.canonical_name
FROM games g1
JOIN game_relationships r1 ON r1.from_entity_type='game' AND r1.from_entity_id=g1.id
    AND r1.relationship = 'belongs_to_genre'
JOIN game_relationships r2 ON r2.to_entity_type='genre' AND r2.to_entity_id=r1.to_entity_id
    AND r2.relationship = 'belongs_to_genre'
JOIN games g2 ON r2.from_entity_id=g2.id
WHERE g1.id = ? AND g2.id != g1.id;
```

### 10.5 População do Grafo

O grafo é populado automaticamente durante o **Game Knowledge Enrichment** (seção 6):

1. Ao enriquecer um jogo, criar relacionamentos:
   - `developed_by` → GameEntity (developer)
   - `published_by` → GameEntity (publisher)
   - `part_of_franchise` → Franchise
   - `belongs_to_genre` → para cada gênero
   - `has_theme` → para cada tema
   - `features_character` → para cada personagem
   - `available_on_platform` → para cada plataforma

2. Relacionamentos entre jogos (sequel, spinoff, remake) vindos do Wikidata

3. Relacionamentos `similar_to` podem ser inferidos por:
   - Mesmo gênero + mesma franquia → peso alto
   - Mesmo gênero + mesma empresa → peso médio
   - Mesmo gênero apenas → peso baixo

---

## 11. Seleção Inteligente de Gameplay

### 11.1 Algoritmo de Seleção Editorial

```python
# src/gpcg/application/gameplay_matcher.py

class GameplayMatcher:
    """Seleciona gameplay compatível para um conteúdo, considerando o Knowledge Graph."""
    
    def find_compatible_gameplays(
        self,
        session: Session,
        user_id: int,
        knowledge_item: KnowledgeItem,
        scope: str,  # "game" | "franchise" | "company" | "genre" | "theme"
        use_public: bool = False,
        target_duration: float = 60.0,
    ) -> list[SelectedGameplay]:
        """
        Algoritmo:
        1. Determinar jogos candidatos baseado no escopo:
           - game: apenas o game_id do knowledge_item
           - franchise: jogos da mesma franquia (via Knowledge Graph)
           - company: jogos da mesma empresa (via Knowledge Graph)
           - genre: jogos do mesmo gênero (via Knowledge Graph)
           - theme: jogos com o mesmo tema
        
        2. Para cada jogo candidato:
           a. Buscar GameplaySources do usuário (ou públicas se use_public)
           b. Filtrar por ingestion_status='ready'
           c. Se use_public: incluir gameplays públicas de outros usuários
        
        3. Para cada GameplaySource:
           a. Se busca semântica: usar embeddings dos GameplayEvents
           b. Score de compatibilidade = semantic_similarity * graph_proximity * interesting_score
           
        4. Ordenar por score, selecionar até preencher target_duration
        """
        ...
    
    def _get_candidate_game_ids(
        self, session, game_id: int, scope: str
    ) -> list[tuple[int, float]]:
        """Retorna [(game_id, proximity_weight)] baseado no escopo."""
        # scope="game": [(game_id, 1.0)]
        # scope="franchise": [(game_id, 1.0), (franchise_mate1, 0.8), ...]
        # scope="company": [(game_id, 1.0), (company_mate1, 0.6), ...]
        # scope="genre": [(game_id, 1.0), (genre_mate1, 0.4), ...]
        ...
    
    def _score_gameplay_compatibility(
        self,
        gameplay_events: list[GameplayEvent],
        knowledge_item: KnowledgeItem,
        game_proximity: float,
    ) -> float:
        """Score de compatibilidade entre gameplay e conteúdo."""
        # semantic_similarity = cosine(embedding(event), embedding(knowledge_item))
        # score = semantic_similarity * game_proximity * event.interesting_score
        ...
```

### 11.2 Scoring de Compatibilidade

```
score = semantic_similarity(event, content)    # 0-1 (cosine de embeddings)
      × game_proximity                          # 0-1 (peso do grafo)
      × event.interesting_score                 # 0-1 (score do VLM)
      × freshness_bonus                         # 1.0-1.2 (gameplay não usada recentemente)
```

### 11.3 Fallback em Cascata

```
1. Escopo "game" → se gameplay suficiente: usar
2. Senão: expandir para "franchise" → se suficiente: usar
3. Senão: expandir para "company" → se suficiente: usar
4. Senão: expandir para "genre" → se suficiente: usar
5. Senão: usar gameplays públicas (se opt-in)
6. Senão: marcar vídeo como "sem gameplay compatível" (usar background genérico)
```

### 11.4 Embeddings em GameplayEvents

Adicionar `event_embedding` aos GameplayEvents:

```python
# Durante o mapping (worker), após gerar a description:
embedding = self.llm.embed(event.description)
event.event_embedding = embedding
```

**Busca semântica:**
```python
def search_events_semantic(session, game_ids, query_embedding, top_k=20):
    """Busca eventos por similaridade de embedding (cosine)."""
    events = session.query(GameplayEvent).join(GameplaySource).filter(
        GameplaySource.game_id.in_(game_ids),
        GameplaySource.ingestion_status == 'ready',
        GameplayEvent.event_embedding.isnot(None),
    ).all()
    
    scored = []
    for ev in events:
        sim = cosine_similarity(query_embedding, ev.event_embedding)
        scored.append((sim, ev))
    
    scored.sort(key=lambda x: x[0], reverse=True)
    return [ev for _, ev in scored[:top_k]]
```

---

## 12. Gameplays Públicas

### 12.1 Modelo

#### Entidade: `GameplayVisibility` (enum)

```python
class GameplayVisibility(str, enum.Enum):
    private = "private"      # apenas o usuário pode usar
    unlisted = "unlisted"    # não aparece em buscas públicas, mas acessível via link
    public = "public"        # outros usuários podem usar como fallback
```

#### Alteração em `GameplaySource`:

```sql
-- Nova coluna:
visibility VARCHAR(20) DEFAULT 'private';  -- private|unlisted|public
```

#### Alteração em `User`:

```sql
-- Nova coluna em users (ou em Automation.config):
allow_public_gameplays BOOLEAN DEFAULT FALSE;  -- opt-in para usar gameplays públicas de outros
```

### 12.2 Fluxo de Consentimento

1. Usuário marca uma gameplay como `public` (na UI de Mídias)
2. A gameplay fica disponível para outros usuários que optaram em `allow_public_gameplays`
3. Quando o `GameplayMatcher` não encontra gameplays privadas suficientes, busca públicas
4. Gameplays públicas são anonimizadas (sem filename original, sem user_id exposto)
5. O vídeo resultante credita "gameplay fornecida por [canal]" (opcional)

### 12.3 Queries

```sql
-- Buscar gameplays públicas compatíveis
SELECT gs.* FROM gameplay_sources gs
JOIN games g ON gs.game_id = g.id
WHERE gs.visibility = 'public'
  AND gs.user_id != ?  -- não do próprio usuário
  AND gs.ingestion_status = 'ready'
  AND g.id IN (candidate_game_ids)
  AND gs.duration >= ?  -- duração mínima
ORDER BY gs.duration DESC;
```

### 12.4 Considerações

- **Storage:** Gameplays públicas não são copiadas — o worker baixa do dono original (ou de um cache compartilhado)
- **Rate limiting:** Limite de uso de gameplays públicas por usuário por dia
- **Revogação:** Dono pode mudar para `private` a qualquer momento — vídeos já gerados não são afetados
- **Métricas:** `times_used_publicly` em GameplaySource para feedback ao dono

---

## 13. Configuração das Automações

### 13.1 Novas Configurações em `Automation.config`

```json
{
  "content_intelligence": {
    "enabled": true,
    "produce_news": true,
    "produce_curiosities": true,
    "produce_evergreen": true,
    "produce_general_content": true,
    "content_scope": "franchise",
    "news_max_age_hours": 48,
    "min_editorial_score": 50,
    "collection_interval_hours": 6
  },
  "gameplay_selection": {
    "scope": "franchise",
    "allow_public_gameplays": false,
    "min_gameplay_duration": 30,
    "fallback_strategy": "expand_scope"
  },
  "knowledge_sources": {
    "rss_enabled": true,
    "wikipedia_enabled": true,
    "steam_enabled": true,
    "reddit_enabled": false,
    "igdb_enabled": false,
    "custom_rss_feeds": []
  }
}
```

### 13.2 Descrição das Configurações

#### Content Intelligence

| Config | Tipo | Default | Descrição |
|--------|------|---------|-----------|
| `enabled` | bool | false | Liga/desliga content intelligence |
| `produce_news` | bool | true | Produz vídeos de notícias |
| `produce_curiosities` | bool | true | Produz vídeos de curiosidades |
| `produce_evergreen` | bool | true | Produz conteúdo evergreen (lore, história) |
| `produce_general_content` | bool | false | Produz conteúdo geral (não específico de jogo) |
| `content_scope` | str | "game" | Escopo: game, franchise, company, genre, theme |
| `news_max_age_hours` | int | 48 | Idade máxima de notícias para considerar |
| `min_editorial_score` | int | 50 | Score mínimo do KnowledgeItem |
| `collection_interval_hours` | int | 6 | Intervalo entre coletas |

#### Gameplay Selection

| Config | Tipo | Default | Descrição |
|--------|------|---------|-----------|
| `scope` | str | "game" | Escopo de busca: game, franchise, company, genre |
| `allow_public_gameplays` | bool | false | Usar gameplays públicas como fallback |
| `min_gameplay_duration` | int | 30 | Duração mínima de gameplay (segundos) |
| `fallback_strategy` | str | "expand_scope" | expand_scope, use_public, skip |

#### Knowledge Sources

| Config | Tipo | Default | Descrição |
|--------|------|---------|-----------|
| `rss_enabled` | bool | true | Habilita conector RSS |
| `wikipedia_enabled` | bool | true | Habilita conector Wikipedia |
| `steam_enabled` | bool | true | Habilita conector Steam |
| `reddit_enabled` | bool | false | Habilita conector Reddit |
| `igdb_enabled` | bool | false | Habilita conector IGDB (requer API key) |
| `custom_rss_feeds` | list | [] | URLs de feeds RSS customizados |

### 13.3 Impacto no Editorial Strategy

O `EditorialStrategyService.decide_next_video()` será estendido para:

1. **Atual:** Decide qual jogo + fact usar (baseado em inventário do usuário)
2. **Proposto:** Decide qual **KnowledgeItem** usar, considerando:
   - KnowledgeItems do usuário (facts de documentos)
   - KnowledgeItems globais (de content intelligence)
   - Configurações de content_scope e tipos permitidos
   - Histórico editorial (evitar repetição)
   - Disponibilidade de gameplay compatível (via Knowledge Graph)

---

## 14. Novos Módulos e Serviços

### 14.1 Módulos de Domínio

| Módulo | Arquivo | Responsabilidade |
|--------|---------|------------------|
| Game Registry | `domain/game_registry.py` | CRUD de jogos canônicos, aliases, deduplicação |
| Knowledge Graph | `domain/knowledge_graph.py` | Queries de relacionamentos, compatibilidade |
| Gameplay Matcher | `application/gameplay_matcher.py` | Seleção cross-game de gameplay |

### 14.2 Módulos de Aplicação

| Módulo | Arquivo | Responsabilidade |
|--------|---------|------------------|
| Game Enrichment Service | `application/game_enrichment_service.py` | Pipeline de enriquecimento de jogos |
| Content Intelligence Service | `application/content_intelligence_service.py` | Orquestra conectores, mantém banco de ideias |
| Knowledge Item Service | `application/knowledge_item_service.py` | CRUD + scoring de KnowledgeItems |
| Gameplay Matcher | `application/gameplay_matcher.py` | Seleção inteligente cross-game |

### 14.3 Conectores

| Módulo | Arquivo | Responsabilidade |
|--------|---------|------------------|
| Base Connector | `application/content_connectors/base.py` | Interface abstrata |
| RSS Connector | `application/content_connectors/rss_connector.py` | Google News + blogs |
| Wikipedia Connector | `application/content_connectors/wikipedia_connector.py` | Wikipedia + Wikidata |
| Steam Connector | `application/content_connectors/steam_connector.py` | Steam Web API |
| Reddit Connector | `application/content_connectors/reddit_connector.py` | Reddit API |
| IGDB Connector | `application/content_connectors/igdb_connector.py` | IGDB API (opcional) |
| Connector Manager | `application/content_connectors/manager.py` | Orquestra conectores, rate limiting |

### 14.4 Infraestrutura

| Módulo | Arquivo | Responsabilidade |
|--------|---------|------------------|
| Wikidata Client | `infrastructure/wikidata_client.py` | SPARQL queries no Wikidata |
| Wikipedia Client | `infrastructure/wikipedia_client.py` | REST API da Wikipedia |
| Steam Client | `infrastructure/steam_client.py` | Steam Web API |
| Reddit Client | `infrastructure/reddit_client.py` | Reddit OAuth2 API |

### 14.5 API Routes

| Módulo | Arquivo | Responsabilidade |
|--------|---------|------------------|
| Game Registry Routes | `api/game_registry_routes.py` | CRUD de jogos, aliases, enriquecimento |
| Knowledge Graph Routes | `api/knowledge_graph_routes.py` | Visualização e query do grafo |
| Knowledge Item Routes | `api/knowledge_item_routes.py` | Lista, aprova, rejeita ideias |
| Content Intelligence Routes | `api/content_intelligence_routes.py` | Configura conectores, trigger coleta |

---

## 15. Alterações de Banco de Dados

### 15.1 Novas Tabelas

| Tabela | Propósito |
|--------|-----------|
| `game_aliases` | Aliases individuais indexáveis |
| `game_entities` | Empresas, estúdios, pessoas, plataformas, engines |
| `franchises` | Franquias de jogos |
| `series` | Séries dentro de franquias |
| `characters` | Personagens de jogos |
| `game_relationships` | Knowledge Graph (arestas) |
| `knowledge_items` | Banco de ideias normalizado |

### 15.2 Colunas Novas em Tabelas Existentes

#### `games`
| Coluna | Tipo | Default | Notas |
|--------|------|---------|-------|
| `slug` | VARCHAR(200) UNIQUE | — | Slug canônico |
| `description` | TEXT | NULL | Resumo do jogo |
| `release_date` | DATE | NULL | Data de lançamento |
| `developer_id` | INTEGER FK | NULL | → game_entities.id |
| `publisher_id` | INTEGER FK | NULL | → game_entities.id |
| `franchise_id` | INTEGER FK | NULL | → franchises.id |
| `series_id` | INTEGER FK | NULL | → series.id |
| `genres` | JSON | [] | Lista de gêneros |
| `themes` | JSON | [] | Lista de temas |
| `engine` | VARCHAR(200) | NULL | Engine do jogo |
| `keywords` | JSON | [] | Palavras-chave |
| `external_ids` | JSON | {} | IDs externos |
| `enrichment_status` | VARCHAR(20) | 'pending' | Status do enriquecimento |
| `enrichment_source` | VARCHAR(50) | NULL | Fonte do enriquecimento |
| `enrichment_at` | DATETIME | NULL | Data do enriquecimento |
| `is_canonical` | BOOLEAN | TRUE | Se é o registro canônico |
| `merged_into_id` | INTEGER FK | NULL | → games.id (se mesclado) |
| `historical_context` | TEXT | NULL | Contexto histórico (LLM) |
| `lore_summary` | TEXT | NULL | Resumo da lore |
| `trivia` | JSON | [] | Curiosidades estruturadas |
| `content_warnings` | JSON | [] | Avisos de conteúdo |

#### `gameplay_sources`
| Coluna | Tipo | Default | Notas |
|--------|------|---------|-------|
| `visibility` | VARCHAR(20) | 'private' | private, unlisted, public |
| `times_used_publicly` | INTEGER | 0 | Contador de uso público |

#### `gameplay_events`
| Coluna | Tipo | Default | Notas |
|--------|------|---------|-------|
| `event_embedding` | JSON | NULL | Embedding vetorial |
| `game_id` | INTEGER | NULL | Denormalizado para queries |
| `franchise_id` | INTEGER | NULL | Denormalizado para cross-game |

#### `users`
| Coluna | Tipo | Default | Notas |
|--------|------|---------|-------|
| `allow_public_gameplays` | BOOLEAN | FALSE | Opt-in para gameplays públicas |

#### `automations`
- Sem novas colunas — configurações vão em `config` JSON

### 15.3 Enums Novos

```python
class GameEnrichmentStatus(str, enum.Enum):
    pending = "pending"
    enriching = "enriching"
    enriched = "enriched"
    failed = "failed"
    manual = "manual"  # enriquecido manualmente, não sobrescrever

class KnowledgeItemType(str, enum.Enum):
    news = "news"
    curiosity = "curiosity"
    event = "event"
    lore = "lore"
    history = "history"
    changelog = "changelog"
    release = "release"
    article = "article"
    trivia = "trivia"
    discussion = "discussion"
    fact = "fact"  # migrado de Fact

class KnowledgeItemSource(str, enum.Enum):
    rss = "rss"
    wikipedia = "wikipedia"
    steam = "steam"
    reddit = "reddit"
    igdb = "igdb"
    user_doc = "user_doc"
    llm_generated = "llm_generated"

class KnowledgeItemStatus(str, enum.Enum):
    fresh = "fresh"
    used = "used"
    expired = "expired"
    rejected = "rejected"
    hidden = "hidden"

class GameplayVisibility(str, enum.Enum):
    private = "private"
    unlisted = "unlisted"
    public = "public"

class EntityType(str, enum.Enum):
    company = "company"
    developer = "developer"
    publisher = "publisher"
    person = "person"
    platform = "platform"
    engine = "engine"
    genre = "genre"
    theme = "theme"

class RelationshipType(str, enum.Enum):
    developed_by = "developed_by"
    published_by = "published_by"
    part_of_franchise = "part_of_franchise"
    part_of_series = "part_of_series"
    features_character = "features_character"
    available_on_platform = "available_on_platform"
    uses_engine = "uses_engine"
    belongs_to_genre = "belongs_to_genre"
    has_theme = "has_theme"
    subsidiary_of = "subsidiary_of"
    similar_to = "similar_to"
    inspired_by = "inspired_by"
    spinoff_of = "spinoff_of"
    sequel_to = "sequel_to"
    prequel_to = "prequel_to"
    remake_of = "remake_of"
    same_universe = "same_universe"
    competes_with = "competes_with"
```

### 15.4 Job Types Novos

```python
class JobType(str, enum.Enum):
    ...
    game_enrich = "game_enrich"          # Enriquecimento de jogo
    content_collect = "content_collect"  # Coleta de conteúdo (content intelligence)
```

### 15.5 Worker Capabilities Novas

```python
class WorkerCapability(str, enum.Enum):
    ...
    enrichment = "enrichment"              # Enriquecimento de jogos (VPS, sem GPU)
    content_intelligence = "content_intelligence"  # Coleta de conteúdo (VPS, sem GPU)
```

---

## 16. Alterações de API

### 16.1 Game Registry Routes (`api/game_registry_routes.py`)

| Método | Path | Descrição | Auth |
|--------|------|-----------|------|
| GET | `/api/games/registry` | Lista jogos canônicos (global) com paginação | user |
| GET | `/api/games/{slug}` | Detalhes de um jogo canônico | user |
| POST | `/api/games/{id}/enrich` | Trigger enriquecimento manual | user |
| GET | `/api/games/{id}/enrichment-status` | Status do enriquecimento | user |
| PUT | `/api/games/{id}` | Editar dados do jogo (manual override) | user |
| POST | `/api/games/merge` | Mesclar jogos duplicados | admin |
| GET | `/api/games/{id}/aliases` | Lista aliases | user |
| POST | `/api/games/{id}/aliases` | Adicionar alias | user |
| DELETE | `/api/games/{id}/aliases/{alias_id}` | Remover alias | user |
| GET | `/api/games/search?q={query}` | Buscar por nome/alias | user |

### 16.2 Knowledge Graph Routes (`api/knowledge_graph_routes.py`)

| Método | Path | Descrição | Auth |
|--------|------|-----------|------|
| GET | `/api/knowledge-graph/games/{id}/related` | Jogos relacionados | user |
| GET | `/api/knowledge-graph/franchises/{id}/games` | Jogos da franquia | user |
| GET | `/api/knowledge-graph/companies/{id}/games` | Jogos da empresa | user |
| GET | `/api/knowledge-graph/games/{id}/graph` | Grafo visual (nós + arestas) | user |
| GET | `/api/franchises` | Lista franquias | user |
| GET | `/api/companies` | Lista empresas | user |
| GET | `/api/characters` | Lista personagens | user |

### 16.3 Knowledge Item Routes (`api/knowledge_item_routes.py`)

| Método | Path | Descrição | Auth |
|--------|------|-----------|------|
| GET | `/api/knowledge-items` | Lista ideias (filtros: game, type, status) | user |
| GET | `/api/knowledge-items/{id}` | Detalhe de uma ideia | user |
| POST | `/api/knowledge-items/{id}/reject` | Rejeitar ideia | user |
| POST | `/api/knowledge-items/{id}/approve` | Aprovar ideia (forçar produção) | user |
| POST | `/api/knowledge-items/collect` | Trigger coleta manual | user |
| GET | `/api/knowledge-items/stats` | Stats do banco de ideias | user |

### 16.4 Content Intelligence Routes (`api/content_intelligence_routes.py`)

| Método | Path | Descrição | Auth |
|--------|------|-----------|------|
| GET | `/api/content-intelligence/status` | Status dos conectores | user |
| POST | `/api/content-intelligence/collect` | Trigger coleta manual | user |
| GET | `/api/content-intelligence/sources` | Lista fontes configuradas | user |
| PUT | `/api/content-intelligence/sources` | Atualizar config de fontes | user |

### 16.5 Gameplay Visibility (em routes.py existente)

| Método | Path | Descrição | Auth |
|--------|------|-----------|------|
| PUT | `/api/sources/{id}/visibility` | Mudar visibilidade da gameplay | user |
| GET | `/api/sources/public/available` | Gameplays públicas disponíveis | user |

### 16.6 Alterações em Endpoints Existentes

- `GET /api/games` — retornar dados enriquecidos (description, developer, franchise, etc.)
- `GET /api/dashboard` — incluir stats de KnowledgeItems, enriquecimento
- `POST /api/gameplays/upload` — após criar GameplaySource, trigger game_enrich job se jogo não enriquecido
- `GET /api/jobs/{id}/data` — incluir KnowledgeItems, Knowledge Graph data, Game enriquecido

---

## 17. Alterações de Frontend

### 17.1 Nova Página: Game Registry

**Rota:** `/gpcg/games`

- Lista de jogos canônicos com cards ricos (nome, capa, developer, franquia, gêneros)
- Busca por nome/alias
- Detalhe do jogo: knowledge completo, grafo de relacionamentos (visual)
- Botão "Enriquecer" para trigger manual
- Indicador de status de enriquecimento
- Editor de aliases

### 17.2 Nova Página: Knowledge Graph Explorer

**Rota:** `/gpcg/knowledge-graph`

- Visualização interativa do grafo (D3.js ou vis.js)
- Filtro por tipo de nó (jogo, franquia, empresa, gênero)
- Click em nó mostra detalhes e relacionados
- Busca por entidade

### 17.3 Nova Página: Content Ideas (Banco de Ideias)

**Rota:** `/gpcg/ideas`

- Lista de KnowledgeItems com filtros (tipo, jogo, status, score)
- Card de cada ideia: título, resumo, fonte, score, tipo
- Ações: aprovar (forçar produção), rejeitar, ocultar
- Botão "Coletar agora" para trigger manual
- Stats: total de ideias, por tipo, por fonte

### 17.4 Alteração: Página de Automação

- Nova seção "Content Intelligence" com toggles:
  - Produzir notícias
  - Produzir curiosidades
  - Produzir evergreen
  - Escopo (game/franchise/company/genre)
- Nova seção "Gameplay Selection" com:
  - Escopo de busca
  - Permitir gameplays públicas
- Nova seção "Fontes de Conhecimento" com toggles por conector

### 17.5 Alteração: Página de Mídias

- Cada gameplay tem dropdown de visibilidade (Private/Unlisted/Public)
- Indicador se a gameplay está sendo usada publicamente

### 17.6 Alteração: Página de Dashboard

- Card "Banco de Ideias": total de ideias, ideias frescas
- Card "Conhecimento": jogos enriquecidos, pendentes
- Card "Grafo": relacionamentos mapeados

---

## 18. Alterações de Worker

### 18.1 Novos Job Types no Remote Worker

```python
# Em _process_job:
elif job_type == "game_enrich":
    self._process_enrichment_job(job)
elif job_type == "content_collect":
    self._process_content_collection_job(job)
```

### 18.2 Processamento de Enriquecimento

```python
def _process_enrichment_job(self, job: dict):
    """Enriquece um jogo com dados de fontes públicas.
    
    Pode rodar no VPS (sem GPU) ou no worker (com LLM local).
    """
    game_id = job["game_id"]
    
    # Buscar dados do jogo da API
    resp = self.client.get(f"/api/games/{game_id}")
    game_data = resp.json()
    
    # Pipeline de enriquecimento
    service = GameEnrichmentService(llm=get_llm())
    result = service.enrich_game(game_id)
    
    # Enviar resultados de volta
    self.submit_job_result(job["id"], status="completed", 
                          artifacts={"enrichment": result.to_dict()})
```

### 18.3 Processamento de Content Collection

```python
def _process_content_collection_job(self, job: dict):
    """Coleta conteúdo de fontes públicas.
    
    Roda no VPS (apenas HTTP, sem GPU).
    """
    user_id = job["user_id"]
    
    service = ContentIntelligenceService()
    items = service.run_collection_cycle(user_id=user_id)
    
    self.submit_job_result(job["id"], status="completed",
                          artifacts={"items_collected": len(items)})
```

### 18.4 Worker no VPS vs Worker Local

| Job Type | Onde roda | GPU necessária? |
|----------|-----------|-----------------|
| mapping | Worker local (GPU) | Sim (VLM + ASR) |
| generation | Worker local (GPU) | Sim (LLM + render) |
| knowledge_index | Worker local (GPU) | Sim (embeddings) |
| game_enrich | VPS ou worker local | Não (apenas HTTP + LLM leve) |
| content_collect | VPS | Não (apenas HTTP) |

**Recomendação:** `game_enrich` e `content_collect` rodam no VPS worker (legacy worker em `application/worker.py`), não no remote worker. Isso evita ocupar a GPU do worker local com tarefas que não precisam de GPU.

### 18.5 Alteração em `local_db_sync.py`

O `populate_local_db` precisa incluir novos dados:

```python
def populate_local_db(job_data, db_path, storage_root):
    # ... existente ...
    
    # NOVO: Popular KnowledgeItems
    for ki in job_data.get("knowledge_items", []):
        session.add(KnowledgeItem(**ki))
    
    # NOVO: Popular Game com dados enriquecidos
    game_data = job_data.get("game", {})
    # Incluir description, developer, franchise, genres, etc.
    
    # NOVO: Popular Knowledge Graph (relacionamentos)
    for rel in job_data.get("game_relationships", []):
        session.add(GameRelationship(**rel))
    
    # NOVO: Popular GameEntities (developer, publisher)
    for entity in job_data.get("game_entities", []):
        session.add(GameEntity(**entity))
```

### 18.6 Alteração em `get_job_data` (worker_routes.py)

```python
def get_job_data(job_id, ...):
    # ... existente ...
    
    # NOVO: Incluir KnowledgeItems relevantes
    knowledge_items = db.query(KnowledgeItem).filter(
        or_(
            KnowledgeItem.user_id == job.user_id,
            KnowledgeItem.user_id.is_(None),  # globais
        ),
        KnowledgeItem.status == "fresh",
    ).order_by(KnowledgeItem.composite_score.desc()).limit(50).all()
    data["knowledge_items"] = [ki.to_dict() for ki in knowledge_items]
    
    # NOVO: Incluir Game enriquecido
    game = db.query(Game).filter(Game.id == job.game_id).first()
    if game:
        data["game"] = {
            ...campos existentes...,
            "description": game.description,
            "developer": game.developer.canonical_name if game.developer else None,
            "franchise": game.franchise.canonical_name if game.franchise else None,
            "genres": game.genres,
            "themes": game.themes,
            "keywords": game.keywords,
        }
    
    # NOVO: Incluir relacionamentos do Knowledge Graph
    relationships = db.query(GameRelationship).filter(
        GameRelationship.from_entity_type == "game",
        GameRelationship.from_entity_id == job.game_id,
    ).all()
    data["game_relationships"] = [r.to_dict() for r in relationships]
```

---

## 19. Pipeline Editorial Revisado

### 19.1 Pipeline Atual

```
1. content_planning → seleciona Fact do jogo
2. story_finding → encontra ângulo
3. editorial_planning → VideoCreativePlan
4. creative_engine → hooks, punchlines
5. script → roteiro
6. humanization → naturalidade
7. script_review → crítica
8. tts → narração
9. gameplay_selection → clips do jogo
10. music_selection → BGM
11. render_plan → plano de render
12. render → vídeo
13. qa → qualidade
14. metadata_generation → título, tags
15. youtube_upload → publicação
```

### 19.2 Pipeline Proposto

```
1. content_planning → seleciona KnowledgeItem (não apenas Fact)
   - Fonte: KnowledgeItems do usuário + globais (content intelligence)
   - Filtro: por tipo (news, curiosity, evergreen), escopo, score
   - Considera: Knowledge Graph para expandir escopo
   
2. story_finding → encontra ângulo (igual)
3. editorial_planning → VideoCreativePlan (igual, mas com contexto enriquecido)
   - Recebe: Game.description, Game.historical_context, Game.lore_summary
   - Recebe: Knowledge Graph relationships para contexto
   
4. creative_engine → hooks, punchlines (igual)
5. script → roteiro (igual, mas com mais contexto)
6. humanization → naturalidade (igual)
7. script_review → crítica (igual)
8. tts → narração (igual)

9. gameplay_selection → clips INTELIGENTES (MUDANÇA MAIOR)
   - Usa GameplayMatcher (não GameplayRetriever diretamente)
   - Busca cross-game via Knowledge Graph
   - Score: semantic_similarity × graph_proximity × interesting_score
   - Fallback em cascata: game → franchise → company → genre → public
   
10. music_selection → BGM (igual)
11. render_plan → plano de render (igual)
12. render → vídeo (igual)
13. qa → qualidade (igual)
14. metadata_generation → título, tags (igual)
15. youtube_upload → publicação (igual)
```

### 19.3 Mudança no ContentPlanningService

```python
class ContentPlanningService:
    def plan_for_game(self, session, game_id, user_id, ...):
        # ATUAL: carrega Facts do jogo
        # PROPOSTO: carrega KnowledgeItems do jogo + relacionados
        
        items = session.query(KnowledgeItem).filter(
            or_(
                KnowledgeItem.game_id == game_id,
                # Itens da mesma franquia
                KnowledgeItem.franchise_id == game.franchise_id,
                # Itens da mesma empresa
                KnowledgeItem.company_id == game.developer_id,
            ),
            KnowledgeItem.status == "fresh",
            KnowledgeItem.composite_score >= min_score,
        ).order_by(KnowledgeItem.composite_score.desc()).limit(15).all()
        
        # Resto igual: envia para LLM decidir
        ...
```

### 19.4 Mudança no EditorialStrategyService

```python
class EditorialStrategyService:
    def decide_next_video(self, session, user_id):
        # ATUAL: inventário por jogo
        # PROPOSTO: inventário por jogo + KnowledgeItems + escopo configurado
        
        config = self._get_automation_config(session, user_id)
        scope = config.get("content_scope", "game")
        
        if scope == "game":
            # Como hoje: decide qual jogo
            decision = self._decide_by_game(session, user_id)
        elif scope == "franchise":
            # Decide qual franquia, depois qual jogo dentro dela
            decision = self._decide_by_franchise(session, user_id)
        elif scope == "company":
            # Decide qual empresa, depois qual jogo
            decision = self._decide_by_company(session, user_id)
        elif scope == "genre":
            # Decide qual gênero, depois qual jogo
            decision = self._decide_by_genre(session, user_id)
        
        # NOVO: considerar KnowledgeItems disponíveis
        # Se há uma notícia quente sobre uma franquia, priorizar
        ...
```

---

## 20. Impacto Arquitetural Completo

### 20.1 Banco de Dados

| Aspecto | Impacto |
|---------|---------|
| Novas tabelas | 7 (game_aliases, game_entities, franchises, series, characters, game_relationships, knowledge_items) |
| Novas colunas | ~25 em 4 tabelas existentes |
| Novos enums | 6 |
| Migração de dados | Facts → KnowledgeItems; Game.user_id → deprecated; aliases JSON → game_aliases |
| Performance | Novos índices em game_aliases, knowledge_items, game_relationships |
| Compatibilidade | Todas as mudanças são aditivas (_ensure_column) |

### 20.2 Entidades

| Entidade | Impacto |
|----------|---------|
| Game | Evolução major: +20 colunas, deprecated user_id, novo slug |
| GameplaySource | +2 colunas (visibility, times_used_publicly) |
| GameplayEvent | +3 colunas (event_embedding, game_id, franchise_id) |
| User | +1 coluna (allow_public_gameplays) |
| Fact | Mantido, espelhado para KnowledgeItem |
| KnowledgeItem | NOVO |
| GameEntity | NOVO |
| Franchise | NOVO |
| Series | NOVO |
| Character | NOVO |
| GameAlias | NOVO |
| GameRelationship | NOVO |

### 20.3 APIs

| Aspecto | Impacto |
|---------|---------|
| Novos routers | 4 (game_registry, knowledge_graph, knowledge_item, content_intelligence) |
| Novos endpoints | ~25 |
| Endpoints alterados | ~5 (games, dashboard, job data, upload) |
| Autenticação | Sem mudança (SSO + worker_auth) |

### 20.4 Backend

| Aspecto | Impacto |
|---------|---------|
| Novos serviços | 4 (enrichment, content_intelligence, knowledge_item, gameplay_matcher) |
| Novos conectores | 5 (RSS, Wikipedia, Steam, Reddit, IGDB) |
| Novos clients HTTP | 4 (Wikidata, Wikipedia, Steam, Reddit) |
| Pipeline editorial | 2 stages alterados (content_planning, gameplay_selection) |
| Dependências Python | feedparser (nova) |

### 20.5 Frontend

| Aspecto | Impacto |
|---------|---------|
| Novas páginas | 3 (Game Registry, Knowledge Graph, Content Ideas) |
| Páginas alteradas | 3 (Automação, Mídias, Dashboard) |
| Novos componentes | GraphVisualizer, KnowledgeItemCard, GameEnrichmentBadge |
| Dependências JS | vis.js ou D3.js (graph visualization) |

### 20.6 Filas e Workers

| Aspecto | Impacto |
|---------|---------|
| Novos job types | 2 (game_enrich, content_collect) |
| Novas capabilities | 2 (enrichment, content_intelligence) |
| Worker VPS (legacy) | Processa game_enrich e content_collect |
| Worker local (GPU) | Sem mudança (continua mapping + generation + knowledge_index) |
| Agendamento | content_collect periódico (a cada N horas) |

### 20.7 Cache

| Aspecto | Impacto |
|---------|---------|
| Wikidata/Wikipedia | Cache em metadata_json com TTL de 30 dias |
| Steam | Cache em metadata_json com TTL de 7 dias |
| RSS | Sem cache (sempre fresco) |
| Embeddings | Cache em KnowledgeItem.embedding (permanente até re-score) |

### 20.8 Embeddings e Busca

| Aspecto | Impacto |
|---------|---------|
| KnowledgeItem | Embedding com nomic-embed-text (já disponível) |
| GameplayEvent | NOVO: embedding com nomic-embed-text |
| Busca semântica | NOVO: cosine similarity em KnowledgeItems e GameplayEvents |
| Busca keyword | Mantida (SQL LIKE) como fallback |

### 20.9 Pipeline Editorial

| Stage | Mudança |
|-------|---------|
| content_planning | MUDANÇA: consulta KnowledgeItems (não apenas Facts) |
| story_finding | Sem mudança |
| editorial_planning | MUDANÇA: recebe contexto enriquecido do Game |
| creative_engine | Sem mudança |
| script | MUDANÇA: recebe mais contexto (description, lore, history) |
| humanization | Sem mudança |
| script_review | Sem mudança |
| tts | Sem mudança |
| gameplay_selection | MUDANÇA MAIOR: GameplayMatcher cross-game |
| music_selection | Sem mudança |
| render_plan | Sem mudança |
| render | Sem mudança |
| qa | Sem mudança |
| metadata_generation | Sem mudança |
| youtube_upload | Sem mudança |

### 20.10 Compatibilidade com Usuários Atuais

| Cenário | Impacto |
|---------|---------|
| Usuário existente com gameplays | Gameplays continuam funcionando. Jogos serão enriquecidos automaticamente. |
| Usuário existente com documentos | Facts migrados para KnowledgeItems. Documentos continuam funcionando. |
| Usuário existente com automação | Automação continua funcionando. Content Intelligence é opt-in (default off). |
| Usuário sem Content Intelligence | Sistema funciona como hoje. KnowledgeItems globais não são coletados. |
| Usuário com Content Intelligence | Sistema produz vídeos mais variados, com notícias e curiosidades. |

### 20.11 Migrações

| Migração | Risco | Estratégia |
|----------|-------|------------|
| Game.user_id → deprecated | Baixo | Preservar em metadata_json. Queries mudam de `Game.user_id=X` para `GameplaySource.user_id=X JOIN games` |
| Facts → KnowledgeItems | Médio | Script de migração: para cada Fact, criar KnowledgeItem. Fact mantido para compatibilidade. |
| aliases JSON → game_aliases | Baixo | Script de migração: para cada alias em Game.aliases, criar registro em game_aliases |
| Game.slug generation | Baixo | Script: slugify(canonical_name) para todos os jogos existentes |
| Deduplicação de Games | Médio | Script: agrupar por slug, mesclar duplicatas, transferir gameplays/facts |

### 20.12 Performance

| Operação | Frequência | Custo Estimado |
|----------|------------|----------------|
| Game enrichment | 1x por jogo | 5-10s (Wikidata + Wikipedia + LLM) |
| Content collection | A cada 6h | 30-60s (RSS + Wikipedia + Steam) |
| KnowledgeItem scoring | 1x por item | 1-3s (LLM) |
| GameplayEvent embedding | 1x por event | 0.1s (nomic-embed-text) |
| Cross-game gameplay search | 1x por vídeo | 0.5-2s (embedding cosine) |
| Knowledge Graph query | 1x por vídeo | <0.1s (SQL com índices) |

### 20.13 Escalabilidade

| Dimensão | Limite Atual | Com Evolução | Mitigação |
|----------|-------------|-------------|-----------|
| Número de jogos | ~100 | ~10.000 | Índices em slug, aliases |
| KnowledgeItems | 0 | ~100.000 | Índices em composite_score, game_id, content_hash |
| GameplayEvents | ~1.000 | ~100.000 | Índices em game_id, embedding (migrar para pgvector) |
| Conectores simultâneos | 0 | 5 | Rate limiting + queue |
| Migração para PostgreSQL | Preparado | Recomendado após 10K items | pgvector para embeddings, GIN para JSON |

---

## 21. Riscos e Mitigações

### 21.1 Riscos Técnicos

| Risco | Probabilidade | Impacto | Mitigação |
|-------|--------------|---------|-----------|
| Wikidata/Wikipedia API instability | Média | Médio | Cache agressivo, fallback para Steam/IGDB |
| Rate limiting de APIs gratuitas | Alta | Baixo | Backoff exponencial, múltiplas fontes |
| Qualidade variável de RSS feeds | Alta | Médio | Scoring editorial + filtro de score mínimo |
| Embeddings ocupam muito espaço em SQLite | Média | Médio | Migrar para PostgreSQL + pgvector quando >50K items |
| Cross-game matching seleciona gameplay inapropriada | Média | Alto | Score de compatibilidade + fallback em cascata + QA |
| Deduplicação de jogos incorreta | Baixa | Alto | Confirmação por LLM + marca needs_review para ambíguos |
| Migração de Facts quebra pipeline existente | Baixa | Alto | Fact mantido, KnowledgeItem é espelho, pipeline consulta ambos |

### 21.2 Riscos de Produto

| Risco | Probabilidade | Impacto | Mitigação |
|-------|--------------|---------|-----------|
| Content Intelligence produz conteúdo irrelevante | Média | Médio | Scoring + filtro + aprovação manual opcional |
| Usuário não quer gameplays públicas | Baixa | Baixo | Opt-in, default private |
| Sobrecarga de informação na UI | Média | Médio | Páginas separadas, filtros, paginação |
| Latência de enriquecimento atrasa upload | Baixa | Baixo | Enriquecimento é assíncrono (job separado) |

### 21.3 Riscos de Segurança

| Risco | Probabilidade | Impacto | Mitigação |
|-------|--------------|---------|-----------|
| Gameplays públicas expõem dados do usuário | Baixa | Alto | Anonimização (sem filename, sem user_id) |
| APIs externas expõem IP do servidor | Baixa | Baixo | User-Agent identificável, sem dados sensíveis |
| RSS feeds maliciosos | Baixa | Médio | Sanitização de HTML, limite de tamanho |

---

## 22. Plano de Implementação

### 22.1 Fases

#### Fase 1: Game Registry Canônico (Fundação)

**Entregáveis:**
- Novas tabelas: game_aliases, game_entities, franchises, series, characters
- Novas colunas em games (slug, description, developer_id, etc.)
- GameRegistryService (CRUD + deduplicação + slug generation)
- Migração de dados existentes (aliases JSON → game_aliases, slug generation)
- Deprecation de Game.user_id
- API: game_registry_routes.py
- Frontend: página Game Registry (lista + detalhe)

**Dependências:** Nenhuma (fase fundamental)

**Duração estimada:** 2-3 semanas

#### Fase 2: Game Knowledge Enrichment

**Entregáveis:**
- WikidataClient, WikipediaClient, SteamClient
- GameEnrichmentService (pipeline completo)
- Novo job type: game_enrich
- Worker processa enrichment jobs (no VPS)
- Frontend: botão "Enriquecer" na página de jogos
- Auto-trigger enriquecimento quando novo jogo é criado

**Dependências:** Fase 1

**Duração estimada:** 2-3 semanas

#### Fase 3: Knowledge Graph

**Entregáveis:**
- Tabela game_relationships
- KnowledgeGraphService (queries de relacionamentos)
- População automática durante enrichment
- API: knowledge_graph_routes.py
- Frontend: Knowledge Graph Explorer (visualização interativa)

**Dependências:** Fase 2

**Duração estimada:** 1-2 semanas

#### Fase 4: Knowledge Item + Content Intelligence

**Entregáveis:**
- Tabela knowledge_items
- KnowledgeItemService (CRUD + scoring + deduplicação)
- ContentConnector base + RSSConnector + WikipediaConnector + SteamConnector
- ConnectorManager (orquestração + rate limiting)
- ContentIntelligenceService (ciclo de coleta)
- Novo job type: content_collect
- Migração de Facts existentes para KnowledgeItems
- API: knowledge_item_routes.py, content_intelligence_routes.py
- Frontend: página Content Ideas

**Dependências:** Fase 1, 2 (precisa de Game Registry + Enrichment para scoping)

**Duração estimada:** 3-4 semanas

#### Fase 5: Gameplay Intelligence + Seleção Cross-Game

**Entregáveis:**
- Embeddings em GameplayEvents (durante mapping)
- GameplayMatcher (algoritmo cross-game)
- Alteração no GameplayRetriever para usar GameplayMatcher
- Denormalização de game_id/franchise_id em GameplayEvents
- Alteração em local_db_sync.py (incluir KnowledgeItems, Game enriquecido, relacionamentos)
- Alteração em get_job_data (incluir novos dados)

**Dependências:** Fase 3 (Knowledge Graph), Fase 4 (KnowledgeItems)

**Duração estimada:** 2-3 semanas

#### Fase 6: Pipeline Editorial Revisado

**Entregáveis:**
- ContentPlanningService consulta KnowledgeItems (não apenas Facts)
- EditorialStrategyService considera escopo (game/franchise/company/genre)
- EditorialPlanner recebe contexto enriquecido
- Script recebe mais contexto (description, lore, history)

**Dependências:** Fase 4, 5

**Duração estimada:** 1-2 semanas

#### Fase 7: Gameplays Públicas

**Entregáveis:**
- Coluna visibility em GameplaySource
- Coluna allow_public_gameplays em User
- Queries de gameplays públicas
- API: visibility endpoints
- Frontend: dropdown de visibilidade na página de Mídias
- Integração com GameplayMatcher (fallback para públicas)

**Dependências:** Fase 5

**Duração estimada:** 1-2 semanas

#### Fase 8: Configuração de Automações + Frontend Final

**Entregáveis:**
- Novas configurações em Automation.config
- Frontend: seção Content Intelligence na página de Automação
- Frontend: seção Gameplay Selection na página de Automação
- Frontend: seção Fontes de Conhecimento na página de Automação
- Frontend: alterações no Dashboard (novos cards)
- Reddit connector (opcional)
- IGDB connector (opcional)

**Dependências:** Fase 4, 5, 7

**Duração estimada:** 2-3 semanas

### 22.2 Ordem de Execução

```
Fase 1 (Game Registry)
    ↓
Fase 2 (Enrichment)
    ↓
Fase 3 (Knowledge Graph) ←─── depende de Fase 2
    ↓
Fase 4 (Knowledge Items + Content Intelligence) ←─── depende de Fase 1, 2
    ↓
Fase 5 (Gameplay Intelligence) ←─── depende de Fase 3, 4
    ↓
Fase 6 (Pipeline Editorial) ←─── depende de Fase 4, 5
    ↓
Fase 7 (Gameplays Públicas) ←─── depende de Fase 5
    ↓
Fase 8 (Config + Frontend Final) ←─── depende de Fase 4, 5, 7
```

**Paralelização possível:**
- Fase 3 e Fase 4 podem rodar em paralelo (após Fase 2)
- Fase 7 pode rodar em paralelo com Fase 6 (após Fase 5)

### 22.3 Critérios de Aceitação por Fase

#### Fase 1
- [ ] Novo jogo criado tem slug único
- [ ] Aliases são buscáveis individualmente
- [ ] Jogos duplicados são mesclados corretamente
- [ ] Game.user_id deprecated sem quebrar queries existentes
- [ ] UI mostra Game Registry com dados enriquecidos

#### Fase 2
- [ ] Novo jogo é enriquecido automaticamente após criação
- [ ] Enriquecimento preenche description, developer, publisher, genres, themes
- [ ] Botão "Enriquecer" funciona na UI
- [ ] Re-enriquecimento não sobrescreve dados manuais

#### Fase 3
- [ ] Relacionamentos são criados durante enrichment
- [ ] Query "jogos da mesma franquia" retorna resultados corretos
- [ ] Visualização do grafo é interativa
- [ ] API retorna dados do grafo

#### Fase 4
- [ ] RSS connector coleta notícias de Google News
- [ ] Wikipedia connector coleta conhecimento estruturado
- [ ] KnowledgeItems são deduplicados por content_hash
- [ ] Scoring editorial funciona (composite_score)
- [ ] Facts existentes migrados para KnowledgeItems
- [ ] UI mostra banco de ideias com filtros

#### Fase 5
- [ ] GameplayEvents têm embeddings
- [ ] Busca semântica encontra eventos relevantes
- [ ] GameplayMatcher seleciona gameplay cross-game
- [ ] Fallback em cascata funciona (game → franchise → company → genre)
- [ ] Vídeo sobre Capcom usa gameplay de RE4 e Monster Hunter

#### Fase 6
- [ ] ContentPlanningService consulta KnowledgeItems
- [ ] EditorialStrategyService considera escopo configurado
- [ ] Pipeline usa contexto enriquecido do Game
- [ ] Vídeos produzidos têm variedade de temas

#### Fase 7
- [ ] Usuário pode marcar gameplay como pública
- [ ] GameplayMatcher usa gameplays públicas como fallback
- [ ] Gameplays públicas são anonimizadas
- [ ] Opt-in funciona corretamente

#### Fase 8
- [ ] Configurações de Content Intelligence na UI
- [ ] Configurações de Gameplay Selection na UI
- [ ] Toggles de conectores funcionam
- [ ] Dashboard mostra novos cards

---

## 23. Extensibilidade

### 23.1 Novos Conectores

Para adicionar uma nova fonte de conteúdo:

1. Criar arquivo em `src/gpcg/application/content_connectors/{name}_connector.py`
2. Implementar `ContentConnector` interface (name, fetch, normalize, rate_limit)
3. Registrar no `ConnectorManager`
4. Adicionar toggle em `Automation.config.knowledge_sources`

**Sem alterar:** pipeline editorial, KnowledgeItem, frontend (conector aparece automaticamente na lista de fontes).

### 23.2 Novos Tipos de KnowledgeItem

Para adicionar um novo tipo (e.g., "interview", "podcast"):

1. Adicionar valor ao enum `KnowledgeItemType`
2. Adicionar à UI (dropdown de filtro)
3. Opcionalmente: adicionar scoring específico no `compute_composite_score`

**Sem alterar:** pipeline editorial, conectores, banco de dados (item_type é VARCHAR).

### 23.3 Novos Relacionamentos no Knowledge Graph

Para adicionar um novo tipo de relacionamento:

1. Adicionar valor ao enum `RelationshipType`
2. Criar o relacionamento durante enrichment ou manualmente
3. Opcionalmente: adicionar query específica em `KnowledgeGraphService`

**Sem alterar:** schema (relationship é VARCHAR), frontend (grafo mostra qualquer relacionamento).

### 23.4 Novos Tipos de Entidade

Para adicionar um novo tipo de entidade (e.g., "event" para eventos de gaming):

1. Adicionar valor ao enum `EntityType`
2. Entidades são criadas em `game_entities` com o novo tipo
3. Relacionamentos podem referenciar o novo tipo

**Sem alterar:** schema (entity_type é VARCHAR), queries existentes.

### 23.5 Novas Fontes de Enriquecimento

Para adicionar uma nova fonte de enriquecimento (e.g., IGDB):

1. Criar client em `src/gpcg/infrastructure/{name}_client.py`
2. Adicionar chamada em `GameEnrichmentService.enrich_game()`
3. Adicionar toggle em configuração (opcional)

**Sem alterar:** Game model, Knowledge Graph, pipeline.

### 23.6 Migração para PostgreSQL

Quando o volume de dados justificar:
1. Trocar `GPCG_DB_PATH` por `GPCG_DB_URL=postgresql://...`
2. Introduzir Alembic para migrations
3. Migrar embeddings de JSON para `pgvector`
4. Migrar aliases para `pg_trgm` (busca fuzzy)
5. Migrar JSON columns para `JSONB` (indexação GIN)

**Sem alterar:** lógica de aplicação (SQLAlchemy abstrai o banco).

---

## Apêndice A: Estrutura de Diretórios Proposta

```
src/gpcg/
├── api/
│   ├── game_registry_routes.py      # NOVO
│   ├── knowledge_graph_routes.py    # NOVO
│   ├── knowledge_item_routes.py     # NOVO
│   ├── content_intelligence_routes.py # NOVO
│   └── ... (existentes)
├── application/
│   ├── game_enrichment_service.py   # NOVO
│   ├── content_intelligence_service.py # NOVO
│   ├── knowledge_item_service.py    # NOVO
│   ├── gameplay_matcher.py          # NOVO
│   ├── content_connectors/          # NOVO
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── manager.py
│   │   ├── rss_connector.py
│   │   ├── wikipedia_connector.py
│   │   ├── steam_connector.py
│   │   ├── reddit_connector.py
│   │   └── igdb_connector.py
│   └── ... (existentes)
├── domain/
│   ├── game_registry.py             # NOVO
│   ├── knowledge_graph.py           # NOVO
│   └── ... (existentes)
├── infrastructure/
│   ├── wikidata_client.py           # NOVO
│   ├── wikipedia_client.py          # NOVO
│   ├── steam_client.py              # NOVO
│   ├── reddit_client.py             # NOVO
│   └── ... (existentes)
└── ...
```

## Apêndice B: Dependências Python Novas

| Pacote | Propósito | Necessário em |
|--------|-----------|---------------|
| `feedparser` | Parse de RSS feeds | RSS Connector |
| `wikidata` (opcional) | Client Wikidata | Wikidata Client (ou httpx direto) |

**Nota:** Wikipedia, Steam, Reddit usam `httpx` (já instalado). Wikidata SPARQL também usa `httpx`.

## Apêndice C: Dependências Frontend Novas

| Pacote | Propósito |
|--------|-----------|
| `vis-network` ou `d3.js` | Visualização do Knowledge Graph |

---

**Fim do documento.**
