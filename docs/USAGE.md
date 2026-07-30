# Guia de Uso — Gameplay Content Generator

> Como usar o GPCG no dia-a-dia: Web UI, CLI, e API.

---

## Sumário

1. [Fluxo Completo](#fluxo-completo)
2. [Web UI](#web-ui)
3. [CLI](#cli)
4. [API (curl)](#api-curl)
5. [Customização de Vídeo](#customização-de-vídeo)
6. [Upload de Voz para TTS](#upload-de-voz-para-tts)
7. [Curiosity Shorts](#curiosity-shorts)
8. [Deploy](#deploy)

---

## Fluxo Completo

O fluxo típico do início ao fim:

```
1. Registrar jogo(s)          → UI: Jogos | API: POST /games
2. Gravar gameplay            → OBS salva no GAMEPLAY_INBOX_DIR
3. Worker descobre + resolve  → Automático (ou gpcg inbox-scan)
4. Upload de documentos       → UI: Conteúdo | API: POST /documents/upload
5. Extrair fatos              → UI: Conteúdo | API: POST /documents/{id}/extract-facts
6. (Opcional) Upload de voz   → UI: Jobs/Curiosidades | API: POST /voices/upload
7. Criar job                  → UI: Jobs | API: POST /jobs/generate
8. Worker processa            → Automático (9 estágios)
9. Vídeo pronto               → UI: Vídeos | API: GET /videos/{id}/file
```

### Fluxo Curiosity Short (curiosidades gerais)

```
1. Upload de documento geral  → UI: Curiosidades | API: POST /documents/upload (sem game_id)
2. Extrair fatos gerais       → UI: Curiosidades | API: POST /documents/{id}/extract-facts
3. Registrar jogo de fundo    → UI: Jogos (qualquer jogo serve como gameplay)
4. Criar curiosity job        → UI: Curiosidades | API: POST /jobs/curiosity
5. Vídeo pronto               → UI: Vídeos
```

---

## Web UI

A UI web tem 6 páginas, acessíveis em http://localhost:5173 (dev) ou
http://localhost:8787 (produção, após `npm run build`).

### 1. Jogos (`/`)

Registrar e gerenciar jogos.

- **Registrar jogo**: nome canônico + aliases + plataformas
  - Ex: "Bully" com aliases ["Bully Scholarship Edition", "Canis Canem Edit"]
  - Aliases ajudam a resolução L1 (determinística) no inbox
- **Cards de jogos**: mostram contadores (sources, clips, fatos, vídeos)
- **Por que registrar antes?** O inbox watcher usa o registro para resolver
  jogos automaticamente. Sem registro, cai em `needs_review`

### 2. Inbox (`/inbox`)

Gameplays descobertos pelo watcher.

- **Lista de sources**: status, método de resolução, confiança, duração, resolução
- **Status**:
  - `discovered` → acabou de chegar
  - `probing` → rodando FFprobe
  - `ready` → pronto para uso
  - `needs_review` → não conseguiu resolver o jogo automaticamente
  - `duplicate` → hash já existe
- **Ação manual**: para `needs_review`, atribuir jogo manualmente
- **Botão "Escanear"**: força um scan do inbox agora

### 3. Conteúdo (`/content`)

Gestão de conteúdo por jogo.

- **Upload de documentos**: PDF, TXT, MD, DOCX
  - Documentos do jogo específico (game_id selecionado)
  - O LLM extrai fatos reescritos nas próprias palavras (anti-plágio)
- **Extração de fatos**: clica em "Extrair fatos" → LLM processa
  - Fatos recebem scores de qualidade e novidade
  - `used_count` rastreia quantas vezes cada fato foi usado
- **Clips manuais**: criar assets com start/end/label a partir de um source
  - Ex: source de 30min → clip "opening" de 0:00 a 2:30
- **Plans + Scripts**: visualizar plans gerados e scripts com:
  - Roteiro final (pt-BR)
  - Originality score (0-100) + badges
  - Rewrite count (quantas vezes foi reescrito)

### 4. Curiosidades (`/curiosity`)

Curiosity Shorts — curiosidades gerais (NÃO sobre um jogo) com gameplay de fundo.

- **Upload de documentos gerais**: sem game_id (vai para o pool geral)
- **Extração de fatos gerais**: fatos de curiosidades aleatórias
- **Seleção**: escolher um fato específico ou deixar o sistema auto-selecionar
- **Jogo de fundo**: escolher qual jogo fornece o gameplay
- **Customização de vídeo**: formato, duração de cena, legendas, voz
- **Criar job**: dispara o pipeline curiosity_short

### 5. Jobs (`/jobs`)

Monitorar e disparar jobs de geração.

- **Disparar job**: selecionar jogo + customizações → criar job
- **Painel de customização**:
  - Duração de cada cena (segundos)
  - Formato da tela (9:16, 16:9, 1:1, 4:5)
  - Voz da narração (upload + seleção)
  - Avançado: legenda (fonte, tamanho, cor, contorno, posição, caixa)
- **Monitoramento**: status, estágio, progresso %, erros, tentativas
- **Estágios**: content_planning → script → tts → gameplay_selection →
  music_selection → render_plan → render → qa → done

### 6. Vídeos (`/videos`)

Vídeos finalizados.

- **Cards**: thumbnail, duração, resolução, QA score
- **Player inline**: assiste o vídeo direto na UI
- **Download**: link para o arquivo MP4

---

## CLI

O CLI usa Typer. Comando base: `gpcg` (ou `.venv/bin/gpcg`).

### Comandos

```bash
# Inicializar banco de dados (cria todas as tabelas)
gpcg db-init

# Escanear inbox uma vez (descobre + resolve + probe)
gpcg inbox-scan

# Rodar worker (inbox watcher + processador de jobs)
# Roda indefinidamente. Polling a cada GPCG_WORKER_POLL_INTERVAL segundos.
gpcg worker

# Rodar servidor API apenas (sem worker)
gpcg serve

# Rodar em modo dev (API :8787 + frontend :5173 com hot reload)
gpcg dev

# Criar job(s) de geração para um jogo
gpcg generate -g "Bully"           # 1 job
gpcg generate -g "Bully" -n 3      # 3 jobs
gpcg generate -g 5                  # Por ID do jogo
```

### Scripts de conveniência

```bash
./scripts/dev.sh setup    # Setup completo (venv + deps + frontend build)
./scripts/dev.sh db       # Inicializar banco
./scripts/dev.sh run      # API + frontend em dev
./scripts/dev.sh worker   # Worker
./scripts/dev.sh scan     # Inbox scan
```

---

## API (curl)

A API roda em `http://localhost:8787` (ou `GPCG_HOST:GPCG_PORT`).

### Games

```bash
# Listar todos os jogos
curl http://localhost:8787/api/games

# Registrar jogo
curl -X POST http://localhost:8787/api/games \
  -H "Content-Type: application/json" \
  -d '{"canonical_name": "Bully", "aliases": ["Bully Scholarship Edition"], "platforms": ["PC"]}'

# Detalhes de um jogo
curl http://localhost:8787/api/games/1
```

### Sources (Inbox)

```bash
# Listar sources
curl http://localhost:8787/api/sources
curl "http://localhost:8787/api/sources?game_id=1"
curl "http://localhost:8787/api/sources?status=needs_review"

# Atribuir jogo manualmente (needs_review)
curl -X POST http://localhost:8787/api/sources/5/assign-game \
  -H "Content-Type: application/json" \
  -d '{"game_id": 1}'

# Forçar scan do inbox
curl -X POST http://localhost:8787/api/inbox/scan
```

### Assets (Clips)

```bash
# Listar clips de um jogo
curl "http://localhost:8787/api/assets?game_id=1"

# Criar clip manual
curl -X POST http://localhost:8787/api/assets \
  -H "Content-Type: application/json" \
  -d '{"source_id": 1, "start_sec": 0, "end_sec": 150, "label": "opening"}'

# Deletar clip
curl -X DELETE http://localhost:8787/api/assets/3
```

### Documents

```bash
# Upload de documento (específico de um jogo)
curl -X POST http://localhost:8787/api/documents/upload \
  -F "game_id=1" \
  -F "file=@curiosidades_bully.pdf"

# Upload de documento geral (curiosity pool, sem game_id)
curl -X POST http://localhost:8787/api/documents/upload \
  -F "file=@curiosidades_gerais.txt"

# Listar documentos
curl "http://localhost:8787/api/documents?game_id=1"
curl "http://localhost:8787/api/documents?general=true"

# Extrair fatos de um documento
curl -X POST http://localhost:8787/api/documents/5/extract-facts
```

### Facts

```bash
# Listar fatos
curl "http://localhost:8787/api/facts?game_id=1"
curl "http://localhost:8787/api/facts?general=true"
curl "http://localhost:8787/api/facts?category=gameplay"
```

### Jobs

```bash
# Listar jobs
curl http://localhost:8787/api/jobs
curl "http://localhost:8787/api/jobs?status=running"

# Criar job de geração (Game Short)
curl -X POST http://localhost:8787/api/jobs/generate \
  -F "game_id=1" \
  -F "video_format=9:16" \
  -F "scene_duration=7200" \
  -F "voice=narrator.wav" \
  -F "subtitle_color=yellow" \
  -F "subtitle_position=top"

# Criar Curiosity Short
curl -X POST http://localhost:8787/api/jobs/curiosity \
  -F "background_game_id=1" \
  -F "video_format=1:1" \
  -F "scene_duration=7200"

# Com fato específico
curl -X POST http://localhost:8787/api/jobs/curiosity \
  -F "background_game_id=1" \
  -F "fact_id=5"
```

### Videos

```bash
# Listar vídeos
curl http://localhost:8787/api/videos
curl "http://localhost:8787/api/videos?game_id=1"

# Baixar vídeo
curl http://localhost:8787/api/videos/1/file -o video.mp4

# Thumbnail
curl http://localhost:8787/api/videos/1/thumbnail -o thumb.jpg
```

### Voices (TTS)

```bash
# Listar vozes disponíveis
curl http://localhost:8787/api/voices

# Upload de voz
curl -X POST http://localhost:8787/api/voices/upload \
  -F "file=@minha_voz.wav"

# Deletar voz
curl -X DELETE http://localhost:8787/api/voices/minha_voz.wav
```

---

## Customização de Vídeo

Tudo opcional — vazio usa o default do config.

### Duração de Cena (`scene_duration`)

Controla como os clips de gameplay são agrupados em cenas:

| Valor | Comportamento |
|-------|--------------|
| `0` (auto) | Cada clip vira uma cena (comportamento legacy) |
| `10` | Cenas de 10s cada (muitos cortes curtos) |
| `30` | Cenas de 30s cada |
| `7200` (2h) | 1 cena longa cobrindo toda a narração (trecho contínuo aleatório) |

**Chaining**: Se `scene_duration=30` mas o vídeo de gameplay tem só 20s, o
sistema encadeia outro vídeo automaticamente para preencher os 10s restantes.

**Recomendação**: Para Shorts de 60s, use `7200` (1 cena contínua) ou `10`
(muitos cortes). Evite valores entre 30-60 que resultam em poucos cortes.

### Formato de Vídeo (`video_format`)

| Formato | Resolução | Uso |
|---------|-----------|-----|
| `9:16` | 1080×1920 | YouTube Shorts, TikTok, Reels (padrão) |
| `16:9` | 1920×1080 | YouTube tradicional |
| `1:1` | 1080×1080 | Instagram feed |
| `4:5` | 1080×1350 | Instagram Reels feed |

Os perfis são registrados em runtime no video-generate via
`VideoProfileRegistry.register()` — sem modificar o video-generate.

### Legendas

| Setting | Valores | Default |
|---------|---------|---------|
| `subtitle_font` | `DejaVuSans-Bold`, `LiberationSans-Bold`, etc. | Perfil |
| `subtitle_font_size` | Inteiro (48, 60...) | Auto |
| `subtitle_color` | `white`, `yellow`, `cyan`, `red`, `lime` | `white` |
| `subtitle_outline_color` | `black`, `white`, `red` | `black` |
| `subtitle_position` | `top`, `middle`, `bottom` | `bottom` |
| `subtitle_case` | `upper`, `lower`, `none` | `none` |

### Áudio

O áudio final é: **narração TTS + música de fundo**. O áudio do gameplay é
**mutado** (visual only).

---

## Upload de Voz para TTS

O TTS usa XTTS v2 com voice cloning — precisa de um arquivo de referência
(5-30s) da voz a ser clonada.

### Como usar

1. **Gravar voz de referência**: 5-30 segundos de áudio limpo (sem ruído)
   da voz que você quer clonar
2. **Upload**: UI (botão "Upload voz" no painel de customização) ou API:
   ```bash
   curl -X POST http://localhost:8787/api/voices/upload \
     -F "file=@minha_voz.wav"
   ```
3. **Selecionar**: ao criar um job, escolher a voz no dropdown
   - Vazio = default do sistema (`GPCG_TTS_VOICE`, tipicamente `bruno.wav`)

### Formatos aceitos

`.wav`, `.mp3`, `.ogg`, `.flac`, `.m4a`

### Onde são salvas

`data/voices/` (diretório de dados do GPCG)

### Como funciona tecnicamente

O GPCG resolve o caminho absoluto da voz e passa para o `synthesize_tts()` do
adapter, que repassa ao `synthesize(speaker_wav=...)` do video-generate. O
video-generate lê o arquivo diretamente do path — sem cópia.

---

## Curiosity Shorts

Curiosity Shorts são vídeos de curiosidades gerais (NÃO sobre um jogo
específico) com gameplay de fundo de qualquer jogo.

### Diferença vs Game Short

| | Game Short | Curiosity Short |
|---|---|---|
| Fato | Específico do jogo (`Fact.game_id`) | Pool geral (`Fact.game_id=NULL`) |
| Documento | Específico do jogo (`Document.game_id`) | Pool geral (`Document.game_id=NULL`) |
| Gameplay | Do próprio jogo | De qualquer jogo (`background_game_id`) |
| `ContentPlan.game_id` | O jogo | `NULL` |
| Endpoint | `POST /jobs/generate` | `POST /jobs/curiosity` |
| UI | Jobs | Curiosidades |

### Anti-plágio aplica igual

O sistema anti-plágio (n-gram overlap + auto-rewrite) funciona igual para
curiosity shorts — o script é comparado contra os documentos gerais + fatos
extraídos.

---

## Deploy

### Desenvolvimento

```bash
./scripts/dev.sh setup    # One-time setup
./scripts/dev.sh run      # API + frontend com hot reload
./scripts/dev.sh worker   # Worker em outra aba
```

### Produção (systemd)

```bash
# Instalar serviços systemd
./scripts/deploy.sh install

# Iniciar
./scripts/deploy.sh start

# Status
./scripts/deploy.sh status

# Logs
./scripts/deploy.sh logs

# Parar
./scripts/deploy.sh stop

# Desinstalar
./scripts/deploy.sh uninstall
```

Isso instala dois serviços systemd user:
- `gpcg-api.service` — servidor API (porta 8787)
- `gpcg-worker.service` — worker background (inbox + jobs)

O frontend é buildado (`npm run build`) e servido pela própria API FastAPI
em produção.
