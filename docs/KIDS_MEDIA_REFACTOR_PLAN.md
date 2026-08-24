# GPCG KIDS — Refatora de Mídias: Biblioteca de Canal + Seleção Semântica

**Versão:** 1.0
**Data:** 2026-08-23
**Status:** Plano de arquitetura aprovado para implementação

---

## 1. PROBLEMA

A implementação atual do Kids tem três defeitos arquiteturais graves:

### 1.1 Mídia vinculada ao tópico em vez de biblioteca do canal

`StoryAsset.topic_id` é `NOT NULL`. Cada mídia é propriedade de um tópico.
Nos Games, `GameplaySource` é uma **biblioteca do canal** — o pipeline busca
gameplays por jogo, não por ideia. O Kids deveria seguir o mesmo padrão: a
mídia é uma biblioteca do canal, e o pipeline seleciona mídias que condizem
com o conteúdo do vídeo.

### 1.2 Botão "Gerar" no card do tópico

Nos Games, o usuário **nunca** clica "Gerar" numa gameplay. A automação
consome a fila de ideias, cria jobs, e o pipeline seleciona gameplays
automaticamente. O botão "Gerar" no card do tópico Kids é uma aberração
que duplica o que a `KidsAutomationStrategy` já faz.

### 1.3 Sem seleção semântica de mídia

Nos Games, o `GameplayRetriever` faz busca semântica: ele pega o
`gameplay_query` do `VideoCreativePlan`, busca eventos mapeados (VLM/ASR)
que match o conteúdo do script, e seleciona clips que condizem com o que
está sendo narrado. O Kids não tem nada disso — a mídia é só um arquivo
estático que aparece no vídeo sem critério.

---

## 2. ANÁLISE DO FLUXO GAMES (referência)

### 2.1 Pipeline completo do Games

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. UPLOAD (VPS)                                                  │
│    POST /api/gameplays/upload                                    │
│    → streaming 1 MiB chunks → .part → atomic rename             │
│    → SHA-256 incremental → dedup por (user_id, file_hash)       │
│    → GameplaySource criada com processing_status=uploaded        │
│    → ingestion_status=probing (FFprobe síncrono leve)           │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ 2. MAPEAMENTO (Worker — job "mapping")                           │
│    Worker claima job → baixa gameplay do VPS (token-auth)        │
│    → confirma checksum → marca downloading/downloaded           │
│    → GameplayAnalyzer: VLM (cenas) + ASR (transcrição)           │
│    → eventos: COMBAT, DIALOGUE, CUTSCENE, EXPLORATION, etc      │
│    → cada evento: start_time, end_time, event_type,             │
│      description, interesting_score, visual_confidence          │
│    → sync eventos via POST /api/gameplays/{id}/mapping-result   │
│    → processing_status: mapping → mapped → ready                │
│    → VPS deleta arquivo temporário após download confirmado     │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ 3. INDEXAÇÃO SEMÂNTICA                                           │
│    GameplayIndexService persiste eventos em gameplay_events     │
│    → indexado por source_id, event_type, interesting_score     │
│    → AnalysisStatus: pending → analyzing → indexing → ready    │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ 4. AUTOMAÇÃO (VPS — poll do worker)                              │
│    Worker poll → POST /api/automation/check                     │
│    → check_automation: tem KI na fila? tem gameplay ready?      │
│    → retorna pending = {user_id, automation_id, config}         │
│    → Worker: POST /api/automation/consume-queue                 │
│    → create_job_from_automation: pega 1º KI da fila             │
│    → cria Job(generate_short, domain=games)                     │
│    → remove KI da fila                                          │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ 5. GERAÇÃO (Worker — job "generate_short")                       │
│    Worker claima job → GET /api/jobs/{id}/data                  │
│    → populate_local_db (SQLite temporário local)                │
│    → GenerationService._run_pipeline:                           │
│                                                                  │
│      content_planning → editorial_planning → creative_engine    │
│      → script → humanization → script_review → TTS              │
│      → GAMEPLAY_SELECTION ← aqui o retriever atua               │
│      → music_selection → render_plan → render → QA              │
│                                                                  │
│    GameplayRetriever.retrieve():                                 │
│      1. Tem VideoCreativePlan? → semantic retrieval             │
│         - gameplay_query do plan (keywords do script)           │
│         - busca GameplayEvent por descrição/transcrição         │
│         - filtra por interesting_score, visual_confidence       │
│         - mapeia beats → event_types (hook→COMBAT, etc)         │
│         - respeita clip_usage (não repete segmentos)            │
│      2. Sem plan ou background_filler? → fallback random        │
│         - GameplaySelector.select() (random com pesos)          │
│      3. Retorna SelectedClip[] (start_sec, end_sec, source_path)│
│                                                                  │
│    RenderPlanBuilder.build():                                    │
│      - extrai clips físicos do gameplay (FFmpeg)                 │
│      - monta request_data para video-generate                   │
│      - subtitles, transitions, format                           │
│                                                                  │
│    → upload vídeo → sync results → YouTube upload (VPS)         │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Componentes-chave do Games

| Componente | Arquivo | Função |
|-----------|---------|--------|
| `GameplaySource` | `domains/games/models.py:232` | Biblioteca de gameplays do canal |
| `GameplayEvent` | `domains/games/models.py:425` | Eventos mapeados (VLM/ASR) |
| `GameplayAsset` | `domains/games/models.py:369` | Clip reutilizável (start→end) |
| `GameplayClipUsage` | `domains/games/models.py:394` | Tracking de segmentos usados |
| `GameplayRetriever` | `application/gameplay_retriever.py:98` | Seleção semântica de clips |
| `GameplaySelector` | `application/gameplay_selector.py:77` | Fallback random de clips |
| `GameplayIndexService` | `application/gameplay_index_service.py` | Indexação de eventos |
| `GameplayAnalyzer` | `application/gameplay_analyzer.py` | VLM + ASR no worker |
| `GenerationService` | `application/generation_service.py:100` | Orquestra pipeline completo |
| `KidsAutomationStrategy` | `domains/automation_strategies.py:79` | Automação Kids (já existe) |
| `content.tsx` | `frontend/src/pages/content.tsx` | UI: upload + status + timeline |

### 2.3 Padrões que DEVEM ser replicados

1. **Upload com drag-and-drop + progresso** (`content.tsx:250-281`)
   - `useUploadStore` para tracking de uploads em andamento
   - Múltiplos arquivos simultâneos
   - Progresso por arquivo
   - Toast de sucesso/erro

2. **Pipeline de status visual** (`content.tsx:44-56`)
   - `PROCESSING_STATUS_CONFIG` mapeia cada status → cor + label
   - Badge de ingestion + badge de processing
   - Barra de progresso animada durante processamento
   - Timeline expansível mostrando eventos mapeados

3. **Streaming upload** (`api/routes.py:345-443`)
   - 1 MiB chunks
   - SHA-256 incremental
   - `.part` → atomic rename
   - Dedup por (user_id, file_hash)
   - 2 GiB max

4. **Retriever com fallback** (`gameplay_retriever.py:113-234`)
   - Semantic retrieval quando tem plan + eventos
   - Fallback random quando não tem
   - Respeita clip_usage (não repete)
   - user-scoped com public fallback

5. **Automação consome fila** (`automation_strategies.py:79-273`)
   - `check()`: tem ideia na fila? tem mídia ready?
   - `create_job()`: pega 1ª ideia, converte em tópico, cria job
   - Worker poll → check → consume-queue → create_job

---

## 3. PLANO DE ARQUITETURA KIDS

### 3.1 Princípio: biblioteca de canal, não por tópico

```
ESTRUTURA ATUAL (ERRADA):
  KidsTopic (Dinossauros)
    └── StoryAsset (video_dino.mp4)  ← vinculado ao tópico
    └── StoryAsset (image_trex.jpg)  ← vinculado ao tópico
  KidsTopic (Sistema Solar)
    └── StoryAsset (video_planets.mp4)  ← vinculado ao tópico

ESTRUTURA CORRETA:
  Canal do Usuário
    └── StoryAsset (video_dino.mp4)   ← biblioteca do canal
    └── StoryAsset (image_trex.jpg)   ← biblioteca do canal
    └── StoryAsset (video_planets.mp4)← biblioteca do canal
    └── StoryAsset (video_generic.mp4)← biblioteca do canal

  KidsTopic (Dinossauros)  ← apenas metadados editoriais
  KidsTopic (Sistema Solar)← apenas metadados editoriais
```

A mídia é do **canal**, não do tópico. O pipeline seleciona mídias da
biblioteca que condizem com o conteúdo do vídeo (título do tópico,
script, tags da ideia).

### 3.2 Modelo de dados

#### StoryAsset (modificado)

```python
class StoryAsset(Base):
    __tablename__ = "story_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)

    # MUDANÇA: topic_id torna-se opcional (nullable)
    # Mídia da biblioteca não precisa estar vinculada a um tópico
    topic_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("kids_topics.id"), nullable=True, index=True
    )

    # Campos existentes (mantidos)
    filename, storage_key, file_hash, file_size, width, height
    media_kind, duration, codec, has_audio, thumbnail_key
    processing_status, process_error, metadata_json, created_at

    # NOVOS: tags para seleção semântica
    tags: Mapped[list] = mapped_column(JSON, default=list)  # ["dinosaur", "nature", "green"]
    description: Mapped[str] = mapped_column(Text, default="")  # descrição opcional
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)  # biblioteca pública
```

#### AssetClipUsage (novo — equivalente a GameplayClipUsage)

```python
class AssetClipUsage(Base):
    """Tracks which time ranges of a video asset have been used in a video.
    Prevents reusing the same video segment across videos.
    Only applies to video assets (images can be reused freely)."""
    __tablename__ = "asset_clip_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("story_assets.id"), index=True)
    consumer_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    start_sec: Mapped[float] = mapped_column(Float, default=0.0)
    end_sec: Mapped[float] = mapped_column(Float, default=0.0)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
```

### 3.3 Upload — biblioteca de canal

#### Endpoint

```
POST /api/kids/assets/upload
```

**Não** vinculado a tópico. Upload direto para a biblioteca do canal.

Comportamento idêntico ao upload de gameplay:

1. Streaming 1 MiB chunks
2. SHA-256 incremental
3. `.part` → atomic rename
4. Dedup por (user_id, file_hash)
5. 2 GiB max
6. MIME validation (image/* + video/*)
7. Imagens: PIL probe síncrono → `ready`
8. Vídeos: cria `kids_asset_process` job → `queued`

#### Parâmetros opcionais

```json
{
  "tags": ["dinosaur", "nature"],
  "description": "Tiranossauro rex em movimento",
  "topic_id": 12  // opcional: vincular a um tópico específico
}
```

### 3.4 Listagem — biblioteca do canal

```
GET /api/kids/assets?media_kind=video&status=ready&topic_id=12
```

Retorna todas as mídias do canal do usuário, com filtros opcionais.

### 3.5 KidsMediaRetriever — seleção semântica

Equivalente ao `GameplayRetriever` mas para mídias Kids.

```python
class KidsMediaRetriever:
    """Retrieves Kids media assets for video generation.

    Two modes:
    1. Semantic: when a VideoCreativePlan is available with a media_query,
       selects assets whose tags/description match the query.
    2. Fallback: random selection from ready assets, weighted by
       used_count (prefer less-used assets).

    For video assets: respects AssetClipUsage (don't reuse same segment).
    For image assets: can be reused (Ken Burns effect doesn't "consume"
      the image, but we track usage for diversity).
    """

    def retrieve(
        self,
        session: Session,
        user_id: int,
        target_duration: float,
        *,
        creative_plan: Optional[VideoCreativePlan] = None,
        topic_id: Optional[int] = None,
        scene_duration: float = 0.0,
        rng: Optional[random.Random] = None,
        accept_public: bool = False,
    ) -> list[SelectedMedia]:
        ...
```

#### SelectedMedia (equivalente a SelectedClip)

```python
@dataclass
class SelectedMedia:
    asset: StoryAsset
    source_path: str
    start_sec: float = 0.0  # 0 for images
    end_sec: float = 0.0    # duration for videos, 0 for images
    duration: float = 0.0   # how long this media appears in the video
    scene_index: int = 0
    selection_reason: str = ""  # "semantic_tag_match", "semantic_description_match", "random_fallback"
    usage_count_at_selection: int = 0
```

#### Estratégia de seleção

1. **Semantic mode** (quando há `VideoCreativePlan` com `media_query`):
   - Extrair keywords do `media_query` (ou do título do tópico + script)
   - Buscar assets cujas `tags` contenham keywords
   - Buscar assets cuja `description` contenha keywords (case-insensitive)
   - Score por número de matches
   - Ordenar por score → interesting (menos usado primeiro)

2. **Topic-scoped mode** (quando há `topic_id`):
   - Filtrar assets vinculados ao tópico + assets da biblioteca geral
   - Priorizar assets do tópico

3. **Fallback random** (sem plan ou sem matches):
   - Random weighted por `1 / (used_count + 1)`
   - Respeitar `AssetClipUsage` para vídeos

### 3.6 GenerationService — branch por domínio

O `_run_pipeline` precisa branchar no estágio `gameplay_selection`:

```python
# ── Stage: media selection ───────────────────────────────────────
self._set_stage(job_id, JobStage.gameplay_selection)
narration_dur = self._get_artifact(job_id, "narration_duration")

if job_domain == "kids":
    # Kids: use KidsMediaRetriever
    topic_id = job.artifacts.get("topic_id")
    clips = self.kids_media_retriever.retrieve(
        session, user_id, target_duration=narration_dur,
        creative_plan=creative_plan,
        topic_id=topic_id,
        scene_duration=scene_duration,
    )
    if not clips:
        raise GenerationError(
            "no ready media assets available — upload media first",
            JobStage.gameplay_selection.value,
        )
else:
    # Games: existing GameplayRetriever (unchanged)
    clips = self.gameplay_retriever.retrieve(...)
```

O `RenderPlanBuilder` também precisa branchar:

- **Games:** extrai clips de gameplay (FFmpeg cut)
- **Kids imagens:** aplica Ken Burns effect (zoom/pan)
- **Kids vídeos:** extrai segmento (FFmpeg cut) ou usa vídeo inteiro

### 3.7 Automação — já existe, manter

A `KidsAutomationStrategy` já funciona corretamente:
- `check()`: tem ideia na fila? tem mídia ready?
- `create_job()`: pega ideia, converte em tópico, cria job

**Mudança:** `check()` precisa verificar mídias da **biblioteca do canal**
(não mais por tópico):

```python
# ANTES (errado):
ready_assets = db.query(StoryAsset).filter(
    StoryAsset.user_id == auto.user_id,
    StoryAsset.processing_status == "ready",
).count()

# DEPOIS (igual, mas agora sem topic_id obrigatório):
# A query é a mesma — StoryAsset sem topic_id continua contando.
# Mudança é no modelo, não na query.
```

Na verdade a query já está correta — ela conta todos os assets ready
do usuário, independente de topic_id. A mudança no modelo (topic_id
nullable) não quebra a automação.

### 3.8 Remover `/api/kids/generate`

O endpoint `POST /api/kids/generate` é redundante. A automação já cria
jobs via `KidsAutomationStrategy.create_job()`. O botão "Gerar" no
frontend chama este endpoint — remover ambos.

### 3.9 Frontend — nova página de Mídias

Replicar `content.tsx` (Games) para Kids:

```
KidsPage
├── Tab: Tópicos (existente, sem botão Gerar)
├── Tab: Mídias (NOVO — biblioteca do canal)
│   ├── Upload zone (drag-and-drop, múltiplos arquivos, progresso)
│   ├── Processing banner (N mídias em processamento)
│   ├── Lista de mídias (cards com status, dimensões, duração, thumbnail)
│   │   ├── Imagens: ícone + dimensões + status ready
│   │   ├── Vídeos: thumbnail + duração + codec + status pipeline
│   │   └── Tags editáveis + descrição
│   └── Mídias públicas da comunidade (se houver)
└── Tab: Config (existente)
```

#### Componentes a reutilizar do Games

- `useUploadStore` — tracking de uploads em andamento
- `usePoll` — polling de status
- Padrão de drag-and-drop zone
- Padrão de card com status badge + progress bar
- Padrão de expandable timeline (adaptar para tags em vez de eventos)

### 3.10 Frontend — tópicos sem botão Gerar

A aba de Tópicos mantém sua função editorial:
- Criar/editar/excluir tópicos
- Ver ideias convertidas em tópicos
- Ver quantos vídeos já foram gerados de cada tópico

**Remover:**
- Botão "Gerar" no card do tópico
- Botão "Mídias" no card do tópico (mídias agora são da biblioteca)
- Expandable de mídias dentro do tópico

### 3.11 Frontend — dashboard

Manter contagem de mídias (não imagens), mas agora da biblioteca do canal:
- Total de mídias
- Mídias prontas
- Mídias em processamento
- Mídias com erro

---

## 4. MAPEAMENTO GAMES → KIDS

| Games | Kids | Status |
|-------|------|--------|
| `GameplaySource` | `StoryAsset` (biblioteca) | Modificar: topic_id nullable + tags + description |
| `GameplayEvent` | — (Kids não tem VLM/ASR mapping) | Não aplicável |
| `GameplayAsset` | — (Kids não tem clips pré-definidos) | Não aplicável |
| `GameplayClipUsage` | `AssetClipUsage` | Criar novo |
| `GameplayRetriever` | `KidsMediaRetriever` | Criar novo |
| `GameplaySelector` | (fallback dentro do retriever) | Integrar no retriever |
| `GameplayIndexService` | — (Kids não tem indexação de eventos) | Não aplicável |
| `GameplayAnalyzer` | — (Kids não analisa mídia com VLM) | Não aplicável |
| `content.tsx` MediaTab | `kids.tsx` MediaTab | Criar novo (replicar padrão) |
| `POST /gameplays/upload` | `POST /kids/assets/upload` | Criar novo endpoint |
| `POST /gameplays/{id}/create-mapping-job` | — (não tem mapeamento) | Não aplicável |
| `POST /kids/generate` | — (remover) | Deletar |
| `KidsAutomationStrategy` | (já existe) | Manter, ajustar query |

### 4.1 Por que Kids não tem mapeamento VLM/ASR

Nos Games, o mapeamento é necessário porque:
- Gameplays são vídeos longos (1-2h) com eventos visuais complexos
- O pipeline precisa saber que trecho é COMBAT vs DIALOGUE vs CUTSCENE
- O `GameplayRetriever` busca eventos que match o script

No Kids, as mídias são:
- **Imagens:** estáticas, usadas com Ken Burns effect. Não há "eventos".
- **Vídeos:** curtos (segundos a minutos), usados como clipes inteiros ou
  segmentos. A seleção é por **tags/descrição** (o usuário tagueia a mídia
  no upload), não por análise VLM.

Isso é uma **diferença legítima** entre os domínios, não um defeito.

### 4.2 Por que Kids mantém tópicos

Nos Games, o `game_id` na `GameplaySource` serve como indexação: o
pipeline busca gameplays por jogo. No Kids, não temos um equivalente
natural (não temos "jogos" para indexar mídias).

A solução é **tags**: o usuário tagueia mídias na biblioteca
(`["dinosaur", "nature"]`) e o retriever busca por tags que match o
conteúdo do vídeo (título do tópico, keywords do script).

Tópicos continuam existindo como **metadados editoriais** (título,
categoria, age_range, descrição), mas **não** como proprietários de
mídias. A automação converte ideias em tópicos e cria jobs; o pipeline
usa o título do tópico + script para selecionar mídias da biblioteca.

---

## 5. PLANO DE IMPLEMENTAÇÃO

### Fase 1: Backend — modelo e schema

1. `StoryAsset.topic_id` → nullable
2. Adicionar `tags`, `description`, `is_public` em `StoryAsset`
3. Criar `AssetClipUsage` model
4. Schema evolution em `database.py` (`_ensure_column` + `_ensure_table`)

### Fase 2: Backend — upload e listagem

5. Novo endpoint `POST /api/kids/assets/upload` (streaming, biblioteca)
6. Novo endpoint `GET /api/kids/assets` (listagem com filtros)
7. Endpoint `PATCH /api/kids/assets/{id}` (editar tags/descrição)
8. Manter `DELETE /api/kids/assets/{id}` (já existe)
9. Migrar upload existente (`POST /kids/topics/{id}/assets`) → chamar mesma lógica

### Fase 3: Backend — retriever e generation

10. Criar `KidsMediaRetriever` em `application/kids_media_retriever.py`
11. Criar `SelectedMedia` dataclass
12. `GenerationService._run_pipeline`: branch por domínio no estágio media_selection
13. `RenderPlanBuilder`: branch por domínio (Ken Burns para imagens, cut para vídeos)
14. Remover `POST /api/kids/generate`

### Fase 4: Backend — worker e job data

15. `worker_routes.py`: job data inclui assets da biblioteca (não só do tópico)
16. `local_db_sync.py`: sync de AssetClipUsage
17. `remote_worker.py`: `_download_kids_assets` já funciona (baixa por asset_id)

### Fase 5: Frontend — página de mídias

18. Nova aba "Mídias" em `kids.tsx` (replicar `content.tsx` MediaTab)
19. Upload zone com drag-and-drop + progresso + múltiplos arquivos
20. Lista de mídias com status pipeline, thumbnail, dimensões, duração
21. Editor de tags inline
22. Remover botão "Gerar" e "Mídias" do card de tópico

### Fase 6: Frontend — dashboard e API

23. `api.ts`: novos endpoints (`uploadKidsAsset`, `listKidsAssets`, `patchKidsAsset`)
24. `dashboard.tsx`: contagem de mídias da biblioteca

### Fase 7: Testes

25. Testes do `KidsMediaRetriever` (semantic, fallback, clip usage)
26. Testes do upload de biblioteca (streaming, dedup, MIME)
27. Testes do branch por domínio no `GenerationService`
28. Testes do `AssetClipUsage`
29. Atualizar testes existentes que assumiam `topic_id` obrigatório

### Fase 8: Verificação

30. `pytest tests/ -q`
31. `cd frontend && npm run typecheck`
32. `cd frontend && npm run build`

---

## 6. RISCOS E MITIGAÇÕES

### 6.1 Migração de dados existentes

**Risco:** StoryAssets existentes têm `topic_id` NOT NULL. Tornar nullable
pode quebrar constraints.

**Mitigação:** Schema evolution com `ALTER TABLE story_assets MODIFY
topic_id DROP NOT NULL` (SQLite suporta via reconstrução de tabela).
Assets existentes mantêm seu `topic_id` — a mudança é aditiva.

### 6.2 Render pipeline não suporta imagens

**Risco:** O `RenderPlanBuilder` atual só sabe extrair clips de gameplay
(vídeo). Imagens com Ken Burns effect podem não ser suportadas pelo
`video-generate` subprocess.

**Mitigação:** Verificar se `video-generate` suporta imagens com Ken Burns.
Se não, o estágio de render precisa converter imagens em vídeo (FFmpeg
zoompan filter) antes de enviar para o pipeline.

### 6.3 Seleção semântica sem embeddings

**Risco:** A seleção por tags é match exato de string. Sem embeddings,
"dinossauro" não match "dinosaur".

**Mitigação:**
- Fase 1: match por tags + descrição (case-insensitive, substring)
- Fase 2 (futuro): usar `EmbeddingService` para similarity search
- Tags em inglês e português (o usuário pode tagueiar em ambos)

### 6.4 Mídias sem tags

**Risco:** Mídia sem tags não é selecionada no modo semantic.

**Mitigação:**
- Fallback random inclui mídias sem tags
- UI incentiva tagueamento no upload (campo tags visível)
- Auto-tag no futuro (VLM descreve a imagem/vídeo)
