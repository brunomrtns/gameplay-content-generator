# Diário de Pesquisa Editorial

> **Status**: Documento vivo de pesquisa. **Não é plano de implementação.**
> **Objetivo**: Registrar o conhecimento adquirido durante a residência editorial,
> amadurecendo o julgamento editorial para futuras decisões.
> **Relação com outros documentos**:
> - `EDITORIAL_REFACTOR_PLAN.md` — plano técnico aprovado (inalterado)
> - `EDITORIAL_PRINCIPLES.md` — princípios editoriais (inalterado)
> - Este documento é **complementar** e não substitui nenhum dos acima.

---

## Sobre este diário

Este documento registra o estudo de quatro obras fundamentais sobre
comunicação, narrativa, curiosidade e cognição. O objetivo não é
converter teorias em código, mas **amadurecer o julgamento editorial**
— da mesma forma que um editor-chefe estuda psicologia, narrativa e
comportamento humano ao longo da carreira.

Cada material é analisado como literatura de referência, não como
especificação. As reflexões aqui registradas podem reforçar princípios
existentes, levantar dúvidas, sugerir hipóteses, ou simplesmente
ampliar o repertório.

---

## Material 1: Made to Stick — Chip & Dan Heath

### Ideias centrais

O framework SUCCESs: ideias que "grudam" compartilham seis traços:

- **Simple**: encontrar o núcleo — a UMA coisa que o público deve lembrar
- **Unexpected**: quebrar padrões — não choque aleatório, mas surpresa
  que serve ao núcleo
- **Concrete**: tornar ideias tangíveis — números específicos, imagens
  vívidas, exemplos sensoriais em vez de abstrações
- **Credible**: construir confiança — não só autoridade, mas
  consistência, humildade, prova experiencial
- **Emotional**: fazer as pessoas se importarem — emoções são atalhos
  cognitivos, não complementos
- **Stories**: usar narrativa — histórias simulam experiência e inspiram
  ação

Conceitos adicionais que se destacaram:

- **A Maldição do Conhecimento**: esquecer como era não saber algo.
  Especialmente relevante para conteúdo gerado por IA, que "sabe demais"
  e assume conhecimento que o espectador não tem.
- **Teoria Velcro da Memória**: ideias grudam quando têm mais "ganchos"
  para se prender a memórias existentes. Conexões múltiplas =
  memorabilidade.
- **Curiosity Gap**: criar perguntas é mais poderoso que fornecer
  choques. O gap sustenta atenção; o choque é momentâneo.
- **Responder vs. Criar perguntas**: conteúdo que responde perguntas
  existentes compete com milhares. Conteúdo que cria perguntas novas
  diferencia.

### Reflexões

O framework SUCCESs valida empiricamente algo que sentíamos
intuitivamente: informação correta não retém. O que retém é a
**estrutura** da informação — como ela é simplificada, surpreendida,
concretizada, emocionalizada e narrativizada.

A distinção mais útil foi entre "surpresa" e "curiosity gap". Surpresa
é um choque momentâneo ("nossa!"). Curiosity gap é uma lacuna
sustentada ("espera, o quê? eu preciso saber"). A surpresa captura;
o gap mantém. Isso muda como penso sobre hooks: não procuro o "wow"
— procuro a pergunta que o espectador não sabia que tinha.

A Maldição do Conhecimento é particularmente relevante para o GPCG.
A IA "sabe" o fato, o jogo, o contexto. O espectador não sabe nada
disso. O roteiro gerado frequentemente assume conhecimento que o
espectador não tem — não por erro, mas porque a IA não lembra como
era não saber. Um criador humano lembra. Ele diz "eu demorei pra
entender isso" ou "no começo parece confuso, mas". A IA pula direto
para a explicação.

A Teoria Velcro sugere que fatos com múltiplas conexões (a jogos
conhecidos, a cultura gaming, a experiências compartilhadas) grudam
mais que fatos isolados. Um fato sobre um jogo obscuro tem um gancho.
Um fato que conecta um jogo conhecido a um padrão em vários jogos
tem vários ganchos.

### Relação com o GPCG

**Reforça princípios existentes**:
- "Curiosidade antes de informação" (EDITORIAL_PRINCIPLES §4.1) —
  o curiosity gap do livro é exatamente isto
- "Perspectiva além do fato" (§11.D) — o princípio Emotional sugere
  que emoção é o mecanismo, não um complemento
- "Criar perguntas antes de respondê-las" (§11.B) — responder vs.
  criar perguntas

**Levanta dúvidas**:
- O GPCG mede "quality_score" e "novelty_score", mas o framework
  sugere que **concreteness** (especificidade, tangibilidade) é tão
  importante quanto. Um fato pode ser novo e de qualidade mas abstrato
  demais para grudar. Deveríamos medir concretude?
- A Maldição do Conhecimento sugere que o roteiro deveria
  explicitamente endereçar o que o espectador **não** sabe. O GPCG
  atual não faz isso — assume que o espectador acompanha.

**Sugere hipóteses**:
- H-MTS-1: Fatos com múltiplas conexões (a outros jogos, a cultura
  gaming, a experiências comuns) deveriam ter score maior que fatos
  isolados. (Teoria Velcro)
- H-MTS-2: O roteiro deveria incluir um momento de "identificação com
  a ignorância" — o narrador reconhece que não sabia isso, criando
  cumplicidade com o espectador. (Maldição do Conhecimento)
- H-MTS-3: Números específicos ("47 segundos", "0.3 segundos",
  "1 em 1000") são mais memoráveis que adjetivos. O roteiro deveria
  preferir concretude numérica. (Concreteness)

### Impacto

- **Apenas repertório adquirido**: a estrutura SUCCESs como checklist
  mental para avaliar conteúdo
- **Reforça princípios existentes**: curiosity gap, emoção como
  mecanismo, criar perguntas
- **Hipótese para discussão futura**: H-MTS-1 (múltiplas conexões),
  H-MTS-2 (identificação com ignorância), H-MTS-3 (concretude numérica)

---

## Material 2: Building a StoryBrand — Donald Miller

### Ideias centrais

O framework SB7 (StoryBrand 7):

1. **Character**: o cliente quer algo
2. **Problem**: algo está no caminho
3. **Guide**: alguém pode ajudar
4. **Plan**: o guia oferece um plano
5. **Call to Action**: desafiar a agir
6. **Failure**: o que acontece se não agir
7. **Success**: a transformação que resulta

Princípios-chave:

- **O cliente é o herói, não a marca** — a marca é o guia (Yoda/Luke)
- **Problemas internos vendem mais que externos** — clientes compram
  soluções para como se sentem, não para o que acontece
- **Se você confundir, você perde** — clareza é estratégia de
  sobrevivência
- **Histórias são sobre transformação, não informação** — todo herói
  começa em um estado e termina em outro
- **Stakes são necessários** — sem o que perder, a história é chata
- **CTA deve ser claro e ousado** — corpos em repouso tendem a
  permanecer em repouso

### Reflexões

O framework SB7 foi desenhado para marketing, mas seu princípio
central é profundamente relevante: **o espectador é o herói, não o
canal, não o jogo, não o fato**.

Isso muda a postura enunciativa. O GPCG atual fala **sobre** o fato:
"O jogo usa Euphoria. Euphoria é um sistema de física procedural."
A perspectiva é a do fato — o fato é o sujeito.

A abordagem herói-como-espectador muda para: "Você jogou isso centenas
de vezes e nunca percebeu. Mas tem uma coisa acontecendo que muda tudo."
Agora o espectador é o sujeito. O fato é o que ele **descobre**, não
o que é **descrito**.

A distinção entre problema interno e externo é reveladora. O GPCG
trata o problema como "o espectador não sabe este fato" (externo).
Mas o problema real é interno: "o espectador está entediado, quer
algo interessante, quer se sentir esperto." Resolver o problema
interno é mais valioso que resolver o externo.

A ideia de transformação micro é poderosa. Em 60 segundos, o
espectador não muda de vida. Mas ele pode mudar de estado:
- Ignorante → informado
- Indiferente → surpreso
- Sem nada a dizer → com algo a compartilhar
- Vendo o jogo de um jeito → vendo de outro

Essa micro-transformação é o que faz o vídeo valer a pena. Não é a
informação — é a **mudança de estado** que a informação provoca.

"If you confuse, you lose" é a frase mais importante do livro para
o GPCG. Fatos de jogos podem ser técnicos — física procedural, hex
editing, engine internals. A tentação é despejar a informação. Mas
a confusão mata o engajamento mais rápido que o tédio.

### Relação com o GPCG

**Reforça princípios existentes**:
- "O roteiro correto responde 'o que é X?', o viciante responde 'por
  que X importa?'" (EDITORIAL_PRINCIPLES §1) — isto é transformação
  vs. informação
- "Identificação com o espectador" (§4.4) — o herói é o espectador
- "Perspectiva além do fato" (§11.D) — transformação requer
  perspectiva

**Levanta dúvidas**:
- O GPCG atual posiciona o **fato** como herói (o fato é o sujeito
  da narração). Miller sugeriria que isso é o erro que marcas
  cometem — posicionar a si mesmas como heróis. O fato deveria ser
  o **presente do guia para o herói**, não o herói em si.
- "Stakes" é um conceito que o GPCG não tem. O roteiro atual não
  articula o que o espectador perde se não assistir. Sem stakes,
  não há urgência.

**Sugere hipóteses**:
- H-SB-1: O roteiro deveria posicionar o espectador como sujeito
  ("você") e o fato como descoberta, não como descrição. Mudar a
  postura enunciativa de "sobre" para "para".
- H-SB-2: Todo roteiro deveria articular stakes — o que o espectador
  ganha (capital social, perspectiva) ou perde (FOMO, ignorância)
  com este vídeo.
- H-SB-3: O roteiro deveria ter uma micro-transformação explícita:
  "antes você via o jogo de um jeito, agora vê de outro." Não só
  informar, mas marcar a mudança de estado.

### Impacto

- **Apenas repertório adquirido**: framework SB7 como referência
  mental, postura guia-vs-herói
- **Reforça princípios existentes**: transformação vs. informação,
  identificação, perspectiva
- **Hipótese para discussão futura**: H-SB-1 (espectador como
  sujeito), H-SB-2 (stakes explícitos), H-SB-3 (micro-transformação
  explícita)

---

## Material 3: The Psychology of Curiosity — George Loewenstein

### Ideias centrais

A **information-gap theory** da curiosidade:

- Curiosidade é uma forma de **deprivação cognitiva induzida** — um
  estado aversivo que motiva a obter informação faltante para
  eliminar a sensação de privação.
- O gap é definido por duas quantidades: (1) o que você sabe, (2) o
  que você quer saber (referência). Curiosidade ocorre quando a
  referência se eleva acima do conhecimento atual.
- Curiosidade é **específica e epistêmica**, não generalizada. Não é
  busca de novidade — é busca de informação **particular** que falta.

Condições que disparam curiosidade:

1. Confronto direto com informação faltante (perguntas, enigmas)
2. Resolução antecipada mas desconhecida (quem ganha, o que acontece)
3. Violação de expectativas (coisas que não fazem sentido)
4. Posse de informação por outros (comparação social)
5. Conquistas passadas como referência (ponta da língua)

A **curva invertida U**: curiosidade é **positivamente** relacionada
ao conhecimento. Quanto mais você sabe sobre um tópico, mais você
foca no que **não** sabe. Sem base de conhecimento, não há gap
perceptível, não há curiosidade.

A **saciação da curiosidade**: curiosidade é aversiva. O prazer vem
de satisfazê-la, não de senti-la. Quando o gap fecha, a força
motivacional desaparece. E a satisfação frequentemente **decepciona**
— a informação é assimilada instantaneamente, a transição de
deprivação para neutralidade é fugaz.

### Reflexões

Este paper é o mais transformador dos quatro. Ele muda
fundamentalmente como penso sobre seleção de fatos e estrutura de
revelação.

**A descoberta mais importante**: curiosidade não é sobre novidade.
É sobre **lacunas perceptíveis**. Um fato completamente obscuro não
gera curiosidade — não há base de conhecimento para perceber o gap.
Um fato que conecta a algo que o espectador já sabe, mas revela um
aspecto que ele não sabia, gera curiosidade máxima.

Isso inverte a intuição. Pensávamos: "fatos mais obscuros = mais
curiosos." Loewenstein diz: "fatos que conectam ao conhecido = mais
curiosos." O ponto ideal é o **near-miss** — algo que o espectador
quase sabe, mas não sabe. A zona da ponta da língua.

**A saciação explica a queda de retenção**: quando o GPCG entrega o
fato nos primeiros 10 segundos, o gap fecha. A força motivacional
desaparece. O espectador não tem motivo para continuar. Os
50 segundos restantes são explicação de algo que já foi revelado.

A implicação é radical: **atrasar a revelação**. O fato não é o
começo — é o payoff. O começo é a **lacuna**. O meio é a
**manutenção do gap**. O final é o **fechamento**.

A distinção entre curiosidade específica e diversiva é crucial.
Espectadores rolando Shorts estão em curiosidade diversiva (busca
de estímulo, baixo compromisso). O hook precisa capturar (diversiva),
mas deve **imediatamente** transitar para curiosidade específica
("eu preciso saber ESTA coisa"). Essa transição deve acontecer nos
primeiros 2-3 segundos.

A curva invertida U sugere que devemos preferir fatos sobre jogos
que o espectador **já conhece**. Fatos sobre jogos obscuros não
geram curiosidade porque não há base. Fatos sobre Minecraft, GTA,
Mario — jogos que o espectador já jogou — geram curiosidade porque
há base de conhecimento para perceber o gap.

### Relação com o GPCG

**Reforça princípios existentes**:
- "Curiosidade não é propriedade do fato, é propriedade da
  apresentação" (EDITORIAL_PRINCIPLES §5) — Loewenstein confirma
  empiricamente
- "A anatomia da curiosidade: lacuna + relevância + atingibilidade"
  (§5) — corresponde à information-gap theory
- "Atraso deliberado da informação" (H5) — a saciação da curiosidade
  justifica isto

**Contradiz ou desafia**:
- A hipótese de que fatos mais obscuros são mais curiosos está
  errada. Loewenstein mostra o oposto: curiosidade requer base de
  conhecimento. Fatos sobre jogos conhecidos > fatos sobre jogos
  obscuros.
- A prática atual de entregar o fato no "development" beat e depois
  explicar é contraproducente. A saciação mata a motivação. O fato
  deveria ser o payoff, não o desenvolvimento.

**Sugere hipóteses**:
- H-PC-1: Fatos sobre jogos populares (alta base de conhecimento do
  espectador) geram mais curiosidade que fatos sobre jogos obscuros.
  O curiosity_score deveria pesar familiarity do jogo.
- H-PC-2: O roteiro deveria estruturar como: (1) estabelecer
  expectativa, (2) violar expectativa, (3) manter gap, (4) fechar
  gap no payoff. Não: (1) revelar fato, (2) explicar fato.
- H-PC-3: Fatos do tipo "insight" (uma peça que ilumina o todo)
  geram mais curiosidade que fatos do tipo "trivia" (um detalhe
  isolado). O curiosity_score deveria distinguir.
- H-PC-4: O roteiro deveria criar **múltiplos gaps sequenciais**
  em vez de um único gap. Quando o primeiro gap fecha, um segundo
  já está aberto, mantendo a motivação.
- H-PC-5: O hook deveria fazer a transição diversiva → específica
  em 2-3 segundos. "Isto parece interessante" (diversiva) → "eu
  preciso saber por que isso acontece" (específica).

### Impacto

- **Reforça princípios existentes**: curiosidade como apresentação,
  anatomia da curiosidade, atraso da informação
- **Desafia hipótese existente**: a ideia de que novidade = curiosidade
  está errada. Conhecimento prévio = curiosidade.
- **Hipóteses para discussão futura**: H-PC-1 (familiarity do jogo),
  H-PC-2 (estrutura suposição→quebra→gap→payoff), H-PC-3 (insight vs.
  trivia), H-PC-4 (gaps sequenciais), H-PC-5 (transição
  diversiva→específica)

---

## Material 4: Thinking, Fast and Slow — Daniel Kahneman

### Ideias centrais

A teoria de processo duplo da cognição:

- **System 1**: rápido, automático, intuitivo, sem esforço, sem senso
  de controle voluntário
- **System 2**: lento, deliberado, analítico, exige esforço, associado
  a agência e escolha

Conceitos-chave para conteúdo:

- **Esforço cognitivo é limitado**: em pico de carga, pessoas ficam
  "efetivamente cegas" para conteúdo subsequente
- **Cognitive ease vs. strain**: facilidade = bom humor, confiança,
  aceitação superficial. Tensão = vigilância, esforço, processamento
  cuidadoso
- **Anchoring effect**: a primeira informação colore tudo que segue
- **Framing effects**: mesma informação, diferente apresentação =
  diferente percepção
- **Peak-end rule**: experiências são julgadas pelo pico e pelo final,
  não pela média
- **Duration neglect**: pessoas não lembram quanto tempo algo durou
  — lembram de picos e finais
- **Narrative fallacy**: construímos histórias para explicar o passado,
  mesmo quando não correspondem à realidade
- **WYSIATI (What You See Is All There Is)**: julgamos com informação
  disponível, não com o que falta
- **Focusing illusion**: "nada na vida é tão importante quanto você
  acha que é enquanto você está pensando nisso"
- **Loss aversion**: perdas pesam mais que ganhos (ratio 1.5-2.5)
- **Availability heuristic**: julgamos frequência pela facilidade de
  exemplos virem à mente

### Reflexões

Kahneman oferece o substrato cognitivo para tudo o que os outros
autores descrevem. Ele explica **por que** as técnicas funcionam,
não apenas **que** funcionam.

**System 1 vs. System 2 e o feed de Shorts**: espectadores rolando
Shorts estão em System 1 — automático, intuitivo, sem esforço. A
decisão de continuar ou swipar é System 1. System 2 é "lazy" e só
engaja quando algo exige atenção deliberada.

Isso significa que o conteúdo deve ser **processável por System 1**
na maior parte do tempo — linguagem simples, padrões familiares,
narrativa intuitiva. Mas deve **ativar System 2 estrategicamente**
em momentos-chave — a revelação, o twist, o "espera, o quê?".

A dança entre os dois sistemas é o que cria retenção. System 1 mantém
o espectador confortável. System 2 cria os momentos de "nossa" que
ficam na memória.

**Peak-end rule e duration neglect**: isto liberta do obsession com
pacing perfeito. Espectadores não lembram quanto tempo cada seção
durou — lembram do pico e do final. Um vídeo com 15 segundos mais
"fracos" no meio mas com um pico forte e final satisfatório é
lembrado melhor que um vídeo uniformemente "ok".

Para o GPCG, isso sugere: não tente manter engajamento uniforme.
**Concentre energia no pico (revelação) e no final (implicação)**.
O meio pode ser build-up — não precisa ser empolgante, precisa ser
claro.

**WYSIATI e mostrar vs. contar**: o que o espectador VÊ é tudo que
ele considera. Se dizemos "isso é arriscado" mas só mostramos
funcionando, o espectador acredita que é seguro. A narrativa visual
sobrescreve a verbal.

Para o GPCG, isso é crítico: o gameplay de fundo precisa
**reforçar** a narração, não contradizê-la. Se o roteiro fala sobre
um glitch raro, o gameplay deveria mostrar o glitch, não gameplay
genérico.

**Focusing illusion**: podemos fazer qualquer fato parecer importante
direcionando atenção para ele. "Nada é tão importante quanto você
acha que é enquanto pensa nisso." Isso é o mecanismo por trás de
fatos triviais que parecem fascinantes em bons vídeos.

Mas há uma advertência: se tudo parece importante, nada parece.
O focusing illusion funciona melhor quando usado
**estrategicamente** — elevando momentos específicos, não tudo.

**Loss aversion e hooks**: "você esteve perdendo isso" é mais
poderoso que "você poderia ganhar isso". Perdas pesam 1.5-2.5x mais
que ganhos. Mas deve ser usado com cuidado — estados emocionais
negativos reduzem compartilhamento.

**Framing**: o mesmo fato, diferente frame, diferente impacto.
"5% dos jogadores completam isto" (exclusividade) vs. "95% dos
jogadores falham" (dificuldade). O GPCG deveria escolher frames
deliberadamente, não acidentalmente.

### Relação com o GPCG

**Reforça princípios existentes**:
- "O arco emocional, não apenas informativo" (EDITORIAL_PRINCIPLES
  §3.4) — peak-end rule explica por que variação emocional importa
- "Ritmo como variável editorial" (H10) — duration neglect sugere
  que ritmo não precisa ser uniforme, mas precisa ter pico e final
- "Simulação de espectador" (H2) — WYSIATI sugere que o que o
  espectador vê é o que ele julga

**Levanta dúvidas**:
- O GPCG trata todos os fatos com o mesmo frame. Kahneman sugere
  que o frame deveria ser uma **decisão editorial deliberada**.
  "5% completam" vs. "95% falham" mudam tudo.
- O GPCG otimiza para engajamento uniforme. Kahneman sugere que
  isso é esforço mal gasto — espectadores lembram picos e finais,
  não médias.

**Sugere hipóteses**:
- H-K-1: O roteiro deveria ter um **pico emocional** deliberado
  (revelação/surpresa) por volta de 60-75% do vídeo, e um **final
  satisfatório** (implicação, não cliffhanger). O meio pode ser
  build-up mais calmo.
- H-K-2: O gameplay de fundo deve **reforçar** a narração, não ser
  filler. WYSIATI: o que o espectador vê é o que ele julga. Gameplay
  contraditório undermines a narração.
- H-K-3: O frame do fato deveria ser uma decisão editorial
  explícita. Mesmo fato, diferentes frames = diferentes emoções.
  O StoryConcept deveria incluir `frame`.
- H-K-4: O hook deveria usar loss aversion ("você esteve perdendo
  isto") mas o final deveria usar gain framing ("agora você sabe").
  Começa com urgência, termina com satisfação.
- H-K-5: O roteiro deveria ser processável por System 1 na maior
  parte (linguagem simples, narrativa intuitiva) com ativação
  estratégica de System 2 no momento da revelação.

### Impacto

- **Apenas repertório adquirido**: dual-process theory, anchoring,
  availability heuristic como referência mental
- **Reforça princípios existentes**: arco emocional, ritmo,
  simulação de espectador
- **Hipóteses para discussão futura**: H-K-1 (pico e final
  deliberados), H-K-2 (gameplay reforça narração), H-K-3 (frame
  como decisão editorial), H-K-4 (loss aversion no hook, gain no
  final), H-K-5 (System 1 com ativação estratégica de System 2)

---

## Síntese Transversal

### Convergências — O que todos os autores concordam

1. **Informação não é suficiente**. Todos os quatro autores, de
   perspectivas completamente diferentes (comunicação, marketing,
   psicologia cognitiva, neurociência), chegam à mesma conclusão:
   informação correta não retém. O que retém é a **estrutura** —
   narrativa, emoção, gap, transformação.

2. **O receptor é o protagonista**. Heath fala em "público", Miller
   em "herói", Loewenstein em "indivíduo curioso", Kahneman em
   "System 1 e 2 do espectador". Todos dizem: o conteúdo existe
   para o receptor, não para o emissor.

3. **Clareza é não-negociável**. Heath: "find the core". Miller:
   "if you confuse, you lose". Kahneman: cognitive ease = aceitação.
   Loewenstein: gap deve ser perceptível, não confuso. Todos
   condenam a complexidade desnecessária.

4. **Surpresa direcionada, não aleatória**. Heath: "unexpectedness
   that serves the core". Loewenstein: "violação de expectativa que
   cria gap". Kahneman: "ativação estratégica de System 2". Todos
   distinguem entre choque random e surpresa significativa.

5. **O começo importa desproporcionalmente**. Heath: hook com
   curiosity gap. Miller: problema que captura. Kahneman: anchoring
   effect. Loewenstein: transição diversiva → específica. Todos
   dizem: os primeiros segundos determinam tudo.

### Divergências — Onde os autores diferem

1. **A natureza da curiosidade**. Heath trata curiosity gap como
   técnica de comunicação. Loewenstein mostra que é um estado
   psicológico aversivo que só pode ser **triggered**, não
   manufactured. A distinção importa: não podemos "fazer" alguém
   ficar curioso — só podemos criar condições onde o gap se torna
   perceptível.

2. **A estrutura ideal**. Miller oferece um framework de 7 passos.
   Heath oferece 6 princípios. Kahneman oferece peak-end. Loewenstein
   oferece gap → saciação. Não há uma estrutura "certa" — há
   princípios que se combinam.

3. **O papel da emoção**. Heath: emoção como atalho cognitivo.
   Miller: emoção como transformação. Kahneman: emoção como System 1
   automático. Loewenstein: emoção (deprivação) como mecanismo de
   curiosidade. Todos concordam que emoção é central, mas descrevem
   mecanismos diferentes.

### Descobertas mais transformadoras

1. **A curva invertida U de Loewenstein** (curiosidade aumenta com
   conhecimento) inverte a intuição sobre seleção de fatos. Fatos
   sobre jogos conhecidos > fatos sobre jogos obscuros.

2. **A saciação da curiosidade de Loewenstein** (o gap fechado mata
   a motivação) sugere que a estrutura atual do GPCG (fato cedo,
   explicação depois) é contraproducente. O fato deveria ser o
   payoff, não o começo.

3. **O peak-end rule de Kahneman** (memória = pico + final, não
   média) liberta do obsession com pacing uniforme. Concentrar
   energia no pico e no final é mais eficiente que tentar manter
   engajamento constante.

4. **WYSIATI de Kahneman** (o que você vê é tudo que você considera)
   eleva a importância do gameplay visual. O gameplay não é
   background — é evidência. Se contradiz a narração, a narração
   perde.

5. **A Maldição do Conhecimento de Heath** explica por que conteúdo
   gerado por IA soa artificial: a IA não lembra como era não saber.
   Um criador humano sim. A identificação com a ignorância é uma
   técnica que a IA não aplica naturalmente.

6. **O herói é o espectador de Miller** muda a postura enunciativa.
   O GPCG fala **sobre** o fato. Deveria falar **para** o
   espectador que **descobre** o fato.

### Hipóteses consolidadas

As hipóteses de todos os materiais, organizadas por tema:

**Seleção de fatos**:
- H-MTS-1: Fatos com múltiplas conexões > fatos isolados (Velcro)
- H-PC-1: Fatos sobre jogos conhecidos > jogos obscuros (curva U)
- H-PC-3: Fatos "insight" > fatos "trivia" (Loewenstein)

**Estrutura do roteiro**:
- H-PC-2: Suposição → quebra → gap → payoff (não: fato → explicação)
- H-PC-4: Múltiplos gaps sequenciais, não um único
- H-K-1: Pico emocional deliberado + final satisfatório (peak-end)
- H-K-5: System 1 na maior parte, System 2 na revelação

**Postura enunciativa**:
- H-SB-1: Espectador como sujeito, fato como descoberta
- H-MTS-2: Identificação com a ignorância ("eu também não sabia")
- H-SB-3: Micro-transformação explícita ("antes você via X, agora vê Y")

**Hooks**:
- H-PC-5: Transição diversiva → específica em 2-3 segundos
- H-K-4: Loss aversion no hook, gain framing no final

**Visual**:
- H-K-2: Gameplay reforça narração (WYSIATI)

**Stakes e frame**:
- H-SB-2: Stakes explícitos (o que ganha/perde)
- H-K-3: Frame como decisão editorial explícita

**Concretude**:
- H-MTS-3: Números específicos > adjetivos

---

## Próximos passos

Este diário é um documento vivo. As hipóteses aqui registradas
**não devem ser implementadas automaticamente**. Elas são
**material para discussão** futura.

Se e quando algumas dessas hipóteses forem confirmadas (por
experimentação, por análise de métricas, por intuição editorial
amadurecida), elas poderão ser migradas para:

- `EDITORIAL_PRINCIPLES.md` — como princípios confirmados
- `EDITORIAL_REFACTOR_PLAN.md` — como requisitos de implementação
  (na seção "Possíveis Evoluções Futuras")

Mas isso é para depois. Por ora, o objetivo foi alcançado: o
julgamento editorial foi ampliado. As futuras decisões técnicas e
editoriais poderão ser tomadas com mais maturidade.

---

*Documento vivo. Atualizado conforme novos materiais forem estudados
ou hipóteses forem confirmadas/refutadas.*
