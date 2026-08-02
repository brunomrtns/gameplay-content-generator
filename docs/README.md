# Documentação Técnica — Gameplay Content Generator

> Índice da documentação completa do GPCG. Comece aqui.

## Visão Geral

O GPCG é uma plataforma multi-usuário que automatiza a criação de YouTube
Shorts a partir de gameplays gravados. O pipeline completo vai do upload de
gameplay (via web UI) ao publish no YouTube — com narração TTS, legendas
alinhadas palavra-por-palavra, gameplay de fundo, música, metadados
otimizados, e thumbnail — tudo gerado automaticamente com IA local (Ollama)
e renderizado via `video-generate`.

### Arquitetura: Control Plane + Compute Plane

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

**Princípio chave:** O Control Plane (VPS) cuida da inteligência,
orquestração, web UI e publish. O Compute Plane (PC local com GPU) roda o
processamento pesado (VLM, ASR, TTS, render). O `video-generate` permanece
responsável pela renderização — consumimos seu contrato público via subprocess.

### Stack

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

## Índice da Documentação

### [USAGE.md](USAGE.md) — Guia de Uso

Como usar o sistema no dia-a-dia:

- **Web UI**: as 7 páginas (Dashboard, Login, Content, Automation, Videos, Jobs, Admin)
- **CLI**: todos os 10 comandos `gpcg`
- **API**: exemplos práticos com `curl` para cada endpoint
- **Fluxo completo**: do upload de gameplay ao publish no YouTube
- **Customização de vídeo**: formatos, duração de cena, legendas, voz
- **Upload de voz para TTS**: como clonar sua voz
- **YouTube upload**: como conectar OAuth e publicar

### [ARCHITECTURE.md](ARCHITECTURE.md) — Arquitetura

Detalhes técnicos para desenvolvedores:

- **Control Plane + Compute Plane**: arquitetura split VPS + PC local
- **Pipeline de 12 estágios**: o que cada um faz, em ordem
- **Estágios pós-QA**: metadata_generation + youtube_upload
- **Modelos de dados**: 13 modelos SQLAlchemy e seus relacionamentos
- **Worker registry + job queue**: como workers se registram e consomem jobs
- **File transfer**: upload de gameplay → download pelo worker → processamento → upload de vídeo
- **Resolução de jogo em 3 camadas** (L1/L2/L3)
- **Gameplay Analysis**: cascaded pipeline (coarse → refine → ASR → merge → interesting score)
- **Editorial Pipeline**: EditorialPlanner + ScriptCritic
- **Creative Engine**: Qwen3-14B com 8 estilos
- **Anti-plágio**: 3 camadas de proteção (prompts + n-gram + auto-rewrite)
- **Integração com video-generate**: padrão subprocess + bridge
- **Alinhamento de legendas**: SequenceAligner palavra-por-palavra
- **TTS chunking**: por que chunked + merge FFmpeg
- **Seleção de gameplay**: modo cena com chaining + semantic search
- **Perfis de vídeo customizados**: registro em runtime
- **YouTube upload**: google-integration adapter, OAuth per-user
- **Thumbnail generation**: ffmpeg no VPS + on-demand

### [API.md](API.md) — Referência da API

Todos os 55 endpoints REST documentados, organizados por route file:

- **routes.py** (26 endpoints): games, sources, assets, documents, facts,
  content-plans, scripts, jobs, videos, voices
- **worker_routes.py** (15 endpoints): workers, jobs/claim, jobs/status,
  jobs/result, jobs/data, jobs/sync, gameplays/download, gameplays/events,
  jobs/upload-video
- **automation_routes.py** (8 endpoints): automation, youtube/connect,
  youtube/status, youtube/disconnect, dashboard
- **auth_routes.py** (6 endpoints): sso-redirect, me, logout, users CRUD

### [CONFIGURATION.md](CONFIGURATION.md) — Configuração

Todas as variáveis de ambiente (67+ vars em 13 seções):

- General, Authentication, Gameplay Inbox, Local AI (Ollama)
- video-generate integration, Content defaults, Scene + video customization
- Anti-plagiarism, Creative Engine, Editorial Pipeline
- Gameplay Understanding, Worker API, Remote Worker
- YouTube Upload, Metadata Generation

### [CREATIVE_ENGINE.md](CREATIVE_ENGINE.md) — Creative Engine (Qwen3-14B)

Camada criativa opcional do pipeline:

- O que é, por que Qwen3-14B, como instalar
- Como ativar/desativar, 8 estilos/presets (humor, absurd, sarcastic, etc.)
- Como trocar o modelo, smoke test via CLI (`gpcg creative-test`)
- Performance, fallback, observabilidade
- Integração com Editorial Pipeline (respeita HumorPlan)

### [EDITORIAL_PIPELINE.md](EDITORIAL_PIPELINE.md) — Editorial Pipeline

Camada editorial do pipeline (2 estágios novos):

- **EditorialPlanner**: produz VideoCreativePlan (video_type, central_idea,
  narrative_beats, tone weights, HumorPlan, gameplay_strategy, model_recommendation)
- **ScriptCritic**: avalia scripts em 6 dimensões (structure, naturalness,
  humor, coherence, gameplay, factual_accuracy), até 3 revisões automáticas
- Regra crítica: "REMOVE this passage" > "replace with another joke"
- Integração com Creative Engine (respeita plan) e ScriptService (model override)

### [GAMEPLAY_ANALYSIS.md](GAMEPLAY_ANALYSIS.md) — Gameplay Analysis

Análise semântica automática de gameplays:

- Cascaded pipeline: coarse segments → adaptive refine → ultra refine
- VLM (Qwen3-VL) para descrição de frames
- ASR (faster-whisper) para transcrição de áudio
- YOLO player detection + image enhancement
- Interesting score (activity + VLM assessment)
- Semantic event index (GameplayEvent) para clip selection

### [EDITORIAL_REFACTOR_PLAN.md](EDITORIAL_REFACTOR_PLAN.md) — Plano de Refatoração Editorial (V1)

Plano técnico **aprovado** para evolução da arquitetura editorial do GPCG.
Versão original, preservada como referência. Para a versão consolidada
atual, ver `EDITORIAL_REFACTOR_PLAN_V2.md`.

- Crítica da arquitetura atual (7 gargalos identificados)
- Nova arquitetura: 7 estágios (2 novos: Story Finder, Humanization)
- Novos componentes: Curiosity Scorer, Story Finder, Humanization Pass
- Alterações em 6 componentes existentes (Fact Scoring, Content Planning,
  Editorial Planner, Creative Engine, Script Service, Script Critic)
- Plano incremental em 8 fases, cada uma gated por feature flag
- **Não modificar automaticamente** — ideias novas em EDITORIAL_PRINCIPLES.md

### [EDITORIAL_REFACTOR_PLAN_V2.md](EDITORIAL_REFACTOR_PLAN_V2.md) — Plano de Refatoração Editorial (V2, consolidado)

Versão consolidada do plano após revisão de coerência com o diário de
pesquisa e o manifesto. **Esta é a versão atual para implementação.**

- Alterações mínimas e bem fundamentadas sobre o V1
- Curiosity Scorer: 6 → 5 sub-scores (removidos `comment_potential`,
  `tension`; adicionados `familiarity`, `insight_quality`)
- StoryConcept: 11 → 9 campos (removidos redundantes; adicionados
  `frame`, `is_insight`)
- Editorial Planner: removido `retention_plan` (over-engineering)
- Script Critic v2: "edições cirúrgicas" → "revisão por seção"
- Humanization: adicionada "identificação com a ignorância"
- Fases: 8 → 6 (Fase 6 mergeada na 2, Fase 7 removida)
- Ver `EDITORIAL_CONSOLIDATION_REPORT.md` para justificativa de cada mudança

### [EDITORIAL_CONSOLIDATION_REPORT.md](EDITORIAL_CONSOLIDATION_REPORT.md) — Relatório de Consolidação

Relatório curto da revisão de coerência do V1 à luz dos estudos:

- O que foi confirmado (5 decisões fortalecidas)
- O que perdeu força (7 ideias removidas ou simplificadas)
- O que realmente merece entrar no plano (4 hipóteses promovidas)
- Contradições encontradas e resolvidas (5)
- Simplificações aplicadas (5)
- Hipóteses que permanecem como conhecimento editorial (12)

### [EDITORIAL_EVALUATION.md](EDITORIAL_EVALUATION.md) — Metodologia de Avaliação Editorial

Processo permanente para avaliação de melhorias editoriais. **Não é
implementação — é processo e critérios.**

- Como comparar duas versões de um roteiro (A/B com 3 camadas:
  estrutural, editorial, público)
- 6 métricas humanas que importam (descoberta, curiosidade, voz
  humana, clareza, ritmo, payoff) + factual accuracy como gate
- Como validar novas hipóteses (observação → hipótese → experimento
  → resultado → decisão)
- Formato de registro de experimentos (`docs/editorial_experiments/`)
- 4 filtros para evitar crescimento infinito (evidência,
  proporcionalidade, consistência, irreversibilidade)
- Hierarquia de conceitos: opinião → hipótese → princípio →
  requisito → implementação
- Cadência de avaliação e limpeza periódica
- Aplicação imediata para a Fase 1

### [EDITORIAL_PRINCIPLES.md](EDITORIAL_PRINCIPLES.md) — Princípios Editoriais (Pesquisa)

Documento vivo de pesquisa editorial. **Não é plano de implementação.**

- O que diferencia roteiro correto de viciante
- Padrões que denunciam IA vs. padrões que denunciam humano
- Como grandes canais mantêm retenção (5 princípios)
- Princípios psicológicos: curiosity gap, open loops, pattern interruption,
  identification, anticipation, reciprocity
- Como curiosidade realmente funciona (anatomia: lacuna + relevância + atingibilidade)
- O que faz compartilhar, comentar, assistir até o payoff
- Reward pacing: micro-recompensas ao longo do vídeo
- Crítica ao GPCG sob ótica editorial (7 pontos)
- 10 hipóteses para exploração futura
- Exemplos comparativos (GTA IV Euphoria, Bully química)

### [EDITORIAL_RESEARCH_JOURNAL.md](EDITORIAL_RESEARCH_JOURNAL.md) — Diário de Pesquisa Editorial

Registro do estudo de quatro obras fundamentais sobre comunicação,
narrativa, curiosidade e cognição. **Não é plano de implementação.**

- **Made to Stick** (Heath) — framework SUCCESs, Maldição do Conhecimento,
  Teoria Velcro, curiosity gap vs. surpresa
- **Building a StoryBrand** (Miller) — SB7, espectador como herói,
  transformação vs. informação, stakes, clareza
- **The Psychology of Curiosity** (Loewenstein) — information-gap theory,
  curva invertida U, saciação da curiosidade, insight vs. trivia
- **Thinking, Fast and Slow** (Kahneman) — System 1/2, peak-end rule,
  duration neglect, WYSIATI, framing, loss aversion, focusing illusion
- Síntese transversal: convergências, divergências, descobertas
  transformadoras, 18 hipóteses consolidadas por tema

### [EDITORIAL_MANIFESTO.md](EDITORIAL_MANIFESTO.md) — Manifesto Editorial

A identidade editorial do GPCG. Responde a uma pergunta:
**que tipo de conteúdo o GPCG existe para criar?**

- O GPCG existe para criar **descobertas**, não fatos
- O espectador é o protagonista; o fato é o veículo
- A lacuna antes da resposta; o fato é o payoff, não o começo
- A emoção é o mecanismo; a informação é o pretexto
- O familiar é mais poderoso que o obscuro
- Filtro editorial: "eu assistira isso até o fim?"
- O que recusamos: enciclopédia, apresentador genérico, humor forçado,
  perfeição artificial, fato como fim em si
- A voz: alguém que se importa com detalhes, tem opinião, confia no
  espectador
- O compromisso: 10 princípios que definem o conteúdo
- **Filtro para qualquer decisão técnica ou editorial futura**

---

## Status do Projeto

Todas as fases estão completas e verificadas:

- ✅ **Fase 0**: Skeleton do projeto
- ✅ **Fase 1**: Domínio + ingestão (modelos, parser, FFprobe, watcher, resolver L1/L2/L3)
- ✅ **Fase 2**: Pesquisa (upload de docs, extração de fatos, scoring, planning, script)
- ✅ **Fase 3**: Pipeline de render (VG adapter, seleção de gameplay, render plan builder)
- ✅ **Fase 4**: QA + jobs (QA técnico + IA, auto-reparo, worker loop)
- ✅ **Fase 5**: Web UI (FastAPI + React, 7 páginas)
- ✅ **Fase 6**: Testes (198 passing) + E2E (TTS + render verificados)
- ✅ **Fase 7**: Customização de vídeo (4 formatos, cena, legendas, voz)
- ✅ **Fase 8**: Multi-usuário (BI Identity SSO, data isolation, Automation)
- ✅ **Fase 9**: Gameplay Analysis (VLM + ASR + cascaded pipeline + semantic index)
- ✅ **Fase 10**: Editorial Pipeline (EditorialPlanner + ScriptCritic)
- ✅ **Fase 11**: Creative Engine (Qwen3-14B, 8 estilos)
- ✅ **Fase 12**: YouTube Upload (google-integration, OAuth per-user, metadata LLM)
- ✅ **Fase 13**: Control Plane + Compute Plane (VPS Docker + Remote Worker GPU)
- ✅ **Fase 14**: Subtitle Alignment (SequenceAligner palavra-por-palavra)

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
- Gameplay Analysis: cascaded pipeline + semantic events ✓
- Editorial Pipeline: plan + critic com revisões ✓
- Creative Engine: Qwen3-14B com estilos humor/absurd ✓
- YouTube upload: OAuth + publish via google-integration ✓
- Metadata generation: LLM gera título/descrição/tags ✓
- Subtitle alignment: SequenceAligner corrige erros do Whisper ✓
- Remote Worker: VPS → worker GPU → upload → publish ✓

---

## Links Rápidos

- [README.md](../README.md) — Visão geral + quick start
- [AGENTS.md](../AGENTS.md) — Notas técnicas para agents/desenvolvedores
- [.env.example](../.env.example) — Template de configuração (67+ vars)
