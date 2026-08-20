# Estratégia de Execução — Plano VM Flávio GPCG

Documento companion de `/home/bruno/Downloads/plano-vm-flavio-gpcg.md`.
Define COMO executar o plano sem se perder, com passos verificáveis e
checkpoints persistentes para consulta de qualquer modelo de IA.

---

## 1. Princípios de Execução

### 1.1 Uma fase por vez, com gate de verificação

Cada fase só começa depois que a anterior passa no gate de verificação.
Gate = testes passam + commit feito + estado salvo no checkpoint.

### 1.2 Commits atômicos por sub-fase

Cada sub-fase (ex: 0.1, 0.2) é um commit separado com mensagem descritiva.
Se algo quebrar, `git revert` do commit específico resolve.

### 1.3 Não quebrar o que funciona

O worker do PC Bruno precisa continuar funcionando durante toda a implementação.
Toda mudança é aditiva ou feature-flagged — nunca destrutiva até a VM estar validada.

### 1.4 Contexto persistente entre sessões

Cada fase salva um checkpoint em `/home/bruno/Downloads/checkpoint-fase-N.md`.
Se a sessão cair, o próximo modelo lê o checkpoint e continua de onde parou.

---

## 2. Grafo de Dependências

```
Fase 0: Job requeue (attempts)
    │
    ▼
Fase 0.5: Sync de gameplays
    │
    ├──▶ Pode paralelizar com Fase 1 (não dependem uma da outra)
    │
    ▼
Fase 1: Adapter LLM/VLM (LiteLLM)
    │
    ▼
Fase 2: Adapter ASR (LiteLLM remoto)
    │
    ▼
Fase 3: video-generate deps + KokoroTTSEngine
    │
    ▼
Fase 4: YOLO CPU + Storage paths
    │
    ▼  (Fases 0-4 testáveis no PC Bruno, sem VM)
    │
Fase 5: WireGuard VM ↔ VPS
    │
    ▼
Fase 6: Deploy worker na VM
    │
    ▼
Fase 7: Testes end-to-end
```

### Paralelização possível

| Grupo | Fases | Pode rodar em paralelo? |
|-------|-------|------------------------|
| A | Fase 0 + Fase 0.5 | Sim — não tocam os mesmos arquivos |
| B | Fase 1 + Fase 2 | Parcial — Fase 2 depende do padrão de adapter da Fase 1 |
| C | Fase 3 (video-generate) | Sim — repo diferente, não conflita com GPCG |
| D | Fase 4 | Sim — trivial, pode rodar com qualquer outra |
| E | Fase 5 (WireGuard) | Sim — infra, não conflita com código |

**Estratégia recomendada:** Fase 3 (video-generate) em subagent paralelo
enquanto Fases 0-2 são feitas no GPCG.

---

## 3. Estado de Checkpoint

### 3.1 Formato do checkpoint

Cada fase salva um arquivo `/home/bruno/Downloads/checkpoint-fase-N.md`:

```markdown
# Checkpoint — Fase N

## Status
- [x] Sub-fase N.1 — descrição
- [x] Sub-fase N.2 — descrição
- [ ] Sub-fase N.3 — descrição (próximo passo)

## Commits
- `abc1234` — feat(gpcg): job requeue com attempts existente
- `def5678` — test(gpcg): teste de stale job recovery

## Arquivos modificados
- `src/gpcg/api/worker_routes.py` — linhas 280-295
- `src/gpcg/config.py` — linhas 150-160

## Testes
- `pytest tests/test_job_requeue.py` — 5/5 passando
- `pytest tests/test_worker.py` — 12/12 passando

## Decisões tomadas
- Usar metadata_json em vez de tts_info (confirmado na Fase 0.5)
- GameplayDownload model criado (decidido na Fase 0.5)

## Bloqueadores
- Nenhum

## Próximo passo
- Começar Fase N+1: descrição
```

### 3.2 Protocolo de resume

Ao retomar uma sessão:

1. Ler `checkpoint-fase-N.md` mais recente
2. Verificar `git log --oneline -10` nos dois repos (GPCG + video-generate)
3. Rodar testes existentes para confirmar que nada quebrou
4. Continuar da sub-fase marcada como "próximo passo"

---

## 4. Gates de Verificação por Fase

### Fase 0: Job requeue

**Pré-condições:**
- Repo GPCG clonado e testes atuais passando

**Arquivos a modificar:**
- `src/gpcg/api/worker_routes.py` — adicionar lógica de requeue no `/jobs/claim`
- `src/gpcg/config.py` — adicionar `gpcg_job_lease_timeout`

**Arquivos a criar:**
- `tests/test_job_requeue.py` — teste de stale job recovery

**Gate de verificação:**
- [ ] `pytest tests/test_job_requeue.py` passa
- [ ] `pytest tests/` (suite existente) não regrediu
- [ ] Job em `running` com worker offline há > timeout volta para `queued`
- [ ] Job com `attempts >= max_attempts` vai para `failed` (não `queued`)
- [ ] Commit feito
- [ ] Checkpoint salvo

### Fase 0.5: Sync de gameplays

**Pré-condições:**
- Fase 0 aprovada no gate

**Arquivos a modificar:**
- `src/gpcg/domain/models.py` — adicionar GameplayDownload (ou estender GameplaySource)
- `src/gpcg/api/worker_routes.py` — confirm_download com tracking + cleanup condicional
- `src/gpcg/worker/remote_worker.py` — sync no startup + download on-demand
- `src/gpcg/worker/local_db_sync.py` — flexibilizar paths hardcoded

**Arquivos a criar:**
- `tests/test_gameplay_sync.py`

**Gate de verificação:**
- [ ] `pytest tests/test_gameplay_sync.py` passa
- [ ] VPS não apaga gameplay até todos os workers confirmarem
- [ ] Worker baixa gameplay no startup se não tem localmente
- [ ] Worker consegue gerar job mesmo se outro worker fez o mapping
- [ ] `pytest tests/` (suite existente) não regrediu
- [ ] Commit feito
- [ ] Checkpoint salvo

### Fase 1: Adapter LLM/VLM

**Pré-condições:**
- Fase 0.5 aprovada no gate

**Arquivos a modificar:**
- `src/gpcg/config.py` — adicionar provider configs
- `src/gpcg/infrastructure/llm.py` — refatorar LLMClient (Ollama | LiteLLM)

**Arquivos a criar:**
- `tests/test_llm_litellm.py`

**Gate de verificação:**
- [ ] `pytest tests/test_llm_litellm.py` passa
- [ ] LLMClient com `provider=ollama` funciona igual ao atual (PC Bruno)
- [ ] LLMClient com `provider=litellm` faz chamada HTTP via httpx
- [ ] Retry com `Retry-After` funciona (mock 429)
- [ ] `pytest tests/` não regrediu
- [ ] Commit feito
- [ ] Checkpoint salvo

### Fase 2: Adapter ASR

**Pré-condições:**
- Fase 1 aprovada (padrão de adapter estabelecido)

**Arquivos a modificar:**
- `src/gpcg/config.py` — adicionar ASR provider config
- `src/gpcg/infrastructure/asr_transcriber.py` — adicionar RemoteASRTranscriber
- `src/gpcg/application/gameplay_analyzer.py` — factory para ASR provider

**Arquivos a criar:**
- `tests/test_asr_remote.py`

**Gate de verificação:**
- [ ] `pytest tests/test_asr_remote.py` passa
- [ ] ASR com `provider=faster_whisper` funciona igual ao atual
- [ ] ASR com `provider=litellm` faz chamada HTTP via httpx
- [ ] Retry com `Retry-After` funciona
- [ ] `pytest tests/` não regrediu
- [ ] Commit feito
- [ ] Checkpoint salvo

### Fase 3: video-generate deps + Kokoro

**Pré-condições:**
- Nenhuma (repo independente — pode paralelizar com Fases 0-2)

**Arquivos a modificar (video-generate):**
- `requirements.txt` — split em core/gpu/comfyui
- `pyproject.toml` — adicionar optional-dependencies
- `src/generators/tts_factory.py` — adicionar KokoroTTSEngine
- `generate.py` — lazy import whisper + config RENDER_DEVICE

**Arquivos a criar (video-generate):**
- `requirements-core.txt`
- `requirements-gpu.txt`
- `requirements-comfyui.txt`
- `src/generators/tts_kokoro.py`
- `src/generators/asr_remote.py`

**Gate de verificação:**
- [ ] `pip install -e .` (core only) funciona sem torch
- [ ] `pip install -e ".[gpu]"` instala torch + TTS + whisper
- [ ] `TTS_ENGINE=xtts` funciona igual ao atual (PC Bruno)
- [ ] `TTS_ENGINE=kokoro` faz chamada HTTP e retorna áudio
- [ ] KokoroTTSEngine retorna `subtitle_mapping` vazio
- [ ] `generate.py` não quebra se whisper não estiver instalado
- [ ] `RENDER_DEVICE=cpu` funciona
- [ ] Testes do video-generate não regrediram
- [ ] Commit feito no repo video-generate
- [ ] Checkpoint salvo

### Fase 4: YOLO CPU + Storage

**Pré-condições:**
- Fase 3 aprovada

**Arquivos a modificar:**
- `src/gpcg/infrastructure/player_detector.py` — ler device do config
- `src/gpcg/config.py` — adicionar storage paths
- `src/gpcg/infrastructure/video_generate_adapter.py` — passar env vars ao subprocess

**Gate de verificação:**
- [ ] `GPCG_YOLO_DEVICE=cpu` funciona
- [ ] `GPCG_YOLO_DEVICE=cuda` funciona igual ao atual
- [ ] `GPCG_WORKER_STORAGE=/var/lib/gpcg` funciona
- [ ] `GPCG_GAMEPLAY_DOWNLOAD_DIR=/data/gpcg/gameplays` funciona
- [ ] Env vars repassadas ao subprocess do video-generate
- [ ] `pytest tests/` não regrediu
- [ ] Commit feito
- [ ] Checkpoint salvo

### Fase 5: WireGuard

**Pré-condições:**
- Fases 0-4 aprovadas
- `vm-flavio` instalado e funcionando
- `my-vps` instalado e funcionando
- Outbound UDP confirmado (já testado)

**Ações (infra, não código):**
- Passo 1: Gerar chave WireGuard na VM (`vm-flavio --shell`)
- Passo 2: Adicionar peer na VPS (`my-vps --shell`)
- Passo 3: Criar `wg0.conf` na VM
- Passo 4: Levantar WireGuard e testar
- Passo 5: Gerar chave SSH na VM e adicionar na VPS
- Passo 6: Instalar `my-vps` na VM
- Passo 7: Testar `scp` e `ssh` da VM para VPS

**Gate de verificação:**
- [ ] `ping 10.0.0.1` da VM responde
- [ ] `ssh root@10.0.0.1` da VM conecta sem senha
- [ ] `scp` da VPS para VM funciona
- [ ] `my-vps --wg-status` na VM mostra handshake recente
- [ ] Reboot da VM → WireGuard levanta automaticamente
- [ ] Checkpoint salvo

### Fase 6: Deploy worker na VM

**Pré-condições:**
- Fase 5 aprovada (WireGuard funcionando)
- Fases 0-4 commitadas e testadas

**Ações:**
- Clonar GPCG na VM
- Instalar video-generate (core only) na VM
- Copiar biblioteca BGM (318MB) via SCP
- Configurar `.env` da VM
- Configurar systemd service
- Primeiro heartbeat

**Gate de verificação:**
- [ ] `gpcg remote-worker` inicia sem erro
- [ ] Worker registra na VPS (aparece em `/workers`)
- [ ] Heartbeat funciona (10s interval)
- [ ] Worker consegue baixar gameplay via SCP
- [ ] Worker consegue baixar voz via SCP
- [ ] Checkpoint salvo

### Fase 7: Testes end-to-end

**Pré-condições:**
- Fase 6 aprovada (worker rodando na VM)

**Testes:**
- 7.1: Mapping na VM (LLM/VLM remoto)
- 7.2: Generation na VM (Kokoro + render CPU)
- 7.3: Resiliência (derrubar VM, job volta para fila)
- 7.4: PC Bruno offline, VM continua processando
- 7.5: Ambos workers processando em paralelo
- 7.6: Voz custom no PC Bruno, Kokoro fixo na VM

**Gate de verificação:**
- [ ] Mapping completo na VM sem erro
- [ ] Generation completa na VM sem erro
- [ ] Vídeo final tem áudio, legendas, BGM
- [ ] Job derrubado volta para `queued`
- [ ] VM processa com PC Bruno offline
- [ ] Ambos workers processam sem conflito
- [ ] Checkpoint final salvo

---

## 5. Estratégia de Subagents

### 5.1 Quando usar subagents

| Cenário | Usar subagent? | Perfil |
|---------|----------------|--------|
| Explorar código antes de modificar | Sim | `subagent_explore` |
| Implementar Fase 3 (video-generate) em paralelo | Sim | `subagent_general` |
| Verificar que nada quebrou após mudança | Sim | `subagent_explore` |
| Implementar Fase 0 (GPCG) | Não — principal | — |
| Configurar WireGuard | Não — interativo | — |
| Testes end-to-end | Não — interativo | — |

### 5.2 Padrão de uso

Antes de modificar um arquivo desconhecido:

```
run_subagent(
  profile="subagent_explore",
  task="Ler {arquivo} e mapear: imports, funções, dependências,
        onde é chamado, o que quebra se mudar {X}. Retornar resumo."
)
```

Para implementar Fase 3 em paralelo:

```
run_subagent(
  profile="subagent_general",
  is_background=true,
  task="Implementar Fase 3 do plano em /home/bruno/Desenvolvimento/brunointegrations/video-generate/.
        Seguir checkpoint-fase-3.md. Commits atômicos por sub-fase."
)
```

### 5.3 Anti-padrão

NUNCA usar subagent para:
- Modificar o mesmo arquivo que a thread principal está editando
- Configurar infra (WireGuard, systemd) — precisa de interação
- Decisões de arquitetura — precisa de contexto completo

---

## 6. Gestão de Contexto

### 6.1 O que manter no contexto

- Plano completo: `/home/bruno/Downloads/plano-vm-flavio-gpcg.md`
- Checkpoint mais recente: `/home/bruno/Downloads/checkpoint-fase-N.md`
- Este documento: `/home/bruno/Downloads/estrategia-execucao-plano.md`
- `git log --oneline -20` dos dois repos

### 6.2 O que NÃO manter no contexto

- Conteúdo completo de arquivos que já foram modificados e commitados
- Output de testes que já passaram (só guardar "passou" no checkpoint)
- Exploração de código que já foi mapeada (só guardar referências no checkpoint)

### 6.3 Protocolo de limpeza de contexto

Ao final de cada sub-fase:

1. Salvar checkpoint com arquivos modificados + commits + testes
2. Remover do contexto o conteúdo dos arquivos já commitados
3. Manter apenas referências (path + linhas) no checkpoint
4. Próxima sub-fase começa com contexto limpo

---

## 7. Git Strategy

### 7.1 Branches

```
main (PC Bruno funcionando)
  │
  ├── feature/multi-worker      (Fases 0-4 no GPCG)
  │     ├── commit: fase-0-job-requeue
  │     ├── commit: fase-0.5-gameplay-sync
  │     ├── commit: fase-1-llm-adapter
  │     ├── commit: fase-2-asr-adapter
  │     └── commit: fase-4-yolo-storage
  │
  └── feature/vm-worker-deploy  (Fase 6, merge depois dos testes)
```

No video-generate:
```
main
  └── feature/optional-deps     (Fase 3)
        ├── commit: split-requirements
        ├── commit: kokoro-tts-engine
        └── commit: lazy-import-whisper
```

### 7.2 Merge para main

Só depois da Fase 7 (testes end-to-end passando).

### 7.3 Rollback

Se algo quebrar:
- `git revert <commit>` do commit específico
- Se quebrar o PC Bruno: `git checkout main` (volta ao estado anterior)
- Video-generate independente: revert separado

---

## 8. Auto-Auditoria

### 8.1 Checklist de auto-auditoria (a cada fase)

Antes de marcar fase como completa:

- [ ] Li o plano completo da fase novamente
- [ ] Verifiquei que TODOS os arquivos listados foram modificados
- [ ] Verifiquei que não adicionei arquivos não listados
- [ ] Rodei a suite de testes completa (não só os novos)
- [ ] Verifiquei que o PC Bruno ainda funciona (worker não quebrou)
- [ ] Commit feito com mensagem descritiva
- [ ] Checkpoint salvo com status atualizado
- [ ] Próxima fase claramente identificada

### 8.2 Sinais de alerta

PARAR imediatamente se:

- Testes existentes quebram e não sei por quê
- Preciso mudar arquivos que não estão no plano
- Uma mudança afeta mais de 5 arquivos não relacionados
- O worker do PC Bruno para de funcionar
- Não consigo commitar (conflito de merge, etc)

### 8.3 Protocolo de parada

Se algo der errado:

1. NÃO continuar tentando aleatoriamente
2. Salvar checkpoint com status "BLOQUEADO"
3. Documentar o problema exato
4. Reverter para último commit bom se necessário
5. Pedir ajuda ao usuário

---

## 9. Mapeamento de Arquivos por Fase

### Fase 0 — GPCG

| Arquivo | Ação | Sub-fase |
|---------|------|----------|
| `src/gpcg/api/worker_routes.py` | Modificar | 0.1 |
| `src/gpcg/config.py` | Modificar | 0.1 |
| `tests/test_job_requeue.py` | Criar | 0.2 |

### Fase 0.5 — GPCG

| Arquivo | Ação | Sub-fase |
|---------|------|----------|
| `src/gpcg/domain/models.py` | Modificar | 0.5.1 |
| `src/gpcg/api/worker_routes.py` | Modificar | 0.5.2 |
| `src/gpcg/worker/remote_worker.py` | Modificar | 0.5.3 |
| `src/gpcg/worker/local_db_sync.py` | Modificar | 0.5.4 |
| `tests/test_gameplay_sync.py` | Criar | 0.5.5 |

### Fase 1 — GPCG

| Arquivo | Ação | Sub-fase |
|---------|------|----------|
| `src/gpcg/config.py` | Modificar | 1.1 |
| `src/gpcg/infrastructure/llm.py` | Modificar | 1.2 |
| `tests/test_llm_litellm.py` | Criar | 1.3 |

### Fase 2 — GPCG

| Arquivo | Ação | Sub-fase |
|---------|------|----------|
| `src/gpcg/config.py` | Modificar | 2.1 |
| `src/gpcg/infrastructure/asr_transcriber.py` | Modificar | 2.2 |
| `src/gpcg/application/gameplay_analyzer.py` | Modificar | 2.3 |
| `tests/test_asr_remote.py` | Criar | 2.4 |

### Fase 3 — video-generate

| Arquivo | Ação | Sub-fase |
|---------|------|----------|
| `requirements.txt` | Modificar | 3.1 |
| `requirements-core.txt` | Criar | 3.1 |
| `requirements-gpu.txt` | Criar | 3.1 |
| `requirements-comfyui.txt` | Criar | 3.1 |
| `pyproject.toml` | Modificar | 3.1 |
| `src/generators/tts_kokoro.py` | Criar | 3.2 |
| `src/generators/tts_factory.py` | Modificar | 3.3 |
| `src/generators/asr_remote.py` | Criar | 3.4 |
| `generate.py` | Modificar | 3.5 |

### Fase 4 — GPCG

| Arquivo | Ação | Sub-fase |
|---------|------|----------|
| `src/gpcg/infrastructure/player_detector.py` | Modificar | 4.1 |
| `src/gpcg/config.py` | Modificar | 4.2 |
| `src/gpcg/infrastructure/video_generate_adapter.py` | Modificar | 4.3 |

### Fase 5 — Infra (sem código)

| Arquivo | Ação | Sub-fase |
|---------|------|----------|
| VPS: `/etc/wireguard/wg0.conf` | Modificar | 5.1 |
| VPS: `/root/.ssh/authorized_keys` | Modificar | 5.2 |
| VM: `/etc/wireguard/wg0.conf` | Criar | 5.3 |
| VM: `~/.ssh/id_ed25519` | Criar | 5.4 |
| VM: `~/my-vps/` | Instalar | 5.5 |

### Fase 6 — VM deploy

| Arquivo | Ação | Sub-fase |
|---------|------|----------|
| VM: `/opt/gpcg/` | Clonar | 6.1 |
| VM: `/opt/video-generate/` | Clonar | 6.2 |
| VM: BGM library | Copiar via SCP | 6.3 |
| VM: `/opt/gpcg/.env` | Criar | 6.4 |
| VM: systemd service | Criar | 6.5 |
| `scripts/setup-worker-vm.sh` | Criar | 6.6 |
| `scripts/gpcg-worker-vm.service` | Criar | 6.6 |

### Fase 7 — Testes (sem código novo)

| Teste | Sub-fase |
|-------|----------|
| Mapping na VM | 7.1 |
| Generation na VM | 7.2 |
| Resiliência | 7.3 |
| PC Bruno offline | 7.4 |
| Paralelo | 7.5 |
| Vozes diferentes | 7.6 |

---

## 10. Ordem de Execução Recomendada

### Sequência principal (GPCG)

```
1. Fase 0  (job requeue)           — 1 dia
2. Fase 0.5 (gameplay sync)        — 2-3 dias
3. Fase 1  (LLM adapter)           — 2-3 dias
4. Fase 2  (ASR adapter)           — 1-2 dias
5. Fase 4  (YOLO + storage)        — 1 dia
```

### Paralelo (video-generate, subagent)

```
P1. Fase 3  (deps split + Kokoro)  — 3-4 dias
```

### Sequência infra (depois do código)

```
6. Fase 5  (WireGuard)             — 1-2 dias
7. Fase 6  (deploy VM)             — 1-2 dias
8. Fase 7  (testes e2e)            — 2-3 dias
```

### Timeline otimizada

```
Dia 1-3:   Fase 0 + Fase 0.5 (GPCG)  ||  Fase 3 (video-generate, subagent)
Dia 4-6:   Fase 1 + Fase 2 (GPCG)    ||  Fase 3 continua
Dia 7:     Fase 4 (GPCG)             ||  Fase 3 finaliza
Dia 8-9:   Fase 5 (WireGuard)
Dia 10-11: Fase 6 (deploy VM)
Dia 12-14: Fase 7 (testes e2e)
```

Total: ~14 dias (vs 15-21 sem paralelização)

---

## 11. Protocolo para Outros Modelos de IA

### 11.1 Como retomar a implementação

Se outro modelo de IA precisar continuar:

1. **Ler este documento** (`estrategia-execucao-plano.md`)
2. **Ler o plano** (`plano-vm-flavio-gpcg.md`)
3. **Ler o checkpoint mais recente** (`checkpoint-fase-N.md`)
4. **Verificar estado do git** nos dois repos:
   ```bash
   cd /home/bruno/Desenvolvimento/brunointegrations/gameplay-content-generator
   git log --oneline -20
   cd /home/bruno/Desenvolvimento/brunointegrations/video-generate
   git log --oneline -20
   ```
5. **Rodar testes** para confirmar que nada quebrou:
   ```bash
   cd /home/bruno/Desenvolvimento/brunointegrations/gameplay-content-generator
   pytest tests/ -x
   ```
6. **Continuar da sub-fase** marcada como "próximo passo" no checkpoint

### 11.2 Regras para outros modelos

- NUNCA pular gates de verificação
- NUNCA modificar arquivos fora da lista da fase atual
- NUNCA fazer commit sem rodar testes
- NUNCA quebrar o worker do PC Bruno
- SEMPRE salvar checkpoint após cada sub-fase
- SEMPRE usar `vm-flavio` para acessar a VM (não SSH direto)
- SEMPRE usar `my-vps` para acessar a VPS (não SSH direto)
- SEMPRE seguir o plano, não inventar arquitetura nova

### 11.3 Sinais de que o modelo se perdeu

- Modifica arquivos que não estão na lista da fase
- Cria abstrações novas não descritas no plano
- Pula testes
- Não salva checkpoint
- Faz commit sem mensagem descritiva
- Quebra testes existentes e continua

Se isso acontecer: parar, reler o plano, reler o checkpoint, voltar ao último commit bom.
