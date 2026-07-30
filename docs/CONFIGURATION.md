# Configuração — Gameplay Content Generator

> Todas as variáveis de ambiente do GPCG. Veja `.env.example` para o template.

---

## Como configurar

```bash
cp .env.example .env
# Editar .env com seus valores
```

As settings são gerenciadas via `pydantic-settings` (`src/gpcg/config.py`).
Todas as variáveis são lidas do ambiente (env vars + `.env` file).

---

## Sumário

1. [General](#general)
2. [Gameplay Inbox](#gameplay-inbox)
3. [Local AI (Ollama)](#local-ai-ollama)
4. [video-generate Integration](#video-generate-integration)
5. [Content Defaults](#content-defaults)
6. [Scene + Video Customization](#scene--video-customization)
7. [Anti-Plagiarism](#anti-plagiarism)
8. [Worker](#worker)

---

## General

Configurações básicas do servidor e dados.

| Variável | Default | Descrição |
|----------|---------|-----------|
| `GPCG_ENV` | `development` | Ambiente (`development`, `production`) |
| `GPCG_HOST` | `127.0.0.1` | Host do servidor API |
| `GPCG_PORT` | `8787` | Porta do servidor API |
| `GPCG_DATA_DIR` | `./data` | Diretório de dados (db, jobs, videos, docs, voices) |
| `GPCG_DB_PATH` | `./data/gpcg.db` | Caminho do banco SQLite |

### Diretórios derivados

Estes são criados automaticamente dentro de `GPCG_DATA_DIR`:

| Diretório | Conteúdo |
|-----------|----------|
| `{data_dir}/docs/` | Documentos enviados (per-game + general) |
| `{data_dir}/inbox/` | Overlay do inbox |
| `{data_dir}/jobs/` | Arquivos temporários por job (TTS, clips, etc.) |
| `{data_dir}/uploads/` | Uploads temporários |
| `{data_dir}/videos/` | Vídeos finalizados |
| `{data_dir}/voices/` | Vozes TTS enviadas via API |

---

## Gameplay Inbox

O inbox watcher monitora um diretório (tipicamente um HD externo) onde o
OBS ou similar salva as gravações de gameplay.

| Variável | Default | Descrição |
|----------|---------|-----------|
| `GAMEPLAY_INBOX_DIR` | `/media/bruno/ToshibaHD` | Diretório a monitorar |
| `GPCG_INBOX_POLL_INTERVAL` | `30` | Intervalo de polling (segundos) |
| `GPCG_INBOX_STABLE_SECONDS` | `10` | Tempo de estabilidade (tamanho não muda) antes de processar |
| `GPCG_INBOX_MIN_SIZE_MB` | `5` | Tamanho mínimo em MB (ignora arquivos pequenos) |

### Como funciona

1. Worker polla `GAMEPLAY_INBOX_DIR` a cada `GPCG_INBOX_POLL_INTERVAL` segundos
2. Detecta arquivos novos (`.mp4`, `.mkv`, `.avi`, `.mov`)
3. Espera `GPCG_INBOX_STABLE_SECONDS` segundos com tamanho estável (gravação finalizada)
4. Se tamanho ≥ `GPCG_INBOX_MIN_SIZE_MB` → ingere
5. Ingestão: FFprobe → hash (dedup) → filename parse → game resolution

---

## Local AI (Ollama)

O GPCG usa Ollama para LLM (texto) e VLM (visão).

| Variável | Default | Descrição |
|----------|---------|-----------|
| `OLLAMA_HOST` | `http://localhost:11434` | Endpoint do Ollama |
| `GPCG_LLM_MODEL` | `llama3.1:8b` | Modelo LLM (planejamento, script, fatos, QA) |
| `GPCG_VLM_MODEL` | `gemma3:12b` | Modelo VLM (resolução de jogo L3) |
| `GPCG_LLM_TIMEOUT` | `180` | Timeout do LLM em segundos |

### Modelos necessários

```bash
ollama pull llama3.1:8b    # LLM principal
ollama pull gemma3:12b     # VLM para resolução L3
```

### Uso por estágio

| Estágio | Modelo |
|---------|--------|
| Content Planning | LLM |
| Script (draft + optimize + rewrite) | LLM |
| Fact Extraction | LLM |
| Fact Scoring | LLM |
| QA (qualidade do script) | LLM |
| Game Resolution L3 | VLM |

---

## video-generate Integration

O GPCG não renderiza vídeos diretamente — delega ao `video-generate` via
subprocess.

| Variável | Default | Descrição |
|----------|---------|-----------|
| `VIDEO_GENERATE_DIR` | `/home/bruno/.../video-generate` | Diretório raiz do video-generate |
| `AI_MEDIA_CORE_DIR` | `/home/bruno/.../ai-media-core/src` | Source root do ai-media-core |
| `VIDEO_GENERATE_PYTHON` | `{VG_DIR}/.venv/bin/python` | Python do venv do VG |
| `GPCG_TTS_VOICE` | `public/voices/bruno.wav` | Voz TTS default (relativo ao VG_DIR) |
| `GPCG_TTS_LANGUAGE` | `pt` | Idioma do TTS |
| `GPCG_RENDER_TIMEOUT` | `3600` | Timeout do subprocess de render (segundos) |

### Sobre `GPCG_TTS_VOICE`

- Caminho relativo ao `VIDEO_GENERATE_DIR`
- Pode ser sobrescrito por job via parâmetro `voice` (upload de voz)
- Se o arquivo não existe, o adapter loga warning e usa o que o VG tiver

### Sobre `GPCG_TTS_LANGUAGE`

- `pt` = português
- XTTS v2 suporta: `pt`, `en`, `es`, `fr`, `it`, `de`, etc.

---

## Content Defaults

Defaults para geração de conteúdo (podem ser sobrescritos por job).

| Variável | Default | Descrição |
|----------|---------|-----------|
| `GPCG_DEFAULT_FORMAT` | `youtube_short` | Formato de conteúdo |
| `GPCG_DEFAULT_TARGET_DURATION` | `60` | Duração alvo em segundos |
| `GPCG_DEFAULT_VIDEO_PROFILE` | `reel_9_16` | Perfil de vídeo default do VG |
| `GPCG_NARRATION_MIN_CHARS` | `800` | Mínimo de chars da narração |
| `GPCG_NARRATION_MAX_CHARS` | `1000` | Máximo de chars da narração |
| `GPCG_MAX_REPAIR_RETRIES` | `2` | Tentativas de auto-reparo no QA |

### Sobre narração

- 800-1000 chars ≈ 55-65 segundos de TTS em pt-BR
- Para vídeos mais longos, aumentar `GPCG_NARRATION_MAX_CHARS`
- Para Shorts, manter em ~1000 (YouTube Shorts tem limite de 60s)

---

## Scene + Video Customization

Defaults de customização de vídeo (sobrescritos por job via API/UI).

| Variável | Default | Descrição |
|----------|---------|-----------|
| `GPCG_SCENE_DURATION` | `0` | Duração de cada cena (0=auto, uma cena por clip) |
| `GPCG_VIDEO_FORMAT` | `9:16` | Formato: `9:16`, `16:9`, `1:1`, `4:5` |
| `GPCG_SUBTITLE_FONT` | `""` | Fonte da legenda (vazio = default do perfil) |
| `GPCG_SUBTITLE_FONT_SIZE` | `0` | Tamanho da fonte (0=auto) |
| `GPCG_SUBTITLE_COLOR` | `""` | Cor do texto (vazio = default) |
| `GPCG_SUBTITLE_OUTLINE_COLOR` | `""` | Cor do contorno |
| `GPCG_SUBTITLE_POSITION` | `""` | Posição: `top`, `middle`, `bottom` |
| `GPCG_SUBTITLE_CASE` | `""` | Caixa: `upper`, `lower`, `none` |

### Prioridade

Params do job (API/UI) > env vars > defaults do perfil do VG

### Exemplo de configuração

Para default quadrado com legendas amarelas no topo:

```env
GPCG_VIDEO_FORMAT=1:1
GPCG_SUBTITLE_COLOR=yellow
GPCG_SUBTITLE_POSITION=top
GPCG_SUBTITLE_CASE=upper
GPCG_SCENE_DURATION=7200
```

---

## Anti-Plagiarism

Configuração do sistema anti-plágio (3 camadas).

| Variável | Default | Descrição |
|----------|---------|-----------|
| `GPCG_MAX_ORIGINALITY_REWRITES` | `3` | Máximo de rewrites automáticos |
| `GPCG_ORIGINALITY_THRESHOLD` | `70.0` | Score mínimo (0-100). Abaixo → rewrite |
| `GPCG_ORIGINALITY_NGRAM_SIZE` | `5` | Tamanho do n-gram em palavras |

### Como funciona

1. Após gerar o script, calcula `originality_score = 100 * (1 - max_overlap)`
2. Se `score < GPCG_ORIGINALITY_THRESHOLD` → rewrite automático
3. Repete até `GPCG_MAX_ORIGINALITY_REWRITES` vezes
4. Métricas persistidas em `Script.originality_score` + `originality_report`

### Ajustando

- **Mais rigoroso**: `GPCG_ORIGINALITY_THRESHOLD=80` + `GPCG_ORIGINALITY_NGRAM_SIZE=4`
- **Menos rigoroso**: `GPCG_ORIGINALITY_THRESHOLD=60` + `GPCG_ORIGINALITY_NGRAM_SIZE=6`
- **Desativar rewrite**: `GPCG_MAX_ORIGINALITY_REWRITES=0` (apenas verifica, não reescreve)

### Sobre n-gram size

- `5` (default): bom para pt-BR, detecta frases de 5 palavras iguais
- `4`: mais sensível (detecta frases mais curtas)
- `6`: menos sensível (só detecta frases longas iguais)

---

## Worker

Configuração do worker background.

| Variável | Default | Descrição |
|----------|---------|-----------|
| `GPCG_WORKER_POLL_INTERVAL` | `5` | Intervalo de polling de jobs (segundos) |
| `GPCG_WORKER_CONCURRENCY` | `1` | Número de jobs processados simultaneamente |

### Sobre concorrência

- `1` (default): um job por vez (recomendado — render usa GPU)
- `2+`: processa múltiplos jobs em paralelo (cuidado com GPU memory)
- O inbox watcher roda independente da concorrência de jobs

---

## Exemplo Completo

`.env` para uma configuração típica:

```env
# General
GPCG_ENV=production
GPCG_HOST=0.0.0.0
GPCG_PORT=8787
GPCG_DATA_DIR=/home/bruno/Desenvolvimento/brunointegrations/gameplay-content-generator/data

# Inbox
GAMEPLAY_INBOX_DIR=/media/bruno/ToshibaHD
GPCG_INBOX_POLL_INTERVAL=60
GPCG_INBOX_STABLE_SECONDS=30
GPCG_INBOX_MIN_SIZE_MB=10

# AI
OLLAMA_HOST=http://localhost:11434
GPCG_LLM_MODEL=llama3.1:8b
GPCG_VLM_MODEL=gemma3:12b

# video-generate
VIDEO_GENERATE_DIR=/home/bruno/Desenvolvimento/brunointegrations/video-generate
AI_MEDIA_CORE_DIR=/home/bruno/Desenvolvimento/brunointegrations/ai-media-core/src
VIDEO_GENERATE_PYTHON=/home/bruno/Desenvolvimento/brunointegrations/video-generate/.venv/bin/python
GPCG_TTS_VOICE=public/voices/bruno.wav
GPCG_TTS_LANGUAGE=pt

# Content
GPCG_DEFAULT_FORMAT=youtube_short
GPCG_DEFAULT_TARGET_DURATION=60
GPCG_NARRATION_MIN_CHARS=800
GPCG_NARRATION_MAX_CHARS=1000

# Customization defaults
GPCG_SCENE_DURATION=7200
GPCG_VIDEO_FORMAT=9:16

# Anti-plagiarism
GPCG_ORIGINALITY_THRESHOLD=70.0
GPCG_MAX_ORIGINALITY_REWRITES=3

# Worker
GPCG_WORKER_POLL_INTERVAL=5
GPCG_WORKER_CONCURRENCY=1
```
