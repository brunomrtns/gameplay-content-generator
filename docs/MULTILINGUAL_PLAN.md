# GPCG — Plano Técnico de Multilinguagem

> **Status:** Rascunho revisado (v3) — segunda auditoria + revisão de refactory completadas
> **Escopo:** Sistema inteiro — web, mobile, backend, worker, video-generate, TTS, ASR, LLM
> **Objetivo inicial:** Deixar o sistema pronto para troca de idioma via configuração (JSON/i18n), mantendo PT-BR como default
>
> **Revisão v2:** 16 furos corrigidos (seção 0.1-0.16)
> **Revisão v3 auditoria:** 3 falsos positivos corrigidos + 12 novos pontos mapeados (seção 0.17-0.28)
> **Revisão v3 refactory:** 13 recomendações de qualidade de implementação (seção 12)

---

## 0. Revisão Arquitetural v2 — Correções após Auditoria de Código

Esta seção documenta os **16 furos, erros e omissões** encontrados quando o plano v1 foi
verificado contra o código real do GPCG e video-generate. Cada item tem o veredito,
a evidência (arquivo:linha), e a correção aplicada ao plano.

### 0.1 ERRO CRÍTICO: Fluxo de propagação VPS → worker estava ERRADO

**Problema:** O plano v1 dizia que o idioma chegava ao worker via `Job.artifacts["target_language"]`
e que `GenerationService` lia de `job.artifacts`. **Isso está parcialmente errado.**

**Realidade do código:**
- `GenerationService` carrega `ChannelProfile` do **DB local SQLite** (replicado por `local_db_sync.py`), não de `job.artifacts`:
  ```python
  # generation_service.py:405-418
  channel_profile = session.query(ChannelProfile).filter(
      ChannelProfile.user_id == cp_user_id
  ).first()
  channel_context = channel_profile.to_prompt_context()
  ```
- `Automation.config` só chega ao worker via `config_snapshot` — um subset definido em `_CONFIG_SNAPSHOT_FIELDS` (`automation_routes.py:74-84`). `language` **não está** nesse subset.
- `get_job_data` (`api/workers/generation.py:346-364`) serializa `ChannelProfile` mas **não inclui** `target_language` (porque a coluna não existe).
- `local_db_sync.py:590-604` replica `ChannelProfile` mas **não inclui** `target_language`.

**Correção aplicada:** Seção 3.4.3 reescrita com o fluxo correto:
```
ChannelProfile.target_language (VPS DB)
  → get_job_data serializa channel_profile.target_language
  → local_db_sync replica ChannelProfile com target_language para SQLite local
  → GenerationService lê channel_profile.target_language do DB local
  → passa language para ContentPlanningService, ScriptService, etc.
```
E `language` DEVE ser adicionado a `_CONFIG_SNAPSHOT_FIELDS` para snapshot determinístico em retries.

### 0.2 ERRO CRÍTICO: `process_video_request()` não tem parâmetro de language

**Problema:** O plano v1 dizia que o adapter passava `self.tts_language` para video-generate.
Mas isso só é verdade para `synthesize()` (low-level). O render path **ignora language**.

**Realidade do código:**
- `video_generate_adapter.py:364-391` — render script chama `process_video_request(req)` **sem language**.
- `generate.py:1647-1724` — `process_video_request(request_data)` não aceita `language`/`whisper_language`/`tts_language`.
- `generate_tts()` (`tts.py:572-663`) — **não tem parâmetro `language`**, não passa para `synthesize()`.
- `generate_media.py:1950-1955` — chama `generate_tts()` sem language.

**Correção aplicada:** Seção 3.9.2 expandida — precisa adicionar `language` a:
1. `process_video_request()` em `generate.py`
2. `generate_tts()` em `tts.py`
3. `generate_media.py` forward
4. `request_data` dict do adapter

### 0.3 ERRO: Whisper em video-generate é para NARRAÇÃO, não gameplay

**Problema:** O plano v1 conflacionou o Whisper do video-generate com o ASR de gameplay.

**Realidade do código:**
- `generate.py:626-642` — Whisper transcreve o **áudio final da narração TTS** para alinhar legendas:
  ```python
  initial_prompt = "Transcrição de narração em português brasileiro..."
  result = model.transcribe(audio_file, language="pt", ...)
  ```
- Não há transcrição de gameplay no video-generate. O ASR de gameplay fica no GPCG (`asr_transcriber.py`).

**Correção aplicada:** Seção 3.9.2 corrigida — Whisper em video-generate = subtitle alignment da narração.
Precisa do `language` do conteúdo, não do gameplay.

### 0.4 ERRO: subtitle_mapping minimal causa fallback Whisper forçado em PT

**Problema:** O plano v1 mencionava que o mapping era "minimal" mas não explicava a consequência.

**Realidade do código:**
- `video_generate_adapter.py:210` — cria `{"tts_text": text, "expansions": []}` — **sem `segments`**.
- `generate.py:962-1009` — `generate_auto_srt()` só usa `subtitle_mapping` se tiver `segments`:
  ```python
  mapping_segments = subtitle_mapping.get("segments") or []
  if prepared_segments:
      srt_content = segments_to_srt(prepared_segments, profile=profile)
  else:
      print("⚠️ subtitle_mapping sem segmentos válidos - ativando fallback Whisper")
  ```
- Sem `segments`, cai no Whisper (forçado em `pt`). Os timings TTS→legenda são perdidos.

**Correção aplicada:** Seção 3.10.4 adicionada — GPCG adapter precisa produzir `segments` no mapping,
ou `generate_auto_srt` precisa aceitar `original_text` + language sem cair no Whisper.

### 0.5 OMISSÃO: TextNormalizer em video-generate é hardcoded PT-BR

**Problema:** O plano v1 não mencionou o `TextNormalizer`.

**Realidade do código:**
- `video-generate/src/processors/text_normalizer.py` — normaliza números, moedas, datas, abreviações para PT-BR:
  ```python
  UNITS = {'km': 'quilômetros', 'kg': 'quilos', 'R$': ('real', 'reais'), ...}
  ABBREVIATIONS = {'Dr.': 'doutor', ...}
  # Converte "20/05/2020" → "vinte de maio de..."
  # Converte "R$ 50" → "cinquenta reais"
  # Converte "1,5" → "um vírgula cinco"
  ```
- Se chamado para EN/ES/FR, produz TTS input errado.

**Correção aplicada:** Seção 3.9.4 adicionada — TextNormalizer precisa de locale packs.

### 0.6 OMISSÃO: AcronymHandler em video-generate é PT-specific

**Problema:** O plano v1 não detalhou o AcronymHandler.

**Realidade do código:**
- `acronym_handler.py:19-20` — `PORTUGUESE_COMMON_WORDS` blacklist
- `acronym_handler.py:321-334` — prompt: "Você é um especialista em fonética e pronúncia de siglas em português brasileiro"
- `acronym_handler.py:300` — `has_vowels = any(char in "AEIOU" for char in acronym)` — só vogais latinas
- `acronym_handler.py:250` — `r"\b[A-Z]{2,5}\b"` — só detecta siglas latinas

**Correção aplicada:** Seção 3.9.5 adicionada.

### 0.7 OMISSÃO: Originality check é language-naive

**Problema:** O plano v1 não abordou como o originality check se comporta cross-lingual.

**Realidade do código:**
- `originality.py:67-75` — `_normalize()` lowercase + strip accents + remove punctuation
- `originality.py:133-155` — Jaccard overlap de 5-grams
- Threshold default 70

**Problema real:**
- Tradução palavra-a-palavra PT→EN **passa** no check (tokens não casam)
- Geração EN de fonte EN pode ter overlap alto e trigger rewrite desnecessário
- Não há `script_language` nem `source_language` no check

**Correção aplicada:** Seção 3.6.1 adicionada — originality precisa de language-awareness.

### 0.8 OMISSÃO: Embeddings não têm coluna de language

**Problema:** O plano v1 disse "nomic-embed-text é multilingual" mas não especificou estratégia de re-embedding.

**Realidade do código:**
- `embedding_service.py` — `KnowledgeItemEmbedding` e `GameplayEventEmbedding` armazenam BLOBs com `model` column mas **sem `language` column**
- Embeddings são gerados do texto como-está (PT ou EN dependendo da fonte)
- Se um KI é traduzido, o embedding fica **stale**

**Correção aplicada:** Seção 3.7.1 adicionada — precisa de `language` nas embedding tables + re-embedding trigger.

### 0.9 OMISSÃO: SSE/events já enviam strings PT para o frontend

**Problema:** O plano v1 recomendou "stable keys em events" mas não quantificou o problema atual.

**Realidade do código:**
- `worker/handlers/generation.py:88` — envia `"Gerando vídeo"` via `send_status`
- `worker/handlers/generation.py:106` — envia `"Enviando vídeo"`
- `automation_routes.py:180-197` — `STAGE_LABELS` dict em PT, retornado como `stage_label` no API
- `workers/_common.py:99` — `current_activity` é string PT legível

**Impacto:** Mudar UI language não ajuda — as strings vêm do backend no event stream.
Dashboard em EN vai mostrar labels PT.

**Correção aplicada:** Seção 3.3.2 adicionada — events devem usar stable keys, frontend localiza.

### 0.10 ERRO: CreativeStyle.description é conteúdo do prompt, não só label

**Problema:** O plano v1 tratou "Humor brasileiro" como label traduzível.

**Realidade do código:**
- `creative_engine.py:76-165` — `CREATIVE_PRESETS` tem `label` (UI) E `description` (prompt content):
  ```python
  "humor": CreativeStyle(
      name="humor",
      label="Humor brasileiro",
      description="Humor espontâneo, observações engraçadas do cotidiano..."
  )
  ```
- `_build_style_block` (213-233) injeta `description` + `level()` (PT: "muito alto", "alto", "médio") no prompt
- Traduzir o `label` sem traduzir `description` e `level()` deixa o prompt em PT

**Correção aplicada:** Seção 3.6.2 corrigida — `CreativeStyle` precisa de `name` (key estável),
`label` (UI), `description` (prompt, por idioma), e `level()` localizado.

### 0.11 OMISSÃO: Game names não têm localização

**Problema:** O plano v1 mencionou Wikipedia em target_language mas não resolveu nomes de jogos.

**Realidade do código:**
- `catalog/models.py:56` — `CatalogGame.name` do IGDB (canônico inglês), sem coluna de locale
- `games/models.py:141` — `Game.canonical_name` sem localização
- Prompts usam `game_name` sem especificar forma localizada

**Correção aplicada:** Seção 3.13.1 adicionada — precisa de `CatalogGameLocalizedName` ou `Game.localized_names`.

### 0.12 OMISSÃO: `text_align` já existe no schema mas é ignorado

**Problema:** O plano v1 não mencionou que `text_align` já está no `SubtitleProfile`.

**Realidade do código:**
- `video_profile.py:30` — `text_align: str = "C"`
- `subtitle_renderer.py:104-162` — `generate_drawtext_filter` hardcodeia `x_expr = "(w-text_w)/2"`, nunca lê `sp.text_align`

**Correção aplicada:** Seção 3.10.5 adicionada — implementar `text_align` antes de suportar RTL.

### 0.13 OMISSÃO: `resolve_video_profile` não aceita override de fonte

**Problema:** O plano v1 disse "passar fonte por idioma via profile" mas não verificou o override path.

**Realidade do código:**
- `profile_registry.py:55-129` — `resolve_video_profile` só aceita overrides de box/stroke/transition
- **Não aceita** `subtitle_font` ou `font_file`
- Override de fonte só é possível via `_gpcg_custom_profile` completo (adapter registra VideoProfile inteiro)

**Correção aplicada:** Seção 3.10.6 adicionada — adicionar `subtitle_font_file` aos overrides de `resolve_video_profile`.

### 0.14 OMISSÃO: Kids pipeline tem chars/second hardcoded separado

**Problema:** O plano v1 propôs um dict de chars/second mas não cobriu o Kids pipeline.

**Realidade do código:**
- `kids/pipeline.py:461` — `target_chars = int(plan.target_duration * 14)` — hardcoded 14 chars/sec
- É separado do `CHARS_PER_SECOND_PT_BR` do video-generate

**Correção aplicada:** Seção 3.9.3 atualizada — Kids pipeline também precisa do dict de chars/second.

### 0.15 OMISSÃO: Checkpoints não snapshot language

**Problema:** O plano v1 não abordou o que acontece quando um job é resumido após mudança de idioma.

**Realidade do código:**
- `generation_service.py:1705-1722` — `_has_checkpoint` checa se key existe em `job.artifacts`
- Checkpoints em: `content_plan_id`, `script_id`, `narration_wav`, `selected_clips`, `video_path`
- **Nenhum** checkpoint inclui `target_language`

**Risco:** Worker crasha → usuário muda idioma do canal → worker reinicia → job resume →
usa o **novo** idioma em vez do idioma original → script em idioma X, TTS em idioma Y.

**Correção aplicada:** Seção 3.4.3 + 3.16.1 atualizadas — `target_language` DEVE ser snapshot
em `job.artifacts` no momento de criação do job, e `_has_checkpoint` deve usar esse snapshot
ao resumir, não o `ChannelProfile` live.

### 0.16 ERRO MENOR: `_ensure_column` está em `infrastructure/database.py`, não `db.py`

**Problema:** O plano v1 referiu-se a `init_db()` em local errado.

**Realidade do código:**
- `infrastructure/database.py:107-122` — `_ensure_column(engine, table, column, ddl_type)`
- `infrastructure/database.py:124-128` — `init_db()` chama `_ensure_column`

**Caveata adicional:** `_ensure_column` adiciona a coluna no SQL mas **não** no modelo SQLAlchemy.
Precisa adicionar o `Mapped[...]` attribute na classe do modelo também, senão o ORM não vê a coluna.

**Correção aplicada:** Seção 3.4.2 corrigida.

### 0.17 FALSO POSITIVO v2: `generate_media.py` não é o entry point do GPCG

**Problema:** A seção 0.2 lista `generate_media.py:1950-1955` como parte do fluxo ativo do GPCG.
**Realidade:** `generate_media.py` **não é usado pelo GPCG**. O GPCG chama `generate.py::process_video_request`
diretamente via subprocess (`video_generate_adapter.py:371-376`). `generate_media.py` é um
entry point standalone/CLI do video-generate, não parte do pipeline GPCG.

**Correção:** A seção 3.9.2 foi atualizada para remover `generate_media.py` do fluxo crítico.
O alvo real é `process_video_request()` → `generate_auto_srt()` → `transcribe_and_align_audio()`.

### 0.18 FALSO POSITIVO v2: TextNormalizer não é sempre chamado

**Problema:** A seção 0.5 diz que TextNormalizer "se chamado para EN/ES/FR, produz TTS input errado".
**Realidade:** TextNormalizer é gated pelo `TextDualProcessor` path:
- `text_dual_processor.py:43-45` — instancia TextNormalizer
- `text_dual_processor.py:77-85` — chama `normalizer.normalize()` antes do LLM
- `tts.py:603-630` — fallback dentro de `generate_tts()` quando dual-processor falha
- `synthesize()` (`tts.py:215-246`) **não** chama TextNormalizer
- `generate_tts()` só entra no dual-processor path quando `return_subtitle_mapping=True` e `llm_client` é fornecido

**Nuance:** O GPCG adapter chama `synthesize()` diretamente (não `generate_tts()`), e
`synthesize()` **não** usa TextNormalizer. Portanto, no fluxo TTS do GPCG, TextNormalizer
**não é chamado**. O problema existe apenas se o fluxo passar por `generate_tts()` com
`return_subtitle_mapping=True`.

**Correção:** Seção 3.9.4 atualizada — TextNormalizer é um problema do `TextDualProcessor`,
não do `synthesize()` path usado pelo GPCG.

### 0.19 FALSO POSITIVO v2: AcronymHandler pode ser desabilitado

**Problema:** A seção 0.6 diz que AcronymHandler é PT-specific sem mencionar que pode ser desabilitado.
**Realidade:**
- `synthesize()` aceita `preprocess_acronyms=True` (default)
- `generate_media.py:1955` seta `preprocess_acronyms=False` — mas não é usado pelo GPCG
- O GPCG adapter chama `synthesize()` sem setar `preprocess_acronyms`, então usa o default `True`

**Correção:** Seção 3.9.5 atualizada — AcronymHandler é ativado no fluxo GPCG (default True),
mas pode ser desabilitado via `preprocess_acronyms=False` em `synthesize()`.

### 0.20 NOVO: TextDualProcessor é um componente PT-BR monolítico não mapeado

**Problema:** O plano v2 não mencionou o `TextDualProcessor` (`video-generate/src/processors/text_dual_processor.py`).
**Realidade:**
- `text_dual_processor.py:32-48` — instancia `TextNormalizer` (PT-BR)
- `text_dual_processor.py:75-85` — normaliza texto antes do LLM
- `text_dual_processor.py:182-253` — prompt inteiro em PT: "Você é um especialista em otimização de texto para síntese de voz (TTS) em português brasileiro"
- Produz `tts_text` (expandido para fala) e `subtitle_text` (texto original para display)
- Retorna `segments` mapping TTS fragments → subtitle fragments + `expansions`

**Impacto:** Se o GPCG começar a usar `generate_tts()` com `return_subtitle_mapping=True`
(que é o caminho que produz `segments` para evitar o fallback Whisper), o TextDualProcessor
vai processar o texto com lógica PT-BR. Isso é um **bloqueador** para a solução 0.4
(adapter produzir segments).

**Correção:** Seção 3.9.7 adicionada.

### 0.21 NOVO: wrap_text descarta \n intencionais + font escaping incompleto

**Problema:** O plano v2 menciona que wrap_text quebra por whitespace, mas não que:
1. `subtitle_renderer.py:26-29` — `clean_text = " ".join(text.replace("\n", " ").split())` — **descarta quebras de linha intencionais**
2. `subtitle_renderer.py:122-123` — só escapa `'` e `:` — **não escapa** `\`, `%`, `,`, `=`, `[`, `]` que podem aparecer em texto não-Latim ou emoji

**Correção:** Seção 3.10.7 adicionada.

### 0.22 NOVO: SRT não tem language tags + VTT é dead code + SRT não vai para YouTube

**Problema:** O plano v2 não cobre a saída de legendas:
1. `generate.py:1011-1013` — SRT é escrito sem `LANGUAGE` tag
2. `subtitle_generator.py:107-159` — `generate_vtt()` existe mas **não é chamado** em lugar nenhum (dead code)
3. `google_integration_adapter.py:59-118` — upload para YouTube **não envia SRT/caption**, só title/description/tags
4. SRT é usado apenas para burn-in, não como caption file separado

**Correção:** Seção 3.10.8 adicionada.

### 0.23 NOVO: Frontend e mobile também têm STAGE_LABELS hardcoded em PT (duplicação)

**Problema:** O plano v2 diz para converter STAGE_LABELS do backend em stable keys, mas não menciona que **frontend e mobile também têm suas próprias cópias**:
- `frontend/src/pages/jobs.tsx:32-52` — `STAGE_LABELS` dict em PT
- `mobile/src/screens/IdeasScreen.tsx:69-84` — `STAGE_LABELS` dict em PT
- `mobile/src/screens/JobsScreen.tsx:38+` — `STAGE_LABELS` dict em PT

**Impacto:** Mesmo que o backend envie stable keys, o frontend tem fallback PT hardcoded.
Precisa remover as cópias duplicadas e usar apenas o i18n layer.

**Correção:** Seção 3.14.8 adicionada.

### 0.24 NOVO: 14+ strings PT adicionais em worker handlers (v2 só listou 2)

**Problema:** A seção 0.9 da v2 só listou 2 strings (`generation.py:88,106`). Há **14+ strings PT** adicionais:

| Arquivo | Linha | String PT |
|---------|-------|-----------|
| `worker/handlers/kids.py` | 32 | "Descobrindo ideias Kids" |
| `worker/handlers/kids.py` | 274 | "Avaliando ideia Kids #{idea_id}" |
| `worker/handlers/kids.py` | 445 | "Processando mídia Kids: {filename}" |
| `worker/handlers/kids.py` | 545 | "Mapeando mídia Kids: {filename}" |
| `worker/handlers/mapping.py` | 57 | "Identificando jogo — {filename}" |
| `worker/handlers/mapping.py` | 281 | "Mapeando {filename}" |
| `worker/handlers/knowledge.py` | 55 | "Baixando documento {filename}" |
| `worker/handlers/knowledge.py` | 67 | "Indexando {filename}" |
| `worker/handlers/content_collect.py` | 26 | "Coletando conteúdo (RSS)" |
| `worker/handlers/enrichment.py` | 30 | "Enriquecendo jogo #{game_id}" |
| `worker/handlers/cleanup.py` | 36 | "Limpando gameplay #{source_id}" |
| `worker/handlers/cleanup.py` | 117 | "Limpando storage {old_domain}..." |
| `worker/file_transfer.py` | 48 | "Baixando {filename}" |
| `worker/remote_worker.py` | 456 | "Sincronizando {filename}" |

**Correção:** Seção 3.3.2 expandida — todas as 16+ strings devem virar stable keys.

### 0.25 NOVO: PresentationConfig não tem language nem font_file

**Problema:** O plano v2 não cobre a Presentation Layer adequadamente.
**Realidade:**
- `presentation_config.py:26-76` — `PresentationConfig` tem `thumbnail_*`, `opening_*`, `auto_*` mas **nenhum campo de language ou font_file**
- `presentation_service.py` resolve texto (title/hook/custom) e passa para `OpeningRenderer`
- `opening_renderer.py:29` — `_FONT_FILE = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"` hardcoded
- `opening_renderer.py:205-215` — prompt PT para title shortening (também tem typo: "Mantenho" → "Mantenha")
- `opening_renderer.py:326-333` — `_wrap_text` quebra por spaces (mesmo problema do subtitle_renderer)

**Correção:** Seção 3.10.9 adicionada.

### 0.26 NOVO: QAService._ai_qa não tem language param

**Problema:** O plano v2 não menciona o QA service.
**Realidade:**
- `qa_service.py:164-197` — prompt em inglês, sem `target_language`
- Avalia script (que está em PT) com critérios em EN
- Issue descriptions são English ("duration too short", "no audio stream")
- Não checa subtitles/captions/text overlays

**Correção:** Seção 3.6.3 adicionada.

### 0.27 NOVO: RenderPlanBuilder não tem campo de language

**Problema:** O plano v2 diz para passar language via request_data, mas não menciona o RenderPlan.
**Realidade:**
- `render_plan_builder.py:55-61` — `RenderPlan` dataclass não tem campo `language`
- `render_plan_builder.py:258-273` — `request_data` dict não inclui `language`
- O `request_data` usa keys em português (`audio_principal`, `musica_fundo`, `delay_musica`) — são keys técnicas, não user-facing, mas precisam ser documentados como não-localizáveis

**Correção:** Seção 3.9.8 adicionada.

### 0.28 NOVO: Notification fallback e default automation name

**Problema:** Pontos menores não mapeados:
1. `api/workers/jobs.py:653-657` — fallback `"Video #{video.id}"` é English, precisa localização
2. `"Minha Automação"` default aparece em **4 lugares** (v2 só listou 2): `auth_routes.py:114,127`, `automation_routes.py:112`, `core/models.py:252`

**Correção:** Seção 3.3.1 atualizada com os 4 locais.

---

## 1. Visão Geral

O GPCG é atualmente **monolíngue em PT-BR por design**. Não existe nenhuma infraestrutura de i18n — nem no backend, nem no frontend, nem no mobile, nem no worker. Todas as strings de UI, prompts de LLM, configurações de TTS/ASR, mensagens de erro, labels de estágio, e heurísticas de processamento de texto estão hardcoded em português brasileiro.

Este documento mapeia **ponto a ponto** tudo que precisa mudar, classifica por dificuldade, e propõe uma arquitetura para suportar múltiplos idiomas.

### 1.1 Princípios

1. **PT-BR permanece como default** — nenhuma mudança deve quebrar o comportamento atual
2. **Troca de idioma = trocar um JSON/config** — não recompilar, não reescrever código
3. **Dois conceitos separados:**
   - **UI Language** — idioma da interface (botões, labels, mensagens, toasts)
   - **Content Language** — idioma do conteúdo gerado (roteiro, narração, legendas, metadata)
4. **Um idioma de conteúdo por canal** — o `ChannelProfile` define o idioma alvo; todos os vídeos do canal usam esse idioma
5. **UI language pode ser independente** — o usuário pode ter a interface em EN enquanto gera conteúdo em PT

### 1.2 Escopo de idiomas iniciais

| Código | Idioma | TTS XTTS | Whisper | CJK/RTL | Fonte |
|--------|--------|----------|---------|---------|-------|
| `pt-BR` | Português Brasileiro | ✅ `pt` | ✅ | Não | DejaVuSans |
| `en` | Inglês | ✅ `en` | ✅ | Não | DejaVuSans |
| `es` | Espanhol | ✅ `es` | ✅ | Não | DejaVuSans |
| `fr` | Francês | ✅ `fr` | ✅ | Não | DejaVuSans |

> Idiomas CJK (ja, zh-cn, ko) e RTL (ar, he) são **fase futura** — exigem mudanças no subtitle renderer, fontes, e quebra de linha. Documentados na seção 10.

---

## 2. Arquitetura Proposta

### 2.1 Camadas de i18n

```
┌─────────────────────────────────────────────────────────┐
│                    UI LAYER (i18n)                       │
│  Web: react-i18next + locales/{lang}/ui.json            │
│  Mobile: react-i18next + locales/{lang}/ui.json         │
│  Backend API: i18n dict + Accept-Language header        │
├─────────────────────────────────────────────────────────┤
│               CONTENT LAYER (prompts)                    │
│  gpcg/i18n/prompts/{lang}/games_prompts.py              │
│  gpcg/i18n/prompts/{lang}/kids_prompts.py               │
│  gpcg/i18n/prompts/{lang}/humanization.py               │
│  gpcg/i18n/prompts/{lang}/metadata.py                   │
│  gpcg/i18n/prompts/{lang}/creative_engine.py            │
│  gpcg/i18n/prompts/{lang}/editorial.py                  │
│  gpcg/i18n/prompts/{lang}/scoring.py                    │
├─────────────────────────────────────────────────────────┤
│              HEURISTICS LAYER (regex/patterns)           │
│  gpcg/i18n/heuristics/{lang}/ai_isms.py                 │
│  gpcg/i18n/heuristics/{lang}/stopwords.py               │
│  gpcg/i18n/heuristics/{lang}/clickbait.py               │
│  gpcg/i18n/heuristics/{lang}/chars_per_second.py        │
├─────────────────────────────────────────────────────────┤
│                ENGINE LAYER (TTS/ASR/VLM)                │
│  TTS: language parameter per job                        │
│  ASR: language hint per job                             │
│  VLM: language instruction in prompt                    │
│  Subtitles: font per language, case transform per lang  │
├─────────────────────────────────────────────────────────┤
│                   DATA LAYER (DB)                        │
│  ChannelProfile.target_language                         │
│  User.ui_language                                       │
│  ContentPlan.target_language                             │
│  Script.language                                        │
│  Video.language                                         │
│  KnowledgeItem.source_language                          │
└─────────────────────────────────────────────────────────┘
```

### 2.2 Fluxo de idioma no pipeline

```
ChannelProfile.target_language = "pt-BR"
        │
        ▼
Automation.config["language"] = "pt-BR"  (snapshot)
        │
        ▼
Job.artifacts["target_language"] = "pt-BR"  (propagado para o worker)
        │
        ├──► ScriptService.generate_script(language="pt-BR")
        │        └── carrega prompts de gpcg/i18n/prompts/pt-BR/
        │
        ├──► CreativeEngine.generate(language="pt-BR")
        │        └── carrega creative prompts de gpcg/i18n/prompts/pt-BR/
        │
        ├──► EditorialPlanner.plan(language="pt-BR")
        │        └── carrega planner prompt de gpcg/i18n/prompts/pt-BR/
        │
        ├──► HumanizationService.humanize(language="pt-BR")
        │        └── carrega ai_isms de gpcg/i18n/heuristics/pt-BR/
        │
        ├──► MetadataGenerator.generate(language="pt-BR")
        │        └── carrega metadata prompt de gpcg/i18n/prompts/pt-BR/
        │
        ├──► TTS: synthesize(text, language="pt", voice=bruno.wav)
        │
        ├──► ASR: transcribe(audio, language="pt")
        │
        ├──► Subtitles: font=DejaVuSans, case=upper, wrap=space-based
        │
        └──► YouTube upload: defaultLanguage="pt-BR"
```

---

## 3. Mapeamento Completo — O Que Precisa Mudar

### 3.1 Backend — Prompts LLM (DIFICULDADE: ALTA)

Esta é a camada mais crítica e com maior volume de mudanças. Existem **~30 prompts** que forçam pt-BR explicitamente.

#### 3.1.1 `src/gpcg/domains/games/prompts.py`

| Prompt | Linhas | Instrução pt-BR atual | Mudança necessária |
|--------|--------|----------------------|-------------------|
| `DRAFT_SYSTEM` | 14-43 | "Write a narration script in Brazilian Portuguese (pt-BR)" | Parameterizar `{target_language}` |
| `PLAN_DRAFT_SYSTEM` | 48-137 | "EXCLUSIVELY in Brazilian Portuguese" | Parameterizar |
| `REVISION_SYSTEM` | 142-177 | "MUST be written EXCLUSIVELY in Brazilian Portuguese" | Parameterizar |
| `OPTIMIZE_SYSTEM` | 180-203 | "output MUST be EXCLUSIVELY in Brazilian Portuguese" | Parameterizar |
| `REWRITE_SYSTEM` | 206-221 | "output MUST be EXCLUSIVELY in Brazilian Portuguese" | Parameterizar |
| `PLANNER_SYSTEM` | 226-317 | Persona "Brazilian gaming channel" | Adicionar `{target_language}` instruction |
| `CRITIC_SYSTEM` | 322-441 | Exemplos de AI-isms em PT | Localizar exemplos |
| `SECTION_CRITIC_SYSTEM` | 446-507 | Mesmo | Localizar |
| `SYSTEM_PROMPT_TEMPLATE` (creative) | 512-558 | "Português brasileiro natural", "em pt-BR" | Parameterizar |
| `BEAT_ORIENTED_PROMPT_TEMPLATE` | 566-617 | "Português brasileiro natural", "em pt-BR" | Parameterizar |
| `STORY_FINDER_SYSTEM` | 620-683 | "All text fields in pt-BR" | Parameterizar |
| `CURIOSITY_SCORER_SYSTEM` | 688-737 | ❌ Sem instrução de idioma | Adicionar instruction |
| `METADATA_SYSTEM` | 742-748 | "title and description in Brazilian Portuguese" | Parameterizar |
| `FACT_EXTRACTOR_SYSTEM` | 753-783 | "MUST be written EXCLUSIVELY in Brazilian Portuguese" | Parameterizar |
| `CONTENT_PLANNING_SYSTEM` | 788-810 | "topic/hook in pt-BR" | Parameterizar |

#### 3.1.2 `src/gpcg/domains/kids/prompts.py`

| Prompt | Linhas | Mesma estrutura |
|--------|--------|----------------|
| `DRAFT_SYSTEM` | 17-44 | ✅ pt-BR hardcoded |
| `PLAN_DRAFT_SYSTEM` | 49-95 | ✅ |
| `REVISION_SYSTEM` | 100-119 | ✅ |
| `OPTIMIZE_SYSTEM` | 122-134 | ✅ |
| `REWRITE_SYSTEM` | 137-152 | ✅ |
| `PLANNER_SYSTEM` | 156-212 | ❌ Sem instruction explícita |
| `CRITIC_SYSTEM` | 217-269 | ❌ |
| `CONTENT_PLANNING_SYSTEM` | 272-296 | ✅ |
| `METADATA_SYSTEM` | 299-308 | ✅ |
| `FACT_EXTRACTOR_SYSTEM` | 313-339 | ✅ |
| `STORY_FINDER_SYSTEM` | 343-366 | ❌ |
| `CURIOSITY_SCORER_SYSTEM` | 369-395 | ❌ |
| `SAFETY_FILTER_SYSTEM` | 400-442 | ❌ |
| `IDEA_SCORER_SYSTEM` | 447-485 | ❌ |
| `IDEATION_SYSTEM` | 490-521 | ✅ "titles in pt-BR" |

#### 3.1.3 Prompts inline (application layer)

| Arquivo | Prompt | Linhas | Mudança |
|---------|--------|--------|---------|
| `humanization.py` | `HUMANIZATION_SYSTEM` | 228-276 | Prompt inteiro em PT; localizar |
| `humanization.py` | `AI_ISM_PATTERNS` | 104-119 | Regex de AI-isms em PT; criar por idioma |
| `humanization.py` | `REDUNDANCY_PATTERNS` | 122-129 | "ou seja", "em outras palavras" etc. |
| `humanization.py` | `IGNORANCE_IDENTIFICATION_PHRASES` | 133-139 | "eu também não sabia" etc. |
| `opening_renderer.py` | Title shortener | 205-215 | "Você é um editor de YouTube..." |
| `knowledge_item_service.py` | Scoring prompt | 401-421 | "Você é um editor de conteúdo gaming..." |
| `knowledge_item_service.py` | Headless scoring | 473-493 | Duplicata do acima |
| `metadata_generator.py` | Inline prompt | 63-78 | "Generate title in pt-BR..." |
| `game_enrichment.py` | Lore summary | 239-245 | "Resuma a história em português..." |
| `editorial_strategy.py` | Editorial prompt | 379-401 | "Você é o editor-chefe..." |
| `editorial_strategy.py` | Detailed version | 518-554 | Mesmo, versão longa |
| `vision_analyzer.py` | VLM prompts (5) | 30-201 | ❌ Em inglês — adicionar instruction de idioma para descrições |

#### 3.1.4 Estratégia de refatoração de prompts

**Opção A — Template variables (recomendada para fase 1):**
```python
# gpcg/i18n/prompts/base.py
LANGUAGE_INSTRUCTIONS = {
    "pt-BR": "The script MUST be written EXCLUSIVELY in Brazilian Portuguese (pt-BR).",
    "en": "The script MUST be written in English.",
    "es": "The script MUST be written in Spanish.",
    "fr": "The script MUST be written in French.",
}

def language_instruction(lang: str) -> str:
    return LANGUAGE_INSTRUCTIONS.get(lang, LANGUAGE_INSTRUCTIONS["pt-BR"])
```

Cada prompt se torna um template:
```python
DRAFT_SYSTEM = f"""You are a scriptwriter for a gaming YouTube Shorts channel.
Write a narration script for a vertical Short.

CRITICAL — LANGUAGE:
{language_instruction(target_language)}
...
"""
```

**Opção B — Arquivos de prompt por idioma (fase 2):**
```
gpcg/i18n/prompts/
  pt-BR/
    games_prompts.py
    kids_prompts.py
    humanization.py
    metadata.py
  en/
    games_prompts.py
    ...
```

**Recomendação:** Começar com Opção A (template variables) — menos código duplicado, mais fácil de manter. Migrar para Opção B quando os prompts divergirem significativamente entre idiomas (ex: humor, expressões culturais).

### 3.2 Backend — Heurísticas de Texto (DIFICULDADE: MÉDIA)

#### 3.2.1 AI-isms (humanization.py)

Padrões regex que detectam linguagem de IA em PT-BR:
```python
AI_ISM_PATTERNS = [
    re.compile(r"você não vai acreditar", re.I),
    re.compile(r"prepare-se para", re.I),
    re.compile(r"e é aí que", re.I),
    re.compile(r"já imaginou se", re.I),
    re.compile(r"neste vídeo (iremos|vamos)", re.I),
    # ... ~15 padrões
]
```

**Solução:** Criar `gpcg/i18n/heuristics/{lang}/ai_isms.py` com listas por idioma. Quando `target_language != pt-BR`, carregar a lista apropriada. Se não existir lista para o idioma, desabilitar humanização de AI-isms (ou usar a lista de EN como fallback).

#### 3.2.2 Stopwords (thumbnail_selector.py)

```python
stop_words = {"o", "a", "os", "as", "de", "do", "da", ...}
```

**Solução:** Mover para `gpcg/i18n/heuristics/{lang}/stopwords.py`. O thumbnail selector já tem fallback graceful — se não houver stopwords para o idioma, usa o texto bruto.

#### 3.2.3 Clickbait/promoção/rumor (knowledge_item_service.py)

```python
re.compile(r"\b(você não vai acreditar|you won'?t believe)\b", re.I),
re.compile(r"\b(número \d+ que|this one trick|este truque)\b", re.I),
re.compile(r"\b(leak|vazamento|não confirmado|unconfirmed)\b", re.I),
```

**Solução:** Já são parcialmente bilíngues (PT+EN). Estender para incluir padrões de outros idiomas ou tornar configurável.

#### 3.2.4 Chars per second (TTS calibration)

```python
CHARS_PER_SECOND_PT_BR = 14.5  # em video-generate
target_chars = int(plan.target_duration * 14)  # em kids/pipeline.py
```

**Solução:** Criar `gpcg/i18n/heuristics/{lang}/tts_config.py`:
```python
TTS_CONFIG = {
    "pt-BR": {"chars_per_second": 14.5, "target_chars_60s": 900},
    "en":    {"chars_per_second": 16.0, "target_chars_60s": 960},
    "es":    {"chars_per_second": 15.0, "target_chars_60s": 900},
    "fr":    {"chars_per_second": 15.0, "target_chars_60s": 900},
}
```

#### 3.2.5 Editorial planner — gameplay query keywords

`editorial_planner.py:338-351` tem keywords mistas PT+EN:
```python
# bicicleta, carro, luta, arma, neve, comida, ninja, furtivo...
```

**Solução:** Mover para um dict por idioma ou tornar o planner dependente do `target_language`.

### 3.3 Backend — Mensagens de API (DIFICULDADE: BAIXA)

#### 3.3.1 Strings hardcoded

| Arquivo | Linha | String PT |
|---------|-------|-----------|
| `routes.py` | 412 | "Este arquivo já foi enviado" |
| `routes.py` | 1808 | "Este vídeo não tem uma ideia associada..." |
| `automation_routes.py` | 180-197 | `STAGE_LABELS` dict (15+ labels) |
| `automation_routes.py` | 231, 269, 304 | "Automação não encontrada" |
| `automation_routes.py` | 273 | "Conecte seu canal do YouTube primeiro" |
| `automation_routes.py` | 282 | "Envie gameplays primeiro" |
| `automation_routes.py` | 1695 | "Não foi possível obter URL do Google" |
| `auth_routes.py` | 282, 310 | "Usuário não encontrado" |
| `auth_routes.py` | 284 | "Não é possível excluir a si mesmo" |
| `kids_routes.py` | 241-242 | "Tipo de arquivo não suportado..." |
| `kids_routes.py` | 269 | "Arquivo muito grande (máx 2 GiB)" |
| `kids_routes.py` | 292 | "Este arquivo já foi enviado" |
| `knowledge_item_routes.py` | 223 | "Já existem 3 jobs de coleta..." |
| `app_routes.py` | 80 | "Nenhum APK disponível" |
| `core/models.py` | 252 | default="Minha Automação" |
| `core/models.py` | 688, 692, 717 | "Descrição do canal", "Público-alvo", etc. |

**Solução:** Criar `gpcg/i18n/api_messages.py`:
```python
MESSAGES = {
    "pt-BR": {
        "file_already_sent": "Este arquivo já foi enviado",
        "automation_not_found": "Automação não encontrada",
        "connect_youtube_first": "Conecte seu canal do YouTube primeiro",
        "send_gameplays_first": "Envie gameplays primeiro",
        "user_not_found": "Usuário não encontrado",
        "cannot_delete_self": "Não é possível excluir a si mesmo",
        "no_apk": "Nenhum APK disponível",
        "stage_labels": {
            "content_planning": "Planejando conteúdo",
            "music_selection": "Escolhendo música",
            "render_plan": "Preparando renderização",
            "render": "Renderizando vídeo",
            "output": "Enviando vídeo",
            "content_collection": "Coletando conteúdo",
            # ...
        },
    },
    "en": {
        "file_already_sent": "This file has already been uploaded",
        "automation_not_found": "Automation not found",
        # ...
    },
}

def get_message(key: str, lang: str = "pt-BR", **kwargs) -> str:
    msg = MESSAGES.get(lang, MESSAGES["pt-BR"]).get(key, key)
    return msg.format(**kwargs) if kwargs else msg
```

**Como o idioma é determinado:** O backend lê `User.ui_language` (do DB) ou o header `Accept-Language` da requisição.

#### 3.3.2 SSE / Event payloads (DIFICULDADE: MÉDIA — revisão 0.9)

**Problema atual:** Events SSE já enviam strings PT para o frontend:
- `worker/handlers/generation.py:88` — `send_status("busy", "Gerando vídeo")`
- `worker/handlers/generation.py:106` — `send_status("busy", "Enviando vídeo")`
- `automation_routes.py:180-197` — `STAGE_LABELS` dict em PT, retornado como `stage_label`
- `workers/_common.py:99` — `current_activity` é string PT legível

**Impacto:** Mudar UI language não ajuda — as strings vêm do backend no event stream.
Dashboard em EN vai mostrar labels PT.

**Solução:** Substituir strings PT por **stable keys**:
```python
# Antes
STAGE_LABELS = {"content_planning": "Planejando conteúdo", ...}
send_status("busy", "Gerando vídeo")

# Depois
STAGE_KEYS = {"content_planning": "stage.content_planning", ...}
send_status("busy", "worker.activity.generating_video")
```

O frontend recebe o key e localiza via `t(key)`. Manter um fallback display string
opcional para compatibilidade com clients que não localizam.

**Escopo completo (revisão 0.24):** São **16+ strings PT** em worker handlers, não apenas 2:

| Handler | Stable key proposto |
|---------|---------------------|
| `generation.py:88` | `worker.activity.generating_video` |
| `generation.py:106` | `worker.activity.uploading_video` |
| `kids.py:32` | `worker.activity.kids_discovering_ideas` |
| `kids.py:274` | `worker.activity.kids_evaluating_idea` |
| `kids.py:445` | `worker.activity.kids_processing_media` |
| `kids.py:545` | `worker.activity.kids_mapping_media` |
| `mapping.py:57` | `worker.activity.identifying_game` |
| `mapping.py:281` | `worker.activity.mapping_gameplay` |
| `knowledge.py:55` | `worker.activity.downloading_document` |
| `knowledge.py:67` | `worker.activity.indexing_document` |
| `content_collect.py:26` | `worker.activity.collecting_rss` |
| `enrichment.py:30` | `worker.activity.enriching_game` |
| `cleanup.py:36` | `worker.activity.cleaning_gameplay` |
| `cleanup.py:117` | `worker.activity.cleaning_storage` |
| `file_transfer.py:48` | `worker.activity.downloading_file` |
| `remote_worker.py:456` | `worker.activity.synchronizing_file` |

Variáveis dinâmicas (`{filename}`, `{idea_id}`, etc.) devem ser passadas como
params separados no event payload, não interpoladas na string PT.

### 3.4 Backend — Modelos de Dados (DIFICULDADE: MÉDIA)

#### 3.4.1 Colunas a adicionar

| Modelo | Coluna | Tipo | Default | Justificativa |
|--------|--------|------|---------|---------------|
| `User` | `ui_language` | `String(10)` | `"pt-BR"` | Idioma da interface do usuário |
| `ChannelProfile` | `target_language` | `String(10)` | `"pt-BR"` | Idioma alvo do conteúdo (canal) |
| `Automation` | adicionar `language` no `config` JSON | — | `"pt-BR"` | Snapshot do idioma na automação |
| `ContentPlan` | `target_language` | `String(10)` | `"pt-BR"` | Idioma alvo do plano |
| `Script` | `language` | `String(10)` | `"pt-BR"` | Idioma em que o script foi gerado |
| `Video` | `language` | `String(10)` | `"pt-BR"` | Idioma do vídeo final |
| `KnowledgeItem` | `source_language` | `String(10)` | nullable | Idioma detectado da fonte |
| `Document` | `source_language` | `String(10)` | nullable | Idioma do documento |
| `Fact` | `source_language` | `String(10)` | nullable | Idioma do fato |

#### 3.4.2 Migração

Usar `_ensure_column()` em `init_db()` (em `src/gpcg/infrastructure/database.py:107-122`, **não** `db.py`):
```python
_ensure_column(engine, "users", "ui_language", "VARCHAR(10) DEFAULT 'pt-BR'")
_ensure_column(engine, "channel_profiles", "target_language", "VARCHAR(10) DEFAULT 'pt-BR'")
_ensure_column(engine, "content_plans", "target_language", "VARCHAR(10) DEFAULT 'pt-BR'")
_ensure_column(engine, "scripts", "language", "VARCHAR(10) DEFAULT 'pt-BR'")
_ensure_column(engine, "videos", "language", "VARCHAR(10) DEFAULT 'pt-BR'")
_ensure_column(engine, "knowledge_items", "source_language", "VARCHAR(10)")
```

**CRÍTICO:** `_ensure_column` adiciona a coluna no SQL mas **não** no modelo SQLAlchemy.
É obrigatório adicionar o `Mapped[...]` attribute na classe do modelo também:
```python
# src/gpcg/core/models.py — ChannelProfile
target_language: Mapped[str] = mapped_column(String(10), default="pt-BR")
```
Sem o attribute no modelo, o ORM não vê a coluna e inserts não populam o valor.

#### 3.4.3 Propagação do idioma (fluxo correto após auditoria)

O plano v1 estava errado sobre este fluxo. O caminho real é:

```
ChannelProfile.target_language (VPS DB)
    ↓
get_job_data (api/workers/generation.py:346-364) serializa channel_profile.target_language
    ↓
local_db_sync.py:590-604 replica ChannelProfile com target_language para SQLite local do worker
    ↓
GenerationService._run_pipeline (generation_service.py:405-418) lê channel_profile.target_language
do DB local (NÃO de job.artifacts)
    ↓
Passa language para ContentPlanningService → ScriptService → MetadataGenerator → etc.
    ↓
ContentPlan.target_language, Script.language, Video.language são setados nos novos registros
```

**Pontos de mudança obrigatórios (4 arquivos):**

1. **`api/workers/generation.py:346-364`** — adicionar `"target_language": profile.target_language` no dict `channel_profile`
2. **`worker/local_db_sync.py:590-604`** — adicionar `target_language=profile_data.get("target_language", "pt-BR")` no `ChannelProfile()`
3. **`api/automation_routes.py:74-84`** — adicionar `"language"` a `_CONFIG_SNAPSHOT_FIELDS` para snapshot determinístico em retries
4. **`application/generation_service.py:405-418`** — ler `channel_profile.target_language` e passar para todos os serviços

**Snapshot para checkpoints (revisão 0.15):**
Além do `ChannelProfile`, o `target_language` DEVE ser snapshot em `job.artifacts["config_snapshot"]["language"]`
no momento de criação do job. Quando o worker reinicia após crash e resume um job, ele deve usar
o `config_snapshot["language"]` (snapshot), **não** o `ChannelProfile.target_language` live
(que pode ter mudado enquanto o worker estava offline). O `config_snapshot` já é lido em
`generation_service.py:750` para `max_clip_uses` e `fallback_policy` — basta adicionar `language`.

**Fluxo de snapshot:**
```
ChannelProfile.target_language
    → _build_config_snapshot inclui "language" (após adicionar a _CONFIG_SNAPSHOT_FIELDS)
    → job.artifacts["config_snapshot"]["language"] = "pt-BR" (congelado no momento do enqueue)
    → worker lê config_snapshot["language"] ao resumir (determinístico)
    → fallback: se config_snapshot não tem "language", lê channel_profile.target_language
```

### 3.5 Backend — Configuração (DIFICULDADE: BAIXA)

#### 3.5.1 Novas env vars

```python
# src/gpcg/config.py
gpcg_default_language: str = "pt-BR"        # Idioma padrão do sistema
gpcg_tts_language: str = "pt"               # Já existe — manter como fallback
gpcg_asr_language: str = ""                 # Novo — vazio = auto-detect
gpcg_rss_language: str = "pt-BR"            # Novo — hl/gl do Google News
gpcg_rss_country: str = "BR"                # Novo — gl do Google News
```

#### 3.5.2 RSS feed

Atual:
```python
gpcg_rss_feed_url: str = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
```

Proposto:
```python
gpcg_rss_feed_url: str = "https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={gl}:{lang_short}"
```

O `content_collectors.py` substitui `{hl}`, `{gl}`, `{lang_short}` baseado em `gpcg_rss_language` e `gpcg_rss_country`.

### 3.6 Backend — Serviços (DIFICULDADE: MÉDIA-ALTA)

Cada serviço que constrói prompts precisa aceitar `target_language` e carregar o prompt apropriado.

#### Serviços a modificar:

| Serviço | Arquivo | Mudança |
|---------|---------|---------|
| `ScriptService` | `script_service.py` | Adicionar `language` param; carregar prompts de `i18n/prompts/{lang}/` |
| `MetadataGenerator` | `metadata_generator.py` | Adicionar `language` param; localizar prompt e fallbacks |
| `CreativeEngine` | `creative_engine.py` | Adicionar `language` param; localizar `CREATIVE_PRESETS` labels e style block |
| `EditorialPlanner` | `editorial_planner.py` | Adicionar `language` param; localizar `PLANNER_SYSTEM` |
| `ScriptCritic` | `script_critic.py` | Adicionar `language` param; localizar `CRITIC_SYSTEM` |
| `StoryFinder` | `story_finder.py` | Adicionar `language` param; localizar `STORY_FINDER_SYSTEM` |
| `CuriosityScorer` | `curiosity_scorer.py` | Adicionar `language` param (ou manter EN — é scoring interno) |
| `HumanizationService` | `humanization.py` | Adicionar `language` param; carregar `AI_ISM_PATTERNS` por idioma |
| `OpeningRenderer` | `opening_renderer.py` | Adicionar `language` param; localizar prompt de title shortening |
| `KnowledgeItemService` | `knowledge_item_service.py` | Adicionar `language` param; localizar scoring prompt |
| `GameEnrichment` | `game_enrichment.py` | Adicionar `language` param; localizar lore summary prompt |
| `EditorialStrategy` | `editorial_strategy.py` | Adicionar `language` param; localizar editorial prompt |
| `ContentPlanningService` | `content_planning_service.py` | Adicionar `language` param; localizar `CONTENT_PLANNING_SYSTEM` |
| `GenerationService` | `generation_service.py` | Ler `target_language` de `job.artifacts`; passar para todos os serviços acima |

#### 3.6.1 Pattern de refatoração

**Antes:**
```python
class ScriptService:
    def generate_script(self, session, job_id, topic, fact, ...):
        system_prompt = DRAFT_SYSTEM  # hardcoded pt-BR
        ...
```

**Depois:**
```python
from gpcg.i18n import get_prompt

class ScriptService:
    def generate_script(self, session, job_id, topic, fact, ..., language: str = "pt-BR"):
        system_prompt = get_prompt("games/draft_system", language)
        ...
```

O `get_prompt` carrega de `gpcg/i18n/prompts/{language}/games_prompts.py` com fallback para `pt-BR`.

#### 3.6.1 Originality check — language-awareness (revisão 0.7)

**Problema:** `originality.py` é language-naive — Jaccard overlap de 5-grams após normalização.
- Tradução palavra-a-palavra PT→EN **passa** no check (tokens não casam, overlap ≈ 0, score = 100)
- Geração EN de fonte EN pode ter overlap alto e trigger rewrite desnecessário
- Não há `script_language` nem `source_language` no check

**Solução:**
1. Adicionar `script_language` e `source_language` params ao `OriginalityService`
2. Se `script_language == source_language`: comportamento atual (n-gram overlap)
3. Se `script_language != source_language`: usar similaridade semântica (embeddings) em vez de n-gram, OU traduzir sources para o target language antes de comparar
4. Threshold por idioma (EN tem mais overlap natural de frases comuns que PT)
5. Adicionar `language` ao `OriginalityReport`

#### 3.6.2 Creative Engine — description é prompt, não só label (revisão 0.10)

**Problema:** `CREATIVE_PRESETS` tem `label` (UI) E `description` (prompt content):
```python
"humor": CreativeStyle(
    name="humor",
    label="Humor brasileiro",
    description="Humor espontâneo, observações engraçadas do cotidiano..."
)
```
`_build_style_block` injeta `description` + `level()` (PT: "muito alto", "alto", "médio") no prompt.
Traduzir o `label` sem traduzir `description` e `level()` deixa o prompt em PT.

**Solução:** Refatorar `CreativeStyle`:
```python
@dataclass
class CreativeStyle:
    name: str                    # key estável: "humor"
    label: dict[str, str]        # UI por idioma: {"pt-BR": "Humor brasileiro", "en": "Humor"}
    description: dict[str, str]  # prompt por idioma: {"pt-BR": "...", "en": "..."}
    # level() também por idioma
```

`get_style(name, language="pt-BR")` retorna o estilo com label/description/level no idioma correto.

#### 3.6.3 QA Service — language param + issue descriptions (revisão 0.26)

**Problema:** `qa_service.py:164-197` — prompt em inglês, sem `target_language`:
```python
system = """You are a YouTube Shorts quality reviewer. Evaluate the script and metadata
for a generated Short. You do NOT see the video frames — evaluate the content quality only."""
```
- Avalia script (que está em PT) com critérios em EN
- Issue descriptions são English ("duration too short", "no audio stream") — surfaced para usuários
- Não checa subtitles/captions/text overlays

**Solução:**
1. Adicionar `language` param ao `QAService` — passar `target_language` do job
2. Prompt system em EN (mantém), mas adicionar instruction: "Evaluate the script in {language_name}"
3. Issue descriptions: usar stable keys + localizar no frontend (ex: `qa.issue.duration_too_short`)
4. Considerar adicionar checks de subtitles/captions (text overlay language match)

### 3.7 Backend — VLM / Vision Analyzer (DIFICULDADE: MÉDIA)

**Problema crítico:** Os prompts do VLM (`vision_analyzer.py`) estão em inglês e produzem descrições de eventos em inglês. Mas o `gameplay_query` do editorial planner é gerado em PT-BR. Isso cria um **mismatch semântico** — a busca por clips usa keywords em PT mas os eventos estão descritos em EN.

**Solução proposta:**
1. **Mantém VLM em inglês** — os modelos VLM (gemma3:12b) são mais confiáveis em inglês para análise visual
2. **Adiciona um campo `language` ao `GameplayEvent`** — registra o idioma da descrição
3. **Traduz o `gameplay_query`** antes da busca semântica, ou usa embeddings multilinguais (nomic-embed-text suporta múltiplos idiomas)
4. **Alternativa:** Adicionar instruction de idioma ao VLM prompt para que as descrições sejam geradas no `target_language`

**Recomendação:** Mantém VLM em EN por enquanto. Usa embeddings multilinguais para casar query (PT) com events (EN). Isso já funciona parcialmente hoje via cosine similarity.

#### 3.7.1 Embeddings — coluna de language + re-embedding (revisão 0.8)

**Problema:** `KnowledgeItemEmbedding` e `GameplayEventEmbedding` armazenam BLOBs com `model` column mas **sem `language` column**. Embeddings são gerados do texto como-está. Se um KI é traduzido, o embedding fica **stale**.

**Solução:**
1. Adicionar `language: str` column a `KnowledgeItemEmbedding` e `GameplayEventEmbedding`
2. Quando um KI é traduzido, regerar embedding e atualizar
3. Expor endpoint/job de re-embedding para dados existentes
4. O `gameplay_retriever` já faz cosine similarity — embeddings multilinguais de `nomic-embed-text` permitem casar query PT com events EN
5. Se VLM mudar para gerar descrições no `target_language`, regerar embeddings dos events

**Estratégia de migração:**
- Embeddings existentes: marcar `language = "en"` (VLM descriptions são EN) ou `language = "unknown"`
- KIs existentes: detectar idioma com `langdetect`, marcar `language` accordingly
- Re-embedding sob demanda (lazy) — não regerar tudo de uma vez

### 3.8 Backend — ASR (DIFICULDADE: BAIXA)

```python
# Atual — auto-detect
transcribe(audio_path, language="")

# Proposto — passar idioma do canal
transcribe(audio_path, language=target_language_short)  # "pt", "en", "es", "fr"
```

O ASR transcreve o áudio do gameplay (não do narrador). O idioma do gameplay pode ser diferente do idioma do conteúdo. **Recomendação:** manter auto-detect para gameplay audio, pois o gameplay pode ter áudio em qualquer idioma. O `AudioSegment.language` já é detectado e pode ser persistido.

### 3.9 Backend — TTS (DIFICULDADE: MÉDIA)

#### 3.9.1 GPCG adapter

```python
# Atual
self.tts_language = s.gpcg_tts_language  # sempre "pt"

# Proposto
def synthesize_tts(self, text, output_wav, *, voice_path=None, language=None):
    lang = language or self.tts_language  # fallback para config
    ...
```

O `GenerationService` passa `language=job.artifacts.get("target_language_short", "pt")`.

#### 3.9.2 video-generate — propagação de language end-to-end (revisão 0.2, 0.3)

**Problema crítico (revisão 0.2):** O plano v1 dizia que o adapter passava `self.tts_language`.
Mas isso só é verdade para `synthesize()` (low-level). O render path **ignora language**:
- `video_generate_adapter.py:364-391` — render script chama `process_video_request(req)` **sem language**
- `generate.py:1647-1724` — `process_video_request()` não aceita `language`
- `generate_tts()` (`tts.py:572-663`) — **não tem parâmetro `language`**
- `generate_media.py:1950-1955` — chama `generate_tts()` sem language

**Whisper em video-generate (revisão 0.3):** O Whisper em `generate.py:626-642` é para
**alinhar legendas da narração TTS**, não para transcrever gameplay. É forçado em `pt`:
```python
initial_prompt = "Transcrição de narração em português brasileiro..."
result = model.transcribe(audio_file, language="pt", ...)
```
E WhisperX align model também: `whisperx.load_align_model(language_code="pt", ...)` (linha 650-651).

**Mudanças necessárias em video-generate:**

| Arquivo | Mudança |
|---------|---------|
| `generate.py:1647-1724` | `process_video_request` aceitar `language`/`whisper_language` no `request_data` |
| `generate.py:626-642` | Whisper `language="pt"` → `language=request_language`; initial_prompt localizado ou removido |
| `generate.py:650-651` | WhisperX `language_code="pt"` → `language_code=request_language` |
| `tts.py:572-663` | `generate_tts()` aceitar `language` param e passar para `synthesize()` |
| `tts.py:220` | `synthesize(..., language="pt")` → default de request, não hardcoded |
| `tts.py:67-70` | `CHARS_PER_SECOND_PT_BR` → dict por idioma |
| `generate_media.py:1950` | Forward `language` de request para `generate_tts()` |
| `tts_xtts.py:58` | Lista de idiomas já existe: `pt,en,es,fr,de,it,...` — validar request |
| `tts_kokoro.py:76` | Default `pt-br` → request language |

**GPCG adapter:**
- `video_generate_adapter.py:104` — `synthesize_tts()` aceitar `language` param
- `video_generate_adapter.py:150` — passar `language` (não `self.tts_language` hardcoded)
- `video_generate_adapter.py:364-391` — render script: adicionar `language` ao `request_data`
- `generation_service.py:664-666` — passar `language=channel_profile.target_language` para `synthesize_tts`

#### 3.9.3 Kids pipeline — chars/second hardcoded (revisão 0.14)

**Problema:** `kids/pipeline.py:461` — `target_chars = int(plan.target_duration * 14)` — hardcoded 14 chars/sec, separado do `CHARS_PER_SECOND_PT_BR` do video-generate.

**Solução:** Usar o mesmo dict de chars/second:
```python
from gpcg.i18n.heuristics import get_tts_config
tts_cfg = get_tts_config(language)
target_chars = int(plan.target_duration * tts_cfg["chars_per_second"])
```

#### 3.9.4 TextNormalizer — hardcoded PT-BR (revisão 0.5)

**Problema:** `video-generate/src/processors/text_normalizer.py` normaliza números, moedas, datas, abreviações para PT-BR:
```python
UNITS = {'km': 'quilômetros', 'kg': 'quilos', 'R$': ('real', 'reais'), ...}
ABBREVIATIONS = {'Dr.': 'doutor', ...}
# "20/05/2020" → "vinte de maio de..."
# "R$ 50" → "cinquenta reais"
# "1,5" → "um vírgula cinco"
```
Se chamado para EN/ES/FR, produz TTS input errado.

**Solução:** Criar locale packs para TextNormalizer:
```
video-generate/src/processors/text_normalizer/
  __init__.py          # dispatch por language
  pt_br.py             # normalizador PT-BR atual
  en.py                # "1.5" → "one point five", "$50" → "fifty dollars"
  es.py
  fr.py
```

#### 3.9.5 AcronymHandler — PT-specific (revisão 0.6)

**Problema:** `acronym_handler.py`:
- `PORTUGUESE_COMMON_WORDS` blacklist (linha 19-20)
- Prompt: "Você é um especialista em fonética e pronúncia de siglas em português brasileiro" (321-334)
- `has_vowels = any(char in "AEIOU" for char in acronym)` — só vogais latinas (300)
- `r"\b[A-Z]{2,5}\b"` — só detecta siglas latinas (250)

**Solução:**
- Common words blacklist por idioma
- Prompt de pronúncia de siglas por idioma
- Para CJK/Cyrillic/Arabic: desabilitar expansão de siglas (não aplicável)

#### 3.9.6 Vozes por idioma

XTTS usa voice cloning — precisa de um WAV de referência por idioma:
```
public/voices/
  bruno-pt.wav      # voz em português
  bruno-en.wav      # voz em inglês (pode ser a mesma pessoa falando EN)
  bruno-es.wav      # voz em espanhol
```

O `Automation.config["voice"]` já suporta path arbitrário. Basta que o usuário faça upload de vozes para cada idioma e selecione a apropriada.

**Alternativa:** Se o usuário só tem uma voz (PT), o XTTS pode tentar clonar para outro idioma, mas a qualidade varia. Documentar isso.

#### 3.9.7 TextDualProcessor — componente PT-BR monolítico (revisão 0.20)

**Problema:** `video-generate/src/processors/text_dual_processor.py` é PT-BR-only:
- `:32-48` — instancia `TextNormalizer` (PT-BR)
- `:75-85` — normaliza texto antes do LLM
- `:182-253` — prompt inteiro em PT: "Você é um especialista em otimização de texto para síntese de voz (TTS) em português brasileiro"
- Produz `tts_text` (expandido para fala) e `subtitle_text` (texto original para display)
- Retorna `segments` mapping TTS fragments → subtitle fragments + `expansions`

**Bloqueador para solução 0.4:** Se o GPCG começar a usar `generate_tts()` com
`return_subtitle_mapping=True` (caminho que produz `segments` para evitar o fallback Whisper),
o TextDualProcessor vai processar o texto com lógica PT-BR.

**Solução (2 opções):**
1. **Parameterizar TextDualProcessor** — adicionar `language` param, localizar prompt,
   e fazer TextNormalizer dispatch por idioma (ver 3.9.4)
2. **Não usar TextDualProcessor no GPCG** — o GPCG adapter já chama `synthesize()` diretamente
   (que não usa TextDualProcessor). Para produzir `segments` sem TextDualProcessor,
   o adapter pode construir segments manualmente a partir do texto + timing do TTS chunks

**Recomendação:** Opção 2 é mais simples e evita modificar o video-generate.
O adapter já sabe os chunks e suas durações (após `synthesize()`), pode construir
`segments` com timing proporcional.

#### 3.9.8 RenderPlanBuilder — adicionar campo language (revisão 0.27)

**Problema:** `render_plan_builder.py`:
- `:55-61` — `RenderPlan` dataclass não tem campo `language`
- `:258-273` — `request_data` dict não inclui `language`
- Keys em português (`audio_principal`, `musica_fundo`, `delay_musica`) — são keys técnicas
  do contract video-generate, **não localizáveis**, mas devem ser documentadas como tal

**Solução:**
1. Adicionar `language: str` ao `RenderPlan` dataclass
2. Adicionar `"language": language` ao `request_data` dict
3. `RenderPlanBuilder.build()` recebe `language` param de `GenerationService`
4. Documentar que keys PT do `request_data` são técnicas (não user-facing)

### 3.10 Backend — Subtitles / Legendas (DIFICULDADE: ALTA para CJK/RTL, BAIXA para Latim)

#### 3.10.1 Fonte

```python
# Atual — hardcoded em video-generate
font_file="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
```

**Solução:** Mapa de fontes por idioma:
```python
FONT_MAP = {
    "pt-BR": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "en":    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "es":    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "fr":    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    # Fase futura:
    # "ja": "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.otf",
    # "zh-cn": "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.otf",
    # "ar": "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
}
```

DejaVuSans cobre todos os caracteres Latim (incluindo acentos PT, ES, FR). Para idiomas latinos, **não há mudança de fonte necessária**.

#### 3.10.2 Case transform

```python
case_transform="upper"  # reel_9_16
```

`upper` funciona para Latim. Para turco (İ/ı), grego, etc. pode causar problemas. **Para PT/EN/ES/FR:** sem mudança necessária.

#### 3.10.3 Quebra de linha

`wrap_text()` quebra por whitespace. Funciona para idiomas separados por espaço (PT, EN, ES, FR). **CJK precisa de quebra por caractere** — fase futura.

#### 3.10.4 subtitle_mapping minimal causa fallback Whisper (revisão 0.4)

**Problema:** `video_generate_adapter.py:210` cria `{"tts_text": text, "expansions": []}` — **sem `segments`**.
`generate.py:962-1009` — `generate_auto_srt()` só usa `subtitle_mapping` se tiver `segments`:
```python
mapping_segments = subtitle_mapping.get("segments") or []
if prepared_segments:
    srt_content = segments_to_srt(prepared_segments, profile=profile)
else:
    print("⚠️ subtitle_mapping sem segmentos válidos - ativando fallback Whisper")
```
Sem `segments`, cai no Whisper (forçado em `pt`). Os timings TTS→legenda são perdidos.

**Solução (2 opções):**
1. **GPCG adapter produz `segments`** — o adapter precisa construir o mapping completo
   com `original_text`, `tts_text`, `subtitle_text`, `segments`, `expansions` (formato que
   video-generate espera, ver `tts.py:696-713`)
2. **`generate_auto_srt` aceita `original_text` + language** sem cair no Whisper —
   usar o texto do script + duração do TTS para gerar SRT sem Whisper

**Recomendação:** Opção 2 é mais simples e robusta. Passar `original_text` (script) +
`language` para `generate_auto_srt`, e usar segmentação por sentença + timing proporcional
em vez de Whisper.

#### 3.10.5 `text_align` já existe no schema mas é ignorado (revisão 0.12)

**Problema:** `video_profile.py:30` — `text_align: str = "C"` já existe no `SubtitleProfile`.
Mas `subtitle_renderer.py:104-162` hardcodeia `x_expr = "(w-text_w)/2"`, nunca lê `sp.text_align`.

**Solução:** Implementar `text_align` no renderer antes de suportar RTL:
```python
if sp.text_align == "L":
    x_expr = "10"  # left margin
elif sp.text_align == "R":
    x_expr = "(w-text_w-10)"  # right margin
else:  # "C"
    x_expr = "(w-text_w)/2"
```

#### 3.10.6 `resolve_video_profile` não aceita override de fonte (revisão 0.13)

**Problema:** `profile_registry.py:55-129` — `resolve_video_profile` só aceita overrides de
box/stroke/transition. **Não aceita** `subtitle_font` ou `font_file`. Override de fonte só
é possível via `_gpcg_custom_profile` completo.

**Solução:** Adicionar `subtitle_font_file` aos overrides aceitos por `resolve_video_profile`:
```python
# profile_registry.py — resolve_video_profile
subtitle_font_file = request_data.get("subtitle_font_file")
if subtitle_font_file:
    profile = replace(profile, subtitle=replace(
        profile.subtitle, font_file=subtitle_font_file
    ))
```
Assim GPCG pode passar fonte por idioma sem enviar um profile completo.

#### 3.10.7 wrap_text descarta \n + font escaping incompleto (revisão 0.21)

**Problema 1:** `subtitle_renderer.py:26-29`:
```python
clean_text = " ".join(text.replace("\n", " ").split())
```
**Descarta quebras de linha intencionais** — se o script tem `\n` para forçar quebra de legenda,
o renderer ignora. Para multilinguagem, isso afeta todos os idiomas.

**Problema 2:** `subtitle_renderer.py:122-123`:
```python
clean = wrapped.replace("'", "'\\\\''").replace(":", "\\:")
```
Só escapa `'` e `:`. **Não escapa** `\`, `%`, `,`, `=`, `[`, `]` que podem aparecer em
texto não-Latim ou emoji. Pode causar FFmpeg errors ou renderização incorreta.

**Solução:**
1. Preservar `\n` intencionais no wrap_text (split por `\n` primeiro, depois wrap cada parágrafo)
2. Usar `textfile=` em vez de `text=` para evitar escaping (o `opening_renderer.py` já faz isso)
3. Ou implementar escaping completo de todos os FFmpeg drawtext specials

#### 3.10.8 SRT sem language tags + VTT dead code + SRT não vai para YouTube (revisão 0.22)

**Problema:**
1. `generate.py:1011-1013` — SRT escrito sem `LANGUAGE` tag
2. `subtitle_generator.py:107-159` — `generate_vtt()` existe mas **não é chamado** (dead code)
3. `google_integration_adapter.py:59-118` — upload YouTube **não envia SRT/caption**, só title/description/tags
4. SRT é usado apenas para burn-in, não como caption file separado

**Solução:**
1. Adicionar `LANGUAGE` tag ao SRT (ou nomear arquivo como `auto_generated.{lang}.srt`)
2. Considerar remover `generate_vtt()` dead code ou ativar para YouTube captions
3. Extender `google_integration_adapter.py` upload body para aceitar `defaultLanguage` e caption file
4. Verificar contract do google-integration service antes de mudar o payload (ver seção 3.11)

#### 3.10.9 Presentation Layer — font, prompt, wrap_text (revisão 0.25)

**Problema:** `PresentationConfig` e `OpeningRenderer` não têm suporte multilingual:
- `presentation_config.py:26-76` — sem campo `language` ou `font_file`
- `opening_renderer.py:29` — `_FONT_FILE = DejaVuSans-Bold.ttf` hardcoded
- `opening_renderer.py:205-215` — prompt PT para title shortening (com typo "Mantenho" → "Mantenha")
- `opening_renderer.py:326-333` — `_wrap_text` quebra por spaces (mesmo problema do subtitle_renderer)
- `opening_renderer.py:304` — `x_expr = "(w-text_w)/2"` hardcoded center (mesmo problema do text_align)

**Solução:**
1. Adicionar `font_file` ao `PresentationConfig` (override por idioma)
2. Localizar prompt de title shortening via `gpcg/i18n/prompts/{lang}/presentation.py`
3. Corrigir typo "Mantenho" → "Mantenha" no prompt PT
4. `_wrap_text` do opening_renderer: mesma solução do subtitle_renderer (preservar \n, CJK-aware)
5. Considerar usar `textfile=` em vez de `text=` no drawtext (já faz isso — confirmado)

### 3.11 Backend — YouTube Publishing (DIFICULDADE: BAIXA)

```python
# Atual — google_integration_adapter.py
body = {
    "userId": uid,
    "videoPath": remote_path,
    "title": title,
    "description": description,
    "tags": all_tags,
    "categoryId": cat,
    "privacy": priv,
}

# Proposto — adicionar defaultLanguage
body = {
    ...
    "defaultLanguage": target_language,  # "pt-BR", "en", etc.
}
```

> **Verificar:** O google-integration service aceita `defaultLanguage`? Precisa ser confirmado. Se não aceitar, adicionar no adapter do google-integration.

### 3.12 Backend — Content Collection (DIFICULDADE: MÉDIA)

#### 3.12.1 RSS feeds

Feeds atuais são todos em inglês (IGN, GameSpot, Polygon, etc.). Para coletar conteúdo em outros idiomas:

1. Adicionar feeds por locale (ex: para PT-BR, feeds brasileiros; para ES, feeds espanhóis)
2. O `gpcg_rss_feed_url` já usa Google News — basta mudar `hl`/`gl`
3. Detectar idioma do conteúdo coletado com `langdetect` ou `fasttext-langid`
4. Armazenar em `KnowledgeItem.source_language`

#### 3.12.2 Detecção de idioma

```python
# Nova dependência: langdetect ou fasttext-langid
from langdetect import detect

def detect_language(text: str) -> str:
    try:
        return detect(text)
    except:
        return "unknown"
```

Adicionar à pipeline de ingestão: quando um `KnowledgeItem` é criado, detectar e armazenar `source_language`.

### 3.13 Backend — Wikipedia / Game Enrichment (DIFICULDADE: BAIXA)

```python
# Atual — game_enrichment.py:239-245
f"Você é um roteirista de vídeos sobre games. Resuma a história e o lore "
f"do jogo '{game_name}' em português, em no máximo 3 parágrafos."

# Proposto
f"You are a scriptwriter for gaming videos. Summarize the story and lore "
f"of the game '{game_name}' in {language_name(target_language)}, "
f"in at most 3 paragraphs."
```

O `WikipediaClient` deve buscar artigos no idioma alvo quando disponível:
```python
article = wikipedia_client.fetch(game_name, lang=target_language_short)
```

#### 3.13.1 Game names — localização no catálogo (revisão 0.11)

**Problema:** O plano v1 não resolveu nomes de jogos localizados.
- `catalog/models.py:56` — `CatalogGame.name` do IGDB (canônico inglês), sem coluna de locale
- `games/models.py:141` — `Game.canonical_name` sem localização
- Prompts usam `game_name` sem especificar forma localizada

**Solução:**
1. Adicionar `CatalogGameLocalizedName` (tabela separada: `catalog_game_id`, `locale`, `name`)
2. IGDB fornece `alternative_names` — importar com locale hints quando disponível
3. `Game` pode ter `localized_names: dict[str, str]` no `metadata_json` (ex: `{"pt-BR": "Crash Team Racing", "en": "Crash Team Racing"}`)
4. Prompts e TTS usam o nome localizado quando disponível, fallback para `canonical_name`
5. Documentar que nomes canônicos em inglês são o safe default (já é a política do `ARCHITECTURE_V2_READINESS_REVIEW.md`)

### 3.14 Frontend — Web (DIFICULDADE: MÉDIA-ALTA)

#### 3.14.1 Escala

- **~229 strings PT** identificadas (lower bound)
- **~788 ocorrências de caracteres acentuados** (upper bound)
- **13 páginas** com strings hardcoded
- **0 infraestrutura i18n**

#### 3.14.2 Biblioteca

```bash
cd frontend && npm install react-i18next i18next
```

#### 3.14.3 Estrutura de arquivos

```
frontend/src/
  i18n/
    index.ts              # config do i18next
    locales/
      pt-BR/
        translation.json  # todas as strings
      en/
        translation.json
  lib/
    utils.ts              # fmtDate usa i18n.language
```

#### 3.14.4 Arquivos com mais strings (prioridade)

| Arquivo | Strings | Prioridade |
|---------|---------|------------|
| `pages/ideas.tsx` | 34 | Alta |
| `pages/kids.tsx` | 29 | Alta |
| `pages/content.tsx` | 19 | Alta |
| `pages/automation.tsx` | 19 | Alta |
| `pages/dashboard.tsx` | 20 | Alta |
| `pages/videos.tsx` | 20 | Alta |
| `pages/kids-ideas.tsx` | 22 | Alta |
| `components/ErrorBoundary.tsx` | 15 | Média |
| `lib/api.ts` | 20 | Média |
| `lib/domain-config.tsx` | 8 | Alta (nav labels) |
| `components/onboarding-tour.tsx` | 4 | Média |

#### 3.14.5 Date formatting

```ts
// Atual
return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", ... });

// Proposto
import { i18n } from '../i18n';
return d.toLocaleDateString(i18n.language, { day: "2-digit", month: "2-digit", ... });
```

#### 3.14.6 Domain config

```ts
// Atual — domain-config.tsx
navigation: [
  { to: "/dashboard", label: "Dashboard", icon: "LayoutDashboard" },
  { to: "/content", label: "Conteúdo", icon: "FileText" },
  ...
]

// Proposto
navigation: [
  { to: "/dashboard", labelKey: "nav.dashboard", icon: "LayoutDashboard" },
  { to: "/content", labelKey: "nav.content", icon: "FileText" },
  ...
]
// No componente: t(item.labelKey)
```

#### 3.14.7 Language toggle

#### 3.14.8 STAGE_LABELS duplicados em frontend/mobile (revisão 0.23)

**Problema:** Frontend e mobile têm suas próprias cópias de `STAGE_LABELS` em PT:
- `frontend/src/pages/jobs.tsx:32-52` — `STAGE_LABELS` dict em PT
- `mobile/src/screens/IdeasScreen.tsx:69-84` — `STAGE_LABELS` dict em PT
- `mobile/src/screens/JobsScreen.tsx:38+` — `STAGE_LABELS` dict em PT

**Impacto:** Mesmo que o backend envie stable keys (seção 3.3.2), o frontend tem
fallback PT hardcoded. Precisa remover as cópias duplicadas e usar apenas o i18n layer.

**Solução:**
1. Remover `STAGE_LABELS` dicts hardcoded de frontend e mobile
2. Usar `t('stage.' + stage)` via i18next/react-i18next
3. Se o backend enviar `stage_label` (legacy), ignorar em favor do i18n local
4. Adicionar translations para todos os stages em `frontend/src/i18n/locales/{lang}.json`

Adicionar no user dropdown (`layout.tsx`):
```tsx
<button onClick={() => i18n.changeLanguage('en')}>English</button>
<button onClick={() => i18n.changeLanguage('pt-BR')}>Português</button>
```

Persistir em `localStorage` e enviar para o backend via `PUT /api/auth/me` (atualiza `User.ui_language`).

### 3.15 Mobile (DIFICULDADE: MÉDIA-ALTA)

#### 3.15.1 Escala

- **~218 strings PT** identificadas
- **~544 ocorrências de caracteres acentuados**
- **11 screens** com strings hardcoded
- **0 infraestrutura i18n**

#### 3.15.2 Biblioteca

```bash
cd mobile && npm install react-i18next i18next react-native-localize
```

#### 3.15.3 Estrutura

```
mobile/src/
  i18n/
    index.ts
    locales/
      pt-BR/
        translation.json
      en/
        translation.json
```

#### 3.15.4 Arquivos com mais strings (prioridade)

| Arquivo | Strings |
|---------|---------|
| `screens/KidsScreen.tsx` | 33 |
| `screens/VideosScreen.tsx` | 27 |
| `screens/IdeasScreen.tsx` | 24 |
| `screens/ContentScreen.tsx` | 20 |
| `screens/KidsIdeasScreen.tsx` | 20 |
| `screens/LoginScreen.tsx` | 19 |
| `screens/DashboardScreen.tsx` | 18 |
| `screens/AutomationScreen.tsx` | 15 |
| `screens/MoreScreen.tsx` | 13 |
| `components/OnboardingModal.tsx` | ~15 |

#### 3.15.5 Date formatting

```ts
// Atual — mobile/src/utils/format.ts
return d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' });

// Proposto
import { i18n } from '../i18n';
return d.toLocaleDateString(i18n.language, { day: '2-digit', month: '2-digit' });
```

Termos relativos (`agora`, `ontem`, `min`, `h`, `d`, `sem`) → translation keys.

#### 3.15.6 Language toggle

Adicionar em `MoreScreen.tsx`:
```tsx
<TouchableOpacity onPress={() => i18n.changeLanguage('en')}>
  <Text>English</Text>
</TouchableOpacity>
```

Persistir com `AsyncStorage` e sincronizar com backend.

### 3.16 Worker / Compute Plane (DIFICULDADE: MÉDIA)

#### 3.16.1 Remote worker

O worker é um "dumb executor" — não tem lógica de idioma própria. Ele recebe `job_data` do VPS (via `GET /api/jobs/{job_id}/data`), que já incluirá `target_language` no `channel_profile` replicado.

**Mudanças necessárias:**
1. `remote_worker.py` — sem mudança direta (já busca job_data)
2. `local_db_sync.py:590-604` — adicionar `target_language=profile_data.get("target_language", "pt-BR")` ao replicar `ChannelProfile`
3. `worker/handlers/generation.py` — sem mudança (já chama `run_generation_locally`)
4. `worker/handlers/mapping.py` — sem mudança (VLM mantém EN)
5. Status messages em PT (`"Processando mídia Kids..."`) → substituir por stable keys (ver 3.3.2)

**Checkpoint/resume (revisão 0.15):**
O worker já tem checkpoints em `_has_checkpoint` (`generation_service.py:1705-1722`) para:
`content_plan_id`, `script_id`, `narration_wav`, `selected_clips`, `video_path`.

**Risco:** Worker crasha → usuário muda idioma do canal → worker reinicia → job resume →
usa o **novo** idioma em vez do idioma original → script em idioma X, TTS em idioma Y.

**Solução:** O `target_language` DEVE ser lido do `config_snapshot` (snapshot congelado no enqueue),
não do `ChannelProfile` live, ao resumir:
```python
# generation_service.py — ao resumir
config_snapshot = job.artifacts.get("config_snapshot") or {}
target_language = config_snapshot.get("language") or channel_profile.target_language or "pt-BR"
```
Isso garante que um job sempre completa no idioma em que começou, mesmo se o canal mudar.

#### 3.16.2 GenerationService

```python
# Atual
def generate(self, session, job_id, ...):
    # sem language param
    ...

# Proposto
def generate(self, session, job_id, ..., language: str = "pt-BR"):
    target_language = language
    # passa para ScriptService, MetadataGenerator, etc.
```

---

## 4. Decisões de Design

### 4.1 LLM: gerar no idioma alvo ou traduzir depois?

**Decisão: Gerar diretamente no idioma alvo.**

**Justificativa:**
- Os modelos (Qwen3, Gemma3) suportam múltiplos idiomas nativamente
- Gerar diretamente produz texto mais natural que traduzir
- Tradução pós-geração adiciona latência e pode introduzir erros
- O anti-plagiarismo já compara contra fontes — se a fonte é EN e o script é EN, o overlap pode ser maior, mas o `originality.py` já tem threshold configurável

**Trade-off:** Se o LLM gerar em EN a partir de fontes EN, o risco de plágio aumenta. Mitigação: o prompt já instrui a reescrever em próprias palavras, e o `originality.py` faz check n-gram. Ajustar threshold por idioma se necessário.

### 4.2 VLM: em que idioma descrever eventos?

**Decisão: Manter VLM em inglês. Usar embeddings multilinguais para casamento semântico.**

**Justificativa:**
- VLMs são mais confiáveis em inglês para análise visual
- `nomic-embed-text` suporta múltiplos idiomas — embeddings de texto PT e EN podem ser comparados via cosine similarity
- Mudar o VLM para PT pode reduzir a qualidade da análise visual
- O `gameplay_query` do editorial planner pode ser traduzido para EN antes da busca, ou a busca pode usar embeddings diretamente

### 4.3 ASR: forçar idioma ou auto-detect?

**Decisão: Auto-detect para gameplay audio. Forçar idioma para narração TTS (se ASR for usado para validar TTS).**

**Justificativa:**
- Gameplay audio pode estar em qualquer idioma (cutscenes em EN, gameplay sem áudio, etc.)
- A narração TTS é sempre no `target_language` — se o ASR for usado para validar/corrigir TTS, deve forçar o idioma

### 4.4 UI language vs Content language

**Decisão: Separados.**

- `User.ui_language` — idioma da interface (web + mobile)
- `ChannelProfile.target_language` — idioma do conteúdo gerado
- O usuário pode ter UI em EN e gerar conteúdo em PT
- O toggle de UI language é independente do toggle de content language

### 4.5 Fallback quando não há tradução

**Decisão: Fallback para pt-BR.**

Se um prompt/heuristic/string não existir para o idioma solicitado, usar pt-BR como fallback. Isso garante que o sistema nunca quebra por falta de tradução.

---

## 5. Plano de Implementação por Fases

### Fase 1 — Fundação (semana 1-2)

**Objetivo:** Deixar a arquitetura pronta para i18n sem mudar comportamento.

1. **DB schema:** Adicionar colunas de language (`User.ui_language`, `ChannelProfile.target_language`, `ContentPlan.target_language`, `Script.language`, `Video.language`, `KnowledgeItem.source_language`)
2. **Config:** Adicionar `gpcg_default_language`, `gpcg_asr_language`, `gpcg_rss_language`, `gpcg_rss_country`
3. **Prompt templates:** Refatorar `domains/games/prompts.py` e `domains/kids/prompts.py` para usar `{target_language}` variable
4. **i18n module:** Criar `gpcg/i18n/` com `prompts/base.py`, `api_messages.py`, `heuristics/`
5. **Service layer:** Adicionar `language` param a todos os serviços (default `"pt-BR"`)
6. **GenerationService:** Ler `target_language` de `job.artifacts` e propagar
7. **Worker:** Passar `target_language` do job para GenerationService
8. **Testes:** Garantir que todos os testes existentes passam (behavior unchanged)

### Fase 2 — UI i18n (semana 3-4)

**Objetivo:** Interface web e mobile pode ser trocada de idioma.

1. **Web:** Instalar react-i18next, criar `locales/pt-BR/translation.json`, extrair strings
2. **Mobile:** Instalar react-i18next + react-native-localize, criar locales, extrair strings
3. **Backend API:** Criar `i18n/api_messages.py`, substituir strings hardcoded
4. **Language toggle:** Web (user dropdown) + Mobile (MoreScreen)
5. **Date formatting:** Usar `i18n.language` em vez de `"pt-BR"` hardcoded
6. **Testes:** Frontend typecheck, mobile typecheck, testes de API

### Fase 3 — Content i18n (semana 5-6)

**Objetivo:** Gerar conteúdo em outro idioma além de PT-BR.

1. **Prompt packs:** Criar `gpcg/i18n/prompts/en/` (primeiro idioma além de PT-BR)
2. **Heuristics packs:** Criar `gpcg/i18n/heuristics/en/ai_isms.py`, `stopwords.py`, etc.
3. **TTS:** Passar `language` per-job para o adapter e video-generate
4. **ASR:** Passar `language` hint quando apropriado
5. **Metadata:** Localizar `MetadataGenerator` e fallbacks (`"Gameplay Curiosidade"` → por idioma)
6. **YouTube:** Adicionar `defaultLanguage` ao upload
7. **Content collection:** RSS feeds por locale, detecção de idioma
8. **Testes E2E:** Gerar um vídeo em EN de ponta a ponta

### Fase 4 — Polimento (semana 7-8)

1. **Vozes por idioma:** UI para upload/ seleção de voz por idioma
2. **Subtitle fonts:** Mapa de fontes por idioma (se necessário)
3. **Wikipedia:** Buscar artigos no idioma alvo
4. **Game enrichment:** Localizar prompt de lore summary
5. **Editorial strategy:** Localizar prompts
6. **Creative engine presets:** Localizar labels e descrições
7. **Onboarding/tour:** Localizar textos
8. **Testes completos:** Suite completa em PT-BR + smoke test em EN

### Fase futura — CJK / RTL

1. **Fontes CJK:** Noto Sans CJK para ja, zh-cn, ko
2. **Quebra de linha CJK:** Implementar word break por caractere
3. **RTL:** Arabic/Hebrew — switch para `ass`/`srt` burn-in em vez de `drawtext`
4. **Case transform:** Desabilitar `upper` para scripts não-Latinos
5. **TTS:** Verificar suporte XTTS para idiomas CJK/RTL
6. **Whisper:** Verificar suporte para idiomas CJK/RTL

---

## 6. Análise de Dificuldade

### 6.1 Mais fácil (baixa complexidade)

| Item | Esforço | Razão |
|------|---------|-------|
| DB schema (adicionar colunas) | 2h | `_ensure_column()` já existe |
| Config (novas env vars) | 1h | pydantic-settings, só adicionar campos |
| API messages i18n | 4h | Substituir strings, criar dict |
| RSS feed language | 1h | Mudar URL template |
| YouTube defaultLanguage | 1h | Adicionar campo no body |
| Date formatting (web + mobile) | 2h | Trocar `"pt-BR"` por `i18n.language` |
| Language toggle UI | 4h | Dropdown + AsyncStorage |

### 6.2 Média complexidade

| Item | Esforço | Razão |
|------|---------|-------|
| Prompt templates (parameterizar) | 16h | ~30 prompts, cada um precisa revisão cuidadosa |
| Service layer (adicionar language param) | 12h | ~14 serviços, cada um precisa threading do param |
| Frontend i18n (extrair 229 strings) | 24h | Trabalho mecânico mas volumoso |
| Mobile i18n (extrair 218 strings) | 20h | Mesmo |
| Heuristics por idioma (ai_isms, stopwords) | 8h | Precisa criar listas para EN |
| TTS language per-job | 4h | Adapter + video-generate |
| Content collection por locale | 6h | RSS + detecção de idioma |
| Worker propagation | 4h | artifacts → GenerationService |

### 6.3 Mais complexa (alta complexidade)

| Item | Esforço | Razão |
|------|---------|-------|
| VLM language mismatch | 8h | Decisão arquitetural + embeddings multilinguais |
| Subtitle CJK/RTL | 40h+ | FFmpeg drawtext não suporta RTL; precisa trocar para ASS |
| TTS para CJK | 20h+ | XTTS pode não suportar todos os CJK; pode precisar de modelo diferente |
| Creative engine localization | 12h | Presets têm humor/cultura embarcada; `description` é prompt, não só label (revisão 0.10) |
| Humanization heuristics por idioma | 16h | AI-isms são culturais; precisa de nativo para cada idioma |
| **TextNormalizer locale packs (revisão 0.5)** | 16h | Números, moedas, datas, abreviações por idioma — cada idioma é um módulo |
| **Originality cross-lingual (revisão 0.7)** | 12h | Precisa de similaridade semântica ou tradução de sources antes do check |
| **Embedding re-embedding strategy (revisão 0.8)** | 8h | Coluna de language + trigger de re-embedding + migração de existentes |
| **subtitle_mapping segments (revisão 0.4)** | 8h | Adapter precisa produzir segments OU generate_auto_srt precisa aceitar original_text |
| **Game names localization (revisão 0.11)** | 8h | Tabela de nomes localizados + integração com IGDB alternative_names |

---

## 7. Dependências Externas

### 7.1 Novas bibliotecas Python

| Biblioteca | Uso | Necessária? |
|------------|-----|-------------|
| `langdetect` | Detectar idioma do conteúdo coletado | Fase 3 |
| `python-bidi` | RTL text shaping (fase futura) | Fase futura |
| `arabic-reshaper` | Arabic text shaping (fase futura) | Fase futura |

### 7.2 Novas bibliotecas JS

| Biblioteca | Uso | Necessária? |
|------------|-----|-------------|
| `react-i18next` | i18n web | Fase 2 |
| `i18next` | i18n core | Fase 2 |
| `react-native-localize` | i18n mobile | Fase 2 |

### 7.3 Fontes do sistema

| Fonte | Idiomas | Necessária? |
|-------|---------|-------------|
| DejaVuSans-Bold.ttf | Latim (PT, EN, ES, FR) | ✅ Já instalada |
| Noto Sans CJK | ja, zh-cn, ko | Fase futura |
| Noto Naskh Arabic | ar | Fase futura |

### 7.4 Modelos

| Modelo | Multilinguagem | Observação |
|--------|----------------|------------|
| Qwen3 14B | ✅ 100+ idiomas | Creative engine |
| Gemma3 12B | ✅ 140+ idiomas | VLM, editorial |
| LLaMA 3.1 8B | ✅ limitado | Script, metadata |
| Whisper large-v3 | ✅ 99 idiomas | ASR |
| XTTS v2 | ✅ 15 idiomas | TTS: pt,en,es,fr,de,it,pl,tr,ru,nl,cs,ar,zh-cn,ja,hu,ko |
| nomic-embed-text | ✅ multilingue | Embeddings |

**Conclusão:** Todos os modelos já suportam múltiplos idiomas. A limitação está nos prompts, não nos modelos.

---

## 8. Riscos e Mitigações

| Risco | Impacto | Mitigação |
|-------|---------|-----------|
| LLM gera em idioma errado | Alto | Prompt instruction explícita + validação pós-geração (langdetect) |
| TTS com voz errada | Médio | Validar compatibilidade voice↔language antes de sintetizar |
| Anti-plagiarismo falha em EN | Médio | Threshold de n-gram por idioma (EN tem mais overlap natural) |
| Humanização remove texto legítimo | Médio | AI-isms são culturais; desabilitar se não houver lista para o idioma |
| Subtitles com caracteres errados | Alto (CJK) | Validar fonte antes de renderizar; fallback para DejaVuSans |
| RSS coleta conteúdo em idioma errado | Baixo | Detectar idioma e filtrar |
| VLM descriptions em EN vs query em PT | Médio | Embeddings multilinguais ou traduzir query |
| Quebra de testes existentes | Alto | Todos os defaults são `pt-BR`; testes não mudam |

---

## 9. Testes

### 9.1 Testes de regressão (Fase 1)

- Todos os 1048 testes existentes devem passar sem mudança
- Adicionar teste que verifica `target_language` default = `"pt-BR"` em todos os modelos
- Adicionar teste que verifica prompt templates com `lang="pt-BR"` produzem o mesmo output que os prompts hardcoded atuais
- **Atenção (revisão 0.7):** `tests/test_originality.py:157` usa frases em PT como fixture — não quebra, mas testes com fixtures PT hardcoded (`test_kids_domain.py`, `test_kids_idea_system.py` com `"Você sabia que o polvo possui três corações?"`) precisarão ser atualizados quando o pipeline começar a retornar EN/ES/FR. Nenhum teste atual asserta `pt-BR` como idioma de output do LLM (grep retornou 0 matches), então a parameterização de prompts não quebra assertions explícitas.

### 9.2 Testes de i18n (Fase 2-3)

- Teste que `get_prompt("games/draft_system", "en")` retorna prompt com instruction em EN
- Teste que `get_prompt("games/draft_system", "xx")` faz fallback para pt-BR
- Teste que `get_message("file_already_sent", "en")` retorna string em EN
- Teste que `GenerationService` propaga `target_language` para todos os serviços
- Teste E2E: gerar vídeo em EN de ponta a ponta (script, TTS, metadata, subtitles)

### 9.3 Teste de regressão de prompts

```python
def test_pt_br_prompts_unchanged():
    """Garante que refatorar prompts para templates não muda o output pt-BR."""
    from gpcg.i18n import get_prompt
    # Comparar get_prompt("games/draft_system", "pt-BR") com o DRAFT_SYSTEM original
    # (antes da refatoração, salvar snapshot)
```

---

## 10. Fase Futura — CJK e RTL

### 10.1 CJK (Chinês, Japonês, Coreano)

**Desafios:**
- Quebra de linha: CJK não usa espaços entre caracteres
- Fontes: DejaVuSans não tem glifos CJK
- Case transform: `upper` não se aplica
- TTS: XTTS suporta `zh-cn`, `ja`, `ko` mas qualidade não validada
- Chars per second: CJK é mais denso que Latim

**Soluções:**
- `wrap_text()`: adicionar quebra por caractere quando `language in {"zh-cn", "ja", "ko"}`
- Fonte: Noto Sans CJK (instalar no sistema)
- `case_transform`: desabilitar para CJK
- TTS: testar qualidade e ajustar `chars_per_second`

### 10.2 RTL (Árabe, Hebraico)

**Desafios:**
- FFmpeg `drawtext` não suporta RTL/bidi
- Texto precisa ser reshaped antes de renderizar
- Posicionamento espelhado

**Soluções:**
- Trocar `drawtext` por `subtitles=file.ass` (libass suporta RTL)
- Usar `python-bidi` + `arabic-reshaper` para pré-processar texto
- Fonte: Noto Naskh Arabic
- `text_align`: implementar `right` para RTL

---

## 11. Resumo Executivo

| Métrica | Valor |
|---------|-------|
| Prompts LLM a parameterizar | ~30 |
| Strings de API a localizar | ~30 |
| Strings de SSE/events a converter para keys | ~20 (revisão 0.9) + 16+ worker handlers (revisão 0.24) |
| Strings de UI web a extrair | ~229+ + STAGE_LABELS duplicado (revisão 0.23) |
| Strings de UI mobile a extrair | ~218+ + STAGE_LABELS duplicado (revisão 0.23) |
| Serviços a modificar | ~14 + QAService + RenderPlanBuilder (revisão 0.26, 0.27) |
| Colunas de DB a adicionar | ~8 + 2 em embedding tables (revisão 0.8) |
| Env vars a adicionar | ~4 |
| Arquivos de video-generate a modificar | ~10 (revisão 0.2-0.6) + TextDualProcessor (revisão 0.20) |
| Componentes PT-BR monolíticos em video-generate | TextNormalizer, AcronymHandler, TextDualProcessor (revisão 0.20) |
| Presentation Layer issues | font hardcoded, prompt PT, wrap_text (revisão 0.25) |
| Subtitle issues | \n descartado, escaping incompleto, SRT sem lang tag (revisão 0.21, 0.22) |
| Novas bibliotecas Python | 1 (langdetect) |
| Novas bibliotecas JS | 3 (react-i18next, i18next, react-native-localize) |
| Modelos que já suportam multilinguagem | Todos (Qwen3, Gemma3, LLaMA, Whisper, XTTS, nomic-embed) |
| **Furos encontrados na revisão v2** | **16** (2 críticos, 9 omissões, 5 erros) |
| **Falsos positivos corrigidos na v3** | **3** (0.17, 0.18, 0.19) |
| **Novos pontos mapeados na v3** | **12** (0.20-0.28) |
| Esforço total estimado (Fases 1-4) | ~170h (revisado de ~155h) |
| Esforço Fase 1 (fundação) | ~40h |
| Esforço Fase 2 (UI) | ~55h |
| Esforço Fase 3 (content) | ~45h |
| Esforço Fase 4 (polimento) | ~30h |

**Conclusão:** A revisão v3 corrigiu 3 falsos positivos da v2 (generate_media.py não é
entry point do GPCG; TextNormalizer não é chamado no fluxo synthesize() do GPCG;
AcronymHandler pode ser desabilitado) e mapeou 12 novos pontos: TextDualProcessor
monolítico, wrap_text descarta \n + escaping incompleto, SRT sem language tags,
STAGE_LABELS duplicados em frontend/mobile, 14+ strings PT adicionais em worker handlers,
PresentationConfig sem language/font, QAService sem language param, RenderPlanBuilder
sem campo language, notification fallback, e default automation name em 4 locais.
O esforço total foi revisado de ~155h para ~170h. O plano v3 é agora uma base sólida
e confiável para implementação, com mapeamento próximo a 100% das alterações necessárias.

---

## 12. Revisão v3 — Melhorias de Implementação e Refactory

Esta seção documenta recomendações de **qualidade de implementação** e **refactory**
para que o resultado final ao rodar o plano seja o melhor possível. Não são novos
pontos faltantes — são melhorias na **forma** de implementar o que já está mapeado.

### 12.1 LanguageContext — um objeto ao invés de 14 parâmetros individuais

**Problema:** O plano propõe adicionar `language` param a ~14 serviços
(ContentPlanningService, ScriptService, CreativeEngine, EditorialPlanner, MetadataGenerator,
HumanizationService, QAService, RenderPlanBuilder, etc.). Isso é um blast radius enorme —
qualquer campo futuro (prompt_version, model_preferences, locale) exige mudar 14 assinaturas novamente.

**Recomendação:** Introduzir um `LanguageContext` dataclass e passar **um objeto**:

```python
# src/gpcg/i18n/language_context.py (NOVO)
@dataclass(frozen=True)
class LanguageContext:
    language: str = "pt-BR"            # BCP-47 tag
    locale: str = "pt_BR"              # ICU/POSIX style
    tts_language: str = "pt"           # whisper/tts code
    prompt_version: str = "v1"
    model_preferences: dict = field(default_factory=dict)

    @classmethod
    def from_channel_profile(cls, profile: ChannelProfile | None) -> "LanguageContext":
        s = get_settings()
        lang = getattr(profile, "target_language", None) or s.gpcg_default_language
        return cls(language=lang, locale=lang.replace("-", "_"), ...)

    def language_directive(self) -> str:
        return f"Idioma do conteúdo: {self.language}\nUse o locale {self.locale}..."
```

**Mudança nas assinaturas:**
```python
# Antes (plano v3): 14 serviços com param language
def generate_script(..., language: str = "pt-BR", ...): ...

# Depois: 14 serviços com param language_context
def generate_script(..., language_context: LanguageContext, ...): ...
```

**Benefício:** Adicionar `prompt_version` ou `currency_format` depois = editar
`LanguageContext`, não 14 `def` lines.

**Canal de contexto já existe:** `ChannelProfile.to_prompt_context()` e `to_stage_context()`
(`models.py:680-738`) já passam contexto para prompts, mas com **labels em PT hardcoded**
("Descrição do canal:", "Nicho:", "Público-alvo:"). Esses labels também precisam ser
localizados via `LanguageContext`:

```python
def to_prompt_context(self, language_context: LanguageContext) -> str:
    parts = [language_context.language_directive()]
    label = lambda k: i18n_label(k, language_context.language)
    if self.channel_description:
        parts.append(f"{label('channel_description')}: {self.channel_description}")
```

### 12.2 PromptRegistry — versionamento, cache, e A/B testing

**Problema:** O plano propõe `gpcg/i18n/prompts/{language}/games_prompts.py` com fallback.
Mas prompts são `str` constants sem versionamento, cache, schema, ou A/B testing.
São **28 prompts** total (14 games + 14 kids).

**Recomendação:** Manter Python constants (não migrar para YAML agora) mas envolvê-las
em um `PromptRegistry` com lazy loading + fallback + versioned cache:

```python
# src/gpcg/i18n/prompts/registry.py (NOVO)
@dataclass(frozen=True)
class PromptTemplate:
    name: str
    text: str
    language: str
    version: str
    output_schema: dict

class PromptRegistry:
    _cache: dict[str, PromptTemplate] = {}

    @classmethod
    def get(cls, name: str, domain: str = "games",
            language: str = "pt-BR", version: str = "v1") -> PromptTemplate:
        key = f"{domain}:{name}:{language}:{version}"
        if key not in cls._cache:
            try:
                module = importlib.import_module(
                    f"gpcg.i18n.prompts.{language.lower().replace('-', '_')}.{domain}_prompts"
                )
            except ModuleNotFoundError:
                module = importlib.import_module(f"gpcg.i18n.prompts.pt_br.{domain}_prompts")
            cls._cache[key] = PromptTemplate(
                name=name, text=getattr(module, name),
                language=language, version=version,
                output_schema=getattr(module, f"{name}_SCHEMA", {"required": ["script"]}),
            )
        return cls._cache[key]

    @classmethod
    def version_hash(cls, template: PromptTemplate) -> str:
        return hashlib.sha1(
            f"{template.name}:{template.language}:{template.version}:{template.text}".encode()
        ).hexdigest()[:12]
```

**A/B testing:** `LanguageContext.prompt_version` + `PromptRegistry.version_hash()` permitem
armazenar o fingerprint exato do prompt em `job.artifacts["prompt_fingerprint"]`.

**Estrutura de arquivos:**
```
src/gpcg/i18n/prompts/
├── registry.py
├── pt_br/
│   ├── games_prompts.py  (movido de gpcg/domains/games/prompts.py)
│   └── kids_prompts.py
└── en_us/
    ├── games_prompts.py
    └── kids_prompts.py
```

### 12.3 GenerationContext para checkpoints — não apenas language

**Problema:** O plano diz para snapshot `target_language` em `config_snapshot`. Mas
checkpoint safety precisa de mais que language — precisa de prompt_version, model version,
TTS engine version. Se qualquer um mudar, o checkpoint deve ser invalidado.

**Recomendação:** Estender `LanguageContext` para `GenerationContext` e validar
compatibilidade em `_has_checkpoint`:

```python
@dataclass(frozen=True)
class GenerationContext(LanguageContext):
    tts_engine_version: str = "xtts-v2"
    llm_script_model: str = "llama3.1:8b"
```

```python
# generation_service.py:1705 — _has_checkpoint context-aware
def _has_checkpoint(self, job_id, key, file_check=False, *, gen_ctx=None) -> bool:
    val = self._get_artifact(job_id, key)
    if val is None:
        return False
    stored = self._get_artifact(job_id, "generation_context") or {}
    if gen_ctx:
        if stored.get("language") != gen_ctx.language:
            log.info(f"checkpoint {key}: language changed {stored.get('language')}→{gen_ctx.language}")
            return False
        if stored.get("prompt_version") != gen_ctx.prompt_version:
            log.info(f"checkpoint {key}: prompt version changed")
            return False
    # existing file_check...
    return True
```

### 12.4 Feature flag strategy — kill switch instantâneo

**Problema:** O plano menciona feature flags mas não detalha. Há 19 flags `gpcg_*_enabled`
em `config.py` seguindo o mesmo padrão.

**Recomendação:** Seguir o padrão existente com hierarquia:

```python
# config.py
gpcg_multilingual_enabled: bool = False           # kill switch master
gpcg_multilingual_languages: list[str] = ["pt-BR"] # allowlist de idiomas
gpcg_multilingual_tts_enabled: bool = False        # TTS multilingual
gpcg_multilingual_prompts_enabled: bool = False    # prompts multilingual
gpcg_multilingual_beta_users: list[int] = []       # user_id allowlist
```

**Rollout order:**
1. `gpcg_multilingual_enabled = False` → sem mudança, todo `LanguageContext` = pt-BR
2. `= True`, `languages = ["pt-BR", "en-US"]` → enable para idiomas allowlisted
3. `beta_users = [1, 42]` → narrow para usuários específicos
4. Per-capability flags permitem desligar TTS EN mantendo prompts EN

**Gating em `LanguageContext.from_channel_profile`:**
```python
if not s.gpcg_multilingual_enabled:
    return cls()  # pt-BR default
if requested not in s.gpcg_multilingual_languages:
    return cls(language="pt-BR")  # safe fallback
```

**Rollback instantâneo:** `gpcg_multilingual_enabled = False` → sistema volta a pt-BR.
Jobs em flight mantêm seu snapshot.

### 12.5 Subprocess boundary — VideoGenerateRequest dataclass tipado

**Problema:** GPCG chama video-generate via subprocess com `request_data` como raw dict.
O contract é informal — adicionar `language` exige adicionar key em dois dicts em dois
repositórios e torcer para `process_video_request` ler.

**Recomendação:** Definir um `VideoGenerateRequest` dataclass tipado em video-generate:

```python
# video-generate/src/transport/request_schema.py (NOVO)
@dataclass
class SubtitleSegment:
    start_time: float
    end_time: float
    tts_fragment: str
    subtitle_fragment: str

@dataclass
class SubtitleMapping:
    original_text: str
    tts_text: str
    subtitle_text: str
    language: str
    segments: list[SubtitleSegment]

@dataclass
class VideoGenerateRequest:
    audio_principal: str
    musica_fundo: str | None
    delay_musica: float
    img_dir: str
    original_narration_text: str
    subtitle_mapping: SubtitleMapping | None
    scene_timeline: list[dict]
    request_id: int
    batch_id: str
    video_profile: str
    language: str = "pt"
    subtitle_font_file: str | None = None
```

`process_video_request` aceita `dict | VideoGenerateRequest` e converte.
`RenderPlanBuilder` constrói o dataclass. Adapter serializa para JSON.

### 12.6 GPCG adapter deve produzir segments — eliminar Whisper fallback

**Problema:** O plano oferece 2 opções para o subtitle_mapping. A opção 2 (generate_auto_srt
aceita original_text + language) ainda depende de Whisper como safety net.

**Recomendação:** **Opção 1 é a correta** — GPCG adapter produz `segments`.
O adapter já chunks o texto e chama `synthesize()` por chunk. Ele sabe os chunks e
suas durações reais. Só precisa probe das durações e construir segments:

```python
# Após synthesize() de cada chunk, probe duração real
chunk_durations = [probe_duration(w) for w in chunk_wavs]
start = 0.0
segments = []
for chunk, dur in zip(chunks, chunk_durations):
    segments.append({
        "start_time": start,
        "end_time": start + dur,
        "tts_fragment": chunk,
        "subtitle_fragment": chunk,
    })
    start += dur
```

**Whisper deve ser removido do path GPCG entirely.** É caro, PT-hardcoded, e desnecessário
quando o adapter já tem timing exato. Manter `transcribe_and_align_audio()` apenas como
ferramenta standalone em `generate_media.py` (não usado pelo GPCG).

### 12.7 GPCG deve ser o dono do processamento de texto

**Problema:** video-generate tem TextNormalizer, AcronymHandler, TextDualProcessor —
todos PT-BR. O plano propõe parameterizar cada um por idioma.

**Recomendação:** **Não parameterizar video-generate.** GPCG deve pré-processar o texto
e enviar `tts_text`/`subtitle_text` prontos. video-generate é um engine de áudio/render,
não um engine de tradução/linguística.

```python
# gpcg/i18n/text_processing/pipeline.py (NOVO)
class TextProcessingPipeline:
    def __init__(self, language: str):
        self._normalizer = load_locale_pack(language)
        self._acronym_handler = load_acronym_pack(language)

    def process(self, text: str) -> ProcessedText:
        normalized = self._normalizer.normalize(text)
        expanded, expansions = self._acronym_handler.expand(normalized)
        return ProcessedText(
            tts_text=expanded,
            subtitle_text=text,  # texto original para subtitles
            expansions=expansions,
        )
```

```python
# GPCG adapter
processed = pipeline.process(script.final)
tts_result = adapter.synthesize_tts(
    processed.tts_text, output_wav,
    language=target_language,
    subtitle_text=processed.subtitle_text,
)
```

Em video-generate `synthesize()`: se `language` não é suportado por `AcronymHandler`,
**não** executar o PT `AcronymHandler`. GPCG já enviou texto pré-processado.

### 12.8 FontManager — validação de fontes em runtime

**Problema:** Fontes são dependência de sistema, não apenas config. Se a fonte não existe
em runtime, FFmpeg falha no meio do render.

**Recomendação:** Adicionar `FontManager` que valida disponibilidade antes do render:

```python
# video-generate/src/font_management/font_manager.py (NOVO)
class FontManager:
    FALLBACKS = {
        "pt": "DejaVuSans-Bold.ttf", "en": "DejaVuSans-Bold.ttf",
        "ja": "NotoSansCJK-Bold.ttc", "ar": "NotoNaskhArabic-Bold.ttf",
    }

    @classmethod
    def resolve(cls, language: str, override: str | None = None) -> Path:
        if override and Path(override).exists():
            return Path(override)
        for base in FONT_SEARCH_PATHS:
            p = base / cls.FALLBACKS.get(language, "DejaVuSans-Bold.ttf")
            if p.exists():
                return p
        raise FileNotFoundError(f"no font for language {language}")
```

Bundle fonts no Docker image. Não depender de system fonts em produção.

### 12.9 SubtitleRenderer — dois backends (drawtext + ASS)

**Problema:** drawtext é fundamentalmente limitado para RTL e complex scripts.
O plano menciona ASS/libass como futuro mas não compromete.

**Recomendação:** Implementar **two-backend SubtitleRenderer** escolhido por idioma:

```python
class SubtitleRenderer:
    @staticmethod
    def for_language(language: str) -> "BaseSubtitleRenderer":
        if language in {"ar", "he", "ja", "ko", "zh", "fa", "ur"}:
            return AssSubtitleRenderer()
        return DrawtextSubtitleRenderer()
```

- **Phase 1 (agora):** drawtext para pt/en/es/fr — fix escaping, respeitar `text_align`, preservar `\n`
- **Phase 2 (próximo):** `AssSubtitleRenderer` para Arabic, Hebrew, CJK
- **Phase 3 (depois):** Migrar Latin para ASS também para consistência

### 12.10 Frontend/Mobile — shared i18n architecture

**Problema:** O plano propõe `frontend/src/i18n/locales/` e `mobile/src/i18n/locales/`
separados — drift garantido. STAGE_LABELS, JOB_STATUS_CONFIG, JOB_TYPE_LABELS
duplicados em 3+ lugares. 27 interfaces TypeScript duplicadas entre web e mobile.

**Recomendação:**

1. **Shared locales directory:**
```
shared/locales/
  pt-BR/{common,jobs,stages,errors,dashboard,automation}.json
  en/{common,jobs,stages,errors,dashboard,automation}.json
```
Script `scripts/sync-i18n.js` copia para `frontend/public/locales/` e `mobile/src/i18n/locales/`
antes do build.

2. **i18next namespaces** (não um arquivo gigante):
```ts
i18n.init({
  defaultNS: 'common',
  ns: ['common', 'jobs', 'stages', 'errors', 'dashboard'],
  fallbackLng: 'pt-BR',
});
```

3. **date-fns** em ambos (não `toLocaleDateString` — inconsistente entre browsers e Hermes):
```ts
import { format, formatDistanceToNow } from 'date-fns';
import { ptBR, enUS } from 'date-fns/locale';
```

4. **Type-safety:** `i18next-parser` extrai keys + `i18next-resources-for-ts` gera `i18next.d.ts`
para autocomplete e TS falha em typos.

5. **CI gate:** `npm run i18n:check` verifica coverage 100% entre locales.

6. **Backend contract:** enviar `stage_key` (i18n key) + `stage_label` (PT-BR fallback):
```json
{"stage": "content_planning", "stage_key": "stages.content_planning", "stage_label": "Planejando conteúdo"}
```
Frontend usa `t(job.stage_key)`. `stage_label` deprecated em 2 release cycles.

### 12.11 Language toggle — per-user no DB, não per-session

**Problema:** O plano diz "persistir em localStorage/AsyncStorage". Mas `User.ui_language`
column não existe. `PUT /api/auth/me` não existe (só admin pode editar users).

**Recomendação:**
1. Adicionar `ui_language: Mapped[str] = mapped_column(String(10), default="pt-BR")` ao `User`
2. Adicionar `PUT /api/auth/me/ui-language` (não admin-only)
3. Precedência on load: localStorage → `user.ui_language` → device locale → `pt-BR`
4. Toggle afeta **só UI language**. Content language é `ChannelProfile.target_language` separado.

### 12.12 Refactory opportunities (mesmo sem multilingual)

**12.12.1 CreativeStyle conflation:** `name` (key), `label` (UI), `description` (prompt),
`level()` (PT adjectives) — tudo misturado. Refatorar para:
```python
@dataclass(frozen=True)
class CreativeStyle:
    name: str                    # key estável
    ui_label: dict[str, str]     # {"pt-BR": "Humor brasileiro", "en": "Humor"}
    descriptions: dict[str, str] # {"pt-BR": "...", "en": "..."}
    intensities: dict[str, dict[str, str]]  # {language: {value: label}}
```

**12.12.2 ScriptService bloat:** 639 linhas fazendo draft, optimize, rewrite, originality,
revision. Split em `ScriptDraftService`, `ScriptOptimizer`, `OriginalityGuard`.

**12.12.3 GenerationService bloat:** 1776 linhas. Introduzir `PipelineStage` protocol:
```python
class PipelineStage(Protocol):
    def run(self, job_id: int, context: GenerationContext) -> StageResult: ...
```
GenerationService vira thin orchestrator.

**12.12.4 Dead code cleanup:**
- `generate_vtt()` em video-generate — deletar (dead code confirmado)
- `generate_media.py` — formalmente deprecar, renomear para `cli_standalone.py`
- Temp files `gpcg_vg_*.py` — cleanup em startup

**12.12.5 Hardcoded values que viram LanguageContext/settings:**
- `150 wpm` (`generation_service.py:650`) → `language_context.words_per_minute`
- `14 chars/sec` (`kids/pipeline.py:461`) → dict por idioma
- `DejaVuSans` (`opening_renderer.py:29`) → `PresentationConfig.font_file`
- `text_align` ignorado em 2 lugares → implementar switch L/C/R

**12.12.6 Shared types package:** 27 interfaces TypeScript duplicadas entre
`frontend/src/lib/api.ts` e `mobile/src/api/endpoints.ts`. Criar `packages/types/`
com shared types. Long-term: OpenAPI-driven types via `openapi-typescript`.

### 12.13 Testing strategy — parametrized por idioma

**Recomendação:**
1. `tests/i18n/test_language_context.py` — parametrize `["pt-BR", "en-US", "es-MX"]`
2. `tests/i18n/test_prompt_registry.py` — fallback + schema assertions
3. `tests/application/test_language_parity.py` — mesmo job em PT e EN produz output equivalente
4. Para prompts sem LLM: assert em `PromptTemplate.text` e `output_schema`
5. Fix tests frágeis: `test_refactory_v2_editorial.py:112-122` hardcodeia 150 wpm —
   substituir por `language_context.words_per_minute`
6. Tests com fixtures PT hardcoded (`test_kids_domain.py`, `test_kids_idea_system.py`) —
   parametrize ou torne language-agnostic
