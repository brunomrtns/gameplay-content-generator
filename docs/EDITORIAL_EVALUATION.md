# Metodologia de Avaliação Editorial

> **Status**: Processo permanente para avaliação de melhorias editoriais.
> **Objetivo**: Garantir que toda evolução do GPCG seja baseada em
> evidências, não em intuição. Definir como medir se uma mudança
> realmente melhorou o produto.
> **Não é implementação.** É processo e critérios.

---

## Premissa

O GPCG evolui por hipóteses. Algumas viram arquitetura. A maioria
não deveria.

Sem uma metodologia de avaliação, cada hipótese vira implementação
porque "parece fazer sentido". Isto é como arquiteturas crescem
infinitamente — por acúmulo de ideias não testadas.

Este documento define como testar, validar, registrar e decidir sobre
cada hipótese editorial. O objetivo não é provar que estamos certos.
É permitir que estejamos errados — e descobrir antes de aumentar a
complexidade do sistema.

---

## 1. Como comparar duas versões de um roteiro

### O formato A/B

Toda comparação editorial é **A/B com mesmo fato**.

- **Script A**: versão atual (baseline)
- **Script B**: versão com a mudança (candidate)

Ambos gerados a partir do **mesmo fato**, **mesmo jogo**, **mesmo
target_duration**. A única variável é a mudança sendo testada.

Sem isto, não há comparação — há impressão.

### Três camadas de avaliação

#### Camada 1: Avaliação estrutural (determinística)

Verificações objetivas que não dependem de julgamento humano:

| Critério | Como medir |
|----------|------------|
| Comprimento | `len(script)` dentro de bounds |
| Originalidade | `originality_score` (n-gram overlap) |
| Factual accuracy | Verificação contra source fact (LLM-as-judge ou humana) |
| AI-ism count | Regex/heurísticas: enumerações, conectivos excessivos, frases banidas |
| Variação de ritmo | Desvio-padrão do comprimento de frases |
| Densidade de perguntas | Contagem de "?" no texto |

**Quando usar**: sempre. É barato, é rápido, é reproduzível.

**Limitação**: não mede retenção, descoberta, ou humanidade. Mede
sintomas, não causas.

#### Camada 2: Avaliação editorial (julgamento humano)

Avaliação por um humano agindo como editor-chefe, usando os critérios
definidos em §2. Para cada critério, o avaliador atribui uma nota
(0-100) e justifica em uma frase.

**Formato**:
```
Critério: [nome]
Script A: [0-100] — [justificativa em uma frase]
Script B: [0-100] — [justificativa em uma frase]
Preferido: [A ou B ou empate]
```

**Quando usar**: quando a Camada 1 não diferencia claramente A e B,
ou quando a mudança testada afeta dimensões subjetivas (humanidade,
descoberta, ritmo).

**Limitação**: subjetiva, não reproduzível perfeitamente, suscetível
a viés do avaliador. Mitigação: dois avaliadores independentes quando
possível; desempate por discussão.

#### Camada 3: Avaliação de público (quando disponível)

Métricas do YouTube após publicação:

| Métrica | O que mede |
|---------|-----------|
| Retenção média | O vídeo prende até o fim? |
| Retenção dos primeiros 3s | O hook funciona? |
| Retenção no segundo pico | O payoff entrega? |
| Like ratio | Satisfação |
| Comentários por view | Engajamento |
| Shares por view | Compartilhabilidade |

**Quando usar**: quando o vídeo é publicado. É a validação final.

**Limitação**: confundida por thumbnail, título, timing, algoritmo.
Não isola a variável editorial. Útil para tendências, não para
atribuição causal precisa.

### Regra de decisão A/B

Uma mudança é considerada **melhoria** quando:

1. Camada 1: Script B não piora em nenhum critério estrutural
2. Camada 2: Script B é preferido em **pelo menos 2 critérios** da
   Camada 2, sem ser pior em nenhum
3. Camada 3 (se disponível): retenção média de B ≥ A

Uma mudança é considerada **neutra** quando:
- Camadas 1 e 2 não diferenciam claramente
- Não justifica aumento de complexidade

Uma mudança é considerada **regressão** quando:
- Qualquer camada mostra B pior que A

**Neutro = não implementar.** Não há "implementar porque não piorou".

---

## 2. Métricas humanas que realmente importam

### Os 6 critérios do GPCG

Após a residência editorial, os critérios que fazem sentido para o
GPCG são derivados diretamente do manifesto e dos princípios. Não são
todos os critérios possíveis — são os que servem à identidade editorial.

#### 1. Sensação de descoberta (0-100)

**O que mede**: O vídeo provoca o momento "nossa, sério?" — a
descoberta que o manifesto define como o propósito do GPCG?

**Como avaliar**: O avaliador se coloca como espectador casual e
pergunta: "eu saí sabendo algo que muda como vejo este jogo/tópico?"
Se sim, score alto. Se só aprendi um fato, score médio. Se nada
mudou, score baixo.

**Por que importa**: É o critério central do manifesto. Sem
descoberta, o vídeo falhou — mesmo que factualmente correto.

#### 2. Curiosidade (0-100)

**O que mede**: O vídeo cria uma lacuna que o espectador quer
preencher? (Loewenstein's information-gap)

**Como avaliar**: O hook cria uma pergunta? O vídeo sustenta a
pergunta até o payoff? Ou entrega a resposta cedo demais e o resto
é explicação?

**Por que importa**: Loewenstein mostra que curiosidade é o
mecanismo de retenção. Sem lacuna, não há motivação para continuar.

#### 3. Voz humana (0-100)

**O que mede**: O roteiro soa como uma pessoa falando, ou como IA
gerando texto?

**Como avaliar**: Detectar AI-isms (enumerações, conectivos
excessivos, "prepare-se para", estrutura previsível, adjetivos
vazios). Quanto mais AI-isms, menor o score. O texto tem opinião
implícita? Tem assimetria? Tem momentos de vulnerabilidade ("eu
também não sabia")?

**Por que importa**: O manifesto recusa a "perfeição artificial".
A humanidade está na imperfeição intencional.

#### 4. Clareza (0-100)

**O que mede**: O espectador consegue seguir o raciocínio sem se
perder?

**Como avaliar**: Há saltos lógicos? Há jargão não explicado? Há
informação redundante? O contexto vem antes da revelação?

**Por que importa**: Miller's "if you confuse, you lose". A
descoberta só acontece se o espectador entende.

#### 5. Ritmo (0-100)

**O que mede**: O vídeo varia velocidade — momentos rápidos e
lentos, frases curtas e longas?

**Como avaliar**: Variação no comprimento de frases. Há pausas?
Há acelerações? Ou é monótono? Há um pico claro (Kahneman's
peak-end)?

**Por que importa**: O manifesto diz "tem ritmo — varia, pausa,
acelera, respira". Ritmo monótono é assinatura de IA.

#### 6. Payoff (0-100)

**O que mede**: O vídeo entrega o que o hook promete? O final é
satisfatório ou anticlímax?

**Como avaliar**: O hook faz uma promessa. O payoff cumpre? Há
implicação (o manifesto diz "termina com implicação, não com
resumo")? Ou é "e é por isso que X. Até o próximo vídeo"?

**Por que importa**: Kahneman's peak-end rule. O final domina a
memória. Payoff fraco = vídeo esquecido.

### Critérios que NÃO usamos

- **Memorabilidade**: difícil de medir sem teste com público real
  e delay. Redundante com payoff + descoberta.
- **Vontade de compartilhar**: confundida por fatores não-editoriais
  (thumbnail, timing). Medido via Camada 3 (YouTube metrics).
- **Vontade de comentar**: idem. Medido via Camada 3.
- **Naturalidade**: absorvido por "voz humana". Termo vago.
- **Energia**: muito subjetivo, não alinhado com o manifesto (que
  recusa o "apresentador genérico").

### Factual accuracy como gate, não critério

Factual accuracy não é um critério de score — é um **gate**. Se o
script inventa mecânicas, ele é rejeitado independentemente dos
demais scores. Não há "factual accuracy = 40 mas descoberta = 90,
passa". Factual accuracy < 70 = rejeição automática.

---

## 3. Como validar novas hipóteses

### O pipeline de hipóteses

```
observação → hipótese → experimento → resultado → decisão
```

Cada etapa tem critérios claros.

### Etapa 1: Observação

Uma observação é algo notado durante a análise editorial ou a
revisão de vídeos gerados. Exemplo: "os hooks estão genéricos demais".

**Registro**: no `EDITORIAL_RESEARCH_JOURNAL.md` ou em discussão
direta. Ainda não é hipótese — é um sintoma.

### Etapa 2: Hipótese

Uma hipótese é uma proposta de causa + efeito. Formato obrigatório:

```
Hipótese H-XX: [proposta]
Causa: [o que mudar]
Efeito esperado: [que métrica deve melhorar]
Risco: [o que pode piorar]
Complexidade: [baixa/média/alta — quanto adiciona ao sistema]
```

**Critério para aceitar como hipótese**: deve ser **falsificável**.
"Melhorar os hooks" não é hipótese — é desejo. "Adicionar `frame`
ao StoryConcept aumenta curiosidade em vídeos game-specific" é
hipótese — pode ser testada e refutada.

**Registro**: `EDITORIAL_RESEARCH_JOURNAL.md`, seção de hipóteses.

### Etapa 3: Experimento

Um experimento testa a hipótese com A/B comparison (§1).

**Formato**:
- Selecionar N fatos (mínimo 5, ideal 10)
- Para cada fato, gerar Script A (baseline) e Script B (com a mudança)
- Avaliar com Camada 1 (estrutural) e Camada 2 (editorial)
- Registrar resultados

**Tamanho da amostra**: 5 fatos é o mínimo para detectar tendência.
10 é preferível. Menos que 5 é impressão, não experimento.

**Controle de variáveis**: apenas a mudança testada deve diferir
entre A e B. Mesmo fato, mesmo jogo, mesmo target_duration, mesmo
modelo LLM.

### Etapa 4: Resultado

Registrar o resultado no formato do §4 (registro de experimentos).

### Etapa 5: Decisão

Três outcomes possíveis:

| Resultado | Decisão |
|-----------|---------|
| Melhoria (§1 regra de decisão) | Promover a implementação |
| Neutro | Manter como conhecimento editorial. Não implementar. |
| Regressão | Descartar. Registrar como refutada. |

### Quando uma hipótese vira arquitetura

Uma hipótese só é promovida a requisito arquitetural quando:

1. **Melhoria confirmada em experimento A/B** (§1 regra de decisão)
2. **Complexidade justificada** — o ganho é proporcional ao custo
   de manutenção do novo componente/campo/prompt
3. **Consistente com o manifesto** — não contradiz a identidade
   editorial
4. **Não conflita com hipótese existente** — ou se conflita, a
   existente é refutada primeiro

**Barreira alta intencional.** A maioria das hipóteses deve
permanecer como conhecimento editorial. A arquitetura cresce apenas
quando há evidência clara de que a mudança melhora o produto.

---

## 4. Como registrar experimentos

### Formato de registro

Cada experimento é registrado em `docs/editorial_experiments/`
como um arquivo markdown numerado:

`docs/editorial_experiments/EXP-NNN-<short-name>.md`

```markdown
# Experimento EXP-NNN: [título]

## Hipótese
H-XX: [proposta]
Causa: [o que mudar]
Efeito esperado: [que métrica deve melhorar]
Risco: [o que pode piorar]
Complexidade: [baixa/média/alta]

## Motivo
[Por que esta hipótese surgiu. Que observação a gerou.]

## Experimento
- Fatos usados: [N fatos, listar IDs ou descrições]
- Script A (baseline): [descrição da versão atual]
- Script B (candidate): [descrição da mudança]
- Avaliadores: [quem avaliou]
- Data: [YYYY-MM-DD]

## Resultado

### Camada 1 (estrutural)
| Critério | A | B | Diferença |
|----------|---|---|-----------|
| ... | ... | ... | ... |

### Camada 2 (editorial)
| Critério | A | B | Preferido |
|----------|---|---|-----------|
| descoberta | XX | YY | A/B/empate |
| curiosidade | ... | ... | ... |
| voz humana | ... | ... | ... |
| clareza | ... | ... | ... |
| ritmo | ... | ... | ... |
| payoff | ... | ... | ... |

### Camada 3 (público, se disponível)
| Métrica | A | B |
|---------|---|---|
| retenção média | ... | ... |

## Decisão
[Melhoria / Neutro / Regressão]
[Promover a implementação / Manter como conhecimento / Descartar]

## Notas
[Observações adicionais, surpresas, vieses identificados]
```

### Histórico

O diretório `docs/editorial_experiments/` forma o histórico técnico
das decisões editoriais. Cada arquivo é permanente — mesmo
experimentos refutados são mantidos para referência futura.

---

## 5. Como evitar crescimento infinito da arquitetura

### O princípio do custo

**Toda adição à arquitetura tem custo permanente.** Um novo campo,
um novo componente, um novo prompt — tudo aumenta a superfície de
manutenção, teste e debugging. O custo não é só de implementação;
é de manutenção perpétua.

Portanto: **o ônus da prova está em quem propõe a adição, não em
quem resiste.**

### Os 4 filtros

Antes de uma hipótese ser promovida a arquitetura, deve passar por
4 filtros:

#### Filtro 1: Evidência

A hipótese foi testada em experimento A/B com ≥5 fatos e mostrou
melhoria pela regra de decisão (§1)?

- **Sim** → passa
- **Não** → permanece como conhecimento editorial

#### Filtro 2: Proporcionalidade

O ganho é proporcional à complexidade adicionada?

- **Complexidade baixa** (mudar prompt, adicionar campo): ganho
  pequeno é aceitável
- **Complexidade média** (novo sub-score, nova heurística): ganho
  deve ser claro em ≥2 critérios
- **Complexidade alta** (novo componente, novo estágio): ganho
  deve ser grande em ≥3 critérios OU resolver um gargalo crítico

#### Filtro 3: Consistência

A mudança é consistente com:
- O manifesto editorial?
- Os princípios existentes?
- As hipóteses já implementadas?

Se contradiz qualquer um, **não passa**. O sistema não evolui por
acúmulo de ideias conflitantes.

#### Filtro 4: Irreversibilidade

A mudança é reversível?

- **Reversível** (feature flag, prompt, campo opcional): mais
  propenso a aceitar
- **Irreversível** (mudança de schema, remoção de componente):
  exige evidência mais forte

### A regra do "não implementar"

**Na dúvida, não implemente.**

Uma hipótese não implementada não custa nada. Uma hipótese
implementada que não funciona custa para reverter, custa para
manter, e custa na clareza do sistema.

O estado default é "não implementar". Implementar é a exceção que
requer evidência.

### Limite de complexidade

Se o número de componentes editoriais crescer além do que um
desenvolvedor consegue manter na cabeça, o sistema está complexo
demais. Sinais de excesso:

- Não é óbvio qual componente é responsável por qual decisão
- Mudar um prompt requer entender 3+ componentes
- Feature flags se acumulam sem remoção
- Experimentos não conseguem isolar variáveis

Quando isto acontece, **simplificar antes de adicionar**.

---

## 6. Hierarquia de conceitos

Para evitar confusão, os conceitos têm definições precisas e
relações claras:

```
opinião editorial → hipótese → princípio consolidado → requisito arquitetural → implementação
```

Cada conceito é um estágio de maturação. Um conceito só avança para
o próximo estágio com justificativa específica.

### Opinião editorial

**Definição**: Uma intuição ou observação sobre o que funciona
editorialmente, sem evidência formal.

**Onde vive**: discussões, `EDITORIAL_RESEARCH_JOURNAL.md`, cabeça
do editor.

**Exemplo**: "Acho que vídeos sobre GTA geram mais retenção que
vídeos sobre jogos obscuros."

**Pode virar**: hipótese (quando formalizada como proposta
falsificável).

### Hipótese

**Definição**: Uma proposta formal de causa + efeito, falsificável,
com métrica definida.

**Onde vive**: `EDITORIAL_RESEARCH_JOURNAL.md`, seção de hipóteses.

**Exemplo**: H-PC-1: "Adicionar `familiarity` ao Curiosity Scorer
aumenta curiosidade em vídeos game-specific, medida pelo critério
'curiosidade' da Camada 2."

**Pode virar**: princípio consolidado (após experimento confirmar)
OU ser descartada (após experimento refutar).

### Princípio consolidado

**Definição**: Uma hipótese que foi confirmada por experimento A/B
e é tratada como verdade editorial provisória.

**Onde vive**: `EDITORIAL_PRINCIPLES.md` (se for princípio geral)
ou `EDITORIAL_REFACTOR_PLAN_V2.md` (se virou requisito).

**Exemplo**: "Curiosidade requer base de conhecimento — fatos sobre
jogos conhecidos geram mais curiosidade que fatos sobre jogos
obscuros." (confirmado por Loewenstein + experimento)

**Pode virar**: requisito arquitetural (se justificar mudança
técnica) OU permanecer como princípio (se for apenas orientação
editorial).

### Requisito arquitetural

**Definição**: Uma mudança concreta na arquitetura do GPCG
(componente, campo, prompt, estágio) justificada por um princípio
consolidado.

**Onde vive**: `EDITORIAL_REFACTOR_PLAN_V2.md`.

**Exemplo**: "Adicionar coluna `curiosity_score` ao modelo `Fact`."

**Pode virar**: implementação (quando codificada).

### Implementação

**Definição**: Código que realiza um requisito arquitetural.

**Onde vive**: `src/gpcg/`.

**Exemplo**: `src/gpcg/application/curiosity_scorer.py`.

### Tabela de transição

| De | Para | Condição |
|----|------|----------|
| Opinião → Hipótese | Formalizar como proposta falsificável com métrica |
| Hipótese → Princípio | Experimento A/B confirma melhoria (§1 regra de decisão) |
| Hipótese → Descartada | Experimento A/B mostra regressão ou neutralidade |
| Princípio → Requisito | Passa pelos 4 filtros (§5) |
| Requisito → Implementação | Codificado, testado, feature-flagged |

### O que NUNCA pode acontecer

- Opinião → Implementação direta (pular hipótese e experimento)
- Hipótese → Requisito sem experimento (pular validação)
- Princípio → Implementação sem passar pelos 4 filtros (pular
  proporcionalidade e consistência)

---

## 7. Cadência de avaliação

### Durante implementação de uma fase

Cada fase do plano V2 deve incluir experimentos A/B antes de
ativar a feature flag permanentemente:

1. Implementar com feature flag off
2. Gerar 5-10 scripts A/B (baseline vs nova versão)
3. Avaliar Camada 1 + Camada 2
4. Se melhoria: ativar flag, monitorar
5. Se neutro/regressão: manter flag off, registrar experimento

### Após implementação

- Revisar métricas do YouTube (Camada 3) quando vídeos forem
  publicados
- Comparar tendências entre vídeos gerados com flag on vs off
- Ajustar prompts com base em observações
- Registrar tudo em `docs/editorial_experiments/`

### Limpeza periódica

A cada 3 meses:
- Revisar feature flags ativas — alguma deveria ser removida?
- Revisar hipóteses pendentes — alguma deveria ser testada?
- Revisar experimentos — algum resultado contradiz uma implementação
  ativa?
- Revisar complexidade — o sistema está mais simples ou mais
  complexo que 3 meses atrás?

**Tendência desejada**: complexidade estável ou decrescente. Se
crescendo, parar e simplificar antes de adicionar mais.

---

## 8. Aplicação imediata

### Para a Fase 1 (Curiosity Scoring)

Antes de ativar `GPCG_CURIOSITY_SCORING_ENABLED` permanentemente:

1. Rodar curiosity_scorer em fatos existentes (sem gerar vídeos)
2. Comparar top 10 fatos por curiosity_score vs top 10 por
   quality_score * novelty_score
3. Avaliar: os top 10 por curiosity_score parecem editorialmente
   melhores? (opinião editorial → hipótese implícita)
4. Gerar 5 vídeos A/B: 5 com scoring atual, 5 com curiosity scoring
5. Avaliar Camada 2 (descoberta, curiosidade, voz humana)
6. Decidir: ativar flag ou manter off

### Para hipóteses já promovidas no V2

As 4 hipóteses promovidas (familiarity, insight_quality, frame,
identificação com ignorância) foram promovidas com base teórica
(Loewenstein, Kahneman, Heath), não experimental. Isto é aceitável
para a primeira implementação, mas **devem ser validadas
experimentalmente após implementação**:

- Se o experimento confirmar: permanecem como implementação
- Se o experimento for neutro: considerar remoção
- Se o experimento refutar: remover

**Nenhuma hipótese é permanente.** Mesmo as já implementadas podem
ser removidas se a evidência mostrar que não funcionam.

---

## Síntese

### O método em uma página

1. **Comparar**: A/B com mesmo fato, três camadas (estrutural,
   editorial, público)
2. **Medir**: 6 critérios (descoberta, curiosidade, voz humana,
   clareza, ritmo, payoff) + factual accuracy como gate
3. **Validar**: observação → hipótese falsificável → experimento
   A/B → resultado → decisão
4. **Registrar**: `docs/editorial_experiments/EXP-NNN-<name>.md`
5. **Controlar**: 4 filtros (evidência, proporcionalidade,
   consistência, irreversibilidade) antes de promover a arquitetura
6. **Distinguir**: opinião → hipótese → princípio → requisito →
   implementação, com transições condicionais
7. **Na dúvida**: não implementar

### O princípio fundamental

> O GPCG evolui por evidências, não por acúmulo.
> Toda adição deve provar valor. Toda remoção é bem-vinda.
> Simples > completo. Consistente > abrangente.

---

*Este documento define o processo. Não define implementação.
Para o plano técnico, ver `EDITORIAL_REFACTOR_PLAN_V2.md`.
Para a identidade editorial, ver `EDITORIAL_MANIFESTO.md`.*
