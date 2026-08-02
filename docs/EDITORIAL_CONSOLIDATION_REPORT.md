# Relatório de Consolidação Editorial

> **Status**: Relatório de revisão de coerência.
> **Objetivo**: Revisar o `EDITORIAL_REFACTOR_PLAN.md` à luz do
> `EDITORIAL_RESEARCH_JOURNAL.md` e do `EDITORIAL_MANIFESTO.md`.
> **Resultado**: `EDITORIAL_REFACTOR_PLAN_V2.md` com alterações mínimas
> e bem fundamentadas.

---

## 1. O que foi confirmado

### Story Finder (Fase 2) — fortemente confirmado

Loewenstein's information-gap theory valida diretamente o Story Finder.
O "ângulo" que transforma fato em história é exatamente o mecanismo
que cria a lacuna de informação. Os campos `angle`, `curiosity_gap` e
`narrative_hook` do StoryConcept correspondem à estrutura
suposição → quebra → lacuna → payoff que Loewenstein descreve.

O gate `is_story` é confirmado pelo manifesto: "melhor não fazer um
vídeo do que fazer um vídeo que não provoca nada". Nem todo fato é
uma descoberta.

### Curiosity Scorer (Fase 1) — confirmado com refinamento

A necessidade de medir potencial editorial (não só qualidade +
novidade) é confirmada por todos os quatro autores. Heath fala em
"stickiness", Loewenstein em "information gap", Kahneman em
"focusing illusion", Miller em "transformation".

**Refinamento necessário**: Loewenstein's curva invertida U mostra
que curiosidade requer base de conhecimento. Fatos sobre jogos
conhecidos geram mais curiosidade que fatos sobre jogos obscuros.
O scorer atual não mede `familiarity`. Ver §3 abaixo.

### Humanization (Fase 4) — confirmado

Heath's "Maldição do Conhecimento" explica por que conteúdo gerado
por IA soa artificial: a IA não lembra como era não saber. A
humanização é onde a "identificação com a ignorância" ("eu também
não sabia") seria injetada — uma técnica que a IA não aplica
naturalmente.

Kahneman's System 1/2 confirma: o script deve ser processável por
System 1 na maior parte, com ativação estratégica de System 2 na
revelação. A humanização garante processabilidade por System 1.

### Script Critic v2 (Fase 5) — confirmado

Kahneman's peak-end rule valida as dimensões `payoff` e `retention`.
O hard gate em `factual_accuracy` é confirmado pelo manifesto:
"factualmente correto — nunca inventa".

### Creative Engine beat-oriented (Fase 3) — confirmado

Miller's "plan" concept (pedras no riacho) valida: o material
criativo deve surgir da estrutura narrativa, não precedê-la.
Criatividade depois da estrutura, orientada por ela.

---

## 2. O que perdeu força

### `retention_plan` no Editorial Planner — REMOVIDO

O plano V1 (§4.3) propunha adicionar um `retention_plan` ao
Editorial Planner: "onde estão os pontos de risco (onde o
espectador pode sair) e como mitigar."

Após ler Kahneman's **duration neglect** (espectadores não lembram
quanto tempo cada seção durou — lembram de picos e finais), isto
parece over-engineering. O que importa é: há um pico claro? O final
é satisfatório? O Script Critic pode avaliar isto depois do fato.
Não precisamos que o planner pre-identifique "pontos de risco".

**Decisão**: Remover `retention_plan` do Editorial Planner.

### `comment_potential` no Curiosity Scorer — REMOVIDO

Peso 0.05 no V1. Essencialmente ruído. Se um fato gera comentários
é função da apresentação, não do fato. Um fato chato apresentado
bem gera comentários; um fato interessante apresentado mal não.

**Decisão**: Remover `comment_potential`. Reduzir de 6 para 5
sub-scores.

### `tension` no Curiosity Scorer — REMOVIDO

Overlap com `surprise_potential`. Ambos medem "quebra de
expectativa". Manter os dois é redundante.

**Decisão**: Remover `tension`. `surprise_potential` já cobre
"quebra de expectativa".

### `retention_strategy` no StoryConcept — REMOVIDO

Conflação de responsabilidades. O Story Finder encontra o ângulo;
o Editorial Planner desenha como segurar a atenção. `retention_strategy`
é o planner's job, não o finder's. O plano V1 critica Content Planning
por conflação de responsabilidades — não deveria repetir o mesmo erro
no StoryConcept.

**Decisão**: Remover `retention_strategy` do StoryConcept.

### `tension` e `surprise` no StoryConcept — REMOVIDOS

Overlap com `angle` e `curiosity_gap`. `angle` já captura "o giro
editorial"; `curiosity_gap` já captura "a lacuna". Campos separados
para `tension` e `surprise` são redundantes.

**Decisão**: Remover `tension` e `surprise` do StoryConcept.

### `why_care` no StoryConcept — REMOVIDO

Vago e implícito em `curiosity_gap`. Se a lacuna é clara, o "por que
se importar" é implícito. Um campo separado não adiciona valor.

**Decisão**: Remover `why_care` do StoryConcept.

### Fase 7 (Refatorar Content Planning) — REMOVIDA

Content Planning naturalmente simplifica quando Story Finder e
Editorial Planner assumem suas responsabilidades (Fases 2-3). Isso
acontece como consequência, não como um esforço separado. Uma fase
dedicada é trabalho desnecessário.

**Decisão**: Remover Fase 7. A simplificação de Content Planning
é parte da Fase 2.

### Fase 6 (Integrar Story Concept) — MERGEADA na Fase 2

A única adição da Fase 6 é passar StoryConcept para o ScriptService.
Isso é uma mudança de uma linha que deveria ser parte da Fase 2
(Story Finder), não uma fase separada.

**Decisão**: Mergear Fase 6 na Fase 2.

### "Edições cirúrgicas" no Script Critic v2 — REESCRITA

O V1 promete "edições cirúrgicas: 'na linha X, troque por Y'". LLMs
não fazem isso de forma confiável — não têm granularidade de linha
precisa. Na prática, "edições cirúrgicas" de LLM viram "reescreva
esta seção", que é apenas uma regeneração menor.

**Decisão**: Reescrever para "revisão por seção" — regenerar apenas
a seção problemática (hook, desenvolvimento, conclusão), não o
script inteiro. Mais honesto e mais factível.

---

## 3. O que realmente merece entrar no plano

Das 18 hipóteses do diário de pesquisa, 4 justificam mudanças
concretas na arquitetura:

### H-PC-1: `familiarity` no Curiosity Scorer — ADICIONADO

Loewenstein's curva invertida U: curiosidade requer base de
conhecimento. Fatos sobre jogos conhecidos > jogos obscuros. O
manifesto diz "o familiar é mais poderoso que o obscuro". O scorer
V1 não mede familiarity. Isto é uma contradição entre manifesto e
plano.

**Mudança**: Adicionar `familiarity` (0.15) ao Curiosity Scorer.
Para game-specific facts: familiarity do jogo. Para general
curiosity facts: familiarity do tópico (não do jogo de fundo).

### H-PC-3: `is_insight` no Curiosity Scorer e StoryConcept — ADICIONADO

Loewenstein distingue "insight" (uma peça ilumina o todo) de
"trivia" (detalhe isolado). Curiosidade é maior para insight.
O scorer V1 não distingue.

**Mudança**: Adicionar `insight_quality` (0.10) ao Curiosity Scorer.
Adicionar `is_insight: bool` ao StoryConcept.

### H-K-3: `frame` no StoryConcept — ADICIONADO

Kahneman's framing: mesmo fato, diferente frame, diferente impacto.
"5% completam" vs. "95% falham". O frame deveria ser uma decisão
editorial explícita, não acidental.

**Mudança**: Adicionar `frame: str` ao StoryConcept.

### H-MTS-2: Identificação com ignorância no Humanization — ADICIONADO

Heath's Maldição do Conhecimento: a IA não lembra como era não saber.
A técnica de "identificação com a ignorância" ("eu também não sabia",
"demorei pra entender isso") é uma correção direta para isto.

**Mudança**: Adicionar à lista de funções do Humanization Pass:
"injeção de identificação com a ignorância — frases que reconhecem
que o narrador também não sabia, criando cumplicidade".

### Hipóteses que permanecem apenas como conhecimento editorial

As 14 hipóteses restantes permanecem no `EDITORIAL_RESEARCH_JOURNAL.md`
como repertório editorial. Não justificam mudanças arquiteturais porque:

- São sobre craft de script (H-MTS-3, H-SB-3, H-PC-4, H-PC-5, H-K-4,
  H-K-5) — devem influenciar prompts, não arquitetura
- São sobre seleção de fatos mas muito vagas para medir (H-MTS-1,
  H-SB-2)
- São sobre postura enunciativa (H-SB-1) — já parcialmente no plano
  via mudança de prompt do Script Service
- São fora do escopo do pipeline editorial (H-K-2 — gameplay
  selection é outro subsistema)

---

## 4. O plano continua simples?

### Simplificações aplicadas

1. **Curiosity Scorer**: 6 → 5 sub-scores. Removidos `comment_potential`
   (mede coisa errada) e `tension` (redundante com `surprise_potential`).
   Adicionados `familiarity` e `insight_quality` com base teórica
   direta.

2. **StoryConcept**: 11 → 9 campos. Removidos `tension`, `surprise`,
   `why_care`, `retention_strategy` (redundantes ou conflagam
   responsabilidades). Adicionados `frame` e `is_insight`.

3. **Editorial Planner**: Removido `retention_plan` (over-engineering
   baseado em duration neglect).

4. **Fases**: 8 → 6. Removidas Fase 6 (mergeada na 2) e Fase 7
   (consequência natural da 2).

5. **Script Critic v2**: "edições cirúrgicas" → "revisão por seção"
   (mais honesto e factível).

### Complexidade desnecessária identificada e removida

- `retention_plan` era uma solução sofisticada para um problema que
  Kahneman mostra ser mais simples (peak-end rule, não análise de
  pontos de risco)
- 6 sub-scores era mais do que o necessário — 5 com base teórica
  direta é suficiente
- 11 campos no StoryConcept era excessivo — 9 cobrem o necessário
- Fases 6 e 7 eram trabalho que acontece naturalmente em outras fases

---

## 5. Contradições encontradas e resolvidas

### Contradição 1: "Remover optimize step" vs. ninguém assume length/TTS

**Problema**: V1 §4.5 diz "Remover: o optimize step focado em TTS
suitability. A humanização substitui isso." Mas a descrição do
Humanization Pass não menciona length ou TTS suitability — só
AI-isms, ritmo, redundância, oralidade.

**Resolução**: O Script Service mantém um check de length/TTS
**após** a humanização (não antes, como o optimize atual). A
humanização pode alterar o length; o check final garante que o
resultado está dentro dos bounds. Não é um "optimize step" editorial
— é uma verificação técnica.

### Contradição 2: Content Planning "não desenhar narrativa" mas define tone/music_mood

**Problema**: V1 §4.2 diz que Content Planning cria "ContentPlan
básico (topic, tone, music_mood)". Mas `tone` e `music_mood` são
decisões narrativas. Se Content Planning é só "selecionar o fato",
não deveria decidir tone.

**Resolução**: Content Planning seleciona o fato e define apenas
parâmetros técnicos (`target_duration`, `video_format`). `tone` e
`music_mood` movem-se para o Editorial Planner (que já define tone
via VideoCreativePlan).

### Contradição 3: Manifesto diz "familiar > obscuro" mas scorer não mede familiarity

**Problema**: O manifesto diz "o familiar é mais poderoso que o
obscuro". O Curiosity Scorer V1 não inclui familiarity. Isto é uma
contradição entre o filtro editorial (manifesto) e a implementação
(plano).

**Resolução**: Adicionar `familiarity` ao Curiosity Scorer. Para
game-specific facts: familiarity do jogo. Para general curiosity
facts: familiarity do tópico.

### Contradição 4: Dois gates para "deveríamos fazer este vídeo?"

**Problema**: V1 tem dois gates: (1) curiosity_score threshold em
Content Planning, (2) `is_story` check em Story Finder. Podem
conflitar: fato com high curiosity_score pode ter `is_story=false`;
fato com low curiosity_score pode ter `is_story=true`.

**Resolução**: Explicitar a relação. curiosity_score determina
**candidatos** (top N). `is_story` determina se o candidato
selecionado **tem história**. Se não, tenta o próximo candidato.
Se nenhum tiver história, o job falha graciosamente. Os gates são
sequenciais, não concorrentes.

### Contradição 5: Familiaridade vs. general curiosity shorts

**Problema**: Se familiaridade é crucial para curiosidade
(Loewenstein), então general curiosity shorts (sobre tópicos não-
-game) sempre pontuariam baixo em familiarity. Mas o GPCG suporta
explicitamente este formato.

**Resolução**: Para general curiosity shorts, `familiarity` mede a
familiaridade do **tópico**, não do jogo de fundo. O jogo de fundo
é filler visual; a curiosidade é sobre o tópico. "Você sabia que
o cérebro sonha em tempo real?" tem familiarity alta (todo mundo
sonha), mesmo que não seja sobre um jogo.

---

## Resumo

### Mudanças incorporadas no V2

1. Curiosity Scorer: 6 → 5 sub-scores (removidos `comment_potential`,
   `tension`; adicionados `familiarity`, `insight_quality`)
2. StoryConcept: 11 → 9 campos (removidos `tension`, `surprise`,
   `why_care`, `retention_strategy`; adicionados `frame`, `is_insight`)
3. Editorial Planner: removido `retention_plan`
4. Content Planning: define apenas parâmetros técnicos, não tone/music_mood
5. Script Service: mantém check de length/TTS após humanização
6. Script Critic v2: "edições cirúrgicas" → "revisão por seção"
7. Humanization: adicionada "identificação com a ignorância"
8. Gates: explicitada relação sequencial (curiosity_score → is_story)
9. Fases: 8 → 6 (Fase 6 mergeada na 2, Fase 7 removida)
10. Familiarity: nuance explicitada para general curiosity shorts

### Ideias rejeitadas

- `retention_plan` no Editorial Planner (over-engineering)
- `comment_potential` no Curiosity Scorer (mede coisa errada)
- `tension` como sub-score separada (redundante)
- Fase 7 dedicada (consequência natural de outras fases)
- "Edições cirúrgicas" no Script Critic (LLMs não fazem isso
  de forma confiável)

### Permanecem apenas como conhecimento editorial

- H-MTS-1 (múltiplas conexões) — muito vago para medir
- H-MTS-3 (concretude numérica) — craft de script, não arquitetura
- H-SB-1 (espectador como sujeito) — já no prompt do Script Service
- H-SB-2 (stakes explícitos) — vago para general curiosity
- H-SB-3 (micro-transformação explícita) — craft de script
- H-PC-2 (estrutura suposição→quebra→gap→payoff) — já implícito no
  StoryConcept
- H-PC-4 (múltiplos gaps sequenciais) — craft de script
- H-PC-5 (transição diversiva→específica) — craft de hook
- H-K-1 (pico e final deliberados) — já no Script Critic via `payoff`
- H-K-2 (gameplay reforça narração) — outro subsistema
- H-K-4 (loss aversion no hook) — craft de hook
- H-K-5 (System 1 com System 2 na revelação) — craft de script

### Decisões mais sólidas após a residência

- Story Finder: confirmado por Loewenstein's information-gap theory
- Curiosity Scorer: confirmado por todos os autores, refinado com
  familiarity e insight
- Humanization: confirmado por Heath's Maldição do Conhecimento
- Script Critic v2: confirmado por Kahneman's peak-end rule
- Creative Engine beat-oriented: confirmado por Miller's plan concept
