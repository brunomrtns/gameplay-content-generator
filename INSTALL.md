# GPCG — Guia de Instalação

O GPCG tem duas partes que rodam em máquinas diferentes:

| Parte | Onde roda | O que faz |
|-------|-----------|-----------|
| **Control Plane** | VPS | API, frontend, banco de dados, fila de jobs |
| **Compute Plane** | PC local (GPU) | Processa gameplays (VLM, ASR) e gera vídeos |

```
VPS (Control Plane)                    PC Local (Compute Plane)
━━━━━━━━━━━━━━━━━━━                    ━━━━━━━━━━━━━━━━━━━━━━━━
FastAPI + React                        gpcg remote-worker
Banco de dados                         GPU (VLM, ASR, FFmpeg)
Fila de jobs                           HD Toshiba (gravações)
           ↕ HTTP (X-Worker-Key) ↕
```

---

## Parte 1: VPS (Control Plane)

### Pré-requisitos
- VPS com Docker instalado
- `my-vps` CLI configurado (trivestia)
- BI Identity Service rodando (para login SSO)

### Passo a passo

```bash
# 1. Deploy do código para a VPS
./scripts/deploy.sh

# 2. Configurar worker API key (gera a key, salva na VPS e localmente)
./scripts/setup-vps.sh

# 3. Verificar que está tudo OK
./scripts/setup-vps.sh --check
```

A VPS vai servir:
- **Frontend:** `https://brunointegrations.com/gpcg/`
- **API:** `https://brunointegrations.com/gpcg/api/health`

---

## Parte 2: PC Local (Compute Plane / Worker)

### Pré-requisitos

| Requisito | Como verificar | Como instalar |
|-----------|---------------|---------------|
| Python 3.11+ | `python3 --version` | `apt install python3` |
| NVIDIA GPU | `nvidia-smi` | Driver NVIDIA |
| Ollama | `curl localhost:11434/api/tags` | [ollama.com](https://ollama.com) |
| Modelo gemma3:12b | `ollama list` | `ollama pull gemma3:12b` |
| Modelo gpt-oss | `ollama list` | `ollama pull gpt-oss:latest` |
| video-generate | `ls ../video-generate/.venv` | `git clone` + `./scripts/setup.sh` |
| ai-media-core | `ls ../ai-media-core/src` | `git clone` |
| HD montado | `ls /media/bruno/ToshibaHD` | Montar o HD |

### Passo a passo

```bash
# 1. Verificar pré-requisitos (sem instalar nada)
./scripts/setup-worker.sh --check

# 2. Instalação completa (venv + GPU deps + credenciais + systemd)
./scripts/setup-worker.sh

# 3. Iniciar o worker
systemctl --user enable --now gpcg-worker

# 4. Verificar logs
journalctl --user -u gpcg-worker -f
```

O worker vai:
1. Registrar na VPS (aparece no dashboard)
2. Enviar heartbeats a cada 10s
3. Pollar por jobs a cada 5s
4. Quando pegar um job de mapeamento: baixar gameplay → rodar VLM+ASR → enviar eventos
5. Quando pegar um job de geração: buscar dados → rodar pipeline → enviar vídeo

---

## Uso do dia a dia

### Enviar gameplay para processamento
1. Acesse `https://brunointegrations.com/gpcg/content`
2. Arraste o arquivo de gameplay para a zona de upload
3. Clique em "Solicitar mapeamento" — o worker vai processar

### Ver status do worker
- Dashboard mostra GPU/CPU/RAM e atividade atual em tempo real
- Página Jobs mostra a fila com progresso

### Gerar vídeo
1. Acesse `https://brunointegrations.com/gpcg/automation`
2. Configure e inicie a automação
3. O worker vai pegar jobs de geração automaticamente

### Comandos úteis

```bash
# Worker
systemctl --user status gpcg-worker     # status
systemctl --user restart gpcg-worker    # reiniciar
systemctl --user stop gpcg-worker       # parar
journalctl --user -u gpcg-worker -f     # logs ao vivo

# VPS
./scripts/deploy.sh                     # novo deploy
./scripts/setup-vps.sh --check          # verificar VPS

# Verificar workers registrados
curl -s https://brunointegrations.com/gpcg/api/workers | python3 -m json.tool
```

---

## Troubleshooting

### Worker não aparece no dashboard
```bash
# Verifique se está rodando
systemctl --user status gpcg-worker

# Verifique os logs
journalctl --user -u gpcg-worker -n 50

# Teste conexão manual
source ~/.config/gpcg-worker-key.env
curl -s -X POST "$GPCG_VPS_URL/api/workers/register" \
  -H "Content-Type: application/json" \
  -H "X-Worker-Key: $GPCG_WORKER_API_KEY" \
  -d "{\"worker_id\":\"manual-test\",\"hostname\":\"$(hostname)\",\"capabilities\":[\"mapping\"],\"worker_version\":\"0.1.0\"}"
```

### Worker registra mas não pega jobs
- Verifique se há jobs na fila (página Jobs no dashboard)
- Verifique se as capabilities do worker incluem "mapping" ou "generation"
- Jobs só são claimados se o worker tem a capability necessária

### Erro "Invalid worker key"
- A key no `~/.config/gpcg-worker-key.env` deve ser igual à do `.env` da VPS
- Rode `./scripts/setup-vps.sh` para regenerar

### GPU não detectada
```bash
nvidia-smi  # deve listar a GPU
# Se não funcionar, reinstale o driver NVIDIA
```

### Ollama não responde
```bash
ollama serve  # inicia o servidor
ollama list   # lista modelos instalados
ollama pull gemma3:12b  # instala o modelo VLM
```

### video-generate não encontrado
```bash
# Clone e instale
cd ~/Desenvolvimento/brunointegrations
git clone <repo-video-generate> video-generate
cd video-generate && ./scripts/setup.sh
```
