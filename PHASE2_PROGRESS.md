# Bruno Integrations + GPCG — Fase 2

## Status

[ ] 01 — Auditoria pós-Fase 1
[ ] 02 — Corrigir healthcheck do worker (herda HTTP check mas não serve HTTP)
[ ] 03 — Corrigir inbox_dir do worker na VPS (procura /media/bruno/ToshibaHD)
[ ] 04 — Eliminar condicionais de domínio espalhados (dashboard, videos, automation)
[ ] 05 — Theme Engine: persistir tema no refresh (não depende apenas de fetch)
[ ] 06 — Logout: verificar duplicação entre GPCG backend e frontend
[ ] 07 — Deploy: granularidade backend vs frontend (não rebuilda ambos se só um mudou)
[ ] 08 — Deploy: build cache otimização (npm ci cache, pip cache)
[ ] 09 — Rollback: documentar e validar estratégia
[ ] 10 — Security review: cookies, CSRF, redirects, localStorage
[ ] 11 — Observabilidade: logs de auth, deploy, domain
[ ] 12 — Testes de regressão: auth, domain, theme
[ ] 13 — Arquitetura multi-app: documentar fronteiras
[ ] 14 — Documentação final

---

## Auditoria pós-Fase 1 — Achados

### Bugs encontrados

1. **Worker healthcheck incorreto** — `gpcg-worker` herda o healthcheck do Dockerfile que checa `http://127.0.0.1:8787/api/health`, mas o worker não serve HTTP (é apenas processador de jobs). Resultado: container sempre `unhealthy`.

2. **Worker inbox_dir inválido na VPS** — `gameplay_inbox_dir` default é `/media/bruno/ToshibaHD` (caminho do PC local com GPU). Na VPS este path não existe, gerando warnings a cada 30s.

3. **Condicionais de domínio espalhados** — Apesar do DomainConfig, `dashboard.tsx`, `videos.tsx` e `automation.tsx` ainda usam `isKidsDomain = dash?.channel_domain === "kids"` em vez de `useDomain()`.

4. **Theme não persiste no refresh** — `DomainProvider` busca o domínio via `api.getDashboard()` no mount. Se a API demora ou falha, o tema padrão (games) é aplicado brevemente antes da correção. Não há persistência local do tema.

5. **Deploy incremental: granularidade insuficiente** — O hash combinado (`backend:frontend:docker`) faz build de tudo se qualquer componente mudou. Como o Dockerfile é multi-stage (frontend + backend na mesma imagem), isso é parcialmente inevitável, mas o cache de layers pode ser melhor aproveitado.

6. **Logout: possível duplicação** — Frontend chama `ssoLogout()` (BI Identity direto) mas o GPCG backend `/api/auth/logout` também proxya para BI Identity. Se alguém chamar ambos, há double-revoke (não quebra, mas é redundante).

7. **GPCG backend logout importa httpx dentro da função** — `import httpx` e `from gpcg.config import get_settings` são feitos dentro do corpo da função em vez de no topo do módulo.

### Pontos corretos

- BI Identity logout corretamente revoga refresh token e limpa cookies
- BI Identity cookie config correta (httpOnly, secure, sameSite=lax, domain, path=/)
- GPCG backend corretamente envia bi_auth + bi_refresh para BI Identity
- ProtectedRoute corretamente valida sessão no mount
- DomainConfig e Theme Engine estruturalmente sólidos
- Deploy incremental funciona (hash comparison)
- Rollback tags são criadas
