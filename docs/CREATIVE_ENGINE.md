# Creative Engine (Qwen3-14B)

> Camada criativa opcional do GPCG, baseada em Qwen3-14B local via Ollama.

---

## O que é

O **CreativeEngine** é uma camada especializada do pipeline que gera
**material criativo** (hooks, ângulos, punchlines, observações) antes da
geração do roteiro final. Ele roda entre o `content_planning` e o `script`.

```
content_planning → [creative_engine] → script → tts → ...
```

Quando desativado (default), o pipeline roda exatamente como antes — sem
nenhuma chamada extra ao LLM. Quando ativado, uma única chamada ao
Qwen3-14B produz todos os ingredientes criativos em uma resposta
estruturada (JSON), que são então oferecidos ao `ScriptService` como
inspiração de tom/estilo.

## Por que Qwen3-14B

- Modelo de 14B parâmetros, otimizado para criatividade e raciocínio
- Quantização Q4_K_M GGUF (~9 GB) — cabe em 12 GB de VRAM (RTX 3060)
- Roda via Ollama, mesmo runtime já usado pelo projeto (llama3.1:8b, gemma3:12b)
- Boa performance em pt-BR para tom informal e humor

## Instalação

```bash
# Baixar o modelo (9.3 GB, ~1-2 min em banda larga)
ollama pull qwen3:14b

# Verificar
ollama list
# deve mostrar: qwen3:14b
```

## Configuração

No `.env`:

```env
# Master switch (default: false — pipeline legacy)
GPCG_CREATIVE_ENGINE_ENABLED=true

# Modelo (default: qwen3:14b)
GPCG_CREATIVE_ENGINE_MODEL=qwen3:14b

# Sampling (default: 0.85 — alto para criatividade)
GPCG_CREATIVE_ENGINE_TEMPERATURE=0.85
GPCG_CREATIVE_ENGINE_MAX_TOKENS=2048

# Fallback (default: true — continua sem material criativo se o modelo falhar)
GPCG_CREATIVE_ENGINE_FALLBACK=true

# Estilo default (default: humor)
GPCG_CREATIVE_ENGINE_STYLE=humor
```

## Como ativar/desativar

- **Desativado** (default): `GPCG_CREATIVE_ENGINE_ENABLED=false` — pipeline
  original, sem chamadas ao Qwen3-14B.
- **Ativado**: `GPCG_CREATIVE_ENGINE_ENABLED=true` — estágio extra
  `creative_engine` roda entre content_planning e script.

Para comparar os dois modos, basta gerar dois jobs: um com a flag ligada e
outro desligada. O material criativo fica persistido em
`job.artifacts["creative_material"]` para inspeção.

## Como executar (smoke test isolado)

```bash
# Testar o CreativeEngine sem gerar vídeo
gpcg creative-test -t "Bully" -f "Você pode dar banhos de privada nos alunos" -s humor

# Com contexto extra
gpcg creative-test -t "GTA San Andreas" -f "Bigfoot existe no jogo" -s absurd -c "Game: GTA San Andreas"
```

Saída esperada: 5 hooks + 5 ângulos + 5 punchlines + 5 observações, em
pt-BR, com o tom do estilo escolhido.

## Estilos / Presets

8 presets disponíveis. Para trocar, basta mudar `GPCG_CREATIVE_ENGINE_STYLE`
ou passar `creative_style` por job (API/CLI).

| Preset | Descrição |
|--------|-----------|
| `humor` | Humor espontâneo, observações do cotidiano, analogias inesperadas |
| `absurd` | Levar ao extremo lógico, "isso não deveria existir", exagero consciente |
| `sarcastic` | Sarcasmo seco, observações irônicas, tom de "óbvio que isso existe" |
| `storytelling` | Narrativa com build-up e revelação, ritmo de história |
| `curiosity` | Tom de "olha isso que louco", foco em curiosidade genuína |
| `nostalgia` | Tom de "lembra disso?", apelo à memória afetiva |
| `dark_humor` | Humor que beira o inadequado sem cruzar a linha |
| `high_energy` | Ritmo acelerado, frases curtas, impacto, estilo criador explosivo |

### Criar novos estilos

Adicione uma entrada em `CREATIVE_PRESETS` (em
`src/gpcg/application/creative_engine.py`):

```python
"my_style": CreativeStyle(
    name="my_style",
    label="Meu Estilo",
    energy=0.7,
    absurdity=0.3,
    sarcasm=0.5,
    informality=0.8,
    creativity=0.85,
    description="Descrição do estilo em linguagem natural para o LLM.",
),
```

Sem necessidade de mudar o pipeline — o estilo é resolvido por nome em
runtime.

## Como trocar o modelo futuramente

Basta mudar `GPCG_CREATIVE_ENGINE_MODEL` para outro tag do Ollama:

```env
# Exemplos
GPCG_CREATIVE_ENGINE_MODEL=qwen3:32b      # maior, mais VRAM
GPCG_CREATIVE_ENGINE_MODEL=llama3.1:70b   # outro modelo
GPCG_CREATIVE_ENGINE_MODEL=mistral-nemo   # alternativa
```

O `CreativeEngine` usa o `LLMClient` existente com `model=` override —
nenhuma mudança de código é necessária.

## Pipeline (visão técnica)

```
1. content_planning  → ContentPlan (topic, hook, tone, fact_id)
2. creative_engine   → CreativeMaterial (hooks[], angles[], punchlines[], observations[])
                       ↳ persistido em job.artifacts["creative_material"]
3. script            → Script (draft → optimize → originality check → rewrite)
                       ↳ draft prompt enriquecido com o material criativo
                       ↳ anti-plágio ainda roda (material é inspiração, não cópia)
4. tts               → narration.wav
5. ... (resto do pipeline)
```

### Performance

- **1 chamada LLM** por job (não 4-5 separadas) — todos os campos em uma
  única resposta JSON estruturada
- Latência típica: ~40s no RTX 3060 com Qwen3-14B Q4
- VRAM: ~9 GB para o Qwen3-14B (cabe junto com llama3.1:8b se necessário,
  mas Ollama faz swap automático)

### Fallback

Se o Qwen3-14B falhar (modelo não instalado, OOM, timeout):

- `GPCG_CREATIVE_ENGINE_FALLBACK=true` (default): loga erro, retorna
  `CreativeMaterial.empty()`, pipeline continua com script legacy
- `GPCG_CREATIVE_ENGINE_FALLBACK=false`: job falha com `CreativeEngineError`

### Observabilidade

Cada execução loga:

```
creative_engine: model=qwen3:14b style=humor latency_ms=41548
  hooks=5 angles=5 punchlines=5 observations=5
```

E em caso de falha:

```
creative_engine FAILED: model=qwen3:14b style=humor latency_ms=5000
  error=connection refused
creative_engine: fallback enabled — continuing without creative material
```

O `CreativeMaterial` completo fica persistido em `job.artifacts` para
auditoria posterior.

## API

Os endpoints `/jobs/generate` e `/jobs/curiosity` aceitam um parâmetro
opcional `creative_style` (Form field) que sobrescreve o estilo default
para aquele job:

```bash
curl -X POST http://localhost:8787/api/jobs/generate \
  -F "game_id=1" \
  -F "creative_style=absurd"
```

Só tem efeito quando `GPCG_CREATIVE_ENGINE_ENABLED=true`.

## Testes

```bash
# Testes do CreativeEngine (29 testes, usa mocks — não precisa do Qwen real)
.venv/bin/pytest tests/test_creative_engine.py -v

# Suite completa (116 testes)
.venv/bin/pytest tests/ -q
```

Os testes unitários usam um `FakeLLM` que retorna respostas canned — não
dependem do Qwen3-14B real. O smoke test real (`gpcg creative-test`) é
separado e opcional.
