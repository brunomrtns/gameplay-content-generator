# Bruno Integrations + GPCG Refactor

## Status

[x] 01 — Auditoria completa Bruno Integrations + GPCG
[x] 02 — Corrigir logout (não limpa cookies)
[x] 03 — Corrigir perda de sessão no refresh
[x] 04 — Backend: enviar bi_refresh junto com bi_auth na validação
[x] 05 — Deploy incremental (detectar mudanças, rebuild só o necessário)
[x] 06 — Sistema de DomainConfig (context, features, navigation)
[x] 07 — Sistema de temas (design tokens por domínio)
[x] 08 — Tema Kids
[x] 09 — Tema Movies (estrutura preparada)
[x] 10 — Testes de autenticação, deploy e temas
[x] 11 — Documentação final

---

## 01 — Auditoria completa

### Arquitetura do ecossistema

```
brunointegrations.com (VPS — nginx reverse proxy)
    │
    ├── /id/          → BI Identity Service (bi-cadpessoas, Fastify+Prisma, port 3300)
    ├── /gpcg/        → GPCG API (FastAPI, port 8787)
    ├── /gpcg/api/catalog/ → Game Catalog Service (port 8788)
    ├── /trivestia/   → Trivestia web app
    ├── /avesia/      → Avesia app
    ├── /portfolio/   → Portfolio app
    ├── /gapi/        → Google Integration Service
    └── /admin/       → SEO system
```

### Autenticação (BI Identity SSO)

**BI Identity Service** (`bi-cadpessoas/apps/api`):
- Login: email/password ou Google OAuth
- Sets cookies: `bi_auth` (access JWT, 15min) + `bi_refresh` (refresh JWT, 7d)
- Cookie options: `httpOnly: true`, `secure: true` (prod), `sameSite: "lax"`, `domain: "brunointegrations.com"`, `path: "/"`
- `/api/auth/check`: valida `bi_auth`, se expirado tenta `bi_refresh`, rotaciona tokens
- `/api/auth/logout`: revoga refresh token, limpa cookies
- Rotas sob prefix `/api/` (nginx stripa `/id/`)

**GPCG Backend** (`src/gpcg/infrastructure/auth.py`):
- Lê `bi_auth` cookie → chama `http://bi-api:3300/api/auth/check`
- Agora envia AMBOS `bi_auth` + `bi_refresh` (correção item 04)
- Se válido: find-or-create User local por email
- Cria ChannelProfile para novos usuários (default: games)

**GPCG Frontend** (`frontend/src/lib/api.ts`):
- `credentials: "include"` em todas as requests
- On 401: chama `/id/api/auth/check` (client-side, browser envia ambos cookies)
- Se refresh OK: retry request
- Se refresh falha: redirect para `/id/login`
- `ssoLogout()`: chama `POST /id/api/auth/logout` diretamente (correção item 02)

### Problemas encontrados e corrigidos

1. **Logout não funcionava** → Corrigido: frontend chama BI Identity logout diretamente
2. **Sessão perdida no refresh** → Corrigido: ProtectedRoute sempre valida sessão; backend envia bi_refresh
3. **Deploy sempre rebuilda tudo** → Corrigido: detecção de mudanças via hash
4. **Sem sistema de temas** → Implementado: DomainConfig com design tokens por domínio
5. **DomainConfig ausente** → Implementado: configuração centralizada por domínio

---

## 02 — Corrigir logout

**Problema**: GPCG `/api/auth/logout` só retornava `{"redirect": "/id/login"}` sem limpar cookies.

**Correção**:
- Frontend: `ssoLogout()` em `frontend/src/lib/api.ts` chama `POST /id/api/auth/logout` diretamente
- Backend: `src/gpcg/api/auth_routes.py` logout agora proxya para BI Identity (best-effort)
- Layout: `handleLogout` chama `ssoLogout()` antes de limpar estado local

**Arquivos modificados**:
- `frontend/src/lib/api.ts` — adicionada `ssoLogout()`
- `frontend/src/components/layout.tsx` — usa `ssoLogout()`
- `src/gpcg/api/auth_routes.py` — logout proxy para BI Identity

**Testes**: `tests/test_auth_fixes.py::test_logout_proxies_to_bi_identity`

---

## 03 — Corrigir perda de sessão no refresh

**Problema**: `ProtectedRoute` pulava validação se usuário existia no localStorage. Após 15min (expiração do `bi_auth`), o usuário via a página brevemente antes de ser redirecionado.

**Correção**:
- `ProtectedRoute` em `frontend/src/main.tsx` agora sempre chama `/api/auth/me` no mount
- Estado `checking` começa como `true` (sempre mostra spinner enquanto valida)
- Se `/api/auth/me` falha (401 → refresh falha), desloga e redireciona

**Arquivos modificados**:
- `frontend/src/main.tsx` — ProtectedRoute sempre valida sessão

---

## 04 — Backend: enviar bi_refresh junto com bi_auth

**Problema**: `_validate_bi_user` enviava apenas `bi_auth` (15min) para BI Identity. Quando expirava, backend retornava 401 mesmo tendo `bi_refresh` (7d) disponível.

**Correção**:
- `src/gpcg/infrastructure/auth.py` agora envia ambos cookies
- BI Identity pode fazer refresh transparente server-side
- Reduz roundtrips de refresh no frontend

**Arquivos modificados**:
- `src/gpcg/infrastructure/auth.py` — `_validate_bi_user` envia `bi_refresh`

**Testes**: `tests/test_auth_fixes.py::test_validate_bi_user_forwards_both_cookies`, `test_validate_bi_user_without_refresh_still_works`

---

## 05 — Deploy incremental

**Problema**: `deploy.sh` sempre faz `docker compose build` + `up -d`, mesmo quando nada mudou.

**Correção**:
- `scripts/deploy.sh` agora computa hashes MD5 de:
  - Backend: `src/`, `pyproject.toml`, `Dockerfile`
  - Frontend: `frontend/src/`, `package.json`, `package-lock.json`, `vite.config.ts`, `tsconfig.json`
  - Docker: `Dockerfile`, `docker-compose.prod.yml`
- Compara com hash armazenado em `/opt/gpcg/.deploy-hash` na VPS
- Se idêntico: pula build e restart (apenas rsync + nginx)
- Se diferente: faz build + restart + atualiza hash

**Arquivos modificados**:
- `scripts/deploy.sh` — Step 1b (detecção de mudanças), Step 2/3 condicionais

---

## 06 — Sistema de DomainConfig

**Problema**: Condicional `if domain === "kids"` espalhado pelo código frontend.

**Correção**:
- Criado `frontend/src/lib/domain-config.tsx` com:
  - `DomainConfig` interface: identity, theme, features, navigation, content
  - `DOMAIN_CONFIGS`: games, kids, movies
  - `DomainProvider`: context que busca domínio do backend e aplica tema
  - `useDomain()` hook: componentes leem config em vez de hardcode
- Layout refatorado para usar `useDomain()` (sem GAMES_NAV/KIDS_NAV hardcoded)
- Navegação, logo, e nome do app vêm da config

**Arquivos modificados**:
- `frontend/src/lib/domain-config.tsx` (novo)
- `frontend/src/main.tsx` — DomainProvider envolve Layout
- `frontend/src/components/layout.tsx` — usa `useDomain()`

---

## 07 — Sistema de temas

**Problema**: CSS variables hardcoded no `:root`. Sem mecanismo para trocar tema por domínio.

**Correção**:
- Cada `DomainConfig` tem um `DomainTheme` com 14 design tokens:
  - `accent`, `accentHover`, `accentGlow`, `accentWarm`
  - `bg`, `bgDeep`, `surface`, `surfaceElevated`, `surfaceHover`
  - `border`, `borderBright`
  - `text`, `textSecondary`, `textMuted`
  - `fontFamily`, `radius`, `logoIcon`, `appName`
- `applyTheme()` seta CSS variables em `:root` dinamicamente
- `index.css` mantém valores padrão no `:root` (fallback)

**Arquivos modificados**:
- `frontend/src/lib/domain-config.tsx` — `DomainTheme`, `applyTheme()`
- `frontend/src/index.css` — adicionadas `--font-family`, `--radius-default`

---

## 08 — Tema Kids

**Implementação**:
- Accent: roxo (`hsl(280, 80%, 60%)`)
- Background: roxo escuro (`#1a0d2e`)
- Surface: tons de roxo
- Border radius: `1rem` (mais arredondado, visual infantil)
- Logo: `Baby` icon
- App name: "GPCG Kids"
- Features: topics (true), gameplayUpload (false), ideas (false)

---

## 09 — Tema Movies (estrutura preparada)

**Implementação**:
- Accent: vermelho (`hsl(0, 70%, 55%)`)
- Background: cinema escuro (`#0a0a0d`)
- Border radius: `0.5rem` (mais quadrado, visual cinematográfico)
- Logo: `Film` icon
- App name: "GPCG Movies"
- `implemented: false` — pipeline não ativo
- Features: todas desativadas
- Navigation: dashboard, jobs, automation, videos (mínimo)

---

## 10 — Testes

**Novos testes**: `tests/test_auth_fixes.py` (5 testes)

| Teste | Verifica |
|-------|----------|
| `test_validate_bi_user_forwards_both_cookies` | bi_auth E bi_refresh enviados |
| `test_validate_bi_user_without_refresh_still_works` | bi_auth sozinho funciona |
| `test_logout_proxies_to_bi_identity` | /api/auth/logout retorna redirect |
| `test_find_or_create_handles_integrity_error` | Race condition tratada |
| `test_find_or_create_creates_channel_profile` | ChannelProfile criado para novos usuários |

**Suite completa**: 782 passed (777 + 5 novos)

**Frontend typecheck**: `tsc --noEmit` passou

---

## 11 — Documentação final

### Arquitetura pós-refactor

```
User
  ↓ (BI Identity SSO — bi_auth 15min + bi_refresh 7d)
Channel
  ↓ (DomainConfig — games/kids/movies)
Domain
  ↓ (DomainTheme — design tokens via CSS variables)
Content production pipeline
```

### Fluxo de autenticação

1. Usuário acessa `/gpcg/dashboard`
2. `ProtectedRoute` chama `GET /gpcg/api/auth/me`
3. Backend lê `bi_auth` + `bi_refresh` cookies
4. Backend chama `POST http://bi-api:3300/api/auth/check` com ambos cookies
5. BI Identity valida (ou refresca) e retorna user object
6. Backend encontra/cria User local + ChannelProfile
7. Backend retorna user para frontend
8. `DomainProvider` busca dashboard → aplica tema do domínio
9. Página renderiza com tema correto

### Fluxo de logout

1. Usuário clica "Sair"
2. Frontend chama `POST /id/api/auth/logout` (direto para BI Identity)
3. BI Identity revoga refresh token + limpa cookies via Set-Cookie
4. Frontend limpa estado Zustand (localStorage)
5. Frontend redireciona para `/id/login?redirect=/gpcg/dashboard`

### Deploy incremental

1. `deploy.sh` computa hash de backend + frontend + docker
2. rsync sincroniza código (sempre)
3. Se hash mudou: build + restart + atualiza hash
4. Se hash igual: pula build e restart
5. nginx config sempre atualizada (barato)
6. Smoke test sempre executa

### Adicionando novo domínio

1. Adicionar entrada em `DOMAIN_CONFIGS` em `frontend/src/lib/domain-config.tsx`
2. Adicionar domínio em `IMPLEMENTED_DOMAINS` em `src/gpcg/domains/registry.py`
3. Implementar generation service se for pipeline ativo
4. Adicionar testes
5. Deploy

### Commits deste refactor

| Commit | Descrição |
|--------|-----------|
| `3d786a5` | fix(auth): logout clears SSO cookies via BI Identity |
| `b0940cc` | fix(auth): send bi_refresh + always verify session on refresh |
| `b8468a9` | feat(deploy): incremental deploy with change detection |
| `cc2d710` | feat(domain): DomainConfig system + theme engine |
| `c14435f` | test(auth): add tests for auth fixes |
| `4e69f1d` | fix(deploy): correct vite.config filename |
