# Referência da API — Gameplay Content Generator

> Todos os endpoints REST do GPCG. Base URL: `http://localhost:8787/api`

---

## Sumário

- [Games](#games)
- [Sources (Inbox)](#sources-inbox)
- [Assets (Clips)](#assets-clips)
- [Documents](#documents)
- [Facts](#facts)
- [Content Plans + Scripts](#content-plans--scripts)
- [Jobs](#jobs)
- [Videos](#videos)
- [Voices (TTS)](#voices-tts)

---

## Games

### `GET /games`

Lista todos os jogos com contadores.

**Query params**: nenhum

**Response 200**:
```json
[
  {
    "id": 1,
    "canonical_name": "Bully",
    "aliases": ["Bully Scholarship Edition", "Canis Canem Edit"],
    "platforms": ["PC", "PS2"],
    "sources_count": 5,
    "assets_count": 12,
    "facts_count": 8,
    "videos_count": 3,
    "created_at": "2026-07-20T..."
  }
]
```

---

### `POST /games`

Registra um novo jogo.

**Body** (JSON):
```json
{
  "canonical_name": "Bully",
  "aliases": ["Bully Scholarship Edition"],
  "platforms": ["PC"]
}
```

**Response 200**:
```json
{
  "id": 1,
  "canonical_name": "Bully",
  "aliases": ["Bully Scholarship Edition"],
  "platforms": ["PC"]
}
```

---

### `GET /games/{game_id}`

Detalhes de um jogo.

**Response 200**:
```json
{
  "id": 1,
  "canonical_name": "Bully",
  "aliases": [...],
  "platforms": [...],
  "capture_sources": [...],
  "sources": [...],
  "facts": [...],
  "documents": [...]
}
```

---

## Sources (Inbox)

### `GET /sources`

Lista gameplay sources (gravações descobertas).

**Query params**:
- `game_id` (optional): filtrar por jogo
- `status` (optional): filtrar por status (`discovered`, `probing`, `ready`, `duplicate`, `error`, `needs_review`)

**Response 200**:
```json
[
  {
    "id": 1,
    "game_id": 1,
    "filename": "Bully_2024-01-15.mp4",
    "file_size": 524288000,
    "duration": 1800.5,
    "width": 1920,
    "height": 1080,
    "fps": 60,
    "codec": "h264",
    "has_audio": true,
    "ingestion_status": "ready",
    "resolution_method": "deterministic",
    "resolution_confidence": 0.95,
    "created_at": "2026-07-20T..."
  }
]
```

---

### `POST /sources/{source_id}/assign-game`

Atribui um jogo manualmente a um source `needs_review`.

**Body** (JSON):
```json
{ "game_id": 1 }
```

**Response 200**:
```json
{ "id": 1, "game_id": 1, "ingestion_status": "ready" }
```

---

### `POST /inbox/scan`

Força um scan do inbox agora (one-shot).

**Response 200**:
```json
{ "scanned": 3, "new": 1, "duplicates": 0, "errors": 0 }
```

---

## Assets (Clips)

### `GET /assets`

Lista clips de gameplay.

**Query params**:
- `game_id` (required): filtrar por jogo

**Response 200**:
```json
[
  {
    "id": 1,
    "source_id": 1,
    "label": "opening",
    "start_sec": 0.0,
    "end_sec": 150.0,
    "duration": 150.0,
    "used_count": 2,
    "source_filename": "Bully_2024-01-15.mp4"
  }
]
```

---

### `POST /assets`

Cria um clip manual a partir de um source.

**Body** (JSON):
```json
{
  "source_id": 1,
  "start_sec": 0,
  "end_sec": 150,
  "label": "opening"
}
```

**Response 200**:
```json
{
  "id": 5,
  "source_id": 1,
  "label": "opening",
  "start_sec": 0.0,
  "end_sec": 150.0,
  "duration": 150.0
}
```

---

### `DELETE /assets/{asset_id}`

Deleta um clip.

**Response 200**:
```json
{ "deleted": 5 }
```

---

## Documents

### `POST /documents/upload`

Upload de documento (PDF, TXT, MD, DOCX).

**Body** (multipart form):
- `file`: arquivo
- `game_id` (optional): ID do jogo. Se omitido → pool geral (curiosity shorts)

**Response 200**:
```json
{
  "id": 5,
  "filename": "curiosidades_bully.pdf",
  "file_type": "pdf",
  "game_id": 1
}
```

**Exemplo**:
```bash
curl -X POST http://localhost:8787/api/documents/upload \
  -F "game_id=1" \
  -F "file=@curiosidades_bully.pdf"
```

---

### `GET /documents`

Lista documentos.

**Query params**:
- `game_id` (optional): filtrar por jogo
- `general` (optional, bool): apenas pool geral (game_id=NULL)

**Response 200**:
```json
[
  {
    "id": 5,
    "game_id": 1,
    "filename": "curiosidades_bully.pdf",
    "file_type": "pdf",
    "file_size": 102400,
    "facts_extracted": true,
    "created_at": "2026-07-20T..."
  }
]
```

---

### `POST /documents/{doc_id}/extract-facts`

Extrai fatos do documento via LLM. Os fatos são reescritos nas próprias
palavras do LLM (anti-plágio) e recebem scores de qualidade e novidade.

**Response 200**:
```json
{
  "doc_id": 5,
  "facts_extracted": 3,
  "facts": [
    {
      "id": 10,
      "category": "gameplay",
      "claim": "O jogo possui um sistema de aulas...",
      "quality_score": 0.85,
      "novelty_score": 0.60
    }
  ]
}
```

---

## Facts

### `GET /facts`

Lista fatos extraídos.

**Query params**:
- `game_id` (optional): filtrar por jogo
- `general` (optional, bool): apenas pool geral
- `category` (optional): filtrar por categoria

**Response 200**:
```json
[
  {
    "id": 10,
    "game_id": 1,
    "document_id": 5,
    "category": "gameplay",
    "claim": "O jogo possui um sistema de aulas...",
    "verification": "unverified",
    "quality_score": 0.85,
    "novelty_score": 0.60,
    "used_count": 0,
    "created_at": "2026-07-20T..."
  }
]
```

---

## Content Plans + Scripts

### `GET /content-plans`

Lista planos de conteúdo.

**Query params**:
- `game_id` (optional): filtrar por jogo

**Response 200**:
```json
[
  {
    "id": 1,
    "game_id": 1,
    "fact_id": 10,
    "background_game_id": null,
    "format": "youtube_short",
    "target_duration": 60,
    "topic": "Sistema de aulas",
    "hook": "Você sabia que...",
    "tone": "curioso",
    "energy": "medium",
    "music_mood": "upbeat",
    "created_at": "2026-07-20T..."
  }
]
```

---

### `GET /scripts/{script_id}`

Detalhes de um script.

**Response 200**:
```json
{
  "id": 1,
  "content_plan_id": 1,
  "draft": "Você sabia que...",
  "optimized": "Você sabia que o jogo...",
  "final": "Você sabia que o jogo possui...",
  "status": "final",
  "char_count": 850,
  "originality_score": 98.4,
  "originality_report": {
    "overlap_fraction": 0.016,
    "matched_source": "curiosidades_bully.pdf",
    "longest_matches": ["um jardim secreto de gnomos"],
    "threshold": 70.0,
    "is_original": true
  },
  "rewrite_count": 0,
  "created_at": "2026-07-20T..."
}
```

---

## Jobs

### `GET /jobs`

Lista jobs.

**Query params**:
- `status` (optional): `queued`, `running`, `completed`, `failed`, `retrying`
- `limit` (optional): máximo de resultados

**Response 200**:
```json
[
  {
    "id": 1,
    "job_uuid": "abc-123",
    "type": "generate_short",
    "game_id": 1,
    "status": "completed",
    "stage": "done",
    "progress": 100,
    "attempts": 1,
    "error": null,
    "artifacts": {
      "scene_duration": 7200,
      "video_format": "1:1",
      "voice_path": "/data/voices/narrator.wav"
    },
    "created_at": "2026-07-20T...",
    "completed_at": "2026-07-20T..."
  }
]
```

---

### `POST /jobs/generate`

Cria um job de geração (Game Short).

**Body** (multipart form):

| Parâmetro | Tipo | Required | Default | Descrição |
|-----------|------|----------|---------|-----------|
| `game_id` | int | ✅ | — | ID do jogo |
| `scene_duration` | float | ❌ | `0` | Duração de cada cena em segundos (0=auto) |
| `video_format` | string | ❌ | `""` | `9:16`, `16:9`, `1:1`, `4:5` |
| `subtitle_font` | string | ❌ | `""` | Fonte da legenda |
| `subtitle_font_size` | int | ❌ | `0` | Tamanho da fonte (0=auto) |
| `subtitle_color` | string | ❌ | `""` | Cor do texto |
| `subtitle_outline_color` | string | ❌ | `""` | Cor do contorno |
| `subtitle_position` | string | ❌ | `""` | `top`, `middle`, `bottom` |
| `subtitle_case` | string | ❌ | `""` | `upper`, `lower`, `none` |
| `voice` | string | ❌ | `""` | Filename da voz (ex: `narrator.wav`) |

**Response 200**:
```json
{
  "id": 1,
  "status": "queued",
  "game": "Bully"
}
```

**Erros**:
- `404`: jogo não encontrado
- `404`: voz não encontrada (se `voice` especificado mas arquivo não existe)

**Exemplo**:
```bash
curl -X POST http://localhost:8787/api/jobs/generate \
  -F "game_id=1" \
  -F "video_format=1:1" \
  -F "scene_duration=7200" \
  -F "voice=narrator.wav" \
  -F "subtitle_color=yellow" \
  -F "subtitle_position=top"
```

---

### `POST /jobs/curiosity`

Cria um Curiosity Short (curiosidade geral + gameplay de fundo).

**Body** (multipart form):

| Parâmetro | Tipo | Required | Default | Descrição |
|-----------|------|----------|---------|-----------|
| `background_game_id` | int | ✅ | — | Jogo cujo gameplay será o fundo |
| `fact_id` | int | ❌ | auto | Fato específico (se omitido, auto-seleciona) |
| `scene_duration` | float | ❌ | `0` | Duração de cada cena |
| `video_format` | string | ❌ | `""` | Formato do vídeo |
| `subtitle_*` | — | ❌ | — | Mesmos params de legenda |
| `voice` | string | ❌ | `""` | Voz TTS |

**Response 200**:
```json
{
  "id": 2,
  "status": "queued",
  "type": "curiosity_short",
  "background_game": "Bully",
  "fact_id": null
}
```

---

## Videos

### `GET /videos`

Lista vídeos finalizados.

**Query params**:
- `game_id` (optional): filtrar por jogo

**Response 200**:
```json
[
  {
    "id": 1,
    "job_id": 1,
    "content_plan_id": 1,
    "game_id": 1,
    "duration": 60.5,
    "width": 1080,
    "height": 1920,
    "qa_score": 0.85,
    "status": "qa_passed",
    "thumbnail_path": "/data/videos/thumb_1.jpg",
    "created_at": "2026-07-20T..."
  }
]
```

---

### `GET /videos/{video_id}/file`

Serve o arquivo de vídeo MP4.

**Response**: `FileResponse` (media_type `video/mp4`)

**Erros**: `404` se vídeo ou arquivo não existir

---

### `GET /videos/{video_id}/thumbnail`

Serve a thumbnail do vídeo.

**Response**: `FileResponse` (media_type `image/jpeg`)

**Erros**: `404` se vídeo ou thumbnail não existir

---

## Voices (TTS)

### `GET /voices`

Lista arquivos de voz disponíveis para TTS.

**Response 200**:
```json
[
  {
    "filename": "narrator.wav",
    "file_size": 1048576,
    "file_size_kb": 1024.0
  },
  {
    "filename": "bruno_voice.mp3",
    "file_size": 524288,
    "file_size_kb": 512.0
  }
]
```

---

### `POST /voices/upload`

Upload de arquivo de voz para TTS (voice cloning).

**Body** (multipart form):
- `file`: arquivo de áudio (`.wav`, `.mp3`, `.ogg`, `.flac`, `.m4a`)

**Response 200**:
```json
{
  "filename": "narrator.wav",
  "file_size": 1048576,
  "file_size_kb": 1024.0,
  "path": "/home/bruno/.../data/voices/narrator.wav"
}
```

**Erros**:
- `400`: filename vazio
- `400`: formato não suportado

**Notas**:
- Se o arquivo já existe, adiciona sufixo de timestamp (não sobrescreve)
- Arquivos salvos em `data/voices/`
- Recomendado: 5-30 segundos de áudio limpo da voz a clonar

---

### `DELETE /voices/{filename}`

Deleta um arquivo de voz.

**Response 200**:
```json
{ "deleted": "narrator.wav" }
```

**Erros**:
- `400`: filename inválido (contém `/`, `\`, ou `..`)
- `404`: voz não encontrada

---

## Códigos de Erro Comuns

| Código | Significado |
|--------|-------------|
| `200` | Sucesso |
| `400` | Requisição inválida (parâmetros, formato) |
| `404` | Recurso não encontrado |
| `422` | Validação falhou (FastAPI automático) |
| `500` | Erro interno do servidor |
