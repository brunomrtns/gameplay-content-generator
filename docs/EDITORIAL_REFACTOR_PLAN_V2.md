# Plano de Refatoração Editorial do GPCG (V2)

> **Status**: Aprovado. Plano oficial de implementação futura.
> **Origem**: Revisão de coerência do V1 à luz do
> `EDITORIAL_RESEARCH_JOURNAL.md` e `EDITORIAL_MANIFESTO.md`.
> **Princípio**: Este documento não deve ser modificado automaticamente.
> **Versão**: V2 — alterações mínimas e bem fundamentadas sobre o V1.
> Ver `EDITORIAL_CONSOLIDATION_REPORT.md` para a justificativa de
> cada mudança.

---

## 1. Crítica da Arquitetura Editorial Atual

> **Inalterado em relação ao V1.** A crítica componente por componente
> e os 7 gargalos identificados permanecem válidos. Não foram
> contestados pela residência editorial — foram confirmados.

*(Ver `EDITORIAL_REFACTOR_PLAN.md` §1 e §2 para a crítica completa.
Preservada integralmente.)*

---

## 2. Proposta de Nova Arquitetura Editorial

### Pipeline proposto

```
content_planning → story_finding → editorial_planning → creative_engine → script → humanization → script_review → tts → ...
```

### Fluxo editorial ideal

```
1. content_planning    — seleciona fato (usando curiosity_score, não só quality*novelty)
2. story_finding       — transforma fato em história (angle, curiosity gap, frame)
3. editorial_planning  — desenha a narrativa (arc, beats, pacing, payoff)
4. creative_engine     — material criativo ORIENTADO pelos beats (não genérico)
5. script              — escreve como conversa (seguindo narrativa + material)
6. humanization        — quebra padrões de IA, garante oralidade, identifica ignorância
7. script_review       — editor-chefe focado em retenção (não revisor de texto)
```

### Diferença conceitual

| Atual | Proposto |
|-------|----------|
| Fato → plano → criatividade → texto → revisão | Fato → **história** → narrativa → criatividade → texto → **humanização** → **edição para retenção** |
| "Como contar este fato?" | "**Existe uma história aqui?**" → "Como contar esta história?" |
| Criatividade antes da estrutura | Criatividade **depois** da estrutura, orientada por ela |
| Revisor de texto | **Editor-chefe** focado em retenção |
| Uma chamada LLM para o script | Script + humanização (edição dirigida) |
| Curiosity = qualidade + novidade | Curiosity = **curiosity gap + surpresa + familiaridade + potencial visual** |

> **Mudança V2**: "Curiosity = curiosity gap + surpresa + tensão +
> potencial visual" → "curiosity gap + surpresa + familiaridade +
> potencial visual". `familiarity` substitui `tension` com base na
> curva invertida U de Loewenstein. Ver `EDITORIAL_CONSOLIDATION_REPORT.md`
> §3, H-PC-1.

---

## 3. Alterações em Componentes Existentes

### 3.1 Fact Scoring (`fact_service.py`)

**Adicionar**: `curiosity_score` (0-100) como terceira dimensão de scoring.

**Novo prompt de scoring** com critérios operacionais:
- `curiosity_gap` (0-100): O fato cria uma lacuna de conhecimento que o espectador quer preencher?
- `surprise_potential` (0-100): O fato quebra uma expectativa comum?
- `retention_potential` (0-100): O fato segura atenção por 60s?
- `familiarity` (0-100): O fato conecta a algo que o espectador já conhece? Para game-specific facts: familiarity do jogo. Para general curiosity facts: familiarity do tópico (não do jogo de fundo).
- `visual_potential` (0-100): O fato pode ser ilustrado com gameplay?
- `insight_quality` (0-100): O fato é um "insight" (uma peça que ilumina o todo) ou "trivia" (detalhe isolado)?

`curiosity_score` = média ponderada:
`curiosity_gap * 0.30 + surprise_potential * 0.25 + retention_potential * 0.20 + familiarity * 0.15 + insight_quality * 0.10`

> **Mudança V2**: Removidos `comment_potential` (peso 0.05, media coisa errada — comentários são função da apresentação, não do fato) e `tension` (redundante com `surprise_potential`). Adicionados `familiarity` (Loewenstein's curva invertida U: curiosidade requer base de conhecimento) e `insight_quality` (Loewenstein: insight > trivia para curiosidade). 6 → 5 sub-scores.

**Mudança na query**: ordenar por `(curiosity_score * 0.5 + quality_score * 0.3 + novelty_score * 0.2).desc()` em vez de `quality_score * novelty_score`.

### 3.2 Content Planning (`content_planning_service.py`)

**Reduzir responsabilidade**: apenas seleciona o fato e cria o ContentPlan com parâmetros técnicos (`target_duration`, `video_format`). Não define `tone` ou `music_mood` — essas são decisões narrativas que cabem ao Editorial Planner.

> **Mudança V2**: V1 dizia "ContentPlan básico (topic, tone, music_mood)". Mas `tone` e `music_mood` são decisões narrativas. Se Content Planning é só "selecionar o fato", não deveria decidir tone. Movidas para Editorial Planner (que já define tone via VideoCreativePlan).

**Mudança no prompt**: focar em "qual fato tem a melhor HISTORIA" (usando curiosity_score), não "qual fato é mais interessante".

**Adicionar**: se nenhum fato tiver curiosity_score >= threshold, retornar None (não fazer vídeo). Melhor não fazer vídeo do que fazer vídeo ruim.

**Relação entre gates** (explicitada): `curiosity_score` determina **candidatos** (top N). `is_story` (no Story Finder) determina se o candidato selecionado **tem história**. Se não, tenta o próximo candidato. Se nenhum tiver história, o job falha graciosamente. Os gates são sequenciais, não concorrentes.

> **Mudança V2**: V1 tinha dois gates implícitos sem relação clara. V2 explicita que são sequenciais: curiosity_score filtra candidatos, is_story valida o selecionado.

### 3.3 Editorial Planner (`editorial_planner.py`)

**Receber StoryConcept** (do story_finding) em vez de apenas ContentPlan.

**Focar em**: estrutura narrativa, pacing, pontos de tensão, payoff. Não em "central idea" abstrata — em "como a história sobe, onde está o giro, onde está o payoff".

**Receber também**: `tone` e `music_mood` (movidos de Content Planning).

> **Mudança V2**: Removido `retention_plan` ("onde estão os pontos de risco e como mitigar"). Kahneman's duration neglect mostra que espectadores não lembram de seções fracas — lembram de picos e finais. Pre-identificar "pontos de risco" é over-engineering. O Script Critic avalia pico e final depois do fato.

### 3.4 Creative Engine (`creative_engine.py`)

**Mover para depois do editorial_planning** (já está, mas agora recebe os beats).

**Mudança crítica**: passar `narrative_beats` e `central_idea` para o prompt. Gerar material criativo **por beat**, não genérico.

**Novo output**: em vez de 5 hooks + 5 angles + 5 punchlines + 5 observations, gerar:
- 3 hooks específicos para o beat "hook"
- 3 ângulos para o beat "development"
- 3 opções de payoff para o beat "payoff"
- 3 observações para os beats de commentary

**Gate**: só rodar se o plano tiver `humor.enabled` ou `tone.casual >= 0.5`. Para vídeos puramente informativos, pular.

> **Inalterado em relação ao V1.** Confirmado por Miller's "plan" concept.

### 3.5 Script Service (`script_service.py`)

**Simplificar**: focar em escrever a narração seguindo a narrativa + material criativo. Remover a responsabilidade de "soar humano" — isso vai para a humanização.

**Mudança no prompt**: "escreva como alguém contando uma história para um amigo" em vez de "write like someone speaking, not writing". Mais concreto.

**Remover**: o optimize step focado em TTS suitability editorial. A humanização substitui isso na dimensão editorial.

**Manter**: um check técnico de length/TTS **após** a humanização (não antes). A humanização pode alterar o length; o check final garante que o resultado está dentro dos bounds. Não é um "optimize step" editorial — é uma verificação técnica.

> **Mudança V2**: V1 dizia "Remover: o optimize step. A humanização substitui isso." Mas a humanização não lida com length/TTS. V2 esclarece: remove o optimize editorial, mantém um check técnico de length/TTS após humanização.

### 3.6 Script Critic (`script_critic.py`)

**Evoluir para editor-chefe**. Novas dimensões:

1. **hook_strength** (0-100): O primeiro 3 segundos prende? É específico ou genérico?
2. **retention** (0-100): O vídeo tem momentos de "vou parar de assistir"? Onde?
3. **pacing** (0-100): O ritmo varia? Tem momentos rápidos e lentos? Ou é monótono?
4. **payoff** (0-100): O vídeo entrega o que o hook promete? Ou é anticlímax?
5. **curiosity** (0-100): O vídeo desperta vontade de saber mais? Ou só informa?
6. **humanity** (0-100): Parece uma pessoa falando? Ou parece IA? Detectar padrões.
7. **factual_accuracy** (0-100): **GATE** — se < 70, REVISE automático, independentemente do overall.

**Mudança no verdict**: factual_accuracy < 70 = REVISE automático (hard gate). Para as outras dimensões, REVISE se overall < 75 (subir de 70) OU qualquer dimensão < 50.

**Mudança no feedback**: em vez de regenerar o script inteiro, o critic identifica a **seção problemática** (hook, desenvolvimento, conclusão) e o ScriptService regenera apenas aquela seção. "Revisão por seção", não "edição cirúrgica linha-a-linha" (LLMs não têm granularidade de linha confiável) nem "regeneração total" (V1).

> **Mudança V2**: V1 prometia "edições cirúrgicas: 'na linha X, troque por Y'". LLMs não fazem isso de forma confiável. V2 é honesto: "revisão por seção" — regenerar apenas a seção problemática, não o script inteiro. Mais factível que edições cirúrgicas, mais útil que regeneração total.

---

## 4. Novos Componentes

### 4.1 Story Finder (`story_finder.py`) — NOVO

**Estágio**: entre `content_planning` e `editorial_planning`.

**Responsabilidade**: transformar um fato em uma história. Recebe o fato selecionado e pergunta: "qual é o ângulo que torna isto uma história?"

**Input**: ContentPlan + Fact
**Output**: `StoryConcept`

```python
@dataclass
class StoryConcept:
    fact_claim: str           # o fato original
    angle: str                # o ângulo editorial ("ninguém programou aquelas quedas")
    curiosity_gap: str        # a lacuna que o vídeo preenche
    narrative_hook: str       # a frase que abre o vídeo (não o "hook" genérico)
    frame: str                # como enquadrar o fato (Kahneman's framing: "5% completam" vs "95% falham")
    is_insight: bool          # se é insight (illumina o todo) ou trivia (detalhe isolado)
    is_story: bool            # se isto é uma história ou só informação
    confidence: float         # 0-1, quão boa é a história
    success: bool
    error: str
```

**Prompt**: "Você é um EDITOR DE YouTube. Dado um fato, encontre o ÂNGULO que o transforma em história. Se não houver ângulo (é só informação), diga is_story=false."

**Gate**: se `is_story=false` ou `confidence < 0.5`, o pipeline volta e tenta outro fato. Se nenhum fato gerar história, o job falha graciosamente ("no story-worthy facts found").

> **Mudança V2**: StoryConcept reduzido de 11 para 9 campos.
> - Removidos: `tension` (redundante com `angle` + `curiosity_gap`), `surprise` (redundante com `angle`), `why_care` (implícito em `curiosity_gap`), `retention_strategy` (é job do Editorial Planner, não do Story Finder — evita conflação de responsabilidades)
> - Adicionados: `frame` (Kahneman's framing effect — o frame é uma decisão editorial explícita), `is_insight` (Loewenstein — insight > trivia para curiosidade)

### 4.2 Curiosity Scorer (`curiosity_scorer.py`) — NOVO

**Estágio**: roda durante fact extraction (não no pipeline de geração).

**Responsabilidade**: scoring quantitativo de potencial editorial.

**Input**: Fact (claim, category, game_name)
**Output**: curiosity_score + sub-scores

**Critérios** (cada um 0-100):
- `curiosity_gap`: cria lacuna de conhecimento?
- `surprise_potential`: quebra expectativa?
- `retention_potential`: segura 60s?
- `familiarity`: conecta a algo que o espectador já conhece? (game-specific: familiarity do jogo; general curiosity: familiarity do tópico)
- `insight_quality`: é insight (illumina o todo) ou trivia (detalhe isolado)?
- `visual_potential`: pode ilustrar com gameplay?

`curiosity_score` = média ponderada: `curiosity_gap * 0.30 + surprise_potential * 0.25 + retention_potential * 0.20 + familiarity * 0.15 + insight_quality * 0.10`

> **Mudança V2**: 6 → 5 sub-scores (na prática 6 com `visual_potential`, mas este é o único "técnico" — os 5 principais são os editoriais). Removidos `comment_potential` (mede coisa errada) e `tension` (redundante). Adicionados `familiarity` (Loewenstein's curva U) e `insight_quality` (Loewenstein's insight vs. trivia).

### 4.3 Humanization Pass (`humanization.py`) — NOVO

**Estágio**: entre `script` e `script_review`.

**Responsabilidade**: quebrar padrões de IA e garantir oralidade.

**Input**: script text + VideoCreativePlan
**Output**: script humanizado + lista de mudanças

**O que faz**:
1. **Detecção de AI-isms**: scan por padrões conhecidos (enumerações, conectivos excessivos, "você não vai acreditar", "e é aí que", estruturas repetitivas)
2. **Variação de ritmo**: detecta se todas as frases têm o mesmo comprimento; se sim, varia
3. **Remoção de redundância**: detecta explicações desnecessárias ("ou seja", "em outras palavras", "isto significa que")
4. **Injeção de oralidade**: adiciona pausas naturais, reformula frases escritas como faladas
5. **Quebra de padrão**: se há 3+ frases com a mesma estrutura, reformula uma
6. **Identificação com a ignorância**: injeta frases que reconhecem que o narrador também não sabia ("eu também não sabia", "demorei pra entender isso"), criando cumplicidade com o espectador. Corrige a Maldição do Conhecimento (Heath) — a IA não lembra como era não saber; a humanização injeta essa identificação.

**Abordagem**: duas opções — (a) LLM pass com prompt focado apenas em humanização, ou (b) regex + heurísticas para detecção e LLM para correção.

**Recomendação**: abordagem híbrida. Regex detecta os padrões (rápido, determinístico), LLM corrige (criativo, contextual).

> **Mudança V2**: Adicionado item 6 — "identificação com a ignorância". Baseado em Heath's Maldição do Conhecimento: a IA não lembra como era não saber. A técnica de "eu também não sabia" é uma correção direta que a IA não aplica naturalmente. Esta é a hipótese H-MTS-2 do diário de pesquisa, promovida a implementação por ser concreta e actionable.

---

## 5. Nova Ordem Ideal do Pipeline

```
content_planning     — seleciona fato (usando curiosity_score)
    ↓
story_finding        — transforma fato em história (angle, curiosity gap, frame)  [NOVO]
    ↓
editorial_planning   — desenha narrativa (arc, beats, pacing, payoff)
    ↓
creative_engine      — material criativo orientado por beat (não genérico)
    ↓
script               — escreve como conversa (seguindo narrativa + material)
    ↓
humanization         — quebra padrões de IA, oralidade, identificação com ignorância  [NOVO]
    ↓
script_review        — editor-chefe: retenção, hook, payoff, humanity (não revisor de texto)
    ↓
tts → gameplay_selection → music_selection → render_plan → render → qa → ...
```

### Estágios opcionais (gates)

| Estágio | Gate | Fallback |
|---------|------|----------|
| `story_finding` | `GPCG_STORY_FINDER_ENABLED` | pular, usar fact direto |
| `creative_engine` | `humor.enabled OR tone.casual >= 0.5` | pular para vídeos sérios |
| `humanization` | `GPCG_HUMANIZATION_ENABLED` | pular, usar script direto |
| `script_review` | `GPCG_SCRIPT_CRITIC_ENABLED` | pular |

### Fluxo de dados

```
content_planning → ContentPlan (fact_id, target_duration, video_format)
story_finding → StoryConcept (angle, curiosity_gap, narrative_hook, frame, is_insight)
editorial_planning → VideoCreativePlan (central_idea, narrative_beats, tone, humor, gameplay_query, music_mood)
creative_engine → CreativeMaterial (beat-oriented hooks, angles, payoffs)
script → Script (draft, final)
humanization → Script (humanized_final, changes_log)
script_review → ScriptReview (hook_strength, retention, pacing, payoff, curiosity, humanity, factual_accuracy)
```

> **Mudança V2**: ContentPlan não inclui mais `tone` (movido para Editorial Planner). StoryConcept não inclui mais `tension`, `surprise`, `why_care`, `retention_strategy`; inclui `frame`, `is_insight`. VideoCreativePlan inclui `music_mood` (movido de Content Planning).

---

## 6. Plano de Implementação Incremental

Cada fase é independente, gated por feature flag, e não quebra o comportamento existente se desativada.

> **Mudança V2**: 8 → 6 fases. Fase 6 (Integrar Story Concept) mergeada na Fase 2. Fase 7 (Refatorar Content Planning) removida — a simplificação acontece como consequência da Fase 2, não como esforço separado.

### Fase 1: Curiosity Scoring

**Arquivos**:
- `src/gpcg/domain/models.py` — adicionar colunas `curiosity_score`, `curiosity_subscores` (JSON) ao `Fact`
- `src/gpcg/application/curiosity_scorer.py` — **NOVO**
- `src/gpcg/application/fact_service.py` — chamar curiosity_scorer após quality/novelty scoring
- `src/gpcg/application/content_planning_service.py` — ordenar por curiosity_score
- `tests/test_curiosity_scoring.py` — **NOVO**

**Mudança no banco**: migration adicionando colunas.

**Feature flag**: `GPCG_CURIOSITY_SCORING_ENABLED` (default: false). Se off, usa scoring atual.

**Verificação**: rodar curiosity_scorer em fatos existentes, comparar scores com qualidade percebida dos vídeos já gerados. Validar se `familiarity` correlaciona com retenção (fatos sobre jogos conhecidos deveriam pontuar alto).

### Fase 2: Story Finder (+ Integração)

**Arquivos**:
- `src/gpcg/domain/creative_plan.py` — adicionar `StoryConcept` dataclass
- `src/gpcg/application/story_finder.py` — **NOVO**
- `src/gpcg/application/generation_service.py` — adicionar estágio `story_finding` entre `content_planning` e `editorial_planning`; passar `StoryConcept` via `job.artifacts["story_concept"]`
- `src/gpcg/domain/models.py` — adicionar `JobStage.story_finding` ao enum
- `src/gpcg/application/editorial_planner.py` — receber `StoryConcept` opcional; usar `angle` como central idea, `frame` no plano
- `src/gpcg/application/script_service.py` — passar `StoryConcept` para o prompt (angle, curiosity_gap, narrative_hook, frame)
- `src/gpcg/application/content_planning_service.py` — simplificar: seleciona fato + parâmetros técnicos apenas; tone/music_mood movidos para Editorial Planner
- `tests/test_story_finder.py` — **NOVO**

> **Mudança V2**: Fase 2 absorve a antiga Fase 6 (Integrar Story Concept) e parte da antiga Fase 7 (simplificação de Content Planning). Uma fase, não três.

**Feature flag**: `GPCG_STORY_FINDER_ENABLED` (default: false). Se off, pula direto para editorial_planning (comportamento atual).

**Verificação**: gerar StoryConcepts para fatos existentes, avaliar se os ângulos e frames são melhores que a abordagem direta. Validar se `is_story=false` rejeita fatos sem potencial narrativo.

### Fase 3: Repositionar e Orientar Creative Engine

**Arquivos**:
- `src/gpcg/application/creative_engine.py` — receber `narrative_beats` e `central_idea`, gerar material por beat
- `src/gpcg/application/generation_service.py` — passar `VideoCreativePlan` completo para creative_engine (já passa, mas o engine não usa)
- `src/gpcg/application/script_service.py` — ajustar `_format_creative_material` para material orientado por beat
- `tests/test_creative_engine.py` — atualizar

**Mudança**: o creative_engine já roda depois do editorial_planning. A mudança é no **prompt** — passar os beats e gerar material orientado.

**Feature flag**: `GPCG_CREATIVE_ENGINE_BEAT_ORIENTED` (default: false). Se off, usa prompt atual.

**Verificação**: comparar material criativo genérico vs orientado por beat. Avaliar se os hooks são mais específicos.

### Fase 4: Humanization Layer

**Arquivos**:
- `src/gpcg/application/humanization.py` — **NOVO**
- `src/gpcg/application/generation_service.py` — adicionar estágio `humanization` entre `script` e `script_review`
- `src/gpcg/domain/models.py` — adicionar `JobStage.humanization` ao enum
- `src/gpcg/application/script_service.py` — adicionar check técnico de length/TTS após humanização
- `tests/test_humanization.py` — **NOVO**

**Feature flag**: `GPCG_HUMANIZATION_ENABLED` (default: false). Se off, pula.

**Verificação**: rodar humanization em scripts já gerados, comparar "antes vs depois" em padrões de IA detectados. Validar se "identificação com a ignorância" é injetada sem soar forçada.

### Fase 5: Evoluir Script Critic

**Arquivos**:
- `src/gpcg/domain/creative_plan.py` — atualizar `CRITIC_DIMENSIONS`, `ScriptReview`
- `src/gpcg/application/script_critic.py` — novas dimensões, hard gate em factual_accuracy, feedback por seção
- `src/gpcg/application/script_service.py` — revisão por seção em vez de regeneração total
- `tests/test_script_critic.py` — atualizar

**Feature flag**: `GPCG_SCRIPT_CRITIC_V2_ENABLED` (default: false). Se off, usa critic atual.

**Mudança no revision loop**: em vez de regenerar o script inteiro, o critic identifica a seção problemática (hook, desenvolvimento, conclusão) e o ScriptService regenera apenas aquela seção.

**Verificação**: comparar verdicts do critic v1 vs v2 nos mesmos scripts. Avaliar se v2 flagga mais problemas reais. Validar se revisão por seção converge mais rápido que regeneração total.

### Fase 6: Ativar Tudo e Testar E2E

**Ativar**: todas as feature flags. Rodar geração completa com a nova arquitetura.

**Comparar**: gerar 5 vídeos com pipeline atual e 5 com pipeline novo. Avaliar:
- Hook strength (primeiros 3s)
- Curiosity gap
- Naturalidade / oralidade
- Padrões de IA detectados
- Retenção subjetiva (eu assistira até o fim?)

**Ajustar**: prompts com base nos resultados. Iterar.

---

## Possíveis Evoluções Futuras

> Esta seção registra ideias que surgiram em análises posteriores
> (ver `docs/EDITORIAL_RESEARCH_JOURNAL.md`). Estas ideias **não fazem
> parte do plano aprovado** e só devem ser migradas para as seções
> principais deste documento após validação e aprovação explícita.

### Hipóteses confirmadas como conhecimento editorial (não justificam arquitetura)

- H-MTS-1: Fatos com múltiplas conexões > fatos isolados (Teoria Velcro) — muito vago para medir
- H-MTS-3: Números específicos > adjetivos (Concreteness) — craft de script
- H-SB-1: Espectador como sujeito, fato como descoberta — já no prompt do Script Service
- H-SB-2: Stakes explícitos — vago para general curiosity shorts
- H-SB-3: Micro-transformação explícita — craft de script
- H-PC-2: Estrutura suposição→quebra→gap→payoff — já implícito no StoryConcept
- H-PC-4: Múltiplos gaps sequenciais — craft de script
- H-PC-5: Transição diversiva→específica — craft de hook
- H-K-1: Pico e final deliberados — já no Script Critic via `payoff`
- H-K-2: Gameplay reforça narração (WYSIATI) — outro subsistema
- H-K-4: Loss aversion no hook, gain no final — craft de hook
- H-K-5: System 1 com System 2 na revelação — craft de script

### Hipóteses promovidas a implementação (incorporadas no V2)

- H-PC-1: `familiarity` no Curiosity Scorer → Fase 1
- H-PC-3: `is_insight` no Curiosity Scorer e StoryConcept → Fase 1, Fase 2
- H-K-3: `frame` no StoryConcept → Fase 2
- H-MTS-2: Identificação com ignorância no Humanization → Fase 4

*(Para novas hipóteses, registrar em `docs/EDITORIAL_RESEARCH_JOURNAL.md`
e migrar para cá apenas após validação.)*
