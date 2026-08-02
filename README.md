# Gameplay Content Generator (GPCG)

> Plataforma multi-usuário que transforma gameplays gravados em YouTube Shorts
> automatizados — do upload ao publish, com mínima intervenção humana.

O GPCG descobre gameplays, identifica o jogo, pesquisa conteúdo a partir de
documentos enviados, escreve roteiros originais (com verificação anti-plágio),
sintetiza narração TTS com clonagem de voz, seleciona gameplay e música,
renderiza o vídeo via `video-generate`, faz QA técnico + IA, gera metadados
otimizados (título/descrição/tags), e publica no YouTube — tudo automático.

Parte do ecossistema **Bruno Integrations**.

---

## Arquitetura: Control Plane + Compute Plane

O GPCG usa uma arquitetura split desde a v0.3.0:

- **Control Plane (VPS)** — Web UI, API REST, banco SQLite, orquestração de
  jobs, registry de workers, upload de gameplays, publish no YouTube.
  Roda em Docker com nginx reverse proxy sob `/gpcg/`.
- **Compute Plane (PC local com GPU)** — Worker remoto que conecta ao VPS,
  baixa gameplays, roda processamento pesado (VLM, ASR, TTS, FFmpeg, render),
  e sincroniza resultados de volta. Gerenciado via systemd user service.

```
┌─────────────────────────────────────────────────────────────────┐
│                    Control Plane (VPS)                          │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│  │  Web UI  │  │ FastAPI  │  │  SQLite  │  │  Job     │         │
│  │ (React)  │  │  Routes  │  │  (ORM)   │  │  Queue   │         │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│  │ Worker   │  │ Gameplay │  │ YouTube  │  │  BI      │         │
│  │ Registry │  │ Upload   │  │ Publish  │  │ Identity │         │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘         │
└────────────────────────┬────────────────────────────────────────┘
                         │ REST API (X-Worker-Key auth)
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Compute Plane (PC local GPU)                   │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│  │ Remote   │→ │ Gameplay │→ │ Pipeline │→ │ Upload   │         │
│  │ Worker   │  │ Analyzer │  │ (12 stg) │  │ Results  │         │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│  │ VLM      │  │ ASR      │  │ TTS      │  │ video-   │         │
│  │ (Qwen)   │  │ (Whisper)│  │ (XTTS)   │  │ generate │         │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘         │
└─────────────────────────────────────────────────────────────────┘
```

---

## O que está feito

| Funcionalidade | Status |
|---|---|
| **Multi-usuário** (BI Identity SSO, data isolation per user) | ✅ |
| **Dashboard** (stats agregadas, worker status ao vivo, vídeos recentes) | ✅ |
| **Upload de gameplay via web** (com probing + dedup) | ✅ |
| **Gameplay Analysis** (VLM + ASR + cascaded pipeline + interesting score) | ✅ |
| **Resolução de jogo em 3 camadas** (determinístico → prior → VLM) | ✅ |
| **Upload de documentos** (PDF/TXT/MD/DOCX) + extração de fatos via LLM | ✅ |
| **Scoring de fatos** (qualidade + novidade) + dedup | ✅ |
| **Content planning** (IA escolhe fato + desenha plano) | ✅ |
| **Editorial Pipeline** (EditorialPlanner + ScriptCritic com revisões) | ✅ |
| **Creative Engine** (Qwen3-14B, 8 estilos: humor, absurd, sarcastic, etc.) | ✅ |
| **Geração de script com 3 camadas anti-plágio** | ✅ |
| **TTS com XTTS v2** (chunking + merge via FFmpeg, clonagem de voz) | ✅ |
| **Upload de voz para clonagem TTS** | ✅ |
| **Seleção de gameplay** (modo cena com chaining + semantic search) | ✅ |
| **4 formatos de vídeo** (9:16, 16:9, 1:1, 4:5) | ✅ |
| **Customização de legendas** (fonte, cor, posição, caixa, outline) | ✅ |
| **Duração de cena configurável** (cena longa ou muitos cortes) | ✅ |
| **Alinhamento de legendas palavra-por-palavra** (SequenceAligner + RapidFuzz) | ✅ |
| **Render via video-generate** (subprocess) | ✅ |
| **QA técnico (FFprobe) + QA por IA** | ✅ |
| **Auto-reparo** (retry do estágio afetado) | ✅ |
| **Metadata generation** (LLM gera título/descrição/tags otimizados) | ✅ |
| **YouTube auto-upload** (via google-integration, OAuth per-user) | ✅ |
| **Thumbnail generation** (ffmpeg no VPS + on-demand) | ✅ |
| **Curiosity Shorts** (curiosidades gerais + gameplay de fundo) | ✅ |
| **Web UI** (React + Tailwind, 7 páginas) | ✅ |
| **Remote Worker** (Compute Plane com GPU, systemd) | ✅ |
| **Deploy via Docker + nginx** | ✅ |
| **198 testes** pytest | ✅ |

---

## Quick Start

### Control Plane (VPS)

```bash
# 1. Setup (cria venv, instala deps, builda frontend)
./scripts/dev.sh setup

# 2. Configurar ambiente
cp .env.example .env
# Editar .env: BI_IDENTITY_URL, GPCG_WORKER_API_KEY, etc.

# 3. Inicializar banco
./scripts/dev.sh db

# 4. Rodar em modo dev (API :8787 + frontend :5173)
./scripts/dev.sh run

# 5. Deploy para VPS (Docker + nginx)
./scripts/deploy.sh
```

### Compute Plane (PC local com GPU)

```bash
# 1. Configurar ambiente
cp .env.example .env
# Editar: GPCG_VPS_URL, GPCG_WORKER_ID, GPCG_WORKER_API_KEY,
#         GPCG_WORKER_STORAGE, VIDEO_GENERATE_DIR, OLLAMA_HOST

# 2. Iniciar remote worker
gpcg remote-worker --vps-url https://brunointegrations.com/gpcg \
  --worker-id pc-bruno --api-key <secret>

# 3. Ou via systemd
systemctl --user start gpcg-worker
```

Abrir https://brunointegrations.com/gpcg/ (produção) ou
http://localhost:5173 (dev) para a UI web.

---

## Pré-requisitos

### Control Plane (VPS)
1. **Docker** + docker-compose
2. **nginx** (reverse proxy via trivestia-nginx)
3. **BI Identity Service** (repo irmão) — autenticação SSO via cookie `bi_auth`
4. **google-integration Service** (repo irmão) — OAuth + upload YouTube
5. **Python 3.12+** (dentro do Docker)
6. **Node 22+** para build do frontend

### Compute Plane (PC local com GPU)
1. **Ollama** rodando com os modelos:
   - `llama3.1:8b` (LLM — planejamento, script, fatos, QA, metadata)
   - `gemma3:12b` (VLM — resolução de jogo L3, gameplay analysis)
   - `qwen3:14b` (Creative Engine — opcional)
2. **video-generate** (repo irmão) com venv configurado — TTS, render, legendas
3. **ai-media-core** (repo irmão) — utilidades de TTS e mídia
4. **FFmpeg** + **FFprobe** instalados no sistema
5. **GPU NVIDIA** (CUDA) para VLM, ASR, TTS
6. **Python 3.9+** (venv do video-generate)

---

## Pipeline de Geração (12 estágios)

```
content_planning → editorial_planning → creative_engine → script
→ script_review → tts → gameplay_selection → music_selection
→ render_plan → render → qa → done
```

Estágios adicionais pós-QA:
- `metadata_generation` — LLM gera título/descrição/tags
- `youtube_upload` — publica no YouTube via google-integration

Estágios do worker (mapping):
- `download` → `confirm_download` → `mapping` (GameplayAnalyzer)

---

## CLI

```bash
gpcg db-init                          # Inicializar banco
gpcg inbox-scan                       # Escanear inbox uma vez
gpcg serve                            # Rodar servidor API
gpcg dev                              # API + frontend em modo dev
gpcg generate -g "Bully" -n 3         # Criar 3 jobs para Bully
gpcg remote-worker --vps-url <url>    # Rodar remote worker (Compute Plane)
gpcg worker                           # Worker legacy (inbox + jobs)
gpcg creative-test -t <topic> -s humor # Smoke test do Creative Engine
gpcg analyze-gameplay <file>          # Análise semântica de gameplay
gpcg set-camera-type "Bully" 3rd      # Definir perspectiva de câmera
```

---

## Estrutura do Projeto

```
gameplay-content-generator/
├── src/gpcg/
│   ├── api/              # FastAPI (55 endpoints, 4 route files)
│   │   ├── routes.py              # CRUD: games, sources, documents, facts, jobs, videos, voices
│   │   ├── worker_routes.py       # Worker registry, job queue, file transfer, upload-video
│   │   ├── automation_routes.py   # Automation config, YouTube OAuth, dashboard
│   │   └── auth_routes.py         # BI Identity SSO, user management
│   ├── application/      # 17 serviços (orquestração)
│   │   ├── generation_service.py    # Pipeline principal (12 estágios)
│   │   ├── editorial_planner.py     # VideoCreativePlan (video_type, humor, tone)
│   │   ├── creative_engine.py       # Qwen3-14B (hooks, punchlines, 8 estilos)
│   │   ├── script_critic.py         # Revisão editorial (6 dimensões, até 3 revisões)
│   │   ├── script_service.py        # Script + 3 camadas anti-plágio
│   │   ├── metadata_generator.py    # LLM → título/descrição/tags YouTube
│   │   ├── gameplay_analyzer.py     # VLM + ASR + cascaded pipeline
│   │   ├── gameplay_retriever.py    # Semantic clip selection
│   │   ├── render_plan_builder.py   # Plano de render + concat
│   │   ├── qa_service.py            # QA técnico + IA
│   │   └── ...
│   ├── domain/           # 8 módulos (lógica de domínio pura)
│   │   ├── models.py     # 13 modelos SQLAlchemy + Base
│   │   ├── creative_plan.py   # VideoCreativePlan, ScriptReview dataclasses
│   │   ├── gameplay_events.py # Event analysis dataclasses
│   │   ├── video_profiles.py  # 4 perfis + builder custom
│   │   ├── originality.py     # Anti-plágio (n-gram)
│   │   ├── game_resolver.py   # 3 camadas
│   │   └── ...
│   ├── infrastructure/   # 13 módulos (integrações externas)
│   │   ├── video_generate_adapter.py  # Subprocess + TTS + render
│   │   ├── google_integration_adapter.py  # YouTube upload
│   │   ├── auth.py          # BI Identity SSO
│   │   ├── asr_transcriber.py   # faster-whisper
│   │   ├── vision_analyzer.py   # VLM abstraction
│   │   ├── player_detector.py   # YOLO player detection
│   │   ├── frame_sampler.py     # Adaptive FFmpeg frame extraction
│   │   ├── image_enhancer.py    # Crop/upscale/sharpen for VLM
│   │   ├── llm.py          # Ollama client
│   │   └── ...
│   ├── worker/           # Compute Plane
│   │   ├── remote_worker.py    # RemoteWorker (register, heartbeat, poll, process)
│   │   └── local_db_sync.py    # Populates local SQLite from VPS API
│   ├── cli/              # Typer CLI (10 comandos)
│   └── config.py         # Settings (pydantic-settings, 67+ env vars)
├── frontend/             # React 19 + Vite + Tailwind + Radix
│   └── src/
│       ├── pages/        # 7 páginas (dashboard, login, content, automation, videos, jobs, admin)
│       ├── components/   # layout, ui, video-customization
│       ├── lib/          # api client, utils
│       └── hooks/        # usePoll
├── scripts/              # dev.sh, deploy.sh, gpcg-worker.service
├── data/                 # Runtime (db, jobs, videos, docs, voices)
├── tests/                # 198 testes pytest (15 arquivos)
├── docs/                 # 7 documentos técnicos
├── Dockerfile            # Multi-stage: Node 22 (frontend) + Python 3.12 (backend)
├── docker-compose.prod.yml  # Produção: api + worker, volumes, networks
└── AGENTS.md             # Notas técnicas para desenvolvedores/agents
```

---

## Documentação

| Documento | Conteúdo |
|---|---|
| [docs/README.md](docs/README.md) | Visão técnica geral + índice da documentação |
| [docs/USAGE.md](docs/USAGE.md) | Guia de uso: Web UI, CLI, e exemplos de API |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Arquitetura, pipeline, modelos, integrações |
| [docs/API.md](docs/API.md) | Referência completa de todos os endpoints |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Todas as configurações (67+ env vars) |
| [docs/CREATIVE_ENGINE.md](docs/CREATIVE_ENGINE.md) | Creative Engine (Qwen3-14B, 8 estilos) |
| [docs/EDITORIAL_PIPELINE.md](docs/EDITORIAL_PIPELINE.md) | Editorial Planner + Script Critic |
| [docs/GAMEPLAY_ANALYSIS.md](docs/GAMEPLAY_ANALYSIS.md) | Gameplay semantic analysis (VLM + ASR) |
| [AGENTS.md](AGENTS.md) | Notas técnicas para desenvolvedores/agents |

---

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0, Typer |
| Frontend | React 19, Vite, Tailwind CSS, Radix UI |
| IA / LLM | Ollama (llama3.1:8b, gemma3:12b, qwen3:14b) |
| VLM | Qwen3-VL (gameplay analysis, game resolution L3) |
| ASR | faster-whisper (gameplay), Whisper + WhisperX (legendas) |
| TTS | XTTS v2 via ai-media-core (voice cloning) |
| Banco | SQLite (SQLAlchemy ORM) |
| Render | video-generate + ai-media-core (subprocess) |
| Mídia | FFmpeg / FFprobe |
| Auth | BI Identity SSO (cookie-based) |
| YouTube | google-integration service (OAuth 2.0, BullMQ) |
| Deploy | Docker + docker-compose + nginx reverse proxy |
| Worker | systemd user service (Compute Plane) |

---

## Licença

Projeto privado do ecossistema Bruno Integrations.
