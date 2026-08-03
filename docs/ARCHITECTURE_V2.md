# GPCG — Arquitetura V2 (Blueprint Definitivo)

**Versão:** 2.0 — Definitiva
**Data:** 2026-08-02
**Status:** Decidido. Referência oficial para implementação.
**Substitui:** `ARCHITECTURE_EVOLUTION.md` (proposta v1) e a revisão crítica associada.

---

## Sumário

1. [Propósito e Escopo](#1-propósito-e-escopo)
2. [Princípios de Arquitetura](#2-princípios-de-arquitetura)
3. [Decisões Arquiteturais Fundamentais](#3-decisões-arquiteturais-fundamentais)
4. [Game Registry Canônico](#4-game-registry-canônico)
5. [Game Knowledge e Enriquecimento](#5-game-knowledge-e-enriquecimento)
6. [Game Lifecycle](#6-game-lifecycle)
7. [Knowledge Items e Content Intelligence](#7-knowledge-items-e-content-intelligence)
8. [Gameplay Intelligence](#8-game-intelligence)
9. [Pipeline Editorial](#9-pipeline-editorial)
10. [Banco de Dados](#10-banco-de-dados)
11. [API](#11-api)
12. [Frontend](#12-frontend)
13. [Worker, Jobs e Filas](#13-worker-jobs-e-filas)
14. [Sincronização VPS ↔ Worker](#14-sincronização-vps--worker)
15. [Configuração de Automações](#15-configuração-de-automações)
16. [Escalabilidade e Migração Futura](#16-escalabilidade-e-migração-futura)
17. [Plano de Implementação](#17-plano-de-implementação)
18. [Componentes Deferidos](#18-componentes-deferidos)

---

## 1. Propósito e Escopo

### 1.1 Problema

O GPCG hoje conhece apenas vídeos. Ele entende gameplays (VLM/ASR), extrai
facts de documentos enviados manualmente, e gera roteiros baseados nesse
conhecimento limitado. Não conhece o universo dos games — franquias,
empresas, gêneros, lore, notícias. A seleção de gameplay é intra-jogo.
O conteúdo depende exclusivamente de input manual do usuário.

### 1.2 Escopo da V2

Esta arquitetura resolve os seguintes gaps comprovados:

| Gap | Descrição |
|-----|-----------|
| G1 | Game Registry não-canônico — duplicatas, sem slug, `user_id` por usuário |
| G2 | Game Knowledge mínimo — sem developer, publisher, franquia, gêneros |
| G3 | Sem enriquecimento automático — jogos ficam minimalistas para sempre |
| G4 | Conteúdo depende 100% do usuário — sem fontes externas |
| G5 | Sem representação normalizada para notícias/lore/conteúdo externo |
| G7 | Seleção de gameplay intra-jogo — sem cross-game |
| G10 | Busca de gameplay por SQL LIKE — baixa recall semântica |

### 1.3 Fora de Escopo (deferido)

Componentes que NÃO fazem parte da V2 e serão reconsiderados quando
houver evidência de necessidade (ver §18):

- Knowledge Graph como tabela de relacionamentos
- GameEntity / Company / Franchise / Series / Character como tabelas
- Gameplays públicas (marketplace entre usuários)
- Conectores Steam, Reddit, IGDB
- Visualização interativa de grafo (D3/vis.js)
- Múltiplos scores editoriais por KnowledgeItem

---

## 2. Princípios de Arquitetura

Estes princípios governam toda decisão. Quando houver conflito entre
simplicidade e completude, simplicidade vence — **desde que a evolução
futura não seja bloqueada**.

1. **Evolução baseada em evidência.** Toda mudança de comportamento
   editorial passa por experimento A/B antes de virar default
   (`EDITORIAL_EVALUATION.md` §5, 4 filtros).

2. **Uma fonte de verdade.** Nenhum dado é armazenado em dois lugares
   com sync. Se duas entidades se sobrepõem, uma é a fonte e a outra
   é derivada em query.

3. **Strings antes de tabelas.** Entidades de domínio (empresas,
   franquias) começam como strings. Tabelas são introduzidas quando
   houver >50 instâncias E queries que strings não resolvem. A
   migração string→FK é sempre limpa se os strings forem canônicos.

4. **Embeddings em tabelas separadas.** Embeddings nunca vivem na
   tabela principal. Tabela separada facilita migração para pgvector
   sem mexer no schema principal.

5. **Feature flags para todo comportamento novo.** Toda mudança de
   pipeline editorial é gated por flag, default off, validada por A/B.

6. **Aditivo, nunca destrutivo.** Schema evolui por adição
   (`_ensure_column`, `create_all`). Dados existentes são preservados.

7. **Reusar antes de criar.** Antes de criar um novo componente,
   verificar se um existente pode ser estendido com um parâmetro.

8. **Funções antes de frameworks.** Coleções de operações relacionadas
   começam como funções em um módulo. Abstrações (ABCs, managers) são
   extraídas quando há ≥3 implementações com lógica compartilhada.

---

## 3. Decisões Arquiteturais Fundamentais

Cada decisão abaixo resolve um conflito entre a proposta original e a
revisão crítica. A decisão é final.

### D1: Empresas e franquias como strings, não tabelas

**Decisão:** `developer`, `publisher`, `franchise` são colunas VARCHAR
em `Game`. Não há tabelas `GameEntity`, `Franchise`, `Company`.

**Justificativa:** Para V2, as queries necessárias são:
- "jogos do mesmo developer" → `WHERE developer = ?`
- "jogos da mesma franquia" → `WHERE franchise = ?`

Strings resolvem ambas. Uma tabela de empresas só se justifica quando
houver conteúdo sobre empresas (não apenas sobre jogos) ou >50
empresas com metadata própria. Nenhum dos dois é verdade hoje.

**Salvaguarda de evolução:** O enriquecimento escreve sempre nomes
canônicos (do Wikidata/Wikipedia). Strings canônicas tornam a migração
futura para FK trivial: `SELECT DISTINCT developer FROM games` →
popula `companies`, adiciona `developer_id`, backfill, drop `developer`.

**Rejeição da proposta original:** `GameEntity` como god-table com 6
entity_types (company, developer, publisher, person, platform, engine,
genre, theme) é heterogênea — `founded_year`/`country` só fazem
sentido para empresas, genres/themes não são entidades. Colunas nulas
para a maioria das linhas é design ruim.

### D2: Sem Knowledge Graph (tabela de relacionamentos)

**Decisão:** Não há tabela `game_relationships`. Cross-game matching
usa campos string/JSON diretamente em `Game`.

**Justificativa:** Os 18 tipos de relacionamento da proposta original
são uma ontologia construída antes da necessidade. Para matching de
gameplay, só importam: mesma franquia, mesmo developer, mesmo gênero.
Todos são queryáveis via strings/JSON em `Game`. Relacionamentos
game→game (sequel, prequel, remake, spinoff, similar_to) não servem
para matching — você não usa gameplay de RE1 num vídeo de RE4 remake
só porque é remake.

**Quando introduzir:** Se futuramente for necessário inferir
relacionamentos não-declarados (similar_to por combinação de
gênero+empresa+tema) ou traversals multi-hop, introduzir
`game_relationships` então. A ausência da tabela não bloqueia nenhuma
query necessária em V2.

### D3: KnowledgeItem sem dual-write com Fact

**Decisão:** `Fact` continua para facts extraídos de documentos do
usuário. `KnowledgeItem` é exclusivamente para conteúdo externo
(RSS, Wikipedia, etc.). **Não há espelhamento.** O pipeline editorial
consulta ambas as tabelas via um método unificado no serviço, sem
duplicação de armazenamento.

**Justificativa:** Dual-write cria duas fontes de verdade com drift
garantido. Se um Fact é editado, o KnowledgeItem espelho fica stale.
A unificação acontece na camada de query (service method que faz
UNION lógico), não na camada de armazenamento.

**Rejeição da proposta original:** "Fact mantido, KnowledgeItem é
espelho" — rejeitado. Espelhamento é o pior dos dois mundos.

### D4: Embeddings em tabelas separadas

**Decisão:** `knowledge_item_embeddings(item_id PK, embedding BLOB)`
e `gameplay_event_embeddings(event_id PK, embedding BLOB)`. Embeddings
nunca são colunas JSON nas tabelas principais.

**Justificativa:** Em escala (100K+ items), embeddings em JSON SQLite
significam 600MB+ de dados parseados em Python em cada busca semântica.
Tabelas separadas permitem: (a) migrar para pgvector com
`ALTER TABLE ... ADD COLUMN embedding vector` + backfill + drop da
tabela BLOB, sem mexer no schema principal; (b) eventualmente usar
SQLite-vec ou sqlite-vss como intermediário.

**Rejeição da proposta original:** `event_embedding JSON` como coluna
em `gameplay_events` e `embedding JSON` em `knowledge_items` —
rejeitado. Tech debt de performance garantida.

### D5: Cross-game via expansão de game_ids, não GameplayMatcher

**Decisão:** Não há classe `GameplayMatcher`. Cross-game é implementado
por uma função `_expand_game_ids(session, game_id, scope)` que retorna
uma lista de `game_ids`. O `GameplayRetriever` existente é estendido
para aceitar `game_ids: list[int]` em vez de `game_id: int`.

**Justificativa:** O `GameplayRetriever` já faz semantic search +
interesting scoring. A única mudança necessária é passar múltiplos
game_ids em vez de um. Um novo componente com score multiplicativo de
4 fatores (`semantic × graph_proximity × interesting × freshness`) é
frágil: se `graph_proximity=0` (jogo não-enriquecido), o score inteiro
é 0 mesmo com alta similaridade semântica. Falha silenciosa.

**Salvaguarda de evolução:** Se futuramente for necessário um scoring
mais sofisticado (pesos por proximidade no grafo, freshness, etc.),
o `GameplayRetriever` pode ser estendido com um `scorer` injetável.
A função simples não bloqueia essa evolução.

### D6: Job types para enriquecimento e coleta (não cron)

**Decisão:** Adicionar `game_enrich` e `content_collect` ao enum
`JobType`. Ambos rodam no VPS (sem GPU), processados pelo worker
legacy ou por um processador leve no Control Plane.

**Justificativa:** O projeto já tem arquitetura de job queue com
claim atômico, prioridade, capabilities. Usar a infraestrutura
existente é mais consistente que introduzir cron paralelo. A revisão
crítica sugeriu "task/cron simples" — rejeitado, pois introduziria
um segundo mecanismo de agendamento ao lado do job queue existente.

**Rejeição da revisão crítica:** "task/cron simples, sem JobType" —
rejeitado. O job queue é o mecanismo de assincronia do GPCG.

### D7: Um conector (RSS), sem framework de abstração

**Decisão:** V2 implementa apenas o conector RSS (Google News).
Conectores são funções em um módulo (`content_collectors.py`), não
classes com ABC base. Sem `ConnectorManager`, sem `ContentConnector`
abstrata.

**Justificativa:** Abstração antes da 3ª implementação é prematura.
`collect_rss(game_id, since)` é uma função. Quando Steam/Reddit forem
adicionados, se houver lógica compartilhada (rate limiting, cache,
dedup), extrair uma base então. Funções → classes é refactor trivial;
classes → funções é difícil.

**Salvaguarda de evolução:** Adicionar um conector = adicionar uma
função `collect_steam(game_id)` e registrá-la no ciclo de coleta.
Sem alterar interface, sem alterar KnowledgeItem, sem alterar pipeline.

### D8: Gameplays públicas deferidas

**Decisão:** Gameplays públicas (visibility, opt-in, marketplace) não
fazem parte de V2. O sistema tem 2 usuários, 1 ativo. Adicionar
visibilidade, consentimento, anonimização e storage cross-user para
zero valor atual é prematuro.

**Justificativa:** Adicionar `visibility` column via `_ensure_column`
quando o recurso for justificado (≥3 usuários ativos com gameplays)
é uma operação trivial. Deferir não bloqueia evolução.

**Rejeição da proposta original:** `GameplayVisibility` enum com 3
estados (private, unlisted, public), `allow_public_gameplays` em User,
`times_used_publicly` em GameplaySource — tudo deferido.

### D9: Um score editorial, não quatro

**Decisão:** `KnowledgeItem` tem uma única coluna `editorial_score`
(0-100). Frescor é derivado de `published_at`/`collected_at` em query,
não armazenado. Não há `relevance_score`, `freshness_score`,
`composite_score`.

**Justificativa:** Quatro scores com fórmula de composição por
`item_type` é premature optimization. Para ranking: `ORDER BY
editorial_score DESC, published_at DESC` para news;
`ORDER BY editorial_score DESC` para evergreen. A diferença de
pesos por tipo é expressa na query, não em 4 colunas armazenadas.

### D10: `lore_summary` como único campo de prosa LLM em Game

**Decisão:** Dos 4 campos de prosa LLM propostos
(`historical_context`, `lore_summary`, `reception_summary`,
`development_history`), apenas `lore_summary` é mantido.

**Justificativa:** `lore_summary` é o contexto mais útil para o
roteiro (a história/narrativa do jogo). Os outros 3 são deferred até
que o pipeline editorial prove que os utiliza com benefício medido
em A/B. Um campo, baixo custo, valor editorial claro.

**Rejeição da revisão crítica:** "remover todos os 4 campos de prosa"
— parcialmente rejeitado. `lore_summary` é mantido por valor editorial
comprovado (o roteiro precisa da narrativa do jogo).

### D11: Prevenção de duplicatas na criação, não merge depois

**Decisão:** Não há `is_canonical`, `merged_into_id`, nem algoritmo de
dedup com Levenshtein. Duplicatas são prevenidas na criação: ao
resolver o jogo de um upload, se o slug ou alias já existe, o gameplay
é vinculado ao Game existente em vez de criar um novo.

**Justificativa:** Merge infra (is_canonical, merged_into_id,
needs_review, transferência de gameplays/facts) é complexidade para
um problema que prevenção resolve. Se duplicatas acumularem apesar da
prevenção, uma ferramenta admin de merge pode ser adicionada depois.

### D12: `rejected` como terceiro status de KnowledgeItem

**Decisão:** `KnowledgeItem.status` ∈ {fresh, used, rejected}. Não há
`expired` nem `hidden`.

**Justificativa:** `rejected` (usuário descartou a ideia) é uma
necessidade de produto real — qualquer banco de ideias precisa de
"não me mostre isto novamente". `expired` é derivável por query de
data. `hidden` é redundante com `rejected`. Três estados, cada um com
comportamento definido.

**Rejeição da revisão crítica:** "apenas fresh|used" — rejeitado.
Curadoria do usuário é necessidade real, não especulativa.

### D13: `Video.knowledge_item_id` para rastreabilidade

**Decisão:** Adicionar `knowledge_item_id` (nullable FK) em `Video`.
Não há `used_in_video_id` em `KnowledgeItem` nem `used_count`.

**Justificativa:** A direção da referência é Video→KnowledgeItem
(um vídeo é baseado em um item). `used_in_video_id` em KnowledgeItem
só rastreia 1 vídeo. `Video.knowledge_item_id` é a fonte de verdade;
`COUNT` de vídeos por item é derivável. Sem denormalização, sem sync.

### D14: Gate de factual accuracy para conteúdo externo

**Decisão:** O `script_critic` existente valida factual accuracy do
roteiro contra o campo `content` do KnowledgeItem (texto fonte),
usando o mesmo mecanismo que valida contra `Fact.claim` hoje.

**Justificativa:** A revisão crítica levantou que RSS não é
fact-checked. A solução é reusar o gate existente: o `content` do
KnowledgeItem é a "fonte de verdade" para aquele item, assim como
`Fact.claim` é para facts. Se o roteiro inventa mecânicas não
presentes no `content`, o critic flagga. Sem nova camada de
verificação.

---

## 4. Game Registry Canônico

### 4.1 Modelo

A tabela `Game` existente é estendida com campos de conhecimento e
identidade canônica. `user_id` é deprecated (jogos são globais).

```sql
-- games (colunas EXISTENTES mantidas)
id              INTEGER PRIMARY KEY
canonical_name  VARCHAR(200) NOT NULL
aliases         JSON          -- deprecated, mantido para compat; game_aliases é a fonte de verdade
platforms       JSON
capture_sources JSON
camera_type     VARCHAR(32)
metadata_json   JSON
created_at      DATETIME
updated_at      DATETIME
user_id         INTEGER       -- DEPRECATED: mover para metadata_json.legacy_user_id na migração

-- games (colunas NOVAS)
slug            VARCHAR(200) UNIQUE NOT NULL
description     TEXT
release_date    DATE
developer       VARCHAR(200)   -- nome canônico (do Wikidata/Wikipedia)
publisher       VARCHAR(200)   -- nome canônico
franchise       VARCHAR(200)   -- nome canônico (nullable)
genres          JSON           -- ["action", "adventure", "open-world"]
themes          JSON           -- ["school", "rebellion"]
lore_summary    TEXT           -- resumo da lore/narrativa (LLM, do Wikipedia)
external_ids    JSON           -- {"wikidata": "Q123", "steam": 123456}
enriched_at     DATETIME       -- NULL = não enriquecido
enrichment_error TEXT          -- NULL ou mensagem de erro do último enrichment
```

**Não há:** `developer_id`/`publisher_id`/`franchise_id` (são strings),
`series_id`, `engine`, `keywords`, `trivia`, `notable_characters`,
`platforms_detailed`, `historical_context`, `reception_summary`,
`development_history`, `esrb_rating`, `metacritic_score`,
`steam_review_pct`, `content_warnings`, `is_canonical`,
`merged_into_id`, `enrichment_status` (enum), `enrichment_source`.

### 4.2 Aliases

```sql
CREATE TABLE game_aliases (
    id          INTEGER PRIMARY KEY,
    game_id     INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    alias       VARCHAR(200) NOT NULL,
    alias_type  VARCHAR(30) DEFAULT 'alternative',
    source      VARCHAR(50) DEFAULT 'manual',
    created_at  DATETIME
);

CREATE UNIQUE INDEX idx_game_aliases_lower ON game_aliases(LOWER(alias));
CREATE INDEX idx_game_aliases_game_id ON game_aliases(game_id);
```

**Justificativa da tabela separada:** Aliases em JSON não são
indexáveis individualmente. Busca por alias em JSON é O(n) scan;
em tabela é O(log n). Proveniência (`source`) rastreia se o alias
veio de input manual, do resolver, ou do enriquecimento.

### 4.3 Deduplicação

Algoritmo de resolução na criação/vinculação de jogo:

```
1. Normalizar nome de entrada: lowercase, remover acentos, remover
   sufixos de plataforma ("PS2", "PC", "Scholarship Edition" se
   for subtitle distinto — heurística simples)
2. Gerar slug do nome normalizado
3. Buscar Game por slug exato → se encontrado: vincular, adicionar
   nome de entrada como alias
4. Se não: buscar em game_aliases por LOWER(alias) = nome normalizado
   → se encontrado: vincular ao game do alias
5. Se não: criar novo Game com slug gerado
```

**Não há:** Levenshtein distance, `needs_review`, merge de duplicatas
existentes via algoritmo automático. Duplicatas existentes (se houver)
são resolvidas manualmente via endpoint admin de merge (futuro).

### 4.4 Migração de Dados Existentes

Script idempotente executado no `init_db` (guardado por flag em
`metadata_json.schema_migrations`):

1. Para cada `Game` sem `slug`: gerar `slug = slugify(canonical_name)`.
   Se colisão de slug: append `-2`, `-3`, etc. (duplicatas reais
   ficam visíveis para merge manual futuro).
2. Para cada `Game` com `user_id` não-null: mover para
   `metadata_json.legacy_user_id`, setar `user_id = NULL`.
3. Para cada alias em `Game.aliases` (JSON): criar registro em
   `game_aliases` se não existir.
4. Setar `enriched_at = NULL` para todos (enriquecimento futuro).

---

## 5. Game Knowledge e Enriquecimento

### 5.1 Pipeline de Enriquecimento

Função única (não god-service de 5 fases):

```python
# src/gpcg/application/game_enrichment.py

def enrich_game(session, game_id: int) -> EnrichmentResult:
    """Enriquece um jogo com dados de Wikipedia + Wikidata.

    Passos internos:
    1. Resolver identidade no Wikidata (buscar por nome, confirmar via LLM)
    2. Coletar dados: Wikidata (developer, publisher, franchise, genres,
       release_date) + Wikipedia (description, lore)
    3. Gerar lore_summary via LLM a partir do artigo da Wikipedia
    4. Persistir em Game + adicionar aliases do Wikidata
    5. Setar enriched_at ou enrichment_error
    """
```

**Fontes V2:** Wikidata (SPARQL via httpx) + Wikipedia (REST API via
httpx). Ambas gratuitas, sem key.

**Não há em V2:** Steam connector, Reddit connector, IGDB connector,
`SteamClient`, `RedditClient`, `GameEnrichmentService` (classe),
fases separadas como métodos.

### 5.2 Resolução de Identidade (Wikidata)

```sparql
SELECT ?item ?itemLabel ?itemDescription WHERE {
  ?item rdfs:label "Bully"@en .
  ?item wdt:P31 wd:Q7889 .  # instância de video game
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
```

Confirmação de match: o LLM recebe o nome do jogo + descrição do
Wikidata e confirma se é o jogo correto (evita ambiguidade — "Bully"
pode ser o jogo ou o filme).

### 5.3 Nomes Canônicos

O enriquecimento escreve sempre nomes canônicos do Wikidata em
`developer`, `publisher`, `franchise`. Isto é crítico para matching
por string: "Rockstar Games" é consistente across todos os jogos da
empresa, permitindo `WHERE developer = 'Rockstar Games'` funcionar.

### 5.4 Trigger

- **Automático:** Quando um novo `Game` é criado (via upload ou
  import), um job `game_enrich` é criado se `enriched_at IS NULL`.
- **Manual:** Endpoint `POST /api/games/{id}/enrich` cria job
  `game_enrich` (re-enriquecimento).
- **Re-enriquecimento:** Sobrescreve dados se `enrichment_error` não
  for null OU se for trigger manual explícito. Não sobrescreve se
  já enriquecido com sucesso (evita churn).

### 5.5 Cache

Resultados do Wikidata/Wikipedia cacheados em
`Game.metadata_json.enrichment_cache` com timestamp. TTL: 30 dias.
Re-enriquecimento manual ignora cache.

---

## 6. Game Lifecycle

Esta seção documenta o ciclo de vida completo de um `Game` dentro do
GPCG, desde a descoberta a partir de uma gameplay até a participação
no pipeline editorial. É a referência oficial para eliminar
ambiguidade sobre como um Game nasce, evolui, é reutilizado e
integra com o restante do sistema.

### 6.1 Visão Geral do Ciclo de Vida

```
Gameplay Upload
    ↓
Game Discovery (sinais: filename, capture_source, automação, VLM)
    ↓
Game Resolver (L1 → L2 → L3)
    ↓
Game Registry (criar novo OU reutilizar existente)
    ↓
Game Enrichment (assíncrono, job game_enrich)
    ↓
Reutilização em todo o pipeline:
    ├── Content Intelligence (coleta RSS por jogo)
    ├── Gameplay Retrieval (seleção cross-game por franchise/developer)
    ├── Editorial Planning (contexto: description, lore_summary)
    ├── Script Generation (contexto enriquecido)
    └── Script Review (gate de factual accuracy)
```

Um Game passa por **três estados de maturidade**:

| Estado | Condição | Comportamento do sistema |
|--------|----------|--------------------------|
| **Recém-criado** | `enriched_at IS NULL`, `enrichment_error IS NULL` | Funciona com dados mínimos. Pipeline usa `canonical_name` + `slug`. Cross-game sem expansão (franchise/developer NULL). Enriquecimento pendente. |
| **Enriquecido** | `enriched_at IS NOT NULL`, `enrichment_error IS NULL` | Dados completos. Cross-game funciona. Content intelligence scoping funciona. Pipeline tem contexto rico. |
| **Erro de enriquecimento** | `enrichment_error IS NOT NULL` | Funciona como recém-criado, mas com erro registrado. Re-enriquecimento manual ou automático disponível. |

**Princípio fundamental:** O sistema **funciona em todos os três
estados**. Um Game recém-criado não bloqueia nenhum fluxo. O
enriquecimento é uma melhoria assíncrona, não um pré-requisito.

### 6.2 Descoberta do Game

A descoberta é o processo de identificar **qual jogo** está presente
em uma gameplay recém-uploaded. O sistema combina múltiplos sinais
em ordem de custo crescente (barato primeiro, caro por último).

#### Sinais de entrada

| Sinal | Fonte | Custo | Confiabilidade |
|-------|-------|-------|----------------|
| **Nome do arquivo** | `filename_parser.parse_filename()` | Zero (regex) | Alta se o usuário segue padrão `GameName_YYYY-MM-DD_HH-MM-SS.mp4` |
| **Capture source** | Extraído do filename (Yuzu, OBS, PCSX2, etc.) | Zero (regex) | Indireta — não identifica o jogo, mas fornece prior |
| **Configuração da automação** | `Automation.config` (se houver game_id forçado) | Zero | Alta — escolha explícita do usuário |
| **Visão computacional (VLM)** | `resolve_l3()` — amostra de frames + gemma3:12b | Alto (GPU, 5-10s) | Alta para jogos visivelmente identificáveis |
| **OCR** | Não implementado em V2 | — | — |

**Combinação dos sinais:** O `GameResolver` aplica os sinais em
camadas hierárquicas (L1 → L2 → L3). Cada camada pode resolver o
jogo ou passar para a próxima. O primeiro resultado com confiança
≥ 0.6 é aceito. Se todas falham, o jogo fica `needs_review`.

#### Sinal de automação (novo em V2)

Se a automação do usuário tem `config.gameplay_selection.game_id`
ou o upload é feito via um fluxo que já especifica o jogo
(ex: upload associado a um game_id explícito), este sinal tem
**prioridade máxima** e pula o resolver. O usuário disse qual é o
jogo — o sistema não precisa adivinhar.

### 6.3 Game Resolver

O `GameResolver` (`domain/game_resolver.py`) é o componente
responsável por responder uma única pergunta:

> **"Esta gameplay pertence a qual Game?"**

**Responsabilidades estritas:**
- Receber sinais (filename, capture_source, frames de vídeo)
- Consultar o Game Registry (slugs, aliases)
- Retornar um `ResolutionResult` (game_name, method, confidence)

**O que o Game Resolver NÃO faz:**
- Não enriquece dados (não consulta Wikidata/Wikipedia)
- Não cria Games (apenas identifica existentes ou retorna
  `needs_review`)
- Não consulta fontes externas
- Não modifica o GameplaySource (quem chama o resolver decide o
  que fazer com o resultado)

#### Camadas de resolução

**L1 — Determinística (filename + alias registry):**

1. `parse_filename(filename)` extrai `candidate_game` e
   `capture_source` via regex.
2. Normalizar o `candidate_game`: lowercase, strip.
3. Buscar em `game_aliases` por `LOWER(alias) = candidate` (O(log n)
   via índice unique).
4. Se encontrado: retornar o Game vinculado ao alias, confiança 0.95.
5. Se não: buscar por `Game.slug = slugify(candidate)` (O(log n)).
6. Se encontrado: retornar, confiança 0.95.
7. Se não: passar para L2.

**L2 — Prior (capture_source → game):**

1. Se o filename tem `capture_source` (ex: "Yuzu").
2. Buscar todos os GameplaySources com este capture_source que
   têm game resolvido.
3. Se **todos** estão associados a **um único** Game: usar como
   prior fraco (confiança 0.5).
4. Se múltiplos jogos: não usar (prior ambíguo).
5. Se nenhum: passar para L3.

**L3 — VLM (visão computacional):**

1. Extrair 5 frames do vídeo.
2. Construir catálogo de candidatos a partir do registry
   (`canonical_name` + aliases de todos os Games, top 50).
3. Enviar frames + catálogo ao VLM (gemma3:12b via Ollama).
4. VLM retorna `{game, confidence, reasoning}`.
5. Se `confidence ≥ 0.5` e game não vazio: retornar.
6. Se não: retornar `needs_review`.

**Fallback final:**

Se todas as camadas falham:
- Se L1 ou L2 retornaram um candidato fraco: retornar esse
  candidato com confiança baixa (`needs_review`).
- Se `parse_filename` extraiu um `candidate_game`: retornar como
  candidato para confirmação manual (confiança 0.3,
  `needs_review`).
- Se nenhum sinal: retornar `game_name=None`, `needs_review`.

#### Política de confiança

| Confiança | Ação |
|-----------|------|
| ≥ 0.6 | Aceitar. Vincular GameplaySource ao Game. |
| 0.3 – 0.6 | `needs_review`. Vincular se possível, marcar para confirmação. |
| < 0.3 ou None | `needs_review`. GameplaySource fica sem `game_id` até confirmação manual. |

#### Resolução de conflitos

Se L1 e L2 retornam Games diferentes:
- L1 tem prioridade (determinístico > prior).
- Se L1 é `needs_review` mas L2 não: usar L2.
- Se ambos são `needs_review`: usar L3 (VLM) como desempate.

Se L3 (VLM) retorna um Game diferente de L1/L2:
- L3 tem prioridade se `confidence ≥ 0.6` (VLM viu o jogo).
- Se L3 `confidence < 0.6`: manter o resultado de L1/L2.

### 6.4 Criação Inicial

Quando o Game Resolver não encontra um Game existente (todas as
camadas falham ou retornam candidato não-cadastrado), um novo Game
é criado.

**Quem cria:** O `GameRegistry` service (`domain/game_registry.py`),
não o Game Resolver. O resolver apenas identifica; o registry
decide criar ou reutilizar.

#### Campos mínimos na criação

| Campo | Valor inicial | Origem |
|-------|---------------|--------|
| `canonical_name` | Nome do candidato (do resolver ou filename) | Input do usuário |
| `slug` | `slugify(canonical_name)` | Gerado |
| `aliases` | `[]` (JSON legacy, vazio) | — |
| `platforms` | `[]` | — |
| `capture_sources` | `[capture_source]` se identificado pelo resolver | Do filename |
| `camera_type` | `"unknown"` | Default |
| `metadata_json` | `{}` | — |
| `user_id` | `NULL` (global, jogos não têm dono) | V2 (deprecated) |
| `description` | `NULL` | Pendente (enriquecimento) |
| `release_date` | `NULL` | Pendente |
| `developer` | `NULL` | Pendente |
| `publisher` | `NULL` | Pendente |
| `franchise` | `NULL` | Pendente |
| `genres` | `[]` | Pendente |
| `themes` | `[]` | Pendente |
| `lore_summary` | `NULL` | Pendente |
| `external_ids` | `{}` | Pendente |
| `enriched_at` | `NULL` | Não enriquecido |
| `enrichment_error` | `NULL` | Sem erro ainda |

#### Registro de alias na criação

Quando um novo Game é criado a partir de um nome de filename que
**diferiu** do `canonical_name` (ex: filename "RE4" → canonical
"Resident Evil 4" se o resolver encontrou via VLM), o nome original
do filename é registrado como alias:

- Inserir em `game_aliases(game_id, alias="RE4", source="resolver")`.
- Isto garante que futuros uploads "RE4" sejam resolvidos em L1
  (determinístico), sem precisar de VLM novamente.

#### Como o sistema funciona antes do enriquecimento

Um Game recém-criado (`enriched_at IS NULL`) é **totalmente
funcional**:

- **Gameplay mapping:** Funciona. O VLM/ASR analisa a gameplay
  independentemente dos metadados do Game.
- **Gameplay retrieval:** Funciona. `GameplayRetriever` busca
  eventos por `game_id`. Cross-game não expande (franchise/developer
  NULL → `_expand_game_ids` retorna `[game_id]`), mas intra-game
  funciona.
- **Content planning:** Funciona. Consulta Facts do jogo. Se
  `GPCG_CONTENT_INTELLIGENCE_ENABLED=on`, consulta KnowledgeItems
  do `game_id` (items sem franchise/developer não participam de
  scoping por escopo, mas participam de escopo `game`).
- **Script generation:** Funciona. Sem `description`/`lore_summary`,
  o script é gerado apenas com o Fact/KnowledgeItem. Menos rico,
  mas funcional.
- **Script review:** Funciona. Gate de factual accuracy valida
  contra `Fact.claim` ou `KnowledgeItem.content`.

**O enriquecimento melhora a qualidade, mas não é gate.**

### 6.5 Enriquecimento

#### Quando acontece

| Trigger | Condição | Fonte |
|---------|----------|-------|
| **Automático** | Novo Game criado (upload ou import) E `enriched_at IS NULL` E `GPCG_GAME_ENRICHMENT_ENABLED=on` | Criação de Game |
| **Manual** | Usuário clica "Enriquecer" na UI | Endpoint `POST /api/games/{id}/enrich` |
| **Re-enriquecimento** | `enrichment_error IS NOT NULL` E próximo ciclo de checagem | Automático (se flag on) ou manual |

**Sem `GPCG_GAME_ENRICHMENT_ENABLED`:** Nenhum enriquecimento
automático acontece. Enriquecimento manual ainda funciona (o
flag controla apenas o trigger automático na criação).

#### Fontes e ordem de prioridade

```
1. Wikidata (SPARQL) — identidade canônica
   ├── Resolver QID por nome do jogo
   ├── Confirmar match via LLM (evita ambiguidade)
   └── Extrair: developer, publisher, franchise, genres, release_date, external_ids.wikidata

2. Wikipedia (REST API) — descrição e lore
   ├── Buscar artigo por QID do Wikidata (ou por nome)
   ├── Extrair: description (primeiros parágrafos)
   └── Extrair: texto para gerar lore_summary

3. LLM (local, Ollama) — lore_summary
   └── Receber texto do Wikipedia → gerar resumo em pt-BR
```

**Ordem é estrita:** Wikidata primeiro (fornece QID que melhora a
busca na Wikipedia). Wikipedia segundo (usa QID do Wikidata). LLM
terceiro (usa texto da Wikipedia). Se Wikidata falha, Wikipedia é
buscada por nome (menos confiável). Se Wikipedia falha, lore_summary
fica NULL.

#### Política de cache

- Resultados do Wikidata/Wikipedia cacheados em
  `Game.metadata_json.enrichment_cache` com timestamp.
- TTL: 30 dias.
- Re-enriquecimento manual **ignora cache** (força re-fetch).
- Re-enriquecimento automático (por erro) **usa cache** se dentro
  do TTL (evita re-fetch desnecessário se o erro foi de LLM, não
  de fetch).

#### Política de retry

- **Falha de fetch (Wikidata/Wikipedia):** 3 tentativas com
  backoff exponencial (1s, 2s, 4s). Se todas falham: registrar
  `enrichment_error`, não enriquecer.
- **Falha de LLM (lore_summary):** 2 tentativas. Se falha:
  `lore_summary` fica NULL, mas o resto do enriquecimento
  (developer, publisher, etc.) é persistido. `enriched_at` é
  setado. `enrichment_error` fica NULL (falha parcial de LLM
  não é erro fatal).
- **Falha de confirmação de identidade (LLM não confirma match
  no Wikidata):** Registrar `enrichment_error="could not confirm
  Wikidata identity"`, não enriquecer. Re-enriquecimento manual
  pode forçar.

#### Boundary transacional

**Todas as escritas em Game acontecem em uma única transação no
final.** As etapas de fetch (Wikidata, Wikipedia) e geração (LLM)
coletam dados em variáveis locais. A persistência é atômica:

```
try:
    wikidata_data = fetch_wikidata(game)  # HTTP
    wikipedia_data = fetch_wikipedia(game, wikidata_data.qid)  # HTTP
    lore = generate_lore(wikipedia_data)  # LLM
    # --- Transação atômica ---
    session.begin()
    game.developer = wikidata_data.developer
    game.publisher = wikidata_data.publisher
    game.franchise = wikidata_data.franchise
    game.genres = wikidata_data.genres
    game.release_date = wikidata_data.release_date
    game.description = wikipedia_data.description
    game.lore_summary = lore
    game.external_ids = {"wikidata": wikidata_data.qid}
    game.enriched_at = now()
    game.enrichment_error = None
    # Adicionar aliases do Wikidata em game_aliases
    for alias in wikidata_data.aliases:
        add_alias_if_not_exists(session, game.id, alias, source="wikidata")
    session.commit()
except FetchError as e:
    game.enrichment_error = str(e)
    session.commit()
```

Se qualquer etapa de fetch falha, **nenhum campo é escrito**. O
Game permanece no estado anterior (recém-criado ou com erro).

#### Quando um Game é considerado enriquecido

Um Game é "enriquecido" quando:
- `enriched_at IS NOT NULL` **E** `enrichment_error IS NULL`

Estes dois campos são **mutuamente exclusivos em sucesso**. Em
falha, `enrichment_error IS NOT NULL` (e `enriched_at` pode ser
NULL ou ter um timestamp de um enriquecimento anterior bem-sucedido
que falhou ao re-enriquecer).

**Queries de estado:**
- Enriquecidos: `WHERE enriched_at IS NOT NULL AND enrichment_error IS NULL`
- Pendentes: `WHERE enriched_at IS NULL AND enrichment_error IS NULL`
- Com erro: `WHERE enrichment_error IS NOT NULL`

#### Lidando com falhas parciais

| Cenário | Comportamento |
|---------|---------------|
| Wikidata OK, Wikipedia falha | `enrichment_error` setado. Nenhum campo escrito. Game permanece pendente. Re-enriquecimento tentará novamente. |
| Wikidata OK, Wikipedia OK, LLM falha | `description` e `lore_summary` ficam NULL. Demais campos persistidos. `enriched_at` setado. `enrichment_error` NULL. Game é "enriquecido" sem lore. |
| Wikidade falha (sem QID) | `enrichment_error` setado. Game permanece pendente. Wikipedia por nome não é tentada (baixa confiabilidade sem QID). |

### 6.6 Atualização (Re-enriquecimento)

#### Quando um Game pode ser enriquecido novamente

| Cenário | Permitido? | Sobrescreve? |
|---------|------------|--------------|
| Game com erro (`enrichment_error IS NOT NULL`) | Sim, automático (se flag on) | Sim — dados anteriores podem estar parciais |
| Game enriquecido, trigger manual explícito | Sim | Sim — usuário pediu |
| Game enriquecido, trigger automático na criação | **Não** | — `enriched_at IS NOT NULL` → não cria job |
| Game enriquecido, cache expirado (30 dias) | **Não automático** | Re-enriquecimento manual apenas |

**Regra:** O enriquecimento automático só acontece para Games
**não-enriquecidos** (`enriched_at IS NULL`). Games já enriquecidos
só são re-enriquecidos por trigger manual explícito. Isto evita
churn (re-fetch desnecessário) e respeita dados que o usuário pode
ter editado manualmente.

#### Quando novas informações substituem antigas

- **Trigger manual:** **Todos os campos enriquecidos são
  sobrescritos** com os novos valores. O usuário pediu
  explicitamente o re-enriquecimento.
- **Trigger automático (erro):** **Todos os campos são
  sobrescritos** (o Game estava com erro, dados anteriores são
  considerados inválidos ou parciais).
- **Edição manual do usuário (via `PUT /api/games/{id}`):** Campos
  editados pelo usuário são marcados em `metadata_json.manual_overrides`
  (lista de campos). O enriquecimento **não sobrescreve** campos
  em `manual_overrides`. Isto protege edições manuais contra
  re-enriquecimento automático.

#### Como evitar chamadas repetidas para as mesmas fontes

1. **Cache em `metadata_json.enrichment_cache`** com TTL de 30 dias.
   Re-enriquecimento automático usa cache se dentro do TTL.
2. **Dedup de jobs pendentes:** Antes de criar um job `game_enrich`,
   verificar se já existe um job `game_enrich` com `status=queued`
   para o mesmo `game_id`. Se sim, não criar duplicata.
3. **`enriched_at` como gate:** Games já enriquecidos não recebem
   novo job automático.

#### Como impedir enriquecimento simultâneo do mesmo Game

**Dedup na criação do job:** O endpoint de trigger (automático ou
manual) verifica:

```python
existing = session.query(Job).filter(
    Job.job_type == "game_enrich",
    Job.game_id == game_id,
    Job.status.in_(["queued", "running"]),
).first()
if existing:
    return  # já existe job pendente, não criar duplicata
```

**Idempotência no processamento:** O `enrich_game` é transacional
(P7 da Readiness Review). Se dois jobs somehow passam pela dedup,
o segundo escreve por cima do primeiro (último write ganha), mas
ambos produzem dados válidos (mesmas fontes, mesmos resultados).
Não há corrupção — apenas wasted work, que a dedup previne.

### 6.7 Reutilização (Deduplicação de Games)

O objetivo é garantir que **um jogo real seja representado por
exatamente um Game** no registry, independentemente de quantas
formas diferentes o usuário se refira a ele.

#### Mecanismo: aliases + slug canônico

Cada Game tem:
- **`slug`** — identificador canônico único, gerado por
  `slugify(canonical_name)`. Ex: `resident-evil-4`.
- **`game_aliases`** — tabela de nomes alternativos, indexada por
  `LOWER(alias)` (unique por game, não globalmente).

#### Algoritmo de deduplicação na criação

Quando o Game Resolver retorna um `game_name` (ou o usuário fornece
um nome), o `GameRegistry` decide criar ou reutilizar:

```
1. Normalizar nome de entrada:
   - lowercase
   - remover acentos (NFD)
   - remover sufixos de plataforma (PS2, PS3, PS4, PS5, PC, Xbox,
     Xbox 360, Xbox One, Switch, Wii, etc.)
   - NÃO remover subtitles ("Scholarship Edition", "Remake",
     "Director's Cut") — estes são aliases, não sufixos

2. Gerar slug do nome normalizado: slugify(normalized)

3. Buscar Game por slug exato:
   SELECT * FROM games WHERE slug = ?
   → Se encontrado: REUTILIZAR. Adicionar nome de entrada como
     alias se não existir. Retornar Game existente.

4. Se não: buscar em game_aliases por LOWER(alias) = normalized:
   SELECT game_id FROM game_aliases WHERE LOWER(alias) = LOWER(?)
   → Se encontrado: REUTILIZAR. O alias aponta para o Game correto.
     Retornar Game do alias.

5. Se não: criar novo Game com slug gerado.
```

#### Resolução de ambiguidade (jogos com mesmo nome)

**Problema:** "Doom" (1993) e "Doom" (2016) têm o mesmo nome. O
slug `doom` colidiria.

**Solução:** Quando há colisão de slug e o `release_date` está
disponível (do enriquecimento ou input do usuário), o slug inclui
o ano: `doom-1993`, `doom-2016`. Se `release_date` não está
disponível, sufixo ordinal: `doom`, `doom-2`, `doom-3`.

**Na dedup:** Se a busca por slug retorna múltiplos candidatos
(por que `doom-1993` e `doom-2016` existem), o algoritmo precisa
desambiguar. Em V2, sem `release_date` no momento da criação, o
sistema:
1. Se há apenas 1 Game com o slug base: reutilizar.
2. Se há múltiplos: marcar `needs_review` para confirmação manual.
   O usuário escolhe qual Game vincular via UI.

#### Exemplo prático: Resident Evil 4

| Upload filename | Nome normalizado | Resolução |
|-----------------|------------------|-----------|
| `Resident Evil 4_2026-07-26_14-32-11.mp4` | `resident evil 4` | L1: slug `resident-evil-4` encontrado → reutilizar |
| `RE4_2026-07-26_15-00-00.mp4` | `re4` | L1: alias `re4` encontrado em `game_aliases` → reutilizar |
| `Resident Evil IV_2026-07-26_16-00-00.mp4` | `resident evil iv` | L1: alias `resident evil iv` encontrado → reutilizar |
| `Resident Evil 4 (PS2)_2026-07-26_17-00-00.mp4` | `resident evil 4` (sufixo PS2 removido) | L1: slug encontrado → reutilizar |
| `Resident Evil 4 Remake_2026-07-26_18-00-00.mp4` | `resident evil 4 remake` | L1: slug `resident-evil-4-remake` encontrado (se existir) → reutilizar. Se não: criar novo Game. |

**"Resident Evil 4" e "Resident Evil 4 Remake" são Games
distintos** — slugs diferentes, entries diferentes no registry.
Isto é correto: são jogos diferentes com gameplay diferente.

**"RE4" e "Resident Evil 4" são o mesmo Game** — `RE4` é alias de
`Resident Evil 4`. Um único Game, múltiplos aliases.

#### Quando aliases são adicionados

| Fonte | Quando | `source` field |
|-------|--------|----------------|
| Resolver | Upload resolve via VLM e o nome do filename difere do canonical | `resolver` |
| Enriquecimento | Wikidata retorna aliases alternativos | `wikidata` |
| Manual | Usuário adiciona via UI (`POST /api/games/{id}/aliases`) | `manual` |

#### Política de confiança por fonte

| Fonte | Confiança | Pode ser removida automaticamente? |
|-------|-----------|-----------------------------------|
| `manual` | Alta (usuário confirmou) | Não |
| `wikidata` | Alta (fonte canônica) | Não (apenas se re-enriquecido e Wikidata não retorna mais) |
| `resolver` | Média (heurística) | Sim, se o alias causar dedup incorreta e for corrigido manualmente |

### 6.8 Integração com o Restante do Pipeline

O Game é a **entidade central** do GPCG. Quase todo componente do
pipeline referencia `game_id`. Abaixo, a participação do Game em
cada etapa:

#### Fluxo completo

```
1. Gameplay Upload
   ├── Usuario faz upload de gameplay
   ├── GameplaySource criado (sem game_id ainda)
   └── Job mapping criado

2. Game Discovery + Resolver
   ├── GameResolver.resolve(filename, video_path, session)
   ├── L1 (filename + aliases) → L2 (capture_source prior) → L3 (VLM)
   └── Retorna ResolutionResult(game_name, method, confidence)

3. Game Registry (criar ou reutilizar)
   ├── GameRegistry.get_or_create(game_name, slug, aliases)
   ├── Se Game existe: reutilizar, adicionar alias se novo
   ├── Se não existe: criar com campos mínimos (§6.4)
   └── GameplaySource.game_id = game.id

4. Game Enrichment (assíncrono)
   ├── Se GPCG_GAME_ENRICHMENT_ENABLED=on E enriched_at IS NULL:
   │   cria job game_enrich
   ├── Worker VPS processa: Wikidata → Wikipedia → LLM
   └── Game atualizado: developer, publisher, franchise, genres,
       description, lore_summary, enriched_at

5. Gameplay Mapping (worker local, GPU)
   ├── Worker baixa gameplay, roda VLM (gemma3:12b) + ASR (Whisper)
   ├── Produz GameplayEvents com descriptions, tags, interesting_score
   ├── Em V2: gera embeddings (nomic-embed-text) por evento
   └── Envia eventos + embeddings ao VPS

6. Content Intelligence (assíncrono, periódico)
   ├── Job content_collect (global, não per-user)
   ├── Para cada Game com gameplay disponível:
   │   collect_rss(game_id) → KnowledgeItems
   ├── KnowledgeItems recebem editorial_score (LLM)
   └── Items disponíveis para Content Planning

7. Editorial Strategy (decide_next_video)
   ├── Se GPCG_CONTENT_INTELLIGENCE_ENABLED=off:
   │   comportamento atual (inventário de Facts por jogo)
   ├── Se on:
   │   considera Facts + KnowledgeItems
   │   filtra por content_scope (game | franchise | developer)
   │   └── franchise/developer vêm do Game enriquecido
   └── Decide qual jogo + qual ideia (Fact ou KnowledgeItem)

8. Content Planning
   ├── get_content_ideas(session, game_id, user_id, scope)
   ├── Consulta Facts (do jogo) + KnowledgeItems (do jogo + escopo)
   ├── Se scope=franchise: KnowledgeItems de jogos da mesma franchise
   │   (JOIN knowledge_items.game_id → games.franchise)
   ├── Se scope=developer: KnowledgeItems de jogos do mesmo developer
   │   (JOIN knowledge_items.game_id → games.developer)
   └── Retorna ContentIdea[] unificado para LLM decidir

9. Story Finding → Editorial Planning
   ├── StoryConcept gerado a partir da ContentIdea
   ├── EditorialPlanner recebe:
   │   - Game.description (contexto do jogo)
   │   - Game.lore_summary (narrativa)
   │   - Game.genres, Game.themes (estilo)
   │   - Knowledge Graph relationships (não em V2)
   └── Produz VideoCreativePlan

10. Creative Engine → Script → Humanization
    ├── Script gerado com contexto enriquecido do Game
    ├── Se baseado em KnowledgeItem: prompt inclui "atribua a fonte,
    │   enfatize atualidade" (para news)
    └── Humanization remove AI-isms

11. Script Review (Critic)
    ├── Factual accuracy gate:
    │   - Se Fact: valida contra Fact.claim
    │   - Se KnowledgeItem: valida contra KnowledgeItem.content
    ├── Se factual_accuracy < 70: rejeição automática
    └── Se REVISE: regenera com feedback

12. Gameplay Selection (Gameplay Retrieval)
    ├── Se GPCG_CROSS_GAME_GAMEPLAY_ENABLED=off:
    │   escopo game (apenas game_id do job)
    ├── Se on:
    │   _expand_game_ids(session, game_id, scope)
    │   ├── scope=game: [game_id]
    │   ├── scope=franchise: [game_id] + jogos com mesma franchise
    │   │   (WHERE Game.franchise = ?)
    │   └── scope=developer: [game_id] + jogos com mesmo developer
    │       (WHERE Game.developer = ?)
    ├── GameplayRetriever.retrieve(game_ids, ...)
    ├── Busca semântica em GameplayEvents dos game_ids
    └── Fallback: se insuficiente, contrai para escopo game

13. Render → QA → Metadata → YouTube Upload
    ├── Vídeo renderizado com gameplay selecionada
    ├── QA avalia qualidade
    ├── Metadata gerada (título, tags)
    ├── Video.knowledge_item_id setado (se baseado em KnowledgeItem)
    └── Publicado no YouTube
```

#### Resumo de dependências do Game por componente

| Componente | Campos do Game utilizados | Estado mínimo necessário |
|------------|--------------------------|--------------------------|
| Game Resolver | `slug`, `game_aliases` | Recém-criado |
| Game Registry | `canonical_name`, `slug` | Recém-criado |
| Game Enrichment | `canonical_name`, `external_ids` | Recém-criado |
| Content Intelligence | `canonical_name` (para RSS query), `game_id` | Recém-criado |
| Content Planning | `game_id` (FK), `franchise`, `developer` (se scope) | Recém-criado (scope=game); Enriquecido (scope=franchise/developer) |
| Editorial Planning | `description`, `lore_summary`, `genres`, `themes` | Recém-criado (sem contexto); Enriquecido (com contexto) |
| Script Generation | `description`, `lore_summary` | Recém-criado (sem contexto); Enriquecido (com contexto) |
| Script Review | Indireto (via Fact.claim ou KnowledgeItem.content) | Qualquer |
| Gameplay Retrieval | `game_id` (FK), `franchise`, `developer` (se cross-game) | Recém-criado (intra-game); Enriquecido (cross-game) |

**Conclusão:** O Game é a entidade pivô. Quanto mais enriquecido,
mais capacidades o sistema tem (cross-game, scoping por franquia,
contexto editorial rico). Mas o sistema **nunca bloqueia** por
falta de enriquecimento — apenas opera com capacidade reduzida.

---

## 7. Knowledge Items e Content Intelligence

### 7.1 Entidade: KnowledgeItem

```sql
CREATE TABLE knowledge_items (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id),  -- NULL = global
    game_id         INTEGER REFERENCES games(id),  -- nullable para conteúdo geral

    -- Identificação
    title           VARCHAR(500) NOT NULL,
    content         TEXT NOT NULL,

    -- Classificação
    item_type       VARCHAR(30) NOT NULL,  -- news|curiosity|lore|fact
    source_type     VARCHAR(30) NOT NULL,  -- rss|wikipedia|steam|reddit|igdb|user_doc

    -- Proveniência
    source_url      VARCHAR(1000),
    source_name     VARCHAR(200),  -- "Google News", "Wikipedia"
    published_at    DATETIME,
    collected_at    DATETIME NOT NULL,

    -- Qualidade editorial
    editorial_score FLOAT DEFAULT 0.0,  -- 0-100, único score

    -- Estado
    status          VARCHAR(20) DEFAULT 'fresh',  -- fresh|used|rejected

    -- Denormalização para filtro sem JOIN
    franchise       VARCHAR(200),  -- denormalizado de Game.franchise
    developer       VARCHAR(200),  -- denormalizado de Game.developer

    -- Metadados
    tags            JSON DEFAULT '[]',

    -- Deduplicação
    content_hash    VARCHAR(64),  -- SHA256(normalize(title) + normalize(content[:500]))

    created_at      DATETIME,
    updated_at      DATETIME
);

CREATE INDEX idx_ki_user ON knowledge_items(user_id);
CREATE INDEX idx_ki_game ON knowledge_items(game_id);
CREATE INDEX idx_ki_type ON knowledge_items(item_type);
CREATE INDEX idx_ki_status ON knowledge_items(status);
CREATE INDEX idx_ki_score ON knowledge_items(editorial_score DESC);
CREATE INDEX idx_ki_franchise ON knowledge_items(franchise);
CREATE INDEX idx_ki_developer ON knowledge_items(developer);
CREATE UNIQUE INDEX idx_ki_hash ON knowledge_items(content_hash);
```

**Não há:** `summary` (LLM preview especulativo), `source_author`,
`language` (sistema é pt-BR), `keywords` (sobrepõe `tags`),
`relevance_score`/`freshness_score`/`composite_score`, `used_in_video_id`
(ver D13 — rastreabilidade via `Video.knowledge_item_id`),
`franchise_id`/`company_id` (são strings, não FKs).

### 7.2 Embeddings

```sql
CREATE TABLE knowledge_item_embeddings (
    item_id     INTEGER PRIMARY KEY REFERENCES knowledge_items(id) ON DELETE CASCADE,
    embedding   BLOB,       -- vetor nomic-embed-text serializado
    model       VARCHAR(50),  -- "nomic-embed-text"
    created_at  DATETIME
);
```

Tabela separada (D4). Migração para pgvector: `ALTER TABLE
knowledge_items ADD COLUMN embedding vector(768)`, backfill da BLOB
table, drop da BLOB table.

### 7.3 Tipos de Item

| Tipo | Descrição | Fonte típica | Peso de frescor |
|------|-----------|--------------|-----------------|
| `news` | Notícia atual | RSS | Alto (ordenar por data) |
| `curiosity` | Curiosidade evergreen | RSS/Wikipedia | Baixo |
| `lore` | História/narrativa do jogo | Wikipedia | Nulo (evergreen) |
| `fact` | Fact de documento do usuário | user_doc | Nulo |

**Não há em V2:** `event`, `history`, `release`, `article`, `trivia`,
`discussion`, `changelog`. Tipos são adicionados ao enum quando um
conector os produzir e o pipeline editorial os tratar
diferentemente.

### 7.4 Relação com Fact

`Fact` continua para facts de documentos do usuário. `KnowledgeItem`
é exclusivamente para conteúdo externo. **Sem espelhamento (D3).**

O `ContentPlanningService` consulta ambas as fontes via um método
unificado:

```python
def get_content_ideas(session, game_id, user_id, scope, limit):
    """Retorna ideias de conteúdo unificadas de Facts + KnowledgeItems."""
    facts = session.query(Fact).filter(Fact.game_id == game_id, ...).all()
    items = session.query(KnowledgeItem).filter(
        KnowledgeItem.game_id.in_(expanded_game_ids),
        KnowledgeItem.status == "fresh",
    ).order_by(KnowledgeItem.editorial_score.desc()).limit(limit).all()
    # Unificar em formato comum para o LLM decidir
    return _merge_sources(facts, items)
```

A unificação é na camada de query, não na de armazenamento. O LLM
recebe uma lista de "ideias" sem saber se vieram de Fact ou
KnowledgeItem.

### 7.5 Content Intelligence — Coleta

Módulo funcional (não OOP):

```python
# src/gpcg/application/content_collectors.py

def collect_rss(session, game_id: int, since: datetime = None) -> int:
    """Coleta notícias via Google News RSS para um jogo.
    Retorna número de KnowledgeItems criados.
    """
    game = session.get(Game, game_id)
    url = f"https://news.google.com/rss/search?q={quote(game.canonical_name)}&hl=pt-BR&gl=BR"
    feed = feedparser.parse(url)
    count = 0
    for entry in feed.entries:
        item = _normalize_rss_entry(entry, game)
        if _dedup(session, item):
            session.add(item)
            count += 1
    session.commit()
    return count
```

**Ciclo de coleta:** Job `content_collect` periódico (a cada N horas,
configurável em `Automation.config.content_intelligence.collection_interval_hours`).
Para cada jogo com gameplay disponível do usuário: chama `collect_rss`.

**Deduplicação:** `content_hash = SHA256(normalize(title) +
normalize(content[:500]))`. UNIQUE constraint previne duplicatas
exatas. Near-duplicates (mesma notícia de fontes diferentes) não são
deduplicados em V2 — o `editorial_score` e a ordenação naturalmente
priorizam o melhor.

### 7.6 Scoring Editorial

```python
def score_knowledge_item(item: KnowledgeItem, llm: LLMClient) -> float:
    """Score 0-100 baseado em potencial editorial.
    Reusa a metodologia do CuriosityScorer (curiosity_gap, surprise,
    retention, familiarity, insight) adaptada para o tipo do item.
    """
```

O score é computado no momento da coleta (job `content_collect`) e
armazenado em `editorial_score`. Recomputação manual via endpoint.

### 7.7 Retenção

News items com `status='fresh'` e `published_at < now - 30 dias` são
deletados por um cleanup periódico (no ciclo de coleta). Evergreen
items (curiosity, lore) são retidos indefinidamente. Isto limita o
crescimento da tabela.

---

## 8. Gameplay Intelligence

### 8.1 Embeddings em GameplayEvents

```sql
CREATE TABLE gameplay_event_embeddings (
    event_id    INTEGER PRIMARY KEY REFERENCES gameplay_events(id) ON DELETE CASCADE,
    embedding   BLOB,
    model       VARCHAR(50),
    created_at  DATETIME
);
```

Gerados durante o mapping (worker local, GPU) após a descrição do
evento ser produzida pelo VLM. Modelo: `nomic-embed-text` (já
disponível no Ollama).

### 8.2 Cross-Game Matching

Função de expansão + reuso do retriever existente (D5):

```python
# src/gpcg/application/gameplay_retriever.py (estendido)

def _expand_game_ids(session, game_id: int, scope: str) -> list[int]:
    """Expande game_id para jogos compatíveis baseado no escopo.

    scope="game":      [game_id]
    scope="franchise": [game_id] + jogos com mesma franchise
    scope="developer": [game_id] + jogos com mesmo developer
    """
    game = session.get(Game, game_id)
    if scope == "game" or not game:
        return [game_id]

    ids = [game_id]
    if scope == "franchise" and game.franchise:
        ids += [g.id for g in session.query(Game).filter(
            Game.franchise == game.franchise, Game.id != game_id
        ).all()]
    elif scope == "developer" and game.developer:
        ids += [g.id for g in session.query(Game).filter(
            Game.developer == game.developer, Game.id != game_id
        ).all()]
    return ids
```

`GameplayRetriever.retrieve()` é estendido para aceitar
`game_ids: list[int]` (backward-compatible: se receber `game_id: int`,
wraps em lista). A busca semântica e o interesting scoring existentes
operam sobre todos os game_ids.

**Escopos V2:** `game`, `franchise`, `developer`. **Não há:**
`company` (redundante com developer), `genre` (JSON overlap é mais
complexo, defer), `theme` (idem).

### 8.3 Fallback

```
1. Escopo configurado (game|franchise|developer) → buscar
2. Se gameplay insuficiente: contrair para escopo "game" (apenas o jogo)
3. Se ainda insuficiente: usar GameplaySelector (random) como hoje
4. Não há fallback para gameplays públicas em V2 (deferido)
```

**Não há cascata de 5 níveis** (game→franchise→company→genre→public).
A cascata é simples: escopo configurado → escopo game → random.

### 8.4 Feature Flag

`GPCG_CROSS_GAME_GAMEPLAY_ENABLED` (default: off). Quando off,
comportamento é idêntico ao atual (escopo game). Quando on, usa o
escopo configurado em `Automation.config.gameplay_selection.scope`.

**Experimento A/B obrigatório** antes de mudar default para on:
5 vídeos escopo game vs 5 escopo franchise, avaliar Camada 2
(clareza, payoff — o espectador confunde com gameplay de outro jogo?).

---

## 9. Pipeline Editorial

### 9.1 Pipeline (estágios)

O pipeline existente é mantido. Dois estágios são modificados:

```
content_planning → story_finding → editorial_planning → creative_engine
→ script → humanization → script_review → tts → gameplay_selection
→ music_selection → render_plan → render → qa → metadata → youtube_upload
```

**Mudanças:**

| Estágio | Mudança |
|---------|---------|
| `content_planning` | Consulta Facts + KnowledgeItems (unificado) em vez de apenas Facts |
| `editorial_planning` | Recebe `Game.description` e `Game.lore_summary` como contexto adicional |
| `script` | Recebe contexto enriquecido do Game (description, lore_summary) |
| `script_review` | Gate de factual accuracy valida contra `KnowledgeItem.content` (ou `Fact.claim`) |
| `gameplay_selection` | Aceita `game_ids: list[int]` (cross-game) quando flag on |

**Não há novos estágios.** O pipeline não fica mais longo.

### 9.2 Tratamento de Notícias

KnowledgeItems do tipo `news` fluem pelo mesmo pipeline. O prompt do
script inclui instrução: "este conteúdo é uma notícia — atribua a
fonte, enfatize atualidade, não trate como curiosidade evergreen".

**Não há branch editorial separado para news.** Se A/B mostrar que
news não funciona com o pipeline atual, não produzir news (desativar
`produce_news` na config).

### 9.3 Gate de Factual Accuracy (D14)

O `script_critic` existente valida factual accuracy contra a fonte:
- Para Facts: contra `Fact.claim` (como hoje)
- Para KnowledgeItems: contra `KnowledgeItem.content`

Se o roteiro inventa informações não presentes no `content` fonte,
o critic flagga e rejeita (factual_accuracy < 70 = rejeição
automática, como hoje).

### 9.4 Feature Flags

| Flag | Default | Efeito quando on |
|------|---------|------------------|
| `GPCG_CONTENT_INTELLIGENCE_ENABLED` | off | Content planning consulta KnowledgeItems |
| `GPCG_CROSS_GAME_GAMEPLAY_ENABLED` | off | Gameplay selection usa escopo configurado |
| `GPCG_GAME_ENRICHMENT_ENABLED` | off | Novos jogos são enriquecidos automaticamente |

Todas as flags default off. Ativação requer experimento A/B
(`EDITORIAL_EVALUATION.md` §7).

---

## 10. Banco de Dados

### 10.1 Novas Tabelas

| Tabela | Propósito |
|--------|-----------|
| `game_aliases` | Aliases individuais indexáveis |
| `knowledge_items` | Banco de ideias (conteúdo externo) |
| `knowledge_item_embeddings` | Embeddings de KnowledgeItems (separada) |
| `gameplay_event_embeddings` | Embeddings de GameplayEvents (separada) |

**4 tabelas novas** (vs 7 da proposta original).

### 10.2 Colunas Novas em Tabelas Existentes

#### `games` (12 colunas novas)

`slug`, `description`, `release_date`, `developer`, `publisher`,
`franchise`, `genres`, `themes`, `lore_summary`, `external_ids`,
`enriched_at`, `enrichment_error`.

#### `videos` (1 coluna nova)

`knowledge_item_id` (nullable FK → `knowledge_items.id`).

### 10.3 Enums Novos

```python
class KnowledgeItemType(str, enum.Enum):
    news = "news"
    curiosity = "curiosity"
    lore = "lore"
    fact = "fact"

class KnowledgeItemSource(str, enum.Enum):
    rss = "rss"
    wikipedia = "wikipedia"
    steam = "steam"      # futuro
    reddit = "reddit"    # futuro
    igdb = "igdb"        # futuro
    user_doc = "user_doc"

class KnowledgeItemStatus(str, enum.Enum):
    fresh = "fresh"
    used = "used"
    rejected = "rejected"

class ContentScope(str, enum.Enum):
    game = "game"
    franchise = "franchise"
    developer = "developer"
```

**4 enums** (vs 6 da proposta original).

### 10.4 Job Types Novos

```python
class JobType(str, enum.Enum):
    ...
    game_enrich = "game_enrich"
    content_collect = "content_collect"
```

### 10.5 Schema Evolution

Continua com `_ensure_column()` para colunas aditivas e
`Base.metadata.create_all()` para novas tabelas. Scripts de migração
de dados (slug generation, aliases migration, user_id deprecation)
são funções idempotentes em `init_db()`, guardadas por flag em
`metadata_json.schema_migrations`.

**Não há Alembic em V2.** Introduzido quando migrar para PostgreSQL.

### 10.6 Índices

Índices definidos nas seções de cada tabela (§4.2, §7.1). Destaques:
- `game_aliases(LOWER(alias))` UNIQUE — busca de alias O(log n)
- `knowledge_items(content_hash)` UNIQUE — dedup
- `knowledge_items(editorial_score DESC)` — ranking
- `knowledge_items(franchise)`, `knowledge_items(developer)` — filtro por escopo

---

## 11. API

### 11.1 Routers Novos

**2 routers** (vs 4 da proposta original):

#### `api/game_registry_routes.py`

| Método | Path | Descrição |
|--------|------|-----------|
| GET | `/api/games/registry` | Lista jogos canônicos (global) com paginação |
| GET | `/api/games/{slug}` | Detalhe de um jogo (dados enriquecidos) |
| GET | `/api/games/search?q={query}` | Busca por nome/alias |
| POST | `/api/games/{id}/enrich` | Trigger enriquecimento manual |
| POST | `/api/games/{id}/aliases` | Adicionar alias |
| DELETE | `/api/games/{id}/aliases/{alias_id}` | Remover alias |

#### `api/knowledge_item_routes.py`

| Método | Path | Descrição |
|--------|------|-----------|
| GET | `/api/knowledge-items` | Lista ideias (filtros: game, type, status) |
| GET | `/api/knowledge-items/{id}` | Detalhe de uma ideia |
| POST | `/api/knowledge-items/{id}/reject` | Rejeitar ideia |
| POST | `/api/knowledge-items/collect` | Trigger coleta manual |
| GET | `/api/knowledge-items/stats` | Stats do banco de ideias |

### 11.2 Endpoints Alterados

| Endpoint | Mudança |
|----------|---------|
| `GET /api/games` | Retorna dados enriquecidos (description, developer, franchise, genres) |
| `GET /api/dashboard` | Inclui stats de KnowledgeItems, jogos enriquecidos |
| `POST /api/gameplays/upload` | Após criar GameplaySource, trigger `game_enrich` se jogo não enriquecido |
| `GET /api/jobs/{id}/data` | Inclui KnowledgeItems relevantes + Game enriquecido |

### 11.3 Config em Automação (endpoint existente)

Configurações de content intelligence e gameplay selection vão no
`Automation.config` JSON, editadas via endpoint `PUT /api/automation`
existente. **Não há router de content_intelligence separado.**

**~11 endpoints novos** (vs ~25 da proposta original).

---

## 12. Frontend

### 12.1 Nova Página: Content Ideas

**Rota:** `/gpcg/ideas`

- Lista de KnowledgeItems com filtros (tipo, jogo, status, score)
- Card de cada ideia: título, conteúdo (preview), fonte, score, tipo
- Ação: rejeitar
- Botão "Coletar agora" para trigger manual
- Stats: total de ideias, por tipo, por fonte

**Justificativa:** Um banco de ideias é uma superfície de UX
genuinamente nova que não cabe em nenhuma página existente.

### 12.2 Alteração: Página de Content

- Nova seção/lista de jogos com indicador de enriquecimento
(enriquecido / pendente / erro)
- Botão "Enriquecer" por jogo

### 12.3 Alteração: Página de Automação

- Nova seção "Content Intelligence" com toggles:
  - Ativar content intelligence
  - Produzir notícias
  - Produzir evergreen
  - Escopo (game / franchise / developer)
- Nova seção "Gameplay Selection" com:
  - Escopo de busca (game / franchise / developer)

### 12.4 Alteração: Página de Dashboard

- Card "Banco de Ideias": total, ideias frescas
- Card "Conhecimento": jogos enriquecidos, pendentes

### 12.5 Não Implementado em V2

- Knowledge Graph Explorer (D2 — sem grafo)
- Página de Game Registry separada (integrada na página de Content)
- Visualização interativa de grafo (D3/vis.js)
- Dropdown de visibilidade de gameplay (D8 — deferido)

**1 página nova, 3 páginas alteradas** (vs 3 páginas novas da
proposta original). **Sem novas dependências JS.**

---

## 13. Worker, Jobs e Filas

### 13.1 Novos Job Types

| Job Type | Onde roda | GPU? | Capability |
|----------|-----------|------|------------|
| `game_enrich` | VPS | Não | `enrichment` |
| `content_collect` | VPS | Não | `content_intelligence` |

Ambos rodam no VPS (Control Plane) — não ocupam a GPU do worker local.

### 13.2 Processamento

O worker legacy no VPS (ou um processador leve integrado à API)
processa `game_enrich` e `content_collect`. O remote worker local
(GPU) continua processando apenas `mapping`, `generation`,
`knowledge_index`.

### 13.3 Agendamento de Content Collect

O worker local polla `/api/automation/check` a cada ciclo (mecanismo
existente). O endpoint verifica se passou `collection_interval_hours`
desde a última coleta e cria job `content_collect` se necessário.

### 13.4 Dependências Python Novas

| Pacote | Propósito |
|--------|-----------|
| `feedparser` | Parse de RSS feeds |

Wikipedia e Wikidata usam `httpx` (já instalado).

---

## 14. Sincronização VPS ↔ Worker

### 14.1 `get_job_data` (worker_routes.py)

Inclui dados novos para jobs de geração:

```python
def get_job_data(job_id, ...):
    # ... existente ...

    # NOVO: KnowledgeItems relevantes (do usuário + globais)
    knowledge_items = db.query(KnowledgeItem).filter(
        or_(
            KnowledgeItem.user_id == job.user_id,
            KnowledgeItem.user_id.is_(None),
        ),
        KnowledgeItem.status == "fresh",
    ).order_by(KnowledgeItem.editorial_score.desc()).limit(30).all()
    data["knowledge_items"] = [ki.to_dict() for ki in knowledge_items]

    # NOVO: Game enriquecido (description, lore_summary, developer, franchise)
    game = db.query(Game).filter(Game.id == job.game_id).first()
    if game:
        data["game"]["description"] = game.description
        data["game"]["lore_summary"] = game.lore_summary
        data["game"]["developer"] = game.developer
        data["game"]["franchise"] = game.franchise
        data["game"]["genres"] = game.genres
```

**Não sincroniza:** GameRelationships (não existem), GameEntities
(não existem), Franchises/Series/Characters (não existem).

### 14.2 `local_db_sync.py`

`populate_local_db` estendido para popular:
- `knowledge_items` (do `job_data["knowledge_items"]`)
- Campos enriquecidos do `Game` (description, lore_summary, etc.)

**Não popula:** embeddings de KnowledgeItems (não necessários para
geração local — a busca semântica de gameplay usa embeddings de
GameplayEvents, que já são synced).

### 14.3 Embeddings de GameplayEvents

Gerados no worker local (GPU, durante mapping) e enviados ao VPS
via `submit_mapping_result` (já existente). O VPS persiste em
`gameplay_event_embeddings`.

---

## 15. Configuração de Automações

### 15.1 Estrutura

Novas seções em `Automation.config`:

```json
{
  "content_intelligence": {
    "enabled": false,
    "produce_news": true,
    "produce_evergreen": true,
    "content_scope": "game",
    "collection_interval_hours": 6,
    "min_editorial_score": 50
  },
  "gameplay_selection": {
    "scope": "game",
    "fallback_strategy": "contract_to_game"
  }
}
```

### 15.2 Descrição

#### Content Intelligence

| Config | Tipo | Default | Descrição |
|--------|------|---------|-----------|
| `enabled` | bool | false | Liga/desliga content intelligence |
| `produce_news` | bool | true | Produz vídeos de notícias |
| `produce_evergreen` | bool | true | Produz conteúdo evergreen (curiosidades, lore) |
| `content_scope` | str | "game" | game, franchise, developer |
| `collection_interval_hours` | int | 6 | Intervalo entre coletas |
| `min_editorial_score` | int | 50 | Score mínimo do KnowledgeItem |

#### Gameplay Selection

| Config | Tipo | Default | Descrição |
|--------|------|---------|-----------|
| `scope` | str | "game" | game, franchise, developer |
| `fallback_strategy` | str | "contract_to_game" | contract_to_game, skip |

**Não há em V2:** `produce_general_content`, `news_max_age_hours`,
`allow_public_gameplays`, `min_gameplay_duration`, `knowledge_sources`
(toggles de conectores — apenas RSS existe, sempre on quando
content_intelligence habilitado), `custom_rss_feeds`.

### 15.3 Impacto no Editorial Strategy

`EditorialStrategyService.decide_next_video()` é estendido para
considerar KnowledgeItems além de Facts, filtrando por
`content_scope` e `min_editorial_score`. Quando
`content_intelligence.enabled=false`, comportamento é idêntico ao
atual.

---

## 16. Escalabilidade e Migração Futura

### 16.1 Limites em V2 (SQLite)

| Dimensão | Limite confortável | Mitigação |
|----------|-------------------|-----------|
| Jogos | ~10.000 | Índices em slug, game_aliases |
| KnowledgeItems | ~50.000 | Índices em editorial_score, content_hash; retenção de news |
| GameplayEvents | ~100.000 | Índices em game_id; embeddings em tabela separada |
| Busca semântica | ~10.000 itens | O(n) em Python; migrar para pgvector acima disso |

### 16.2 Migração para PostgreSQL

Quando o volume justificar (>50K KnowledgeItems ou busca semântica
>1s):

1. Trocar `GPCG_DB_PATH` por `GPCG_DB_URL=postgresql://...`
2. Introduzir Alembic para migrations
3. Migrar embeddings: `knowledge_item_embeddings` BLOB →
   `knowledge_items.embedding vector(768)` com pgvector
4. Migrar `gameplay_event_embeddings` BLOB →
   `gameplay_events.embedding vector(768)` com pgvector
5. Migrar JSON columns para JSONB (indexação GIN)
6. `game_aliases(LOWER(alias))` → `pg_trgm` para busca fuzzy

**A lógica de aplicação não muda** (SQLAlchemy abstrai o banco).
Embeddings em tabelas separadas (D4) tornam a migração um backfill
+ drop, sem mexer no schema principal.

### 16.3 Evolução de Entidades (strings → tabelas)

Quando houver >50 empresas E conteúdo sobre empresas (não apenas
sobre jogos):

1. Criar tabela `companies(id, slug, canonical_name, founded_year, country, parent_id, external_ids)`
2. `SELECT DISTINCT developer FROM games` → popular `companies`
3. Adicionar `developer_id` FK em `games`, backfill from `developer` string
4. Eventualmente drop `developer` string (ou manter como denormalizado)

**Strings canônicos (do enriquecimento) garantem que a migração é
limpa** — "Rockstar Games" é consistente across todos os jogos.

### 16.4 Evolução para Knowledge Graph

Quando cross-game matching por gênero/tema for necessário E queries
JSON overlap provarem ser insuficientes:

1. Criar `game_relationships(from_game_id, to_game_id, relationship, weight)`
2. Popular durante enrichment
3. Estender `_expand_game_ids` para usar relacionamentos

**A função `_expand_game_ids` é o ponto de extensão.** Adicionar
grafo não requer mudança no `GameplayRetriever`.

---

## 17. Plano de Implementação

### Fase 1: Game Registry Canônico

**Entregáveis:**
- Colunas novas em `games`: slug, description, release_date, developer,
  publisher, franchise, genres, themes, lore_summary, external_ids,
  enriched_at, enrichment_error
- Tabela `game_aliases`
- Migração: slug generation, aliases JSON → game_aliases, user_id
  deprecation
- `GameRegistry` service: CRUD, dedup na criação, busca por alias
- API: `game_registry_routes.py` (6 endpoints)
- Frontend: seção de jogos na página de Content com status de
  enriquecimento

**Dependências:** Nenhuma.
**Feature flag:** Nenhuma (aditivo, não muda comportamento).

### Fase 2: Game Knowledge Enrichment

**Entregáveis:**
- `content_collectors.py` → `game_enrichment.py`:
  `enrich_game(session, game_id)` função
- `WikidataClient`, `WikipediaClient` (httpx direto, em
  `infrastructure/`)
- Job type `game_enrich`, processado no VPS
- Auto-trigger enriquecimento quando novo jogo é criado
- Frontend: botão "Enriquecer" na lista de jogos

**Dependências:** Fase 1.
**Feature flag:** `GPCG_GAME_ENRICHMENT_ENABLED` (default off).

### Fase 3: Knowledge Items + Content Intelligence

**Entregáveis:**
- Tabelas `knowledge_items`, `knowledge_item_embeddings`
- `content_collectors.py`: `collect_rss(session, game_id)` função
- `KnowledgeItem` service: CRUD, scoring, dedup
- Job type `content_collect`, processado no VPS
- Agendamento via `/api/automation/check` (extensão do endpoint
  existente)
- API: `knowledge_item_routes.py` (5 endpoints)
- Frontend: página Content Ideas

**Dependências:** Fase 1 (Game Registry para scoping).
**Feature flag:** `GPCG_CONTENT_INTELLIGENCE_ENABLED` (default off).

### Fase 4: Gameplay Intelligence

**Entregáveis:**
- Tabela `gameplay_event_embeddings`
- Geração de embeddings durante mapping (worker local)
- Sync de embeddings para VPS via `submit_mapping_result`
- `_expand_game_ids(session, game_id, scope)` função
- `GameplayRetriever.retrieve()` estendido para `game_ids: list[int]`
- Busca semântica cross-game (reusando `search_events` existente)

**Dependências:** Fase 1 (Game.franchise/developer para expansão).
**Feature flag:** `GPCG_CROSS_GAME_GAMEPLAY_ENABLED` (default off).

### Fase 5: Pipeline Editorial

**Entregáveis:**
- `ContentPlanningService` consulta Facts + KnowledgeItems (unificado)
- `EditorialPlanner` recebe `Game.description` + `Game.lore_summary`
- `ScriptService` recebe contexto enriquecido
- `ScriptCritic` valida factual accuracy contra `KnowledgeItem.content`
- `Video.knowledge_item_id` para rastreabilidade
- Config de automação: seções `content_intelligence` e
  `gameplay_selection`

**Dependências:** Fase 2, 3, 4.
**Feature flags:** `GPCG_CONTENT_INTELLIGENCE_ENABLED`,
`GPCG_CROSS_GAME_GAMEPLAY_ENABLED`.

### Fase 6: Frontend Final

**Entregáveis:**
- Página de Automação: seções Content Intelligence + Gameplay Selection
- Dashboard: cards Banco de Ideias + Conhecimento
- Refinamentos da página Content Ideas (filtros, stats)

**Dependências:** Fase 3, 5.

### Ordem de Execução

```
Fase 1 (Game Registry)
    ↓
Fase 2 (Enrichment) ←── depende de Fase 1
    ↓ (paralelo com Fase 3)
Fase 3 (Knowledge Items) ←── depende de Fase 1
    ↓
Fase 4 (Gameplay Intelligence) ←── depende de Fase 1
    ↓
Fase 5 (Pipeline Editorial) ←── depende de Fase 2, 3, 4
    ↓
Fase 6 (Frontend Final) ←── depende de Fase 3, 5
```

**Paralelização:** Fase 2, 3 e 4 podem rodar em paralelo após Fase 1.

### Critérios de Aceitação

#### Fase 1
- [ ] Novo jogo criado tem slug único
- [ ] Aliases são buscáveis individualmente
- [ ] Upload com nome de jogo existente vincula ao Game existente (não cria duplicata)
- [ ] `Game.user_id` deprecated sem quebrar queries existentes
- [ ] UI mostra lista de jogos com status de enriquecimento

#### Fase 2
- [ ] Novo jogo é enriquecido automaticamente após criação (flag on)
- [ ] Enriquecimento preenche description, developer, publisher, franchise, genres
- [ ] Botão "Enriquecer" funciona na UI
- [ ] Re-enriquecimento sobrescreve apenas se erro anterior ou trigger manual

#### Fase 3
- [ ] RSS connector coleta notícias de Google News
- [ ] KnowledgeItems são deduplicados por content_hash
- [ ] Scoring editorial funciona (editorial_score)
- [ ] UI mostra banco de ideias com filtros
- [ ] Retenção de news > 30 dias funciona

#### Fase 4
- [ ] GameplayEvents têm embeddings após mapping
- [ ] Busca semântica encontra eventos relevantes
- [ ] Cross-game (escopo franchise) retorna gameplay de jogos da mesma franquia
- [ ] Fallback contrai para escopo game quando insuficiente
- [ ] Flag off = comportamento idêntico ao atual

#### Fase 5
- [ ] ContentPlanningService consulta Facts + KnowledgeItems
- [ ] ScriptCritic valida factual accuracy contra KnowledgeItem.content
- [ ] Video.knowledge_item_id é setado quando vídeo baseado em KnowledgeItem
- [ ] Config de automação aceita content_intelligence e gameplay_selection

#### Fase 6
- [ ] Configurações de Content Intelligence na UI
- [ ] Configurações de Gameplay Selection na UI
- [ ] Dashboard mostra novos cards

### Experimentos A/B Obrigatórios

Antes de ativar permanentemente qualquer flag:

1. **`GPCG_GAME_ENRICHMENT_ENABLED`:** Comparar 5 roteiros com jogo
   enriquecido vs 5 sem enriquecimento. Avaliar Camada 2 (descoberta,
   clareza — o contexto extra melhora o roteiro?).

2. **`GPCG_CONTENT_INTELLIGENCE_ENABLED`:** Comparar 5 vídeos de
   KnowledgeItem (RSS) vs 5 vídeos de Fact (baseline). Avaliar Camada
   2 (descoberta, payoff — conteúdo externo gera vídeos válidos?).

3. **`GPCG_CROSS_GAME_GAMEPLAY_ENABLED`:** Comparar 5 vídeos escopo
   game vs 5 escopo franchise. Avaliar Camada 2 (clareza, payoff —
   gameplay de outro jogo confunde o espectador?).

Cada experimento registrado em `docs/editorial_experiments/EXP-NNN-*.md`
conforme `EDITORIAL_EVALUATION.md` §4.

---

## 18. Componentes Deferidos

Cada componente abaixo foi removido de V2 com justificativa. Nenhum
bloqueia evolução futura — todos podem ser adicionados quando a
necessidade for comprovada.

| Componente | Por que deferido | Como reintroduzir |
|------------|-----------------|-------------------|
| `GameEntity` / `Company` table | Strings em Game resolvem queries de V2. God-table heterogênea é design ruim. | Quando >50 empresas E conteúdo sobre empresas. Migração string→FK é limpa com nomes canônicos. |
| `Franchise` table | String em Game resolve. | Quando franquias precisarem de metadata própria (descrição, lore transversal). |
| `Series` table | Distinção série/franquia raramente necessária. `franchise` string cobre ambos. | Quando houver casos reais de série distinta de franquia. |
| `Character` table | Personagens são conteúdo de roteiro, não infraestrutura. | Se feature futura exigir relacionamentos de personagens. |
| `game_relationships` (Knowledge Graph) | Strings/JSON em Game resolvem matching de V2. 18 tipos é ontologia prematura. | Quando matching por gênero/tema precisar de traversals ou relacionamentos inferidos. |
| Gameplays públicas | 2 usuários, 1 ativo. Complexidade de visibility/consent/storage para zero valor. | Quando ≥3 usuários ativos com gameplays. Adicionar `visibility` column é trivial. |
| Steam connector | Wikipedia cobre conhecimento essencial. Steam adiciona dependência de key. | Quando dados de Steam (review tags, news) provarem valor editorial em A/B. |
| Reddit connector | r/gaming é majoritariamente memes. Signal-to-noise baixo para short-form. OAuth2 adiciona complexidade. | Quando houver evidência de que discussões do Reddit geram conteúdo editorialmente válido. |
| IGDB connector | API paga. Marcado opcional na própria proposta original. | Quando tier gratuito for suficiente e dados (temas, keywords) provarem valor. |
| Knowledge Graph Explorer (D3/vis.js) | Sem grafo para explorar em V2. | Junto com `game_relationships`, se justificado. |
| 4 scores em KnowledgeItem | 1 score + ordenação por data resolve ranking. | Se fórmula de composição complexa provar valor em A/B. |
| `summary` (LLM preview) em KnowledgeItem | `title` + `content[:200]` basta para preview. | Se UI de preview precisar de resumo mais elaborado. |
| `historical_context`, `reception_summary`, `development_history` em Game | Sem caso de uso editorial comprovado. `lore_summary` cobre o essencial. | Quando pipeline editorial provar que os utiliza com benefício medido. |
| `enrichment_status` enum (5 estados) | `enriched_at` + `enrichment_error` (2 campos) cobrem o ciclo. | Se estados adicionais (enriching, manual) provarem necessários. |
| `GameplayVisibility` (unlisted) | `unlisted` não tem comportamento definido. | Se UX futura exigir "acessível via link mas não listado". |
| `times_used_publicly` em GameplaySource | Métrica para marketplace de gameplays (deferido). | Junto com gameplays públicas. |
| `ConnectorManager` + `ContentConnector` ABC | 1 conector não justifica framework. | Quando ≥3 conectores com lógica compartilhada. Extrair base então. |
| `GameplayMatcher` (classe com score multiplicativo) | Reusar `GameplayRetriever` + `_expand_game_ids` é mais simples e robusto. | Se scoring multi-fator provar superioridade em A/B. Estender retriever com scorer injetável. |
| Dedup com Levenshtein + `needs_review` | Prevenção na criação resolve. | Se duplicatas acumularem apesar da prevenção. |

---

## Apêndice: Estrutura de Diretórios

```
src/gpcg/
├── api/
│   ├── game_registry_routes.py       # NOVO
│   ├── knowledge_item_routes.py      # NOVO
│   └── ... (existentes)
├── application/
│   ├── game_enrichment.py            # NOVO (função enrich_game)
│   ├── content_collectors.py         # NOVO (função collect_rss)
│   ├── knowledge_item_service.py     # NOVO (CRUD + scoring)
│   ├── gameplay_retriever.py         # ESTENDIDO (_expand_game_ids, game_ids list)
│   └── ... (existentes)
├── infrastructure/
│   ├── wikidata_client.py            # NOVO
│   ├── wikipedia_client.py           # NOVO
│   └── ... (existentes)
└── domain/
    └── models.py                     # ESTENDIDO (Game + KnowledgeItem + embeddings)
```

**4 arquivos novos, 2 estendidos** (vs ~15 arquivos novos da proposta
original).

---

## Apêndice: Impacto Comparado

| Aspecto | Proposta Original | Revisão Crítica | **V2 Definitiva** |
|---------|-------------------|-----------------|-------------------|
| Tabelas novas | 7 | 3 | **4** |
| Colunas novas em Game | ~20 | 8 | **12** |
| Enums novos | 6 | 2 | **4** |
| Routers novos | 4 | 2 | **2** |
| Endpoints novos | ~25 | ~10 | **~11** |
| Páginas frontend novas | 3 | 0 | **1** |
| Conectores | 5 + ABC + manager | 1 | **1 (RSS)** |
| Services novos | 4 | 1 | **2 (enrichment, knowledge_item)** |
| Job types novos | 2 | 0 | **2** |
| Relationship types | 18 | 0 | **0** |
| Scores por KnowledgeItem | 4 | 1 | **1** |
| Arquivos Python novos | ~15 | ~4 | **4** |
| Dependências Python novas | 2 | 1 | **1 (feedparser)** |
| Dependências JS novas | 1 (vis.js) | 0 | **0** |

A V2 Definitiva é mais próxima da revisão crítica que da proposta
original, com 5 pontos de divergência da revisão (D6: job types, D10:
lore_summary, D12: rejected status, D13: Video.knowledge_item_id, D14:
factual accuracy gate) onde a revisão foi conservadora demais e a
proposta original tinha razão em essência mas errada em escala.

---

**Fim do documento. Esta é a referência oficial para implementação
do GPCG V2.**
