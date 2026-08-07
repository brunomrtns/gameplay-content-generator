# Game Catalog Service — Plano de Implementação

## Visão Geral

Serviço dentro do monorepo do GPCG que mantém um catálogo interno de jogos
sincronizado com o IGDB (fonte da verdade). O GPCG consulta este serviço
para toda operação de identificação, seleção e atribuição de jogos.
Usuários nunca criam jogos — apenas selecionam do catálogo.

O catalog service é "burro": só sincroniza e serve dados do IGDB.
A inteligência (associação de gameplay a um jogo) fica no worker,
usando a LLM local (Ollama) que já roda durante o mapeamento.

## Arquitetura

```
┌─────────────────────────────────────────────────────────┐
│                 VPS (Control Plane)                      │
│        (monorepo GPCG — docker-compose.prod.yml)         │
│                                                          │
│  ┌──────────────┐  ┌───────────┐  ┌──────────────────┐  │
│  │   GPCG API   │  │  Worker   │  │  Catalog Service  │  │
│  │  (port 8787) │  │ (internal)│  │  (port 8788)      │  │
│  │              │  │           │  │                   │  │
│  │  - Upload    │  │ - Mapping │  │ - IGDB Sync daily │  │
│  │  - Jobs      │  │ - Assoc   │  │ - Catalog Query   │  │
│  │  - Frontend  │  │   (LLM)   │  │ - SQLite catalog  │  │
│  │  - Proxy     │  │           │  │ - Sem LLM         │  │
│  └──────┬───────┘  └─────┬─────┘  └────────┬──────────┘  │
│         │                │                  │             │
│         └────────────────┼──────────────────┘             │
│                          │ IGDB API (sync only)           │
└──────────────────────────┼───────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────┐
│  Local PC (Worker)              │
│  - GPU + Ollama (LLM local)     │
│  - Mapping                      │
│  - Associação de jogo:          │
│    só se usuário não setou jogo │
│    usa catálogo + LLM local     │
└─────────────────────────────────┘
```

## Monorepo — Estrutura

O catalog service vive dentro do repo do GPCG. Mesma imagem Docker,
mesmo docker-compose, mesmo deploy.sh, mesma versão.

```
gameplay-content-generator/
├── src/gpcg/
│   ├── api/                  # GPCG API (já existe)
│   ├── worker/               # GPCG worker (já existe)
│   ├── domain/               # Domain layer (já existe)
│   ├── application/          # Application services (já existe)
│   ├── infrastructure/       # Infra (já existe)
│   └── catalog/              # Catalog service (NOVO)
│       ├── __init__.py
│       ├── app.py            # FastAPI app (porta 8788)
│       ├── config.py         # Settings (env vars — IGDB_*)
│       ├── database.py       # SQLAlchemy engine + session (catalog.db)
│       ├── models.py         # catalog_games, catalog_aliases, sync_state
│       ├── igdb_client.py    # IGDB API client (auth + fetch)
│       ├── sync_service.py   # Sync logic (full + incremental)
│       ├── query_service.py  # Search/autocomplete/get logic
│       └── routes.py         # API endpoints
├── tests/
│   ├── ... (testes existentes)
│   ├── test_catalog_igdb.py  # IGDB client (mock)
│   ├── test_catalog_sync.py  # Sync logic
│   └── test_catalog_query.py # Query/search logic
├── frontend/                 # Frontend (já existe)
├── docker-compose.prod.yml   # api + worker + catalog (3 services)
├── scripts/deploy.sh         # deploy único (já existe, builda tudo)
├── Dockerfile                # imagem única (já existe)
├── pyproject.toml            # versão única (já existe)
└── docs/CATALOG_SERVICE_PLAN.md  # este arquivo
```

### Por que monorepo e não repo separado
- **Uma imagem Docker**: api, worker e catalog compartilham a mesma
  imagem `gpcg-api:latest`. Catalog é só um `command` diferente no
  docker-compose (igual o worker já é).
- **Um deploy.sh**: já faz `docker compose build` + `docker compose up -d`.
  Adicionar catalog como service = build e deploy automático.
- **Uma versão**: sem risco de version mismatch entre GPCG e catalog.
- **Uma tag de rollback**: cobre tudo.
- **Código compartilhado**: pode reusar `slug_utils`, infra, etc.
- **Testes juntos**: `pytest tests/` roda tudo de uma vez.

## Docker Compose

Adicionar `catalog` ao `docker-compose.prod.yml` existente:

```yaml
services:
  api:       # já existe
    # ... config atual ...
    depends_on:
      - catalog  # opcional: api pode precisar do catalog

  worker:    # já existe
    # ... config atual ...

  catalog:   # NOVO
    image: gpcg-api:latest       # mesma imagem!
    container_name: gpcg-catalog
    restart: unless-stopped
    env_file: .env
    environment:
      CATALOG_DB_PATH: /app/data/catalog.db
      IGDB_CLIENT_ID: ${IGDB_CLIENT_ID}
      IGDB_CLIENT_SECRET: ${IGDB_CLIENT_SECRET}
    command: ["python", "-m", "gpcg.catalog.app"]
    expose:
      - "8788"
    volumes:
      - gpcg-data:/app/data      # mesmo volume do GPCG
    networks:
      - trivestia-net
      - bi-net
    healthcheck:
      test: ["CMD", "python", "-c", "import httpx; httpx.get('http://127.0.0.1:8788/health').raise_for_status()"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 256M
```

**Mesma imagem, command diferente** — igual o worker já faz com
`command: ["gpcg", "worker"]`. O catalog roda com
`command: ["python", "-m", "gpcg.catalog.app"]`.

## Deploy

**Nada muda no deploy.sh.** O script já faz:
1. `docker compose -f docker-compose.prod.yml build` — builda a imagem
2. `docker compose -f docker-compose.prod.yml up -d` — sobe todos os
   services (api, worker, e agora catalog)
3. Docker compose só recreates containers que mudaram — se só mexeu no
   catalog, api e worker não reiniciam

O que precisa mudar no deploy.sh:
- **Nginx**: adicionar proxy `/gpcg/api/catalog/*` → `http://gpcg-catalog:8788`
  no bloco de nginx config (Step 4 do deploy.sh atual)
- **Smoke test**: adicionar check do catalog health além do API health

### Nginx (atualização do bloco GPCG no deploy.sh)

Adicionar dentro do location `/gpcg/`:

```nginx
# Catalog service proxy (interno — GPCG faz auth)
location ~ ^/gpcg/api/catalog/(.*)$ {
    limit_req zone=api_limit burst=30 nodelay;
    rewrite ^/gpcg/api/catalog/(.*)$ /api/$1 break;
    proxy_pass         http://gpcg_catalog;
    proxy_http_version 1.1;
    proxy_set_header   Host              $host;
    proxy_set_header   X-Real-IP         $remote_addr;
    proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto $scheme;
    proxy_set_header   Connection        "";
}
```

E adicionar upstream:
```nginx
upstream gpcg_catalog {
    server gpcg-catalog:8788;
    keepalive 16;
}
```

### Variáveis de Ambiente (.env)

Adicionar ao `.env` existente do GPCG:
```
# IGDB (Game Catalog Service)
IGDB_CLIENT_ID=...
IGDB_CLIENT_SECRET=...
CATALOG_SYNC_CRON=0 3 * * *
CATALOG_SYNC_FILTER_RATING_MIN=70
CATALOG_SYNC_FILTER_YEAR_MIN=2003
CATALOG_SYNC_BATCH_SIZE=500
```

## IGDB — Fonte da Verdade

### Autenticação
- Twitch Developer Portal: Client ID + Client Secret
- OAuth2: `POST https://id.twitch.tv/oauth2/token`
- Token expira em ~60 dias, renova automaticamente
- Headers: `Client-ID` + `Authorization: Bearer <token>`

### Filtro de Jogos Relevantes
Não sincronizar tudo (~250k jogos). Filtro:

```apicalypse
fields id,name,slug,summary,first_release_date,rating,rating_count,
      total_rating,total_rating_count,hypes,category,platforms,
      genres,themes,franchise,involved_companies,alternative_names,
      cover,screenshots,game_modes,player_perspectives,url;

where category = 0                              # main_game apenas (sem DLC/expansion)
  & (rating > 70 | total_rating_count > 20 | hypes > 5)
  & first_release_date > 1041379200;            # depois de 2003-01-01

sort total_rating_count desc;
limit 500;
```

Paginar com `offset` até esgotar. Estimativa: ~5.000-10.000 jogos.

### Sync Incremental (diário)
```apicalypse
fields id,name,slug,...;
where category = 0 & updated_at > <last_sync_timestamp>;
sort updated_at asc;
limit 500;
```

### Rate Limit
- 4 requests/segundo (respeitar com sleep de 250ms entre requests)
- 8 conexões concorrentes máx
- Token renovado automaticamente quando expira

### Endpoints IGDB Usados
- `POST /v4/games` — buscar/filtrar jogos
- `POST /v4/alternative_names` — aliases (join com game)
- `POST /v4/games/count` — total para paginação

## Data Model (Catalog Service)

DB separado (`catalog.db`) no mesmo volume do GPCG. Não mistura com
o `gpcg.db` principal — o catalog service é autossuficiente.

### Table: `catalog_games`
```sql
id              INTEGER PRIMARY KEY  -- IGDB game ID
name            TEXT NOT NULL        -- canonical name (IGDB)
slug            TEXT UNIQUE NOT NULL -- IGDB slug
summary         TEXT                 -- IGDB summary
storyline       TEXT                 -- IGDB storyline
first_release_date  INTEGER          -- unix timestamp
rating          REAL                 -- IGDB user rating (0-100)
rating_count    INTEGER
total_rating    REAL                 -- critics + users
total_rating_count INTEGER
hypes           INTEGER              -- pre-release hype
category        INTEGER              -- 0=main_game, 1=dlc, etc
cover_url       TEXT                 -- IGDB image URL
screenshots     JSON                 -- list of URLs
genres          JSON                 -- ["Action", "Shooter"]
themes          JSON                 -- ["War", "Sci-fi"]
game_modes      JSON                 -- ["Single player", "Multiplayer"]
player_perspectives JSON             -- ["Third person"]
platforms       JSON                 -- ["PC", "PS5", "Xbox Series"]
franchise       TEXT
developer       TEXT                 -- resolved from involved_companies
publisher       TEXT
igdb_url        TEXT
synced_at       INTEGER              -- last sync timestamp (unix)
created_at      INTEGER
updated_at      INTEGER
```

### Table: `catalog_aliases`
```sql
id          INTEGER PRIMARY KEY
game_id     INTEGER NOT NULL REFERENCES catalog_games(id) ON DELETE CASCADE
alias       TEXT NOT NULL
alias_type  TEXT DEFAULT 'alternative'
source      TEXT DEFAULT 'igdb'
created_at  INTEGER
UNIQUE(game_id, alias)
INDEX ON LOWER(alias)
```

### Table: `sync_state`
```sql
id              INTEGER PRIMARY KEY  -- singleton, always 1
last_full_sync  INTEGER
last_incremental_sync INTEGER
last_igdb_updated_at INTEGER
total_games     INTEGER
sync_in_progress BOOLEAN DEFAULT FALSE
```

## API do Catalog Service

### Query (usado pelo GPCG e frontend)

```
GET  /api/search?q=<query>&limit=20
     → fuzzy match em name + aliases
     → retorna [{id, name, slug, cover_url, rating, year}]

GET  /api/games/<id>
     → dados completos de um jogo

GET  /api/games/slug/<slug>
     → busca por slug

GET  /api/games/popular?limit=50&offset=0
     → top jogos por total_rating_count

GET  /api/games/recent?limit=50
     → lançamentos recentes

GET  /api/autocomplete?q=<partial>
     → autocomplete rápido (name prefix match)
```

### Admin

```
POST /admin/sync              # trigger sync manual
GET  /admin/sync/status       # status do sync
GET  /admin/stats             # total jogos, aliases, last sync
```

### Health

```
GET  /health                  # liveness probe
```

## Associação de Gameplay (no worker, pós-mapeamento)

### Condição
Só executa se `game_id is None` — ou seja, o usuário não setou um jogo
antes ou durante o upload. Se já tem jogo associado, pula.

### Fluxo
```
Worker completa mapeamento
    → verifica: source.game_id is None?
        → sim: continua com associação
        → não: pula, jogo já definido
    → coleta dados do mapeamento:
        - eventos (descrições, tags, personagens, locais, ações)
        - transcript de áudio
        - filename original (hint fraco)
    → consulta catalog service: GET /api/search com termos extraídos
    → se encontra candidatos:
        → LLM local (Ollama, mesmo modelo do mapeamento) compara:
            - dados do mapeamento vs summary/genres/themes do catálogo
            - retorna {game_id, confidence, reasoning}
        → confiança > 0.7: atribui automaticamente
        → confiança 0.4-0.7: marca needs_review
        → confiança < 0.4: marca "não identificado"
    → se não encontra candidatos:
        → marca "não identificado" (needs_review)
```

### Por que LLM local e não no catalog service
- O worker já tem Ollama rodando (usado no mapeamento)
- A LLM tem acesso aos dados ricos do mapeamento (eventos, transcript)
- Não precisa de API externa — comparação é local (catálogo vs mapeamento)
- O catalog service fica simples: só serve dados, não pensa

### Por que só se game_id is None
- Se o usuário setou o jogo, respeitamos a escolha
- Se o L1 (filename) acertou, não desperdiçamos LLM
- Só recorremos à LLM quando realmente não sabemos o jogo

## Upload com seleção opcional de jogo

### Fluxo atual
```
upload → VPS cria source (sem game_id) → mapping job → worker
```

### Fluxo novo
```
upload → frontend mostra busca de jogos (autocomplete do catalog)
    → usuário pode selecionar um jogo (opcional)
    → se selecionou: source.game_id = <escolhido>
    → se não selecionou: source.game_id = None
→ mapping job → worker
    → mapeamento
    → se game_id is None: associação via LLM local
    → se game_id já setado: pula associação
```

### Correção pelo usuário
- Após mapeamento, se o sistema identificou errado (ou não identificou),
  o usuário pode corrigir via frontend
- Correção = reatribuir source.game_id para outro jogo do catálogo
- Usuário **sempre** escolhe do catálogo, nunca cria jogo novo

## Integração GPCG → Catalog Service

### Modificações no GPCG

1. **Game resolver** (`game_resolver.py`)
   - L1/L2 agora consultam catalog service via HTTP (não DB local)
   - L3 (VLM) continua igual (frames → Ollama → nome → catalog service valida)

2. **Game table local** (`models.py`)
   - Continua existindo (FK constraints)
   - Virou cache: populado sob demanda quando catalog service retorna um jogo
   - `external_ids` guarda `{"catalog": <id>}`

3. **Frontend** (`api.ts`, `content.tsx`)
   - Upload form: autocomplete de jogos do catalog (opcional)
   - Game selector: busca no catalog service com cover art, rating, ano
   - Correção: trocar jogo associado (busca no catalog)

4. **Worker** (`remote_worker.py`)
   - Após mapeamento, se `game_id is None`:
     - Consulta catalog service com termos do mapeamento
     - LLM local compara e atribui (ou marca needs_review)
   - Se `game_id` já setado: pula

5. **GPCG API proxy** (nova rota em `api/`)
   - `GET /api/catalog/search?q=...` → proxy para `http://gpcg-catalog:8788/api/search`
   - `GET /api/catalog/games/<id>` → proxy para catalog service
   - `GET /api/catalog/autocomplete?q=...` → proxy para catalog service
   - Mantém auth do GPCG (não expõe catalog service diretamente)

### Comunicação
- Catalog service na mesma Docker network do GPCG (trivestia-net)
- GPCG API acessa via `http://gpcg-catalog:8788` (Docker DNS)
- Frontend acessa via GPCG proxy (`/gpcg/api/catalog/...`)
- Worker acessa via VPS URL (`https://brunointegrations.com/gpcg/api/catalog/...`)

## Cronograma de Implementação

### Fase 1 — Catalog Service (dentro do monorepo)
1. `src/gpcg/catalog/` — estrutura de módulos
2. Data model: catalog_games, catalog_aliases, sync_state (SQLAlchemy)
3. IGDB client: auth (OAuth2), fetch games, fetch alternative_names
4. Sync service: full sync com filtro de popularidade + paginação
5. Query API: search, get, autocomplete, popular, recent
6. Health endpoint
7. Adicionar `catalog` ao docker-compose.prod.yml
8. Adicionar proxy nginx no deploy.sh
9. Testes básicos (IGDB client mock, sync, query)

### Fase 2 — Deploy e Sync Inicial
10. Criar Twitch Developer account + app (Client ID + Secret)
11. Adicionar IGDB_CLIENT_ID/SECRET ao .env da VPS
12. Deploy (./scripts/deploy.sh — builda e sobe tudo)
13. Primeiro sync full do IGDB
14. Sync incremental diário (updated_at > last_sync)
15. Cron com jitter anti-bot (horário aleatório ±2h)
16. Admin endpoints: trigger sync manual, status, stats

### Fase 3 — Integração com GPCG
17. GPCG API proxy: /api/catalog/search, /api/catalog/games/<id>
18. GPCG Game table: cache pattern (create-on-demand from catalog)
19. GPCG game_resolver: consulta catalog service em L1/L2
20. Frontend: autocomplete de jogos no upload (opcional)
21. Frontend: game selector com cover art, rating, ano

### Fase 4 — Associação no Worker
22. Worker: após mapeamento, se game_id=None, consulta catalog
23. Worker: LLM local compara dados do mapeamento vs catálogo
24. Worker: atribui automaticamente ou marca needs_review
25. Frontend: UI de correção (reatribuir jogo do catálogo)

### Fase 5 — Migração
26. Migrar jogos existentes do GPCG para catalog (match por slug/name)
27. GPCG: remover game creation do upload flow (só catalog cria)
28. GPCG: remover IngestionService._ingest_file resolution (deprecated)
29. Testes end-to-end: upload → mapping → association → catalog lookup

## Decisões Pendentes

1. **Catálogo inicial**: full sync de ~5-10k jogos demora quanto?
   - 500 jogos/request × 4 req/s = 2000 jogos/s
   - 10k jogos = ~5 segundos de API + tempo de processamento
   - Sem LLM no sync, é rápido (só insert no SQLite)

2. **GPCG Game table**: manter como cache ou eliminar?
   - Recomendação: manter como cache (FK constraints, performance)

3. **Cover art**: baixar imagens do IGDB ou só guardar URLs?
   - Recomendação: só URLs (IGDB serve via CDN, sem custo de storage)

4. **Wikipedia enrichment**: continuar no GPCG?
   - Sim, manter no GPCG (enriquecimento é responsabilidade do GPCG,
     catalog só tem dados canônicos do IGDB)

5. **IGDB account**: precisa criar Twitch Developer account + app
   - Client ID + Client Secret
   - 2FA obrigatório no Twitch

6. **Repo separado**: o repo `gpcg-game-catalog-service` no GitHub pode
   ser arquivado/deletado — o catalog service vai no monorepo do GPCG.
