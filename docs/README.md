# Documentação Técnica — Gameplay Content Generator

> Índice da documentação completa do GPCG. Comece aqui.

## Visão Geral

O GPCG é uma aplicação local-first que automatiza a criação de YouTube Shorts
a partir de gameplays gravados. O pipeline completo vai da descoberta de
gameplays no inbox até a entrega de um vídeo final com narração TTS, legendas,
gameplay de fundo e música — tudo gerado automaticamente com IA local
(Ollama) e renderizado via `video-generate`.

### Arquitetura em uma imagem

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         GPCG (this project)                             │
│                                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │  Inbox   │→ │  Game    │→ │ Research │→ │ Content  │→ │  Script  │  │
│  │  Watcher │  │ Resolver │  │ (Docs)   │  │  Plan    │  │ + Anti-  │  │
│  │          │  │ L1/L2/L3 │  │ Facts    │  │  (IA)    │  │ Plagiarism│ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
│                                                                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │   TTS    │→ │ Gameplay │→ │  Music   │→ │  Render  │→ │    QA    │  │
│  │ (XTTS)   │  │ Selector │  │ Selector │  │   Plan   │  │ Técnico  │  │
│  │ + Voice  │  │ (scenes) │  │          │  │ + Concat │  │ + IA     │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
│       │                                                          │     │
│       └────────────── subprocess ───────────────────────────────┘     │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ▼
              ┌──────────────────────────────────────┐
              │       video-generate (mature)        │
              │  • TTS (XTTS v2 via ai-media-core)   │
              │  • BGM library (curated)             │
              │  • process_video_request()           │
              │  • FFmpeg render pipeline            │
              │  • VideoProfileRegistry (custom)     │
              └──────────────────────────────────────┘
```

**Princípio chave:** O GPCG cuida da inteligência, orquestração e lógica de
domínio. O `video-generate` permanece responsável pela renderização. NÃO
modificamos o video-generate — consumimos seu contrato público via subprocess.

### Stack

| Camada | Tecnologia |
|--------|-----------|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0, Typer |
| Frontend | React 19, Vite, Tailwind CSS, Radix UI |
| IA | Ollama (llama3.1:8b, gemma3:12b), XTTS v2 |
| Banco | SQLite (SQLAlchemy ORM) |
| Render | video-generate + ai-media-core (subprocess) |
| Mídia | FFmpeg / FFprobe |
| Deploy | systemd user units |

---

## Índice da Documentação

### [USAGE.md](USAGE.md) — Guia de Uso

Como usar o sistema no dia-a-dia:

- **Web UI**: as 6 páginas (Jogos, Inbox, Conteúdo, Curiosidades, Jobs, Vídeos)
- **CLI**: todos os comandos `gpcg`
- **API**: exemplos práticos com `curl` para cada endpoint
- **Fluxo completo**: do upload de gameplay ao vídeo final
- **Customização de vídeo**: formatos, duração de cena, legendas, voz
- **Upload de voz para TTS**: como clonar sua voz

### [ARCHITECTURE.md](ARCHITECTURE.md) — Arquitetura

Detalhes técnicos para desenvolvedores:

- **Pipeline de 9 estágios**: o que cada um faz, em ordem
- **Modelos de dados**: 10 modelos SQLAlchemy e seus relacionamentos
- **Resolução de jogo em 3 camadas** (L1/L2/L3)
- **Anti-plágio**: 3 camadas de proteção (prompts + n-gram + auto-rewrite)
- **Integração com video-generate**: padrão subprocess + bridge
- **TTS chunking**: por que chunked + merge FFmpeg
- **Seleção de gameplay**: modo cena com chaining
- **Perfis de vídeo customizados**: registro em runtime
- **Worker**: inbox watcher + processador de jobs
- **Estrutura de diretórios**: código, dados, testes

### [API.md](API.md) — Referência da API

Todos os endpoints REST documentados:

- Games, Sources, Assets, Documents, Facts
- Content Plans, Scripts, Jobs, Videos, Voices
- Parâmetros, respostas, exemplos

### [CONFIGURATION.md](CONFIGURATION.md) — Configuração

Todas as variáveis de ambiente (`.env`):

- General, Inbox, AI, video-generate, Content, Customization, Anti-plagiarism, Creative Engine, Worker
- Defaults, descrições, exemplos

### [CREATIVE_ENGINE.md](CREATIVE_ENGINE.md) — Creative Engine (Qwen3-14B)

Camada criativa opcional do pipeline:

- O que é, por que Qwen3-14B, como instalar
- Como ativar/desativar, estilos/presets
- Como trocar o modelo, smoke test via CLI
- Performance, fallback, observabilidade

---

## Status do Projeto

Todas as fases estão completas e verificadas:

- ✅ **Fase 0**: Skeleton do projeto
- ✅ **Fase 1**: Domínio + ingestão (modelos, parser, FFprobe, watcher, resolver L1/L2/L3)
- ✅ **Fase 2**: Pesquisa (upload de docs, extração de fatos, scoring, planning, script)
- ✅ **Fase 3**: Pipeline de render (VG adapter, seleção de gameplay, render plan builder)
- ✅ **Fase 4**: QA + jobs (QA técnico + IA, auto-reparo, worker loop)
- ✅ **Fase 5**: Web UI (FastAPI + React, 6 páginas)
- ✅ **Fase 6**: Testes (87 passing) + E2E (TTS + render verificados)
- ✅ **Fase 7**: Customização de vídeo (4 formatos, cena, legendas, voz)

### E2E Verificado

- Ingestão → resolução L1 determinística → criação de asset ✓
- Extração de fatos via Ollama (3 fatos extraídos + scored) ✓
- TTS multi-chunk (10.7s de narração) ✓
- Seleção de música da biblioteca VG ✓
- Render completo via `process_video_request` (10.5s, 1080x1920, h264+aac) ✓
- Curiosity Short: curiosidade geral + gameplay de Bully ✓
- Formato 1:1 (1080x1080) com customizações de legenda ✓
- Anti-plágio: originality score 98.4% (passou na primeira tentativa) ✓
- Upload de voz + seleção por job ✓

---

## Links Rápidos

- [README.md](../README.md) — Visão geral + quick start
- [AGENTS.md](../AGENTS.md) — Notas técnicas para agents/desenvolvedores
- [.env.example](../.env.example) — Template de configuração
