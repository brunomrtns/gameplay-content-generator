# Plano Técnico Detalhado: VM Flávio como Worker Dedicado do GPCG

## 1. Diagnóstico Atual

### 1.1 Arquitetura existente

```
                    ┌─────────────────────────┐
                    │   VPS Contabo (França)   │
                    │   Control Plane          │
                    │                         │
                    │   • FastAPI (porta 8787) │
                    │   • SQLite (gpcg.db)     │
                    │   • React Frontend       │
                    │   • Nginx (/gpcg/)       │
                    │   • Job Queue            │
                    │   • Worker Registry      │
                    │   • BI Identity          │
                    │                         │
                    │   Docker: gpcg-api       │
                    │          gpcg-worker     │
                    │          gpcg-catalog    │
                    └────────┬────────────────┘
                             │ WireGuard 10.0.0.0/24
                             │ (238ms latência)
                             │
                    ┌────────▼────────────────┐
                    │   PC Bruno (casa)        │
                    │   Compute Plane          │
                    │                         │
                    │   • RemoteWorker        │
                    │   • RTX 3060 (GPU)      │
                    │   • Ollama local        │
                    │     - llama3.1:8b       │
                    │     - gemma3:12b (VLM)  │
                    │     - qwen3:14b         │
                    │   • faster-whisper CUDA │
                    │   • YOLOv8m CUDA        │
                    │   • XTTS v2 (voice clone)│
                    │   • FFmpeg libx264      │
                    │   • Storage: ToshibaHD  │
                    │   • systemd service     │
                    └─────────────────────────┘
```

### 1.2 O que a VM Flávio tem

| Recurso | Valor | Notas |
|---------|-------|-------|
| vCPUs | 4 (uso prático: 3) | i7-3770, LXC/Proxmox |
| RAM | 8 GB + 4 GB swap | |
| Disco `/` | 60 GB SSD local | I/O rápido, hot work |
| Disco `/data` | 100 GB rede | Cold storage |
| GPU | Nenhuma | Passthrough impossível |
| Docker | 29.7.2 + Compose v5.4.0 | |
| Python | 3.13.5 | |
| FFmpeg | 7.1.5 | Já instalado |
| LiteLLM | Acessível (4.5ms) | 7 modelos texto + Kokoro |
| NetBird | 100.68.105.88 | bmi.netbird.cloud, 38ms do PC |
| /dev/sdb | **NÃO TOCAR** | LVM do host, 10 containers |

### 1.3 O que o Flávio já entregou

- Pillow no LiteLLM → VLM gemma3:12b com visão funcionando remotamente
- Rate limit VLM: 6 → 15 req/min
- ASR com GPU remota: `POST $LITELLM_BASE_URL/audio/transcriptions`
- Kokoro TTS via LiteLLM (vozes: `pm_alex`, `pm_santa`, `pf_dora`)
- WireGuard permitido (precisa declarar endpoint IP + porta)

### 1.4 O que NÃO temos

| Componente | Status | Impacto |
|-----------|--------|---------|
| Adapter LLM LiteLLM | Não existe | LLM hardcoded para Ollama local |
| Adapter VLM LiteLLM | Não existe | VLM hardcoded para Ollama local |
| Adapter ASR remoto | Não existe | ASR hardcoded para faster-whisper local |
| Adapter TTS Kokoro | Não existe | TTS hardcoded para XTTS via video-generate |
| YOLO device config | Parcial | `device="cuda"` hardcoded no DetectorConfig |
| Job requeue automático | Não existe | Job preso em `running` se worker cai |
| WireGuard VM ↔ VPS | Não configurado | VM não consegue acessar a API |
| Worker na VM | Não instalado | |
| Render path CPU-only | Parcial | video-generate tem muitas deps de GPU |

---

## 2. Decisões Arquiteturais

### 2.1 VM será worker COMPLETO e DEDICADO

A VM processa jobs inteiros de ponta a ponta (mapping + generation), de forma independente e paralela ao PC do Bruno. Não é complemento — é um segundo worker completo.

```
                    ┌─────────────────────────┐
                    │   VPS Contabo            │
                    │   Control Plane          │
                    │   • Job Queue            │
                    │   • Worker Registry      │
                    └───┬─────────────────┬───┘
                        │                 │
              WireGuard │                 │ WireGuard
               (existe)  │                 │ (novo)
                        │                 │
              ┌─────────▼───┐    ┌────────▼──────────┐
              │ PC Bruno    │    │ VM Flávio (bmi)   │
              │ Worker 1    │    │ Worker 2          │
              │             │    │                   │
              │ GPU RTX3060 │    │ 4 vCPU, 8GB RAM   │
              │ Ollama local│    │ LiteLLM remoto    │
              │ XTTS local  │    │ Kokoro remoto     │
              │ faster-whisp│    │ ASR GPU remoto    │
              │   CUDA      │    │ YOLO CPU          │
              │ YOLO CUDA   │    │ FFmpeg CPU        │
              │ FFmpeg CPU  │    │                   │
              │             │    │ Voz: fixa Kokoro  │
              │ Voz: custom │    │                   │
              │  (XTTS)     │    │                   │
              └─────────────┘    └───────────────────┘
```

### 2.2 TTS: vozes diferentes por worker

| Worker | TTSEngine | Voz | Voice cloning |
|--------|-----------|-----|---------------|
| PC Bruno | XTTSEngine (local) | `bruno.wav` (custom upload) | Sim |
| VM Flávio | KokoroTTSEngine (remoto via LiteLLM) | `pm_alex` (fixa) | Não |

Ambos usam o **mesmo pipeline** do video-generate. A diferença é só qual `TTSEngine` o `tts_factory` retorna:
- PC Bruno: `TTS_ENGINE=xtts` → `XTTSEngine` (torch + Coqui TTS local)
- VM Flávio: `TTS_ENGINE=kokoro` → `KokoroTTSEngine` (HTTP direto para LiteLLM, sem torch)

O usuário sobe a voz no PC → vídeos gerados no PC saem com a voz dele.
Vídeos gerados na VM → saem com voz fixa do Kokoro.
Isso é aceitável para beta. No futuro, se o Flávio hospedar XTTS remoto, ambos podem usar a mesma voz.

### 2.3 Deploy: direct install (não Docker)

A VM usa o mesmo modelo do PC Bruno: clone do repo + venv + systemd service.

Motivo:
- WireGuard roda no host (mais simples que dentro de container)
- Storage paths (`/data`, `/`) são diretamente acessíveis
- Sem overhead de Docker para um único processo
- Mesmo modelo do worker existente = menos variáveis

### 2.4 Render: MESMO pipeline do video-generate, deps opcionais

**Decisão crítica:** A VM usa o **mesmo** video-generate do PC Bruno, mas instala apenas as dependências core (sem torch/CUDA/ComfyUI).

**Por que não SimpleRenderer separado:**
- Reimplementar BGMSelector (anti-repetição, mood mapping) = duplicação
- Reimplementar geração de legendas (Whisper + alinhamento + subtitle_mapping) = duplicação
- Reimplementar composição FFmpeg (transições, scaling, safe areas) = duplicação
- Manter dois pipelines = toda feature nova precisa ser portada

**Por que não instalar tudo na VM:**
- torch+CUDA = ~5GB inúteis (VM não tem GPU)
- ComfyUI = ~5GB inúteis (GPCG não usa)
- MusicGen/AudioCraft = ~6.5GB inúteis (GPCG não usa)
- Total = ~15GB de deps que nunca são usadas

**Solução: dependências opcionais no video-generate**

O video-generate hoje tem `requirements.txt` flat (233 linhas, tudo obrigatório). Mas o código **já tem estrutura para separar**:
- `tts.py`: torch já tem `try/except` no import
- `generate.py`: torch e whisperx já têm `try/except`
- `bgm_selector.py`: não importa torch/ML (só JSON + arquivos)
- `generate_media.py` (ComfyUI/MusicGen/Runway/Veo): imports lazy, GPCG nunca chama

**Mudanças no video-generate:**

1. **Split `requirements.txt`** em:
   - `requirements-core.txt` — FFmpeg bindings, pydub, httpx, bgm_selector deps, ai_media_core
   - `requirements-gpu.txt` — torch+CUDA, TTS (Coqui), openai-whisper, whisperx
   - `requirements-comfyui.txt` — ComfyUI, audiocraft, etc

2. **`pyproject.toml`** com extras:
   ```toml
   [project.optional-dependencies]
   gpu = ["torch>=2.8", "TTS", "openai-whisper", "whisperx"]
   comfyui = ["comfyui-client", "audiocraft"]
   ```

3. **Instalação:**
   - PC Bruno: `pip install -e ".[gpu]"` → tem tudo (mesmo comportamento de hoje)
   - VM Flávio: `pip install -e .` → só core, sem torch/CUDA/ComfyUI

4. **`tts_factory.py`** — adicionar `KokoroTTSEngine`:
   ```python
   if engine_name == 'kokoro':
       from .tts_kokoro import KokoroTTSEngine
       engine = KokoroTTSEngine()  # HTTP direto para LiteLLM, sem torch
   ```

5. **`generate.py`** — `import whisper` vira lazy import (try/except). Se whisper não está instalado, legendas usam ASR remoto via LiteLLM.

6. **Render FFmpeg** — já funciona em CPU (libx264). Só precisa configurar device/cpu em vez de hardcoded CUDA.

**Resultado:**
- Um pipeline só. Zero duplicação.
- PC Bruno não muda (instala com `[gpu]`, mesmo comportamento)
- VM Flávio instala só core (~2GB em vez de ~17GB)
- BGMSelector, legendas, composição FFmpeg = mesmo código em ambos
- TTS é a única diferença: XTTSEngine vs KokoroTTSEngine (factory decide)

### 2.5 LLM/VLM: adapter com provider configurável

O `LLMClient` atual fala o protocolo nativo do Ollama (`/api/chat` com `images` base64).
LiteLLM fala o protocolo OpenAI (`/v1/chat/completions` com `image_url` data URI).

**Solução:** Refatorar `LLMClient` para suportar dois modos:
- `provider=ollama` → protocolo Ollama atual (PC Bruno)
- `provider=litellm` → protocolo OpenAI-compatible (VM Flávio)

A diferença principal é o formato das imagens no vision:
- Ollama: `"images": ["<base64>"]` no content da mensagem
- OpenAI: `"content": [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,<base64>"}}]`

E o endpoint:
- Ollama: `POST {host}/api/chat`
- LiteLLM: `POST {host}/v1/chat/completions`

E JSON mode:
- Ollama: `"format": "json"`
- LiteLLM: `"response_format": {"type": "json_object"}`

### 2.6 ASR: chunking + upload HTTP

O ASR remoto do Flávio exige:
1. Extrair só o áudio (FFmpeg na VM)
2. Comprimir (não WAV gigante — usar MP3 ou Opus)
3. Dividir em blocos (não arquivo de 30 min inteiro)
4. Enviar via multipart HTTP para `POST $LITELLM_BASE_URL/audio/transcriptions`
5. Juntar transcrições com timestamps ajustados

Chunk size recomendado: 5-10 minutos por bloco.

### 2.7 Rede: WireGuard direto VM ↔ VPS

```
VM Flávio (bmi)
  │
  │ WireGuard (novo)
  │ Endpoint: VPS Contabo IP público:51820
  │ IP da VM: 10.0.0.3/24
  │ AllowedIPs: 10.0.0.0/24
  │
  ▼
VPS Contabo (10.0.0.1)
  │
  │ Nginx escuta em 0.0.0.0:80/443
  │ → acessível via 10.0.0.1
  │
  ▼
GPCG API (http://10.0.0.1/gpcg)
```

A VM usa `GPCG_VPS_URL=http://10.0.0.1/gpcg` (via túnel WireGuard, sem HTTPS necessário pois o túnel já criptografa).

Precisamos:
1. Adicionar a VM como peer no WireGuard da VPS
2. Configurar WireGuard na VM apontando para a VPS
3. Declarar o endpoint IP + porta para o Flávio (ele pediu)

---

## 3. Fases de Implementação

### Fase 0: Job requeue automático (PRÉ-REQUISITO)

**Por quê primeiro:** Se a VM cair no meio de um job, o job precisa voltar para a fila automaticamente. Sem isso, multi-worker é perigoso — jobs ficam presos.

**O que fazer:**

1. **`src/gpcg/config.py`** — adicionar:
   ```python
   gpcg_job_lease_timeout: int = 300  # 5 minutos sem heartbeat = job stale
   ```

2. **`src/gpcg/api/worker_routes.py`** — adicionar lógica de requeue:
   - No endpoint `POST /jobs/claim`, antes de buscar jobs `queued`, verificar jobs `running` cujo worker está `offline` há mais de `gpcg_job_lease_timeout` segundos
   - Para cada job stale: resetar para `queued` (attempts já é incrementado no claim)
   - Se `attempts >= max_attempts` (ex: 3), marcar como `failed`

3. **`src/gpcg/api/worker_routes.py`** — adicionar endpoint opcional:
   - `POST /jobs/{id}/requeue` (admin only) para requeue manual

**Arquivos:**
- `src/gpcg/config.py` (adicionar config)
- `src/gpcg/api/worker_routes.py` (lógica de requeue no claim)

**Estimativa:** 1-2 dias

---

### Fase 0.5: Sincronização de gameplays entre workers (PRÉ-REQUISITO CRÍTICO)

**Por quê:** O design atual QUEBRA com multi-worker. Hoje:

1. Usuário upload → VPS temp dir
2. Worker de mapping baixa gameplay via SCP → processa → VPS **deleta** o arquivo temp
3. Arquivo fica **APENAS no worker que fez o mapping**
4. Worker de generation **assume que o arquivo já existe localmente** — não baixa da VPS
5. `local_db_sync.py` tem paths hardcoded em `/media/bruno/ToshibaHD/` para encontrar gameplays

**Cenário que quebra:**
- VM faz mapping de "Bully.mp4" → arquivo fica em `/data/gpcg/gameplays/123_Bully.mp4` na VM
- PC Bruno pega job de generation que usa "Bully.mp4"
- PC Bruno procura em `/media/bruno/ToshibaHD/...` → não encontra → **job falha**

#### Decisão TEMPORÁRIA: todo worker baixa todas as gameplays, VPS só apaga quando todos baixaram

> **NOTA DE ARQUITETURA (temporário):**
>
> Esta solução é uma **decisão temporária** para o cenário de 2 workers (PC Bruno + VM Flávio).
> Cada worker mantém sua própria cópia local de todas as gameplays.
> A VPS serve como **relay temporário** — não como storage permanente.
>
> **Como funciona:**
> - Usuário upload → VPS temp dir
> - Worker baixa gameplay → confirma download
> - VPS **NÃO apaga** até que **TODOS** os workers registrados tenham confirmado download
> - Quando todos confirmaram → VPS apaga o arquivo temp
> - Cada worker fica com sua cópia local permanentemente
>
> **Por que temporário:**
> - Não escala para muitos workers (cada um baixa tudo)
> - Desperdiça storage (gameplay duplicada em cada worker)
> - Transferência inicial é lenta (baixar todas as gameplays ao adicionar um worker)
> - VPS precisa esperar todos os workers baixarem antes de limpar
>
> **Plano futuro:** migrar para storage compartilhado (S3, Backblaze B2, ou solução dedicada)
> onde a gameplay é armazenada uma vez e baixada on-demand por qualquer worker.
>
> **Quando migrar:** quando tivermos 3+ workers, ou quando storage local dos workers
> começar a encher, ou quando a transferência inicial de gameplays novas se tornar
> um gargalo operacional.
>
> **O que já está pronto para a migração:** o mecanismo de download (SCP + HTTP streaming)
> já existe e é reutilizável. A mudança futura será: em vez de "baixar tudo no startup",
> será "baixar on-demand quando precisar" — o que é uma mudança simples no trigger.

#### 0.5.1 Tracking de downloads por worker

**`src/gpcg/domain/models.py`** — adicionar modelo de tracking:

```python
class GameplayDownload(Base):
    """Registra quais workers já baixaram cada gameplay.
    
    FIXME: TEMPORÁRIO — este modelo existe apenas enquanto a VPS serve como relay.
    Quando migrar para storage compartilhado (S3/B2), a VPS não precisará mais
    saber quem baixou o quê — o arquivo fica no storage externo e cada worker
    baixa on-demand. Este modelo pode ser removido nessa migração.
    """
    __tablename__ = "gameplay_downloads"
    
    id: int (PK)
    gameplay_source_id: int (FK → gameplay_sources.id)
    worker_id: str (FK → workers.worker_id)
    downloaded_at: datetime
    checksum_verified: bool
    
    __table_args__ = (UniqueConstraint("gameplay_source_id", "worker_id"),)
```

#### 0.5.2 VPS só apaga quando todos os workers confirmaram download

**`src/gpcg/api/worker_routes.py`** — modificar `confirm_download`:

Hoje: após confirmar checksum, VPS deleta arquivo de `temp_uploads` imediatamente.

Novo comportamento:
- Após confirmar checksum, registrar `GameplayDownload(worker_id, source_id)`
- Verificar se **TODOS** os workers registrados já têm `GameplayDownload` para esta gameplay
- Se sim → deletar arquivo de `temp_uploads`
- Se não → manter arquivo (outros workers ainda precisam baixar)

```python
# worker_routes.py — confirm_download

# FIXME: TEMPORÁRIO — lógica de "apagar só quando todos baixaram" existe porque
# a VPS tem storage limitado e não pode manter gameplays permanentemente.
# Quando migrar para storage compartilhado (S3/B2), o arquivo vai direto para
# o storage externo no upload, e a VPS nunca guarda o arquivo localmente.
# Este bloco inteiro de tracking + cleanup condicional pode ser removido.

def confirm_download(source_id, worker_id, checksum_ok):
    if not checksum_ok:
        raise HTTPException(400, "Checksum mismatch")
    
    # Registrar download deste worker
    download = GameplayDownload(
        gameplay_source_id=source_id,
        worker_id=worker_id,
        downloaded_at=datetime.utcnow(),
        checksum_verified=True,
    )
    db.add(download)
    db.commit()
    
    # Verificar se todos os workers conhecidos já baixaram
    all_workers = db.query(Worker).filter(
        Worker.status.in_(["online", "offline"])  # todos os workers registrados
    ).all()
    
    downloads = db.query(GameplayDownload).filter(
        GameplayDownload.gameplay_source_id == source_id
    ).all()
    
    downloaded_worker_ids = {d.worker_id for d in downloads}
    all_worker_ids = {w.worker_id for w in all_workers}
    
    if all_worker_ids.issubset(downloaded_worker_ids):
        # Todos baixaram — pode apagar da VPS
        source = db.query(GameplaySource).get(source_id)
        temp_path = temp_uploads_dir / source.storage_key
        if temp_path.exists():
            temp_path.unlink()
            log.info(f"All workers downloaded gameplay {source_id}, deleted temp file")
        # Limpar storage_key para indicar que arquivo não está mais na VPS
        source.storage_key = None
        db.commit()
    else:
        pending = all_worker_ids - downloaded_worker_ids
        log.info(f"Gameplay {source_id} still pending download from: {pending}")
```

#### 0.5.3 Worker baixa gameplay quando precisa (mapping E generation)

**`src/gpcg/worker/remote_worker.py`** — duas mudanças:

**Mudança 1: download_gameplay já funciona para mapping (não mudar)**

O método `download_gameplay()` já baixa via SCP/HTTP e confirma download.

**Mudança 2: generation job também baixa gameplay**

Hoje `_process_generation_job` não baixa gameplay — assume que existe localmente.

Novo: antes de chamar `run_generation_locally`, verificar se arquivos existem localmente. Se não, baixar da VPS.

```python
def _ensure_gameplay_files(self, job_data: dict) -> None:
    """Garante que arquivos de gameplay existem localmente para generation.
    
    TODO: TEMPORÁRIO — baixa a gameplay inteira se o worker não tem.
    Quando migrar para storage compartilhado, baixar on-demand apenas os
    clipes que serão usados no vídeo, não a gameplay inteira.
    """
    sources = job_data.get("gameplay_sources", [])
    for src in sources:
        filename = src.get("filename", "")
        source_id = src.get("id")
        local_path = self.storage_root / "gameplays" / f"{source_id}_{filename}"
        if not local_path.exists():
            log.info(f"Gameplay {filename} not found locally, downloading from VPS...")
            # FIXME: se a VPS já apagou o arquivo (todos baixaram), este download
            # vai falhar. Quando migrar para storage compartilhado, baixar de S3/B2.
            # Por ora, se falhar, o job falha com erro claro.
            self.download_gameplay(src)
```

#### 0.5.4 Sincronização inicial: worker baixa todas as gameplays no startup

**`src/gpcg/worker/remote_worker.py`** — adicionar método de sync inicial:

```python
def sync_all_gameplays(self) -> None:
    """Baixa todas as gameplays disponíveis na VPS que o worker não tem localmente.
    
    TODO: TEMPORÁRIO — chamado no startup do worker para garantir que tem todas
    as gameplays. Quando migrar para storage compartilhado, este método não será
    necessário — workers baixarão on-demand apenas quando precisarem.
    """
    resp = self.client.get("/api/gameplays/list-for-sync")
    sources = resp.json().get("sources", [])
    
    for src in sources:
        source_id = src["id"]
        filename = src["filename"]
        local_path = self.storage_root / "gameplays" / f"{source_id}_{filename}"
        
        if not local_path.exists():
            log.info(f"Syncing gameplay {filename} (id={source_id})...")
            self.download_gameplay(src)
        else:
            log.debug(f"Gameplay {filename} already exists locally, skipping")
```

**`src/gpcg/api/worker_routes.py`** — adicionar endpoint:

```python
@router.get("/gameplays/list-for-sync")
def list_gameplays_for_sync(_: None = Depends(worker_auth)):
    """Lista todas as gameplays com arquivo ainda disponível na VPS.
    
    TODO: TEMPORÁRIO — usado para sync inicial de workers novos.
    Só retorna gameplays que ainda têm arquivo na VPS (storage_key não nulo).
    Quando migrar para storage compartilhado, este endpoint lista do S3/B2.
    """
    sources = db.query(GameplaySource).filter(
        GameplaySource.processing_status == GameplayProcessingStatus.ready.value,
        GameplaySource.storage_key.isnot(None),  # ainda tem arquivo na VPS
    ).all()
    return {"sources": [serialize_for_sync(s) for s in sources]}
```

#### 0.5.5 Mapeamento não duplicado

**JÁ FUNCIONA** — não precisa mudar nada:

1. **`/jobs/claim` é atômico**: UPDATE condicional previne que dois workers pegam o mesmo job. Se VM pegar o job de mapping, PC Bruno não consegue pegar (status muda de `queued` para `running`).

2. **`create-mapping-job` previne duplicação**: se `processing_status` já é `mapping`, `mapped`, ou `ready`, retorna 409. Não cria segundo job para a mesma gameplay.

3. **Resultado**: cada gameplay é mapeada uma única vez, por um único worker. O mapeamento fica na VPS (eventos no DB) e é disponível para ambos os workers em jobs de generation.

#### 0.5.6 Flexibilizar paths hardcoded

**`src/gpcg/worker/local_db_sync.py`** — modifier `_resolve_local_gameplay_path`:

```python
def _resolve_local_gameplay_path(vps_path: str, filename: str, storage_root: Path) -> Optional[str]:
    """Resolve a VPS gameplay file path to a local file path.
    
    TODO: TEMPORÁRIO — procura em paths locais e em search_dirs configuráveis.
    Quando migrar para storage compartilhado, sempre baixar de S3/B2 se não
    existir local.
    """
    # Paths padrão (sempre procurar)
    search_dirs = [
        storage_root / "gameplays",
        storage_root / "data" / "gameplays",
        storage_root / "data" / "inbox",
    ]
    
    # Paths extras via env var (ex: Captures do OBS no PC Bruno)
    # GPCG_GAMEPLAY_SEARCH_DIRS=/media/bruno/ToshibaHD/Captures,/media/bruno/ToshibaHD
    extra_dirs = os.environ.get("GPCG_GAMEPLAY_SEARCH_DIRS", "")
    if extra_dirs:
        for d in extra_dirs.split(","):
            search_dirs.append(Path(d.strip()))
    
    # Procurar arquivo em todos os dirs
    for d in search_dirs:
        candidate = d / filename
        if candidate.exists():
            return str(candidate)
        # Também procurar com prefixo {source_id}_
        for f in d.glob(f"*_{filename}"):
            return str(f)
    
    # Último recurso: find recursivo nos search_dirs
    for d in search_dirs:
        try:
            result = subprocess.run(
                ["find", str(d), "-name", filename, "-type", "f"],
                capture_output=True, text=True, timeout=10,
            )
            if result.stdout.strip():
                return result.stdout.strip().split("\n")[0]
        except Exception:
            pass
    
    return None
```

**PC Bruno** mantém comportamento atual via env var:
```
GPCG_GAMEPLAY_SEARCH_DIRS=/media/bruno/ToshibaHD/Captures,/media/bruno/ToshibaHD
```

**VM Flávio** não precisa dessa env var — todos os arquivos vêm do download para `{storage_root}/gameplays/`.

#### 0.5.7 Migração de gameplays existentes

As gameplays já upadas no PC Bruno (em `/media/bruno/ToshibaHD/gpcg/gameplays/`) precisam estar disponíveis na VPS para que a VM possa baixá-las no primeiro sync.

**Script de migração:** `scripts/migrate-gameplays-to-vps.sh`

```bash
#!/bin/bash
# Migra gameplays locais para a VPS (relay temporário)
# TODO: TEMPORÁRIO — necessário para que a VM Flávio tenha acesso às gameplays
# que já existiam no PC Bruno antes da VM entrar no sistema.
# Quando migrar para storage compartilhado, este script será substituído por
# um upload direto para S3/B2.

VPS_HOST="10.0.0.1"  # via WireGuard
VPS_USER="root"
VPS_VOLUME="/var/lib/docker/volumes/gpcg_gpcg-data/_data"
LOCAL_DIR="/media/bruno/ToshibaHD/gpcg/gameplays"
TEMP_UPLOADS="${VPS_VOLUME}/temp_uploads"

# Para cada gameplay local
for f in "$LOCAL_DIR"/*.mp4; do
    filename=$(basename "$f")
    echo "Migrando $filename para VPS..."
    scp "$f" "$VPS_USER@$VPS_HOST:$TEMP_UPLOADS/$filename"
done

echo "Migração completa."
echo "Gameplays disponíveis na VPS para download pelos workers."
echo "A VPS vai apagar cada arquivo depois que todos os workers confirmarem download."
```

#### 0.5.8 Endpoint de download

**`src/gpcg/api/worker_routes.py`** — o endpoint de download existente (`GET /api/gameplays/{id}/download`) já serve de `temp_uploads`. Não precisa mudar — enquanto o arquivo estiver na VPS (ainda não foi apagado porque nem todos baixaram), o endpoint funciona.

A única mudança é que o arquivo pode não estar mais na VPS se todos já baixaram. Nesse caso, o endpoint retorna 404 e o worker precisa ter o arquivo localmente (ou o job falha com erro claro).

```python
# worker_routes.py — download endpoint (sem mudança significativa)
# FIXME: TEMPORÁRIO — se o arquivo já foi apagado da VPS (todos baixaram),
# retorna 404. Quando migrar para storage compartilhado, redirecionar para
# URL do S3/B2 (presigned URL).
```

#### 0.5.9 Config

**`src/gpcg/config.py`** — adicionar:

```python
# FIXME: TEMPORÁRIO — VPS serve como relay temporário para gameplays.
# Arquivo fica na VPS até todos os workers confirmarem download, depois é apagado.
# Quando migrar para storage compartilhado (S3/B2), estas configs não serão necessárias.
gpcg_gameplay_download_tracking: bool = True  # tracking de downloads por worker
```

**Arquivos:**
- `src/gpcg/domain/models.py` (adicionar modelo GameplayDownload)
- `src/gpcg/config.py` (adicionar config de tracking)
- `src/gpcg/api/worker_routes.py` (confirm_download com tracking + cleanup condicional + endpoint list-for-sync)
- `src/gpcg/worker/remote_worker.py` (download on-demand em generation + sync inicial)
- `src/gpcg/worker/local_db_sync.py` (flexibilizar paths hardcoded + GPCG_GAMEPLAY_SEARCH_DIRS)
- `scripts/migrate-gameplays-to-vps.sh` (novo — migração de gameplays existentes)

**Estimativa:** 3-4 dias

---

### Fase 1: Adapter LLM/VLM (Ollama → LiteLLM)

**Por quê:** É a base de tudo. Sem LLM/VLM remoto, a VM não pensa nem enxerga.

#### 1.1 Config

**`src/gpcg/config.py`** — adicionar:
```python
# LLM Provider
gpcg_llm_provider: str = "ollama"  # ollama | litellm
gpcg_litellm_base_url: str = ""     # ex: http://10.0.0.5:4000
gpcg_litellm_api_key: str = ""      # se necessário

# Model names com prefixo do provider
# LiteLLM usa "ollama/gemma3:12b" em vez de "gemma3:12b"
gpcg_llm_model_litellm: str = "ollama/llama3.1:8b"
gpcg_vlm_model_litellm: str = "ollama/gemma3:12b"
```

#### 1.2 Refatorar LLMClient

**`src/gpcg/infrastructure/llm.py`** — refatorar `LLMClient`:

```python
class LLMClient:
    def __init__(self):
        s = get_settings()
        self.provider = s.gpcg_llm_provider  # "ollama" ou "litellm"
        
        if self.provider == "litellm":
            self.host = s.gpcg_litellm_base_url.rstrip("/")
            self.api_key = s.gpcg_litellm_api_key
            self.text_model = s.gpcg_llm_model_litellm
            self.vlm_model = s.gpcg_vlm_model_litellm
            self.chat_endpoint = f"{self.host}/v1/chat/completions"
        else:
            self.host = s.ollama_host.rstrip("/")
            self.text_model = s.gpcg_llm_model
            self.vlm_model = s.gpcg_vlm_model
            self.chat_endpoint = f"{self.host}/api/chat"
```

**Método `chat()`** — dois caminhos:
- `provider=ollama`: payload atual (formato Ollama)
- `provider=litellm`: payload OpenAI-compatible
  ```json
  {
    "model": "ollama/llama3.1:8b",
    "messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
    "temperature": 0.7,
    "max_tokens": 2048,
    "stream": false
  }
  ```

**Método `vision()`** — dois caminhos:
- `provider=ollama`: `"images": ["<base64>"]` no content
- `provider=litellm`: content como array de partes:
  ```json
  {
    "role": "user",
    "content": [
      {"type": "text", "text": "prompt aqui"},
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,<base64>"}}
    ]
  }
  ```

**Método `chat_json()`** — JSON mode:
- `provider=ollama`: `"format": "json"`
- `provider=litellm`: `"response_format": {"type": "json_object"}`

**Método `embed()`** — embeddings:
- `provider=ollama`: `POST {host}/api/embeddings` (atual)
- `provider=litellm`: `POST {host}/v1/embeddings` (formato OpenAI)

**Método `unload_model()` / `unload_all_models()`**:
- `provider=litellm`: no-op (modelos estão no servidor remoto, não local)

#### 1.3 Rate limit pacing — retry com backoff para TODAS as chamadas remotas

Toda chamada remota (LLM, VLM, Kokoro TTS, ASR) está sujeita a rate limit do LiteLLM do Flávio. Implementar retry com backoff exponencial em todas:

```python
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

def parse_retry_after(response: httpx.Response) -> float | None:
    """Extrai tempo de espera do header Retry-After da resposta 429.
    
    O header pode vir em dois formatos (RFC 7231):
    1. Inteiro (segundos): Retry-After: 120
    2. HTTP-date: Retry-After: Wed, 21 Oct 2025 07:28:00 GMT
    
    Retorna segundos a esperar, ou None se header não existir/for inválido.
    """
    value = response.headers.get("Retry-After")
    if not value:
        return None
    
    # Formato 1: inteiro (segundos)
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    
    # Formato 2: HTTP-date
    try:
        target = parsedate_to_datetime(value)
        if target is None:
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        delta = (target - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta)
    except (TypeError, ValueError):
        return None


def call_with_retry(func, max_retries=5, base_wait=1.0, max_wait=60.0):
    """Chama func() com retry em caso de rate limit (429).
    
    Prioridade de espera:
    1. Header Retry-After do servidor (se presente) — tempo exato que o servidor pede
    2. Backoff exponencial (fallback se servidor não enviar Retry-After ou for inválido)
    
    Logar cada retry com warning.
    """
    for attempt in range(max_retries):
        try:
            return func()
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429 or attempt == max_retries - 1:
                raise
            # Priorizar Retry-After do servidor, senão backoff exponencial
            retry_after = parse_retry_after(e.response)
            wait = retry_after if retry_after is not None else min(base_wait * (2 ** attempt), max_wait)
            log.warning(f"Rate limit hit (429), retry {attempt+1}/{max_retries} in {wait:.1f}s")
            time.sleep(wait)
```

**Lógica de espera:**
1. **Prioridade 1:** Header `Retry-After` da resposta 429 — o servidor diz exatamente quanto esperar
   - Pode ser inteiro (segundos): `Retry-After: 120`
   - Pode ser HTTP-date: `Retry-After: Wed, 21 Oct 2025 07:28:00 GMT`
   - `parse_retry_after()` trata os dois formatos
2. **Prioridade 2:** Backoff exponencial (1s, 2s, 4s, 8s, 16s, cap 60s) — fallback se:
   - Servidor não enviar o header
   - Header for inválido/não-parseable
   - Data já passou (delta negativo → 0)

Não é aleatório — é o tempo que o servidor retorna, ou backoff exponencial como fallback.

**Como detectar o 429:** httpx levanta `HTTPStatusError` se `response.raise_for_status()` for chamado. Verificar `e.response.status_code == 429` e ler `e.response.headers.get("Retry-After")`.

**Onde aplicar:**
- `LLMClient.chat()` — LLM (script, editorial, metadata)
- `LLMClient.vision()` — VLM (análise de frames no mapping)
- `KokoroTTSEngine._synthesize_chunk()` — TTS (cada chunk da narração)
- `RemoteASRTranscriber.transcribe()` — ASR (legendas)

**Rate limits conhecidos (testados em 2026-08-15):**
- VLM (gemma3:12b): 6 req/min → 15 req/min (aumentado)
- Kokoro TTS: 15 req/min
- ASR (whisper): não medido, assumir 15 req/min
- LLM texto: não medido, assumir 15 req/min

**Por que NÃO paralelizar jobs enquanto espera:**
- Worker é síncrono e single-job (processa um job por vez)
- Render CPU (~26s/vídeo de 60s — benchmark real) não é gargalo
- No pior caso (Kokoro 15 chunks a 15 req/min), espera ~4s — irrelevante vs 26s de render
- Paralelizar 2 renders CPU em 4 vCPUs divide CPU e deixa ambos mais lentos
- Complexidade de paralelização não justifica economia de segundos

**Decisão:** Retry com backoff agora. Paralelização de stages (I/O overlap com CPU) é melhoria futura se necessário.

**Arquivos:**
- `src/gpcg/config.py` (adicionar configs)
- `src/gpcg/infrastructure/llm.py` (refatorar LLMClient)

**Estimativa:** 2-3 dias

---

### Fase 2: Adapter ASR (faster-whisper local → LiteLLM remoto)

#### 2.1 Config

**`src/gpcg/config.py`** — adicionar:
```python
# ASR Provider
gpcg_asr_provider: str = "faster_whisper"  # faster_whisper | litellm
gpcg_asr_chunk_minutes: int = 5  # tamanho do bloco em minutos
gpcg_asr_compressed_format: str = "mp3"  # mp3 | opus
gpcg_asr_compressed_bitrate: str = "64k"
```

#### 2.2 Criar ASR adapter remoto

**`src/gpcg/infrastructure/asr_transcriber.py`** — adicionar classe `RemoteASRTranscriber`:

```python
class RemoteASRTranscriber:
    """ASR via LiteLLM remoto com chunking."""
    
    def __init__(self, base_url, api_key="", language="pt"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.language = language
    
    def transcribe(self, audio_path, language=""):
        # 1. Extrair áudio comprimido do vídeo (se necessário)
        compressed = self._ensure_compressed_audio(audio_path)
        
        # 2. Dividir em blocos de N minutos
        chunks = self._split_audio(compressed, chunk_minutes=5)
        
        # 3. Enviar cada bloco para LiteLLM
        all_segments = []
        for chunk_path, chunk_offset in chunks:
            segments = self._transcribe_chunk(chunk_path)
            # Ajustar timestamps pelo offset do bloco
            for seg in segments:
                seg["start"] += chunk_offset
                seg["end"] += chunk_offset
                all_segments.append(seg)
        
        # 4. Limpar temporários
        self._cleanup(chunks)
        
        return all_segments
    
    def _split_audio(self, audio_path, chunk_minutes=5):
        # FFmpeg: dividir em blocos de chunk_minutes
        # Retorna lista de (path, offset_em_segundos)
        pass
    
    def _transcribe_chunk(self, chunk_path):
        # POST {base_url}/audio/transcriptions
        # multipart: file=@chunk_path, model=whisper, language=pt
        # Retorna segments com start/end/text
        pass
```

#### 2.3 Factory no gameplay_analyzer

**`src/gpcg/application/gameplay_analyzer.py`** — selecionar provider:

```python
def _create_asr(self):
    s = get_settings()
    if s.gpcg_asr_provider == "litellm":
        return RemoteASRTranscriber(
            base_url=s.gpcg_litellm_base_url,
            api_key=s.gpcg_litellm_api_key,
        )
    else:
        return ASRTranscriber()  # faster-whisper local (atual)
```

**Arquivos:**
- `src/gpcg/config.py` (adicionar configs)
- `src/gpcg/infrastructure/asr_transcriber.py` (adicionar RemoteASRTranscriber)
- `src/gpcg/application/gameplay_analyzer.py` (factory)

**Estimativa:** 2-3 dias

---

### Fase 3: video-generate com deps opcionais + KokoroTTSEngine

Esta fase modifica o **video-generate** (não o GPCG) para suportar instalação CPU-only e adicionar Kokoro como TTSEngine. O GPCG continua usando o mesmo `VideoGenerateAdapter` — sem SimpleRenderer, sem duplicação.

#### 3.0 Princípio

O video-generate já tem estrutura para separar deps de GPU:
- `tts.py`: torch com `try/except` no import
- `generate.py`: torch e whisperx com `try/except`
- `bgm_selector.py`: não importa ML
- `generate_media.py` (ComfyUI/MusicGen): imports lazy, GPCG nunca chama

A mudança é **tornar isso oficial** com extras no pyproject.toml + lazy imports onde faltam.

#### 3.1 Split de dependências

**`video-generate/requirements.txt`** — hoje é flat (233 linhas, tudo obrigatório).

**Mudança:** Separar em três arquivos:

**`requirements-core.txt`** (novo — instala na VM):
```
# Sem torch, sem CUDA, sem TTS, sem whisper
httpx>=0.28
pydub
pydantic
python-dotenv
psutil
# ai_media_core deps
# bgm_selector deps (só JSON, pathlib)
# FFmpeg bindings leves
```

**`requirements-gpu.txt`** (novo — instala no PC Bruno com `[gpu]`):
```
-r requirements-core.txt
torch>=2.8
torchaudio>=2.8
torchvision
TTS  # Coqui TTS para XTTS
openai-whisper
whisperx
pyannote.audio
speechbrain
# NVIDIA CUDA packages
```

**`requirements-comfyui.txt`** (novo — só se usar ComfyUI/MusicGen):
```
-r requirements-gpu.txt
# ComfyUI client
# audiocraft (MusicGen)
```

**`video-generate/pyproject.toml`** — adicionar extras:
```toml
[project.optional-dependencies]
gpu = ["torch>=2.8", "TTS", "openai-whisper", "whisperx"]
comfyui = ["audiocraft"]
```

**Instalação:**
- PC Bruno: `pip install -e ".[gpu]"` (mesmo comportamento de hoje)
- VM Flávio: `pip install -e .` (só core, ~2GB em vez de ~17GB)

#### 3.2 Lazy imports onde faltam

**`video-generate/generate.py`** — `import whisper` é top-level (linha 15). Mudar para lazy:

```python
# ANTIGO (linha 15):
import whisper

# NOVO:
try:
    import whisper
    WHISPER_AVAILABLE = True
except ImportError:
    whisper = None
    WHISPER_AVAILABLE = False
```

**`video-generate/generate.py`** — `generate_auto_srt()` precisa de fallback:
```python
def generate_auto_srt(audio_file, original_text=None, subtitle_mapping=None, profile=None):
    if WHISPER_AVAILABLE:
        # fluxo atual: whisper local + alinhamento
        ...
    else:
        # Fallback: ASR remoto via LiteLLM
        # FIXME: TEMPORÁRIO — VM não tem whisper local.
        # Quando whisper for instalado na VM (se decidirmos), voltar para local.
        from src.generators.asr_remote import transcribe_remote
        segments = transcribe_remote(audio_file, base_url=os.getenv("LITELLM_BASE_URL"))
        # Gerar SRT a partir dos segments remotos
        ...
```

#### 3.3 KokoroTTSEngine

**`video-generate/src/generators/tts_kokoro.py`** — novo arquivo:

```python
from .tts_base import TTSEngine
from typing import Optional, Dict, Any

class KokoroTTSEngine(TTSEngine):
    """TTS via Kokoro no LiteLLM remoto.
    
    Sem torch, sem GPU, sem voice cloning.
    Vozes fixas: pm_alex, pm_santa, pf_dora.
    """
    
    VOICES = ["pm_alex", "pm_santa", "pf_dora"]
    
    def __init__(self):
        self.base_url = os.getenv("LITELLM_BASE_URL", "").rstrip("/")
        self.api_key = os.getenv("LITELLM_API_KEY", "")
        self.default_voice = os.getenv("KOKORO_VOICE", "pm_alex")
        self.max_chars = int(os.getenv("KOKORO_MAX_CHARS", "200"))
    
    def synthesize(
        self,
        text: str,
        output_path: str,
        speaker_wav: str = "",  # ignorado — Kokoro não usa voice cloning
        language: str = "pt",
        device: str = "cpu",  # ignorado — Kokoro é remoto
        speed: float = 1.0,
        **kwargs
    ) -> bool:
        # 1. Chunking de texto
        chunks = self._chunk_text(text, max_chars=self.max_chars)
        
        # 2. Sintetizar cada chunk via HTTP
        chunk_files = []
        for i, chunk in enumerate(chunks):
            chunk_path = f"{output_path}.part{i}.mp3"
            if not self._synthesize_chunk(chunk, self.default_voice, chunk_path):
                return False
            chunk_files.append(chunk_path)
        
        # 3. Merge com FFmpeg + normalizar para WAV 22050Hz mono
        # (formato esperado pelo pipeline downstream)
        self._merge_and_normalize(chunk_files, output_path)
        
        # 4. Cleanup
        for f in chunk_files:
            os.unlink(f)
        
        return True
    
    def _synthesize_chunk(self, text, voice, output_path):
        # POST {base_url}/audio/speech
        # body: {"model": "kokoro", "voice": voice,
        #        "input": text, "response_format": "mp3"}
        # Salvar response content em output_path
        ...
    
    def _chunk_text(self, text, max_chars=200):
        # Dividir por frases (. ! ?) respeitando max_chars
        ...
    
    def _merge_and_normalize(self, files, output):
        # FFmpeg concat + -ar 22050 -ac 1 -c:a pcm_s16le
        ...
```

**`video-generate/src/generators/tts_factory.py`** — adicionar Kokoro:

```python
def get_tts_engine(engine_name: Optional[str] = None) -> TTSEngine:
    if engine_name is None:
        engine_name = os.getenv('TTS_ENGINE', 'xtts').lower()
    
    if engine_name == 'xtts':
        from .tts_xtts import XTTSEngine
        engine = XTTSEngine()
    elif engine_name == 'kokoro':
        from .tts_kokoro import KokoroTTSEngine
        engine = KokoroTTSEngine()
    elif engine_name == 'styletts2':
        from .tts_styletts2 import StyleTTS2Engine
        engine = StyleTTS2Engine()
    else:
        raise ValueError(f"Unknown TTS engine: {engine_name}")
    
    return engine
```

#### 3.4 ASR remoto para legendas

**`video-generate/src/generators/asr_remote.py`** — novo arquivo:

```python
def transcribe_remote(audio_path: str, base_url: str, api_key: str = "") -> dict:
    """Transcreve áudio via LiteLLM /audio/transcriptions (whisper remoto).
    
    Usado na VM Flávio onde openai-whisper não está instalado.
    Retorna segments no mesmo formato que whisper.transcribe() retorna.
    """
    # POST {base_url}/audio/transcriptions
    # multipart: file=@audio, model=whisper, language=pt
    # Retorna: {text, segments, language}
    ...
```

#### 3.5 Config de render device

**`video-generate/generate.py`** — onde usa CUDA hardcoded, ler de config:

```python
# ANTIGO:
device = "cuda" if torch.cuda.is_available() else "cpu"

# NOVO:
device = os.getenv("RENDER_DEVICE", "auto")
if device == "auto":
    device = "cuda" if torch.cuda.is_available() else "cpu"
```

**VM Flávio:** `RENDER_DEVICE=cpu` (força CPU, não tenta CUDA)
**PC Bruno:** `RENDER_DEVICE=auto` (usa CUDA se disponível)

#### 3.6 GPCG — nenhuma mudança

O GPCG **não muda nada** nesta fase. O `VideoGenerateAdapter` continua chamando:
- `vg.synthesize_tts()` — que agora pode usar KokoroTTSEngine via factory
- `vg.select_music()` — BGMSelector, sem mudança
- `vg.render_video()` — composição FFmpeg, sem mudança (só device muda)

O GPCG só precisa passar a env var `TTS_ENGINE` para o subprocess do video-generate:

**`src/gpcg/infrastructure/video_generate_adapter.py`** — adicionar env var ao subprocess:
```python
env = os.environ.copy()
env["TTS_ENGINE"] = self.settings.gpcg_tts_engine  # "xtts" ou "kokoro"
env["LITELLM_BASE_URL"] = self.settings.gpcg_litellm_base_url
env["LITELLM_API_KEY"] = self.settings.gpcg_litellm_api_key
env["KOKORO_VOICE"] = self.settings.gpcg_kokoro_voice
env["RENDER_DEVICE"] = self.settings.gpcg_render_device
subprocess.run([sys.executable, script_path], env=env, ...)
```

**`src/gpcg/config.py`** — adicionar:
```python
# TTS Engine (passado para video-generate via env var)
gpcg_tts_engine: str = "xtts"  # xtts | kokoro
gpcg_kokoro_voice: str = "pm_alex"  # pm_alex | pm_santa | pf_dora
gpcg_render_device: str = "auto"  # auto | cuda | cpu
```

#### 3.7 Worker reporta capacidade de TTS

**`src/gpcg/worker/remote_worker.py`** — modificar registro para incluir TTS info:

```python
{
    "worker_id": "home-pc",
    "capabilities": ["mapping", "generation"],
    "metadata_json": {  # usar metadata_json existente, não criar campo novo
        "tts": {
            "engine": "xtts",
            "supports_cloning": True,
            "voices": ["bruno.wav"],
        }
    }
}
```

**VM Flávio:**
```python
{
    "worker_id": "vm-flavio-bmi",
    "capabilities": ["mapping", "generation"],
    "metadata_json": {
        "tts": {
            "engine": "kokoro",
            "supports_cloning": False,
            "voices": ["pm_alex", "pm_santa", "pf_dora"],
        }
    }
}
```

**`src/gpcg/api/worker_routes.py`** — endpoint de registro já aceita `metadata_json` (campo existente em Worker). Só precisa garantir que é persistido.

**`src/gpcg/domain/models.py`** — **NENHUMA mudança**. Worker já tem `metadata_json` (JSON column).

#### 3.8 Por que isso é melhor que SimpleRenderer

| | SimpleRenderer | Deps opcionais |
|---|---|---|
| Codebases | 2 (GPCG + SimpleRenderer) | 1 (video-generate) |
| BGMSelector | Reimplementar ou copiar | Mesmo código |
| Legendas | Reimplementar | Mesmo código (whisper ou remoto) |
| Composição FFmpeg | Reimplementar | Mesmo código |
| Manutenção | Dupla (toda feature nova portar) | Única |
| Esforço | 8-10 dias | 3-4 dias |
| Risco | Alto (quebrar fluxo) | Baixo (PC Bruno não muda) |

**Arquivos no video-generate:**
- `requirements-core.txt` (novo)
- `requirements-gpu.txt` (novo)
- `requirements-comfyui.txt` (novo)
- `pyproject.toml` (adicionar extras)
- `src/generators/tts_kokoro.py` (novo — KokoroTTSEngine)
- `src/generators/asr_remote.py` (novo — ASR remoto para legendas)
- `src/generators/tts_factory.py` (adicionar Kokoro)
- `generate.py` (lazy import whisper + config device + fallback ASR remoto)

**Arquivos no GPCG:**
- `src/gpcg/config.py` (adicionar gpcg_tts_engine, gpcg_kokoro_voice, gpcg_render_device)
- `src/gpcg/infrastructure/video_generate_adapter.py` (passar env vars ao subprocess)
- `src/gpcg/worker/remote_worker.py` (reportar TTS info no metadata_json do registro)

**Estimativa:** 3-4 dias (vs 8-10 dias do SimpleRenderer)


---

### Fase 4: YOLO CPU + Storage paths

#### 4.1 YOLO device configurável

**`src/gpcg/config.py`** — adicionar:
```python
gpcg_yolo_device: str = "cuda"  # cuda | cpu
```

**`src/gpcg/infrastructure/player_detector.py`** — ler do config:
```python
class DetectorConfig:
    device: str = get_settings().gpcg_yolo_device  # em vez de "cuda" hardcoded
```

#### 4.2 Storage paths configuráveis

**`src/gpcg/config.py`** — tornar configuráveis:
```python
# Já existe mas tem default hardcoded — mudar default para relativo
gameplay_inbox_dir: str = "./data/gameplay-inbox"  # era /media/bruno/ToshibaHD

# video-generate paths — já são env vars mas com default hardcoded
# Manter como está (só usado quando render_provider=video_generate)
```

**`src/gpcg/worker/local_db_sync.py`** — flexibilizar paths hardcoded:

Os paths hardcoded de busca de gameplays (`/media/bruno/ToshibaHD/Captures/` etc.) 
precisam ser lidos do config ou do `storage_root` do worker.

**Arquivos:**
- `src/gpcg/config.py` (adicionar gpcg_yolo_device, mudar defaults)
- `src/gpcg/infrastructure/player_detector.py` (ler device do config)
- `src/gpcg/worker/local_db_sync.py` (flexibilizar paths)

**Estimativa:** 1 dia

---

### Fase 5: WireGuard VM ↔ VPS

#### 5.1 Por que WireGuard é necessário

A VM tem internet pública e consegue alcançar a VPS via HTTPS (testado: HTTP 200 em 745ms). Mas WireGuard é **necessário** por três motivos técnicos:

**1. SCP download de gameplays (crítico)**

O worker baixa gameplays da VPS via SCP como caminho primário (`remote_worker.py` linhas 387-444):
```python
scp -o ConnectTimeout=10 root@{ssh_host}:{volume_path}/temp_uploads/{storage_key} {local_path}
```
- Gameplays pesam 400MB a 4.4GB
- SCP tem timeout de 600s (10 min) — robusto para arquivos grandes
- SSH da VPS **só é acessível via WireGuard** (10.0.0.1), não exposto na internet pública
- Sem WireGuard, SCP falha e cai para HTTP streaming (timeout 300s = 5 min) — insuficiente para 4.4GB em conexão lenta

**2. SCP download de voice files**

Vozes custom do usuário são baixadas via SCP (`remote_worker.py` linhas ~520):
- Mesmo mecanismo do gameplay download
- Sem WireGuard, fallback HTTP pode falhar para vozes grandes

**3. Latência e bandwidth para operações em tempo real**

| Métrica | Via WireGuard | Via HTTPS público |
|---------|--------------|-------------------|
| Latência | ~238ms | ~745ms |
| SSH access | Sim (10.0.0.1:22) | Não (SSH não exposto) |
| SCP (gameplay 4.4GB) | Funciona (600s timeout) | Não funciona (SSH indisponível) |
| HTTP streaming fallback | Funciona | Funciona (mas 300s timeout pode estourar) |
| Heartbeat (10s interval) | OK | OK (745ms é aceitável) |
| Upload vídeo (50-200MB) | OK via HTTP | OK via HTTP |

**4. Acesso SSH para debug/administração**

O Bruno precisa de SSH à VPS da VM para troubleshooting. Sem WireGuard, teria que usar o PC Bruno como jump host.

#### 5.2 Contexto do my-vps e vm-flavio

**Ferramentas de acesso remoto:**

| Ferramenta | Repo | Acesso | Uso |
|------------|------|--------|-----|
| `my-vps` | `my-vps/` | WireGuard manual → VPS Contabo (10.0.0.1) | Bruno acessa VPS |
| `vm-flavio` | `vm-flavio/` | NetBird → VM Flávio (bmi.netbird.cloud) | Bruno acessa VM Flávio |

Ambas já estão instaladas no PC Bruno e funcionando:
- `my-vps "echo ok"` → VPS Contabo via WireGuard
- `vm-flavio "echo ok"` → VM Flávio via NetBird

**my-vps** (`/home/bruno/Desenvolvimento/brunointegrations/my-vps/`):
- PC Bruno usa `wg0.conf` → `Address = 10.0.0.2/24` → conecta à VPS (`10.0.0.1`)
- VPS Contabo tem `wg0.conf` com o PC Bruno como peer (`AllowedIPs = 10.0.0.2/32`)
- my-vps NÃO tem comando `--add-peer` — só gerencia uma máquina (o Bruno)
- `wg-watchdog` (systemd timer) mantém o túnel ativo no PC Bruno

**vm-flavio** (`/home/bruno/Desenvolvimento/brunointegrations/vm-flavio/`):
- Acesso à VM Flávio via NetBird (P2P WireGuard gerenciado)
- FQDN: `bmi.netbird.cloud`, IP: `100.68.105.88`, latência: ~38ms
- NetBird auto-reconnect (não precisa de watchdog)
- Comandos: `vm-flavio "cmd"`, `vm-flavio --shell`, `vm-flavio --scp`, `vm-flavio --rsync`

Para a VM Flávio, precisamos:
1. Adicionar a VM como **peer novo** no WireGuard da VPS (IP `10.0.0.3`)
2. Configurar WireGuard na VM com sua própria chave
3. Instalar `my-vps` na VM (adaptado) ou configurar manualmente
4. Configurar SSH key da VM para acessar a VPS

**Decisão:** Instalar `my-vps` na VM com `wg0.conf` próprio (IP `10.0.0.3`). O `my-vps` funciona para qualquer cliente — só precisa do `wg0.conf` certo. O `wg-watchdog` também funciona na VM.

#### 5.3 Passo a passo — configurar WireGuard para a VM

**Passo 1: Na VM Flávio — gerar chave WireGuard própria**

```bash
vm-flavio --shell  # via NetBird
sudo apt install wireguard-tools -y
wg genkey | tee /etc/wireguard/vm-flavio-private.key | wg pubkey > /etc/wireguard/vm-flavio-public.key
sudo chmod 600 /etc/wireguard/vm-flavio-private.key
cat /etc/wireguard/vm-flavio-public.key  # anotar para o Passo 2
```

**Passo 2: Na VPS Contabo — adicionar a VM como peer**

Fazer isso via PC Bruno (que já tem WireGuard):
```bash
# Do PC Bruno
my-vps --shell

# Na VPS (como root):
# 2a. Adicionar peer dinamicamente (funciona imediatamente)
wg set wg0 peer <VM_PUBLIC_KEY_DO_PASSO_1> allowed-ips 10.0.0.3/32

# 2b. Persistir no wg0.conf (sobrevive a reboot)
# Editar /etc/wireguard/wg0.conf e adicionar no final:
cat >> /etc/wireguard/wg0.conf << 'EOF'

# VM Flávio (worker GPCG)
[Peer]
PublicKey = <VM_PUBLIC_KEY_DO_PASSO_1>
AllowedIPs = 10.0.0.3/32
EOF

# 2c. Anotar a chave pública da VPS (a VM precisa disso no Passo 3)
wg show wg0 public-key
```

**Passo 3: Na VM Flávio — criar wg0.conf**

```bash
vm-flavio --shell
sudo tee /etc/wireguard/wg0.conf << 'EOF'
[Interface]
PrivateKey = <VM_PRIVATE_KEY_DO_PASSO_1>
Address = 10.0.0.3/24

[Peer]
# VPS Contabo — GPCG
PublicKey = <VPS_PUBLIC_KEY_DO_PASSO_2C>
Endpoint = 161.97.133.242:51820
AllowedIPs = 10.0.0.0/24
PersistentKeepalive = 25
EOF
sudo chmod 600 /etc/wireguard/wg0.conf
```

**Passo 4: Na VM Flávio — levantar WireGuard e testar**

```bash
sudo wg-quick up wg0
ping -c 3 10.0.0.1  # deve responder (~238ms)
sudo systemctl enable wg-quick@wg0  # levantar no boot
```

**Passo 5: Na VM Flávio — gerar chave SSH e adicionar na VPS**

```bash
vm-flavio --shell
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N "" -C "vm-flavio-gpcg"
cat ~/.ssh/id_ed25519.pub  # anotar
```

Adicionar na VPS (via PC Bruno):
```bash
# Do PC Bruno
my-vps --shell
echo "<VM_SSH_PUBLIC_KEY>" >> /root/.ssh/authorized_keys
```

Ou via SSH da própria VM (depois do WireGuard no Passo 4):
```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub root@10.0.0.1
```

**Passo 6: Na VM Flávio — instalar my-vps (opcional, recomendado)**

```bash
vm-flavio --shell
git clone git@github.com:brunomrtns/my-vps.git ~/my-vps
cd ~/my-vps
# wg0.conf já foi criado no Passo 3
sudo cp /etc/wireguard/wg0.conf src/wg0.conf
sudo ./INSTALL.sh
# Testar
my-vps "echo ok"
my-vps --wg-status
```

Isso instala:
- `my-vps` comando (acesso SSH/SCP à VPS via WireGuard)
- `wg-watchdog` (systemd timer que mantém túnel ativo)

**Passo 7: Declarar endpoint para o Flávio**

O Flávio pediu para declarar o IP e porta do endpoint WireGuard:
- **Endpoint:** `161.97.133.242:51820` (IP público da VPS Contabo, porta WireGuard)
- **Protocolo:** UDP (WireGuard)
- **Direção:** Outbound da VM → VPS (a VM inicia a conexão)
- A VM só precisa de outbound UDP para `161.97.133.242:51820`. Não precisa de inbound.

**Confirmado: outbound UDP já está liberado.** Testado em 2026-08-15:
- `tcpdump` na VPS escutando porta 51820
- `nc -u` da VM enviando pacote UDP para `161.97.133.242:51820`
- Pacote chegou: `179.181.85.230.58891 > 161.97.133.242.51820: UDP`
- Não precisa pedir nada para o Flávio

#### 5.4 Variáveis de ambiente do worker

Com WireGuard configurado, o worker na VM usa:

```ini
# URL da API via WireGuard (mais rápido, bypassa nginx público)
GPCG_VPS_URL=http://10.0.0.1/gpcg

# SSH para SCP (caminho primário de download de gameplays)
GPCG_SSH_HOST=10.0.0.1
GPCG_SSH_USER=root
GPCG_DOCKER_VOLUME=/var/lib/docker/volumes/gpcg_gpcg-data/_data
```

#### 5.5 Verificações

1. `ping 10.0.0.1` da VM → deve responder (~238ms)
2. `ssh root@10.0.0.1` da VM → deve conectar (sem senha, via chave)
3. `curl http://10.0.0.1/gpcg/api/health` → HTTP 200
4. `scp root@10.0.0.1:/var/lib/docker/volumes/gpcg_gpcg-data/_data/teste .` → deve funcionar
5. `my-vps --wg-status` na VM → deve mostrar handshake recente
6. Reiniciar VM → WireGuard deve levantar automaticamente (`wg-quick@wg0` enabled)
7. `wg-watchdog` rodando → mantém túnel ativo

**Arquivos:**
- VPS: `/etc/wireguard/wg0.conf` (adicionar peer da VM, persistir)
- VPS: `/root/.ssh/authorized_keys` (adicionar chave SSH da VM)
- VM: `/etc/wireguard/wg0.conf` (criar com IP 10.0.0.3)
- VM: `~/.ssh/id_ed25519` (chave SSH da VM)
- VM: `~/my-vps/` (instalação do my-vps, opcional)

**Estimativa:** 1-2 dias (inclui coordenação com Flávio para liberar outbound UDP)

---

### Fase 6: Instalar worker na VM

#### 6.1 Setup script

Criar `scripts/setup-worker-vm.sh` (baseado no `setup-worker.sh` mas adaptado para VM):

```bash
#!/bin/bash
# Setup do worker GPCG na VM do Flávio
# Diferenças do setup-worker.sh:
# - Não verifica GPU (não tem)
# - Não verifica Ollama (usa LiteLLM remoto)
# - Não instala deps de GPU (torch, ultralytics, faster-whisper)
# - Instala deps mínimas (httpx, ffmpeg, opencv-headless para YOLO CPU)
# - Configura WireGuard
# - Configura .env com endpoints do Flávio

set -euo pipefail

REPO_DIR="/opt/gpcg"
VENV="$REPO_DIR/.venv"

# 1. Clonar repo
git clone https://github.com/brunointegrations/gameplay-content-generator.git "$REPO_DIR"
cd "$REPO_DIR"

# 2. Criar venv
python3 -m venv "$VENV"
"$VENV/bin/pip" install -e ".[dev]"

# 3. Instalar deps CPU-only
# YOLO CPU (sem CUDA)
"$VENV/bin/pip" install ultralytics opencv-python-headless psutil

# 3.1 Clonar e instalar video-generate (deps core only, sem GPU)
git clone https://github.com/brunointegrations/video-generate.git /opt/video-generate
cd /opt/video-generate
"$VENV/bin/pip" install -e .  # só requirements-core.txt, sem torch/CUDA/ComfyUI

# 3.2 Copiar biblioteca de músicas (BGM) para a VM
# BGMSelector lê de internal_media_library/audios/bgm/ (318MB, 6 moods)
# Estrutura: calm/ dramatic/ energetic/ inspirational/ mysterious/ neutral/ + _index.json
scp -r /opt/video-generate/internal_media_library/audios/bgm/ /opt/video-generate/internal_media_library/audios/bgm/
# Ou baixar de um storage compartilhado se disponível

# 4. Configurar .env
cat > "$REPO_DIR/.env" << 'EOF'
# Worker identity
GPCG_VPS_URL=http://10.0.0.1/gpcg
GPCG_WORKER_ID=vm-flavio-bmi
GPCG_WORKER_API_KEY=<SECRET>
GPCG_WORKER_STORAGE=/var/lib/gpcg
GPCG_GAMEPLAY_DOWNLOAD_DIR=/data/gpcg/gameplays
GPCG_WORKER_CAPABILITIES=mapping,generation

# LLM/VLM via LiteLLM remoto
GPCG_LLM_PROVIDER=litellm
GPCG_LITELLM_BASE_URL=<URL_DO_FLAVIO>
GPCG_LITELLM_API_KEY=<KEY_DO_FLAVIO>
GPCG_LLM_MODEL_LITELLM=ollama/llama3.1:8b
GPCG_VLM_MODEL_LITELLM=ollama/gemma3:12b

# ASR via LiteLLM remoto
GPCG_ASR_PROVIDER=litellm
GPCG_ASR_CHUNK_MINUTES=5

# TTS via Kokoro remoto (passado ao video-generate via env vars)
TTS_ENGINE=kokoro
KOKORO_VOICE=pm_alex

# Render em CPU (video-generate com deps core only)
RENDER_DEVICE=cpu

# YOLO em CPU
GPCG_YOLO_DEVICE=cpu

# Storage (hot vs cold)
GPCG_WORKER_STORAGE=/var/lib/gpcg
GPCG_GAMEPLAY_DOWNLOAD_DIR=/data/gpcg/gameplays
EOF

# 5. Configurar systemd
cat > /etc/systemd/system/gpcg-worker.service << 'EOF'
[Unit]
Description=GPCG Remote Worker (VM Flávio)
After=network-online.target wg-gpcg@wg-gpcg.service

[Service]
Type=simple
WorkingDirectory=/opt/gpcg
ExecStart=/opt/gpcg/.venv/bin/gpcg remote-worker
EnvironmentFile=/opt/gpcg/.env
Restart=on-failure
RestartSec=10
User=bruno

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now gpcg-worker
```

#### 6.2 Estrutura de diretórios na VM

```
/                           ← SSD local (hot work)
├── opt/gpcg/               ← código + venv
├── tmp/gpcg/               ← frames temporários, render temp
└── var/lib/gpcg/           ← DB local temporário

/data                       ← rede (cold storage)
├── gpcg/
│   ├── gameplays/          ← gameplays baixadas da VPS
│   ├── mapped/             ← resultados de mapping
│   ├── renders/            ← renders temporários
│   └── outputs/            ← vídeos finalizados antes do upload
```

#### 6.3 Systemd service

Diferenças do service do PC Bruno:
- `User=bruno` (não roda como root)
- `After=wg-gpcg@wg-gpcg.service` (depende do WireGuard)
- Sem `OLLAMA_HOST` (não usa Ollama local)
- Sem paths de video-generate (não usa)

**Arquivos:**
- `scripts/setup-worker-vm.sh` (novo)
- `scripts/gpcg-worker-vm.service` (novo template)

**Estimativa:** 1-2 dias

---

### Fase 7: Testes de integração

#### 7.1 Teste de mapping na VM

1. Upload de uma gameplay curta (5-10 min) via frontend
2. Criar job de mapping
3. Verificar se a VM pega o job
4. Verificar se o VLM remoto responde (gemma3:12b via LiteLLM)
5. Verificar se o ASR remoto funciona (com chunking)
6. Verificar se o YOLO CPU funciona (lento mas funcional)
7. Verificar se os eventos chegam na VPS

#### 7.2 Teste de generation na VM

1. Selecionar uma gameplay já mapeada
2. Criar job de generation
3. Verificar se o LLM remoto gera o script
4. Verificar se o Kokoro TTS funciona
5. Verificar se o video-generate compõe o vídeo em CPU
6. Verificar se o upload funciona
7. Verificar o vídeo final (qualidade, áudio, legendas)

#### 7.3 Teste de resiliência

1. Derrubar a VM no meio de um job → job deve voltar para `queued`
2. Derrubar o WireGuard → worker deve parar de pegar jobs (sem VPS)
3. Subir de novo → worker deve registrar e voltar a pegar jobs
4. Rodar ambos workers simultaneamente → não deve haver conflito

#### 7.4 Teste de operação paralela

1. PC Bruno e VM Flávio ambos online
2. Criar 2 jobs (1 mapping, 1 generation)
3. Verificar se cada worker pega um job diferente
4. Verificar se ambos completam sem interferência

**Estimativa:** 2-3 dias

---

## 4. Resumo de Arquivos a Modificar/Criar

### Modificar

**No GPCG:**

| Arquivo | Mudança |
|---------|---------|
| `src/gpcg/config.py` | Adicionar configs de provider (LLM, ASR, TTS engine, render device, storage, retention) |
| `src/gpcg/domain/models.py` | Adicionar modelo GameplayDownload (tracking de downloads por worker) |
| `src/gpcg/infrastructure/llm.py` | Refatorar LLMClient para suportar Ollama e LiteLLM (protocolo OpenAI via httpx) |
| `src/gpcg/infrastructure/asr_transcriber.py` | Adicionar RemoteASRTranscriber |
| `src/gpcg/infrastructure/player_detector.py` | Ler device do config (gpcg_yolo_device) em vez de hardcoded "cuda" |
| `src/gpcg/application/gameplay_analyzer.py` | Factory para ASR provider (local vs remoto) |
| `src/gpcg/infrastructure/video_generate_adapter.py` | Passar env vars ao subprocess (TTS_ENGINE, LITELLM_BASE_URL, KOKORO_VOICE, RENDER_DEVICE) |
| `src/gpcg/api/worker_routes.py` | Job requeue + confirm_download com tracking + cleanup condicional + endpoint list-for-sync |
| `src/gpcg/worker/remote_worker.py` | Download on-demand de gameplay em generation + sync inicial no startup + reportar TTS info no metadata_json |
| `src/gpcg/worker/local_db_sync.py` | Flexibilizar paths hardcoded + env var GPCG_GAMEPLAY_SEARCH_DIRS |

**No video-generate:**

| Arquivo | Mudança |
|---------|---------|
| `requirements.txt` | Split em requirements-core.txt, requirements-gpu.txt, requirements-comfyui.txt |
| `pyproject.toml` | Adicionar [project.optional-dependencies] com gpu e comfyui extras |
| `src/generators/tts_factory.py` | Adicionar KokoroTTSEngine no factory |
| `generate.py` | Lazy import whisper (try/except) + config RENDER_DEVICE + fallback ASR remoto em generate_auto_srt |

### Criar

**No GPCG:**

| Arquivo | Propósito |
|---------|-----------|
| `scripts/setup-worker-vm.sh` | Setup do worker na VM Flávio |
| `scripts/gpcg-worker-vm.service` | Systemd service para VM |
| `scripts/migrate-gameplays-to-vps.sh` | Migração de gameplays locais para VPS (temporário) |

**No video-generate:**

| Arquivo | Propósito |
|---------|-----------|
| `requirements-core.txt` | Deps CPU-only (sem torch, sem CUDA, sem whisper) |
| `requirements-gpu.txt` | Deps GPU (torch+CUDA, TTS, openai-whisper, whisperx) |
| `requirements-comfyui.txt` | Deps ComfyUI/MusicGen (GPCG não usa) |
| `src/generators/tts_kokoro.py` | KokoroTTSEngine — TTS via LiteLLM /audio/speech (HTTP direto, sem torch) |
| `src/generators/asr_remote.py` | ASR remoto via LiteLLM /audio/transcriptions (para legendas na VM) |

---

## 5. Config .env Final da VM

```ini
# === Worker Identity ===
GPCG_VPS_URL=http://10.0.0.1/gpcg
GPCG_WORKER_ID=vm-flavio-bmi
GPCG_WORKER_API_KEY=<SECRET_SHARED_COM_VPS>
GPCG_WORKER_CAPABILITIES=mapping,generation
GPCG_WORKER_HEARTBEAT_INTERVAL=10
GPCG_WORKER_POLL_INTERVAL=5

# === Storage (hot vs cold) ===
# Hot: frames, renders temporários, DB local — SSD local (rápido)
GPCG_WORKER_STORAGE=/var/lib/gpcg
# Cold: gameplays baixadas, vídeos finalizados — NFS (lento, mas 100GB)
GPCG_GAMEPLAY_DOWNLOAD_DIR=/data/gpcg/gameplays
GPCG_GAMEPLAY_SEARCH_DIRS=/data/gpcg/gameplays

# === SSH/SCP (download de gameplays via WireGuard) ===
GPCG_SSH_HOST=10.0.0.1
GPCG_SSH_USER=root
GPCG_DOCKER_VOLUME=/var/lib/docker/volumes/gpcg_gpcg-data/_data

# === LLM/VLM (LiteLLM remoto do Flávio) ===
GPCG_LLM_PROVIDER=litellm
GPCG_LITELLM_BASE_URL=http://litellm.ganja.sv.house.dev.br:4000/v1
GPCG_LITELLM_API_KEY=<OPENAI_API_KEY_DO_LITELLM_ENV>
GPCG_LLM_MODEL_LITELLM=ollama/hermes3:latest
GPCG_VLM_MODEL_LITELLM=ollama/gemma3:12b

# === ASR (LiteLLM remoto com GPU) ===
GPCG_ASR_PROVIDER=litellm
GPCG_ASR_CHUNK_MINUTES=5
GPCG_ASR_COMPRESSED_FORMAT=mp3
GPCG_ASR_COMPRESSED_BITRATE=64k

# === TTS (Kokoro via LiteLLM — passado ao video-generate via env vars) ===
TTS_ENGINE=kokoro
KOKORO_VOICE=pm_alex
GPCG_TTS_LANGUAGE=pt

# === Render (video-generate em CPU) ===
RENDER_DEVICE=cpu

# === YOLO (CPU) ===
GPCG_YOLO_DEVICE=cpu
```

---

## 6. Config .env do PC Bruno (mudanças mínimas)

O PC Bruno continua funcionando como hoje. Única mudança: adicionar as novas configs com defaults que mantêm o comportamento atual:

```ini
# === Novas configs (defaults mantêm comportamento atual) ===
GPCG_LLM_PROVIDER=ollama
GPCG_ASR_PROVIDER=faster_whisper
TTS_ENGINE=xtts
RENDER_DEVICE=auto
GPCG_YOLO_DEVICE=cuda
```

Nenhuma mudança no comportamento do PC Bruno. As novas configs só ativam quando o provider é trocado.

---

## 7. Riscos e Mitigações

### 7.1 Render em CPU — benchmark real (testado em 2026-08-15)

**Benchmark rodado na VM Flávio (4 vCPU, 8GB RAM, FFmpeg 7.1.5):**

| Cenário | Duração do vídeo | Tempo de render | Ratio |
|---------|------------------|-----------------|-------|
| 1080p preset medium (encode only) | 60s | 35s | 0.58x |
| 1080p preset medium + legendas + scaling | 60s | 37s | 0.62x |
| 1080p pipeline completo (concat + legendas + mix áudio + encode) | 60s | 37s | 0.62x |
| 1080p preset medium (encode only) | 5 min | 2m54s | 0.58x |
| **720p preset fast (mais próximo do GPCG)** | **60s** | **26s** | **0.43x** |

**Conclusão:** Render CPU na VM é **mais rápido que o tempo real do vídeo**. Um vídeo de 60s renderiza em ~26s. Um vídeo de 5 min renderiza em ~3 min. As estimativas originais (5-8 min para 60s) estavam **erradas por um fator de 10x** — eram baseadas em presets lentos e 1080p.

**Recomendação:** Usar 720p + preset fast na VM (padrão do GPCG). Render não é gargalo — é mais rápido que o TTS (Kokoro tem ~13s de latência por chunk).

**Mitigação:** Manter vídeos curtos (60-90s) inicialmente. Se vídeos longos chegarem, 5 min renderiza em ~3 min — perfeitamente viável.

### 7.2 Rate limit — todas as chamadas remotas

Toda chamada ao LiteLLM do Flávio (LLM, VLM, Kokoro TTS, ASR) está sujeita a rate limit.

**Rate limits conhecidos:**
- VLM (gemma3:12b): 15 req/min — mapping faz 50-200 chamadas → 3.5-13 min de espera
- Kokoro TTS: 15 req/min — vídeo de 60s tem ~10-15 chunks → ~4s de espera no pior caso
- ASR (whisper): assumir 15 req/min — 1 chamada por vídeo → irrelevante
- LLM texto: assumir 15 req/min — ~5 chamadas por job → irrelevante

**Mitigação:** Retry com backoff exponencial em todas as chamadas remotas (ver Fase 1.3). Worker espera e tenta de novo — não trava, não perde o job.

**Por que não paralelizar jobs enquanto espera:** Render CPU (~26s/vídeo de 60s — benchmark real) não é gargalo. Economia de segundos de rate limit não justifica complexidade de paralelização. Decisão: retry com backoff agora, paralelização de stages é melhoria futura.

### 7.3 /data é storage em rede

**Mitigação:** Hot work (frames, temp FFmpeg, DB local) em `/` (SSD). Cold storage (gameplays baixadas, vídeos finalizados) em `/data`.

### 7.4 ASR remoto — chunking obrigatório

Se mandar arquivo de 30 min inteiro, pode dar timeout.

**Mitigação:** Chunking de 5 min com FFmpeg. Enviar blocos sequenciais. Juntar transcrições com offset de timestamp.

### 7.5 Job preso se VM cair

**Mitigação:** Fase 0 (job requeue) é pré-requisito. Jobs `running` sem heartbeat há > 5 min voltam para `queued`.

### 7.6 Vozes diferentes entre workers

Vídeos do PC Bruno saem com voz do Bruno (XTTS cloning).
Vídeos da VM saem com voz fixa do Kokoro (`pm_alex`).

**Mitigação:** Aceitável para beta. No futuro, hospedar XTTS remoto ou padronizar Kokoro em ambos.

### 7.7 WireGuard endpoint

A VM precisa alcançar o IP público da VPS Contabo para o túnel WireGuard.

**Pergunta em aberto:** A VM tem acesso à internet pública? Se sim, WireGuard direto funciona. Se não, precisamos rotear via NetBird → PC Bruno → WireGuard → VPS (mais complexo).

### 7.8 Legendas sem Whisper local — qualidade

A VM não tem openai-whisper instalado (deps core only). Legendas precisam de ASR remoto:
1. Usar o ASR remoto do LiteLLM para transcrever a narração TTS e gerar SRT
2. Fallback: calcular timing proporcional baseado na duração do áudio (menos preciso)

**Mitigação:** `generate_auto_srt()` em `generate.py` tem fallback para ASR remoto quando whisper não está disponível (lazy import + try/except). Mesma lógica de legendas do PC Bruno, só muda a fonte do ASR.

---

## 8. Ordem de Execução

```
Fase 0:   Job requeue automático              [PRÉ-REQUISITO]
    │
    ▼
Fase 0.5: Sync de gameplays entre workers     [PRÉ-REQUISITO CRÍTICO]
    │
    ▼
Fase 1:   Adapter LLM/VLM (LiteLLM)           [pode testar no PC Bruno]
    │
    ▼
Fase 2:   Adapter ASR (LiteLLM remoto)        [pode testar no PC Bruno]
    │
    ▼
Fase 3:   video-generate deps opcionais + KokoroTTSEngine  [pode testar no PC Bruno]
    │
    ▼
Fase 4:   YOLO CPU + Storage paths            [trivial]
    │
    ▼
Fase 5:   WireGuard VM ↔ VPS                  [precisa da VM + coordenação com Flávio]
    │
    ▼
Fase 6:   Migrar gameplays + instalar worker  [precisa da VM]
    │
    ▼
Fase 7:   Testes de integração                [precisa da VM]
```

**Fases 0-4 podem ser desenvolvidas e testadas no PC do Bruno** (não precisam da VM).
**Fases 5-7 precisam da VM.**

**WireGuard é necessário** porque:
- SSH da VPS só é acessível via WireGuard (não exposto na internet pública)
- SCP é o caminho primário para download de gameplays (400MB-4.4GB)
- HTTP streaming fallback tem timeout de 300s — insuficiente para arquivos grandes
- Latência 3x menor via WireGuard (238ms vs 745ms)

**Sync de gameplays é necessário** porque:
- Hoje a VPS deleta gameplay após mapping → arquivo fica só no worker que mapeou
- Worker de generation assume que arquivo existe localmente → quebra se outro worker fez o mapping
- Solução temporária: VPS serve como relay — só apaga depois que TODOS os workers confirmam download
- Cada worker baixa todas as gameplays no startup e mantém cópia local
- Mapeamento já não duplica (jobs/claim é atômico + create-mapping-job retorna 409)
- FIXME/TODO: migrar para storage compartilhado (S3/B2) — documentado no código

**Estimativa total:** 15-21 dias (Fase 3 reduzida de 8-10 para 3-4 dias com deps opcionais em vez de SimpleRenderer)

---

## 9. Perguntas Respondidas (testadas na VM em 2026-08-15)

### 9.1 WireGuard → NECESSÁRIO

A VM tem internet pública (IP 179.181.85.230) e alcança a VPS Contabo via HTTPS:
- `curl https://brunointegrations.com/gpcg/api/health` → HTTP 200 em 745ms

**Mas WireGuard é necessário porque:**

1. **SCP é o caminho primário para download de gameplays** (400MB-4.4GB). O SSH da VPS só é acessível via WireGuard (10.0.0.1), não exposto na internet pública. Sem WireGuard, SCP falha e cai para HTTP streaming com timeout de 300s — insuficiente para arquivos grandes.

2. **SCP também baixa voice files** custom do usuário.

3. **Latência 3x menor** via WireGuard (238ms vs 745ms) — relevante para transferências grandes.

4. **SSH access à VPS** para debug/administração da VM.

**Endpoint WireGuard a declarar para o Flávio:**
- `161.97.133.242:51820` (UDP outbound)
- A VM só precisa de outbound UDP, não inbound

### 9.2 LiteLLM endpoint

- **URL base:** `http://litellm.ganja.sv.house.dev.br:4000/v1`
- **Acessível da VM:** Sim, diretamente (DNS interno da casa resolve)
- **API key:** Sim, 25 chars, já em `~/litellm.env` como `OPENAI_API_KEY`
- **Env vars:** `OPENAI_BASE_URL` e `OPENAI_API_KEY` (compatível com protocolo OpenAI — usaremos httpx, não SDK OpenAI)

### 9.3 Kokoro TTS

- **Endpoint:** `POST {OPENAI_BASE_URL}/audio/speech` (mesmo endpoint do LiteLLM)
- **Payload:** `{"model":"kokoro","input":"texto","voice":"pm_alex","response_format":"mp3"}`
- **Testado:** HTTP 200, 23KB de MP3 em ~13s para frase curta
- **Formatos:** `mp3` e `opus` (24kHz mono)
- **Rate limit:** 15 req/min
- **Vozes:** `pm_alex` (masculina padrão), `pm_santa` (masculina grave), `pf_dora` (feminina)

### 9.4 ASR remoto

- **Endpoint:** `POST {OPENAI_BASE_URL}/audio/transcriptions`
- **Payload:** multipart com `file=@audio`, `model=whisper`, `language=pt`
- **Testado:** HTTP 200, retornou JSON com `text`, `segments`, `duration`, `language`
- **Segmentos incluem:** `start`, `end`, `text`, `tokens`, `avg_logprob`, `no_speech_prob`
- **Sem chunking necessário para áudio curto** — mas para gameplays de 30+ min, chunking ainda recomendado

### 9.5 VLM com imagens

- **Endpoint:** `POST {OPENAI_BASE_URL}/chat/completions` com `model=ollama/gemma3:12b`
- **Formato imagem:** `data:image/png;base64,<b64>` em `image_url.url`
- **Testado:** identificou corretamente cor vermelha em imagem de teste
- **Latência:** ~11s para uma chamada VLM simples
- **Rate limit:** 6 req/min (gemma3:12b) — confirmado no guia

### 9.6 Modelos disponíveis no LiteLLM

| Modelo | Rate limit | Uso recomendado no GPCG |
|--------|-----------|------------------------|
| `ollama/llama3.2:3b` | 15/min | Metadata, tasks simples |
| `ollama/hermes3:latest` | 10/min | Content planning, script |
| `ollama/qwen2.5-coder:7b` | 10/min | (não usado — não é código) |
| `ollama/qwen2.5-coder:14b` | 5/min | (não usado) |
| `ollama/deepseek-r1:8b` | 10/min | Script critic, humanization |
| `ollama/deepseek-r1:14b` | 5/min | Editorial, creative engine |
| `ollama/gemma3:12b` | 6/min | VLM (análise de frames) |
| `kokoro` | 15/min | TTS |
| `whisper` | — | ASR |

**Mapeamento sugerido para o pipeline de generation:**
- Content planning → `ollama/hermes3:latest` (uso geral, 10/min)
- Editorial planner → `ollama/deepseek-r1:14b` (raciocínio, 5/min)
- Creative engine → `ollama/deepseek-r1:14b` (raciocínio, 5/min)
- Script → `ollama/hermes3:latest` (10/min)
- Humanization → `ollama/deepseek-r1:8b` (10/min)
- Script critic → `ollama/deepseek-r1:8b` (10/min)
- Metadata → `ollama/llama3.2:3b` (15/min, simples)
- VLM (mapping) → `ollama/gemma3:12b` (6/min)

### 9.7 Recursos da VM (confirmados)

| Recurso | Valor | Notas |
|---------|-------|-------|
| vCPUs | 4 | `nproc` = 4 |
| RAM | 8 GB + 4 GB swap | `free -h` |
| Disco `/` | 59 GB (54 GB livres) | SSD local, `/dev/mapper/kush--wd-vm--147--disk--0` |
| Disco `/data` | 100 GB (100 GB livres) | NFS, `omv.house.dev.br:/export/bmi-data` |
| Python | 3.13.5 | |
| FFmpeg | 7.1.5 | Já instalado |
| Docker | 29.7.2 | |
| sudo | Sim, sem senha | `sudo -n whoami` → `root` |
| NetBird | Conectado | `bmi.netbird.cloud` (100.68.105.88) |
| Internet pública | Sim | IP 179.181.85.230, curl google.com OK |
| WireGuard | Não instalado | `wg` não encontrado — mas não precisamos |

### 9.8 /dev/sdb

`lsblk` mostra `sdb 447.1G` mas **NÃO devemos tocar** — é LVM do host. O guia confirma que a VM é isolada e `/data` já está montado como NFS.

### 9.9 Perguntas que restam (não técnicas)

1. **Voz do Kokoro:** `pm_alex` (masculina padrão). Decidido — usar por enquanto.

2. **Música de fundo:** BGMSelector já funciona sem ML (só JSON + arquivos MP3).
   - **Decidido: Copiar biblioteca de músicas do video-generate para a VM** (Opção A)
   - BGMSelector lê de um diretório local de MP3s + JSON de metadata (usage_count, last_used, mood)
   - Sem GPU, sem ML — só seleção de arquivo com anti-repetição
   - Cópia feita via SCP durante setup da VM (Fase 6)

3. **Legendas:** ASR remoto do LiteLLM (`POST /audio/transcriptions`, model=whisper).
   - Cada vídeo precisa de 1 chamada ASR (transcrever narração TTS de 60-90s)
   - Rate limit do ASR não é gargalo — render CPU (~26s/vídeo de 60s) é rápido
   - Decidido: usar ASR remoto, sem whisper local na VM

---

## 10. Estado Final Desejado

```
                    ┌──────────────────────────────┐
                    │        VPS Contabo            │
                    │        Control Plane          │
                    │                              │
                    │  • API FastAPI (porta 8787)  │
                    │  • SQLite (gpcg.db)          │
                    │  • Job Queue com requeue     │
                    │  • Worker Registry (2 workers)│
                    │  • Nginx (/gpcg/)            │
                    │  • WireGuard (10.0.0.1)      │
                    └───┬──────────────────────┬───┘
                        │                      │
              WireGuard │                      │ WireGuard
              10.0.0.2  │                      │ 10.0.0.3
                        │                      │
              ┌─────────▼──────┐    ┌──────────▼───────────┐
              │  PC Bruno      │    │  VM Flávio (bmi)     │
              │  Worker 1      │    │  Worker 2            │
              │                │    │                      │
              │  GPU RTX 3060  │    │  4 vCPU, 8GB RAM     │
              │  Ollama local  │    │  LiteLLM remoto      │
              │  XTTS (custom) │    │  Kokoro (pm_alex)    │
              │  faster-whisp  │    │  ASR GPU remoto      │
              │    CUDA        │    │  YOLO CPU            │
              │  YOLO CUDA     │    │  FFmpeg CPU          │
              │  FFmpeg CPU    │    │  video-generate      │
              │  video-generate│    │  (deps core only)    │
              │  (deps gpu)    │    │                      │
              │                │    │  Voz: fixa Kokoro    │
              │  Voz: Bruno    │    │  Render: ~26s/60s    │
              │  Render: rápido│    │                      │
              └────────────────┘    └──────────────────────┘
```

Ambos workers:
- Registram na mesma VPS
- Pegam jobs da mesma fila
- Processam de forma independente
- Não dependem um do outro
- Se um cai, o outro continua
- Jobs presos voltam para a fila automaticamente

---

## 11. Deep-Review — Correções aplicadas (2026-08-15)

Esta seção documenta as inconsistências encontradas ao comparar o plano com o código real, e as correções aplicadas.

### 11.1 SimpleRenderer eliminado

**Problema:** O plano original propunha um SimpleRenderer "apenas FFmpeg" que na prática precisaria reimplementar BGMSelector, geração de legendas (Whisper + alinhamento), e composição FFmpeg com transições — basicamente reescrever metade do video-generate.

**Correção:** Substituído por dependências opcionais no video-generate. A VM instala só `requirements-core.txt` (sem torch/CUDA/ComfyUI). TTS via KokoroTTSEngine no factory existente. Render, BGM e legendas usam o mesmo código do PC Bruno.

### 11.2 Storage hot vs cold

**Problema:** `GPCG_WORKER_STORAGE=/data/gpcg` apontava para NFS (rede, lento). Hot files (frames, renders temporários, DB local) em NFS engasgariam o pipeline.

**Correção:** Separado em:
- `GPCG_WORKER_STORAGE=/var/lib/gpcg` (SSD local — hot files)
- `GPCG_GAMEPLAY_DOWNLOAD_DIR=/data/gpcg/gameplays` (NFS — cold files)

### 11.3 Job já tem `attempts`, não `retry_count`

**Problema:** Plano propunha criar campo `retry_count` em Job.

**Correção:** Job já tem `attempts` e `max_attempts` (models.py linhas 886-887). O `/jobs/claim` já incrementa `attempts` no UPDATE atômico. Requeue usa `attempts` existente.

### 11.4 Worker já tem `metadata_json`, não precisa `tts_info`

**Problema:** Plano propunha adicionar coluna `tts_info` (JSON) em Worker.

**Correção:** Worker já tem `metadata_json` (JSON column, models.py linha 861). TTS info armazenada dentro de `metadata_json` — sem migration de schema.

### 11.5 ASR device config já existe

**Problema:** Plano propunha `gpcg_asr_device`.

**Correção:** Já existe `gpcg_gameplay_asr_device` (config.py linha 158). Usar nome existente.

### 11.6 OpenAI SDK vs httpx

**Problema:** Plano propunha usar OpenAI SDK para LiteLLM, mas projeto não tem dependência `openai`.

**Correção:** Usar `httpx` (já é dependência, pyproject.toml linha 23). Implementar protocolo OpenAI-compatible manualmente (POST /v1/chat/completions, /audio/speech, /audio/transcriptions).

### 11.7 TTS e render não eram independentes

**Problema:** Plano dizia "TTS e render são independentes" mas o `TTSResult` do XTTS retorna `subtitle_mapping` usado pelo render para legendas.

**Correção:** Com a abordagem de deps opcionais, TTS e render continuam no mesmo pipeline (video-generate). KokoroTTSEngine retorna `subtitle_mapping` vazio — legendas usam ASR remoto como fallback.

### 11.8 Voice dataclass era breaking change

**Problema:** Plano propunha `Voice` dataclass substituindo `voice_path` (string), quebrando jobs existentes.

**Correção:** Abordagem de deps opcionais não precisa mudar `voice_path`. KokoroTTSEngine ignora `speaker_wav` (usa voz fixa). `voice_path` continua como string — zero migração.

### 11.9 WireGuard porta e outbound UDP

**Status: RESOLVIDO.** Outbound UDP da VM para `161.97.133.242:51820` confirmado por teste (2026-08-15). Pacote UDP da VM chegou na VPS via tcpdump. Porta 51820 é o padrão WireGuard. Não precisa pedir nada para o Flávio.

### 11.10 GameplaySource já tem `downloaded_by_worker`

**Status:** GameplaySource tem campo `downloaded_by_worker` (string, models.py linha 493). Não é tracking completo (só um worker). Avaliar se estende o modelo existente ou se cria GameplayDownload novo — decidir na implementação da Fase 0.5.
