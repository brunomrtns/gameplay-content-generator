# Bruno Integrations + GPCG Refactor

## Status

[x] 01 — Auditoria completa Bruno Integrations + GPCG
[ ] 02 — Corrigir logout (não limpa cookies)
[ ] 03 — Corrigir perda de sessão no refresh
[ ] 04 — Backend: enviar bi_refresh junto com bi_auth na validação
[ ] 05 — Deploy incremental (detectar mudanças, rebuild só o necessário)
[ ] 06 — Sistema de DomainConfig (context, features, navigation)
[ ] 07 — Sistema de temas (design tokens por domínio)
[ ] 08 — Tema Kids
[ ] 09 — Tema Movies (estrutura preparada)
[ ] 10 — Testes de autenticação, deploy e temas
[ ] 11 — Documentação final

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
- Lê `bi_auth` cookie → chama `http://bi-api:3300/api/auth/check` com apenas `bi_auth`
- Se válido: find-or-create User local por email
- **BUG**: não envia `bi_refresh` → não pode refresh server-side

**GPCG Frontend** (`frontend/src/lib/api.ts`):
- `credentials: "include"` em todas as requests
- On 401: chama `/id/api/auth/check` (client-side, browser envia ambos cookies)
- Se refresh OK: retry request
- Se refresh falha: redirect para `/id/login`

### Problemas encontrados

1. **Logout não funciona**: GPCG `/api/auth/logout` só retorna `{"redirect": "/id/login"}` — não limpa cookies, não chama BI Identity logout. Cookies `bi_auth` e `bi_refresh` permanecem setados.

2. **Sessão perdida no refresh**: GPCG backend valida apenas com `bi_auth` (15min). Quando expira, retorna 401. Frontend tenta refresh via `/id/api/auth/check`. Se houver qualquer problema no refresh, usuário é deslogado.

3. **Backend não faz refresh server-side**: `_validate_bi_user` envia apenas `bi_auth`, não `bi_refresh`. Poderia fazer refresh direto na chamada server-side.

4. **Deploy sempre rebuilda tudo**: `deploy.sh` sempre faz `docker compose build` + `up -d` para todos os serviços, mesmo quando apenas um componente mudou.

5. **Sem sistema de temas**: CSS variables hardcoded no `:root`. Sem mecanismo para trocar tema por domínio.

6. **DomainConfig ausente**: Condicional `if domain === "kids"` espalhado pelo código frontend.

### Arquivos principais da auditoria

- `bi-cadpessoas/apps/api/src/routes/auth.ts` — BI Identity auth routes
- `bi-cadpessoas/apps/api/src/index.ts` — BI Identity server setup
- `gameplay-content-generator/src/gpcg/infrastructure/auth.py` — GPCG SSO validation
- `gameplay-content-generator/src/gpcg/api/auth_routes.py` — GPCG auth endpoints
- `gameplay-content-generator/frontend/src/lib/api.ts` — Frontend API client
- `gameplay-content-generator/frontend/src/lib/auth.ts` — Frontend auth store
- `gameplay-content-generator/frontend/src/main.tsx` — Router + ProtectedRoute
- `gameplay-content-generator/frontend/src/components/layout.tsx` — Layout + logout button
- `gameplay-content-generator/scripts/deploy.sh` — Deploy script
- `gameplay-content-generator/frontend/src/index.css` — CSS variables (tema hardcoded)
- `gameplay-content-generator/src/gpcg/domains/registry.py` — Domain registry
