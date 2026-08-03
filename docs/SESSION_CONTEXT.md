# Resumo de Contexto — Continuação do GPCG

## O que é o GPCG

GPCG (Gameplay Content Generator) é uma plataforma multi-usuário para geração automatizada de vídeos de YouTube Shorts sobre games. Localizado em `/home/bruno/Desenvolvimento/brunointegrations/gameplay-content-generator/`.

**Arquitetura:** Control Plane (VPS com Docker, API FastAPI, SQLite, frontend React) + Compute Plane (PC local com GPU RTX 3060, worker que roda VLM/ASR/render via Ollama + video-generate subprocess).

**Deploy:** `./scripts/deploy.sh` faz rsync para VPS + docker compose build + nginx reload. VPS acessível via `my-vps` command.

## Estado Atual do Sistema

### Funcionalidades já funcionando
- Upload de gameplays (chunked, resumable)
- Mapping de gameplays (VLM gemma3:12b + ASR Whisper) no worker local
- Knowledge base (upload de PDFs, chunking, embeddings nomic-embed-text, RAG)
- Fact extraction de documentos (LLM)
- Pipeline editorial completo: content_planning → story_finding → editorial_planning → creative_engine → script → humanization → script_review → tts → gameplay_selection → render → qa → metadata → youtube_upload
- Automação contínua (worker chama `/api/automation/check` a cada poll cycle, cria jobs via `create_job_from_automation`)
- Publicação no YouTube (via google-integration service)
- Multi-usuário com SSO (BI Identity)

### Bugs corrigidos nesta sessão
1. **YouTube publish na conta errada** — `_maybe_auto_publish` e `publish_video` usavam `settings.gpcg_youtube_user_id` (hardcoded=1) em vez de `job.user_id`. Corrigido para passar `user.id`/`job_user_id`. Commits: `e4bc000`.
2. **Legendas ignorando config do usuário** — Jobs criados manualmente não tinham `subtitle_config` nos artifacts. Corrigido adicionando endpoint `POST /api/automation/check` que o worker chama, que usa `create_job_from_automation()` (passa todas as configs de legenda/voz/transição corretamente). Commit: `9db913d`.
3. **Job capability mismatch** — Job `generate_short` requeria capability `generate_short` mas worker tem `generation`. Corrigido manualmente no DB.

### Vídeos gerados
- Video 7: publicado (QA 85)
- Video 8: publish_failed (foi publicado na conta errada — YouTube ID fl7sv77M5sk, precisa ser deletado manualmente do canal brunointegrations@gmail.com)
- Video 9: pending_approval (gerado corretamente, aguardando publicação na conta certa)

### Worker local
Rodando em background com:
```bash
GPCG_SSH_HOST=10.0.0.1 GPCG_SSH_USER=root \
.venv/bin/gpcg remote-worker \
  --vps-url https://brunointegrations.com/gpcg \
  --worker-id home-pc \
  --api-key EL07MPUjZ1dChDct7yVJDdbUq340PV1r2NmeoJGSTwg \
  --capabilities "mapping,generation,knowledge_index"
```
Logs em `/tmp/gpcg-worker.log`. PID pode variar (reiniciado várias vezes).

### Automação
- User 2 (brunointegrationsgaming@gmail.com) tem automação status="running"
- `auto_publish=false` (vídeos ficam pending_approval)
- Worker cria jobs automaticamente via `/api/automation/check`

## Documento de Arquitetura Entregue

Arquivo: `docs/ARCHITECTURE_EVOLUTION.md` — blueprint completo para evolução do GPCG com:

1. **Game Registry Canônico** — tabelas: game_aliases, game_entities, franchises, series, characters. Game.user_id deprecated (jogos globais). Slug + deduplicação.
2. **Game Knowledge** — 20+ campos ricos (developer, publisher, franchise, genres, themes, engine, release_date, description, lore_summary, etc.)
3. **Game Knowledge Enrichment** — pipeline automático via Wikidata + Wikipedia + Steam. Novo job type `game_enrich`.
4. **Gameplay Intelligence** — herança de conhecimento via JOINs, embeddings em GameplayEvents, denormalização de game_id/franchise_id.
5. **Content Intelligence** — 5 conectores (RSS/Google News, Wikipedia/Wikidata, Steam, Reddit, IGDB). Interface abstrata `ContentConnector`. Novo job type `content_collect`.
6. **Knowledge Item** — entidade normalizada unificando notícias, curiosidades, eventos, lore, facts. Tabela `knowledge_items`. Facts existentes migrados.
7. **Knowledge Graph** — grafo relacional (não Neo4j). Tabela `game_relationships` com 18 tipos de relacionamento. Populado durante enrichment.
8. **Seleção Inteligente de Gameplay** — `GameplayMatcher` cross-game via Knowledge Graph. Scoring: semantic_similarity × graph_proximity × interesting_score. Fallback em cascata: game → franchise → company → genre → public.
9. **Gameplays Públicas** — visibilidade private/unlisted/public. Opt-in em User.allow_public_gameplays.
10. **Config de Automações** — novas seções: content_intelligence, gameplay_selection, knowledge_sources.
11. **Impacto Arquitetural** — 7 tabelas novas, ~25 colunas novas, 6 enums novos, 4 routers novos, ~25 endpoints novos, 3 páginas frontend novas.
12. **Plano de Implementação** — 8 fases com dependências. Fase 1 (Game Registry) → Fase 2 (Enrichment) → Fase 3 (Knowledge Graph) → Fase 4 (Knowledge Items + Content Intelligence) → Fase 5 (Gameplay Intelligence) → Fase 6 (Pipeline Editorial) → Fase 7 (Gameplays Públicas) → Fase 8 (Config + Frontend).

**NÃO foi implementado nada.** Apenas o documento foi escrito.

## Pendências

1. **Video 8 no YouTube errado** — deletar `fl7sv77M5sk` do canal brunointegrations@gmail.com manualmente
2. **Video 9** — publicar via UI (agora vai para a conta correta: brunointegrationsgaming@gmail.com)
3. **Implementar a arquitetura proposta** — o documento `docs/ARCHITECTURE_EVOLUTION.md` é o blueprint. Começar pela Fase 1 (Game Registry Canônico) quando o usuário autorizar.

## Comandos Úteis

```bash
# Verificar status dos jobs na VPS
my-vps --no-lock 'docker exec gpcg-api python3 -c "
import sqlite3
conn = sqlite3.connect(\"/app/data/gpcg.db\")
c = conn.cursor()
c.execute(\"SELECT id, status, stage, progress FROM jobs ORDER BY id DESC LIMIT 5\")
for r in c.fetchall(): print(r)
conn.close()
"'

# Verificar vídeos
my-vps --no-lock 'docker exec gpcg-api python3 -c "
import sqlite3
conn = sqlite3.connect(\"/app/data/gpcg.db\")
c = conn.cursor()
c.execute(\"SELECT id, status, youtube_url FROM videos ORDER BY id DESC LIMIT 3\")
for r in c.fetchall(): print(r)
conn.close()
"'

# Deploy
cd /home/bruno/Desenvolvimento/brunointegrations/gameplay-content-generator
./scripts/deploy.sh

# Reiniciar worker local
kill $(pgrep -f "gpcg remote-worker") 2>/dev/null
GPCG_SSH_HOST=10.0.0.1 GPCG_SSH_USER=root \
.venv/bin/gpcg remote-worker \
  --vps-url https://brunointegrations.com/gpcg \
  --worker-id home-pc \
  --api-key EL07MPUjZ1dChDct7yVJDdbUq340PV1r2NmeoJGSTwg \
  --capabilities "mapping,generation,knowledge_index" > /tmp/gpcg-worker.log 2>&1 &

# Logs do worker
tail -f /tmp/gpcg-worker.log

# GPU status
nvidia-smi
```

## Arquivos Importantes

- `docs/ARCHITECTURE_EVOLUTION.md` — blueprint da evolução (LER PRIMEIRO)
- `AGENTS.md` — documentação do projeto (comandos, arquitetura, convenções)
- `src/gpcg/domain/models.py` — todos os modelos (897 linhas, 15 modelos, 14 enums)
- `src/gpcg/application/generation_service.py` — pipeline de geração (1260 linhas)
- `src/gpcg/application/editorial_strategy.py` — decisão editorial autônoma
- `src/gpcg/worker/remote_worker.py` — worker local (Compute Plane)
- `src/gpcg/api/worker_routes.py` — API do worker (Control Plane)
- `src/gpcg/api/automation_routes.py` — automação + `create_job_from_automation`
- `src/gpcg/config.py` — todas as settings/env vars
- `src/gpcg/worker/local_db_sync.py` — sync de DB para geração local
