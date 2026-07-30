# Gameplay Content Generator (GPCG)

> Aplicação local-first que transforma gameplays gravados em YouTube Shorts
> automatizados — do inbox ao vídeo final, com mínima intervenção humana.

O GPCG descobre gameplays, identifica o jogo, pesquisa conteúdo a partir de
documentos enviados, escreve roteiros originais (com verificação anti-plágio),
sintetiza narração TTS com clonagem de voz, seleciona gameplay e música,
renderiza o vídeo via `video-generate`, faz QA técnico + IA, e entrega o
Short final pronto para publicar.

Parte do ecossistema **Bruno Integrations**.

---

## O que está feito

| Funcionalidade | Status |
|---|---|
| Inbox watcher (descoberta + FFprobe + dedup) | ✅ |
| Resolução de jogo em 3 camadas (determinístico → prior → VLM) | ✅ |
| Upload de documentos (PDF/TXT/MD/DOCX) + extração de fatos via LLM | ✅ |
| Scoring de fatos (qualidade + novidade) + dedup | ✅ |
| Content planning (IA escolhe fato + desenha plano) | ✅ |
| Geração de script com **3 camadas anti-plágio** | ✅ |
| TTS com XTTS v2 (chunking + merge via FFmpeg) | ✅ |
| **Upload de voz para clonagem TTS** | ✅ |
| Seleção de gameplay (modo cena com chaining) | ✅ |
| **4 formatos de vídeo** (9:16, 16:9, 1:1, 4:5) | ✅ |
| **Customização de legendas** (fonte, cor, posição, caixa) | ✅ |
| **Duração de cena configurável** (cena longa ou muitos cortes) | ✅ |
| Render via `video-generate` (subprocess) | ✅ |
| QA técnico (FFprobe) + QA por IA | ✅ |
| Auto-reparo (retry do estágio afetado) | ✅ |
| Curiosity Shorts (curiosidades gerais + gameplay de fundo) | ✅ |
| Web UI (React + Tailwind, 6 páginas) | ✅ |
| Worker background (inbox + jobs) | ✅ |
| Deploy via systemd | ✅ |
| 87 testes + E2E verificado | ✅ |

---

## Quick Start

```bash
# 1. Setup (cria venv, instala deps, builda frontend)
./scripts/dev.sh setup

# 2. Configurar ambiente
cp .env.example .env
# Editar .env: GAMEPLAY_INBOX_DIR, VIDEO_GENERATE_DIR, etc.

# 3. Inicializar banco
./scripts/dev.sh db

# 4. Rodar em modo dev (API :8787 + frontend :5173)
./scripts/dev.sh run

# 5. (Opcional) Worker em outro terminal
./scripts/dev.sh worker
```

Abrir http://localhost:5173 para a UI web.

---

## Pré-requisitos

1. **Ollama** rodando localmente com os modelos:
   - `llama3.1:8b` (LLM — planejamento, script, fatos, QA)
   - `gemma3:12b` (VLM — resolução de jogo L3)

2. **video-generate** (repo irmão) com venv configurado — responsável por
   TTS, biblioteca de música, e render via FFmpeg

3. **ai-media-core** (repo irmão) — utilidades de TTS e mídia

4. **FFmpeg** instalado no sistema

5. **Node 20+** para o frontend

6. **Python 3.12+** para o backend

---

## Documentação

| Documento | Conteúdo |
|---|---|
| [docs/README.md](docs/README.md) | Visão técnica geral + índice da documentação |
| [docs/USAGE.md](docs/USAGE.md) | Guia de uso: Web UI, CLI, e exemplos de API |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Arquitetura, pipeline, modelos, integrações |
| [docs/API.md](docs/API.md) | Referência completa de todos os endpoints |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Todas as configurações (env vars) |
| [AGENTS.md](AGENTS.md) | Notas técnicas para desenvolvedores/agents |

---

## CLI

```bash
gpcg db-init                          # Inicializar banco
gpcg inbox-scan                       # Escanear inbox uma vez
gpcg worker                           # Rodar worker (inbox + jobs)
gpcg serve                            # Rodar servidor API
gpcg dev                              # API + frontend em modo dev
gpcg generate -g "Bully" -n 3         # Criar 3 jobs para Bully
```

---

## Estrutura do Projeto

```
gameplay-content-generator/
├── src/gpcg/
│   ├── api/              # FastAPI (app.py, routes.py)
│   ├── application/      # Serviços (orquestração)
│   │   ├── generation_service.py    # Pipeline principal (9 estágios)
│   │   ├── ingestion_service.py     # Inbox watcher + FFprobe
│   │   ├── fact_service.py          # Extração + scoring de fatos
│   │   ├── content_planning_service.py
│   │   ├── script_service.py        # Script + originalidade
│   │   ├── gameplay_selector.py     # Seleção por cena + chaining
│   │   ├── render_plan_builder.py   # Plano de render + concat
│   │   ├── qa_service.py            # QA técnico + IA
│   │   └── worker.py                # Worker background
│   ├── domain/           # Lógica de domínio (pura)
│   │   ├── models.py     # 10 modelos SQLAlchemy
│   │   ├── video_profiles.py  # 4 perfis + builder custom
│   │   ├── originality.py     # Anti-plágio (n-gram)
│   │   ├── filename_parser.py
│   │   ├── game_resolver.py   # 3 camadas
│   │   └── game_repository.py
│   ├── infrastructure/   # Integrações externas
│   │   ├── video_generate_adapter.py  # Subprocess + TTS + render
│   │   ├── llm.py          # Ollama client
│   │   ├── media.py        # FFmpeg/FFprobe
│   │   ├── database.py
│   │   └── document_parser.py
│   ├── cli/              # Typer CLI
│   └── config.py         # Settings (pydantic-settings)
├── frontend/             # React 19 + Vite + Tailwind + Radix
│   └── src/
│       ├── pages/        # 6 páginas (games, inbox, content, curiosity, jobs, videos)
│       ├── components/   # layout, ui, video-customization
│       ├── lib/          # api client, utils
│       └── hooks/        # usePoll
├── scripts/              # dev.sh, deploy.sh
├── data/                 # Runtime (db, jobs, videos, docs, voices)
├── tests/                # 87 testes pytest
└── docs/                 # Esta documentação
```

---

## Licença

Projeto privado do ecossistema Bruno Integrations.
