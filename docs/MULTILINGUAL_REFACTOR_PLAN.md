# Multilingual Architecture Refactor Plan — GPCG
## Revisão Crítica e Remapeamento Total (v3 — revisão PO + engenheiro mestre)

> **Status:** IMPLEMENTADO — 1144 testes passando. Pendente: deploy + verificação end-to-end.
> **Autor:** Revisor crítico (engenheiro nominado) + auditoria do engenheiro TTS/original + auditoria de infraestrutura (engenheiro mestre) + revisão PO
> **Princípio:** Geração nativa no idioma alvo desde a base. Lógicas independentes de linguagem. Cada job tratado no idioma alvo desde o primeiro estágio.
>
> **Histórico de revisão:**
> - v1: Mapeamento por 7 subagentes, plano inicial
> - v2: Auditoria de código pelo engenheiro original. Encontrados 12 problemas no plano v1 (C1-C12) que poderiam causar regressões, perda de dados, ou implementação inviável. Corrigidos.
> - v3: Auditoria de infraestrutura pelo engenheiro mestre. Encontrados ROOT CAUSE (C28 — worker sem env vars multilingual) + 4 bugs críticos adicionais (C29-C31). Revisão PO adicionou C32-C35, consolidou duplicações, dividiu fases grandes, adicionou plano de migração, rollback plan, critério en-US, e notas operacionais.
>
> **Histórico de implementação:**
> - Fase 0: CONCLUÍDA — worker env vars, .env loading, curiosity constructor, VPS/worker language sync, voice artifact handling
> - Fase 1: CONCLUÍDA — Chinese prompt density (5.5 chars/s, 280-379 chars), ChannelProfile.model_preferences, GenerationContext language-aware model selection, PromptRegistry fallback warning, prompt-pack coverage tests
> - Fase 1.5a: CONCLUÍDA — Voice table (id, user_id, filename, language, display_name, file_size, created_at), system voice seeding, upload/list/delete with persisted metadata
> - Fase 1.5b: CONCLUÍDA — _resolve_voice_path() unificado, _validate_voice_language() com auto-select override, voice_language artifact, worker voice audit log
> - Fase 2: CONCLUÍDA — PromptRegistry ativado em script_service, script_critic, editorial_planner, creative_engine, story_finder, curiosity_scorer, content_planning_service, metadata_generator, fact_service
> - Fase 3: CONCLUÍDA — fact_text traduzido in-memory, _translate_script removido do fluxo normal, CJK tokenization em originality.py, gameplay_query em inglês, ScriptCritic com duration/language bounds
> - Fase 5: CONCLUÍDA — _build_subtitle_segments removido, Whisper + SequenceAligner restaurado, CJK-aware segments_to_srt, BCP-47 normalization no video-generate
> - Fase 6a: CONCLUÍDA — YouTube defaultLanguage/defaultAudioLanguage, _normalize_bcp47(), title translation workaround removido
> - Fase 6b: CONCLUÍDA — check_script_language_consistency() em language_qa.py, pipeline integration com artifacts["language_qa"], 8 testes
> - Fase 7: CONCLUÍDA — _translate_creative_material, _translate_story_concept, _translate_creative_plan, _build_subtitle_segments, _force_translate_title removidos
> - Fase 1.5c: PENDENTE — UI web + mobile para seleção de idioma e voz
> - Verificação final: PENDENTE — deploy + job zh-CN end-to-end + job en-US + regressão pt-BR

---

## PARTE 0 — CORREÇÕES DO PLANO v1 (auditoria do engenheiro original)

> Esta seção documenta os 12 problemas encontrados no plano v1 e como foram corrigidos. Cada item é um erro do plano v1 que poderia causar regressão, perda de dados, ou implementação inviável.

### C1. Tradução de Fact é PERIGOSA — Fact é conteúdo compartilhado

**Plano v1 dizia:** "Fact é traduzido early (se não-pt-BR) antes de entrar no pipeline"

**Realidade:** `Fact` tem `is_public` e `user_id=NULL` para fatos do pool do sistema. `KnowledgeItem` também é compartilhado. Traduzir e persistir um Fact em zh-CN corromperia o pool para TODOS os usuários. Fact.claim e KnowledgeItem.content são armazenados no idioma original de extração (pt-BR via `FACT_EXTRACTOR_SYSTEM`).

**Correção:** A tradução do fact é **em memória apenas**, nunca persistida. O pipeline recebe `fact_text` já traduzido como parâmetro, mas `Fact.claim` no DB permanece no idioma original. Criar um campo `Fact.source_language` para auditoria (opcional, fase posterior).

### C2. Humanization é profundamente pt-BR — não é só "habilitar"

**Plano v1 dizia:** "Humanization funciona para qualquer idioma"

**Realidade:** O `Humanizer` tem:
- 13 regex patterns `AI_ISM_PATTERNS` hardcoded em pt-BR ("você não vai acreditar", "prepare-se para", etc.)
- 6 regex patterns `REDUNDANCY_PATTERNS` em pt-BR ("ou seja", "em outras palavras", etc.)
- 5 frases `IGNORANCE_IDENTIFICATION_PHRASES` em pt-BR
- `HUMANIZATION_SYSTEM` prompt inteiramente em pt-BR (228-276)

Fazer isso funcionar para zh-CN requer: patterns CJK de AI-ism, prompt CJK, frases CJK de identificação. Isso é **trabalho significativo**, não um flag.

**Correção:** Humanization multilíngue é movida para Fase 6 (não Fase 3). Para CJK no curto prazo, o estágio é mantido desabilitado (como hoje) mas com log explícito. A implementação CJK de humanization requer pesquisa de patterns de AI-ism em chinês e é um subprojeto separado.

### C3. Originality checker quebra para CJK — `_tokenize` usa `text.split()`

**Plano v1 dizia:** "check_originality roda no idioma final"

**Realidade:** `originality.py:89-91`:
```python
def _tokenize(text: str) -> list[str]:
    return text.split() if text else []
```

Para chinês sem espaços, `text.split()` retorna **uma única string** (o texto inteiro). N-grams de tamanho 5 sobre 1 token = 0 n-grams. `check_originality` retorna score=100 (sem overlap) **independentemente do conteúdo**. O checker já recebe `language` mas `_tokenize` ignora.

**Correção:** Adicionar tokenização CJK em `originality.py` antes de remover tradução tardia. Para CJK, tokenizar por caractere (cada caractere = 1 token) ou por bigrama. Ajustar n-gram size para CJK (n=2 ou n=3 caracteres ao invés de n=5 palavras).

### C4. Kids pipeline é um GenerationService separado e paralelo

**Plano v1 dizia:** "Kids pipeline não tem NENHUMA adaptação de idioma" como se fosse um fix simples

**Realidade:** `KidsGenerationService` em `domains/kids/pipeline.py` (663 linhas) é um pipeline **completamente separado** do `GenerationService`. Não herda, não compartilha código. Tem seus próprios estágios, sem `gen_ctx`, sem `language_context`, sem `adapt_system_prompt`. O `get_generation_service(domain)` faz dispatch entre os dois.

**Correção:** Kids multilíngue é um subprojeto separado (Fase 6). O pipeline Kids precisa de `GenerationContext` injetado, `PromptRegistry` integrado, e adaptação de `topic_library`/`seasonal_calendar`. Isso é escopo significativo. Para a refatoração principal, focar no domínio Games primeiro.

### C5. Remover tradução tardia antes de validar prompts é arriscado

**Plano v1 dizia (Fase 3):** "Remover `_translate_creative_material`, `_translate_story_concept`, `_translate_creative_plan`" junto com ativar PromptRegistry

**Realidade:** Se os packs i18n tiverem erros de tradução, ou se o LLM (mesmo Qwen3) não respeitar perfeitamente o prompt, o conteúdo pode vir em pt-BR. As funções de tradução tardia são uma **rede de segurança**. Removê-las sem validar primeiro é perigoso.

**Correção:** Fase 2 (ativar PromptRegistry) e Fase 3 (remover tradução tardia) são **sequenciais com validação entre elas**. Após Fase 2, rodar jobs zh-CN de teste e verificar se o conteúdo vem no idioma alvo. Só depois remover as traduções tardias em Fase 3.

### C6. `_translate_creative_plan` não traduz tudo — campos não traduzidos virarão pt-BR

**Plano v1 dizia:** "Remover `_translate_creative_plan` — conteúdo já é gerado no idioma alvo"

**Realidade:** `_translate_creative_plan` (linha 1810) traduz apenas `central_idea` e `narrative_beats[].description`. NÃO traduz: `tone`, `humor.styles`, `gameplay_query`, `model_recommendation`. Hoje esses campos ficam em pt-BR e são consumidos pelo ScriptService e GameplayRetriever. Remover a tradução sem garantir que o `EditorialPlanner` gere esses campos no idioma alvo vai deixar `gameplay_query` em pt-BR, afetando seleção de gameplay.

**Correção:** O `EditorialPlanner` com PromptRegistry deve gerar TODOS os campos de texto no idioma alvo. `gameplay_query` merece atenção especial — keywords em pt-BR não funcionam para busca de gameplay em CJK. Considerar `gameplay_query` como vocabulário semântico independente de idioma (tags de ação: "explosion", "racing", "combat" — já em inglês no `editorial_planner.py:340-353`).

### C7. `is_compatible_with` invalidará TODOS os checkpoints CJK existentes

**Plano v1 não mencionava isso.**

**Realidade:** `is_compatible_with` (language_context.py:299-318) verifica `llm_script_model`. Quando mudarmos de `llama3.1:8b` para `qwen3:14b` para CJK, TODOS os jobs CJK com checkpoints existentes terão `is_compatible_with` retornando False. Isso é **comportamento correto** (queremos regenerar), mas precisa ser documentado.

**Correção:** Documentar que a mudança de modelo invalida checkpoints CJK existentes. Job #505 será regenerado do zero (esperado). Jobs pt-BR não são afetados (modelo não muda).

### C8. Metadata fallback "Gameplay Curiosidade" — e se plan.topic também for vazio?

**Plano v1 dizia:** "Remover fallback 'Gameplay Curiosidade'. Usar `plan.topic` (já no idioma alvo)"

**Realidade:** Se `plan.topic` for vazio E o LLM de metadata falhar, não há fallback. O título fica vazio. O plano v1 não trata este edge case.

**Correção:** Manter um fallback chain: `plan.topic` → título gerado pelo LLM → fallback localizado por idioma (`"游戏趣闻"` para zh, `"Gameplay Curiosities"` para en, `"Gameplay Curiosidade"` para pt). O fallback localizado é último recurso, não primeiro.

### C9. PromptRegistry fallback para pt-BR pode mascarar erro

**Plano v1 dizia:** "PromptRegistry.get() com fallback para pt-BR"

**Realidade:** Se um pack zh_cn tiver um bug de import (typo no nome da constante), o registry silenciosamente cai para pt_br. O serviço usa prompt pt-BR sem saber. Isso reproduz o problema atual.

**Correção:** Adicionar log WARNING quando fallback ocorre. Adicionar teste que verifica que TODAS as constantes existem em TODOS os packs (sem exceção). Se uma constante faltar em zh_cn, o teste falha — não há fallback silencioso.

### C10. Packs zh_cn/zh_tw têm targets desatualizados (3.5 chars/s)

**Plano v1 dizia:** "Atualizar packs zh_cn/zh_tw com targets 5.5 chars/s"

**Realidade confirmada:** `zh_cn/games_prompts.py:14-17`:
```
Mandarin Chinese narration is ~3.5 characters per second (vs ~15 for English).
For a 60-second video: ~210 characters (vs ~900 for English).
...(~200-280 汉字 instead of ~800-1000 characters).
```

E `zh_cn/games_prompts.py:25`: `60秒视频约需要200-280个汉字`
E `zh_cn/games_prompts.py:42`: `每秒3.5个字的旁白`

**Correção:** Confirmado. Atualizar todos os packs zh para 5.5 chars/s e 280-379 chars para 60s. Este é um fix simples mas necessário antes de ativar PromptRegistry.

### C11. Creative engine context line hardcoded pt-BR

**Plano v1 não mencionava isso explicitamente.**

**Realidade:** `generation_service.py:1709-1714`:
```python
context = "Curiosidade geral"
```

Este context é passado para o creative engine. Para zh-CN deveria ser "一般趣闻" ou equivalente.

**Correção:** Adicionar à Fase 3: localizar strings de contexto do creative engine.

### C12. `model_preferences` em ChannelProfile — migration necessária

**Plano v1 dizia:** "Adicionar coluna `model_preferences: JSON` em `ChannelProfile`"

**Realidade confirmada:** `ChannelProfile` não tem essa coluna. `LanguageContext.from_channel_profile` faz `getattr(profile, "model_preferences", None) or {}` que sempre retorna `{}`. Adicionar a coluna requer migration via `_ensure_column()` (padrão do GPCG).

**Correção:** Confirmado. A migration usa `_ensure_column("channel_profiles", "model_preferences", "JSON", "NULL")` em `init_db()`. Baixo risco.

### C13. Voz e idioma não são validados — usuário pode selecionar voz pt-BR para job zh-CN

**Plano v1 não mencionava isso.**

**Realidade:** Em nenhum ponto do código há validação de compatibilidade entre a voz selecionada e o idioma alvo do job. O fluxo é:

1. `routes.py:1078-1090` — se `voice` não for setado, faz auto-select por idioma
2. `routes.py:1078` — se `voice` já estiver setado na automação (`auto_cfg.get("voice")`), o auto-select **é pulado**
3. A voz selecionada é passada para `synthesize_tts(voice_path=..., language=gen_ctx.tts_language)`
4. O XTTS recebe `speaker_wav` (voz) e `language` (idioma) como parâmetros **separados**

XTTS v2 clona a voz do `speaker_wav` mas usa o `language` para guiar a síntese. Se a voz for pt-BR (bruno.wav) e o idioma for zh, o XTTS vai tentar sintetizar chinês com a voz do Bruno — o resultado soa como português com palavras chinesas, ou prosódia completamente errada.

**Causa provável do bug #505:** Usuário 4 tem `voice-zh-native.mp3` em `user_4/`, mas se a automação tinha `voice=bruno.wav` (ou similar) setado explicitamente, o auto-select é pulado e `bruno.wav` (pt-BR) é usado com `language=zh`.

**Correção:** Adicionar validação voz↔idioma na criação do job. Se a voz selecionada é incompatível com o idioma alvo, fazer override para auto-select ou rejeitar com erro claro.

### C14. Upload de voz não pede idioma — `_detect_voice_language` é heurística frágil

**Plano v1 não mencionava isso.**

**Realidade:** `POST /voices/upload` (`routes.py:2010-2039`) não recebe parâmetro `language`. O idioma da voz é inferido por `_detect_voice_language(filename)` que faz match de substrings:

```python
if "en" in name or "english" in name or "ingles" in name: return "en-US"
if "zh" in name or "chines" in name or "chinese" in name or "mandarin" in name: return "zh-CN"
if "es" in name or "spanish" in name or "espanol" in name: return "es-ES"
if "fr" in name or "french" in name or "frances" in name: return "fr-FR"
return "pt-BR"  # default
```

Problemas:
- `"es"` em "especial", "voice", "test" → falso positivo es-ES
- `"en"` em "agent", "open", "spoken" → falso positivo en-US
- `"fr"` em "from", "frequency" → falso positivo fr-FR
- Upload de `minha_voz.mp3` (sem hint) → default pt-BR (pode estar errado)

**Correção:** Adicionar parâmetro `language` no upload de voz. O usuário declara o idioma. `_detect_voice_language` fica como fallback apenas.

### C15. Vozes não têm metadados persistidos — não há tabela Voice

**Plano v1 não mencionava isso.**

**Realidade:** Não existe tabela `Voice` no DB. Vozes são apenas arquivos no disco. `_SYSTEM_VOICES` é um dict hardcoded com 3 entradas (`bruno.wav`, `voice-en-native.mp3`, `voice-zh-native.mp3`). Vozes uploaded não têm metadados persistidos — o idioma é re-inferido a cada `GET /voices` pelo nome do arquivo.

**Correção:** Criar tabela `Voice` com: `id`, `user_id`, `filename`, `language`, `display_name`, `file_size`, `created_at`. O upload persiste o idioma declarado. O `GET /voices` lê do DB (não re-infere). Vozes do sistema são seedadas na migration.

### C16. Auto-select de voz só roda quando voice está vazio

**Plano v1 não mencionava isso.**

**Realidade:** `routes.py:1077-1092`:
```python
if not voice:
    voice = auto_cfg.get("voice", "")
# Auto-select voice based on channel profile language when none is set
if not voice:
    # ... auto-select logic ...
```

Se `auto_cfg.get("voice")` retornar `"bruno.wav"`, o auto-select **nunca roda**, mesmo que o idioma alvo seja zh-CN. A voz pt-BR é usada para sintetizar chinês.

**Correção:** O auto-select deve rodar quando:
1. `voice` está vazio, OU
2. `voice` está setado mas é incompatível com o idioma alvo do job

### C17. Vozes no storage não batem com `_SYSTEM_VOICES`

**Plano v1 não mencionava isso.**

**Realidade confirmada no storage:**
```
/media/bruno/ToshibaHD/gpcg/data/voices/
├── bruno-slow.wav              (pt-BR, 52MB) — NÃO está no _SYSTEM_VOICES
├── brunoamplifier-slow.wav     (pt-BR, 9MB)  — NÃO está no _SYSTEM_VOICES
├── voice-en-native.mp3         (en-US, 1.7MB) — está no _SYSTEM_VOICES
├── voice-zh-native.mp3         (zh-CN, 3.3MB) — está no _SYSTEM_VOICES
├── user_2/bruno.wav            (pt-BR, 6MB)  — referenciado no _SYSTEM_VOICES mas está em user_2/
└── user_4/voice-zh-native.mp3  (zh-CN, 3.3MB) — cópia do sistema
```

`_SYSTEM_VOICES` tem `"bruno.wav"` mas no storage root não existe `bruno.wav` — existe `bruno-slow.wav` e `brunoamplifier-slow.wav`. O `bruno.wav` real está em `user_2/`. Isso significa que o auto-select para pt-BR pode não encontrar a voz correta.

**Correção:** Atualizar `_SYSTEM_VOICES` para refletir os arquivos reais no storage. Adicionar `bruno-slow.wav` e `brunoamplifier-slow.wav` como vozes pt-BR do sistema.

### C18. PUXADINHO `_build_subtitle_segments` quebra TODAS as legendas — não só CJK

**Plano v1 dizia:** "Remover `_build_subtitle_segments` (puxadinho). Voltar a `{"tts_text": text, "expansions": []}`"

**Realidade descoberta pelo engenheiro de legendas:** O puxadinho é uma mudança **não commitada** (presente apenas em `git diff HEAD`) que quebra o pipeline de legendas para **TODOS os idiomas**, incluindo pt-BR que funcionava perfeitamente antes.

**O caminho original que funcionava (commit 8236e70):**
1. `synthesize_tts()` retorna `subtitle_mapping = {"tts_text": text, "expansions": []}` — **SEM segments**
2. `generate_auto_srt()` vê `subtitle_mapping` mas `mapping_segments` é vazio → cai para Whisper
3. Whisper transcreve o áudio → **timestamps reais do áudio**
4. `SequenceAligner` alinha texto do roteiro com timestamps do Whisper → texto correto + timing real
5. `segments_to_srt()` converte para SRT com timing word-level
6. Resultado: legendas que acompanham exatamente a fala

**O que o puxadinho fez (uncommitted):**
1. `synthesize_tts()` agora SEMPRE chama `_build_subtitle_segments()` → retorna **segments com timing proporcional**
2. `generate_auto_srt()` vê segments → usa diretamente → **NUNCA cai para Whisper**
3. Timing é distribuído proporcionalmente por contagem de caracteres — **não do áudio real**
4. Resultado: legendas com timing fake que não acompanha a fala

**Isto afeta pt-BR também!** O puxadinho foi adicionado para "evitar Whisper para CJK" mas como `_build_subtitle_segments` sempre retorna segments, **todos os jobs** agora usam timing proporcional em vez do Whisper+SequenceAligner que funcionava.

**Correção:** Reverter `subtitle_mapping` para `{"tts_text": text, "expansions": []}` (sem segments). Deixar Whisper fazer seu trabalho. O Whisper já foi corrigido para ser language-aware (prompt por idioma, language code correto). O SequenceAligner precisa de fix para CJK (ver C19).

### C19. SequenceAligner quebra para CJK — tokeniza por whitespace

**Plano v1 dizia:** "Adicionar tokenização CJK em `originality.py`"

**Realidade:** O mesmo problema existe no `SequenceAligner` do video-generate, que é mais grave porque afeta legendas em tempo real.

`text_normalizer.py:146`:
```python
tokens = cleaned.split()
```

Para chinês sem espaços, `cleaned.split()` retorna **1 token** (texto inteiro). O SequenceAligner faz:
- `official_tokens` = 1 token (texto chinês inteiro)
- `whisper_tokens` = 1 token (transcrição chinesa inteira)
- SequenceMatcher com 1 token cada → match ou replace de 1 token
- Resultado: 1 segmento com o texto inteiro → 1 legenda gigante para o vídeo todo

**Correção:** Adicionar tokenização CJK no `TextNormalizer`:
- Para CJK: tokenizar por caractere (cada caractere = 1 token)
- Para Latin: manter `cleaned.split()` (whitespace)
- O `SequenceAligner` então alinha caractere-a-caractere para CJK
- N-gram grouping em `AlignmentResult.segments` (48 chars) já funciona para CJK

### C20. `align_text_to_transcription` não recebe `language`

**Plano v1 não mencionava isso.**

**Realidade:** `subtitle_aligner.py:196-198`:
```python
def align_text_to_transcription(
    original_text: str, whisper_segments: List[Dict], confidence_threshold: float = 0.80
) -> Tuple[List[Dict], float]:
```

Não recebe `language`. O `TextNormalizer` dentro usa whitespace tokenização independente do idioma. Mesmo que corrijamos C19, o caller não passa o idioma.

E em `generate.py:714`:
```python
aligned_segments, confidence = align_text_to_transcription(
    original_text, segments, confidence_threshold=0.80
)
```

**Correção:** Adicionar parâmetro `language` em `align_text_to_transcription` e `SubtitleAligner.align_segments`. Passar `language` do caller em `generate.py`.

### C21. Whisper CPU fallback não passa `language` — defaults para "pt"

**Plano v1 dizia:** "Fallback de CPU passa `language` (line 580-583 — hoje não passa)"

**Realidade confirmada:** `generate.py:581-583`:
```python
result = _whisper_transcribe_core(
    audio_file, "cpu", original_text, use_alignment
)
```

Não passa `language`. A função tem `language="pt"` como default. Áudio CJK transcrito em CPU usa configuração português — Whisper tenta transcrever chinês como se fosse português, produzindo lixo.

**Correção:** Passar `language=language` na chamada de CPU fallback. Também aplicar `apply_subtitle_mapping` no fallback de CPU (hoje é pulado).

### C22. `segments_to_srt` sem word-level timestamps quebra para CJK

**Plano v1 dizia:** "`segments_to_srt` reconhece pontuação CJK"

**Realidade:** `generate.py:841`:
```python
words = text.split()
```

Para CJK sem espaços, `text.split()` retorna 1 token. O loop `for i, word in enumerate(words)` executa 1 vez. `chunk_text` = texto inteiro. `should_break` = `True` (última palavra). Resultado: 1 legenda por segmento Whisper, sem quebra de linha.

Se o WhisperX estiver disponível, segments têm `words` com timestamps → caminho word-level funciona. Mas sem WhisperX, o fallback de segmento quebra para CJK.

**Correção:** Para CJK sem word-level timestamps, quebrar por contagem de caracteres (similar ao `SubtitleRenderer.wrap_text` CJK path). Distribuir timing proporcionalmente entre os caracteres dentro do segmento.

### C23. `voice_path` NOT SET no job #505 — causa raiz do bug de voz

**Descoberta direta do DB da VPS:**

```sql
SELECT artifacts FROM jobs WHERE id=505;
-- "voice_path": NOT SET (chave não existe)
-- "config_snapshot": {"voice": "voice-zh-native.mp3", ...}
-- "generation_context": {"tts_language": "zh", ...}
```

A automação do User 4 tem `"voice": "voice-zh-native.mp3"`. Mas `voice_path` (caminho absoluto) **não foi setado nos artifacts**. O worker recebeu `voice_path` vazio → usou o default do video-generate (`public/voices/bruno.wav` — pt-BR) → XTTS sintetizou chinês com voz portuguesa.

**Causa raiz:** `generation_service.py:189`:
```python
if voice_path:  # empty string is falsy → artifacts["voice_path"] nunca é setado
    artifacts["voice_path"] = voice_path
```

Se `voice_path` chega como `""` (vazio), a chave nunca é adicionada aos artifacts. O worker lê `self._get_artifact(job_id, "voice_path")` → retorna `None` → usa default.

**Mas por que `voice_path` chegou vazio?** A automação do User 4 tem `voice=voice-zh-native.mp3`. O endpoint deveria resolver para o caminho absoluto. Possíveis causas:
1. O endpoint que criou o job não resolveu a voz corretamente
2. O arquivo não existia no VPS no momento da criação
3. Bug em um dos endpoints de automação (há 2 code paths com lógica diferente)

**Correção:** Além da validação voz↔idioma (Fase 1.5.8), garantir que `voice_path` seja SEMPRE setado nos artifacts quando a automação tem `voice` configurado. Se a voz não existe no VPS, rejeitar o job com erro claro (não silenciosamente usar default).

### C24. Vozes do sistema não são sincronizadas VPS ↔ worker

**Realidade confirmada por inspeção direta:**

```
VPS (Docker volume):
  voices/bruno.wav              (6.1MB, pt-BR)
  voices/voice-en-native.mp3    (1.7MB, en-US)
  voices/voice-zh-native.mp3    (3.3MB, zh-CN)
  voices/user_4/voz-infantil-aggressive.wav  (6.2MB)

Worker local (/media/bruno/ToshibaHD/gpcg/data/voices/):
  voices/bruno-slow.wav         (52MB, pt-BR)  ← NÃO existe no VPS
  voices/brunoamplifier-slow.wav (9MB, pt-BR)  ← NÃO existe no VPS
  voices/voice-en-native.mp3    (1.7MB, en-US) ← existe em ambos
  voices/voice-zh-native.mp3    (3.3MB, zh-CN) ← existe em ambos
  voices/user_2/bruno.wav       (6.1MB, pt-BR) ← existe no VPS root
  voices/user_4/voice-zh-native.mp3 (3.3MB)   ← cópia local
```

Problemas:
- `bruno-slow.wav` (52MB) existe apenas no worker. User 1 tem `voice=bruno-slow.wav` na automação. Se a API no VPS tentar resolver, não encontra → `voice_path=""` → worker usa default.
- `bruno.wav` existe no VPS root mas não no worker root (apenas em `user_2/`).
- Vozes do sistema (shared root) não têm mecanismo de sincronização.

**Correção:** Adicionar endpoint `/api/voices/system` que lista vozes do sistema com metadados. O worker baixa vozes do sistema sob demanda (já faz isto para vozes de usuário). Adicionar log WARNING quando uma voz do sistema não existe localmente e é baixada do VPS.

### C25. Endpoint de automação não rejeita voz inexistente — silenciosamente usa default

**Realidade:** Há **2 code paths** para resolução de voz na automação:

**Code path 1** (`automation_routes.py:1177-1186` — auto-fill):
```python
voice_path = ""
if voice_name:
    user_voice = settings.voices_dir / f"user_{req.user_id}" / voice_name
    shared_voice = settings.voices_dir / voice_name
    if user_voice.exists():
        voice_path = str(user_voice)
    elif shared_voice.exists():
        voice_path = str(shared_voice)
    # SEM else — voice_path fica "" silenciosamente
```

**Code path 2** (`routes.py:1099-1103` — manual):
```python
else:
    raise HTTPException(404, f"voice '{voice}' not found — upload it first")
```

O code path 1 (auto-fill) **não rejeita** — `voice_path` fica vazio, job é criado, worker usa default. O code path 2 (manual) rejeita com 404.

**Isto explica o #505:** Se o job foi criado via auto-fill (ou um endpoint que usa o code path 1), e a voz não existia no VPS no momento, `voice_path=""` foi passado silenciosamente.

**Correção:** Ambos os code paths devem ter o mesmo comportamento: se `voice_name` está setado mas a voz não existe, rejeitar com erro claro. NUNCA usar default silenciosamente quando o usuário configurou uma voz específica.

### C26. Tabela `Voice` precisa ser sincronizada para o worker DB local

**Realidade:** O worker cria um DB SQLite local temporário para cada job (`local_db_sync.py:670`):
```python
os.environ["GPCG_DATA_DIR"] = str(storage_root / "data")
```

O DB local é populado com dados do job (content_plans, scripts, etc). Se criarmos a tabela `Voice`, ela precisa ser populada no DB local do worker também — caso contrário, o GenerationService não pode validar voz↔idioma durante o processamento.

**Mas:** A validação voz↔idioma deve acontecer na **criação do job** (VPS API), não durante o processamento (worker). O worker recebe `voice_path` já resolvido. A tabela `Voice` no worker é necessária apenas para:
- Logging/auditoria de qual voz foi usada e em qual idioma
- Listagem de vozes no UI do worker (se aplicável)

**Correção:** A tabela `Voice` é VPS-only. O worker não precisa dela para processamento. Mas o `voice_path` nos artifacts deve incluir o idioma da voz para auditoria: `artifacts["voice_language"] = "zh-CN"`.

### C27. Deploy não sincroniza vozes do sistema

**Realidade:** `deploy.sh` faz rsync do código + build Docker. Vozes são arquivos grandes (52MB para bruno-slow.wav) e não são sincronizadas. Vozes do sistema (shared root) precisam ser consistentes entre VPS e worker.

Hoje não há mecanismo. O worker baixa vozes sob demanda via SCP/HTTP, mas apenas vozes referenciadas em jobs. Vozes do sistema que não são referenciadas não são baixadas.

**Correção:** Adicionar comando `gpcg sync-voices` que sincroniza vozes do sistema entre VPS e worker. O worker baixa vozes do sistema que não existem localmente. Vozes do usuário são baixadas sob demanda (como hoje).

### C28. Worker não tem env vars multilingual — ROOT CAUSE de TODA a falha multilíngue

**Descoberta direta do ambiente de produção:**

```
VPS .env:
  GPCG_MULTILINGUAL_ENABLED=true
  GPCG_MULTILINGUAL_LANGUAGES=pt-BR,en,zh-CN,zh-TW,zh
  GPCG_MULTILINGUAL_TTS_ENABLED=true
  GPCG_MULTILINGUAL_PROMPTS_ENABLED=true
  GPCG_MULTILINGUAL_QA_ENABLED=true

Worker systemd service:
  (NENHUMA variável multilingual)
```

O worker roda `GenerationService` localmente. `GenerationContext.from_channel_profile()` tem um kill switch em `language_context.py:182`:
```python
if not getattr(s, "gpcg_multilingual_enabled", False):
    return cls()  # retorna pt-BR default
```

Como o worker não tem `GPCG_MULTILINGUAL_ENABLED=true`, o kill switch retorna `pt-BR` para **TODOS** os jobs, independente do `target_language` do ChannelProfile.

**Evidência direta do DB da VPS (job #511, User 4, zh-CN, COMPLETED):**
```json
{
  "generation_context": {
    "language": "pt-BR",      ← WRONG (should be zh-CN)
    "tts_language": "pt",     ← WRONG (should be zh)
    "llm_script_model": "llama3.1:8b"  ← WRONG (should be qwen3)
  },
  "voice_path": "/app/data/voices/voice-zh-native.mp3"  ← correct voice, wrong context
}
```

**Impacto em cascata:**
1. `generation_context.language = "pt-BR"` → todos os prompts em português
2. `generation_context.tts_language = "pt"` → XTTS sintetiza como português
3. `llm_script_model = "llama3.1:8b"` → modelo não-CJK usado para CJK
4. `channel_context` é gerado em português (não chinês)
5. ContentPlan.topic em português
6. Script.final em português
7. Video renderizado com áudio português + voz chinesa = som errado

**Isto explica o bug reportado pelo usuário:** "em chines tava marcado a voz em chines mas parecia a voz de pt-br". A voz chinesa estava selecionada, mas o texto era português e o TTS language era português. O XTTS tentou sintetizar texto português com voz chinesa e language=pt.

**Correção:** Adicionar todas as env vars multilingual ao worker systemd service. O worker é um Compute Plane completo que roda a mesma codebase do VPS — precisa das mesmas feature flags.

### C29. ContentPlan/Script/Video `language` não é sincronizado VPS↔worker

**Descoberta direta do DB da VPS:**
```sql
SELECT id, user_id, target_language FROM content_plans WHERE user_id=4;
-- ALL show target_language='pt-BR' (should be zh-CN)

SELECT id, language FROM scripts WHERE content_plan_id IN (SELECT id FROM content_plans WHERE user_id=4);
-- ALL show language='pt-BR' (should be zh-CN)

SELECT id, language FROM videos WHERE job_id IN (SELECT id FROM jobs WHERE user_id=4);
-- ALL show language='pt-BR' (should be zh-CN)
```

**Causa dupla:**

1. **Worker não envia language no sync payload** (`local_db_sync.py:716-774`):
   - `content_plan` dict não inclui `target_language`
   - `script` dict não inclui `language`
   - `video` dict não inclui `language`

2. **VPS não seta language ao criar registros** (`api/workers/generation.py:480-564`):
   - `ContentPlan(...)` não passa `target_language`
   - `Script(...)` não passa `language`
   - `Video(...)` não passa `language`

Resultado: mesmo se o worker gerar conteúdo em zh-CN, o VPS DB mostra `pt-BR` (default da coluna).

**Correção:**
- `local_db_sync.py`: incluir `target_language`/`language` nos dicts extraídos
- `api/workers/generation.py`: setar `target_language`/`language` ao criar registros
- Ou melhor: o VPS já tem `job.artifacts["generation_context"]["language"]` — usar isso como fonte de verdade

### C30. `mood=` em vez de `music_mood=` — causa falha de jobs curiosity_short

**Realidade:** `content_planning_service.py:389`:
```python
plan = ContentPlan(
    ...
    mood="energetic",        ← BUG: field is music_mood
    ...
    metadata={               ← BUG: field is metadata_json
    ...
    scope=ContentScope.general.value,  ← BUG: field doesn't exist, ContentScope not defined
)
```

**Evidência direta do DB da VPS:**
```
Job #575: failed, error: "'mood' is an invalid keyword argument for ContentPlan"
Job #568: failed, error: "'mood' is an invalid keyword argument for ContentPlan"
```

**Causa:** Código duplicado. O path correto (linha 275-294) usa `music_mood=` e `metadata_json=`. O path bugado (linha 384-404) usa `mood=`, `metadata=`, e `scope=`.

**3 bugs no mesmo constructor:**
1. `mood=` → should be `music_mood=`
2. `metadata=` → should be `metadata_json=`
3. `scope=ContentScope.general.value` → `scope` doesn't exist on ContentPlan, `ContentScope` is not defined

**Correção:** Corrigir os 3 campos no path bugado (linha 384-404) para match com o path correto (linha 275-294).

### C31. Worker não carrega .env — depende apenas de env vars do systemd

**Realidade:** O worker não chama `load_dotenv()` em nenhum lugar. Todas as configurações vêm das env vars do systemd service file. Se uma configuração não está no service file, usa o default.

**Isto significa que o worker está faltando TODAS estas configurações:**
- `GPCG_MULTILINGUAL_ENABLED` → default `False` → kill switch
- `GPCG_MULTILINGUAL_LANGUAGES` → default `"pt-BR"` → allowlist não inclui zh-CN
- `GPCG_MULTILINGUAL_TTS_ENABLED` → default `False`
- `GPCG_MULTILINGUAL_PROMPTS_ENABLED` → default `False`
- `GPCG_MULTILINGUAL_QA_ENABLED` → default `False`
- `GPCG_CREATIVE_ENGINE_ENABLED` → default `False`
- `GPCG_CONTENT_INTELLIGENCE_ENABLED` → default `False`
- `GPCG_TTS_ENGINE` → default `"coqui"` (OK)
- `GPCG_TTS_MODEL` → default pode ser errado
- `GPCG_LLM_MODEL` → worker usa default do config, não `gpt-oss:latest`

**Correção:** Duas opções:
1. **Quick fix:** Adicionar todas as env vars necessárias ao systemd service file
2. **Arquitetural:** Worker carrega `.env` local (e.g. `~/.config/gpcg/worker.env`) que é sincronizado com o VPS .env

Opção 2 é melhor porque:
- Novas env vars no VPS .env precisam ser manualmente copiadas para o systemd service
- Esquecer uma env var causa bugs silenciosos (como este)
- O worker já tem `GPCG_VPS_URL` — poderia fazer `GET /api/config/env` para buscar env vars do VPS

### C32. YouTube upload não seta `defaultLanguage` nem `defaultAudioLanguage`

**Realidade:** `google-integration/apps/api/src/modules/providers/youtube/youtube.provider.ts:35-40`:
```typescript
snippet: {
  title: metadata.title,
  description: metadata.description,
  tags: metadata.tags ?? [],
  categoryId: metadata.categoryId ?? '22',
  // MISSING: defaultLanguage, defaultAudioLanguage
}
```

YouTube API suporta `snippet.defaultLanguage` (idioma do metadata) e `snippet.defaultAudioLanguage` (idioma do áudio). Sem estes campos, YouTube assume o idioma do canal ou auto-detect, que pode estar errado para conteúdo zh-CN.

**Impacto:**
- SEO: YouTube não recomenda o vídeo para audiência chinesa
- Accessibility: legendas auto-geradas usam idioma errado
- Analytics: audiência geográfica é mal direcionada
- Monetização: anúncios podem não ser relevantes

**Correção:**
1. `google-integration` API: adicionar `defaultLanguage` e `defaultAudioLanguage` ao snippet, lendo de `metadata.language`
2. `gpcg/infrastructure/google_integration_adapter.py`: passar `language` do `gen_ctx.language` ao chamar upload
3. `gpcg/api/workers/generation.py` ou `presentation_service`: incluir `language` no metadata enviado ao google-integration
4. Normalizar BCP-47: `zh-CN` → `zh-CN`, `zh-TW` → `zh-TW`, `zh` → `zh-Hans`, `pt-BR` → `pt-BR`, `en-US` → `en-US`

### C33. `gameplay_query` cross-lingual pode quebrar semantic search

**Realidade:** O `GameplayRetriever` usa `gameplay_query` para buscar clips semanticamente:
1. `EditorialPlanner` gera `gameplay_query` (após Fase 2, pode ser em zh-CN)
2. `gameplay_index_service.search_events()` gera embedding do query via `nomic-embed-text`
3. Compara com embeddings de eventos (descrições em inglês do VLM)
4. Retorna eventos com cosine similarity > 0.3

**Problema:** `nomic-embed-text` é multilingual, mas cross-lingual zh↔en tem precisão reduzida. Uma query "爆炸" (explosão) pode não match bem com descrição "big explosion scene".

**Impacto:**
- Semantic search retorna poucos/poor matches → fallback para random clips
- Vídeo zh-CN tem gameplay visualmente dissociado do roteiro
- Não causa erro, mas degrada qualidade

**Correção (Fase 3.10):**
- **Opção A (simples):** `gameplay_query` é sempre em inglês (vocabulário semântico independente de idioma). O `EditorialPlanner` é instruído a gerar `gameplay_query` em inglês independente do `target_language`. Tags de ação: "explosion", "racing", "combat" — já em inglês no `editorial_planner.py:340-353`.
- **Opção B (robusta):** Traduzir `gameplay_query` para inglês antes de buscar. Adicionar `_translate_gameplay_query()` que traduz zh→en antes de chamar `search_events()`.
- **Recomendação:** Opção A. `gameplay_query` é vocabulário técnico de ação, não conteúdo narrativo. Inglês é o idioma natural para tags de gameplay.

### C34. `game_enrichment.py` gera lore em português hardcoded

**Realidade:** `game_enrichment.py:246`:
```python
prompt = (
    f"Você é um roteirista de vídeos sobre games. Resuma a história e o lore "
    f"do jogo '{game_name}' em português, em no máximo 3 parágrafos. ..."
)
```

Lore é usado como contexto para geração de conteúdo. Para canal zh-CN, o contexto vem em pt-BR.

**Impacto:** Baixo. O `language_directive` do `gen_ctx` instrui o LLM a gerar em chinês independente do contexto ser pt-BR. O LLM (Qwen3) é capaz de ler português e gerar chinês. Mas não é ideal — contexto em pt-BR pode influenciar vocabulário/tom.

**Correção:** Postergar para Fase 6. Baixo impacto. Se implementar: receber `language_context` e adaptar prompt. Mas lore é cacheado por game (não por user/idioma), então gerar lore em zh-CN para um game afetaria todos os users. Solução: gerar lore em inglês (idioma neutro) ou manter pt-BR e confiar no `language_directive`.

### C35. `ContentPlanningService` não adapta prompt (confirmado)

**Realidade:** `content_planning_service.py:160`:
```python
data = llm.chat_json(SYSTEM_PROMPT, prompt, temperature=0.6, max_tokens=1024)
```

`SYSTEM_PROMPT` é importado de `domains.games.prompts` (pt-BR hardcoded). Não usa `adapt_system_prompt` nem `PromptRegistry`.

**Já coberto pela Fase 2.7** do plano. Confirmar que a implementação:
1. Troca `SYSTEM_PROMPT` por `PromptRegistry.get("content_planning_system", language_context)`
2. OU aplica `adapt_system_prompt(SYSTEM_PROMPT, language_context)`
3. E que o `prompt` (user message) também recebe `language_directive`

---

## PARTE I — DIAGNÓSTICO ARQUITETURAL

### 1. O problema fundamental

O GPCG foi construído como um sistema monolíngue (pt-BR) e recebeu multilinguismo como **camada de pós-processamento**. O idioma alvo não é uma propriedade fundamental do job — é um override aplicado no final do pipeline, depois de todo o conteúdo já ter sido gerado, criticado, reescrito e validado em português.

A cadeia atual:

```
Fact (pt-BR) → ContentPlan (pt-BR) → StoryConcept (pt-BR, traduzido depois)
  → EditorialPlan (pt-BR, traduzido depois) → CreativeMaterial (pt-BR, traduzido depois)
    → Script draft/optimized/final (pt-BR) → ScriptCritic (pt-BR)
      → Anti-plágio (pt-BR, n-gram quebrado para CJK) → Expansão (pt-BR)
        → _translate_script() ← ÚNICO ponto de tradução
          → TTS (idioma alvo) → Metadata (idioma alvo, modelo errado)
            → Legendas (Whisper com prompt pt-BR) → Render → Video.language=pt-BR
              → Sync → VPS perde idioma em 3 modelos
```

### 2. Os 14 defeitos arquiteturais (expandido de 12 com auditoria)

| # | Defeito | Causa raiz | Impacto |
|---|---------|------------|---------|
| 1 | **Script gerado em pt-BR, traduzido no final** | `ScriptService` gera draft/optimized/final/expansão/reescrita em pt-BR, só traduz na linha 455 | Duração errada, perda de naturalidade, originalidade não verificada no idioma final |
| 2 | **PromptRegistry existe mas nunca é usado** | Todos os serviços importam de `gpcg.domains.games.prompts` (pt-BR) diretamente | Packs i18n completos estão mortos. `adapt_system_prompt` é band-aid |
| 3 | **Modelo LLM não selecionado por idioma** | `gpcg_llm_model = "llama3.1:8b"` para tudo | Títulos em inglês, scripts curtos, mistura de idiomas |
| 4 | **ContentPlanning não aplica adapt_system_prompt** | `content_planning_service.py:160` passa prompt pt-BR direto | `topic` e `hook` ficam em pt-BR para sempre |
| 5 | **ScriptCritic não recebe target_duration** | Assinatura não inclui esses parâmetros | Crítico aprova scripts curtos demais |
| 6 | **Idioma não é sincronizado worker→VPS** | 4 pontos de sync, nenhum propaga `language` | VPS sempre mostra pt-BR |
| 7 | **model_preferences é campo fantasma** | `ChannelProfile` não tem essa coluna | Override de modelo por canal nunca funciona |
| 8 | **Legendas: Whisper pt-BR + alinhador Latin** | Prompt hardcoded, `SubtitleAligner` tokeniza por espaços | Legendas em português para áudio chinês |
| 9 | **Puxadinho: subtitle_segments proporcionais** | `_build_subtitle_segments` bypassa Whisper | Timing não vem do áudio real |
| 10 | **Expansão no fluxo normal não adapta prompt** | `script_service.py:373` passa `OPTIMIZE_SYSTEM` puro | Expansão em pt-BR para jobs CJK |
| 11 | **Anti-plágio quebra para CJK** | `originality.py:89` `_tokenize` usa `text.split()` | CJK sem espaços = 1 token = score sempre 100 |
| 12 | **Kids pipeline não tem adaptação de idioma** | `KidsGenerationService` é pipeline separado, sem gen_ctx | Jobs Kids em zh-CN geram conteúdo em pt-BR |
| 13 | **Humanization é pt-BR only (regex + prompt)** | 13 regex patterns + prompt em pt-BR | Não pode ser simplesmente "habilitado" para CJK |
| 14 | **Creative engine context hardcoded pt-BR** | `generation_service.py:1709` `"Curiosidade geral"` | Contexto do creative engine em pt-BR para todos |
| 15 | **Voz não validada contra idioma do job** | `routes.py:1078-1090` — auto-select pulado se voice já setado na automação | XTTS sintetiza zh com voz pt-BR — som errado |
| 16 | **Upload de voz não pede idioma** | `routes.py:2010-2039` — `_detect_voice_language` faz heurística frágil por substring | Falsos positivos, idioma errado inferido |
| 17 | **Vozes não têm metadados persistidos** | Não há tabela `Voice` no DB | Idioma re-inferido a cada `GET /voices` |
| 18 | **`_SYSTEM_VOICES` não bate com storage real** | Dict tem `bruno.wav` mas storage tem `bruno-slow.wav` | Auto-select pt-BR pode não encontrar voz |
| 19 | **Puxadinho `_build_subtitle_segments` quebra TODAS as legendas** | Mudança uncommitted bypassa Whisper para todos os jobs | Timing proporcional em vez de real para pt-BR e CJK |
| 20 | **SequenceAligner tokeniza por whitespace** | `text_normalizer.py:146` `cleaned.split()` | CJK = 1 token = 1 legenda gigante |
| 21 | **`align_text_to_transcription` não recebe language** | `subtitle_aligner.py:196` sem parâmetro language | Não pode trocar tokenização por idioma |
| 22 | **Whisper CPU fallback não passa language** | `generate.py:582` omite `language=language` | CJK em CPU transcreve como português |
| 23 | **`segments_to_srt` sem word-level quebra CJK** | `generate.py:841` `text.split()` | CJK = 1 token = 1 legenda por segmento |
| 24 | **`voice_path` NOT SET no job #505** | `generation_service.py:189` `if voice_path:` — string vazia é falsy | Worker usou default pt-BR para job zh-CN |
| 25 | **2 code paths para resolução de voz** | `automation_routes.py` não rejeita voz inexistente; `routes.py` rejeita | Auto-fill usa default silenciosamente |
| 26 | **Vozes do sistema não sincronizadas VPS↔worker** | VPS tem `bruno.wav`, worker tem `bruno-slow.wav` | Voz configurada pode não existir no VPS |
| 27 | **Deploy não sincroniza vozes** | `deploy.sh` só sincroniza código | Vozes do sistema divergem entre VPS e worker |
| 28 | **Worker sem env vars multilingual** | systemd service não tem `GPCG_MULTILINGUAL_*` | Kill switch retorna pt-BR para TODOS os jobs — ROOT CAUSE |
| 29 | **Language não sincronizado VPS↔worker** | `local_db_sync.py` e `generation.py` não enviam/setam `language` | VPS DB mostra pt-BR para conteúdo zh-CN |
| 30 | **`mood=` em vez de `music_mood=`** | `content_planning_service.py:389` código duplicado bugado | Jobs curiosity_short falham: "'mood' is an invalid keyword argument" |
| 31 | **Worker não carrega .env** | Worker só lê env vars do systemd, não carrega .env | Qualquer nova config VPS esquecida no worker = bug silencioso |
| 32 | **YouTube upload não seta `defaultLanguage`/`defaultAudioLanguage`** | `youtube.provider.ts:35-40` — snippet sem campos de language | YouTube não sabe que o vídeo é zh-CN; afeta SEO, accessibility, recommendations |
| 33 | **`gameplay_query` cross-lingual pode quebrar semantic search** | `gameplay_query` pode ser zh, event descriptions são en, `nomic-embed-text` cross-lingual zh↔en tem baixa precisão | Semantic search falha → fallback para random clips (visualmente dissociado do roteiro) |
| 34 | **`game_enrichment.py` gera lore em português hardcoded** | `game_enrichment.py:246` — "Resuma em português" | Lore usado como contexto para geração zh-CN vem em pt-BR |
| 35 | **`ContentPlanningService` não adapta prompt** | `content_planning_service.py:160` usa `SYSTEM_PROMPT` pt-BR sem `adapt_system_prompt` | Prompt do LLM fica em pt-BR mesmo para canal zh-CN |

---

## PARTE II — ARQUITETURA ALVO

### 3. Princípios de engenharia

1. **Language-agnostic pipeline logic** — toda lógica de pipeline funciona para qualquer idioma sem código condicional
2. **Language-aware content generation** — todo conteúdo gerado por LLM é no idioma alvo desde o primeiro token
3. **Single source of truth** — `GenerationContext.language` é a autoridade, congelada em artifacts
4. **No late translation** — o script nunca é gerado em pt-BR e traduzido depois
5. **Same subtitle pipeline for all languages** — Whisper + alignment para todos
6. **Model selection by language capability** — CJK usa qwen3:14b, Latin usa llama3.1:8b
7. **PromptRegistry as single prompt source** — todos os prompts carregados por (name, domain, language, version)
8. **Full sync of language fields** — target_language, Script.language, Video.language atravessam worker↔VPS
9. **Fact translation is in-memory only** — Fact.claim e KnowledgeItem.content permanecem no idioma original no DB
10. **Graceful degradation** — se um pack i18n tiver erro, log WARNING + fallback pt-BR (não silencioso)

### 4. Cadeia alvo

```
Fact (pt-BR no DB, traduzido em memória para idioma alvo)
  → ContentPlan (idioma alvo desde a criação, topic/hook no idioma alvo)
    → StoryConcept (idioma alvo)
      → EditorialPlan (idioma alvo, TODOS os campos de texto)
        → CreativeMaterial (idioma alvo)
          → Script draft/optimized/final (idioma alvo)
            → ScriptCritic (idioma alvo, com target_duration)
              → Anti-plágio (idioma alvo, tokenização CJK-aware)
                → Humanization (pt-BR por enquanto, CJK futuro)
                  → TTS (idioma alvo, modelo correto)
                    → Metadata (idioma alvo, modelo correto, fallback localizado)
                      → Legendas (Whisper + alignment, mesmo caminho pt-BR)
                        → Render → Video.language = idioma alvo
                          → Sync → VPS reflete idioma alvo em todos os modelos
```

---

## PARTE III — PLANO DE IMPLEMENTAÇÃO REVISADO

#### FASE 0: Fixes críticos de infraestrutura (BLOCKER — deve vir antes de tudo)

**Objetivo:** Corrigir bugs que bloqueiam TODA a funcionalidade multilíngue. Sem estes fixes, nenhuma outra fase funciona.

| # | Arquivo(s) | Mudança | Risco |
|---|-----------|---------|-------|
| 0.1 | `~/.config/systemd/user/gpcg-worker.service` | Adicionar `GPCG_MULTILINGUAL_ENABLED=true`, `GPCG_MULTILINGUAL_LANGUAGES=pt-BR,en,zh-CN,zh-TW,zh`, `GPCG_MULTILINGUAL_TTS_ENABLED=true`, `GPCG_MULTILINGUAL_PROMPTS_ENABLED=true`, `GPCG_MULTILINGUAL_QA_ENABLED=true` | Baixo — só adiciona env vars |
| 0.2 | `~/.config/systemd/user/gpcg-worker.service` | Adicionar `GPCG_CREATIVE_ENGINE_ENABLED=true`, `GPCG_CONTENT_INTELLIGENCE_ENABLED=true`, `GPCG_LLM_MODEL=gpt-oss:latest` | Baixo |
| 0.3 | `src/gpcg/worker/cli.py` ou `remote_worker.py` | Carregar `~/.config/gpcg/worker.env` via `load_dotenv()` no startup do worker | Médio — mudança arquitetural |
| 0.4 | `src/gpcg/application/content_planning_service.py:389` | `mood=` → `music_mood=` | Baixo — fix óbvio |
| 0.5 | `src/gpcg/application/content_planning_service.py:395` | `metadata=` → `metadata_json=` | Baixo — fix óbvio |
| 0.6 | `src/gpcg/application/content_planning_service.py:392` | Remover `scope=ContentScope.general.value` (campo não existe) | Baixo |
| 0.7 | `src/gpcg/worker/local_db_sync.py:716-730` | Incluir `target_language` no dict de content_plan extraído | Baixo |
| 0.8 | `src/gpcg/worker/local_db_sync.py:747-758` | Incluir `language` no dict de script extraído | Baixo |
| 0.9 | `src/gpcg/worker/local_db_sync.py:763-774` | Incluir `language` no dict de video extraído | Baixo |
| 0.10 | `src/gpcg/api/workers/generation.py:480-494` | Setar `target_language` ao criar ContentPlan no VPS DB | Baixo |
| 0.11 | `src/gpcg/api/workers/generation.py:518-528` | Setar `language` ao criar Script no VPS DB | Baixo |
| 0.12 | `src/gpcg/api/workers/generation.py:548-564` | Setar `language` ao criar Video no VPS DB | Baixo |
| 0.13 | `src/gpcg/application/generation_service.py:189` | `if voice_path is not None:` ao invés de `if voice_path:` | Alto — causa raiz do #505 |
| 0.14 | `src/gpcg/api/automation_routes.py:1177-1186` | Adicionar `else: raise HTTPException` quando voz não existe | Alto — causa raiz do #505 |

**Verificação da Fase 0:**
- Após restart do worker, rodar job zh-CN e verificar:
  - `job.artifacts["generation_context"]["language"] == "zh-CN"` (não pt-BR)
  - `job.artifacts["generation_context"]["tts_language"] == "zh"` (não pt)
  - `job.artifacts["generation_context"]["llm_script_model"]` é qwen3 (não llama3.1)
  - ContentPlan.target_language == "zh-CN" no VPS DB após sync
  - Script.language == "zh-CN" no VPS DB após sync
  - Video.language == "zh-CN" no VPS DB após sync
  - Jobs curiosity_short não falham mais com "'mood' is an invalid keyword argument"
  - `voice_path` é setado nos artifacts quando automação tem voice configurado

### 5. Fases e ordem de implementação (revisada v3)

> **Mudanças vs v2:**
> - Fase 0 adicionada (fixes bloqueadores de infraestrutura)
> - Fase 4 removida (consolidada na Fase 0)
> - Fase 1.5 dividida em 1.5a (metadados), 1.5b (validação), 1.5c (UI)
> - Fase 6 dividida em 6a (metadata+YouTube), 6b (QA), 6c (humanization), 6d (kids)
> - Items 1.5.15-1.5.17 consolidados na Fase 0
> - C32-C35 adicionados
> - Plano de migração, rollback plan, e notas operacionais adicionados

**Ordem de execução:**
```
Fase 0   → Fixes bloqueadores (14 items) — BLOCKER
  ↓ validar: job zh-CN gera em zh-CN (não pt-BR)
Fase 1   → Fundação i18n (9 items)
  ↓ validar: testes passam, packs corretos
Fase 1.5a → Metadados de voz (7 items) — pode rodar em paralelo com Fase 1
Fase 1.5b → Validação voz↔idioma (9 items)
  ↓ validar: upload pede idioma, validação server-side
Fase 2   → PromptRegistry (12 items)
  ↓ validar: job zh-CN gera em chinês sem tradução tardia
Fase 3   → Remover tradução tardia (10 items)
  ↓ validar: script em chinês desde o draft
Fase 5   → Legendas (13 items, repo video-generate separado)
  ↓ validar: legendas acompanham a fala
Fase 6a  → Metadata + YouTube language (5 items)
Fase 6b  → QA (1 item)
  ↓ validar: end-to-end zh-CN + en-US + pt-BR
Fase 7   → Limpeza e testes (10 items)
  ↓ validar: cobertura completa
Fase 1.5c → UI web + mobile (3 items, pode rodar em paralelo)
Fase 6c  → Humanization CJK (postergável)
Fase 6d  → Kids multilíngue (postergável)
```

#### FASE 1: Fundação i18n (sem mudar comportamento runtime)

**Objetivo:** Preparar infraestrutura sem quebrar nada.

| # | Arquivo | Mudança | Risco |
|---|---------|---------|-------|
| 1.1 | `i18n/prompts/zh_cn/games_prompts.py` | Atualizar targets: 3.5→5.5 chars/s, 200-280→280-379 chars | Baixo |
| 1.2 | `i18n/prompts/zh_tw/games_prompts.py` | Idem | Baixo |
| 1.3 | `i18n/prompts/zh_cn/kids_prompts.py` | Idem (se aplicável) | Baixo |
| 1.4 | `i18n/prompts/zh_tw/kids_prompts.py` | Idem | Baixo |
| 1.5 | `core/models.py` | Adicionar coluna `model_preferences: JSON` em `ChannelProfile` via `_ensure_column()` | Baixo |
| 1.6 | `i18n/language_context.py` | `GenerationContext.from_channel_profile` usa `get_recommended_model(language)` quando `model_preferences` não especifica | Baixo |
| 1.7 | `i18n/language_context.py` | `GenerationContext.llm_script_model` setado por idioma em `from_channel_profile` | Baixo |
| 1.8 | `i18n/prompts/registry.py` | Adicionar log WARNING quando fallback para pt-BR ocorre | Baixo |
| 1.9 | `tests/` | Adicionar teste: TODAS as constantes existem em TODOS os packs (sem fallback silencioso) | Baixo |

**Verificação:** Testes existentes (1128) continuam passando. PromptRegistry funciona para todos os pares.

#### FASE 1.5a: Voz — Metadados persistidos (backend)

**Objetivo:** Vozes têm idioma persistido em tabela `Voice`. Upload pede idioma. Listagem lê do DB.

| # | Arquivo(s) | Mudança | Risco |
|---|-----------|---------|-------|
| 1.5a.1 | `core/models.py` | Criar tabela `Voice`: `id`, `user_id` (NULL=system), `filename`, `language`, `display_name`, `file_size`, `created_at`. UniqueConstraint `(user_id, filename)` | Médio (migration) |
| 1.5a.2 | `core/db.py` ou `init_db()` | `_ensure_table("voices")` + seed das vozes do sistema (`bruno-slow.wav`→pt-BR, `brunoamplifier-slow.wav`→pt-BR, `voice-en-native.mp3`→en-US, `voice-zh-native.mp3`→zh-CN) | Baixo |
| 1.5a.3 | `api/routes.py` | `POST /voices/upload` recebe parâmetro `language` (obrigatório). Persiste na tabela `Voice`. `_detect_voice_language` fica como fallback se `language` não for enviado | Médio |
| 1.5a.4 | `api/routes.py` | `GET /voices` lê da tabela `Voice` (não re-infere do filename). Inclui campo `language` e `display_name` | Baixo |
| 1.5a.5 | `api/routes.py` | `DELETE /voices/{filename}` remove também o registro da tabela `Voice` | Baixo |
| 1.5a.6 | `api/routes.py` | Atualizar `_SYSTEM_VOICES` para refletir storage real: adicionar `bruno-slow.wav`, `brunoamplifier-slow.wav` | Baixo |
| 1.5a.7 | `worker/handlers/generation.py` | Ao baixar voz do VPS, também baixar metadados de idioma (ou ler da tabela Voice local) | Baixo |

**Verificação 1.5a:** Upload de voz com `language=zh-CN` persiste corretamente. `GET /voices` retorna idioma correto sem re-inferir.

#### FASE 1.5b: Voz — Validação voz↔idioma e auto-select correto (backend)

**Objetivo:** Auto-select funciona mesmo com voice setado. Validação voz↔idioma na criação do job. Unificação de code paths.

| # | Arquivo(s) | Mudança | Risco |
|---|-----------|---------|-------|
| 1.5b.1 | `api/routes.py` | `_auto_select_voice_for_language`: buscar da tabela `Voice` (owner=NULL/system) ao invés do dict hardcoded | Baixo |
| 1.5b.2 | `api/routes.py` | Na criação do job (generate_short e curiosity_short): se `voice` está setado mas é incompatível com `gen_ctx.language`, fazer override para auto-select. Log WARNING | Médio |
| 1.5b.3 | `api/routes.py` | Adicionar função `_validate_voice_language(voice_filename, target_language, user_id, db)` → retorna voice compatível ou faz auto-select | Médio |
| 1.5b.4 | `api/automation_routes.py` | Aplicar mesma validação voz↔idioma nos endpoints de automação (linhas 1177-1186, 1460-1467) | Médio |
| 1.5b.5 | `api/routes.py` e `api/automation_routes.py` | Unificar resolução de voz em função única `_resolve_voice_path(voice_name, user_id, settings, db)` usada por todos os endpoints | Médio |
| 1.5b.6 | `application/generation_service.py` | Sempre setar `artifacts["voice_language"]` junto com `artifacts["voice_path"]` para auditoria no worker | Baixo |
| 1.5b.7 | `worker/handlers/generation.py` | Log explícito da voz usada: `log.info(f"Job #{job_id}: voice={voice_filename}, language={voice_language}, target_language={gen_ctx.language}")` | Baixo |
| 1.5b.8 | CLI: `gpcg sync-voices` | Novo comando que sincroniza vozes do sistema VPS→worker (baixa vozes do sistema que não existem localmente) | Médio |
| 1.5b.9 | `api/workers/file_transfer.py` | Endpoint `/api/voices/system/list` que lista vozes do sistema com metadados (filename, language, display_name) para o worker sincronizar | Baixo |

**Regras de validação voz↔idioma:**

```
voice.language base == gen_ctx.language base → compatível
  (pt-BR + pt-BR ✓, zh-CN + zh-TW ✓, en-US + en-US ✓)

voice.language base != gen_ctx.language base → incompatível
  (pt-BR + zh-CN ✗ → override para auto-select zh)
```

Casos especiais:
- `zh` + `zh-CN` → compatível (zh é família)
- `zh-CN` + `zh-TW` → compatível (mesma família)
- `pt-BR` + `en-US` → incompatível → override

**Verificação 1.5b:**
- Job zh-CN com `voice=bruno.wav` (pt-BR) na automação → override para `voice-zh-native.mp3`
- Job pt-BR com `voice=bruno-slow.wav` → funciona normalmente
- Log WARNING quando override ocorre
- Vozes do sistema sincronizadas entre VPS e worker via `gpcg sync-voices`

#### FASE 1.5c: Voz — UI web e mobile (frontend)

**Objetivo:** UI de upload pede idioma. UI de seleção mostra idioma e sinaliza incompatibilidade.

| # | Arquivo(s) | Mudança | Risco |
|---|-----------|---------|-------|
| 1.5c.1 | `frontend/src/pages/automation.tsx` | UI de upload de voz pede idioma (dropdown: pt-BR, en-US, zh-CN, zh-TW, zh) | Médio |
| 1.5c.2 | `frontend/src/pages/automation.tsx` | UI de seleção de voz mostra idioma de cada voz e filtra/sinaliza incompatibilidade com target_language do canal | Médio |
| 1.5c.3 | `mobile/src/screens/AutomationScreen.tsx` | Mesmas mudanças no mobile | Médio |

**Verificação 1.5c:** UI mostra idioma de cada voz. Upload pede idioma. Seleção sinaliza incompatibilidade.

> **NOTA:** Os items 1.5.15-1.5.17 da v1 (`voice_path` fix, `else: raise HTTPException`) foram consolidados na **Fase 0** (items 0.13-0.14) por serem fixes bloqueadores.

#### FASE 2: Ativar PromptRegistry em produção (com rede de segurança)

**Objetivo:** Todos os serviços carregam prompts do registry por idioma. Traduções tardias ainda existem como rede de segurança.

| # | Arquivo(s) | Mudança | Risco |
|---|-----------|---------|-------|
| 2.1 | `application/script_service.py` | Trocar imports de `gpcg.domains.games.prompts` por `PromptRegistry.get()` | Médio |
| 2.2 | `application/script_critic.py` | Idem | Médio |
| 2.3 | `application/editorial_planner.py` | Idem | Médio |
| 2.4 | `application/creative_engine.py` | Idem | Médio |
| 2.5 | `application/story_finder.py` | Idem | Baixo |
| 2.6 | `application/curiosity_scorer.py` | Idem | Baixo |
| 2.7 | `application/content_planning_service.py` | Idem + aplicar `language_context` na chamada LLM (line 160 — hoje não aplica) | Médio |
| 2.8 | `application/metadata_generator.py` | Idem | Baixo |
| 2.9 | `application/fact_service.py` | Idem (mas Fact extraction continua pt-BR — fact é traduzido em memória depois) | Baixo |
| 2.10 | `application/script_service.py` | Corrigir expansão no fluxo normal (line 373) — usar prompt do idioma alvo via PromptRegistry | Médio |
| 2.11 | `application/generation_service.py` | Creative engine recebe `gen_ctx` do pipeline (não recria internamente line 1696) | Baixo |
| 2.12 | `application/generation_service.py` | Localizar context line "Curiosidade geral" (line 1709) | Baixo |

**NÃO remover ainda:** `_translate_creative_material`, `_translate_story_concept`, `_translate_creative_plan`, `_translate_script` — permanecem como rede de segurança.

**Verificação:** Rodar job zh-CN de teste. Verificar SE o conteúdo já vem no idioma alvo (sem precisar da tradução tardia). Se sim, prosseguir para Fase 3. Se não, investigar packs antes de remover rede de segurança.

#### FASE 3: Geração nativa — remover tradução tardia (após validar Fase 2)

**Objetivo:** Eliminar tradução tardia. Script gerado no idioma alvo desde o draft.

| # | Arquivo(s) | Mudança | Risco |
|---|-----------|---------|-------|
| 3.1 | `application/generation_service.py` | Traduzir `fact_text` em memória antes de passar para ContentPlanning (se não-pt-BR). **NÃO persistir tradução no Fact.** | Médio |
| 3.2 | `application/generation_service.py` | Remover `_translate_creative_material` (linha 1785) — conteúdo já vem no idioma alvo via PromptRegistry | Alto |
| 3.3 | `application/generation_service.py` | Remover `_translate_story_concept` (linha 1797) | Alto |
| 3.4 | `application/generation_service.py` | Remover `_translate_creative_plan` (linha 1810) | Alto |
| 3.5 | `application/script_service.py` | Remover `_translate_script` do fluxo normal (lines 455-461) | Alto |
| 3.6 | `application/script_service.py` | `_translate_script` pode ser mantido como método utilitário para tradução de fact (3.1) | Médio |
| 3.7 | `domain/originality.py` | **PRÉ-REQUISITO:** Adicionar tokenização CJK em `_tokenize` (por caractere ou bigrama para zh/ja/ko). Ajustar n-gram size para CJK | Médio |
| 3.8 | `application/script_service.py` | `check_originality` agora roda no idioma final com tokenização correta | Médio |
| 3.9 | `application/script_critic.py` | Passar `target_duration`, `lang_min`, `lang_max` para `_build_prompt` e `_build_section_prompt` | Médio |
| 3.10 | `application/editorial_planner.py` | Garantir que `gameplay_query` é **sempre em inglês** (vocabulário semântico independente de idioma — ver C33). Tags de ação: "explosion", "racing", "combat". Instruir o LLM no prompt a gerar `gameplay_query` em inglês independente do `target_language` | Médio |

**Verificação:** Job zh-CN gera script em chinês desde o draft. `check_originality` funciona para CJK. ScriptCritic avalia duração.

> **NOTA:** A Fase 4 (Sync completa de idioma) da v1 foi **removida** — seu conteúdo (items 4.1-4.5) foi consolidado na **Fase 0** (items 0.7-0.12) por serem fixes bloqueadores de sync VPS↔worker. O item 4.5 (eliminar fallback `order_by(Script.id.desc())`) já foi implementado em commit anterior.

#### FASE 5: Legendas — restaurar caminho original + fix CJK

**Objetivo:** Remover puxadinho que quebra TODAS as legendas. Restaurar Whisper + SequenceAligner (caminho original que funcionava para pt-BR). Adicionar tokenização CJK para que o mesmo caminho funcione para chinês.

> **Contexto do engenheiro de legendas:** O pipeline original (commit 8236e70) funcionava assim: Whisper transcreve o áudio → timestamps reais → SequenceAligner substitui texto do Whisper pelo texto do roteiro (master source) mantendo timestamps → `segments_to_srt` converte para SRT → `convert_srt_to_drawtext` gera filtros FFmpeg. Este caminho produzia legendas que acompanhavam exatamente a fala. O puxadinho `_build_subtitle_segments` (uncommitted) bypassou este caminho inteiro para todos os idiomas, substituindo timestamps reais por timing proporcional fake.

| # | Arquivo(s) | Mudança | Risco |
|---|-----------|---------|-------|
| 5.1 | `infrastructure/video_generate_adapter.py` | **REVERTER** `subtitle_mapping` para `{"tts_text": text, "expansions": []}` (sem segments). Remover `_build_subtitle_segments`. | Médio — restaura caminho original |
| 5.2 | `video-generate/src/processors/alignment/text_normalizer.py` | Adicionar tokenização CJK em `normalize()`: para zh/ja/ko, tokenizar por caractere ao invés de `cleaned.split()`. Manter whitespace para Latin. | Médio — core do alinhamento |
| 5.3 | `video-generate/src/processors/alignment/text_normalizer.py` | `_split_original_words` em `sequence_aligner.py:467` espelha a tokenização — atualizar para CJK também | Médio |
| 5.4 | `video-generate/src/processors/subtitle_aligner.py` | Adicionar parâmetro `language` em `align_text_to_transcription()` e `SubtitleAligner.align_segments()`. Passar para `TextNormalizer` | Baixo |
| 5.5 | `video-generate/generate.py:714` | Passar `language=language` para `align_text_to_transcription()` | Baixo |
| 5.6 | `video-generate/generate.py:582` | CPU fallback passa `language=language` para `_whisper_transcribe_core()` | Baixo |
| 5.7 | `video-generate/generate.py:582` | CPU fallback também aplica `apply_subtitle_mapping()` (hoje é pulado) | Baixo |
| 5.8 | `video-generate/generate.py:841` | `segments_to_srt` sem word-level: para CJK, quebrar por contagem de caracteres ao invés de `text.split()`. Distribuir timing dentro do segmento | Médio |
| 5.9 | `video-generate/generate.py:810` | `segments_to_srt` com word-level: para CJK, `word_text.endswith((".", "!", "?"))` deve incluir pontuação CJK `。！？` | Baixo |
| 5.10 | `video-generate/generate.py:918` | SRT regex parser `convert_srt_to_drawtext`: regex já usa `re.DOTALL` → funciona para CJK. Verificar | Baixo |
| 5.11 | `video-generate/src/profiles/subtitle_renderer.py` | `wrap_text` CJK: quebrar por `max_chars` mas respeitar pontuação CJK `。！？、` como pontos de quebra preferenciais | Baixo |
| 5.12 | `video-generate/src/profiles/subtitle_renderer.py` | `generate_drawtext_filter`: já escapa `\n` e `:` (lines 163-167). Verificar que funciona para CJK | Baixo |
| 5.13 | `video-generate/generate.py:308` | `apply_subtitle_mapping`: para CJK, `text.split()` retorna 1 token → no-op (sem expansões para CJK). Adicionar early return se language é CJK | Baixo |

**Arquitetura do caminho restaurado:**

```
synthesize_tts()
  → subtitle_mapping = {"tts_text": text, "expansions": []}  (SEM segments)
  → narration.wav

generate_auto_srt(audio, original_text, subtitle_mapping, language)
  → subtitle_mapping sem segments → cai para Whisper
  → transcribe_and_align_audio(audio, original_text, subtitle_mapping, language)
    → _whisper_transcribe_core(audio, device, original_text, language=language)
      → Whisper transcreve áudio → timestamps REAIS
      → initial_prompt por idioma (já implementado)
    → apply_subtitle_mapping(result, subtitle_mapping)  (expansions para pt-BR, no-op para CJK)
    → align_text_to_transcription(original_text, segments, language=language)
      → TextNormalizer tokeniza por caractere para CJK, whitespace para Latin
      → SequenceAligner alinha texto do roteiro com timestamps do Whisper
      → Retorna segments com texto correto + timing real
  → segments_to_srt(segments, profile, language)
    → CJK: quebra por caracteres, pontuação CJK
    → Latin: quebra por palavras, pontuação ASCII
  → SRT file

convert_srt_to_drawtext(srt_file, profile, language)
  → SubtitleRenderer.generate_drawtext_filter(text, start, end, profile, language)
    → wrap_text: CJK-aware wrapping
    → drawtext filter com escaping correto
```

**Verificação:**
- Job pt-BR: legendas acompanham a fala exatamente como antes do puxadinho (caminho original restaurado)
- Job zh-CN: Whisper transcreve com prompt chinês → SequenceAligner alinha por caractere → legendas com timing real
- Job zh-CN em CPU fallback: Whisper usa `language=zh` (não `pt`)
- Sem word-level timestamps (sem WhisperX): CJK quebra por caracteres dentro do segmento
- Com word-level timestamps (WhisperX): CJK usa timestamps por palavra/caractere
- `apply_subtitle_mapping` é no-op para CJK (sem expansões)
- Drawtext escaping funciona para CJK (já implementado)

#### FASE 6a: Metadata + YouTube language (pode rodar após Fase 3)

**Objetivo:** Metadata consistente no idioma alvo. YouTube recebe `defaultLanguage`/`defaultAudioLanguage`.

| # | Arquivo(s) | Mudança | Risco |
|---|-----------|---------|-------|
| 6a.1 | `application/metadata_generator.py` | Remover `_force_translate_title` (puxadinho). Título gerado no idioma alvo via PromptRegistry + modelo correto | Médio |
| 6a.2 | `application/metadata_generator.py` | Fallback chain: `plan.topic` → LLM → fallback localizado por idioma (`"游戏趣闻"`/`"Gameplay Curiosities"`/`"Gameplay Curiosidade"`) | Baixo |
| 6a.3 | `google-integration/apps/api/src/modules/providers/youtube/youtube.provider.ts` | Adicionar `defaultLanguage` e `defaultAudioLanguage` ao snippet (ver C32). Ler de `metadata.language` | Médio |
| 6a.4 | `gpcg/infrastructure/google_integration_adapter.py` | Passar `language` do `gen_ctx.language` ao chamar upload. Normalizar BCP-47 (`zh` → `zh-Hans`) | Médio |
| 6a.5 | `gpcg/application/presentation_service.py` ou `generation_service.py` | Incluir `language` no metadata enviado ao google-integration | Baixo |

**Verificação 6a:** Metadata em zh-CN tem título E descrição em chinês. YouTube upload recebe `defaultLanguage=zh-CN` e `defaultAudioLanguage=zh-CN`.

#### FASE 6b: QA — validação de consistência de idioma (pode rodar após 6a)

**Objetivo:** QA valida que o script está no idioma declarado.

| # | Arquivo(s) | Mudança | Risco |
|---|-----------|---------|-------|
| 6b.1 | `application/qa_service.py` | Adicionar validação de consistência de idioma (script vs language declarada). Se script não está no idioma declarado, flag como issue | Médio |

**Verificação 6b:** QA flaga script em pt-BR para job zh-CN como inconsistência.

#### FASE 6c: Humanization CJK (subprojeto separado, postergável)

**Objetivo:** Humanization funciona para CJK com patterns de AI-ism em chinês.

| # | Arquivo(s) | Mudança | Risco |
|---|-----------|---------|-------|
| 6c.1 | `application/humanization.py` | Criar patterns CJK de AI-ism + prompt CJK + frases CJK de identificação. Habilitar humanization para zh | Alto |

> **Postergável:** Humanization CJK requer pesquisa de patterns de AI-ism em chinês. Pode ser postergado — CJK fica sem humanization no curto prazo (como hoje).

#### FASE 6d: Kids multilíngue (subprojeto separado, postergável)

**Objetivo:** Kids pipeline gera em zh-CN.

| # | Arquivo(s) | Mudança | Risco |
|---|-----------|---------|-------|
| 6d.1 | `domains/kids/pipeline.py` | Injetar `GenerationContext`, integrar PromptRegistry, aplicar `language_context` em todas as chamadas LLM | Alto |
| 6d.2 | `domains/kids/pipeline.py` | Usar `get_chars_per_second(language)` ao invés de hardcoded 14 | Baixo |
| 6d.3 | `domains/kids/pipeline.py` | Remover fallbacks hardcoded pt-BR ("Sabia que...", "Write in pt-BR") | Médio |
| 6d.4 | `domains/kids/topic_library.py` | Internacionalizar ou carregar por idioma (escopo grande) | Alto |
| 6d.5 | `domains/kids/seasonal_calendar.py` | Internacionalizar ou carregar por idioma (escopo grande) | Alto |

> **Postergável:** Kids multilíngue é um subprojeto separado. `KidsGenerationService` é um pipeline completamente separado (663 linhas, não herda de `GenerationService`). Pode ficar pt-BR-only por enquanto.

#### FASE 7: Limpeza e testes

**Objetivo:** Remover código morto, adicionar testes de integração.

| # | Arquivo(s) | Mudança | Risco |
|---|-----------|---------|-------|
| 7.1 | `i18n/prompt_adapter.py` | `adapt_system_prompt` vira fallback apenas (não mais mecanismo primário) | Baixo |
| 7.2 | `application/script_service.py` | Remover `_translate_script` se não for mais usado como utilitário | Baixo |
| 7.3 | `infrastructure/video_generate_adapter.py` | Confirmar remoção de `_build_subtitle_segments` | Baixo |
| 7.4 | `application/metadata_generator.py` | Confirmar remoção de `_force_translate_title` | Baixo |
| 7.5 | `tests/test_multilingual.py` | Expandir: testar geração nativa zh-CN sem tradução tardia | Médio |
| 7.6 | `tests/` | Teste de integração: job zh-CN end-to-end, verifica Script.language, Video.language, metadata em chinês | Médio |
| 7.7 | `tests/` | Teste: PromptRegistry carregado em todos os serviços | Médio |
| 7.8 | `tests/` | Teste: sync worker→VPS propaga language em todos os modelos | Médio |
| 7.9 | `tests/` | Teste: originality checker funciona para CJK (tokenização por caractere) | Médio |
| 7.10 | `tests/` | Teste: fallback de PromptRegistry loga WARNING | Baixo |

---

## PARTE IV — PUXADINHOS A SEREM REMOVIDOS

| # | Puxadinho | Onde | Substituído por | Fase |
|---|-----------|------|-----------------|------|
| 1 | `_build_subtitle_segments` (timing proporcional) | `video_generate_adapter.py` | Reverter para `{"tts_text": text, "expansions": []}` + Whisper + SequenceAligner CJK-aware | 5.1 |
| 2 | `_force_translate_title` (tradução forçada) | `metadata_generator.py` | Geração nativa via PromptRegistry + qwen3 | 6.1 |
| 3 | `_translate_script` (tradução tardia do script) | `script_service.py` | Geração nativa no idioma alvo | 3.5 |
| 4 | `_translate_creative_material` (tradução tardia) | `generation_service.py` | Geração nativa via PromptRegistry | 3.2 |
| 5 | `_translate_story_concept` (tradução tardia) | `generation_service.py` | Geração nativa via PromptRegistry | 3.3 |
| 6 | `_translate_creative_plan` (tradução tardia) | `generation_service.py` | Geração nativa via PromptRegistry | 3.4 |
| 7 | `adapt_system_prompt` como mecanismo primário | `prompt_adapter.py` | PromptRegistry com packs nativos | 7.1 |
| 8 | `order_by(Script.id.desc())` no sync | `local_db_sync.py` | Sempre usar `script_id` de artifacts | 4.5 |
| 9 | `get_recommended_model` aplicado pontualmente | `script_service.py`, `script_critic.py`, `metadata_generator.py` | Integrado em `GenerationContext` | 1.6 |
| 10 | Estimativa de duração `word_count / 150` | `generation_service.py` | `char_count / get_chars_per_second(language)` (já corrigido) | — |

---

## PARTE V — RISCOS E MITIGAÇÕES

| Risco | Probabilidade | Impacto | Mitigação |
|-------|--------------|---------|-----------|
| Qwen3:14b é mais lento (9.3 GB vs 4.9 GB) | Certo | Médio | Aceitar latência maior para CJK. Documentar trade-off |
| Packs i18n podem ter erros de tradução | Média | Médio | Fase 2 mantém rede de segurança. Teste 1.9 verifica todas as constantes |
| Remover tradução tardia quebra jobs existentes | Baixa | Alto | `is_compatible_with` invalida checkpoints CJK (correto). Jobs pt-BR não afetados |
| Originality checker CJK ainda não implementado | Certo | Alto | Fase 3.7 é pré-requisito para remover tradução tardia |
| Kids topic_library internacionalizada é escopo grande | Certo | Alto | Fase 6d.4-6d.5 pode ser postergada. Kids pode ficar pt-BR-only por enquanto |
| Humanization CJK requer pesquisa de patterns | Certo | Médio | Fase 6c é subprojeto separado. CJK fica sem humanization no curto prazo |
| Whisper pode não ter modelo para zh | Baixa | Médio | Whisper medium suporta zh. Validar antes |
| Migration de DB (model_preferences) | Baixa | Baixo | `_ensure_column()` já usado pelo GPCG |
| Fallback silencioso do PromptRegistry | Média | Médio | Fase 1.8 adiciona log WARNING. Teste 1.9 verifica constantes |
| `voice_path` vazio nos artifacts | Alta | Alto | Fase 0.13 fixa `if voice_path is not None:` + Fase 0.14 rejeita voz inexistente |
| Vozes do sistema divergem VPS↔worker | Alta | Médio | Fase 1.5b.8 adiciona `gpcg sync-voices` |
| `bruno-slow.wav` não existe no VPS | Certo | Médio | Upload da voz para o VPS ou migrar para o worker via sync-voices |
| Worker sem env vars multilingual | Certo | CRÍTICO | Fase 0.1-0.2 adiciona env vars ao systemd service |
| `mood=` bug em curiosity_short | Certo | Alto | Fase 0.4-0.6 corrige código duplicado |
| Language não sincronizado VPS↔worker | Certo | Alto | Fase 0.7-0.12 corrige sync payload e VPS criação |
| YouTube sem `defaultLanguage`/`defaultAudioLanguage` | Certo | Médio | Fase 6a.3-6a.5 adiciona campos ao snippet |
| `gameplay_query` cross-lingual quebra semantic search | Média | Médio | Fase 3.10 força `gameplay_query` em inglês (vocabulário semântico) |
| `game_enrichment` lore em pt-BR hardcoded | Certo | Baixo | Postergado para Fase 6. `language_directive` compensa |
| `ContentPlanningService` não adapta prompt | Certo | Alto | Fase 2.7 troca `SYSTEM_PROMPT` por `PromptRegistry.get()` |

---

## PARTE Vb — MIGRAÇÃO, ROLLBACK E NOTAS OPERACIONAIS

### Migração de dados existentes (User 4 — jobs zh-CN gerados em pt-BR)

**Estado atual confirmado no VPS DB:**
- User 4 tem 5 jobs completed (#427, #501, #505, #507, #511) para canal zh-CN
- Todos foram gerados em pt-BR (root cause: C28 — worker sem env vars multilingual)
- ContentPlan.target_language = "pt-BR" (INCORRETO)
- Script.language = "pt-BR" (INCORRETO)
- Video.language = "pt-BR" (INCORRETO)
- Script.final em português (conteúdo incorreto para canal zh-CN)
- Alguns podem estar publicados no YouTube

**Plano de migração:**

1. **Identificar jobs afetados:**
   ```sql
   SELECT j.id, j.status, j.created_at, j.artifacts
   FROM jobs j
   JOIN channel_profiles cp ON cp.user_id = j.user_id
   WHERE cp.target_language != 'pt-BR'
     AND j.status = 'completed';
   ```
   Verificar `artifacts["generation_context"]["language"]` vs `cp.target_language`.

2. **Verificar YouTube:**
   ```sql
   SELECT id, youtube_url, youtube_video_id FROM videos WHERE job_id IN (<afetados>);
   ```
   Se `youtube_video_id` não é NULL, o vídeo está publicado.

3. **Decisão por job:**
   - **Não publicado no YouTube:** Marcar como legado. Não reprocessar. O vídeo existe localmente mas não foi publicado.
   - **Publicado no YouTube (private):** Reprocessar após Fase 0. O vídeo novo substitui o antigo.
   - **Publicado no YouTube (public):** **Decisão do usuário** — reprocessar e substituir, ou manter como legado e criar novo.

4. **Correção de DB (opcional, após Fase 0):**
   - Para jobs que NÃO serão reprocessados: atualizar `ContentPlan.target_language`, `Script.language`, `Video.language` para refletir o idioma real do conteúdo (pt-BR, já que foi gerado em pt-BR).
   - Isto é correção de metadados, não reprocessamento.
   - ```sql
     UPDATE content_plans SET target_language = 'pt-BR' WHERE id IN (<afetados>);
     UPDATE scripts SET language = 'pt-BR' WHERE content_plan_id IN (<afetados>);
     UPDATE videos SET language = 'pt-BR' WHERE job_id IN (<afetados>);
     ```
   - Adicionar `metadata_json["legacy_wrong_language"] = true` para auditoria.

5. **Reprocessamento (após Fase 0 completa):**
   - Requeue jobs afetados via API ou DB.
   - `is_compatible_with` invalida checkpoints CJK existentes (modelo muda de llama3.1 → qwen3).
   - Jobs são regenerados do zero com `generation_context` correto.

### Rollback plan por fase

| Fase | Como reverter | Impacto do rollback |
|------|--------------|---------------------|
| **Fase 0** | Reverter env vars do systemd service (`GPCG_MULTILINGUAL_ENABLED=false`) + redeploy VPS | Volta ao estado anterior (pt-BR para tudo). Jobs em queue podem falhar se dependem de fixes de `mood=` ou `voice_path`. |
| **Fase 1** | Kill switch `GPCG_MULTILINGUAL_ENABLED=false` reverte `GenerationContext` para pt-BR default | Sem impacto — Fase 1 não muda runtime, só prepara infraestrutura |
| **Fase 1.5a/b** | Reverter migration da tabela `Voice` (drop table). Auto-select volta a usar `_SYSTEM_VOICES` dict | Vozes perdem metadados persistidos. Validação voz↔idioma desativada. |
| **Fase 1.5c** | Reverter frontend | UI volta ao estado anterior |
| **Fase 2** | PromptRegistry fallback para pt-BR reverte todos os prompts | Conteúdo volta a ser gerado em pt-BR. Tradução tardia (ainda presente) funciona como rede de segurança. |
| **Fase 3** | Reativar `_translate_creative_material`, `_translate_story_concept`, `_translate_creative_plan`, `_translate_script` (código mantido em git) | Tradução tardia volta a funcionar. Conteúdo pode vir em pt-BR e ser traduzido depois. |
| **Fase 5** | Reverter `subtitle_mapping` para com `_build_subtitle_segments` (código mantido em git) | Legendas voltam a timing proporcional. **NÃO RECOMENDADO** — restaurar caminho Whisper é melhor. |
| **Fase 6a** | Reverter metadata generator e YouTube provider | Metadata volta a ser gerada em pt-BR. YouTube sem `defaultLanguage`. |
| **Fase 6b** | Reverter QA validation | QA para de validar idioma |
| **Fase 6c/d** | Não aplicar (postergável) | Sem impacto |

**Estratégia de rollback geral:**
- O kill switch `GPCG_MULTILINGUAL_ENABLED=false` no worker systemd reverte a maioria das mudanças de runtime.
- Traduções tardias (Fase 3) são mantidas em git e podem ser reativadas.
- O `is_compatible_with` invalida checkpoints quando modelo muda, garantindo que jobs em queue sejam regenerados com o novo contexto.

### Nota: video-generate é um repositório separado

A Fase 5 mexe em `video-generate` (`/home/bruno/Desenvolvimento/brunointegrations/video-generate/`), que é um repositório separado do GPCG.

- O worker usa video-generate via subprocess com path configurado em `VIDEO_GENERATE_DIR`.
- **Não há deploy automático** — o worker local usa o repo local diretamente.
- Mudanças em video-generate requerem commit + push no repo video-generate.
- O `VIDEO_GENERATE_PYTHON` aponta para `.venv/bin/python` do video-generate.
- Após mudanças em video-generate, restart do worker é necessário (não há hot-reload).
- O `ai-media-core` (`AI_MEDIA_CORE_DIR`) é outra dependência local com mesmo padrão.

### Esclarecimento: modelo LLM para CJK

**Estado atual (após Fase 0):**
- Worker systemd tem `GPCG_LLM_MODEL=gpt-oss:latest` (default do VPS .env)
- `GenerationContext.from_channel_profile()` faz override para CJK via `get_recommended_model(language)`
- Para `zh-CN`/`zh-TW`/`zh`: `get_recommended_model()` retorna `qwen3:14b`
- Para `pt-BR`/`en-US`: mantém `gpt-oss:latest` (ou `llama3.1:8b` se configurado)

**Fluxo:**
```
GPCG_LLM_MODEL=gpt-oss:latest (default)
  ↓
GenerationContext.from_channel_profile(profile)
  ↓
if language is CJK:
  llm_script_model = get_recommended_model("zh-CN") → "qwen3:14b"
else:
  llm_script_model = "gpt-oss:latest"
  ↓
gen_ctx.llm_script_model = "qwen3:14b" (para CJK)
  ↓
ScriptService usa gen_ctx.llm_script_model
ScriptCritic usa gen_ctx.llm_script_model
```

**Por que o job #511 tinha `llm_script_model = "llama3.1:8b"`?**
Porque o kill switch (C28) desativou `GenerationContext.from_channel_profile()`, que retorna `cls()` (pt-BR default com `llm_script_model` do config default). Após Fase 0, o kill switch não ativa e o modelo correto é selecionado.

## PARTE VI — CRITÉRIOS DE ACEITAÇÃO

### A. Job zh-CN end-to-end (domínio Games) deve produzir:

1. ✅ `ContentPlan.target_language = "zh-CN"` no VPS
2. ✅ `ContentPlan.topic` e `hook` em chinês
3. ✅ `StoryConcept` em chinês (sem tradução tardia)
4. ✅ `EditorialPlan` em chinês, com `gameplay_query` em inglês (vocabulário semântico — ver C33)
5. ✅ `CreativeMaterial` em chinês (sem tradução tardia)
6. ✅ `Script.draft`, `Script.optimized`, `Script.final` em chinês desde a geração
7. ✅ `Script.language = "zh-CN"` no VPS
8. ✅ `Script.char_count` reflete chars chineses (280-379 para 60s)
9. ✅ ScriptCritic avalia no idioma alvo com conhecimento de target_duration
10. ✅ Anti-plágio roda no idioma final com tokenização CJK-aware
11. ✅ TTS usa qwen3:14b para geração e voz mandarim
12. ✅ Metadata: título E descrição em chinês, tags podem ser inglês
13. ✅ Legendas: Whisper com prompt chinês, alinhamento CJK-aware, cobertura completa
14. ✅ `Video.language = "zh-CN"` no VPS
15. ✅ Duração do vídeo respeita target_duration (±30%)
16. ✅ Mesmo caminho de legendas que pt-BR (Whisper + SequenceAligner, não proporcional)
17. ✅ Legendas têm timing extraído do áudio real (Whisper), não proporcional
18. ✅ SequenceAligner alinha texto do roteiro com timestamps do Whisper (CJK: por caractere)
19. ✅ Legendas acompanham a fala em tempo real (não timing fake proporcional)
20. ✅ Whisper CPU fallback usa `language=zh` (não `pt`) para áudio CJK
21. ✅ `Fact.claim` permanece em pt-BR no DB (tradução foi em memória apenas)
22. ✅ YouTube upload recebe `defaultLanguage=zh-CN` e `defaultAudioLanguage=zh-CN`

### B. Job en-US end-to-end (domínio Games) deve produzir:

1. ✅ `ContentPlan.target_language = "en-US"` no VPS
2. ✅ `ContentPlan.topic` e `hook` em inglês
3. ✅ `Script.language = "en-US"` no VPS
4. ✅ `Video.language = "en-US"` no VPS
5. ✅ TTS usa voz `voice-en-native.mp3` com `language=en`
6. ✅ Legendas: Whisper com prompt inglês, `language=en`
7. ✅ YouTube upload recebe `defaultLanguage=en-US` e `defaultAudioLanguage=en-US`
8. ✅ Modelo LLM: `gpt-oss:latest` (default, sem override para en-US)

### C. Voz e validação voz↔idioma:

1. ✅ Voz selecionada é compatível com idioma alvo (voice-zh-native.mp3 para zh-CN)
2. ✅ Se automação tem voz pt-BR e job é zh-CN, override para voz zh-CN com log WARNING
3. ✅ Upload de voz pede idioma e persiste na tabela `Voice`
4. ✅ `GET /voices` retorna idioma persistido (não re-infere do filename)
5. ✅ `voice_path` é SEMPRE setado nos artifacts quando automação tem voice configurado
6. ✅ Se voz não existe no VPS, job é rejeitado com erro claro (não usa default silenciosamente)
7. ✅ `artifacts["voice_language"]` é setado para auditoria no worker
8. ✅ Worker loga voz usada + idioma alvo no início do TTS
9. ✅ Vozes do sistema são sincronizadas entre VPS e worker via `gpcg sync-voices`

### D. Infraestrutura VPS↔Worker deve estar correta:

1. ✅ Worker systemd service tem `GPCG_MULTILINGUAL_ENABLED=true`
2. ✅ Worker systemd service tem `GPCG_MULTILINGUAL_LANGUAGES=pt-BR,en,zh-CN,zh-TW,zh`
3. ✅ Worker carrega `.env` local (ou tem todas env vars necessárias)
4. ✅ `GenerationContext.from_channel_profile()` retorna zh-CN para User 4 (não pt-BR)
5. ✅ ContentPlan.target_language é sincronizado corretamente VPS↔worker
6. ✅ Script.language é sincronizado corretamente VPS↔worker
7. ✅ Video.language é sincronizado corretamente VPS↔worker
8. ✅ Jobs curiosity_short não falham com "'mood' is an invalid keyword argument"
9. ✅ `voice_path` é SEMPRE setado nos artifacts quando voice está configurado
10. ✅ `generation_context` está presente nos artifacts antes do worker iniciar
11. ✅ `generation_context.language` persiste após sync VPS↔worker

### E. Job pt-BR deve continuar funcionando (regressão):

1. ✅ Comportamento idêntico ao atual
2. ✅ Packs pt_br re-exportam os mesmos prompts
3. ✅ Modelo LLM default (gpt-oss:latest ou llama3.1:8b) continua sendo usado
4. ✅ Legendas via Whisper com prompt pt-BR
5. ✅ Todos os testes existentes passam
6. ✅ Legendas pt-BR acompanham a fala exatamente como antes do puxadinho (caminho Whisper + SequenceAligner restaurado)
7. ✅ Timing das legendas pt-BR vem do áudio real (Whisper), não proporcional

### F. Migração de dados existentes:

1. ✅ Jobs afetados do User 4 (#427, #501, #505, #507, #511) identificados
2. ✅ Status no YouTube verificado (publicado vs não publicado)
3. ✅ Jobs não publicados: marcados como legado ou reprocessados
4. ✅ Jobs publicados: decisão do usuário aplicada (reprocessar ou manter)
5. ✅ DB corrigido para jobs legados (language reflete idioma real do conteúdo)

---

## PARTE VII — CONHECIMENTO DE REFERÊNCIA

### 8. Modelos LLM

| Modelo | Tamanho | CJK | Uso alvo |
|--------|---------|-----|----------|
| `llama3.1:8b` | 4.9 GB | Fraco | pt-BR, en-US |
| `qwen3:14b` | 9.3 GB | Nativo | zh-CN, zh-TW, zh, ja, ko |
| `gemma3:12b` | 8.1 GB | Razoável | Fallback |

### 9. Métricas calibradas

| Idioma | chars/seg | Target 60s | Fonte |
|--------|-----------|------------|-------|
| pt-BR | 13.0 | 663-897 | Config original |
| en-US | 15.0 | 765-1035 | Config original |
| zh-CN | 5.5 | 280-379 | Medido: 205 chars / 37.4s |

### 10. Estrutura de arquivos i18n

```
src/gpcg/i18n/
├── language_context.py          # LanguageContext, GenerationContext, get_chars_per_second
├── prompt_adapter.py            # adapt_system_prompt (band-aid, será deprecado)
└── prompts/
    ├── registry.py              # PromptRegistry (não usado em produção — será ativado)
    ├── pt_br/                   # 11 linhas (re-exporta domains.games.prompts)
    ├── en_us/                   # 812 linhas (games) + 524 (kids)
    ├── zh_cn/                   # 807 linhas (games) + 516 (kids) — targets desatualizados
    └── zh_tw/                   # 808 linhas (games) + 530 (kids) — targets desatualizados
```

### 11. Pontos de sync worker↔VPS (4 pontos, todos perdem idioma)

```
1. get_job_data          (api/workers/generation.py)     → não envia target_language/language
2. populate_local_db     (worker/local_db_sync.py)       → não seta target_language/language
3. run_generation_locally (worker/local_db_sync.py)      → não extrai language de script/video
4. sync_job_result       (api/workers/generation.py)     → não seta language em script/video
```

### 12. Checkpoint invalidation

`is_compatible_with` (language_context.py:299-318) invalida checkpoints quando mudam:
- `language`
- `prompt_version`
- `tts_engine_version`
- `llm_script_model`

**Consequência:** Mudar modelo de `llama3.1:8b` para `qwen3:14b` para CJK invalida TODOS os checkpoints CJK existentes. Jobs pt-BR não são afetados. Job #505 será regenerado do zero (esperado e desejado).

### 13. Job #511 — caso de estudo (dados reais do DB, atualizado)

- Job: `#511`, tipo: `curiosity_short`, User: `4`, status: `completed`
- `generation_context.language`: `pt-BR` (INCORRETO — deveria ser zh-CN)
- `generation_context.tts_language`: `pt` (INCORRETO — deveria ser zh)
- `generation_context.llm_script_model`: `llama3.1:8b` (INCORRETO — deveria ser qwen3:14b)
- `voice_path`: `/app/data/voices/voice-zh-native.mp3` (correto, mas contexto errado)
- Video.language no DB: `pt-BR` (INCORRETO)
- Script.final no VPS: português (INCORRETO — "Você já ouviu as últimas vazamentos sobre o GTA 6?...")
- ContentPlan.target_language: `pt-BR` (INCORRETO)
- Gameplay sources: `[200, 201, 205, 208]` (públicas, fallback)

**Root cause:** Worker systemd service não tem `GPCG_MULTILINGUAL_ENABLED=true` (C28). Kill switch em `language_context.py:182` retorna `cls()` (pt-BR default) para TODOS os jobs.

**Todos os jobs completos do User 4 tem o mesmo problema:**
- #427, #501, #505, #507, #511 — todos com `generation_context.language = "pt-BR"`

**Jobs falhando por bug de `mood=` (C30):**
- #575, #568 — "'mood' is an invalid keyword argument for ContentPlan"

### 14. Vozes disponíveis no storage

```
/media/bruno/ToshibaHD/gpcg/data/voices/
├── bruno-slow.wav              (pt-BR, 52MB) — voz principal do Bruno
├── brunoamplifier-slow.wav     (pt-BR, 9MB)  — voz amplificada
├── voice-en-native.mp3         (en-US, 1.7MB) — voz nativa inglesa
├── voice-zh-native.mp3         (zh-CN, 3.3MB) — voz nativa chinesa (普通话)
├── user_2/bruno.wav            (pt-BR, 6MB)  — cópia do user_2
└── user_4/voice-zh-native.mp3  (zh-CN, 3.3MB) — cópia do user_4 (mesmo MD5 do sistema)
```

Referências fornecidas pelo usuário:
- `/home/bruno/Downloads/chines.mp3` → mesmo MD5 de `voice-zh-native.mp3` (zh-CN)
- `/home/bruno/Downloads/ingles.mp3` → mesmo de `voice-en-native.mp3` (en-US)

### 15. Arquitetura XTTS — voz e idioma são parâmetros separados

O XTTS v2 recebe:
- `speaker_wav` — arquivo de referência para clonar a voz
- `language` — código de idioma para guiar a síntese (`pt`, `en`, `zh-cn`, etc.)

Estes são **independentes**. O XTTS clona o timbre/prosódia do `speaker_wav` mas usa o `language` para pronúncia. Se houver incompatibilidade (voz pt-BR + idioma zh), o resultado é ruim — o modelo tenta pronunciar chinês com a entonação de português.

**Conclusão:** A validação voz↔idioma deve acontecer antes de chamar o XTTS. O XTTS não rejeita combinações inválidas — ele simplesmente produz áudio de baixa qualidade.

### 16. Pipeline de legendas — caminho original (commit 8236e70)

O pipeline de legendas que funcionava para pt-BR antes do puxadinho:

```
1. synthesize_tts()
   → subtitle_mapping = {"tts_text": text, "expansions": []}  (SEM segments)
   → narration.wav com duração real

2. render_plan_builder.py
   → request_data["original_narration_text"] = script.final
   → request_data["subtitle_mapping"] = {"tts_text": ..., "expansions": []}
   → request_data["language"] = gen_ctx.tts_language  (ISO 639-1: "pt", "zh")

3. process_video_request() → generate_auto_srt()
   → subtitle_mapping SEM segments → cai para Whisper fallback

4. transcribe_and_align_audio()
   → _whisper_transcribe_core(audio, device, language=language)
     → Whisper transcreve áudio → segments com timestamps REAIS
     → initial_prompt por idioma (pt/en/zh)
   → apply_subtitle_mapping(result, subtitle_mapping)
     → Mapeia expansões (ex: "dezenove" → "XIX") para pt-BR
     → No-op para CJK (sem expansões)
   → align_text_to_transcription(original_text, segments)
     → SequenceAligner alinha palavra-por-palavra
     → Texto do roteiro (master source) + timestamps do Whisper
     → Retorna segments com texto correto + timing real

5. segments_to_srt(segments, profile, language)
   → Converte segments para formato SRT
   → Quebra por palavras (Latin) ou caracteres (CJK)
   → Aplica max_chars_per_line e max_duration do profile

6. convert_srt_to_drawtext(srt_file, profile, language)
   → SubtitleRenderer.generate_drawtext_filter()
   → wrap_text: CJK-aware (já implementado)
   → drawtext filter com escaping correto (já implementado)

7. create_video() → FFmpeg aplica drawtext filters
```

**Por que funcionava:** Whisper extrai timestamps do áudio real. SequenceAligner substitui o texto transcrito (que pode ter erros) pelo texto do roteiro (master source), mantendo os timestamps do Whisper. Resultado: legendas com texto perfeito e timing que acompanha a fala.

### 17. Pipeline de legendas — o que o puxadinho quebrou (uncommitted)

```
1. synthesize_tts()
   → _build_subtitle_segments(text, duration, language)
   → subtitle_mapping = {"tts_text": ..., "expansions": [], "segments": [...]}
   → segments com timing PROPORCIONAL por contagem de caracteres

2. generate_auto_srt()
   → subtitle_mapping COM segments → usa diretamente
   → NUNCA cai para Whisper
   → NUNCA chama SequenceAligner
   → Timing é fake (proporcional), não do áudio real

3. segments_to_srt()
   → Para CJK: text.split() retorna 1 token → 1 legenda por segmento
   → Para Latin: quebra por palavras mas com timing fake
```

**Por que quebrou:** O puxadinho bypassa Whisper e SequenceAligner inteiramente. O timing proporcional assume que cada sentença dura tempo proporcional aos seus caracteres, mas TTS não funciona assim — há pausas, variações de ritmo, etc. Resultado: legendas não acompanham a fala.

### 18. Infraestrutura VPS ↔ Worker — mapa completo

```
VPS (Control Plane — /opt/gpcg via Docker)
├── docker-compose.prod.yml
│   ├── api (gpcg-api:latest, port 8787)
│   │   ├── FastAPI + frontend estático
│   │   ├── SQLite: /app/data/gpcg.db (volume gpcg-data)
│   │   ├── Voices: /app/data/voices/ (volume gpcg-data)
│   │   ├── Jobs: /app/data/jobs/
│   │   └── Redis: redis://redis:6379/0
│   ├── catalog (gpcg-api:latest, port 8788)
│   └── redis (redis:7-alpine)
├── trivestia-nginx (reverse proxy, /gpcg/ prefix)
└── bi-api (BI Identity, port 3300, rede bi-net)

Worker local (Compute Plane — /media/bruno/ToshibaHD/gpcg/)
├── data/ (GPCG_DATA_DIR — setado dinamicamente por job)
│   ├── voices/ (vozes do sistema + user_N/)
│   ├── jobs/ (renders temporários)
│   └── gpcg.db (DB local temporário, populado por job)
├── gameplays/ (gameplays baixados do VPS)
├── mapped/ (análises de gameplay)
├── renders/ (vídeos renderizados)
└── outputs/ (outputs finais)
```

### 19. Fluxo de voz VPS → Worker

```
1. Usuário faz upload de voz via POST /api/voices/upload (VPS API)
   → Salva em /app/data/voices/user_{N}/filename (Docker volume)
   → [FUTURO] Persiste metadados na tabela Voice

2. Usuário configura automação com voice=filename (VPS API)
   → Salvo em automations.config.voice

3. Job é criado (VPS API)
   → Resolve voice_name → voice_path absoluto no VPS
   → [BUG ATUAL] Se voz não existe no VPS, voice_path="" silenciosamente
   → [FUTURO] Validação voz↔idioma aqui
   → Store voice_path em job.artifacts

4. Worker claima job (GET /api/jobs/{id}/data)
   → Recebe artifacts com voice_path (caminho absoluto VPS)

5. Worker baixa voz se não existe localmente
   → Extrai filename de voice_path
   → Procura em voices_dir/user_{N}/filename e voices_dir/filename
   → Se não existe: SCP do VPS Docker volume ou HTTP download
   → Salva em voices_dir/user_{N}/filename

6. GenerationService lê voice_path do artifact
   → [BUG ATUAL] Se voice_path não está nos artifacts, usa default
   → Passa para synthesize_tts(voice_path=..., language=gen_ctx.tts_language)

7. XTTS sintetiza com speaker_wav=voice_path e language=tts_language
   → [BUG ATUAL] Se voice_path é default pt-BR e language é zh, som é errado
```

### 20. Vozes no storage — estado atual real

```
VPS (Docker volume gpcg-data/_data/voices/):
├── bruno.wav                    6.1MB  pt-BR  (referenciado por User 2)
├── voice-en-native.mp3          1.7MB  en-US  (sistema)
├── voice-zh-native.mp3          3.3MB  zh-CN  (sistema, referenciado por User 4)
└── user_4/
    └── voz-infantil-aggressive.wav  6.2MB  ???  (User 4)

Worker (/media/bruno/ToshibaHD/gpcg/data/voices/):
├── bruno-slow.wav              52MB   pt-BR  (referenciado por User 1, NÃO existe no VPS)
├── brunoamplifier-slow.wav      9MB   pt-BR  (não referenciado, NÃO existe no VPS)
├── voice-en-native.mp3         1.7MB  en-US  (existe em ambos)
├── voice-zh-native.mp3         3.3MB  zh-CN  (existe em ambos)
├── user_2/
│   └── bruno.wav               6.1MB  pt-BR  (existe no VPS root)
└── user_4/
    └── voice-zh-native.mp3     3.3MB  zh-CN  (cópia local do sistema)
```

**Inconsistências:**
- `bruno-slow.wav` (52MB) existe apenas no worker → User 1 configura esta voz → VPS não encontra → `voice_path=""` → worker usa default
- `bruno.wav` existe no VPS root mas não no worker root (apenas em `user_2/`)
- `voz-infantil-aggressive.wav` existe apenas no VPS → se referenciada, worker baixa sob demanda
- Vozes do sistema (`voice-en-native.mp3`, `voice-zh-native.mp3`) existem em ambos → OK

### 21. Channel profiles e automações — estado atual real

```
Channel Profiles:
  User 1: pt-BR, games
  User 2: pt-BR, games
  User 3: pt-BR, kids
  User 4: zh-CN, games

Automações (campo voice):
  User 1: "bruno-slow.wav"        (pt-BR, existe apenas no worker)
  User 2: "bruno.wav"             (pt-BR, existe no VPS root e worker user_2/)
  User 3: (não setado)            (kids, usa default)
  User 4: "voice-zh-native.mp3"   (zh-CN, existe em ambos)

Job #505 (User 4, zh-CN):
  config_snapshot.voice = "voice-zh-native.mp3"
  artifacts.voice_path = NOT SET  ← BUG
  generation_context.tts_language = "zh"
  generation_context.llm_script_model = "gpt-oss:latest"  ← não é qwen3
```

### 22. ROOT CAUSE — Worker sem env vars multilingual (evidência de produção)

**O bug mais grave de toda a auditoria.** O worker systemd service não tem `GPCG_MULTILINGUAL_ENABLED=true`. O kill switch em `language_context.py:182` retorna `pt-BR` para TODOS os jobs.

**Evidência direta do DB da VPS — Job #511 (User 4, zh-CN, COMPLETED):**
```json
{
  "generation_context": {
    "language": "pt-BR",
    "tts_language": "pt",
    "llm_script_model": "llama3.1:8b"
  },
  "voice_path": "/app/data/voices/voice-zh-native.mp3"
}
```

O job processou como pt-BR (texto, TTS, modelo LLM) mas com voz chinesa. Resultado: voz chinesa tentando falar português, ou áudio completamente errado.

**Todos os jobs completos do User 4 no VPS DB:**
```
Job #511: generation_context.language = "pt-BR"  ← WRONG (should be zh-CN)
Job #507: generation_context.language = "pt-BR"  ← WRONG
Job #505: generation_context.language = "pt-BR"  ← WRONG (but voice_path NOT SET — double bug)
Job #501: generation_context.language = "pt-BR"  ← WRONG
Job #427: generation_context.language = "pt-BR"  ← WRONG
```

**Todos os ContentPlans/Scripts/Videos do User 4 no VPS DB:**
```
ContentPlan #113: target_language = "pt-BR"  ← WRONG
ContentPlan #112: target_language = "pt-BR"  ← WRONG
Script #113: language = "pt-BR"  ← WRONG
Script #112: language = "pt-BR"  ← WRONG
Video #113: language = "pt-BR"  ← WRONG
Video #112: language = "pt-BR"  ← WRONG
```

**Script #113 (User 4, zh-CN channel) — primeiro trecho:**
```
"Você já ouviu as últimas vazamentos sobre o GTA 6? Dizem que o jogo terá
um clube de striptease, e uma novidade bem legal: uma câmera em lento..."
```
Isto é PORTUGUÊS, não chinês. O conteúdo foi gerado em português para um canal zh-CN.

**Jobs falhando por bug de `mood=`:**
```
Job #575: failed — "'mood' is an invalid keyword argument for ContentPlan"
Job #568: failed — "'mood' is an invalid keyword argument for ContentPlan"
```

### 23. Diagrama do fluxo broken vs corrected

```
BROKEN (atual):
  VPS .env tem GPCG_MULTILINGUAL_ENABLED=true
  Worker systemd NÃO tem GPCG_MULTILINGUAL_ENABLED
    ↓
  GenerationContext.from_channel_profile(profile)
    → kill switch: gpcg_multilingual_enabled=False
    → return cls()  # pt-BR default
    ↓
  gen_ctx.language = "pt-BR"
  gen_ctx.tts_language = "pt"
  gen_ctx.llm_script_model = "llama3.1:8b"
    ↓
  ContentPlanningService gera tópico em português
  ScriptService gera script em português
  TTS sintetiza com language=pt + voice-zh-native.mp3
    ↓
  Resultado: áudio errado (português com voz chinesa)

CORRECTED (após Fase 0):
  Worker systemd tem GPCG_MULTILINGUAL_ENABLED=true
  Worker systemd tem GPCG_MULTILINGUAL_LANGUAGES=pt-BR,en,zh-CN,zh-TW,zh
    ↓
  GenerationContext.from_channel_profile(profile)
    → kill switch: gpcg_multilingual_enabled=True
    → allowlist: zh-CN in ["pt-BR","en","zh-CN","zh-TW","zh"] ✓
    → return cls(language="zh-CN", tts_language="zh", ...)
    ↓
  gen_ctx.language = "zh-CN"
  gen_ctx.tts_language = "zh"
  gen_ctx.llm_script_model = "qwen3" (language-aware model selection)
    ↓
  ContentPlanningService gera tópico em chinês
  ScriptService gera script em chinês
  TTS sintetiza com language=zh + voice-zh-native.mp3
    ↓
  Resultado: áudio correto (chinês com voz chinesa)
```
