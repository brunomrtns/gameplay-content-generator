Você vai atuar como um Staff/Principal Engineer responsável por uma fase de homologação, auditoria arquitetural, estabilização e refactor sistêmico do GPCG.

Esta NÃO é uma tarefa para corrigir bugs isolados um por um.

Os problemas observados durante homologação indicam que várias peças do sistema já existem e funcionam individualmente, porém ainda não estão suficientemente amarradas como uma plataforma multiusuário confiável, previsível, configurável e escalável.

Quero que você trate os sintomas atuais como evidências de problemas de engenharia mais profundos e faça uma revisão completa da planta do projeto antes de decidir a solução.

# Objetivo principal

Reestruturar e fortalecer o fluxo de geração end-to-end para que:

* cada vídeo seja gerado estritamente com os dados, configurações, gameplays, ideias, voz, canal e preferências pertencentes ao usuário correto;
* configurações definidas pelo usuário sejam realmente respeitadas até o render final;
* o banco de ideias seja utilizado de forma efetiva;
* os roteiros parem de cair em assuntos genéricos e repetitivos;
* gameplays e trechos de gameplay parem de ser reutilizados excessivamente;
* vídeos editorialmente subdesenvolvidos (rasos, curtos demais, sem densidade, sem progressão) parem de ser produzidos;
* o pipeline editorial existente passe a produzir conteúdo coerente com a direção editorial já definida no projeto;
* publicação em conta/canal incorreto seja arquiteturalmente impedida;
* retries, workers e execuções concorrentes não permitam mistura de contexto;
* o sistema fique preparado para crescer sem depender de comportamento implícito ou estado local frágil.

Não faça uma reescrita completa sem necessidade.

A intenção é fortalecer a arquitetura existente e conectar corretamente as capacidades já implementadas.

# Contexto do GPCG

O GPCG é uma plataforma multiusuário para geração automatizada de vídeos de gameplay.

A arquitetura é dividida entre:

## Control Plane — VPS

Responsável por áreas como:

* Web UI;
* FastAPI;
* persistência;
* usuários;
* gameplays;
* jobs;
* configurações;
* fontes;
* conteúdo;
* workers;
* automação;
* integração de publicação.

## Compute Plane — worker local

Responsável pelo processamento pesado, incluindo:

* download dos materiais necessários;
* análise de gameplay;
* IA local;
* planejamento editorial;
* geração de roteiro;
* TTS;
* seleção de gameplay;
* render;
* QA;
* integração com video-generate;
* sincronização de resultados com a VPS.

Antes de alterar qualquer arquitetura, leia o código e a documentação existente para entender o fluxo real.

# Estado atual observado em homologação

Estamos gerando vídeos reais e publicando para avaliar o sistema.

Os resultados revelaram problemas importantes.

## 1. Configurações do usuário não são respeitadas

Há casos em que configurações selecionadas no formulário/interface do usuário não aparecem corretamente no vídeo.

Isso inclui, conforme observado:

* legenda;
* posição da legenda;
* fonte;
* borda/outline;
* transições;
* formato de tela;
* tema;
* canal;
* estilo;
* outras configurações associadas à geração.

Não assuma previamente onde o problema está.

Audite todo o caminho:

UI
→ API
→ persistência
→ criação do job
→ payload recebido pelo worker
→ pipeline
→ render plan
→ video-generate
→ vídeo final

Determine:

* onde cada configuração nasce;
* onde é salva;
* como é carregada;
* como é serializada;
* como chega ao worker;
* quais defaults são aplicados;
* quais etapas podem sobrescrevê-la;
* o que efetivamente chega ao video-generate.

Procure principalmente por defaults silenciosos, valores globais e reconstrução tardia de configuração.

# 2. Mistura entre usuários

Já houve problema grave de vídeo ser publicado na conta de outro usuário.

Esse tipo de erro é crítico.

Audite rigorosamente o isolamento multiusuário.

Mapeie o ownership e o fluxo de contexto envolvendo pelo menos:

* usuário;
* job;
* vídeo;
* gameplay;
* configuração;
* voz;
* canal;
* credencial de publicação;
* destino de publicação;
* automação;
* artefatos do worker.

Procure:

* queries sem escopo de usuário;
* lookups por ID sem validação de ownership;
* objetos globais compartilhados;
* estado residual no worker;
* caches;
* diretórios reutilizados;
* defaults globais;
* seleção do “primeiro canal” ou equivalente;
* objetos reconstituídos sem user context;
* dados enviados ao worker sem informação suficiente;
* associações determinadas somente no momento da publicação.

Em qualquer situação ambígua, prefira falhar o job a correr risco de cross-user publication.

Publicar um vídeo no usuário errado deve se tornar estruturalmente impossível, não apenas improvável.

# 3. Banco de ideias praticamente não está sendo usado

Hoje existem muitas ideias coletadas automaticamente, incluindo notícias e conteúdos com score relevante.

Mesmo assim, os vídeos gerados continuam voltando para ideias fracas, repetitivas e genéricas.

Exemplos reais recentes:

* Bully: skate com carros;
* guerra de comida;
* “5 táticas secretas”;
* variações dos mesmos assuntos.

Enquanto isso, o banco contém dezenas ou centenas de ideias diferentes.

Investigue o fluxo REAL que alimenta `content_planning`.

Descubra:

* de onde os fatos/ideias estão vindo durante geração;
* como os candidatos são buscados;
* como são filtrados;
* quais entidades realmente participam da geração;
* se o banco mostrado na UI é o mesmo pool consumido pelo pipeline;
* se existe alguma separação entre Content Ideas, Facts, Sources ou outras estruturas;
* se informações coletadas automaticamente estão sendo convertidas corretamente para o formato editorial utilizado pelo pipeline;
* por que itens disponíveis e com score alto não estão entrando nos vídeos.

Não crie uma segunda fonte de conteúdo.

Conecte corretamente o que já existe.

# 4. Conteúdo e gameplay estão conceitualmente invertidos

A gameplay não deve necessariamente determinar o assunto da narração.

Para boa parte dos vídeos, principalmente conteúdos editoriais, notícias, curiosidades e assuntos gerais relacionados ao universo de games:

A NARRAÇÃO É O PRODUTO PRINCIPAL.

A gameplay é principalmente o background visual.

Existe uma diferença entre:

* o que vale a pena contar;
* o que vale a pena mostrar.

Essas duas decisões precisam se relacionar, mas não precisam ser idênticas.

Uma boa ideia pode ser compatível com a biblioteca do usuário por relações como:

* mesmo jogo;
* franquia;
* desenvolvedora;
* publisher;
* plataforma;
* gênero;
* universo;
* contexto relacionado;
* outras relações existentes no catálogo.

Não invente uma taxonomia nova antes de investigar os modelos atuais.

Primeiro descubra quais dessas relações já existem ou podem ser obtidas da arquitetura atual.

O sistema não deveria descartar automaticamente uma boa história simplesmente porque não existe uma cena que representa literalmente o que está sendo narrado.

# 5. Repetição excessiva de gameplay (controle por intervalo temporal)

Os mesmos trechos estão aparecendo repetidamente.

Isso ocorre mesmo depois da implementação de mapeamento e análise semântica de gameplays.

O projeto já possui análise de gameplay e indexação semântica.

Não implemente outra análise paralela.

O problema atual NÃO é falta de análise.

É ausência de um controle de utilização em nível de **intervalo temporal** dentro de cada arquivo de gameplay, **escopado por usuário consumidor**.

## Conceito fundamental: owner, visibilidade e histórico de consumo

NÃO trate "gameplay utilizada" como um estado global.

Existem três conceitos separados que NÃO devem ser confundidos:

1. **Owner da gameplay** — o usuário que fez o upload; ownership nunca é transferido.
2. **Visibilidade da gameplay** — privada (somente o owner) ou pública (disponível no pool de outros usuários, conforme configuração do consumidor).
3. **Histórico de consumo por usuário** — quais intervalos cada usuário consumidor já utilizou, escopado por consumidor, não por arquivo.

Exemplo que define o comportamento esperado:

```text
Gameplay X
Owner: User A
Visibility: public

Histórico User A:
  13:22 → 13:40 usado

Histórico User B:
  nenhum trecho usado

Resultado:
  User A deve evitar 13:22 → 13:40
  User B ainda pode usar 13:22 → 13:40
```

Portanto, o histórico de utilização é escopado pelo **usuário consumidor**, não globalmente pelo arquivo.

Não imponha previamente uma tabela ou modelo específico.

Primeiro analise os modelos atuais e escolha a representação arquitetural adequada.

Mas o comportamento funcional deve equivaler conceitualmente a:

```text
consumer_user
  + gameplay_source
  + intervalo temporal
  + estado de utilização
```

A regra arquitetural que esta seção transmite sem ambiguidade é:

**Gameplay tem owner.
Gameplay tem visibility.
Histórico de trechos pertence ao usuário consumidor.
Material próprio tem prioridade.
Gameplay pública é fallback configurável.**

## Conceito central: trecho ≠ arquivo

Utilizar um trecho de uma gameplay NÃO significa tornar o arquivo inteiro indisponível.

Uma gameplay de 30, 60 ou 120 minutos deve continuar podendo fornecer muitos trechos diferentes.

O recurso que está sendo consumido é principalmente a **região temporal selecionada**, não automaticamente o arquivo inteiro.

Não quero apenas um histórico abstrato de "gameplay utilizada".

Quero saber, para cada `GameplaySource`, quais **regiões temporais** já foram efetivamente consumidas em vídeos, e usar isso durante a seleção para reduzir drasticamente repetição visual entre vídeos.

Exemplo conceitual do estado esperado:

```
Gameplay A — 30:00

Trechos utilizados:
  02:10 → 02:27
  08:42 → 09:01
  13:22 → 13:40
  21:05 → 21:19

Trechos ainda disponíveis:
  todas as demais regiões elegíveis,
  respeitando também eventos, qualidade, score,
  regras de seleção e overlaps.
```

## Análise prévia obrigatória

ANTES de decidir a implementação, analise os modelos e o fluxo existentes e decida a forma arquitetural mais adequada de persistir essa utilização.

No mínimo, examine:

* `GameplaySource`, `GameplayAsset`, `GameplayEvent` (`src/gpcg/domain/models.py`);
* `GameplayRetriever` (`src/gpcg/application/gameplay_retriever.py`);
* `GameplaySelector` / `SelectedClip` (`src/gpcg/application/gameplay_selector.py`);
* `GameplayIndexService`;
* `Job`, `Job.artifacts`, `Video`, `ContentPlan`, `Script`;
* o fluxo `gameplay_selection` → `SelectedClip` → render → `Video`;
* onde os intervalos efetivamente utilizados já são (ou não são) persistidos hoje.

Não imponha novos modelos se isso puder ser representado corretamente utilizando estruturas já existentes (ex.: `GameplayAsset.used_count`, `Job.artifacts`, `Video`, `metadata_json`).

Se uma nova estrutura for realmente necessária, justifique com base na arquitetura atual e mantenha migrations/backward compatibility.

O comportamento final, porém, precisa ser explícito e testável, independentemente da escolha de persistência.

## Comportamento exigido

### Tracking por intervalo temporal

O sistema deve ser capaz de rastrear, por `GameplaySource` e por usuário, quais intervalos `[start_sec, end_sec]` já foram efetivamente consumidos em vídeos gerados.

A precisão temporal deve seguir os modelos existentes (segundos float, alinhado aos `GameplayEvent.start_time`/`end_time` e `SelectedClip.start_sec`/`end_sec`).

### Associação vídeo ↔ gameplay ↔ intervalo

Deve ser possível rastrear quais intervalos de quais gameplays foram efetivamente usados em cada vídeo.

Exemplo conceitual do formato esperado:

```
Video X
  → GameplaySource 17
    → 13:22.100 – 13:40.450
  → GameplaySource 17
    → 21:05.000 – 21:16.800
  → GameplaySource 42
    → 04:31.500 – 04:47.200
```

O formato real deve seguir os modelos existentes.

Isso é necessário tanto para auditabilidade (seção "Auditabilidade") quanto para a liberação opcional de trechos ao excluir vídeos (seção 5b).

### Escopo por usuário (histórico por consumidor)

O histórico de utilização deve respeitar o isolamento multiusuário.

O conceito de "trecho já utilizado" deve ser considerado dentro da biblioteca/contexto do **usuário consumidor** correspondente — não globalmente por arquivo.

Trechos consumidos pelo usuário A não podem influenciar a seleção do usuário B, e nenhum controle de utilização pode criar mistura entre usuários.

Mesmo para uma mesma gameplay pública, cada usuário consumidor mantém histórico independente:

```text
Gameplay pública X

User A já usou:
  02:10 → 02:30
  13:22 → 13:40

User B já usou:
  05:00 → 05:20

User C nunca usou.

Disponibilidade:
  A: evitar seus próprios intervalos usados.
  B: evitar apenas 05:00 → 05:20.
  C: todo o material elegível permanece inicialmente disponível.
```

Uso por um usuário NÃO reduz a disponibilidade para outro usuário.

### Gameplay privada

O usuário que fizer upload de uma gameplay pode mantê-la privada.

Gameplay privada:

* pertence ao usuário que fez upload;
* somente esse usuário pode utilizá-la;
* nunca deve aparecer no pool de candidatos de outros usuários;
* deve respeitar normalmente o histórico de trechos daquele usuário.

Valide ownership em todos os pontos relevantes (seleção, download, render, publicação).

### Gameplay pública

O proprietário também pode optar por tornar uma gameplay pública.

Gameplay pública significa que ela pode entrar no pool de gameplays disponíveis para outros usuários da plataforma, conforme as configurações de fallback de cada usuário consumidor.

Mesmo pública:

* continua possuindo owner;
* não perde ownership;
* não possui histórico global de consumo;
* cada usuário consumidor mantém histórico independente;
* uso por um usuário não reduz a disponibilidade para outro usuário.

Nunca acesse gameplay privada de terceiros.

### Prioridade obrigatória da biblioteca própria

O sistema deve SEMPRE priorizar gameplays pertencentes ao usuário que está gerando o vídeo.

Não escolha gameplay pública de terceiros apenas porque possui score maior.

A lógica deve funcionar conceitualmente assim:

```text
1. Buscar gameplays próprias elegíveis.
2. Aplicar histórico daquele usuário.
3. Aplicar overlap/diversidade/qualidade.
4. Selecionar material próprio enquanto existir material adequado.
5. Somente quando a biblioteca própria estiver insuficiente/esgotada:
   consultar a política de fallback configurada pelo usuário.
```

Gameplays públicas de terceiros funcionam como fallback/extensão da biblioteca, e não como substituição automática.

### Esgotamento é por usuário

Uma gameplay pública não fica "esgotada para o sistema".

Ela pode estar esgotada para um usuário e completamente nova para outro.

```text
Gameplay pública com 30 minutos.

User A: já consumiu praticamente todas as regiões elegíveis.
User B: usou apenas dois intervalos.

Resultado:
  Para A: gameplay pode estar esgotada.
  Para B: ainda existe muito material disponível.
```

Todo cálculo de disponibilidade deve considerar o usuário consumidor.

### Reserva versus uso efetivo (estados do trecho)

O sistema deve distinguir corretamente os estados de um trecho:

* **candidato** — trecho em consideração pela seleção;
* **selecionado/reservado** para um job — ainda não utilizado, mas temporariamente indisponível para outros jobs concorrentes;
* **efetivamente utilizado** em um vídeo gerado com sucesso;
* **associado a um vídeo publicado**;
* **liberado novamente** — volta a ser elegível (ex.: vídeo excluído com liberação explícita);
* **descartado permanentemente** — quando aplicável (ex.: usuário opta por manter como utilizado ao excluir o vídeo).

Regra crítica: **não marque um trecho como efetivamente utilizado cedo demais.**

A transição para "efetivamente utilizado" deve ocorrer somente quando o vídeo for de fato gerado com sucesso (render válido persistido), não no momento da seleção.

### Comportamento em falhas, retries e cancelamentos

O controle de utilização deve interagir corretamente com o ciclo de vida do job:

* **render falho antes de produzir vídeo** — o trecho reservado deve voltar a ser elegível. O sistema NÃO deve perder permanentemente aquele intervalo por causa de um job fracassado;
* **retry** — um retry não deve duplicar o registro de utilização nem consumir dois trechos distintos sem necessidade; o trecho reservado original deve ser reutilizado ou liberado de forma determinística;
* **job cancelado** — trechos reservados devem ser liberados;
* **vídeo gerado mas não publicado** — o trecho deve permanecer como "efetivamente utilizado" (o vídeo existe e pode ser publicado depois), a menos que o vídeo seja excluído;
* **publicação concluída** — atualiza o estado para "associado a vídeo publicado", sem alterar a elegibilidade (continua utilizado);
* **exclusão posterior do vídeo** — segue a seção 5b.

Defina explicitamente em qual ponto do pipeline cada transição de estado acontece.

### Concorrência entre jobs (escopada por consumidor)

Qualquer mecanismo de reserva deve respeitar o **usuário consumidor**.

NÃO implemente lock global da gameplay pública inteira.

Se houver reserva atômica, lease, lock ou mecanismo equivalente, o escopo precisa permitir que:

* **usuários diferentes** utilizem a mesma região de uma gameplay pública (históricos independentes);
* **jobs do mesmo usuário** não colidam entre si no mesmo intervalo.

Exemplo permitido (históricos independentes):

```text
User A / Job 1: Gameplay pública X, 13:22 → 13:40
User B / Job 1: Gameplay pública X, 13:22 → 13:40
```

Isso é permitido porque são históricos independentes.

Exemplo que deve ser impedido (mesmo consumidor):

```text
User A / Job 1: Gameplay pública X, 13:22 → 13:40
User A / Job 2: Gameplay pública X, 13:22 → 13:40
```

Dois jobs concorrentes do MESMO usuário não devem reservar o mesmo intervalo.

A estratégia exata (reserva atômica no banco, lock por `(consumer_user, gameplay_source)`, particionamento do espaço de busca, ou outra) deve ser decidida com base na arquitetura atual de claim de jobs (`/api/jobs/claim` usa UPDATE condicional atômico — siga o mesmo padrão quando aplicável).

Comportamentos verificáveis:

* dois jobs paralelos do mesmo usuário que encontram o mesmo melhor segmento não produzem dupla utilização indevida do mesmo intervalo;
* dois jobs paralelos de usuários diferentes podem utilizar o mesmo intervalo de uma gameplay pública.

Preserve mecanismo de recuperação de reserva em caso de:

* worker crash;
* timeout;
* job cancelado;
* desconexão;
* retry.

Nenhum trecho deve ficar reservado para sempre por causa de execução abandonada.

## Overlap

Não considere apenas intervalos idênticos.

Exemplo:

```
Vídeo anterior:   13:22 → 13:40
Novo candidato:   13:27 → 13:45
```

Isso possui overlap significativo e deve ser tratado como reutilização parcial, não como trecho novo.

Defina uma política adequada de overlap/cooldown baseada na arquitetura atual.

A regra NÃO precisa ser "qualquer segundo compartilhado bloqueia o trecho inteiro".

Uma política razoável deve:

* bloquear ou penalizar fortemente candidatos cujo overlap com trechos utilizados ultrapasse um limiar configurável;
* permitir reaproveitamento eficiente de gameplays longas sem que pequenas regiões visualmente iguais apareçam repetidamente;
* ser determinística e testável.

Se julgar adequado, trate o cooldown como uma janela de penalização ao redor de trechos utilizados (ex.: N segundos antes/depois), em vez de um bloqueio duro por segundo.

Documente a política escolhida e o limiar default.

## Integração com a seleção existente

A seleção futura deve combinar:

* relevância semântica;
* `interesting_score`;
* qualidade visual (`visual_confidence`);
* estratégia editorial (`VideoCreativePlan.gameplay_strategy`);
* compatibilidade com o vídeo (`compatibility` flags);
* **disponibilidade temporal** (trechos não utilizados);
* **histórico de utilização**;
* **overlap com trechos utilizados**;
* diversidade entre cenas.

Não quero que "nunca repetir" destrua a qualidade da seleção.

A prioridade é:

> selecionar bons trechos que ainda não tenham sido explorados antes de reutilizar material recente.

### Comportamento de fallback (configurável por usuário)

Se, excepcionalmente, não existir material próprio suficiente (todos os trechos próprios elegíveis já foram utilizados, ou a biblioteca do usuário é pequena), o sistema deve possuir um comportamento **explícito e previsível** em vez de silenciosamente voltar sempre aos mesmos clips ou usar gameplays públicas sem autorização.

O usuário deve poder escolher o comportamento de fallback quando não existirem mais trechos próprios elegíveis suficientes.

Inclua no `REFACTORY_V2` explicitamente pelo menos duas opções:

#### Opção A — Parar geração

Se todos os trechos próprios elegíveis forem consumidos:

* não reutilizar silenciosamente material já gasto;
* não usar gameplay pública;
* não continuar a automação como se nada tivesse acontecido;
* interromper/pausar o fluxo da forma mais coerente com a arquitetura atual;
* registrar claramente que a biblioteca de gameplay própria foi esgotada.

#### Opção B — Permitir gameplays públicas

Se essa opção estiver habilitada:

* primeiro tentar a biblioteca própria;
* somente quando não houver material próprio adequado, expandir o pool;
* considerar somente gameplays marcadas como públicas;
* aplicar o histórico de utilização do **usuário consumidor** sobre essas gameplays;
* continuar respeitando relevância, qualidade, `interesting_score`, overlap, diversidade e estratégia editorial.

Nunca acessar gameplay privada de terceiros.

#### Fallback dentro do pool (reutilização)

Independentemente da opção acima, se mesmo dentro do pool elegível (próprio ou público) não existir material totalmente novo, o sistema pode — de forma explícita, configurável e testável — adotar uma política de reutilização:

* permitir reutilização com penalização progressiva (trechos menos utilizados primeiro);
* reiniciar o histórico de utilização daquela gameplay **para aquele usuário consumidor** de forma controlada e registrada (não globalmente por arquivo);
* falhar o job com mensagem clara de "material insuficiente" para o usuário.

Decida, com base na arquitetura atual, qual política interna de reutilização adotar, e documente.

O fallback escolhido deve ser documentado, configurável quando relevante, e testável.

## Seleção consolidada

A seleção deve funcionar conceitualmente assim:

```text
1.  Identificar o usuário do job.
2.  Buscar gameplays pertencentes ao usuário.
3.  Aplicar o histórico temporal desse usuário.
4.  Excluir/penalizar overlaps já consumidos.
5.  Rankear por:
      - compatibilidade;
      - estratégia editorial;
      - semantic relevance;
      - interesting_score;
      - visual quality;
      - diversidade;
      - disponibilidade.
6.  Se existir material próprio adequado:
      usar material próprio.
7.  Se não existir:
      consultar a configuração de fallback.
8.  Se fallback = stop:
      interromper explicitamente.
9.  Se fallback = allow_public:
      expandir o pool para gameplays públicas.
10. Aplicar novamente o histórico do mesmo usuário consumidor
    sobre o pool público.
11. Rankear e selecionar.
12. Reservar o intervalo de forma segura (escopada por consumidor).
13. Após render válido:
    registrar uso efetivo.
```

## Objetivo verificável

Quando existem múltiplos segmentos bons disponíveis, o sistema não deve ficar escolhendo repetidamente o mesmo pequeno subconjunto, nem intervalos com overlap significativo com trechos recentemente utilizados.

Uma gameplay longa deve poder alimentar muitas gerações diferentes antes de qualquer reutilização se tornar necessária.

# 5b. Exclusão de vídeo e liberação de trechos de gameplay

A regra de utilização de trechos deve ser integrada ao fluxo de exclusão de vídeos na interface.

Hoje não existe endpoint/UI de exclusão de vídeo. Implemente-o como parte desta fase, seguindo o padrão visual e textual existente da aplicação (dark theme, teal accent, glass effects, toasts).

## Fluxo de UX

Quando o usuário clicar em excluir um vídeo, deve existir o popup normal de confirmação de exclusão.

Nesse fluxo, ofereça também uma decisão relacionada aos trechos de gameplay utilizados naquele vídeo.

A interface deve perguntar, em linguagem clara para um usuário comum, se os trechos de gameplay daquele vídeo podem voltar a ficar disponíveis para futuras gerações.

NÃO use texto técnico como "Deseja liberar os segmentos temporais?".

A UX deve comunicar algo conceitualmente semelhante a:

> "Quer permitir que os trechos de gameplay usados neste vídeo sejam utilizados novamente em futuros vídeos?"

E oferecer duas decisões semanticamente claras:

* **liberar os trechos novamente**;
* **manter esses trechos como já utilizados**.

Escolha a melhor redação seguindo o padrão visual e textual existente da aplicação.

## Semântica das duas decisões

Excluir o vídeo e liberar a gameplay são **decisões diferentes**.

Se o usuário excluir o vídeo e optar por **liberar os trechos**:

* o vídeo é removido conforme a política atual de exclusão (arquivo, thumbnail, registro, referências em `Job`/`ContentPlan` quando aplicável);
* os intervalos associados a ele voltam a ser elegíveis para futuras gerações **daquele usuário consumidor**.

Se o usuário excluir o vídeo e optar por **NÃO liberar**:

* o vídeo é removido;
* aqueles intervalos continuam registrados como utilizados;
* o sistema continua evitando reutilizá-los no futuro.

## Escopo da liberação (por consumidor)

A liberação afeta **somente o histórico do usuário que gerou aquele vídeo**.

Exemplo:

* User A é dono da gameplay pública.
* User B gera um vídeo usando essa gameplay.
* B exclui o vídeo e escolhe liberar os trechos.

Resultado:

* somente o histórico de B é liberado;
* nada muda no histórico de A;
* nada muda para User C;
* a gameplay continua pública;
* ownership continua sendo de A.

## Requisitos de implementação

* O endpoint de exclusão deve receber explicitamente a decisão do usuário sobre os trechos (ex.: `release_segments: bool`), não inferir automaticamente;
* A associação vídeo ↔ intervalos (seção 5) é pré-requisito para esta funcionalidade — sem ela não é possível saber quais trechos liberar;
* A exclusão deve respeitar o isolamento multiusuário (somente o dono do vídeo pode excluí-lo);
* A liberação deve ser idempotente: excluir novamente (ou chamar o endpoint duas vezes) não pode causar efeito colateral;
* Se o vídeo já tiver sido publicado no YouTube, a exclusão local NÃO deve remover o vídeo do YouTube automaticamente (isso é uma ação separada) — deixe explícito na UI quando aplicável.

# 5c. Visibilidade de gameplay: pública/privada e aceite explícito

Esta seção define o fluxo de tornar uma gameplay pública/privada e o aceite explícito do usuário.

## Publicar gameplay — aceite explícito

Ao tornar uma gameplay pública, o usuário deve passar por um aceite explícito.

NÃO publique gameplays automaticamente.

A UI deve mostrar um modal/termo antes da confirmação.

Evite linguagem juridicamente incorreta como "os direitos da gameplay se tornam públicos".

A intenção correta é conceder/autorizar o uso daquela gameplay dentro dos usos previstos pela plataforma.

A UX deve deixar claro, em linguagem simples, que:

* aquela gameplay poderá ser utilizada pelo sistema na geração de vídeos para outros usuários;
* o usuário declara possuir os direitos/permissões necessários para disponibilizá-la;
* tornar a gameplay pública permite uso futuro por outros usuários;
* mudar a gameplay para privada depois NÃO desfaz usos já ocorridos;
* o usuário precisa aceitar explicitamente antes da alteração.

A redação jurídica definitiva deve seguir os termos reais da plataforma.

NÃO invente cláusulas legais.

## Persistência do aceite

O aceite para tornar pública deve ser auditável.

Avalie a forma adequada de persistir informações como:

* usuário;
* gameplay;
* momento do aceite;
* versão do termo/política;
* mudança de visibilidade realizada.

Não imponha modelo novo se a arquitetura atual já permitir representar isso de forma limpa (ex.: `metadata_json`, tabela de auditoria existente, etc.).

## Retornar gameplay pública para privada

Ao tornar uma gameplay privada novamente:

* novos jobs de terceiros deixam de poder selecioná-la;
* ela deve sair imediatamente dos novos pools públicos;
* vídeos já gerados/publicados com ela permanecem válidos;
* o histórico de uso dos consumidores anteriores continua consistente;
* jobs já em execução/reservas existentes precisam ter comportamento explicitamente definido.

Analise o código e escolha a política mais segura para jobs já em andamento (ex.: permitir concluir a execução atual, ou cancelar e requeue, ou falhar explicitamente).

Documente a política escolhida.

# 6. Repetição editorial

Além da gameplay, os próprios vídeos estão repetitivos.

Aparecem repetidamente formatos como:

* “5 coisas que você não sabia”;
* “segredo de X”;
* “tática secreta”;
* perguntas/hook muito parecidos;
* assuntos semanticamente equivalentes.

Isso precisa ser analisado em conjunto com a arquitetura editorial existente.

# 7. Qualidade de conteúdo e densidade editorial

Esta seção NÃO substitui a arquitetura editorial existente.

O projeto já passou por uma fase extensa de pesquisa e engenharia editorial baseada em livros, artigos, princípios de retenção, psicologia, narrativa, curiosidade, humanização e revisão crítica.

Já existem documentos, decisões e implementações relacionadas a:

* Editorial Planner;
* Creative Engine;
* Script Critic;
* Story Finder;
* Humanization;
* Curiosity Scorer;
* curiosity gap;
* familiarity;
* payoff;
* narrative beats;
* retenção;
* anti-plágio;
* factualidade;
* padrões de escrita artificial;
* metodologia de avaliação editorial.

Esta seção é **auditar o que já existe, preservar o que está correto e acrescentar mecanismos para evitar vídeos fracos, rasos, curtos demais ou sem densidade editorial.**

A regra é: **preservar → auditar → identificar gaps → acrescentar.**

NÃO crie um segundo Story Finder, outro Script Critic ou outro Creative Engine se os existentes puderem ser fortalecidos.

## Problema observado em homologação

Mesmo com toda a estrutura editorial, alguns vídeos ainda estão saindo:

* curtos demais (20–30 segundos quando o conteúdo poderia sustentar muito mais);
* com pouca substância;
* com assunto fraco;
* parecendo apenas uma observação rápida;
* sem desenvolvimento;
* sem aprofundamento;
* sem descoberta real;
* sem payoff forte.

Quero que exista diferença entre:

**um vídeo tecnicamente gerável**

e

**um vídeo que realmente vale a pena publicar.**

## Estado real do pipeline editorial (auditar antes de modificar)

ANTES de propor mudanças, audite o estado real do código.

No mínimo, examine:

* `EditorialPlanner` (`src/gpcg/application/editorial_planner.py`);
* `ScriptCritic` (`src/gpcg/application/script_critic.py`);
* `StoryFinder` (`src/gpcg/application/story_finder.py`);
* `Humanization` (`src/gpcg/application/humanization.py`);
* `CuriosityScorer` (`src/gpcg/application/curiosity_scorer.py`);
* `CreativeEngine` (`src/gpcg/application/creative_engine.py`);
* `ScriptService` (`src/gpcg/application/script_service.py`);
* `ContentPlanningService` (`src/gpcg/application/content_planning_service.py`);
* `GenerationService` (`src/gpcg/application/generation_service.py`);
* `QAService` (`src/gpcg/application/qa_service.py`);
* as feature flags em `config.py` que gateiam cada componente.

Para cada componente, determine:

1. o que já foi implementado;
2. se está ativo ou inativo por feature flag;
3. se já resolve os problemas de qualidade;
4. o que está falhando na prática;
5. quais gaps permanecem.

Muitos componentes editoriais já implementados podem estar **inativos por feature flag**.

Antes de criar qualquer mecanismo novo, avalie se o problema é ausência de implementação ou simplesmente ausência de ativação.

## Não otimizar para duração mínima

NÃO crie artificialmente uma regra do tipo "todo vídeo precisa ter 60 segundos".

Isso seria errado.

Existem histórias excelentes que funcionam em 35 ou 40 segundos.

O problema é diferente:

**o sistema não deve produzir vídeos de 20–25 segundos simplesmente porque encontrou um fato pequeno e conseguiu transformá-lo em algumas frases.**

A duração deve ser consequência da quantidade de substância editorial disponível.

Se a história não possui material suficiente para sustentar um vídeo bom, o sistema deve considerar:

* aprofundar a pesquisa;
* buscar contexto adicional nas fontes;
* buscar fatos relacionados;
* enriquecer a narrativa;
* encontrar outro ângulo;
* ou rejeitar aquela ideia.

NÃO preencher tempo com enrolação.

## Densidade editorial

Avalie se uma ideia possui DENSIDADE suficiente para virar vídeo.

NÃO necessariamente como um novo campo/modelo — primeiro analise o que já existe.

A decisão deveria considerar conceitos como:

* existe uma descoberta clara?
* existe contexto suficiente?
* existe desenvolvimento?
* existem consequências ou implicações?
* existe algo novo sendo aprendido ao longo do vídeo?
* existe progressão?
* existe payoff?
* existe material factual suficiente para sustentar a narrativa?
* existe profundidade suficiente para justificar o vídeo?

Um conteúdo que só sustenta:

```
hook → fato → fim
```

provavelmente não deveria virar vídeo.

O `CuriosityScorer` já possui um sub-score `retention_potential` que avalia se o fato sustenta ~60s.

O `StoryFinder` já possui `is_story` e `confidence` que gateiam se um fato tem potencial narrativo.

Reaproveite esses mecanismos antes de criar algo novo.

Se eles estiverem inativos por feature flag, avalie se a ativação resolve parte do problema.

## Expansão inteligente antes da rejeição

Se uma ideia é boa, mas possui pouco material isoladamente, o sistema deve tentar enriquecê-la antes de desistir.

Hoje o `StoryFinder` rejeita fatos sem história (`is_story=false`) e tenta outro fato.

NÃO existe mecanismo de "enriquecer este fato antes de rejeitá-lo".

Dependendo da arquitetura existente, isso pode envolver:

* recuperar mais informações da fonte original;
* utilizar informações relacionadas já persistidas;
* buscar fatos complementares;
* conectar o evento ao contexto histórico;
* explicar por que aquilo importa;
* mostrar consequência;
* mostrar contraste;
* mostrar origem;
* mostrar impacto;
* conectar com algo familiar ao público.

Mas isso deve continuar respeitando:

* factualidade;
* anti-plágio;
* qualidade das fontes;
* direção editorial existente.

NÃO invente informação para preencher duração.

## Progressão editorial

Um vídeo forte não deveria ser:

```
hook → resposta → CTA
```

Deve existir progressão editorial real.

O `VideoCreativePlan` já define `narrative_beats` (hook → context → development → escalation → payoff → conclusion).

NÃO imponha uma fórmula rígida, mas avalie se o roteiro realmente executa os beats planejados.

O espectador deve sentir que aprendeu/descobriu mais ao longo do vídeo.

Se o `EditorialPlanner` planejou 6 beats mas o `ScriptService` gerou um roteiro que só executa hook + fato + fim, existe um gap entre planejamento e execução que precisa ser corrigido.

## Gate editorial antes de TTS/render

Inclua um gate editorial antes de TTS/render.

O objetivo é impedir que um roteiro seja renderizado apenas porque:

* passou anti-plágio;
* tem português correto;
* tem começo/meio/fim;
* cabe na duração;
* não contém erros factuais óbvios.

O gate precisa considerar se o roteiro realmente possui valor editorial.

Reaproveite o `ScriptCritic`, `StoryFinder`, `Humanization`, `CuriosityScorer` e outras estruturas existentes antes de criar algo novo.

Hoje o `ScriptCritic` avalia 6 dimensões (structure, naturalness, humor, coherence, gameplay, factual_accuracy) e produz veredito PASS/REVISE.

MAS após 3 revisões sem sucesso, o pipeline prossegue com o roteiro mesmo assim.

Esse comportamento permissivo precisa ser revisado: um roteiro que falha repetidamente no critic não deveria chegar ao render como se nada tivesse acontecido.

Se os componentes atuais já possuem campos que permitem avaliar densidade/progressão/payoff, fortaleça seu uso em vez de criar um novo gate paralelo.

## Duração como sintoma

Use duração como um sinal, não como única regra.

Um vídeo inesperadamente curto pode indicar:

* fato raso;
* Story Concept pobre (ou `StoryFinder` inativo);
* pouca pesquisa;
* narrativa incompleta;
* `ScriptService` resumindo demais;
* configuração incorreta de `target_duration`;
* TTS encurtando;
* conteúdo sendo truncado;
* outro bug de pipeline.

Investigue qual dessas causas está acontecendo atualmente.

NÃO assuma que o problema é simplesmente "LLM escreve pouco".

## target_duration precisa ser respeitado

Audite o caminho real de `target_duration`:

```
UI/configuração
→ job
→ content planning
→ editorial planning
→ script
→ TTS
→ duração real
```

Hoje `target_duration` é apenas uma referência no prompt do LLM.

NÃO existe validação que impeça um script curto de prosseguir.

Se o usuário configurou um target de ~60 segundos, um vídeo de 22 segundos NÃO deveria ser considerado normal sem uma justificativa editorial clara.

Isso NÃO significa preencher 38 segundos com redundância.

Significa que:

* ou a história precisa ser enriquecida;
* ou outra história precisa ser escolhida;
* ou o sistema deve reconhecer que não possui material suficiente.

O `ScriptService` já possui bounds de caracteres (`gpcg_narration_min_chars`, `gpcg_narration_max_chars`) e instrui o LLM a expandir se curto.

MAS se o LLM ignorar e retornar um script curto, não há gate que impeça o prosseguimento.

Esse gap precisa ser corrigido: o bound de caracteres deve ser uma constraint, não apenas uma sugestão.

A validação de duração no `QAService` ocorre APÓS o render e é permissiva (um vídeo de 22s com target 60s passa com score 85).

Isso é tarde demais e permissivo demais.

## Pesquisa como matéria-prima

O projeto já possui coleta de fontes, notícias, artigos e banco de ideias.

Use isso para criar conteúdo rico.

Uma notícia não deve ser resumida em três frases.

Uma fonte pode conter:

* contexto;
* causa;
* consequência;
* comparação;
* histórico;
* personagens;
* números;
* mudança de cenário;
* implicações.

O pipeline editorial deve conseguir encontrar a história dentro desse material.

NÃO necessariamente usar tudo.

Usar o que fortalece a descoberta.

## Não voltar para conteúdo genérico

Fortalecer profundidade NÃO significa voltar para formatos como:

* "5 curiosidades";
* "3 coisas que você não sabia";
* "segredos de X";
* "você sabia?";
* listas genéricas.

A arquitetura editorial existente já possui princípios melhores do que isso.

Preserve-os.

## Critério de publicação

Um vídeo só deve avançar para produção quando houver confiança suficiente de que:

* existe uma história;
* existe uma descoberta;
* existe substância;
* existe progressão;
* existe payoff;
* existe material factual;
* existe duração coerente com a história;
* o roteiro não parece apenas um fato expandido artificialmente.

Se não houver:

**melhor escolher outra ideia do que publicar conteúdo fraco.**

Isso é coerente com o manifesto editorial existente: "melhor não fazer um vídeo do que fazer um vídeo que não provoca nada".

# Direção editorial existente

NÃO invente uma nova filosofia editorial.

O projeto já possui uma direção explícita.

O manifesto define que o GPCG existe para criar DESCOBERTAS.

Não simples fatos.

Não enciclopédia.

Não informação pela informação.

O conteúdo deve provocar uma mudança de percepção no espectador.

A direção editorial enfatiza:

* curiosity gap;
* familiaridade;
* surpresa útil;
* clareza;
* emoção;
* transformação;
* payoff;
* conteúdo que parece feito por uma pessoa.

Considere esses documentos como referência editorial do produto.

# Arquitetura editorial já definida

O projeto já possui:

* Content Planning;
* Editorial Planner;
* Creative Engine;
* Script Service;
* Script Critic.

Também existe um Plano de Refatoração Editorial V2 aprovado.

Esse plano propõe evoluir o fluxo para:

content_planning
→ story_finding
→ editorial_planning
→ creative_engine
→ script
→ humanization
→ script_review
→ tts
→ ...

Não ignore isso.

Não desenvolva uma segunda arquitetura editorial paralela.

Durante esta fase, examine o estado real do código e determine:

* o que desse plano já foi implementado;
* o que ainda está apenas documentado;
* se parte dos problemas de homologação já é consequência das limitações identificadas no plano;
* quais partes podem ou devem ser implementadas junto desta reestruturação;
* quais mudanças são necessárias especificamente para resolver os problemas observados.

O plano V2 aprovado deve ser tratado como direção técnica existente, não como uma sugestão descartável.

# Importante sobre os documentos editoriais

Diferencie claramente:

DOCUMENTOS NORMATIVOS / APROVADOS

de

DOCUMENTOS DE PESQUISA / HIPÓTESES.

Não transforme automaticamente cada ideia do diário de pesquisa ou dos princípios editoriais em feature ou arquitetura.

Use os planos aprovados como especificação.

Use manifesto, princípios, pesquisa e consolidação para compreender a intenção editorial.

# Notícias, artigos e fontes

Conteúdo coletado automaticamente não deve virar simples tradução ou cópia.

Quando uma boa fonte é selecionada, o pipeline precisa usá-la como matéria-prima.

A intenção é:

fonte
→ compreensão
→ seleção do que vale a pena contar
→ história/ângulo
→ narrativa
→ roteiro original em PT-BR
→ humanização/revisão
→ narração

Respeitando:

* factualidade;
* originalidade;
* anti-plágio;
* direção editorial;
* naturalidade.

O sistema já possui mecanismos de originalidade e pipeline editorial.

Reaproveite-os.

# Auditabilidade

Hoje estamos em homologação.

Precisamos conseguir responder:

“Por que este vídeo saiu assim?”

Para um job finalizado deve ser possível reconstruir, usando dados persistidos ou artefatos já existentes quando apropriado:

* usuário;
* configuração aplicada;
* conteúdo/fato/ideia escolhido;
* fonte;
* decisões editoriais;
* roteiro;
* gameplay selecionada;
* segmentos utilizados;
* voz;
* render settings;
* canal;
* publicação;
* QA;
* worker responsável.

Não saia criando novas tabelas só por isso.

Primeiro descubra o que já é persistido em:

* modelos;
* artifacts;
* jobs;
* scripts;
* videos;
* eventos;
* metadata;
* logs.

Melhore a rastreabilidade onde realmente houver lacunas.

# Worker e escalabilidade

Hoje o Compute Plane roda localmente.

Mas o design não pode pressupor eternamente um único worker.

Analise:

* como jobs são reivindicados;
* como payload/contexto são enviados;
* como arquivos são baixados;
* como o worker guarda estado local;
* como resultados são enviados de volta;
* como retry funciona;
* como reconnect funciona.

Uma execução não deve depender de conhecimento implícito existente apenas naquele computador.

Evite qualquer solução baseada em:

* estado global do processo;
* dados de job anterior;
* usuário “atual” no worker;
* arquivo compartilhado sem ownership;
* ordem específica de execução;
* single-worker assumptions.

# Retry, idempotência e publicação

Audite principalmente os pontos em que falhas podem ocorrer depois de trabalho parcial.

Exemplos:

* render concluído;
* upload do vídeo para VPS;
* geração de metadata;
* chamada de publicação;
* timeout da integração externa;
* restart/reconnect do worker.

Verifique se retries podem:

* gerar dois vídeos;
* publicar duas vezes;
* consumir conteúdo mais de uma vez incorretamente;
* alterar configuração;
* trocar canal;
* repetir gameplay (reutilização duplicada indevida do mesmo intervalo em retry — distinta da liberação legítima após falha, definida na seção 5);
* criar inconsistência de estado.

Não assuma que existe um problema.

Teste e prove.

Se houver, corrija de forma idempotente.

# QA

Hoje o QA precisa ser considerado também como possível barreira de integridade do pipeline.

Avalie o que ele verifica atualmente.

Determine se vale adicionar verificações determinísticas para inconsistências que não deveriam chegar à publicação.

Por exemplo, quando possível verificar objetivamente:

* render possui formato solicitado;
* configuração essencial foi aplicada;
* output corresponde ao job;
* ownership do publish target continua válido;
* artefatos necessários existem;
* seleção de gameplay é válida;
* pipeline terminou em estado consistente.

Não use LLM para validar algo que pode ser validado deterministicamente.

# O que eu quero de você primeiro

ANTES DE IMPLEMENTAR QUALQUER MUDANÇA SIGNIFICATIVA:

Faça uma análise profunda do repositório.

Leia, no mínimo:

* AGENTS.md;
* docs/ARCHITECTURE.md;
* docs/CONFIGURATION.md;
* docs/EDITORIAL_PIPELINE.md;
* docs/CREATIVE_ENGINE.md;
* docs/GAMEPLAY_ANALYSIS.md;
* docs/EDITORIAL_REFACTOR_PLAN.md;
* docs/EDITORIAL_REFACTOR_PLAN_V2.md;
* docs/EDITORIAL_MANIFESTO.md;
* docs/EDITORIAL_PRINCIPLES.md;
* docs/EDITORIAL_RESEARCH_JOURNAL.md;
* docs/EDITORIAL_CONSOLIDATION_REPORT.md;
* docs/EDITORIAL_EVALUATION.md.

Depois mapeie no código o caminho completo:

configuração do usuário
→ automação
→ criação de job
→ seleção de conteúdo
→ planejamento editorial
→ script
→ TTS
→ gameplay
→ render
→ QA
→ vídeo
→ metadata
→ publicação

Não confie somente na documentação.

A documentação pode estar defasada.

O código é a autoridade sobre o comportamento atual.

# Primeira entrega: diagnóstico

Antes da implementação, produza um diagnóstico técnico contendo:

## A. Mapa do fluxo real

Liste:

* componentes;
* serviços;
* modelos;
* endpoints;
* worker routes;
* adapters;
* payloads;
* pontos de persistência.

## B. Causas raiz

Para cada problema observado, identifique a causa ou causas reais.

Não diga apenas “configuração não propagada”.

Mostre:

* onde deveria ser propagada;
* onde está sendo perdida;
* qual código causa isso;
* qual consequência aparece no vídeo.

## C. Problemas sistêmicos

Agrupe sintomas que têm a mesma causa.

Exemplo:

se legenda, formato e transição falham porque todos usam um mesmo fallback incorreto, trate como uma única falha arquitetural.

## D. Lacunas entre arquitetura documentada e implementação

Aponte explicitamente.

## E. Plano de implementação

Proponha o menor conjunto coerente de mudanças capaz de resolver as causas raiz.

Priorize:

1. integridade multiusuário;
2. propagação correta de configuração;
3. consumo real das fontes/ideias;
4. qualidade e diversidade editorial;
5. diversidade de gameplay;
6. idempotência;
7. rastreabilidade;
8. escalabilidade.

Depois do diagnóstico, prossiga com a implementação.

Não espere confirmação minha para cada pequena mudança se a solução estiver claramente dentro deste escopo.

# Implementação

Durante a implementação:

* preserve o que já funciona;
* reutilize componentes existentes;
* elimine caminhos duplicados quando forem a causa da inconsistência;
* centralize responsabilidades somente quando houver ganho arquitetural claro;
* não crie abstrações sem necessidade;
* evite refactor cosmético;
* não introduza dependências desnecessárias;
* mantenha migrations/backward compatibility quando relevante;
* atualize documentação afetada.

# Testes

Adicione testes de regressão baseados nos bugs reais.

No mínimo cubra:

## Isolamento multiusuário

Dois usuários com:

* configurações diferentes;
* gameplays diferentes;
* vozes diferentes;
* canais diferentes.

Um job do usuário A nunca pode usar dados privados ou destino do usuário B.

## Propagação de configuração

Configure valores claramente distintos e verifique o valor efetivamente consumido no render.

Não teste apenas API/database.

Teste o caminho até o ponto responsável pelo render.

## Seleção de conteúdo

Crie candidatos diferentes.

Garanta que o pipeline realmente consome o pool correto e que o item selecionado consegue ser rastreado até o vídeo.

## Diversidade editorial

Execute múltiplas gerações com material suficiente.

Verifique que o sistema não cai sistematicamente no mesmo assunto/estrutura quando há alternativas adequadas.

Não dependa somente de teste literal de string.

## Qualidade de conteúdo e densidade editorial

Casos de regressão obrigatórios para a seção 7:

### Densidade editorial — rejeição de conteúdo raso

1. Uma ideia/fato possui material muito raso (só sustenta hook → fato → fim).
2. O sistema avalia a densidade.
3. O vídeo NÃO é produzido apenas porque passou anti-plágio e tem português correto.
4. O sistema tenta enriquecer ou escolhe outra ideia.

### Duração coerente com target

1. `target_duration` configurado para ~60 segundos.
2. O roteiro gerado produziria ~22 segundos de narração.
3. O sistema NÃO prossegue silenciosamente para TTS/render sem justificativa editorial.
4. O sistema tenta enriquecer o roteiro, escolhe outra ideia, ou falha explicitamente.

### Gate editorial antes de render

1. Um roteiro passa anti-plágio e tem português correto.
2. Mas possui baixa densidade editorial (sem progressão, sem payoff, sem desenvolvimento).
3. O gate editorial bloqueia o prosseguimento para TTS/render.
4. O roteiro é revisado, enriquecido ou rejeitado.

### ScriptCritic — falha persistente

1. O `ScriptCritic` retorna REVISE após o máximo de revisões.
2. O pipeline NÃO prossegue com o roteiro como se nada tivesse acontecido.
3. O comportamento é explícito (rejeitar, escalar, ou marcar como falha editorial).

### Progressão editorial

1. O `EditorialPlanner` planeja 6 narrative_beats.
2. O roteiro gerado executa apenas hook + fato + fim.
3. O sistema detecta o gap entre planejamento e execução.
4. O roteiro é revisado ou rejeitado.

### Expansão antes da rejeição

1. Uma ideia é boa mas possui pouco material isoladamente.
2. O sistema tenta enriquecer (contexto, consequência, contraste, impacto).
3. Se enriquecida com sucesso, o vídeo prosome.
4. Se não enriquecível, a ideia é rejeitada — não preenchida com enrolação.

### Componentes inativos

1. `StoryFinder`, `Humanization`, `CuriosityScorer`, `CreativeEngine` estão implementados mas inativos.
2. A auditoria identifica se a ativação resolve parte dos problemas de qualidade.
3. A decisão de ativar/desativar é documentada com justificativa.

## Diversidade de gameplay (controle por intervalo temporal)

Forneça múltiplos eventos/clips elegíveis.

Garanta que múltiplas gerações não selecionem sistematicamente o mesmo segmento quando existem alternativas comparáveis.

Casos de regressão obrigatórios para a seção 5 / 5b:

### Diversidade básica por intervalo

1. Uma gameplay possui 30 minutos.
2. Um vídeo utiliza `13:22 → 13:40`.
3. O próximo job possui outras regiões elegíveis comparáveis.
4. A seleção não escolhe novamente `13:22 → 13:40` nem um overlap significativo sem necessidade.

### Overlap parcial

1. Um vídeo utiliza `13:22 → 13:40`.
2. Um novo candidato `13:27 → 13:45` é considerado.
3. O sistema trata como reutilização parcial (bloqueia ou penaliza conforme a política definida), não como trecho novo.

### Liberação ao excluir vídeo

1. Um vídeo utiliza determinados segmentos.
2. O usuário exclui o vídeo escolhendo **liberar os trechos**.
3. Esses intervalos voltam a poder participar da seleção em gerações futuras.

### Manutenção ao excluir vídeo

1. Um vídeo utiliza determinados segmentos.
2. O usuário exclui o vídeo escolhendo **manter os trechos como utilizados**.
3. O vídeo é removido, mas os intervalos continuam indisponíveis/penalizados conforme a política definida.

### Reserva versus falha

1. Um job reserva determinado intervalo.
2. O render falha antes de produzir o vídeo.
3. O sistema NÃO deve perder permanentemente aquele intervalo por causa de um job fracassado — ele volta a ser elegível.

### Concorrência

1. Dois jobs são executados em paralelo.
2. Ambos encontram o mesmo melhor segmento.
3. A arquitetura impede ou controla a dupla utilização indevida do mesmo intervalo, conforme a estratégia implementada.

### Isolamento multiusuário

1. O usuário A utiliza trechos de uma gameplay.
2. O usuário B possui a mesma gameplay (outra biblioteca/contexto).
3. Os trechos utilizados por A não influenciam a seleção de B.

### Fallback explícito

1. Todos os trechos elegíveis de uma gameplay já foram utilizados.
2. O sistema executa um novo job.
3. O comportamento segue o fallback definido (penalização progressiva, reinício controlado, ou falha explícita) — nunca reutilização silenciosa e repetitiva dos mesmos clips.

### Privacidade de gameplay

1. User A envia gameplay privada.
2. User B gera vídeo.
3. Gameplay de A nunca aparece no pool de B.

### Gameplay pública como fallback

1. User A torna gameplay pública.
2. User B possui fallback público habilitado.
3. Após esgotar material próprio, B pode selecionar gameplay de A.

### Prioridade da biblioteca própria

1. User B ainda possui material próprio adequado.
2. Existe gameplay pública com score melhor.
3. Resultado: usar material próprio de B.

### Histórico independente (gameplay pública)

1. User A usa `13:22 → 13:40` de gameplay pública X.
2. Para A: trecho fica utilizado.
3. Para B: trecho continua disponível.

### Concorrência do mesmo usuário

1. User A tem dois jobs concorrentes.
2. Ambos querem `13:22 → 13:40`.
3. Somente um pode reservar/utilizar o intervalo.

### Concorrência entre usuários (gameplay pública)

1. User A e User B executam jobs simultaneamente.
2. Ambos escolhem `13:22 → 13:40` de uma gameplay pública.
3. Ambos podem utilizar (históricos independentes).

### Esgotamento — parar

1. User A esgotou todos os trechos próprios.
2. Fallback = stop.
3. Resultado: não reutilizar material gasto, não usar públicas, interromper explicitamente.

### Esgotamento — públicas

1. User A esgotou os trechos próprios.
2. Fallback = allow_public.
3. Resultado: sistema passa a procurar gameplay pública.

### Pública → privada

1. User A torna gameplay pública.
2. User B consegue utilizá-la.
3. A torna gameplay privada novamente.
4. Novos jobs de B não podem mais selecioná-la.
5. Vídeos existentes permanecem consistentes.

### Exclusão com gameplay pública

1. User B usa gameplay pública de A.
2. B exclui o vídeo e libera os trechos.
3. Somente o histórico de B é alterado; nada muda para A ou C.

## Retry/idempotência

Simule falhas em pontos críticos.

Garanta que retry preserve ownership/contexto e não gere publicação duplicada.

# Homologação final

Depois de implementar, execute uma bateria de geração controlada.

Quero evidências.

Gere casos suficientes para verificar:

* usuários diferentes;
* configurações visivelmente diferentes;
* ideias diferentes;
* conteúdos diferentes;
* gameplays diferentes;
* formatos diferentes;
* destinos de publicação diferentes quando seguro no ambiente de teste.

Compare o resultado produzido com a configuração solicitada.

## Homologação editorial

Além dos testes automatizados, execute uma etapa de homologação editorial.

Pegue um conjunto fixo de ideias/fontes e compare:

* pipeline atual;
* pipeline após as melhorias.

Para cada vídeo, registre pelo menos:

* duração planejada;
* duração final;
* quantidade de material factual usado;
* Story Concept (quando `StoryFinder` ativo);
* descoberta central;
* progressão;
* payoff;
* avaliação do `ScriptCritic`;
* motivo de aprovação/rejeição.

NÃO transforme tudo em métricas artificiais.

Use também a metodologia editorial já existente no projeto (`docs/EDITORIAL_EVALUATION.md`).

# Critério de conclusão

Não considere a tarefa concluída porque “os testes passam”.

Considere concluída quando for possível demonstrar que:

* cada job mantém o usuário correto durante todo o ciclo;
* configurações do usuário chegam corretamente ao output;
* publicação cross-user está impedida;
* o banco de conteúdo realmente participa da geração;
* os vídeos deixam de depender de um pequeno conjunto de assuntos genéricos;
* a direção editorial existente está sendo aplicada;
* gameplay não dita indevidamente o conteúdo editorial;
* seleção de gameplay possui diversidade adequada;
* ownership e visibilidade de gameplay são respeitadas (privada nunca vira pool de terceiros; pública exige aceite explícito);
* o histórico de trechos é escopado por usuário consumidor (uso por A não bloqueia B);
* a biblioteca própria do usuário é sempre priorizada sobre gameplays públicas;
* o fallback configurável pelo usuário (stop / allow_public) é respeitado;
* vídeos editorialmente subdesenvolvidos (rasos, curtos demais, sem progressão, sem payoff) NÃO chegam ao render;
* `target_duration` é respeitado como constraint, não apenas como referência no prompt;
* um roteiro que falha repetidamente no `ScriptCritic` não prossegue para render silenciosamente;
* componentes editoriais inativos são auditados e ativados (ou justificadamente mantidos inativos);
* retries são seguros;
* comportamento é rastreável;
* a arquitetura suporta evolução para múltiplos workers sem depender de estado implícito.

# Entrega final

Ao terminar, apresente:

1. causas raiz encontradas;
2. problemas que inicialmente pareciam separados mas tinham a mesma origem;
3. arquitetura antes e depois;
4. arquivos e componentes alterados;
5. alterações em modelos/persistência, se houver;
6. alterações no protocolo Control Plane ↔ Worker, se houver;
7. mudanças no pipeline editorial;
8. mudanças na seleção de gameplay;
9. mudanças na propagação de configuração;
10. mudanças de isolamento multiusuário;
11. testes adicionados;
12. evidências da homologação;
13. riscos restantes;
14. dívida técnica ainda relevante;
15. próximos passos recomendados.

# Princípio final

Não quero uma sequência de patches para esconder os sintomas.

Quero que você trate esta etapa como engenharia de produto.

Temos fontes automáticas.
Temos ideias.
Temos catálogo de jogos.
Temos gameplays analisadas.
Temos IA local.
Temos direção editorial.
Temos render.
Temos publicação.
Temos configuração por usuário.

A fase agora é fazer tudo isso funcionar como UM sistema.

Primeiro entenda profundamente a arquitetura atual.

Depois corrija as causas estruturais.

E então prove, por testes e homologação, que o pipeline ficou confiável.
