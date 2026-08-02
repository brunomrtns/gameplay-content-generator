# Plano de Refatoração Editorial do GPCG

> **Status**: Aprovado. Plano oficial de implementação futura.
> **Origem**: Análise crítica da arquitetura editorial realizada em sessão de revisão.
> **Princípio**: Este documento não deve ser modificado automaticamente durante
> análises editoriais posteriores. Novas ideias devem ser registradas em
> `docs/EDITORIAL_PRINCIPLES.md` e, se confirmadas, migradas para cá apenas
> após aprovação explícita.

---

## 1. Crítica da Arquitetura Editorial Atual

### O problema fundamental

O pipeline atual está estruturado como uma **linha de montagem de informação**:

```
fatos → selecionar fato → planejar como contar → gerar criatividade → escrever → revisar
```

Um criador humano trabalha de forma inversa:

```
encontrar uma história → validar se vale a pena → construir narrativa → escrever como conversa → revisar para retenção
```

A diferença não é sutil. **O pipeline atual parte do fato e tenta fazer ele ficar interessante.** Um criador parte do interesse e usa o fato como material.

### Análise componente por componente

#### Fact Scoring (`fact_service.py`) — Insuficiente

O scoring atual avalia dois critérios:
- `quality_score`: "editorial potential" (vago, sem definição operacional)
- `novelty_score`: "how little-known"

**Problema**: Um fato pode ser novo e de qualidade e ainda ser editorialmente morto. "GTA IV usa Euphoria" é novo e correto, mas não tem tensão, surpresa, ou curiosity gap. O scoring não mede **potencial editorial** — mede **correção e raridade**.

O prompt de scoring (linha 185-190) diz apenas "give quality_score (editorial potential, 0-100) and novelty_score (how little-known, 0-100)". Não há critérios. O LLM decide sozinho o que é "editorial potential" sem orientação.

#### Content Planning (`content_planning_service.py`) — Decisão cedo demais, critérios vagos

O `ContentPlanningService` faz **duas decisões em uma chamada**:
1. Seleciona o fato (entre top 15)
2. Desenha o plano (topic, hook, tone, energy, music_mood, visual_strategy)

**Problema 1**: Conflação de responsabilidades. Selecionar o fato e desenhar o plano são decisões editoriais diferentes. Um editor primeiro diz "é esta a história", depois diz "vamos contar assim".

**Problema 2**: O prompt (linha 22-43) diz "maximizes viewer retention and curiosity" mas os critérios são uma lista vaga: "Hook potential", "Curiosity / surprise factor", "Tellability", "Visual potential", "Originality". Não há definição operacional de nenhum. O LLM interpreta como quiser.

**Problema 3**: O fato já vem pré-ordenado por `quality_score * novelty_score` (linha 62). Se o scoring é fraco, a pré-ordenação é fraca, e o LLM recebe candidatos mediocres no topo.

**Problema 4**: Não há etapa de "isto é uma história?". O pipeline assume que todo fato é uma história. Mas a maioria é informação. Não há gate que diga "este fato não tem narrativa, pular".

#### Editorial Planner (`editorial_planner.py`) — Recebe o fato já selecionado

O `EditorialPlanner` recebe um `ContentPlan` com fato já escolhido. Ele decide **como contar**, mas não pode dizer **"este fato não merece vídeo, tenta outro"**.

**Problema**: O planner é um arquiteto que recebe o terreno e não pode questionar a escolha do terreno. Ele pode ter a melhor planta do mundo, mas se o terreno é ruim, o vídeo é ruim.

**Aspecto positivo**: O planner já tem conceitos corretos — central idea, narrative arc, humor como tempero, evitar padrões de IA. Mas ele aplica esses conceitos a um fato que pode não ter potencial narrativo.

#### Creative Engine (`creative_engine.py`) — Cedo demais, descontextualizado

O Creative Engine roda **depois** do editorial_planning mas **antes** do script. Gera 5 hooks, 5 angles, 5 punchlines, 5 observations.

**Problema 1**: O material é genérico. O prompt (linha 235-281) recebe apenas `topic`, `fact`, `context`, `style`. **Não recebe os narrative_beats do plano editorial.** O creative engine gera material criativo sem saber a estrutura narrativa. É um compositor escrevendo melodias sem saber a estrutura da música.

**Problema 2**: Criatividade antes da estrutura. O user levantou esta hipótese e está correta. Gerar hooks/angles/punchlines antes de ter uma narrativa sólida é aplicar tempero antes de ter a receita. O material criativo deveria surgir **da** narrativa, não antes dela.

**Problema 3**: O material é "inspiração" que o scriptwriter pode ignorar. O `_format_creative_material` (script_service.py linha 493) diz "use como inspiração, NÃO copie verbatim". Mas na prática, o LLM tende a usar os hooks sugeridos porque são a coisa mais concreta no prompt. Isso significa que o hook do vídeo é frequentemente decidido pelo creative engine, não pelo scriptwriter seguindo a narrativa.

#### Script Service (`script_service.py`) — Uma chamada LLM para tudo

O `generate_script` faz draft → optimize → originality check → rewrite em uma sequência. O draft é uma única chamada LLM que precisa:
- Seguir os narrative beats
- Matchear os tone weights
- Respeitar o humor plan
- Usar o creative material como inspiração
- Ser factualmente correto
- Ser original
- Soar falado, não escrito
- Atingir o target de caracteres

**Problema**: São muitas restrições simultâneas para um modelo 8B-14B. O resultado é que algo sempre cede — geralmente a naturalidade e a retenção, porque são as restrições mais "soft".

**Problema 2**: O optimize step (linha 186-207) é focado em TTS suitability e length, não em retenção ou humanidade. Ele aperta o roteiro, não o melhora editorialmente.

#### Script Critic (`script_critic.py`) — Revisor, não editor

O critic avalia 6 dimensões: structure, naturalness, humor, coherence, gameplay, factual_accuracy.

**Problema 1**: Dimensões erradas para o objetivo. Não há:
- **hook_strength**: o primeiro 3 segundos prende?
- **retention**: alguém continuaria assistindo?
- **pacing**: o ritmo varia ou é monótono?
- **payoff**: o vídeo entrega o que promete?
- **curiosity**: desperta vontade de saber mais?
- **humanity**: parece uma pessoa falando?

**Problema 2**: Quando o critic diz REVISE, o script inteiro é **regenerado do zero** (script_service.py linha 288-333). Não é edição — é "tentar de novo". Um editor humano faria edições cirúrgicas, não reescreveria o texto todo.

**Problema 3**: O critic não tem poder de veto sobre o fato escolhido. Ele pode dizer "structure lacks central idea" mas não pode dizer "this fact doesn't have a story, pick another one". Ele revisa o texto, não a decisão editorial.

---

## 2. Principais Gargalos

### Gargalo 1: Curiosidade tratada como fato (CRÍTICO)

O sistema não distingue "informação" de "curiosidade". Um fato é extraído, scored por qualidade/novidade, e transformado em vídeo. Nunca há a pergunta: **"isto desperta curiosidade?"**

**Exemplo**: "GTA IV usa Euphoria" passa pelo pipeline. "Ninguém programou aquelas quedas — o jogo improvisa cada reação em tempo real" não passa, porque ninguém transformou o fato em curiosidade.

**Impacto**: Vídeos corretos mas sem curiosity gap. O espectador aprende algo mas não tem o momento "nossa, sério?". Sem esse momento, não há retenção.

### Gargalo 2: Story finding inexistente (CRÍTICO)

Não há etapa de "encontrar a história". O pipeline vai direto de "selecionar fato" para "planejar como contar". Falta o passo intermediário: **"qual é o ângulo que transforma este fato em uma história?"**

**Impacto**: Cada fato é contado da forma mais óbvia. Não há twist, não há perspectiva, não há "mas na verdade...".

### Gargalo 3: Criatividade antes da estrutura (ALTO)

O Creative Engine gera material criativo antes de existir uma narrativa estruturada. O material é genérico (5 hooks, 5 angles) e não orientado pelos beats narrativos.

**Impacto**: O hook é frequentemente o melhor dos 5 hooks genéricos, não um hook que nasce da estrutura narrativa. Criatividade desconectada da narrativa.

### Gargalo 4: Script em uma chamada (ALTO)

O roteiro é gerado em uma única chamada LLM com 8+ restrições simultâneas. Modelos 8B-14B não conseguem respeitar todas. O que cede é sempre naturalidade e retenção.

**Impacto**: Roteiros que seguem a estrutura mas soam artificiais. O LLM prioriza "cobrir os beats" e "atingir o char count" porque são verificáveis, e sacrifica "soar humano" porque é subjetivo.

### Gargalo 5: Critic revisa, não edita (MÉDIO)

O critic diz REVISE e o script é regenerado do zero. Não há edição cirúrgica. Cada revisão é uma rolagem de dados nova.

**Impacto**: Uma revisão pode corrigir o problema apontado mas introduzir três novos. O loop de revisão não converge — itera aleatoriamente.

### Gargalo 6: Sem camada de humanização (MÉDIO)

Não há passo dedicado a quebrar padrões de IA. Os prompts dizem "soe falado" mas é uma instrução entre muitas. Não há detecção ativa de AI-isms nem correção direcionada.

**Impacto**: Padrões recorrentes: enumerações, conectivos excessivos, estrutura previsível, frases de transição genéricas. O critic flagga alguns, mas a "correção" é regenerar tudo.

### Gargalo 7: Factual accuracy como dimensão, não como gate (BAIXO)

O critic avalia factual_accuracy como uma das 6 dimensões, contribuindo para o overall_score. Mas invenção de mecânicas deveria ser um **veto automático**, não um fator ponderado.

**Impacto**: Um script com factual_accuracy=40 mas structure=90 e naturalness=85 pode passar se o overall >= 70. Isso é perigoso — um vídeo que inventa mecânicas mas é bem estruturado.

---

## 3. Proposta de Nova Arquitetura Editorial

### Pipeline proposto

```
content_planning → story_finding → editorial_planning → creative_engine → script → humanization → script_review → tts → ...
```

### Fluxo editorial ideal

```
1. content_planning    — seleciona fato (usando curiosity_score, não só quality*novelty)
2. story_finding       — transforma fato em história (angle, tension, curiosity gap)
3. editorial_planning  — desenha a narrativa (arc, beats, pacing, payoff)
4. creative_engine     — material criativo ORIENTADO pelos beats (não genérico)
5. script              — escreve como conversa (seguindo narrativa + material)
6. humanization        — quebra padrões de IA, garante oralidade
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
| Curiosity = qualidade + novidade | Curiosity = **curiosity gap + surpresa + tensão + potencial visual** |

---

## 4. Alterações em Componentes Existentes

### 4.1 Fact Scoring (`fact_service.py`)

**Adicionar**: `curiosity_score` (0-100) como terceira dimensão de scoring.

**Novo prompt de scoring** com critérios operacionais:
- `curiosity_gap` (0-100): O fato cria uma lacuna de conhecimento que o espectador quer preencher?
- `surprise_potential` (0-100): O fato quebra uma expectativa comum?
- `tension` (0-100): Existe conflito, paradoxo, ou contradição?
- `visual_potential` (0-100): O fato pode ser ilustrado com gameplay?
- `comment_potential` (0-100): O fato geraria comentários/discussão?
- `retention_potential` (0-100): O fato segura atenção por 60s?

`curiosity_score` = média ponderada desses sub-scores.

**Mudança na query**: ordenar por `(curiosity_score * 0.5 + quality_score * 0.3 + novelty_score * 0.2).desc()` em vez de `quality_score * novelty_score`.

### 4.2 Content Planning (`content_planning_service.py`)

**Reduzir responsabilidade**: apenas seleciona o fato e cria o ContentPlan básico (topic, tone, music_mood). Não tenta desenhar a narrativa.

**Mudança no prompt**: focar em "qual fato tem a melhor HISTORIA" (usando curiosity_score), não "qual fato é mais interessante".

**Adicionar**: se nenhum fato tiver curiosity_score >= threshold, retornar None (não fazer vídeo). Melhor não fazer vídeo do que fazer vídeo ruim.

### 4.3 Editorial Planner (`editorial_planner.py`)

**Receber StoryConcept** (do story_finding) em vez de apenas ContentPlan.

**Focar em**: estrutura narrativa, pacing, pontos de tensão, payoff. Não em "central idea" abstrata — em "como a história sobe, onde está o giro, onde está o payoff".

**Adicionar**: `retention_plan` — onde estão os pontos de risco (onde o espectador pode sair) e como mitigar.

### 4.4 Creative Engine (`creative_engine.py`)

**Mover para depois do editorial_planning** (já está, mas agora recebe os beats).

**Mudança crítica**: passar `narrative_beats` e `central_idea` para o prompt. Gerar material criativo **por beat**, não genérico.

**Novo output**: em vez de 5 hooks + 5 angles + 5 punchlines + 5 observations, gerar:
- 3 hooks específicos para o beat "hook"
- 3 ângulos para o beat "development"
- 3 opções de payoff para o beat "payoff"
- 3 observações para os beats de commentary

**Gate**: só rodar se o plano tiver `humor.enabled` ou `tone.casual >= 0.5`. Para vídeos puramente informativos, pular.

### 4.5 Script Service (`script_service.py`)

**Simplificar**: focar em escrever a narração seguindo a narrativa + material criativo. Remover a responsabilidade de "soar humano" — isso vai para a humanização.

**Mudança no prompt**: "escreva como alguém contando uma história para um amigo" em vez de "write like someone speaking, not writing". Mais concreto.

**Remover**: o optimize step focado em TTS suitability. A humanização substitui isso.

### 4.6 Script Critic (`script_critic.py`)

**Evoluir para editor-chefe**. Novas dimensões:

1. **hook_strength** (0-100): O primeiro 3 segundos prende? É específico ou genérico?
2. **retention** (0-100): O vídeo tem momentos de "vou parar de assistir"? Onde?
3. **pacing** (0-100): O ritmo varia? Tem momentos rápidos e lentos? Ou é monótono?
4. **payoff** (0-100): O vídeo entrega o que o hook promete? Ou é anticlímax?
5. **curiosity** (0-100): O vídeo desperta vontade de saber mais? Ou só informa?
6. **humanity** (0-100): Parece uma pessoa falando? Ou parece IA? Detectar padrões.
7. **factual_accuracy** (0-100): **GATE** — se < 70, REVISE automático, independentemente do overall.

**Mudança no verdict**: factual_accuracy < 70 = REVISE automático (hard gate). Para as outras dimensões, REVISE se overall < 75 (subir de 70) OU qualquer dimensão < 50.

**Mudança no feedback**: em vez de "regenerate", fornecer **edições cirúrgicas**: "na linha X, troque por Y", "remova a frase Z", "o hook é fraco, tente: A, B ou C".

---

## 5. Novos Componentes

### 5.1 Story Finder (`story_finder.py`) — NOVO

**Estágio**: entre `content_planning` e `editorial_planning`.

**Responsabilidade**: transformar um fato em uma história. Recebe o fato selecionado e pergunta: "qual é o ângulo que torna isto uma história?"

**Input**: ContentPlan + Fact
**Output**: `StoryConcept`

```python
@dataclass
class StoryConcept:
    fact_claim: str           # o fato original
    angle: str                # o ângulo editorial ("ninguém programou aquelas quedas")
    tension: str              # onde está a tensão ("o jogo improvisa em tempo real")
    surprise: str             # o que quebra expectativa ("você assumiria que foi animado")
    curiosity_gap: str        # a lacuna que o vídeo preenche
    why_care: str             # por que o espectador deveria se importar
    narrative_hook: str       # a frase que abre o vídeo (não o "hook" genérico)
    retention_strategy: str   # como segurar a atenção
    is_story: bool            # se isto é uma história ou só informação
    confidence: float         # 0-1, quão boa é a história
    success: bool
    error: str
```

**Prompt**: "Você é um EDITOR DE YouTube. Dado um fato, encontre o ÂNGULO que o transforma em história. Se não houver ângulo (é só informação), diga is_story=false."

**Gate**: se `is_story=false` ou `confidence < 0.5`, o pipeline volta e tenta outro fato. Se nenhum fato gerar história, o job falha graciosamente ("no story-worthy facts found").

### 5.2 Curiosity Scorer (`curiosity_scorer.py`) — NOVO

**Estágio**: roda durante fact extraction (não no pipeline de geração).

**Responsabilidade**: scoring quantitativo de potencial editorial.

**Input**: Fact (claim, category, game_name)
**Output**: curiosity_score + sub-scores

**Critérios** (cada um 0-100):
- `curiosity_gap`: cria lacuna de conhecimento?
- `surprise_potential`: quebra expectativa?
- `tension`: tem conflito/paradoxo?
- `visual_potential`: pode ilustrar com gameplay?
- `comment_potential`: gera discussão?
- `retention_potential`: segura 60s?

`curiosity_score` = média ponderada: `retention_potential * 0.30 + curiosity_gap * 0.25 + surprise_potential * 0.20 + tension * 0.10 + visual_potential * 0.10 + comment_potential * 0.05`

### 5.3 Humanization Pass (`humanization.py`) — NOVO

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

**Abordagem**: duas opções — (a) LLM pass com prompt focado apenas em humanização, ou (b) regex + heurísticas para detecção e LLM para correção.

**Recomendação**: abordagem híbrida. Regex detecta os padrões (rápido, determinístico), LLM corrige (criativo, contextual).

---

## 6. Nova Ordem Ideal do Pipeline

```
content_planning     — seleciona fato (usando curiosity_score)
    ↓
story_finding        — transforma fato em história (angle, tension, curiosity gap)  [NOVO]
    ↓
editorial_planning   — desenha narrativa (arc, beats, pacing, payoff, retention_plan)
    ↓
creative_engine      — material criativo orientado por beat (não genérico)
    ↓
script               — escreve como conversa (seguindo narrativa + material)
    ↓
humanization         — quebra padrões de IA, garante oralidade  [NOVO]
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
content_planning → ContentPlan (fact_id, topic, tone)
story_finding → StoryConcept (angle, tension, curiosity_gap, narrative_hook, retention_strategy)
editorial_planning → VideoCreativePlan (central_idea, narrative_beats, tone, humor, gameplay_query)
creative_engine → CreativeMaterial (beat-oriented hooks, angles, payoffs)
script → Script (draft, final)
humanization → Script (humanized_final, changes_log)
script_review → ScriptReview (hook_strength, retention, pacing, payoff, curiosity, humanity, factual_accuracy)
```

---

## 7. Plano de Implementação Incremental

Cada fase é independente, gated por feature flag, e não quebra o comportamento existente se desativada.

### Fase 1: Curiosity Scoring

**Arquivos**:
- `src/gpcg/domain/models.py` — adicionar colunas `curiosity_score`, `curiosity_subscores` (JSON) ao `Fact`
- `src/gpcg/application/curiosity_scorer.py` — **NOVO**
- `src/gpcg/application/fact_service.py` — chamar curiosity_scorer após quality/novelty scoring
- `src/gpcg/application/content_planning_service.py` — ordenar por curiosity_score
- `tests/test_curiosity_scoring.py` — **NOVO**

**Mudança no banco**: migration adicionando colunas.

**Feature flag**: `GPCG_CURIOSITY_SCORING_ENABLED` (default: false). Se off, usa scoring atual.

**Verificação**: rodar curiosity_scorer em fatos existentes, comparar scores com qualidade percebida dos vídeos já gerados.

### Fase 2: Story Finder

**Arquivos**:
- `src/gpcg/domain/creative_plan.py` — adicionar `StoryConcept` dataclass
- `src/gpcg/application/story_finder.py` — **NOVO**
- `src/gpcg/application/generation_service.py` — adicionar estágio `story_finding` entre `content_planning` e `editorial_planning`
- `src/gpcg/domain/models.py` — adicionar `JobStage.story_finding` ao enum
- `src/gpcg/application/editorial_planner.py` — receber `StoryConcept` opcional
- `tests/test_story_finder.py` — **NOVO**

**Feature flag**: `GPCG_STORY_FINDER_ENABLED` (default: false). Se off, pula direto para editorial_planning (comportamento atual).

**Verificação**: gerar StoryConcepts para fatos existentes, avaliar se os ângulos são melhores que a abordagem direta.

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
- `tests/test_humanization.py` — **NOVO**

**Feature flag**: `GPCG_HUMANIZATION_ENABLED` (default: false). Se off, pula.

**Verificação**: rodar humanization em scripts já gerados, comparar "antes vs depois" em padrões de IA detectados.

### Fase 5: Evoluir Script Critic

**Arquivos**:
- `src/gpcg/domain/creative_plan.py` — atualizar `CRITIC_DIMENSIONS`, `ScriptReview`
- `src/gpcg/application/script_critic.py` — novas dimensões, hard gate em factual_accuracy, feedback cirúrgico
- `src/gpcg/application/script_service.py` — revisão cirúrgica em vez de regeneração total
- `tests/test_script_critic.py` — atualizar

**Feature flag**: `GPCG_SCRIPT_CRITIC_V2_ENABLED` (default: false). Se off, usa critic atual.

**Mudança no revision loop**: em vez de regenerar o script inteiro, o critic fornece edições pontuais e o ScriptService aplica apenas essas edições.

**Verificação**: comparar verdicts do critic v1 vs v2 nos mesmos scripts. Avaliar se v2 flagga mais problemas reais.

### Fase 6: Integrar Story Concept no Pipeline

**Arquivos**:
- `src/gpcg/application/editorial_planner.py` — usar `StoryConcept.angle` como central idea, `StoryConcept.retention_strategy` no plano
- `src/gpcg/application/script_service.py` — passar `StoryConcept` para o prompt (angle, tension, curiosity_gap, narrative_hook)
- `src/gpcg/application/generation_service.py` — passar `StoryConcept` entre estágios via `job.artifacts["story_concept"]`

**Feature flag**: já coberto por `GPCG_STORY_FINDER_ENABLED`.

**Verificação**: gerar vídeos com e sem story_finding, comparar qualidade editorial.

### Fase 7: Refatorar Content Planning

**Arquivos**:
- `src/gpcg/application/content_planning_service.py` — simplificar: apenas seleciona fato, não desenha narrativa
- `src/gpcg/application/story_finder.py` — absorver a responsabilidade de "qual ângulo"
- `src/gpcg/application/editorial_planner.py` — absorver a responsabilidade de "como contar"

**Mudança**: ContentPlanningService fica enxuto. Story Finder e Editorial Planner dividem o trabalho editorial.

**Verificação**: garantir que o pipeline atual (sem story_finding) ainda funciona.

### Fase 8: Ativar Tudo e Testar E2E

**Ativar**: todas as feature flags. Rodar geração completa com a nova arquitetura.

**Comparar**: gerar 5 vídeos com pipeline atual e 5 com pipeline novo. Avaliar:
- Hook strength (primeiros 3s)
- Curiosity gap
- Naturalidade / oralidade
- Padrões de IA detectados
- Retenção subjetiva (eu assistiri até o fim?)

**Ajustar**: prompts com base nos resultados. Iterar.

---

## Possíveis Evoluções Futuras

> Esta seção registra ideias que surgiram em análises posteriores
> (ver `docs/EDITORIAL_PRINCIPLES.md`). Estas ideias **não fazem parte do
> plano aprovado** e só devem ser migradas para as seções principais
> deste documento após validação e aprovação explícita.

*(Vazio no momento. Será preenchido conforme hipóteses do EDITORIAL_PRINCIPLES.md
forem confirmadas.)*
