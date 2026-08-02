# Princípios Editoriais — Pesquisa e Estratégia

> **Status**: Documento vivo de pesquisa editorial. **Não é plano de implementação.**
> **Objetivo**: Compreender por que uma pessoa continua assistindo a um vídeo até o final,
> e documentar os princípios editoriais que fazem conteúdo parecer feito por humanos.
> **Relação com o plano técnico**: O plano aprovado está em
> `docs/EDITORIAL_REFACTOR_PLAN.md`. Este documento é complementar. Ideias aqui
> registradas são **hipóteses para discussão** e não alteram o plano técnico
> sem aprovação explícita.

---

## Premissa

Esqueça código. Esqueça arquitetura. Esqueça LLMs.

Você é o editor-chefe de um canal de YouTube com milhões de inscritos.
Seu trabalho não é gerar texto. Seu trabalho é **aumentar retenção**.

Toda decisão editorial passa por uma pergunta:

> *Se eu fosse um espectador casual rolando o feed, eu assistira isso até o fim?*

Se a resposta for "provavelmente não", o vídeo não deveria existir.
Ou deveria existir de outra forma.

---

## 1. O que diferencia um roteiro correto de um roteiro viciante

### O roteiro correto

Um roteiro correto informa. Ele diz os fatos, na ordem certa, sem erros.
Cumpre o contrato de "vídeo sobre X". É verificável, factual, organizado.

**Características**:
- Estrutura lógica (introdução → desenvolvimento → conclusão)
- Linguagem clara
- Informação precisa
- Cobre o tópico

**Problema**: É o que uma enciclopédia faz. E ninguém assiste enciclopédias no YouTube.

### O roteiro viciante

Um roteiro viciante **cria uma experiência**. O espectador não está recebendo
informação — está vivendo uma descoberta. A informação é o veículo, não o destino.

**Características**:
- Começa com uma **lacuna** (não com uma resposta)
- Cria **tensão** entre o que o espectador sabe e o que ele vai descobrir
- Tem **ritmo**: acelera, desacelera, pausa
- Oferece **micro-recompensas** ao longo do caminho (não só no final)
- Termina com um **payoff** que vale a jornada
- Soa como alguém que **se importa** com o que está contando

### A diferença fundamental

O roteiro correto responde: *"O que é X?"*
O roteiro viciante responde: *"Por que X importa — e por que você deveria se importar agora?"*

**Exemplo**:

Correto: *"GTA IV usa o sistema Euphoria para animações."*
Viciante: *"Ninguém programou exatamente como o corpo do personagem cai. O jogo decide isso sozinho, em tempo real, toda vez. E o resultado é estranhamente humano."*

A informação é quase a mesma. Mas o segundo cria uma lacuna: *"como assim
ninguém programou? o jogo improvisa?"* — e o espectador precisa continuar
para entender.

---

## 2. O que faz um vídeo parecer feito por uma pessoa

### Padrões que denunciam IA

Texto gerado por IA tem **assinaturas** que o cérebro humano reconhece
instintivamente, mesmo sem saber nomear:

1. **Estrutura previsível**: toda frase tem o mesmo padrão sujeito-verbo-predicado.
2. **Conectivos excessivos**: "Além disso", "No entanto", "Por outro lado",
   "É importante notar que". Pessoas não falam assim.
3. **Enumerações**: "Primeiro... Segundo... Terceiro...". Pessoas falam
   "tem isso, e aquilo, e também uma coisa esquisita".
4. **Explicações redundantes**: dizer algo e imediatamente explicar o que
   acabou de dizer. "O jogo usa Euphoria. Ou seja, um sistema de física
   procedural. Isso significa que..."
5. **Tom de apresentador genérico**: "Prepare-se para descobrir...",
   "Você não vai acreditar no que aconteceu...", "E é aí que as coisas
   ficam interessantes."
6. **Adjetivos vazios**: "incrível", "fascinante", "surpreendente" sem
   justificativa. Pessoas dizem o que é incrível e deixam o ouvinte concluir.
7. **Conclusões explícitas**: "Em resumo, podemos ver que...". Pessoas
   terminam com uma observação, não com um resumo.
8. **Metáforas forçadas**: "É como se X encontrasse Y". A comparação não
   surge da narrativa — é enxertada.
9. **Simetria perfeita**: três pontos, cada um com a mesma extensão.
   Pessoas são assimétricas. Falam mais sobre o que acham mais interessante.
10. **Falta de opinião**: IA informa. Pessoas têm perspectiva. Mesmo
    sutil, há um ponto de vista: "isso é meio irônico", "na verdade é
    mais estranho do que parece".

### Padrões que denunciam humano

1. **Assimetria**: gasta mais tempo no que acha interessante, menos no
   que é contexto.
2. **Interrupções**: começa uma frase, muda de ideia, se corrige.
   "O jogo usa Euphoria — bom, na verdade não é bem Euphoria, é..."
3. **Opiniões implícitas**: não diz "isso é interessante", mas a forma
   como conta já implica.
4. **Referências pessoais**: "quando eu joguei isso pela primeira vez",
   "lembro de ter pensado", "demorei pra entender isso".
5. **Ritmo irregular**: frases curtas seguidas de uma longa. Pausas.
   Às vezes acelera, às vezes respira.
6. **Conexões inesperadas**: liga o fato a algo que não é óbvio. "É
   quase como aquele bug de Skyrim, mas ao contrário."
7. **Humor seco**: não é uma piada. É uma observação que acontece de
   ser engraçada. "Eles gastaram milhões pra um cara cair de forma
   realista. Prioridades."
8. **Vulnerabilidade**: admite confusão, dúvida, surpresa. "Demorei
   pra entender isso, mas quando entendi, mudou tudo."
9. **Economia**: não explica o que não precisa. Confia que o espectador
   acompanha.
10. **Fim sem encerramento**: termina no momento certo, sem amarrar tudo.
    Deixa algo no ar.

### Implicação para o GPCG

O sistema atual produz texto que tem **todas as informações certas** mas
que soa como uma **entidade que informa**, não uma **pessoa que descobriu**.

A diferença não está no conteúdo. Está na **postura enunciativa**:
- IA: "Aqui estão os fatos sobre X."
- Humano: "Olha só isso que eu descobri sobre X."

A segunda postura é intrinsecamente mais retentiva porque cria cumplicidade.
O espectador não está sendo informado — está acompanhando uma descoberta.

---

## 3. Como grandes canais conseguem manter retenção

### Princípio 1: O hook não é uma frase — é uma promessa

Canais de alta retenção não começam com "Você sabia que...".
Começam com uma **promessa específica** que o vídeo vai cumprir.

**Hook fraco**: "Hoje vamos falar sobre GTA IV."
**Hook forte**: "Tem uma coisa em GTA IV que ninguém percebeu em 15 anos —
e quando você percebe, não dá pra jogar do mesmo jeito."

O hook forte faz três coisas:
1. Cria **curiosidade** ("o que é?")
2. Cria **urgência** ("quando você percebe")
3. Cria **transformação** ("não dá pra jogar do mesmo jeito")

### Princípio 2: A informação é recompensa, não ponto de partida

Canais de alta retenção **atrasam** a informação. Eles não começam com a
resposta — começam com a pergunta. A informação é a **recompensa** por
continuar assistindo.

Estrutura típica:
1. Hook (promessa + lacuna)
2. Contexto (o mínimo necessário)
3. **Tensão** (por que isso é estranho/interessante?)
4. **Quase-resposta** (tá quase lá, mais um pouco)
5. Revelação (a informação, finalmente)
6. Implicação (o que isso muda?)

O GPCG atual entrega a informação no beat "development" e depois explica.
Canais de sucesso **atrasam** a revelação até que o espectador esteja
investido o suficiente para valorizá-la.

### Princípio 3: Micro-recompensas ao longo do vídeo

Retenção não é sobre o final. É sobre **não dar motivo para sair a cada
5 segundos**. Canais de sucesso inserem pequenas recompensas constantemente:

- Uma observação inesperada
- Uma conexão com algo que o espectador conhece
- Um detalhe visual que recompensa quem está prestando atenção
- Uma virada sutil ("mas tem um detalhe...")
- Uma pergunta que o espectador estava prestes a fazer

Cada micro-recompensa **reseta o relógio de atenção**. O espectador que
estava prestes a sair fica mais 5 segundos. E mais 5. E mais 5.

### Princípio 4: O vídeo tem um arco emocional, não apenas informativo

Canais de sucesso têm **variação emocional**:
- Surpresa → curiosidade → tensão → alívio → surpresa maior
- Confusão → clareza → "nossa" → implicação
- Nostalgia → reconhecimento → detalhe novo → recontextualização

O GPCG atual tem um arco plano: informa, informa, informa, conclui.
Não há variação emocional. É uma linha reta. Linhas retas não retêm.

### Princípio 5: O final é o segundo hook

Canais de alta retenção terminam de forma que **recompensa quem ficou**
e **faz querer o próximo vídeo**. O final não é um resumo — é uma
**implicação**.

**Final fraco**: "E é por isso que GTA IV usa Euphoria. Até o próximo vídeo!"
**Final forte**: "E o mais estranho? Isso foi feito em 2008. Treze anos antes
do que você tá pensando agora."

O final forte conecta com algo que o espectador já sabe (jogos mais
recentes) e cria uma nova lacuna.

---

## 4. Princípios psicológicos em vídeos de sucesso

### 4.1 Curiosity gap (a lacuna de conhecimento)

**O que é**: A diferença entre o que o espectador sabe e o que ele
**descobre que não sabe**. Não é sobre ignorância — é sobre **tornar
visível uma lacuna que o espectador não sabia que tinha**.

**Como funciona**: O cérebro humano é motivado a **completar padrões
incompletos**. Quando você apresenta algo que parece incompleto, o
espectador fica desconfortável e precisa continuar até completar.

**Exemplo**:
- Sem gap: "GTA IV usa Euphoria."
- Com gap: "Tem uma coisa que acontece em GTA IV que você nunca percebeu —
  mas agora que eu tô te falando, você vai notar toda vez."

O segundo cria uma lacuna: *"o que é? o que eu não percebi?"*

**Erro comum do GPCG**: O sistema apresenta a informação **sem criar a
lacuna primeiro**. Informa em vez de provocar.

### 4.2 Open loops (ciclos abertos)

**O que é**: Abrir uma "pergunta" no início do vídeo que só é respondida
no final. Pode ser uma pergunta literal ou uma promessa implícita.

**Como funciona**: O cérebro mantém a pergunta "aberta" enquanto não é
respondida. Esse "trabalho pendente" mantém a atenção.

**Exemplo**: "O detalhe mais estranho de GTA IV não está nos gráficos.
Não está na história. Está em algo que acontece toda vez que o personagem
cai — e quase ninguém percebeu."

O loop: *"o que acontece quando o personagem cai?"* — só fecha no final.

**Múltiplos loops**: Vídeos longos abrem vários loops em momentos
diferentes. Cada loop fecha em um momento diferente, criando um
**ritmo de recompensas**.

### 4.3 Pattern interruption (interrupção de padrão)

**O que é**: Quebrar um padrão estabelecido para resetar a atenção.

**Como funciona**: O cérebro adormece quando detecta padrão. Quebrar o
padrão força o cérebro a prestar atenção novamente.

**Exemplo**: Depois de três frases informativas, uma observação inesperada:
"Mas aqui é onde fica estranho." ou "E aí as coisas saem dos trilhos."

**No GPCG atual**: O roteiro é uma sequência informativa sem quebras.
O padrão é "fato → fato → fato → conclusão". Não há interrupção.

### 4.4 Identification (identificação)

**O que é**: O espectador se reconhece no conteúdo. "Eu já fiz isso",
"eu pensei isso também", "isso aconteceu comigo".

**Como funciona**: A identificação cria **investimento emocional**.
O espectador não está mais assistindo passivamente — está revendo uma
experiência própria.

**Exemplo**: "Você provavelmente jogou GTA IV e nem pensou nisso. Eu também
não. A gente assume que é só animação. Mas não é."

**No GPCG atual**: O roteiro fala **sobre** o jogo, não fala **com** quem
jogou. Não há "você" real — há um "você" genérico de apresentador.

### 4.5 Anticipation (antecipação)

**O que é**: Fazer o espectador **querer** o que está por vir, antes de
chegar.

**Como funciona**: A antecipação é mais poderosa que a recompensa. O
espectador que **quer** ver algo fica até ver. O espectador que **vê**
algo já recebeu a recompensa e pode sair.

**Exemplo**: "Mas espera — antes de eu te mostrar o que acontece, você
precisa entender uma coisa." (atraso deliberado da recompensa)

**No GPCG atual**: O sistema entrega a informação o mais rápido possível.
Não há antecipação. Não há "espera, deixa eu explicar uma coisa antes".

### 4.6 Reciprocity (reciprocidade)

**O que é**: O espectador sente que recebeu algo valioso e "retribui"
com atenção, like, comentário, compartilhamento.

**Como funciona**: Se o vídeo deu uma **perspectiva nova** (não só
informação), o espectador sente que ganhou algo. Reciprocidade leva
a engajamento.

**No GPCG atual**: O vídeo dá informação. Informação é commodity.
Perspectiva é valor. O sistema não tem perspectiva.

---

## 5. Como curiosidade realmente funciona

### O mal-entendido fundamental

O GPCG trata "curiosidade" como sinônimo de "fato interessante".
Isso é um erro conceitual.

**Curiosidade não é uma propriedade do fato. É uma propriedade da
apresentação.**

O mesmo fato pode ser curioso ou não, dependendo de como é apresentado:

| Apresentação | Efeito |
|---|---|
| "GTA IV usa Euphoria" | Informação. Sem curiosidade. |
| "O que acontece quando o personagem cai em GTA IV não foi animado por ninguém" | Curiosidade. Lacuna aberta. |
| "Você sabe como o personagem cai em GTA IV? Você acha que sabe. Mas não." | Curiosidade + antecipação. |

### A anatomia da curiosidade

Curiosidade tem três componentes:

1. **Lacuna**: o espectador percebe que não sabe algo que gostaria de saber.
2. **Relevância**: o espectador se importa em saber (porque é sobre algo
   que ele conhece, ou porque é estranho, ou porque quebra uma expectativa).
3. **Atingibilidade**: o espectador sente que a resposta está ao alcance
   (não é um mistério insolúvel, é algo que o vídeo vai revelar).

**Sem lacuna**: não há curiosidade, só informação.
**Sem relevância**: não há curiosidade, só indiferença.
**Sem atingibilidade**: não há curiosidade, só frustração.

### O erro do GPCG

O GPCG pula o passo de **criar a lacuna**. Vai direto para a resposta.
O fato é apresentado como informação, não como mistério.

Para criar curiosidade, o sistema precisaria:
1. **Antes** de revelar o fato, estabelecer o que o espectador **assume**
   ("você provavelmente acha que as animações foram todas programadas").
2. **Então** sugerir que essa suposição está errada ("mas não foram").
3. **Só então** revelar o fato ("o jogo decide em tempo real").

Esse padrão — **suposição → quebra → revelação** — é a estrutura básica
de curiosidade em Shorts de sucesso.

---

## 6. O que faz alguém compartilhar um vídeo

### Motivações para compartilhar

Pessoas compartilham vídeos por **razões sociais**, não informativas:

1. **Capital social**: "eu sabia disso antes de você" — compartilhar
   posiciona o remetente como insider.
2. **Identidade**: "isso é exatamente o que eu penso" — compartilhar
   expressa uma identidade.
3. **Surpresa compartilhável**: "isso é absurdo, você precisa ver" —
   a surpresa é tão grande que precisa ser validada socialmente.
4. **Utilidade percebida**: "isso vai te ajudar" — compartilhar como
   favor.
5. **Debate**: "você acredita nisso?" — compartilhar para provocar
   discussão.

### O que o GPCG gera vs. o que é compartilhável

O GPCG gera **informação correta**. Informação correta raramente é
compartilhada porque:
- Não posiciona o remetente como insider (todo mundo pode saber)
- Não expressa identidade (é neutra)
- Não surpreende (é factual, não contraintuitiva)
- Não provoca debate (não há controvérsia)

Para ser compartilhável, o vídeo precisaria ter:
- Um **twist** (algo contraintuitivo)
- Uma **perspectiva** (não só o fato, mas uma leitura dele)
- Um **momento "nossa"** (surpresa genuína)

---

## 7. O que faz alguém comentar

### Motivações para comentar

1. **Reação emocional**: surpresa, indignação, nostalgia, riso.
   Comentários emocionais são os mais comuns.
2. **Correção/adendo**: "na verdade, isso também acontece em X" — o
   espectador quer contribuir.
3. **Experiência pessoal**: "eu lembro de ter jogado isso" — o
   espectador se reconhece.
4. **Debate**: "mas isso não é bem assim" — o espectador discorda.
5. **Reconhecimento**: "eu nunca tinha pensado nisso" — o espectador
   valida o conteúdo.

### O que gera comentários vs. o que o GPCG produz

O GPCG produz conteúdo que **não provoca reação**. É correto demais,
neutro demais, completo demais. Não há espaço para o espectador
adicionar algo.

Vídeos que geram comentários **deixam espaço**:
- Uma pergunta não respondida
- Uma opinião implícita que pode ser debatida
- Um detalhe que alguns vão reconhecer e outros não
- Uma conexão que nem todo mundo faz

**Hipótese**: O GPCG deveria ocasionalmente **deixar algo no ar** —
não responder todas as perguntas, não fechar todos os loops. Dar ao
espectador algo para completar no comentário.

---

## 8. O que faz alguém assistir até o payoff

### A equação da retenção

Retenção = **investimento + expectativa + fricção de saída**

- **Investimento**: quanto tempo/atenção o espectador já gastou. Quanto
  mais gastou, mais reluta em sair (sunk cost).
- **Expectativa**: quanto o espectador acredita que a recompensa está
  próxima. Se a recompensa parece distante, sai.
- **Fricção de saída**: quanto esforço cognitivo é necessário para
  decidir sair. Se o vídeo é fluido, a fricção é baixa. Se há momentos
  de "espera, o que?", a fricção aumenta.

### Como manter os três altos

**Investimento**: comece com algo que exija atenção. Um hook que faz
o espectador pensar "espera, o quê?" o investe imediatamente.

**Expectativa**: sinalize constantemente que a recompensa está próxima.
"Mas espera, tem mais." "E aqui é onde fica estranho." "E aí você
entende o porquê." Cada sinalização diz: "vale a pena continuar".

**Fricção de saída**: crie **micro-perguntas** que o espectador quer
respondidas. Cada micro-pergunta é um ponto de fricção — sair agora
significa não saber a resposta.

### Onde o GPCG falha

- **Investimento**: o hook é informativo, não provocante. O espectador
  não se investe nos primeiros 3 segundos.
- **Expectativa**: o vídeo não sinaliza que algo melhor está por vir.
  Parece que tudo já foi dito nos primeiros 10 segundos.
- **Fricção de saída**: não há micro-perguntas. O vídeo é uma linha
  reta de informação. Sair a qualquer ponto não custa nada.

---

## 9. Pequenas recompensas ao longo do vídeo

### O conceito de "reward pacing"

Vídeos de sucesso não entregam uma grande recompensa no final.
Entregam **pequenas recompensas constantemente** e uma recompensa
maior no final.

**Tipos de micro-recompensa**:
- Uma observação inesperada ("e o detalhe é que...")
- Uma conexão com algo conhecido ("é quase como em Skyrim quando...")
- Uma virada de perspectiva ("mas pensa pelo outro lado")
- Um detalhe visual que recompensa quem presta atenção
- Uma confirmação ("então não é só você que pensava isso")
- Uma surpresa pequena ("e aqui é onde fica esquisito")

**Distribuição ideal** (para um Short de 60s):
- 0-3s: hook (recompensa de curiosidade)
- 3-15s: contexto + primeira micro-recompensa
- 15-30s: desenvolvimento + 1-2 micro-recompensas
- 30-45s: escalation + micro-recompensa maior
- 45-55s: payoff (recompensa principal)
- 55-60s: implicação (recompensa final + gancho para o próximo)

### No GPCG atual

O roteiro típico do GPCG:
- 0-10s: hook + fato principal (tudo entregue de uma vez)
- 10-40s: explicação do fato (sem micro-recompensas)
- 40-60s: conclusão genérica

O problema: **a informação é entregue muito cedo** e o resto é explicação.
Depois que o espectador sabe o fato, não há motivo para continuar.

---

## 10. Como evitar que o conteúdo pareça uma sequência de fatos

### O problema da "lista de fatos"

O GPCG atualmente produz roteiros que são, estruturalmente, listas de
fatos conectados por transições. Mesmo quando há uma "central idea",
a execução é: fato 1 → fato 2 → fato 3 → conclusão.

Isso é **jornalismo**, não **conteúdo de YouTube**.

### A alternativa: narrativa de descoberta

Em vez de listar fatos, o roteiro deveria simular uma **descoberta**:
o narrador está descobrindo algo junto com o espectador.

**Estrutura de descoberta**:
1. "Você já reparou em X?" (pergunta)
2. "Eu também não tinha reparado." (identificação)
3. "Mas quando você para pra pensar..." (transição para profundidade)
4. "Tem uma coisa que muda tudo." (promessa de revelação)
5. [revelação] (a informação, finalmente)
6. "E o mais estranho é..." (implicação)

Nessa estrutura, a informação não é uma lista — é uma **jornada**.
O espectador acompanha o narrador descobrindo algo.

### A diferença entre informar e contar

**Informar**: "O jogo usa Euphoria. Euphoria é um sistema de física
procedural. Ele simula o corpo humano em tempo real."

**Contar**: "Sabe quando o personagem cai e cada parte do corpo se
movimenta de um jeito diferente? Aquilo não foi animado. Ninguém
desenhou aquilo. O jogo está decidindo, em tempo real, como cada
músculo reage."

A segunda versão **conta a mesma informação** mas:
- Começa com uma experiência do espectador ("sabe quando?")
- Cria uma lacuna ("não foi animado")
- Revela em camadas ("ninguém desenhou" → "o jogo está decidindo")
- Termina com uma imagem mental vívida

---

## 11. Crítica ao GPCG sob a ótica editorial

### O que o sistema faz bem

- **Factual accuracy**: o sistema é bom em não inventar fatos (com o
  ScriptCritic verificando contra o source fact).
- **Anti-plágio**: três camadas protegem contra cópia.
- **Estrutura**: o EditorialPlanner já pensa em arco narrativo.
- **Consciência de padrões de IA**: os prompts já listam padrões a evitar.

### O que o sistema não faz (e deveria)

#### A. Pensar em emoções, não em fatos

O sistema seleciona fatos por qualidade e novidade. Mas **a decisão
editorial deveria ser emocional**: "este fato gera surpresa? nostalgia?
indignação? identificação?"

Um fato pode ser novo e correto e **emocionalmente morto**. O sistema
não tem como distinguir "fato que gera emoção" de "fato que é correto".

#### B. Criar perguntas antes de respondê-las

O sistema informa. Não provoca. Não cria a lacuna antes de preenchê-la.
O roteiro é uma resposta sem pergunta prévia.

**Hipótese**: antes de escrever o roteiro, o sistema deveria formular
a pergunta que o roteiro responde. Não "qual é o fato?" mas "qual é a
pergunta que este fato responde?".

#### C. Simular a reação do espectador

O sistema não simula como um espectador reagiria ao roteiro. Escreve
e revisa tecnicamente, mas não pergunta: *"se eu fosse um espectador
casual, em que momento eu sairia?"*

**Hipótese**: um estágio de "simulação de espectador" — antes do
script_review, um LLM simula ser um espectador casual e identifica
pontos de saída. "Aos 15 segundos eu fiquei entediado porque...".
Esses pontos viram feedback para revisão.

#### D. Ter perspectiva, não só informação

O sistema entrega fatos. Não tem **ponto de vista**. Mesmo quando o
tone é "sarcastic", a sarcástica é aplicada por cima da informação,
não enraizada nela.

**Hipótese**: o StoryConcept (já proposto no plano técnico) deveria
incluir uma `perspective` — não só o ângulo, mas **a leitura** do
fato. "O que isso significa? Por que isso importa? O que isso diz
sobre o jogo/sobre a indústria/sobre nós?"

#### E. Variar estrutura

Todo vídeo do GPCG tem a mesma estrutura: hook → contexto → fato →
explicação → conclusão. Canais de sucesso **variam**:
- Às vezes começam pelo final
- Às vezes começam com uma pergunta que só faz sentido no final
- Às vezes começam com uma cena específica e zoom out
- Às vezes são uma lista de coisas que parecem não conectadas até
  o final

**Hipótese**: o EditorialPlanner deveria ter um catálogo de
**estruturas narrativas** (não só os 6 beats fixos) e escolher a
mais adequada para cada história.

#### F. Deixar espaço para o espectador

O sistema é **completo demais**. Explica tudo, fecha tudo, não
deixa nada no ar. Vídeos de sucesso deixam **espaço** — para o
espectador completar, discordar, adicionar.

**Hipótese**: o roteiro deveria ocasionalmente **não responder**
uma pergunta, ou **não fechar** um loop, deliberadamente. Isso
gera comentários e engajamento.

#### G. Ter personalidade consistente

O sistema não tem uma "voz" consistente. Cada vídeo soa como um
narrador diferente (porque cada chamada LLM é independente). Canais
de sucesso têm uma **voz reconhecível** — você saberia que é aquele
canal mesmo sem ver o nome.

**Hipótese**: o sistema deveria ter uma "persona editorial"
persistente — não um caricatura, mas um conjunto de preferências,
manias, tipos de observação, formas de terminar frases. Isso seria
parte do Automation config (por usuário/canal).

---

## 12. Hipóteses para Exploração Futura

> As hipóteses abaixo **não fazem parte do plano técnico aprovado**.
> São ideias registradas para discussão e possível validação posterior.

### H1: Suposição → Quebra → Revelação

Toda curiosidade deveria seguir a estrutura: estabelecer o que o
espectador assume → sugerir que está errado → revelar a verdade.

**Validação**: comparar roteiros com e sem essa estrutura. Medir
retenção subjetiva.

### H2: Simulação de espectador

Um estágio onde um LLM simula ser um espectador casual e identifica
pontos de saída ("aqui eu sairia"). Esses pontos viram feedback
para revisão.

**Validação**: rodar simulação em roteiros existentes, ver se os
pontos identificados correspondem a momentos realmente fracos.

### H3: Catálogo de estruturas narrativas

Em vez de 6 beats fixos (hook → context → development → escalation
→ payoff → conclusion), ter um catálogo de estruturas: mistério,
lista-surpresa, contraste, flashback, pergunta-resposta-atrasada,
etc.

**Validação**: gerar roteiros com estruturas diferentes para o
mesmo fato, avaliar qual performa melhor.

### H4: Persona editorial persistente

Uma "voz" de canal que persiste entre vídeos. Não uma caricatura,
mas preferências editoriais consistentes: tipos de observação,
formas de terminar, relação com o espectador.

**Validação**: gerar 5 vídeos com persona e 5 sem, avaliar se a
versão com persona parece mais "de um canal" e menos "de uma IA".

### H5: Atraso deliberado da informação

O roteiro deveria atrasar a revelação principal o máximo possível,
criando antecipação. A informação é a recompensa, não o ponto de
partida.

**Validação**: comparar roteiros que entregam o fato nos primeiros
10s vs roteiros que atrasam até os 40s. Medir retenção.

### H6: Micro-recompensas quantificadas

Cada 10-15 segundos deveria ter uma micro-recompensa identificável.
O script_review deveria verificar: "houve uma micro-recompensa nos
últimos 15 segundos? se não, o espectador pode sair."

**Validação**: marcar micro-recompensas em roteiros existentes,
ver se há correlação com retenção percebida.

### H7: Espaço deliberado para comentários

O roteiro deveria ocasionalmente deixar uma pergunta não respondida,
ou uma opinião não fechada, para gerar comentários.

**Validação**: comparar vídeos que "fecham tudo" vs vídeos que
"deixam algo no ar". Medir número de comentários.

### H8: Perspectiva como componente do StoryConcept

O StoryConcept (já proposto no plano técnico) deveria incluir não
só o ângulo, mas a **perspectiva**: o que este fato significa, por
que importa, o que diz sobre o jogo/indústria/jogador.

**Validação**: gerar roteiros com e sem perspectiva explícita.
Avaliar se os com perspectiva geram mais "nossa" e menos "ah, ok".

### H9: Conexão com experiência do espectador

O roteiro deveria, em algum ponto, conectar o fato a uma experiência
que o espectador provavelmente teve. "Você provavelmente jogou isso
e nem pensou." Isso cria identificação.

**Validação**: comparar roteiros com e sem momentos de identificação.
Medir sensação de "isso é comigo".

### H10: Ritmo como variável editorial

O ritmo (variação de comprimento de frases, pausas, acelerações)
deveria ser uma decisão editorial explícita, não um subproduto do
LLM. Vídeos de sucesso têm ritmo **intencional**: rápido no hook,
lento na revelação, rápido na implicação.

**Validação**: analisar roteiros do GPCG quanto à variação de ritmo.
Comparar com roteiros de canais de sucesso.

---

## 13. Síntese

### O problema central

O GPCG produz **conteúdo informativamente correto e editorialmente plano**.
O sistema entende **o que** contar, mas não entende **como fazer alguém
querer ouvir**.

### A diferença entre informar e reter

Informar é entregar fatos. Reter é criar uma experiência que faz o
espectador **querer continuar**. São habilidades diferentes. O GPCG
é bom na primeira e fraco na segunda.

### Princípios que faltam

1. **Curiosidade antes de informação**: criar a lacuna antes de
   preenchê-la.
2. **Emoção antes de fato**: selecionar conteúdo pelo potencial
   emocional, não só factual.
3. **Antecipação antes de recompensa**: atrasar a revelação para
   criar desejo.
4. **Micro-recompensas constantes**: não esperar o final para
   recompensar.
5. **Perspectiva além do fato**: ter uma leitura, não só uma
   apresentação.
6. **Identificação com o espectador**: falar com quem jogou, não
   para uma audiência genérica.
7. **Ritmo intencional**: variar velocidade como ferramenta editorial.
8. **Espaço para o espectador**: deixar algo para completar,
   discordar, comentar.
9. **Personalidade consistente**: ter uma voz reconhecível entre
   vídeos.
10. **Simulação de espectador**: imaginar a reação antes de
    finalizar.

### Próximo passo

Estes princípios deveriam informar a implementação do plano técnico
aprovado (`docs/EDITORIAL_REFACTOR_PLAN.md`), especialmente:

- **Story Finder**: deveria encontrar a **pergunta** que o fato
  responde, não só o ângulo.
- **Curiosity Scorer**: deveria medir **potencial emocional**, não
  só curiosidade abstrata.
- **Humanization**: deveria injetar **ritmo e identificação**, não
  só remover AI-isms.
- **Script Critic v2**: deveria avaliar **retenção e micro-recompensas**,
  não só estrutura e naturalidade.

Mas isso é para a fase de implementação. Por ora, este documento
serve como **referência editorial** para guiar decisões futuras.

---

## Apêndice: Exemplos Comparativos

### Exemplo 1: GTA IV Euphoria

**Roteiro GPCG atual (estilo)**:
> "Você sabia que GTA IV usa o sistema Euphoria? Esse sistema simula
> o corpo humano em tempo real, criando animações realistas. Cada
> queda é única, porque o jogo calcula a física proceduralmente. Isso
> foi revolucionário em 2008 e ainda impressiona hoje."

**Roteiro com princípios editoriais**:
> "Toda vez que o personagem cai em GTA IV, algo estranho acontece.
> Cada parte do corpo se move de um jeito diferente. Você provavelmente
> jogou isso centenas de vezes e nem pensou. Mas aqui é o detalhe:
> ninguém animou aquilo. Ninguém programou cada queda. O jogo está
> decidindo, em tempo real, como cada músculo reage. Em 2008. Treze
> anos antes de a maioria dos jogos fazer algo parecido. E o mais
> estranho? A gente ainda trata isso como se fosse normal."

**Diferenças**:
- Começa com experiência do espectador, não com fato
- Cria lacuna ("ninguém animou aquilo")
- Atrasa a revelação
- Tem perspectiva ("a gente ainda trata isso como normal")
- Termina com implicação, não com conclusão
- Tem ritmo: frases curtas, pausa, revelação, frase longa, observação

### Exemplo 2: Bully — aula de química

**Roteiro GPCG atual (estilo)**:
> "Em Bully, você pode assistir a aulas de química. Durante as aulas,
> você precisa completar um minigame de criação de compostos. Isso
> dá habilidades especiais ao personagem."

**Roteiro com princípios editoriais**:
> "Tem uma aula em Bully que ninguém fala. Você senta, abre o
> caderno, e de repente tá misturando compostos químicos. Num jogo
> sobre um moleque que bate em todo mundo. A Rockstar colocou aula
> de química. E não é só enfeite — você precisa aprender. Se errar,
> perde. Se acertar, ganha habilidades. É o único jogo que eu conheço
> onde a punição por ser mau aluno é literalmente não ter tantas
> bombas pra jogar nos outros alunos."

**Diferenças**:
- Começa com lacuna ("que ninguém fala")
- Cria contraste ("num jogo sobre um moleque que bate em todo mundo")
- Tem humor seco que nasce do contexto, não de uma piada
- Tem perspectiva ("é o único jogo que eu conheço")
- Termina com observação irônica, não com conclusão

---

*Documento vivo. Atualizado conforme novas observações e hipóteses
surgirem. Para o plano técnico de implementação, ver
`docs/EDITORIAL_REFACTOR_PLAN.md`.*
