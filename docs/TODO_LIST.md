# TODO List — GPCG (Gameplay Content Generator)

> **Status**: Lista viva de melhorias e correções a implementar
> **Última atualização**: 2026-08-06
> **Organização**: Por prioridade (P0 = crítico/estabilidade, P1 = importante, P2 = nice-to-have)

---

## P0 — Estabilidade e Correções Críticas

### [P0-01] Verificar clips ANTES de criar job (generate_short)
- **Status**: ✅ Implementado (v0.3.13)
- **Arquivo**: `src/gpcg/api/automation_routes.py` — `create_job_from_automation()`
- **Descrição**: Quando uma KI na fila tem `game_id`, o sistema agora verifica se o usuário tem clips utilizáveis (GameplayAsset) para esse jogo ANTES de criar o job. Sem isso, o sistema gastava GPU em script/TTS para falhar no final com "no gameplay assets available".
- **Comportamento**: Se não tem clips → remove KI da fila → retorna None → próximo ciclo pega próxima KI.

### [P0-02] Deletar gameplay (frontend + backend + worker)
- **Status**: ✅ Implementado (v0.3.14)
- **Problema**: Não existe forma de deletar uma gameplay. O usuário não pode remover gameplay que não quer mais usar, e os arquivos físicos no HD Toshiba ficam acumulando como lixo.
- **Estado atual** (auditoria):
  - ❌ NÃO existe endpoint `DELETE /api/sources/{id}` ou `DELETE /api/gameplays/{id}`
  - ❌ Frontend `content.tsx` não tem botão deletar (ícone `Trash2` importado mas não usado)
  - ✅ Existem endpoints DELETE para assets, videos, voices, users (padrão a seguir)
  - ✅ Worker salva arquivos em `/media/bruno/ToshibaHD/gpcg/gameplays/{source_id}_{filename}`
  - ❌ Worker NÃO deleta arquivos automaticamente — ficam no HD permanentemente
- **Escopo**:
  - [ ] Backend: endpoint `DELETE /api/sources/{id}` que:
    - Verifica ownership (user_id == requester)
    - Verifica se não há job em andamento usando essa source
    - Marca GameplaySource como `deleted` (soft delete) ou remove do DB
    - Remove GameplayAssets e GameplayEvents associados
    - Sinaliza o worker para apagar arquivos físicos do HD Toshiba
  - [ ] Worker: handler para deletar arquivos físicos quando receber sinal
    - Apaga vídeo original (`gameplays/{source_id}_{filename}`)
    - Apaga analysis JSON (`mapped/`)
    - Apaga renders relacionados (`renders/`)
    - Log de auditoria do que foi apagado
  - [ ] Frontend: botão "Deletar" no card de gameplay (`content.tsx:385-468`)
    - Usar ícone `Trash2` (já importado)
    - Confirmação dupla (gameplay não pode ser deletada por acidente)
    - Feedback visual durante deleção
- **Risco**: Médio — precisa garantir que jobs em andamento não quebrem se a gameplay for deletada

### [P0-03] Privacy enforcement — isolamento total entre usuários
- **Status**: ✅ Implementado (v0.3.15)
- **Problema**: O editorial system pode estar vendo sources de outros usuários. Queries de GameplaySource nem sempre filtram por `user_id` ou `is_public`. Um usuário pode acabar usando gameplay de outro sem saber.
- **Gaps identificados** (auditoria completa):
  - ⚠️ **CRÍTICO**: `POST /sources/{source_id}/assign-game` (`routes.py:248-267`) — não verifica `user_id`. Qualquer usuário pode atribuir game a source de outro.
  - ⚠️ **CRÍTICO**: `IngestionService._ingest_file` (`ingestion_service.py:91-97`) — dedup por `file_hash` sem `user_id`. Segundo usuário com mesmo arquivo não consegue upload.
  - ⚠️ **MENOR**: Worker endpoints (`worker_routes.py`) — download/confirm/mapping-result não filtram por `user_id` (mitigado por worker auth + upload_token).
- **Escopo**:
  - [ ] Fix `POST /sources/{source_id}/assign-game`: adicionar `if source.user_id != user.id: raise HTTPException(403)`
  - [ ] Fix `IngestionService._ingest_file`: adicionar `user_id` na query de duplicata
  - [ ] Adicionar verificação de `user_id` nos endpoints worker como defesa em profundidade
  - [ ] Endpoint de listar gameplays: adicionar `is_public` na resposta + filtrar públicas
  - [ ] Editorial system: só considerar gameplay do usuário + públicas
  - [ ] GameplayRetriever: respeitar ownership/visibility (já filtra, mas validar)
  - [ ] Testes: garantir que user A não vê/usa gameplay de user B (a menos que pública)
- **Risco**: Alto — mudança que pode quebrar fluxos existentes se não for bem testada

### [P0-04] Auto-detecção de problemas no sistema
- **Status**: ✅ Implementado (v0.3.16)
- **Problema**: Problemas como "source sem clips", "KI sem jogo", "gameplay órfã" só são detectados quando o sistema tenta usar e falha. Deveria haver detecção proativa.
- **Escopo**:
  - [ ] Job periódico de health-check que detecta:
    - GameplaySource com status=ready mas 0 GameplayAssets
    - GameplaySource com 0 GameplayEvents (não mapeada)
    - KnowledgeItems com game_id de jogo que não existe mais
    - Jobs presos (running por > 1h sem heartbeat)
    - Fila com KIs rejeitadas que não foram limpas
  - [ ] Ações automáticas:
    - Alertar na UI (badge de warning no dashboard)
    - Auto-limpar KIs rejeitadas da fila
    - Auto-falhar jobs presos
    - Sugerir re-mapeamento para sources sem events
  - [ ] Endpoint `GET /api/health/detailed` com diagnóstico completo
- **Risco**: Baixo — só detecta, não modifica dados sem confirmação

---

## P1 — Melhorias Importantes

### [P1-01] Separar gameplays: minhas vs públicas da comunidade
- **Status**: 🔲 Não implementado
- **Problema**: A tela de upload/listagem de gameplays mostra tudo misturado. O usuário não sabe quais são suas gameplays e quais são públicas de outros usuários.
- **Estado atual** (auditoria frontend):
  - ❌ `GET /api/sources` (`routes.py:179-201`) NÃO retorna campo `is_public` na resposta
  - ❌ Frontend `content.tsx` não tem separação visual nem filtro
  - ❌ Ícone `Eye` importado mas não usado
  - ✅ Backend tem `PATCH /api/gameplays/{source_id}/visibility` (`routes.py:378`)
  - ✅ Frontend tem `api.toggleGameplayVisibility` (`api.ts:387`)
  - ✅ Modelo `GameplaySource` tem campo `is_public` (`models.py:502`)
- **Escopo**:
  - [ ] Backend: adicionar `is_public` na resposta de `GET /api/sources`
  - [ ] Backend: adicionar parâmetro `include_public=true` para incluir gameplays públicas de outros usuários
  - [ ] Frontend: duas seções claras na tela de gameplays (`content.tsx:369-471`)
    - "Minhas Gameplays" — upload do usuário, com botão deletar/gerenciar
    - "Gameplays Públicas da Comunidade" — gameplays marcadas como is_public
  - [ ] Badge visual: "Privada" vs "Pública" em cada card (usar ícone Eye já importado)
  - [ ] Toggle para marcar gameplay como pública/privada (usar `api.toggleGameplayVisibility` já existe)
  - [ ] Filtro: ver só minhas / só públicas / todas
- **Risco**: Baixo — mudança visual + query filter

### [P1-02] Scoring via LLM no worker (5 dimensões editoriais)
- **Status**: ✅ Implementado (v0.3.13)
- **Arquivo**: `src/gpcg/application/knowledge_item_service.py` — `score_rss_item_headless()`
- **Descrição**: Substituiu a heurística burra (tamanho do título + source) por scoring real via LLM com 5 dimensões editoriais (curiosidade, surpresa, retenção, familiaridade, insight). Items rejeitados pelo quality gate (clickbait/promoção/rumor) não são mais sincronizados para a VPS.

### [P1-03] Coleta automática de ideias (scheduler)
- **Status**: ✅ Implementado (v0.3.13)
- **Arquivo**: `src/gpcg/worker/remote_worker.py` — `_maybe_auto_collect()`
- **Descrição**: Worker agora coleta ideias automaticamente a cada `gpcg_content_collection_interval_hours` (default: 6h). Antes só coletava quando o usuário clicava "Coletar Agora" manualmente.

### [P1-04] Coleta orientada por profile (Editorial Brief integrado)
- **Status**: ✅ Implementado (v0.3.13)
- **Arquivo**: `src/gpcg/application/content_collectors.py` — `collect_rss_items(search_queries=...)`
- **Descrição**: O coletor agora usa queries expandidas do Editorial Brief ("Bully hidden secrets", "Bully easter egg", "Bully story lore") em vez de só "{jogo} game". Isso produz curiosidades/lore, não só notícias.

### [P1-05] Diversidade de tipos de conteúdo na fila
- **Status**: ✅ Implementado (v0.3.13, via P1-04)
- **Descrição**: Antes a fila era 100% news. Agora com queries expandidas, o coletor busca curiosity/lore/fact também. A proporção depende do `content_type_affinity` do ChannelProfile.

### [P1-06] Limpeza automática da fila (KIs rejeitadas/inválidas)
- **Status**: ✅ Implementado (v0.3.16)
- **Problema**: KIs rejeitadas continuam na fila até serem consumidas e falharem. O reconciliador deveria limpar KIs inválidas da fila proativamente.
- **Escopo**:
  - [ ] Reconciliador: ao auto-preencher fila, remover KIs com status=rejected
  - [ ] Reconciliador: remover KIs cujo game_id não tem clips para o usuário
  - [ ] Endpoint `POST /api/idea-queue/cleanup` para limpeza manual
- **Risco**: Baixo

---

## P2 — Nice-to-Have / Futuro

### [P2-01] IA para avaliação de qualidade de clips
- **Status**: 🔲 Não implementado (análise em docs/AI_OPPORTUNITIES_ANALYSIS.md)
- **Descrição**: Usar VLM para avaliar qualidade visual dos clips e sugerir melhores momentos.

### [P2-02] IA para detecção de gameplay de baixa qualidade
- **Status**: 🔲 Não implementado (análise em docs/AI_OPPORTUNITIES_ANALYSIS.md)
- **Descrição**: Detectar gameplay com baixo interesting_score médio e sugerir re-mapeamento ou exclusão.

### [P2-03] IA para recomendação de re-mapeamento
- **Status**: 🔲 Não implementado (análise em docs/AI_OPPORTUNITIES_ANALYSIS.md)
- **Descrição**: Quando uma gameplay tem poucos events ou events de baixa qualidade, sugerir re-mapeamento com parâmetros diferentes.

### [P2-04] IA para avaliação de qualidade do vídeo final
- **Status**: 🔲 Não implementado (análise em docs/AI_OPPORTUNITIES_ANALYSIS.md)
- **Descrição**: Após renderizar, IA avalia o vídeo final (script + narração + gameplay) e dá um score de qualidade.

### [P2-05] IA para detecção de conteúdo duplicado
- **Status**: 🔲 Não implementado (análise em docs/AI_OPPORTUNITIES_ANALYSIS.md)
- **Descrição**: Detectar se dois vídeos do canal são muito similares (mesmo tema, mesmo jogo, mesmo ângulo) usando embeddings.

### [P2-06] IA para otimização automática de thumbnails
- **Status**: 🔲 Não implementado (análise em docs/AI_OPPORTUNITIES_ANALYSIS.md)
- **Descrição**: Gerar thumbnails otimizados para CTR usando VLM para selecionar o frame mais impactante.

### [P2-07] IA para análise de performance do canal
- **Status**: 🔲 Não implementado (análise em docs/AI_OPPORTUNITIES_ANALYSIS.md)
- **Descrição**: Integrar YouTube Analytics + IA para analisar performance do canal e ajustar estratégia editorial automaticamente.

### [P2-08] IA para sugestão de novos jogos para o canal
- **Status**: 🔲 Não implementado (análise em docs/AI_OPPORTUNITIES_ANALYSIS.md)
- **Descrição**: Baseado no perfil do canal e performance histórica, sugerir novos jogos para o usuário adicionar gameplay.

---

## Concluídos (Histórico)

### [DONE-01] Fix: reconciler commit + queue description + processing info
- **Versão**: v0.3.9
- **Data**: 2026-08-06

### [DONE-02] Fix: editorial system no longer picks games without usable clips
- **Versão**: v0.3.10
- **Data**: 2026-08-06

### [DONE-03] Fix: scoring via LLM + coleta automática + Editorial Brief integrado
- **Versão**: v0.3.13
- **Data**: 2026-08-06
- **Itens**:
  - P1-02: Scoring via LLM no worker
  - P1-03: Coleta automática (scheduler 6h)
  - P1-04: Coleta orientada por profile
  - P1-05: Diversidade de tipos
  - P0-01: Verificar clips antes de criar job

### [DONE-04] Deletar gameplay (backend + worker + frontend)
- **Versão**: v0.3.14
- **Data**: 2026-08-06
- **Itens**:
  - P0-02: Endpoint DELETE /api/sources/{id} com soft-delete + cleanup job
  - Worker handler _process_cleanup_gameplay_job apaga arquivos do HD
  - Frontend botão Trash2 com confirmação dupla
  - Filtro: list_sources não retorna sources deletadas

### [DONE-05] Privacy enforcement — isolamento entre usuários
- **Versão**: v0.3.15
- **Data**: 2026-08-06
- **Itens**:
  - Fix POST /sources/{source_id}/assign-game: adicionado auth + ownership check
  - Fix IngestionService._ingest_file: dedup agora respeita user_id
  - IngestionService.scan_once e _ingest_file aceitam user_id opcional
  - Endpoint /inbox/scan passa user_id para IngestionService
  - Endpoint /gameplays/upload passa user_id para IngestionService

### [DONE-06] Auto-detecção de problemas + limpeza de fila
- **Versão**: v0.3.16
- **Data**: 2026-08-06
- **Itens**:
  - P0-04: ProblemDetectorService detecta sources sem clips, sources sem events, jobs presos, KIs rejeitadas na fila, KIs sem gameplay
  - Endpoint GET /api/health/problems retorna diagnóstico completo
  - P1-06: Endpoint POST /api/idea-queue/cleanup remove KIs inválidas da fila

---

## Notas

- Itens P0 devem ser feitos antes de P1/P2
- Dentro de cada prioridade, ordem numérica
- Marcar ✅ quando implementado, 🔲 quando pendente, 🔄 quando em progresso
- Cada item implementado deve ser movido para "Concluídos" com versão e data
