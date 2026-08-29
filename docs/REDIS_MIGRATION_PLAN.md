# Redis Migration Plan — GPCG

> Status: **ativo** (rev 2 — pós-auditoria)
> Criado em: 2026-08-28
> Revisado em: 2026-08-28 (21 correções de auditoria incorporadas)
> Objetivo: substituir polling por eventos (SSE + Redis Pub/Sub), migrar job queue para Redis Streams, adicionar cache Redis.

## Contexto atual

### Polling hoje

**Web (12 pontos: 10 usePoll + 2 setInterval manuais):**
- 3s: kids-ideas (`/api/jobs/{id}`) — setInterval manual
- 5s: content (`/api/sources`), jobs (`/api/jobs`), kids (`/api/kids/assets`), worker-status (`/api/workers`)
- 10s: dashboard (`/api/dashboard`), videos (`/api/videos`), admin (`/api/auth/users`), ideas (`/api/automation/current-job`) — setInterval, video-customization (`/api/voices`)
- 15s: automation (`/api/games`, `/api/voices`)
- 30s: automation (`/api/automation`, `/api/dashboard`)

**Mobile (15 pontos: 14 refetchInterval + 1 setTimeout manual):**
- 5s: Content, Jobs, KidsIdeas (manual)
- 10s: Dashboard (x2), Videos, Kids, Ideas
- 15s: Automation (x2), KidsIdeas (x2)
- 30s: Automation (x2)

### Backend hoje
- Job queue: SQLite com UPDATE condicional (`WHERE id=:jid AND status='queued'`), sem SELECT FOR UPDATE
- Worker: poll a cada 5s via HTTP, heartbeat a cada 10s via HTTP
- Stale recovery: query SQL a cada claim (LEFT JOIN jobs + workers, filtro em Python) — roda a cada 5s
- 19 pontos de criação de Job no código (2 sem user_id: game_enrich)
- Zero notificação push — sem SSE, WebSocket, pub/sub
- Auth: BI Identity (cookie web, SameSite=Lax, httpOnly) + JWT Bearer (mobile, 30 dias), validado via HTTP a cada request
- Cache: só em escopo de request e game_enrichment (metadata_json TTL 30 dias)
- Dashboard: computado on-demand (count() + limit 5 em SQLite, provavelmente <100ms)
- Worker modelo: global, sem user_id — `/api/workers` é público sem filtro de usuário

### Infra hoje
- Docker: gpcg-api + gpcg-catalog em trivestia-net + bi-net
- Sem Redis no stack (google-integration-redis isolado em outra rede, não compartilhar)
- Nginx: proxy_buffering off no catch-all, proxy_read_timeout 1200s, **send_timeout 10s global** (mata SSE — obrigatório sobrescrever na location SSE)
- HTTP/2: **não habilitado** (listen 80 apenas, bloco 443 comentado) — limite de 6 conexões SSE por domínio no browser
- Uvicorn: 1 worker (sem --workers) — pub/sub in-memory funcionaria hoje, mas Redis é necessário porque worker remoto publica de fora do container
- Sem redis-py no pyproject.toml

### Bugs confirmados durante auditoria (corrigir na Fase 2)
1. **`src/gpcg/api/workers/panel.py:155`** — usa `job_type=` em vez de `type=` → `TypeError` em runtime. NÃO é dead code (`worker_panel.py:448` chama). Zero testes. **Corrigir**: `type=JobType.content_collect.value` + `required_capabilities=["content_intelligence"]`
2. **`src/gpcg/application/domain_reset_service.py:376`** — `cleanup_gameplay` sem `required_capabilities` (inconsistente com `routes.py:562` que seta `["mapping"]`). Worker sem mapping pode claimar. **Corrigir**: adicionar `required_capabilities=["mapping"]`
3. **Job types sem required_capabilities** — `generate_short`, `curiosity_short`, `kids_idea_discovery`, `kids_idea_score`, `kids_asset_process` — qualquer worker pode claimar. **Corrigir**: definir capabilities em todos

### Gaps confirmados durante auditoria
- **game_enrich sem user_id** — jobs criados em `game_registry.py:242` e `game_registry_routes.py:208` sem `user_id`. Canal `user:{None}:events` não entrega pra ninguém. `Game.user_id` existe e deve ser usado.
- **Testes que vão quebrar** — `tests/test_multi_worker_e2e.py` (3 métodos), `tests/test_job_requeue.py` (1 método) testam `/api/jobs/claim`. Precisa de `fakeredis` nas dev deps.
- **Catalog service** — não precisa de Redis (isolado, sync via thread própria + SQLite). Fica de fora do plano.

---

## Fase 1 — Infra Redis

### 1.1 Docker
- Adicionar serviço `redis` no `docker-compose.prod.yml`:
  - Imagem: `redis:7-alpine`
  - Rede: `trivestia-net`
  - Persistência: AOF (`appendonly yes`, `appendfsync everysec`)
  - Volume: `gpcg-redis-data:/data`
  - Healthcheck: `redis-cli ping`
  - Sem port expostas (interno apenas)
  - Memory limit: 256M
  - **maxmemory-policy: `allkeys-lru`** (sem isso, Redis default `noeviction` rejeita writes quando enche)
  - **Redis dedicado** — não compartilhar com google-integration-redis (128M, allkeys-lru, risco de evicção cruzada e colisão de chaves)
- **depends_on** em api e catalog:
  ```yaml
  api:
    depends_on:
      redis:
        condition: service_healthy
  ```

### 1.2 Dependências
- `pyproject.toml`: adicionar `redis[hiredis]>=5.0` (deps) + `fakeredis>=2.0` (dev deps)
- `mobile/package.json`: adicionar `react-native-sse` (ver nota abaixo)
- `frontend/package.json`: adicionar `@tanstack/react-query`

> **Nota sobre react-native-sse**: última release mar/2024 (1+ ano sem update), issues abertas sem resposta, não testado com RN 0.87 New Architecture. **Plano B**: `react-native-nitro-sse` (baseada em Nitro Modules/JSI, compatível Fabric) ou `rn-eventsource-reborn`. Testar em branch antes de commitar.

### 1.3 Config
- `src/gpcg/config.py`: adicionar `redis_url: str = "redis://redis:6379/0"`
- `.env` VPS: adicionar `REDIS_URL=redis://redis:6379/0`

### 1.4 Redis adapter
- `src/gpcg/infrastructure/redis_adapter.py`:
  - Singleton `RedisAdapter` com:
    - `publish(channel, event_type, payload)` — pub/sub
    - `subscribe(channels) -> generator` — pub/sub consumer
    - `xadd(stream, fields, maxlen=10000)` — stream producer com MAXLEN obrigatório
    - `xreadgroup(group, consumer, streams, block_ms=5000)` — stream consumer
    - `xautoclaim(stream, group, consumer, min_idle_ms)` — stale recovery
    - `xack(stream, group, ids)` — ack
    - `set(key, value, ttl)` / `get(key)` / `delete(key)` — cache
    - `ping()` — health
  - Conexão lazy (só conecta quando usado)
  - **Retry com backoff**: tentar 5x, 1s entre tentativas, antes de decidir que Redis está down
  - Fallback gracioso: se Redis cair, operações pub/sub e cache viram no-op, job queue cai pra SQLite polling
  - **Stream MAXLEN**: todo `XADD` usa `MAXLEN 10000` para evitar crescimento indefinido

---

## Fase 2 — Eventos backend (pub/sub)

### 2.1 Canais
- `user:{user_id}:events` — eventos per-user (jobs, gameplays, vídeos)
- `global:workers` — eventos globais de worker status
- `global:games` — eventos globais de game enrichment (novo — resolve gap do game_enrich sem user_id)

### 2.2 Tipos de evento
- `job.status_changed` — {job_id, status, stage, progress, type, user_id}
- `job.created` — {job_id, type, priority, user_id}
- `gameplay.status_changed` — {source_id, processing_status, filename, user_id}
- `video.created` — {video_id, title, status, user_id}
- `video.updated` — {video_id, status, youtube_url, user_id}
- `worker.status_changed` — {worker_id, status, activity, gpu_usage, cpu_usage}
- `automation.status_changed` — {automation_id, status, user_id}
- `idea_queue.updated` — {user_id, queue_size}
- `kids_idea.updated` — {idea_id, status, user_id}
- `game.enriched` — {game_id, canonical_name} (novo — canal `global:games`)

### 2.3 Pontos de publicação

| Arquivo | Função/endpoint | Canal | Evento | user_id source |
|---|---|---|---|---|
| `src/gpcg/api/workers/jobs.py:250` | `update_job_status` | `user:{job.user_id}:events` | `job.status_changed` | `job.user_id` |
| `src/gpcg/api/workers/jobs.py:346` | `submit_job_result` | `user:{job.user_id}:events` | `job.status_changed` + `video.created/updated` | `job.user_id` |
| `src/gpcg/api/workers/jobs.py:544` | `_maybe_auto_publish` | `user:{job.user_id}:events` | `video.updated` | `job.user_id` |
| `src/gpcg/api/workers/mapping.py:107,254` | mapping result | `user:{source.user_id}:events` | `gameplay.status_changed` | `source.user_id` |
| `src/gpcg/api/workers/file_transfer.py:274` | download confirm | `user:{source.user_id}:events` | `gameplay.status_changed` | `source.user_id` |
| `src/gpcg/api/upload_routes.py:321,344` | upload complete | `user:{user.id}:events` | `gameplay.status_changed` + `job.created` | `user.id` |
| `src/gpcg/application/generation_service.py:1647` | `_set_stage` | `user:{job.user_id}:events` | `job.status_changed` | `job.user_id` |
| `src/gpcg/domains/kids/pipeline.py:631` | `_set_stage` | `user:{job.user_id}:events` | `job.status_changed` | `job.user_id` |
| `src/gpcg/api/workers/registry.py:90,117` | heartbeat/status | `global:workers` | `worker.status_changed` | global |
| `src/gpcg/api/routes.py:1479+` | video publish | `user:{user.id}:events` | `video.updated` | `user.id` |
| `src/gpcg/application/qa_service.py:212` | QA result | `user:{video.user_id}:events` | `video.updated` | `video.user_id` |
| `src/gpcg/api/automation_routes.py:313` | automation check | `user:{user.id}:events` | `automation.status_changed` | `user.id` |
| `src/gpcg/api/knowledge_item_routes.py:231` | content collect | `user:{user.id}:events` | `job.created` | `user.id` |
| `src/gpcg/api/kids_idea_routes.py:270,400,533` | kids idea jobs | `user:{user.id}:events` | `job.created` + `kids_idea.updated` | `user.id` |
| `src/gpcg/api/kids_routes.py:362,762` | kids asset process | `user:{user.id}:events` | `gameplay.status_changed` | `user.id` |
| `src/gpcg/domain/game_registry.py:242` | game enrich (auto) | `global:games` | `game.enriched` | global (job sem user_id) |
| `src/gpcg/api/game_registry_routes.py:208` | game enrich (manual) | `global:games` | `game.enriched` | global |

> **Correção do gap game_enrich**: jobs de game_enrich não têm user_id. Em vez de publicar em `user:{None}:events` (que não entrega pra ninguém), publicar em `global:games`. Todos os usuários conectados recebem `game.enriched` e invalidam `['games']`. Adicionalmente, adicionar `user_id=game.user_id` na criação do job para auditoria.

### 2.4 Bugs a corrigir antes da publicação de eventos
- `src/gpcg/api/workers/panel.py:155`: `job_type=` → `type=JobType.content_collect.value`, adicionar `required_capabilities=["content_intelligence"]`
- `src/gpcg/application/domain_reset_service.py:376`: adicionar `required_capabilities=["mapping"]`
- `src/gpcg/domain/game_registry.py:242`: adicionar `user_id=game.user_id` (para auditoria, evento vai pra `global:games`)
- `src/gpcg/api/game_registry_routes.py:208`: adicionar `user_id=game.user_id`
- Definir `required_capabilities` em todos os job types:
  - `generate_short` → `["generation"]`
  - `curiosity_short` → `["generation"]`
  - `kids_idea_discovery` → `["content_intelligence"]`
  - `kids_idea_score` → `["content_intelligence"]`
  - `kids_asset_process` → `["mapping"]`
  - `cleanup_user_storage` → `[]` (qualquer worker)
  - `cleanup_gameplay` → `["mapping"]`

### 2.5 Isolamento de canais
- Redis Pub/Sub garante isolamento por canal — só subscribers do canal recebem
- Worker deve usar `job.user_id` do job atual (não cache entre jobs) para publicar no canal correto
- Worker status é global (todos recebem)
- Game enrichment é global (todos recebem)

---

## Fase 3 — SSE endpoint + nginx

### 3.1 Endpoint
- `GET /api/events/stream` em `src/gpcg/api/events_routes.py`
- Autenticação: `get_current_user` (funciona com cookie web e Bearer mobile)
- Subscreve `user:{user.id}:events` + `global:workers` + `global:games` no Redis
- Headers de resposta:
  - `Content-Type: text/event-stream`
  - `Cache-Control: no-cache`
  - `Connection: keep-alive`
  - `X-Accel-Buffering: no`
- Keepalive: `:\n\n` a cada 5s (proteção extra contra timeouts)
- Formato SSE: `data: {"type":"job.status_changed","payload":{...}}\n\n`
- Reconexão: cliente EventSource reconecta nativamente
- **Sem Last-Event-ID**: Redis Pub/Sub não persiste mensagens. Eventos perdidos durante desconexão são perdidos. Cliente usa `staleTime: 30s` como fallback para garantir que dados não ficam desatualizados. **Deduplicação no cliente**: ignorar eventos com `event_id` já visto (evita refetch duplicado na reconexão).

> **Correção de falso positivo**: o plano anterior prometia `Last-Event-ID` para retomar eventos. Isso é impossível com Pub/Sub (não persiste). Ou usamos Redis Streams para eventos (com XRANGE) ou removemos a promessa. Escolhemos remover — deduplicação no cliente + staleTime 30s é suficiente.

### 3.2 Nginx
- `deploy.sh`: adicionar location específica antes do catch-all:
  ```nginx
  location = /gpcg/api/events/stream {
      limit_req zone=api_limit burst=50 nodelay;
      rewrite ^/gpcg/(.*)$ /$1 break;
      proxy_pass http://gpcg_api;
      proxy_http_version 1.1;
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
      proxy_set_header Connection "";
      proxy_buffering off;
      proxy_cache off;
      proxy_read_timeout 3600s;
      proxy_send_timeout 3600s;
      send_timeout 0;          # OBRIGATÓRIO — global é 10s, mata SSE
      proxy_connect_timeout 60s;
      add_header X-Accel-Buffering no always;
  }
  ```

> **Correção**: `send_timeout 0` é obrigatório, não opcional. O global de 10s derruba SSE mesmo com keepalive de 5s se o cliente ficar lento. Keepalive é proteção extra, não substituto.

### 3.3 Rsync excludes
- `deploy.sh` linha 282: adicionar:
  ```
  --exclude=mobile/android/build --exclude=mobile/android/.gradle --exclude=mobile/android/app/build
  --exclude=mobile/.expo --exclude=mobile/ios/build --exclude=mobile/ios/Pods
  --exclude=frontend/dist
  ```

### 3.4 Limite de conexões HTTP/1.1
- Nginx hoje só escuta na porta 80 (HTTP/1.1) — bloco 443/http2 comentado
- Browser limita 6 conexões TCP por domínio em HTTP/1.1
- 3 abas do dashboard = 3 conexões SSE — OK
- 6+ abas pode esgotar conexões para outras requests (CSS, JS, API)
- **Futuro**: habilitar `listen 443 ssl http2` para multiplexação (remove limite)

---

## Fase 4 — Web: React Query + SSE

### 4.1 Setup React Query
- `frontend/package.json`: adicionar `@tanstack/react-query`
- `frontend/src/main.tsx` (ou entry point): envolver App com `QueryClientProvider`
- QueryClient config: `staleTime: 30s`, `retry: 1`, `refetchOnWindowFocus: false`

### 4.2 SSE hook
- `frontend/src/hooks/useEvents.ts`:
  - `EventSource` com `withCredentials: true` (envia cookie bi_auth SameSite=Lax, mesmo site brunointegrations.com)
  - Conecta em `/gpcg/api/events/stream` (prod) ou `/api/events/stream` (dev)
  - Context provider que distribui eventos via `useContext`
  - Reconexão automática (EventSource nativo)
  - Estado de conexão (connected/disconnected)
  - **Deduplicação**: Set de event_ids já vistos (últimos 100), ignorar duplicados

### 4.3 Invalidação por evento
- `frontend/src/hooks/useLiveData.ts`:
  - `useLiveData(queryKey, queryFn, eventTypes)` — wrap de `useQuery`
  - Quando evento relevante chega, chama `queryClient.invalidateQueries({queryKey})`
  - `staleTime: 30s` como fallback (se evento não chega, refetch em 30s)
  - Sem `refetchInterval` (eventos substituem polling)
  - **Atenção**: `invalidateQueries` não é idempotente — chamar 2x pode disparar 2 refetches. Deduplicação no useEvents previne isso.

### 4.4 Migração das 12 páginas/componentes
| Página/Componente | Query keys | Eventos que invalidam |
|---|---|---|
| `dashboard.tsx` | `['dashboard']` | `job.status_changed`, `video.created`, `automation.status_changed` |
| `content.tsx` | `['sources']` | `gameplay.status_changed`, `job.created` |
| `jobs.tsx` | `['jobs']` | `job.status_changed`, `job.created` |
| `videos.tsx` | `['videos']` | `video.created`, `video.updated` |
| `automation.tsx` | `['automation']`, `['games']`, `['voices']`, `['dashboard']` | `automation.status_changed`, `job.status_changed`, `game.enriched` |
| `ideas.tsx` | `['current-job']`, `['idea-queue']` | `job.status_changed`, `idea_queue.updated` |
| `kids.tsx` | `['kids-assets']` | `gameplay.status_changed`, `job.status_changed` |
| `kids-ideas.tsx` | `['jobs']` (por ID) | `job.status_changed`, `kids_idea.updated` |
| `admin.tsx` | `['users']` | (sem evento específico, staleTime 30s) |
| `worker-status.tsx` | `['workers']` | `worker.status_changed` |
| `video-customization.tsx` | `['voices']` | (sem evento, staleTime 60s) |

---

## Fase 5 — Mobile: SSE + invalidação

### 5.1 Setup
- `mobile/package.json`: adicionar `react-native-sse` (testar em branch primeiro — ver nota na Fase 1.2)
- `mobile/src/hooks/useEvents.ts`:
  - `EventSource` de `react-native-sse` com `headers: { Authorization: Bearer ${token} }`
  - Mesma lógica do web: context provider + distribuição via useContext
  - Reconexão automática
  - Token lido do AsyncStorage
  - **Deduplicação**: mesma lógica do web (Set de event_ids)

### 5.2 Migração das 15 queries
- Substituir `refetchInterval` por invalidação via evento
- Manter `staleTime: 30s` como fallback
- Substituir `setTimeout` manual em `KidsIdeasScreen.tsx:136-157` por evento `job.status_changed`

| Screen | Query keys | Eventos |
|---|---|---|
| `DashboardScreen` | `['dashboard']`, `['workers']` | `job.status_changed`, `video.created`, `worker.status_changed` |
| `ContentScreen` | `['gameplays']` | `gameplay.status_changed`, `job.created` |
| `JobsScreen` | `['jobs']` | `job.status_changed`, `job.created` |
| `AutomationScreen` | `['automation']`, `['games']`, `['voices']`, `['dashboard']` | `automation.status_changed`, `game.enriched` |
| `VideosScreen` | `['videos']` | `video.created`, `video.updated` |
| `IdeasScreen` | `['idea-queue']`, `['current-job']` | `job.status_changed`, `idea_queue.updated` |
| `KidsScreen` | `['kids-assets']` | `gameplay.status_changed` |
| `KidsIdeasScreen` | `['kids-ideas']`, `['kids-idea-queue']`, `['jobs']` | `kids_idea.updated`, `job.status_changed` |

---

## Fase 6 — Job queue (Redis Streams)

### 6.1 Streams por (prioridade, capability-set)

**15 streams** (3 prioridades × 5 capability-sets):
```
jobs:high:_                       jobs:normal:_                       jobs:low:_
jobs:high:mapping                 jobs:normal:mapping                 jobs:low:mapping
jobs:high:generation              jobs:normal:generation              jobs:low:generation
jobs:high:content_intelligence    jobs:normal:content_intelligence    jobs:low:content_intelligence
jobs:high:enrichment              jobs:normal:enrichment              jobs:low:enrichment
```

> **Correção de contagem**: o plano anterior listava 9 streams, faltavam 6 (high/low de _, content_intelligence, enrichment).

> **Jobs multi-capability**: hoje não existem jobs com múltiplas required_capabilities. Se surgirem, publicar em **todas as streams de subsets** (ex: `["generation","mapping"]` → publica em `jobs:normal:generation` E `jobs:normal:mapping`). Deduplicar no claim SQL (UPDATE condicional já previne duplo-claim). Alternativa: stream única + filtro no claim-by-id.

### 6.2 Consumer groups
- Um consumer group por stream: `gpcg-workers`
- Cada worker é um consumer único dentro do grupo
- **`XREADGROUP GROUP gpcg-workers {worker_id} BLOCK 5000 STREAMS {streams...}`**

> **Correção crítica**: `BLOCK 0` trava o worker indefinidamente — nunca retorna para `_check_automations()` que roda a cada 5s. Usar `BLOCK 5000` (5s) para permitir o loop normal: `_check_automations()` → `XREADGROUP BLOCK 5000` → processa ou continua.

### 6.3 Job creation (outbox pattern)
- Em cada um dos 19 pontos de criação:
  1. `INSERT` no SQLite (status='queued') — como hoje
  2. `XADD ... MAXLEN 10000` na stream correspondente com `{job_id, type, user_id, priority, required_capabilities}`
- Se `XADD` falhar (Redis caiu), job fica no SQLite como `queued` e o reconciliador re-hidrata depois
- Helper `enqueue_job(job)` no redis_adapter para centralizar

### 6.4 Worker claim
- `RemoteWorker.claim_job()` muda de `POST /api/jobs/claim` para:
  1. `XREADGROUP BLOCK 5000` nas streams compatíveis (high → normal → low)
  2. Se mensagem chegou, faz `POST /api/jobs/claim-by-id` com `{job_id, worker_id}`
  3. API faz UPDATE condicional no SQLite (status queued → running, seta worker_id)
  4. Se claim SQL falhar (outro worker claimou), `XACK` e ignora
  5. Se claim SQL succeed, processa job
  6. **`XACK` só após `submit_job_result` retornar sucesso** (garante que mensagem não é acked antes do resultado ser gravado)
- Heartbeat: **mantém HTTP heartbeat como primário** (SQLite precisa de last_heartbeat para dashboard)
  - Redis `SET worker:{id}:heartbeat EX 30` é opcional, só para pub/sub de status
  - HTTP heartbeat continua gravando no SQLite

> **Correção**: o plano anterior propunha heartbeat via Redis como primário. Mas o dashboard lê `last_heartbeat` do SQLite via `GET /api/workers`. Se heartbeat só for pro Redis, SQLite fica desatualizado. Solução: manter HTTP como primário, Redis só para pub/sub.

### 6.5 Stale recovery
- **`XAUTOCLAIM`** com min_idle = `gpcg_job_lease_timeout` (300s)
  - XAUTOCLAIM **transfere ownership** da mensagem para outro consumer (não "devolve pra stream" como dito antes)
  - Disponível em Redis 6.2+ (redis:7-alpine tem)
- **Reconciliador a cada 10s** (não 60s — 60s é muito lento para jobs interativos):
  - `XPENDING` em cada stream → mensagens stale
  - `XAUTOCLAIM` → re-atribui a outro consumer
  - `SELECT * FROM jobs WHERE status='running' AND updated_at < cutoff` → marca failed/queued no SQLite
  - Re-hidrata: `SELECT * FROM jobs WHERE status='queued'` → `XADD` se não estiver no stream
- **XAUTOCLAIM também no início de cada XREADGROUP** (como hoje `_requeue_stale_jobs_in_claim` roda em todo claim)

> **Correções**: (1) semântica do XAUTOCLAIM corrigida — transfere ownership, não devolve pra stream. (2) Reconciliador a cada 10s, não 60s. (3) XAUTOCLAIM no início de cada claim, não só no reconciliador.

### 6.6 Fallback SQLite
- Se Redis cair (`ping()` falha após 5 retries):
  - Worker cai pra polling SQL antigo (`POST /api/jobs/claim`)
  - Pub/sub vira no-op (frontend volta pra staleTime 30s)
  - Cache vira no-op (computa on-demand)
- Quando Redis volta, reconciliador re-hidrata streams
- **Idempotência**: `submit_job_result` e `update_job_status` devem ignorar updates para jobs já `completed` (previne duplicação se XAUTOCLAIM reprocessar)
- **Dois code paths**: Redis (XREADGROUP) e SQLite (POST /claim) — complexidade aceitável dado o fallback gracioso

---

## Fase 7 — Cache

### 7.1 Dashboard
- `GET /api/dashboard` — cachear resultado no Redis por user_id (TTL 10s)
- Key: `cache:dashboard:{user_id}`
- Invalidar por evento: `job.status_changed`, `video.created`, `automation.status_changed`
- **Medir latência real antes de ativar** — se query for <100ms (provável em SQLite com count+limit), cache é over-engineering. Invalidação por evento anula o TTL na prática.

### 7.2 BI Identity
- `_validate_bi_user` — cachear resultado por user_id (**TTL 10s**, não 60s)
- Key: `cache:auth:{user_id}`
- **Risco**: logout/ban no BI Identity não é instantâneo no GPCG. Janela de 10s é aceitável, 60s não é.
- Sem webhook de invalidação do BI Identity — TTL curto é a única proteção
- Reduz calls HTTP ao BI Identity

> **Correção**: TTL reduzido de 60s para 10s. 60s de janela de revogação é risco de segurança.

### 7.3 Games e voices
- `GET /api/games` — cachear (TTL 60s), key: `cache:games`
- `GET /api/voices` — cachear (TTL 60s), key: `cache:voices:{user_id}`
- Invalidar por evento: `game.enriched` (global), upload de voice

### 7.4 Automation config
- `GET /api/automation` — cachear (TTL 30s), key: `cache:automation:{user_id}`
- Invalidar por evento: `automation.status_changed`

---

## Fase 8 — Deploy e validação

### 8.1 Deploy
- `.env` VPS: `REDIS_URL=redis://redis:6379/0`
- `docker-compose.prod.yml`: serviço redis + healthcheck + depends_on
- `deploy.sh`: bloco nginx SSE + rsync excludes + health check Redis
- Ordem: Redis sobe primeiro (depends_on: service_healthy), api e catalog depois

### 8.2 Testes
- `pyproject.toml`: adicionar `fakeredis>=2.0` em dev deps
- `tests/conftest.py`: adicionar fixture de Redis (fakeredis)
- **Testes que vão quebrar e precisam adaptação**:
  - `tests/test_multi_worker_e2e.py` — 3 métodos testam `/api/jobs/claim` (test_different_workers_claim_different_jobs, test_no_job_when_queue_empty, test_offline_worker_job_reclaimed_by_other)
  - `tests/test_job_requeue.py` — 1 método testa claim + stale recovery (test_claim_recovers_stale_then_claims)
- `frontend && npm run typecheck`
- `mobile && npm run typecheck`
- Teste manual SSE: curl com header de auth, verificar eventos chegam
- Teste mobile: adb logcat monitorando conexão SSE
- Teste de fallback: matar Redis e verificar se sistema volta pra polling

### 8.3 Rollback
- Redis é aditivo: se cair, sistema volta pra polling (staleTime) e SQLite job queue
- Para desativar Redis completamente: remover `REDIS_URL` do .env → adapter vira no-op

### 8.4 Catalog service
- **Fica de fora do plano** — não precisa de Redis (isolado, sync via thread própria + SQLite, sem polling externo, sem pub/sub)

---

## Ordem de implementação

1. **Fase 1** (Infra) — sem impacto no sistema, só adiciona Redis
2. **Fase 2** (Eventos backend) — publica eventos mas ninguém consome ainda. Corrige bugs.
3. **Fase 3** (SSE + nginx) — endpoint existe mas frontend ainda não usa
4. **Fase 4** (Web) — migra pra React Query + SSE, remove polling
5. **Fase 5** (Mobile) — migra pra SSE, remove refetchInterval
6. **Fase 6** (Job queue) — migra pra Redis Streams, worker muda claim
7. **Fase 7** (Cache) — adiciona cache Redis
8. **Fase 8** (Deploy final) — validação completa

Cada fase é independente e deployable. Se uma fase tiver problema, as anteriores continuam funcionando.

---

## Auditoria — 21 pontos corrigidos (rev 2)

### Bugs confirmados (3)
1. `panel.py:155` — `job_type=` → `type=` (TypeError em runtime, sem testes)
2. `domain_reset_service.py:376` — cleanup_gameplay sem required_capabilities
3. Job types sem required_capabilities (generate_short, curiosity_short, kids_*)

### Erros no plano corrigidos (5)
4. `XREADGROUP BLOCK 0` → `BLOCK 5000` (BLOCK 0 trava _check_automations)
5. `XAUTOCLAIM` semântica: transfere ownership, não "devolve pra stream"
6. Streams: 9 → 15 (3 prioridades × 5 capability-sets)
7. Jobs multi-capability: publicar em múltiplas streams de subsets
8. `Last-Event-ID` removido (Pub/Sub não persiste — sem histórico, promessa era falsa)

### Gaps corrigidos (6)
9. `game_enrich` sem user_id → canal `global:games` + `user_id=game.user_id` para auditoria
10. `maxmemory-policy allkeys-lru` + `MAXLEN 10000` nos XADD
11. `depends_on: redis: condition: service_healthy` no docker-compose
12. Deduplicação no cliente (event_ids) — invalidateQueries não é idempotente
13. Reconciliador a cada 10s (não 60s) + XAUTOCLAIM no início de cada claim
14. Testes mapeados: test_multi_worker_e2e.py (3 métodos), test_job_requeue.py (1 método), fakeredis nas dev deps

### Falsos positivos confirmados (3)
15. Heartbeat via Redis: manter HTTP como primário (SQLite precisa de last_heartbeat)
16. Catalog service: fica de fora (não precisa de Redis)
17. Redis do google-integration: não compartilhar (128M, allkeys-lru, risco de evicção cruzada)

### Pontos parciais resolvidos (4)
18. `send_timeout 0` é obrigatório na location SSE (não opcional)
19. `react-native-sse` desatualizada — Plano B: `react-native-nitro-sse` ou `rn-eventsource-reborn`
20. Cache BI Identity: TTL 10s (não 60s) — janela de revogação menor
21. Cache dashboard: medir latência antes de ativar (provavelmente over-engineering)
