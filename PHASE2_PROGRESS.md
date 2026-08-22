# Bruno Integrations + GPCG — Fase 2

## Status

[x] 01 — Auditoria pós-Fase 1
[x] 02 — Corrigir healthcheck do worker (herda HTTP check mas não serve HTTP)
[x] 03 — Corrigir inbox_dir do worker na VPS (procura /media/bruno/ToshibaHD)
[x] 04 — Eliminar condicionais de domínio espalhados (dashboard, videos, automation)
[x] 05 — Theme Engine: persistir tema no refresh (não depende apenas de fetch)
[x] 06 — Logout: verificar duplicação entre GPCG backend e frontend
[x] 07 — Deploy: granularidade backend vs frontend (cache de layers)
[x] 08 — Deploy: build cache otimização (preservar cache, não prune -af)
[x] 09 — Rollback: documentar e validar estratégia (scripts/rollback.sh)
[x] 10 — Security review: cookies, CSRF, redirects, localStorage
[x] 11 — Observabilidade: logs de auth, deploy, domain
[x] 12 — Testes de regressão: auth, domain, theme (10 novos testes)
[x] 13 — Arquitetura multi-app: documentar fronteiras
[x] 14 — Documentação final

---

## Auditoria pós-Fase 1 — Achados

### Bugs encontrados e corrigidos

1. **Worker healthcheck incorreto** — `gpcg-worker` herda o healthcheck do
   Dockerfile que checa `http://127.0.0.1:8787/api/health`, mas o worker não
   serve HTTP (é apenas processador de jobs). Resultado: container sempre
   `unhealthy`.

   **Correção:** Adicionado healthcheck de processo no docker-compose.prod.yml
   que verifica se o processo `gpcg` está rodando via `/proc/1/cmdline`.

2. **Worker inbox_dir inválido na VPS** — `gameplay_inbox_dir` default é
   `/media/bruno/ToshibaHD` (caminho do PC local com GPU). Na VPS este path
   não existe, gerando warnings a cada 30s.

   **Correção:** Adicionado config `gpcg_inbox_watcher_enabled` (default True).
   No docker-compose.prod.yml do worker na VPS, setado para `false`.

3. **Condicionais de domínio espalhados** — Apesar do DomainConfig,
   `dashboard.tsx`, `videos.tsx` e `automation.tsx` ainda usavam
   `isKidsDomain = dash?.channel_domain === "kids"` em vez de `useDomain()`.

   **Correção:** Refatorado para usar `useDomain()` de `domain-config.tsx`.
   Automation agora usa `domainConfig.features.gameplayUpload` em vez de
   checar string de domínio.

4. **Theme não persiste no refresh** — `DomainProvider` buscava o domínio via
   `api.getDashboard()` no mount. Se a API demora ou falha, o tema padrão
   (games) era aplicado brevemente antes da correção (flash de tema).

   **Correção:** DomainProvider agora inicializa do localStorage
   (`gpcg-domain` key) e aplica o tema imediatamente no mount. O fetch do
   backend sincroniza/corrige após o mount.

5. **Deploy: build cache removido após cada build** — `docker builder prune -af`
   removia todo o cache de build após cada deploy, derrotando o propósito de
   cache de layers.

   **Correção:** Trocado por `docker image prune -f` (remove apenas imagens
   dangling, preserva build cache).

6. **Dockerfile: pip install invalidava cache a cada mudança de código** —
   `COPY src/` vinha antes do `pip install -e "."`, invalidando o cache de
   pip a cada mudança de código.

   **Correção:** Separado em duas layers: (1) pip install com pyproject.toml
   + __init__.py stub, (2) COPY src/ depois. Agora mudanças de código não
   rebuildam dependências Python.

7. **Logout: imports inline no auth_routes.py** — `import httpx` e
   `from gpcg.config import get_settings` eram feitos dentro do corpo da
   função logout.

   **Correção:** Movidos para imports no topo do módulo.

### Pontos corretos (não alterados)

- **BI Identity logout** corretamente revoga refresh token e limpa cookies
  com `clearCookie` usando os mesmos `COOKIE_OPTS` (httpOnly, secure,
  sameSite=lax, domain, path=/).
- **BI Identity cookie config** correta (httpOnly: true, secure: true em
  produção, sameSite: "lax", domain: "brunointegrations.com", path: "/").
- **GPCG backend** corretamente envia `bi_auth` + `bi_refresh` para BI
  Identity `/api/auth/check`.
- **ProtectedRoute** corretamente valida sessão no mount via `api.getMe()`.
- **DomainConfig e Theme Engine** estruturalmente sólidos.
- **Deploy incremental** funciona (hash comparison com `.deploy-hash`).
- **Rollback tags** são criadas antes de cada deploy.
- **Frontend ssoLogout()** chama BI Identity diretamente (`/id/api/auth/logout`).
- **Backend `/api/auth/logout`** existe como fallback e proxya para BI Identity.
- **`is_admin` no localStorage** é seguro — o backend valida admin em cada
  request via `get_admin_user` → `_validate_bi_user` → `_is_gpcg_admin`.
- **Worker API key** (`X-Worker-Key`) é separada de SSO humano — não há
  confluência entre os dois sistemas de auth.

---

## Arquitetura Multi-App — Bruno Integrations

### Produção (brunointegrations.com)

```
brunointegrations.com (Nginx — trivestia-nginx)
    ├── /id/              → BI Identity Service (Fastify + Prisma)
    │                        Auth central: login, logout, /api/auth/check
    │                        Cookies: bi_auth (15min), bi_refresh (7d)
    │                        Domain: brunointegrations.com, path: /
    │
    ├── /gpcg/            → GPCG API + Frontend (FastAPI + React)
    │                        Frontend: React/Vite/Tailwind sob /gpcg/
    │                        API: FastAPI sob /gpcg/api/
    │                        Valida cookies via BI Identity
    │                        SQLite em volume Docker
    │
    ├── /gpcg/api/catalog/ → Catalog Service (FastAPI, porta 8788)
    │                        IGDB sync, query API
    │
    ├── /trivestia/       → Trivestia
    ├── /avesia/          → Avesia
    ├── /portfolio/       → Portfolio
    ├── /gapi/            → Google Integration Service
    └── /admin/           → SEO system
```

### Autenticação SSO

```
Browser
  │
  ├── bi_auth cookie (JWT access, 15min, httpOnly, secure, sameSite=lax)
  ├── bi_refresh cookie (JWT refresh, 7d, httpOnly, secure, sameSite=lax)
  │
  ↓ (cookies enviados para brunointegrations.com em qualquer path)
  │
  Nginx
  │
  ├── /id/api/auth/check  → BI Identity valida/rotaciona tokens
  ├── /id/api/auth/logout → BI Identity revoga + limpa cookies
  │
  └── /gpcg/api/*         → GPCG backend lê cookies, encaminha para
                            BI Identity /api/auth/check (server-side),
                            mapeia para User local por email
```

### Fluxo de Login

1. Usuário acessa `/gpcg/dashboard` (rota protegida)
2. `ProtectedRoute` chama `api.getMe()` → GPCG backend → BI Identity
3. Se não autenticado → redirect para `/id/login?redirect=/gpcg/dashboard`
4. Usuário faz login no BI Identity
5. BI Identity seta cookies `bi_auth` + `bi_refresh` (domain: brunointegrations.com)
6. Redirect de volta para `/gpcg/dashboard`
7. `ProtectedRoute` re-valida → autenticado

### Fluxo de Refresh (token expirado)

1. `bi_auth` expira após 15min
2. Próxima chamada API → GPCG backend → BI Identity retorna 401
3. GPCG backend envia `bi_refresh` → BI Identity rotaciona access token
4. Se BI Identity retornar 200 com novo `Set-Cookie: bi_auth` → sessão continua
5. Frontend também tem `tryRefreshSsoCookie()` que chama `/id/api/auth/check`
   diretamente para renovar o cookie no browser

### Fluxo de Logout

1. Frontend chama `ssoLogout()` → `POST /id/api/auth/logout`
2. BI Identity revoga refresh token no DB (`revokedAt = now()`)
3. BI Identity limpa cookies (`clearCookie` com mesmos attrs)
4. Frontend limpa estado local (`useAuth.getState().logout()`)
5. Redirect para `/id/login`
6. Reuso de refresh token antigo falha (revogado)

### Workers (Compute Plane) — Auth separada

- Workers usam header `X-Worker-Key: $GPCG_WORKER_API_KEY`
- **NÃO** usam cookies BI Identity
- Rotas: `/api/workers/*`, `/api/jobs/claim`, etc.
- Validação via `worker_auth` dependency em `worker_routes.py`

---

## Deploy

### Deploy Incremental

```bash
./scripts/deploy.sh
```

1. Valida working tree limpa
2. Roda pytest (792 testes)
3. Cria tag `pre-deploy-TIMESTAMP`
4. rsync para VPS (exclui .git, data, .env, .venv, node_modules)
5. Computa hashes: backend, frontend, docker
6. Compara com `.deploy-hash` na VPS
7. Se mudou: `docker compose build` + `docker compose up -d`
8. Se não mudou: pula build e restart
9. Atualiza nginx (sempre — barato)
10. Valida e recarrega nginx
11. Smoke test: API pública + Catalog health

### Rollback

```bash
./scripts/rollback.sh                    # rollback para última tag pre-deploy
./scripts/rollback.sh pre-deploy-20260821-223659  # tag específica
./scripts/rollback.sh v0.3.14            # versão específica
```

Rollback faz checkout da tag e deploy com `--no-build --no-commit --no-test`.
**Importante:** Rollback não reverte migrations de DB. Se um deploy incluiu
mudanças de schema, rollback manual do DB pode ser necessário.

### Build Cache

- Dockerfile multi-stage: Stage 1 (frontend builder) + Stage 2 (Python runtime)
- Cache de npm preservado (`COPY package.json` antes de `COPY frontend/`)
- Cache de pip preservado (`COPY pyproject.toml` antes de `COPY src/`)
- `docker image prune -f` remove apenas imagens dangling (não build cache)

---

## Domain System

### Domínios Configurados

| Domínio  | Implementado | Tema         | Features principais                    |
|----------|--------------|--------------|----------------------------------------|
| games    | sim          | dark/teal    | gameplayUpload, ideas, gameRegistry    |
| kids     | sim          | purple       | topics                                 |
| movies   | não          | red/cinema   | (nenhuma — estrutura preparada)        |

### Theme Engine

- 14+ design tokens (accent, bg, surface, border, text, radius, etc.)
- Aplicados via CSS variables em `:root`
- Persistido em `localStorage` (`gpcg-domain` key) para evitar flash no refresh
- Sincronizado com backend via `api.getDashboard()` após mount

### Feature Gating

- `domainConfig.features.gameplayUpload` — mostra/esconde upload de gameplay
- `domainConfig.features.ideas` — mostra/esconde página de ideias
- `domainConfig.features.topics` — mostra/esconde página de tópicos (Kids)
- Navegação renderizada dinamicamente de `domainConfig.navigation`

---

## Testes

### Suíte completa

```
792 passed, 4 warnings in ~65s
```

### Testes de regressão da Fase 2

`tests/test_phase2_fixes.py` — 10 testes:

1. `test_inbox_watcher_config_exists` — config existe e default True
2. `test_inbox_watcher_can_be_disabled` — pode ser setado para False
3. `test_worker_skips_inbox_when_disabled` — worker respeita flag
4. `test_domain_configs_have_all_domains` — games, kids, movies presentes
5. `test_domain_configs_have_required_theme_tokens` — 17 tokens verificados
6. `test_domain_configs_have_features` — 6 feature flags verificados
7. `test_games_features_enabled` — games tem gameplayUpload, ideas, gameRegistry
8. `test_kids_features_enabled` — kids tem topics, não gameplayUpload
9. `test_movies_not_implemented` — movies tem implemented: false
10. `test_domain_persistence_uses_localStorage` — persistência verificada

### Frontend typecheck

```
tsc --noEmit — passed
```

---

## Produção — Estado Final

```
gpcg-api       Up (healthy)
gpcg-worker    Up (healthy)  ← antes era unhealthy
gpcg-catalog   Up (healthy)
bi-api         Up (healthy)
trivestia-nginx Up
```

API pública: `https://brunointegrations.com/gpcg/api/health` → `{"status":"ok"}`

---

## Recomendações para a Fase 3

1. **Testes E2E de auth** — Testar login/refresh/logout em browser real
   (Playwright/Cypress) para validar fluxos que unit tests não cobrem.

2. **Separação de imagens Docker** — Atualmente API, worker e catalog
   compartilham `gpcg-api:latest`. Separar em imagens distintas permitiria
   rebuild/restart independente.

3. **DB migrations versionadas** — Adicionar Alembic ou similar para
   migrations versionadas e rollback seguro de schema.

4. **Movies domain** — Implementar pipeline de Movies quando requisitado
   (atualmente apenas estrutura frontend preparada).

5. **CSRF protection** — Avaliar necessidade de CSRF tokens para endpoints
   mutáveis (POST/PUT/DELETE). Cookies sameSite=lax protege contra CSRF
   cross-site, mas não same-site.

6. **Rate limiting de auth** — Adicionar rate limiting nos endpoints de
   BI Identity (login, refresh) para prevenir brute force.

7. **Observabilidade de produção** — Adicionar structured logging (JSON)
   e métricas (Prometheus/Grafana) para monitorar auth, jobs, worker.

---

## Commits da Fase 2

```
b8034e2 fix(phase2): worker healthcheck, inbox watcher, domain conditionals, theme persistence
<hash> feat(phase2): deploy cache, rollback script, observability, regression tests
```

## Relatório Final

### O que foi auditado

- Phase 1 commits e diffs (7 commits, 10 arquivos, 956 inserções)
- BI Identity Service (auth.ts, cookie config, logout, refresh)
- GPCG backend (auth.py, auth_routes.py, config.py, worker.py)
- GPCG frontend (api.ts, auth.ts, main.tsx, domain-config.tsx, layout.tsx)
- Deploy script (deploy.sh, Dockerfile, docker-compose.prod.yml)
- Produção (3 containers GPCG + BI Identity + Nginx)

### Problemas encontrados

7 problemas (6 bugs + 1 melhoria de código), listados acima.

### Problemas corrigidos

Todos os 7. Ver seção "Bugs encontrados e corrigidos".

### Melhorias implementadas

- Dockerfile com cache de pip preservado
- Build cache preservado após deploy
- Script de rollback (`scripts/rollback.sh`)
- Logs de observabilidade em auth
- 10 testes de regressão da Fase 2
- Documentação completa de arquitetura multi-app

### Testes executados

- 792 testes pytest (782 da Fase 1 + 10 novos da Fase 2)
- Frontend typecheck (tsc --noEmit)
- Smoke test de produção (API + Catalog health)
- Verificação de containers (todos healthy)

### Mudanças de infraestrutura

- Worker healthcheck alterado de HTTP para processo
- Worker na VPS com inbox watcher desabilitado
- Build cache preservado (não mais pruned)
- Dockerfile com layers de pip separados

### Mudanças de arquitetura

- Nenhuma mudança estrutural — arquitetura da Fase 1 é sólida
- DomainConfig agora é a fonte única de verdade para domínio no frontend
- Theme persistido em localStorage para UX sem flash

### Itens pendentes

Nenhum item bloqueado. Recomendações para Fase 3 listadas acima.

### Recomendações para a próxima fase

Ver seção "Recomendações para a Fase 3".
