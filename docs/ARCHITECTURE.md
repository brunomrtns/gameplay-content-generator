# Arquitetura — Gameplay Content Generator

> Detalhes técnicos para desenvolvedores: pipeline, modelos, integrações,
> padrões de design.

---

## Sumário

1. [Pipeline de Geração](#pipeline-de-geração)
2. [Modelos de Dados](#modelos-de-dados)
3. [Resolução de Jogo (3 Camadas)](#resolução-de-jogo-3-camadas)
4. [Anti-Plágio (3 Camadas)](#anti-plágio-3-camadas)
5. [Integração com video-generate](#integração-com-video-generate)
6. [TTS com Chunking](#tts-com-chunking)
7. [Seleção de Gameplay (Scene-based)](#seleção-de-gameplay-scene-based)
8. [Perfis de Vídeo Customizados](#perfis-de-vídeo-customizados)
9. [Worker](#worker)
10. [Estrutura de Diretórios](#estrutura-de-diretórios)
11. [Padrões de Design](#padrões-de-design)

---

## Pipeline de Geração

O pipeline de geração de vídeo tem 9 estágios, orquestrados pelo
`GenerationService` (`src/gpcg/application/generation_service.py`).

```
content_planning → script → tts → gameplay_selection → music_selection
    → render_plan → render → qa → done
```

### Estágios em detalhe

#### 1. content_planning
- **Arquivo**: `content_planning_service.py`
- **O que faz**: LLM escolhe o melhor fato e desenha o plano de conteúdo
- **Output**: `ContentPlan` com topic, hook, tone, energy, music_mood, visual_strategy
- **Para curiosity shorts**: fato do pool geral (`Fact.game_id=NULL`)

#### 2. script
- **Arquivo**: `script_service.py`
- **O que faz**:
  1. Gera draft do roteiro (pt-BR, ~800-1000 chars para ~60s)
  2. Otimiza (LLM refina hook, pacing, CTA)
  3. **Verificação de originalidade** (n-gram overlap vs fontes)
  4. Se score < threshold → auto-rewrite (até 3x)
  5. Salva `Script` com `originality_score`, `originality_report`, `rewrite_count`

#### 3. tts
- **Arquivo**: `video_generate_adapter.py` → `synthesize_tts()`
- **O que faz**:
  1. Chunka o texto via `prepare_commercial_chunks` (ai-media-core)
  2. Sintetiza cada chunk via `synthesize()` (XTTS v2)
  3. Merge dos WAVs via FFmpeg (evita bug do `wav_utils`)
  4. Gera `subtitle_mapping` (timestamps por chunk)
- **Voz**: per-job override (`voice_path`) ou config default (`GPCG_TTS_VOICE`)
- **Output**: `narration.wav` + duração + subtitle_mapping

#### 4. gameplay_selection
- **Arquivo**: `gameplay_selector.py`
- **O que faz**: Seleciona clips de gameplay baseado em `scene_duration`
  - `scene_duration=0` (auto): cada asset vira uma cena
  - `scene_duration>0`: modo scene-based (ver [Seleção de Gameplay](#seleção-de-gameplay-scene-based))
- **Algoritmo**: weighted random (prefere `used_count` baixo, evita mesmo source consecutivo)
- **Output**: lista de `SelectedClip` (source, start, end, duration)

#### 5. music_selection
- **Arquivo**: `video_generate_adapter.py`
- **O que faz**: Chama `BGMSelector.select()` do video-generate
- **Output**: path da música de fundo

#### 6. render_plan
- **Arquivo**: `render_plan_builder.py`
- **O que faz**:
  1. Extrai cada clip como `scene_NNN.mp4` (FFmpeg)
  2. Se chaining necessário: concatena múltiplos clips com FFmpeg concat demuxer
  3. Aplica scale/crop para resolução alvo (baseado no `video_format`)
  4. Constrói `scene_timeline` (lista de cenas com paths + durações)
  5. Monta `request_data` (JSON com tudo que o video-generate precisa)
  6. Se customizações ativas: anexa `_gpcg_custom_profile` ao request_data
- **Output**: `request_data` pronto para o render

#### 7. render
- **Arquivo**: `video_generate_adapter.py` → `render_video()`
- **O que faz**:
  1. Salva `request_data` em arquivo JSON temp
  2. Gera script Python que:
     - Seta `sys.path` (VG + ai-media-core)
     - Carrega `.env` do VG
     - **Registra perfil customizado** se houver (`VideoProfileRegistry.register()`)
     - Chama `process_video_request(request_data)`
     - Escreve resultado via `gpcg_bridge.write_result()`
  3. Roda como subprocess com o Python do venv do VG
  4. Lê resultado do JSON sidecar
- **Output**: arquivo MP4 final

#### 8. qa
- **Arquivo**: `qa_service.py`
- **O que faz**:
  1. **QA técnico** (FFprobe): duração, aspect ratio, codec, áudio
  2. **QA por IA** (LLM): qualidade do script, hook, pacing, coerência
  3. Se falhar → auto-reparo (retry do estágio afetado, até `GPCG_MAX_REPAIR_RETRIES`)
- **Output**: `Video` com `qa_score`, `qa_report`, `status` (qa_passed/qa_failed)

#### 9. done
- Marca job como `completed`
- Limpa arquivos temporários do job
- Atualiza `Video.status` para `ready`

### Auto-Reparo

Se o QA falhar, o sistema identifica o estágio afetado e refaz a partir dele:
- Script ruim → refaz `script`
- Áudio ruim → refaz `tts`
- Render inválido → refaz `render`
- Até `GPCG_MAX_REPAIR_RETRIES` (default: 2)

---

## Modelos de Dados

10 modelos SQLAlchemy em `src/gpcg/domain/models.py`.

### Enums

```python
IngestionStatus: discovered | probing | ready | duplicate | error | needs_review
GameResolutionMethod: deterministic | prior | vlm | manual | unknown
FactVerification: unverified | verified | disputed
JobType: generate_short | curiosity_short | ingest | extract_facts | re_render
JobStatus: queued | running | paused | completed | failed | retrying | cancelled
JobStage: ingest | game_resolution | extract_facts | content_planning | script
         | tts | gameplay_selection | music_selection | render_plan | render
         | qa | output | done
VideoStatus: pending | ready | qa_passed | qa_failed | published
ScriptStatus: draft | optimized | final | rejected
```

### Game
```python
- id, canonical_name (unique), aliases (JSON), platforms (JSON)
- capture_sources (JSON), metadata_json (JSON)
- created_at, updated_at
- relationships: sources, facts, documents, content_plans
```

### GameplaySource
```python
- id, game_id (nullable), file_path, filename, file_hash (unique)
- file_size, capture_source, recorded_at
- duration, width, height, fps, codec, has_audio
- ingestion_status, resolution_method, resolution_confidence, resolution_notes
- created_at, updated_at
- relationships: game, assets
```

### GameplayAsset
```python
- id, source_id, label, start_sec, end_sec, duration
- used_count, metadata_json, created_at
- relationships: source
```

### Document
```python
- id, game_id (nullable — NULL = pool geral para curiosity shorts)
- filename, file_path, file_type, file_size, text_extracted
- facts_extracted, created_at
- relationships: game
```

### Fact
```python
- id, game_id (nullable — NULL = pool geral), document_id (nullable)
- category, claim, source_ref, verification
- quality_score, novelty_score, used_count, metadata_json
- created_at
- relationships: game
```

### ContentPlan
```python
- id, game_id (nullable), fact_id (nullable)
- background_game_id (nullable — para curiosity shorts)
- format, target_duration
- topic, hook, tone, energy, music_mood, visual_strategy
- metadata_json, created_at
- relationships: game, background_game, scripts, videos
```

### Script
```python
- id, content_plan_id, draft, optimized, final
- status, char_count
- originality_score (nullable), originality_report (JSON), rewrite_count
- created_at
- relationships: content_plan
```

### Job
```python
- id, job_uuid (unique), type, game_id (nullable), content_plan_id (nullable)
- status, stage, progress, attempts, max_attempts, error
- artifacts (JSON — guarda scene_duration, video_format, voice_path, etc.)
- created_at, started_at, updated_at, completed_at
```

### Video
```python
- id, job_id (nullable), content_plan_id (nullable), game_id (nullable)
- file_path, duration, width, height
- qa_score, qa_report (JSON), status, thumbnail_path
- created_at
- relationships: content_plan
```

### Diagrama de Relacionamentos

```
Game ─┬─ GameplaySource ──── GameplayAsset
      ├─ Document ──── Fact
      └─ ContentPlan ─┬─ Script
                      └─ Video ──── Job

ContentPlan.background_game_id → Game (para curiosity shorts)
Job.content_plan_id → ContentPlan
Job.artifacts → {scene_duration, video_format, voice_path, subtitle_config, ...}
```

---

## Resolução de Jogo (3 Camadas)

Quando um gameplay chega no inbox, o sistema precisa identificar qual jogo é.
Três camadas em ordem de confiança:

### L1 — Determinístico (confiança: 0.95)
- **Arquivo**: `game_resolver.py`
- **Como**: Parse do filename + match contra alias registry
- Ex: `Bully_Scholarship_Edition_2024-01-15.mp4` → match "Bully Scholarship Edition" (alias de "Bully")
- **100% determinístico** — sem IA

### L2 — Prior (confiança: 0.5)
- **Como**: `capture_source` (ex: nome do OBS scene) → associação histórica
- Se o capture_source "Gameplay PC" sempre foi Bully, assume Bully
- **Heurística** baseada em histórico

### L3 — VLM (confiança: variável)
- **Como**: Extrai um frame do vídeo → envia para `gemma3:12b` (VLM)
- O VLM analisa a imagem e sugere o jogo
- **IA multimodal**

### Fallback — needs_review (confiança: 0.3)
- Se todas as camadas falham → status `needs_review`
- Usuário atribui manualmente via UI/API

---

## Anti-Plágio (3 Camadas)

Como o sistema recebe **documentos de terceiros**, precisa garantir que o
output seja original.

### Camada 1 — Prompts
- **Arquivo**: `fact_service.py`, `script_service.py`
- Os prompts de extração de fatos e geração de script explicitamente instruem
  o LLM a:
  - NUNCA copiar verbatim
  - Sempre reescrever nas próprias palavras
  - Usar sinônimos, mudar estrutura de frases, reframe narrativo

### Camada 2 — Verificação n-gram
- **Arquivo**: `originality.py`
- **Como funciona**:
  1. Normaliza texto (lowercase, strip accents, remove punctuation)
  2. Constrói sets de n-grams (5 palavras, configurável)
  3. Compara script vs:
     - Todos os documentos fonte do jogo
     - Todos os claims de fatos extraídos (output intermediário do LLM)
  4. Calcula fração de n-grams do script que aparecem em alguma fonte
  5. `originality_score = 100 * (1 - max_overlap)`
- **Determinístico** — não depende de IA

### Camada 3 — Auto-rewrite
- Se `originality_score < threshold` (default: 70):
  1. Mostra ao LLM exatamente quais frases matched
  2. Instrução dedicada de rewrite completo
  3. Re-verifica originalidade
  4. Repete até `GPCG_MAX_ORIGINALITY_REWRITES` (default: 3)

### Métricas Persistidas

Cada `Script` guarda:
- `originality_score` (0-100, maior = mais original)
- `originality_report` (JSON: overlap fraction, matched source, longest matches, threshold, is_original)
- `rewrite_count` (quantos auto-rewrites foram feitos)

Visível na UI (Content page) como badges.

---

## Integração com video-generate

**Princípio**: NUNCA importar video-generate diretamente no processo do GPCG.
Sempre via subprocess.

### Por que subprocess?
1. VG tem seu próprio venv com deps diferentes (PyTorch, XTTS, etc.)
2. VG carrega modelos GPU pesados — não queremos no processo da API
3. Isolamento: crash no VG não derruba o GPCG
4. Mesmo padrão de `videoclip-generator` e `trivestia-course-generator`

### Como funciona

**Arquivo**: `src/gpcg/infrastructure/video_generate_adapter.py`

1. GPCG gera um script Python dinâmico que:
   - Seta `sys.path` para incluir VG + ai-media-core
   - Carrega `.env` do VG via `python-dotenv`
   - (Opcional) Registra perfil customizado via `VideoProfileRegistry.register()`
   - Importa funções públicas do VG (`synthesize`, `BGMSelector.select`, `process_video_request`)
   - Chama a função apropriada
   - Escreve resultado via `gpcg_bridge.write_result()`

2. Script salvo em arquivo temp e executado com:
   - `python`: venv do VG (`VIDEO_GENERATE_PYTHON`)
   - `cwd`: diretório do VG
   - `env`: PYTHONPATH inclui bridge dir

3. Resultado passado via **JSON sidecar file** (não stdout — prints do VG são noisy)

### Funções consumidas do VG

| Função | Uso |
|--------|-----|
| `synthesize(text, out_path, speaker_wav, language)` | TTS por chunk |
| `BGMSelector.select(...)` | Seleção de música |
| `process_video_request(request_data)` | Render final |
| `VideoProfileRegistry.register(profile)` | Registrar perfil custom |
| `prepare_commercial_chunks(text, ...)` | Chunking de texto (via ai-media-core) |

---

## TTS com Chunking

### Problema

O XTTS v2 tem um bug conhecido: quando sintetiza textos longos em chunks, os
chunks podem ter **sample rates diferentes**. O `wav_utils.smart_merge_wavs`
do ai-media-core não lida bem com isso — áudio glitchado/final cortado.

### Solução

1. Chunka o texto com `prepare_commercial_chunks` (mesma lógica do VG)
   - Params: `max_chars=170`, `max_words=24`, `min_words=6`
2. Sintetiza cada chunk separadamente → `chunk_NNN.wav`
3. **Merge com FFmpeg** (não wav_utils) — FFmpeg resample automaticamente
4. Gera `subtitle_mapping` com timestamps por chunk

**Arquivo**: `video_generate_adapter.py` → `synthesize_tts()`

---

## Seleção de Gameplay (Scene-based)

**Arquivo**: `src/gpcg/application/gameplay_selector.py`

### Modo Auto (scene_duration=0)
- Cada `GameplayAsset` vira uma cena
- Duração da cena = duração do asset
- Weighted random: prefere `used_count` baixo, evita mesmo source consecutivo

### Modo Scene-based (scene_duration>0)

Para cada cena necessária (até cobrir a duração da narração):

1. **Pega um vídeo aleatório** + ponto de início aleatório
2. **Se o segmento cabe**: take contíguo sub-clip
   - Ex: vídeo de 30min, scene_duration=60s → pega de 12:34 a 13:34
3. **Se o vídeo é mais curto que scene_duration**: **chaining**
   - Pega o vídeo inteiro (ex: 20s)
   - Faltam 10s → pega outro vídeo aleatório para preencher
   - RenderPlanBuilder concatena com FFmpeg concat demuxer

### Caso especial: scene_duration >= duração total

Se `scene_duration=7200` (2h) e a narração tem 60s:
- 1 cena de 60s
- Pega um trecho contínuo aleatório do gameplay (ex: 5:37 a 6:37)
- Resultado: vídeo de 60s com um trecho contínuo (sem cortes)

---

## Perfis de Vídeo Customizados

**Arquivo**: `src/gpcg/domain/video_profiles.py`

### 4 perfis padrão

| Nome | Format | Resolução |
|------|--------|-----------|
| `gpcg_9_16` | 9:16 | 1080×1920 |
| `gpcg_16_9` | 16:9 | 1920×1080 |
| `gpcg_1_1` | 1:1 | 1080×1080 |
| `gpcg_4_5` | 4:5 | 1080×1350 |

### Builder de perfil custom

`build_custom_profile()` cria um `VideoProfile` com:
- Dimensões do formato escolhido
- Subtitle overrides (font, size, color, outline, position, case)
- Safe area + provider hints

### Registro em runtime

O `VideoGenerateAdapter` injeta código de registro no script do subprocess:

```python
# No script gerado:
from src.profiles.profile_registry import VideoProfileRegistry
_profile = VideoProfile(name="gpcg_1_1", ...)
VideoProfileRegistry.register(_profile)
# Agora process_video_request pode usar profile="gpcg_1_1"
```

**Detalhe técnico crítico**: o código de registro deve ser indentado a 12
espaços para combinar com o template do script e o `textwrap.dedent` funcionar.

### Fluxo completo

```
API params (video_format, subtitle_*) 
  → job.artifacts 
  → GenerationService lê artifacts 
  → RenderPlanBuilder.build() cria custom_profile 
  → request_data["_gpcg_custom_profile"] = {...}
  → VideoGenerateAdapter.render_video() pops _gpcg_custom_profile
  → injeta código de registro no script subprocess
  → VideoProfileRegistry.register() no subprocess
  → process_video_request usa o perfil registrado
```

---

## Worker

**Arquivo**: `src/gpcg/application/worker.py`

O worker roda duas funções em loop:

### 1. Inbox Watcher
- Polla `GAMEPLAY_INBOX_DIR` a cada `GPCG_INBOX_POLL_INTERVAL` segundos
- Detecta novos arquivos
- Espera estabilidade (`GPCG_INBOX_STABLE_SECONDS` — tamanho não muda)
- Filtra por tamanho mínimo (`GPCG_INBOX_MIN_SIZE_MB`)
- Chama `IngestionService.ingest()`:
  - FFprobe (duração, resolução, codec, áudio)
  - Hash SHA-256 (dedup)
  - Filename parse
  - Game resolution (L1 → L2 → L3 → fallback)
  - Cria `GameplaySource` no banco

### 2. Job Processor
- Polla jobs `queued` a cada `GPCG_WORKER_POLL_INTERVAL` segundos
- Pegar próximo job queued → marcar `running` → rodar pipeline
- Concorrência: `GPCG_WORKER_CONCURRENCY` (default: 1)

### Estados do Job

```
queued → running → (stages) → completed
                  ↘ failed
                  ↘ retrying → running
                  ↘ paused
```

---

## Estrutura de Diretórios

```
gameplay-content-generator/
├── src/gpcg/
│   ├── api/                  # FastAPI
│   │   ├── app.py            # App factory + middleware
│   │   └── routes.py         # Todos os endpoints REST
│   ├── application/          # Serviços (orquestração)
│   │   ├── generation_service.py    # Pipeline principal (9 estágios)
│   │   ├── ingestion_service.py     # Inbox watcher + FFprobe
│   │   ├── fact_service.py          # Extração + scoring de fatos
│   │   ├── content_planning_service.py
│   │   ├── script_service.py        # Script + originalidade
│   │   ├── gameplay_selector.py     # Seleção por cena + chaining
│   │   ├── gameplay_asset_service.py
│   │   ├── render_plan_builder.py   # Plano de render + concat
│   │   ├── qa_service.py            # QA técnico + IA
│   │   └── worker.py                # Worker background
│   ├── domain/               # Lógica de domínio (pura, sem I/O)
│   │   ├── models.py         # 10 modelos SQLAlchemy
│   │   ├── video_profiles.py # 4 perfis + builder custom
│   │   ├── originality.py    # Anti-plágio (n-gram overlap)
│   │   ├── filename_parser.py
│   │   ├── game_resolver.py  # 3 camadas (L1/L2/L3)
│   │   └── game_repository.py
│   ├── infrastructure/       # Integrações externas
│   │   ├── video_generate_adapter.py  # Subprocess + TTS + render
│   │   ├── llm.py            # Ollama client
│   │   ├── media.py          # FFmpeg/FFprobe helpers
│   │   ├── database.py       # SQLAlchemy engine + session
│   │   ├── document_parser.py  # PDF/TXT/MD/DOCX
│   │   └── llm.py
│   ├── cli/                  # Typer CLI
│   │   ├── main.py           # Entry point
│   │   └── commands.py       # Subcomandos
│   ├── config.py             # Settings (pydantic-settings)
│   └── logging.py
├── frontend/                 # React 19 + Vite + Tailwind + Radix
│   └── src/
│       ├── pages/            # 6 páginas
│       │   ├── games.tsx
│       │   ├── inbox.tsx
│       │   ├── content.tsx
│       │   ├── curiosity.tsx
│       │   ├── jobs.tsx
│       │   └── videos.tsx
│       ├── components/
│       │   ├── layout.tsx    # Nav + layout
│       │   ├── ui.tsx        # Primitives (Label, etc)
│       │   └── video-customization.tsx  # Painel de customização
│       ├── lib/
│       │   ├── api.ts        # API client
│       │   └── utils.ts
│       ├── hooks/
│       │   └── usePoll.ts    # Polling hook
│       └── main.tsx          # Router
├── scripts/
│   ├── dev.sh                # dev setup/run/worker/db/scan
│   └── deploy.sh             # systemd install/start/stop/logs
├── data/                     # Runtime (gitignored)
│   ├── gpcg.db               # SQLite
│   ├── docs/                 # Documentos enviados
│   ├── inbox/                # Symlink/overlay do inbox
│   ├── jobs/                 # Arquivos temporários por job
│   ├── uploads/              # Uploads temporários
│   ├── videos/               # Vídeos finalizados
│   └── voices/               # Vozes TTS enviadas
├── tests/                    # 87 testes pytest
│   ├── conftest.py
│   ├── fixtures/             # sample_3s.mp4, sample_vertical.mp4
│   ├── test_filename_parser.py
│   ├── test_document_parser.py
│   ├── test_game_resolver.py
│   ├── test_gameplay_assets.py
│   ├── test_ingestion.py
│   ├── test_jobs_and_render.py
│   ├── test_media.py
│   ├── test_originality.py
│   └── test_scene_selection.py
├── docs/                     # Esta documentação
├── pyproject.toml
├── .env.example
└── AGENTS.md
```

---

## Padrões de Design

### Separação de camadas

```
api/         → HTTP layer (FastAPI routes)
application/ → Services (orquestração, use cases)
domain/      → Lógica pura (modelos, regras, algoritmos)
infrastructure/ → Integrações externas (DB, LLM, FFmpeg, VG)
```

- `domain/` não importa nada de `infrastructure/` ou `api/`
- `application/` orquestra `domain/` + `infrastructure/`
- `api/` é thin — delega para `application/`

### Session management

- `session_scope()` context manager para transações
- `get_db()` dependency para FastAPI

### Subprocess pattern

Para VG: gerar script Python dinâmico → escrever em temp file → subprocess.run
com venv Python → resultado via JSON sidecar (bridge module).

### Config via pydantic-settings

Todas as settings via env vars com defaults sensatos. `get_settings()` é
cached singleton.

### Polling no frontend

`usePoll()` hook faz polling a cada N segundos para updates em tempo real
(sem WebSocket — simplicidade > otimização prematura).
