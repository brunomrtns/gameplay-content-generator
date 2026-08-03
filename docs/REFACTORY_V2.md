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
* **selecionado/reservado** para um job — ainda não utilizado, mas temporariamente indisponível para outros jobs concorrentes do mesmo usuário;
* **efetivamente utilizado** — quando o vídeo correspondente está persistido no GPCG como vídeo gerado (pendente de aprovação, aprovado, aguardando publicação, publicado, ou estado equivalente que represente um output efetivamente gerado e preservado no sistema);
* **liberado novamente** — volta a ser elegível (ex.: geração falhou, job cancelado, vídeo pendente excluído);
* **associado a vídeo publicado no YouTube** — permanece utilizado; exclusão local NÃO libera automaticamente.

Regra crítica: **selecionado ≠ utilizado.**

Um trecho só passa a ser efetivamente utilizado quando existe um vídeo persistido no GPCG.

Antes disso, está apenas reservado temporariamente.

```text
disponível
→ reservado para job
→ geração em andamento
```

Enquanto o vídeo ainda está sendo gerado, o trecho está apenas temporariamente reservado.

Se a geração não chegar ao estado em que o vídeo fica disponível no GPCG, o trecho deve voltar automaticamente para disponível.

### Quando o trecho passa a ser utilizado

O trecho passa para estado efetivamente utilizado quando existe um vídeo persistido no GPCG e disponível como:

* pendente de aprovação;
* aprovado;
* aguardando publicação;
* publicado;
* ou estado equivalente existente na arquitetura que represente um vídeo efetivamente gerado e preservado no sistema.

NÃO dependa exclusivamente da publicação no YouTube.

O simples fato de o vídeo já existir no GPCG como um output gerado e pendente de decisão do usuário já deve consumir aqueles trechos.

Analise os estados reais do modelo `Video` e do fluxo de publicação e adapte a terminologia aos estados existentes.

### Casos que DEVEM liberar o trecho (reservado → disponível)

Os intervalos reservados devem voltar ao pool disponível quando ocorrer:

* geração falhou antes de produzir vídeo válido;
* worker falhou;
* pipeline foi interrompido;
* usuário clicou em STOP durante a geração;
* job foi cancelado;
* job expirou;
* retry abandonou a tentativa anterior;
* qualquer outro fluxo em que nenhum vídeo pendente de aprovação tenha sido produzido.

Nesses casos:

**o trecho não foi efetivamente consumido.**

Portanto, deve voltar a ser elegível.

### Resumo do ciclo de vida

```text
disponível
→ reservado
→ [falha / stop / cancelamento]
→ disponível
```

ou:

```text
disponível
→ reservado
→ vídeo persistido no GPCG como pendente de aprovação
→ utilizado
```

E:

```text
vídeo pendente excluído
→ trecho volta a disponível
```

E:

```text
vídeo publicado no YouTube
→ trecho permanece utilizado
→ exclusão local NÃO libera automaticamente
```

Essa transição deve ser idempotente e auditável.

### Comportamento em falhas, retries e cancelamentos

O controle de utilização deve interagir corretamente com o ciclo de vida do job:

* **render falho antes de produzir vídeo** — o trecho reservado deve voltar a ser elegível. O sistema NÃO deve perder permanentemente aquele intervalo por causa de um job fracassado;
* **STOP durante a geração** — se o usuário clicar em STOP antes de existir vídeo pendente persistido, o trecho reservado deve voltar a ser elegível;
* **retry** — um retry não deve duplicar o registro de utilização nem consumir dois trechos distintos sem necessidade; o trecho reservado original deve ser reutilizado ou liberado de forma determinística;
* **job cancelado** — trechos reservados devem ser liberados;
* **job expirou** — trechos reservados devem ser liberados;
* **vídeo gerado e persistido no GPCG (pendente de aprovação)** — o trecho passa a ser "efetivamente utilizado" (o vídeo existe no sistema e pode ser aprovado/publicado depois);
* **vídeo pendente excluído pelo usuário** — os trechos voltam automaticamente a ficar disponíveis (ver seção 5b);
* **publicação concluída no YouTube** — o trecho permanece utilizado; exclusão local NÃO libera automaticamente (ver seção 5b).

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

**Precedência do fallback = stop:**

Se `fallback = stop`, o sistema **para**.

NÃO:

* reutiliza gameplay;
* reseta histórico;
* busca pública;
* aplica fallback interno de reciclagem.

Qualquer política de reutilização só pode acontecer quando uma configuração explícita permitir reutilização.

`stop` NÃO cai em outra política secundária.

**Quando fallback != stop:**

Se mesmo dentro do pool elegível (próprio ou público) não existir material totalmente novo, o sistema pode — de forma explícita, configurável e testável — adotar uma política de reutilização:

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
13. Após persistência válida do Video no GPCG
    (pendente de aprovação ou posterior):
    registrar uso efetivo.
    Render temporário/artefato intermediário NÃO consome segmento.
```

## Objetivo verificável

Quando existem múltiplos segmentos bons disponíveis, o sistema não deve ficar escolhendo repetidamente o mesmo pequeno subconjunto, nem intervalos com overlap significativo com trechos recentemente utilizados.

Uma gameplay longa deve poder alimentar muitas gerações diferentes antes de qualquer reutilização se tornar necessária.

# 5b. Exclusão de vídeo e liberação de trechos de gameplay

A regra de utilização de trechos deve ser integrada ao fluxo de exclusão de vídeos na interface.

A regra de exclusão **diferencia** entre vídeo pendente de aprovação e vídeo já publicado no YouTube.

Esses são ciclos de vida diferentes e devem ser tratados de forma diferente.

Hoje não existe endpoint/UI de exclusão de vídeo. Implemente-o como parte desta fase, seguindo o padrão visual e textual existente da aplicação (dark theme, teal accent, glass effects, toasts).

## Vídeo pendente de aprovação — liberação automática

Se um vídeo **pendente de aprovação** for excluído pelo usuário:

**os trechos utilizados naquele vídeo voltam automaticamente a ficar disponíveis.**

Nesse caso NÃO pergunte se o usuário deseja manter os trechos como usados.

O vídeo foi descartado antes de publicação/aprovação final e, portanto, seus segmentos devem voltar ao pool.

```text
vídeo pendente excluído
→ trechos voltam a disponível automaticamente
```

## Vídeo já publicado no YouTube — comportamento diferente

Para vídeos **já publicados no YouTube**:

* os trechos permanecem utilizados;
* excluir apenas o registro local NÃO deve automaticamente liberar os segmentos;
* qualquer política de liberação nesse caso precisa ser explícita e coerente com o comportamento real do produto.

NÃO misture:

* descarte de vídeo ainda não publicado (liberação automática);
* remoção de vídeo já publicado (trechos permanecem utilizados).

São ciclos de vida diferentes.

## Fluxo de UX

Quando o usuário clicar em excluir um vídeo, deve existir o popup normal de confirmação de exclusão.

### Vídeo pendente de aprovação

Para vídeo pendente, a exclusão é simples: o vídeo é removido e os trechos voltam automaticamente ao pool.

A interface pode informar isso ao usuário, mas NÃO precisa oferecer decisão sobre manter trechos.

### Vídeo já publicado no YouTube

Para vídeo já publicado, se a arquitetura permitir liberação opcional de trechos, a interface pode oferecer uma decisão relacionada aos trechos de gameplay utilizados naquele vídeo.

A interface deve perguntar, em linguagem clara para um usuário comum, se os trechos de gameplay daquele vídeo podem voltar a ficar disponíveis para futuras gerações.

NÃO use texto técnico como "Deseja liberar os segmentos temporais?".

A UX deve comunicar algo conceitualmente semelhante a:

> "Quer permitir que os trechos de gameplay usados neste vídeo sejam utilizados novamente em futuros vídeos?"

E oferecer duas decisões semanticamente claras:

* **liberar os trechos novamente**;
* **manter esses trechos como já utilizados**.

Escolha a melhor redação seguindo o padrão visual e textual existente da aplicação.

Se a arquitetura não permitir liberação de trechos de vídeo já publicado, apenas mantenha os trechos como utilizados e informe o usuário.

## Semântica das duas decisões (vídeo publicado)

Excluir o vídeo e liberar a gameplay são **decisões diferentes**.

Se o usuário excluir o vídeo publicado e optar por **liberar os trechos**:

* o vídeo é removido conforme a política atual de exclusão (arquivo, thumbnail, registro, referências em `Job`/`ContentPlan` quando aplicável);
* os intervalos associados a ele voltam a ser elegíveis para futuras gerações **daquele usuário consumidor**.

Se o usuário excluir o vídeo publicado e optar por **NÃO liberar**:

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

* O endpoint de exclusão deve diferenciar vídeo pendente de aprovação de vídeo publicado no YouTube;
* Para vídeo pendente: liberação automática de trechos, sem decisão do usuário;
* Para vídeo publicado: se liberação opcional for suportada, o endpoint deve receber explicitamente a decisão do usuário sobre os trechos (ex.: `release_segments: bool`), não inferir automaticamente;
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

## Densidade antes de duração (proibição de padding)

Existe um comportamento que deve ser explicitamente proibido:

**NÃO esticar artificialmente uma ideia pequena para atingir o `target_duration`.**

Já aconteceu de uma ideia que naturalmente sustentaria ~20–25 segundos ser transformada em um roteiro de ~60 segundos através de:

* repetição da mesma informação;
* reformulação do mesmo ponto;
* contexto irrelevante;
* frases de preenchimento;
* explicações óbvias;
* introduções longas;
* conclusão redundante;
* perguntas retóricas;
* suspense artificial;
* CTA usado como preenchimento.

Isso NÃO é enriquecimento editorial.

É **padding**.

O plano deve deixar explícito que:

**o sistema deve adaptar a duração à quantidade de história disponível, e nunca fabricar história para preencher duração.**

## Enriquecer ≠ esticar

Deixe clara a diferença.

### Enriquecer

Adicionar informação factual e editorialmente útil que aprofunda a descoberta:

* contexto;
* causa;
* consequência;
* contraste;
* origem;
* impacto;
* cronologia;
* implicação;
* conexão relevante;
* evidência complementar.

### Esticar

Usar mais palavras para dizer essencialmente a mesma coisa.

Isso deve ser rejeitado.

Toda expansão deve passar por uma pergunta simples:

> "Esta nova informação muda ou aprofunda a compreensão do espectador?"

Se não muda, provavelmente é padding.

A expansão deve aumentar densidade factual ou narrativa.

NÃO apenas número de caracteres.

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

## Capacidade narrativa natural da ideia

Adicione ao pipeline o conceito de **capacidade narrativa natural**.

NÃO precisa necessariamente virar um novo campo, modelo ou serviço.

É um conceito editorial a ser avaliado utilizando os mecanismos existentes.

Uma ideia pode naturalmente sustentar:

* ~25s;
* ~40s;
* ~60s;
* ou mais.

O sistema deve tentar avaliar isso antes de produzir o roteiro final.

Exemplo:

Se uma ideia possui:

* uma única afirmação;
* pouco contexto;
* nenhuma consequência relevante;
* nenhuma progressão;
* nenhum aprofundamento disponível;

ela provavelmente não sustenta um vídeo de 60 segundos.

Nesse caso, NÃO peça ao LLM para simplesmente "expandir".

O sistema deve reconhecer que existe incompatibilidade entre:

* densidade da história;
* duração desejada.

### Relação com Story Finder

O `StoryFinder` e os mecanismos editoriais existentes devem ajudar a responder não apenas:

> "Existe uma história aqui?"

mas também:

> "Existe história suficiente aqui para o tipo e duração de vídeo que estamos tentando produzir?"

NÃO crie outro componente se isso puder ser incorporado ou inferido pelos mecanismos existentes.

A intenção é fortalecer o fluxo existente, não redesenhá-lo.

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

### Detecção de padding

Avalie se mecanismos existentes, principalmente `ScriptCritic` e/ou `Humanization`, podem detectar sintomas de padding como:

* mesma afirmação repetida em palavras diferentes;
* baixa quantidade de novas informações por beat;
* parágrafos que não alteram a compreensão;
* redundância semântica;
* contexto que não contribui para o payoff;
* conclusão repetindo integralmente a abertura;
* excesso de transições sem conteúdo;
* suspense sem nova informação;
* CTA usado para preencher tempo.

NÃO crie uma nova arquitetura sem necessidade.

Fortaleça os mecanismos existentes.

### Critério por beat

Durante revisão, cada beat deveria justificar sua existência.

Uma pergunta útil:

> "Se eu remover este trecho, a história perde alguma informação, progressão, emoção ou compreensão relevante?"

Se a resposta for não, esse trecho provavelmente é preenchimento.

Isso pode ser usado como princípio de revisão pelo mecanismo existente.

NÃO precisa necessariamente virar uma regra determinística isolada.

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

## target_duration precisa ser respeitado (constraint com tolerância)

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

`target_duration` deve representar a duração desejada do produto, mas **não um número rígido exato**.

A tolerância real deve ser definida com base na arquitetura/configuração existente.

Mas uma regra deve ficar clara:

**`target_duration` não autoriza o sistema a inventar densidade que a história não possui.**

Quando existir incompatibilidade forte entre a história e o target, o pipeline deve decidir entre:

1. enriquecer a história com informação factual realmente relevante;
2. buscar contexto/fontes complementares;
3. encontrar um ângulo editorial melhor;
4. aceitar duração menor, quando editorialmente apropriado;
5. rejeitar a ideia e selecionar outra.

Nunca:

> transformar uma história de 20 segundos em uma história de 60 segundos apenas aumentando a quantidade de palavras.

### Relação com `gpcg_narration_min_chars`

`gpcg_narration_min_chars` deve funcionar como:

* guard técnico;
* sinal de diagnóstico;
* indicador de possível subdesenvolvimento;
* gatilho para revisão.

Mas NÃO como uma obrigação editorial absoluta.

Um roteiro abaixo de `min_chars` pode indicar:

* pouca substância;
* Story Finder fraco;
* pesquisa insuficiente;
* truncamento;
* resumo excessivo;
* problema de `target_duration`;
* falha de geração.

Nesses casos, o sistema deve investigar.

Porém, se o pipeline conseguir demonstrar que:

* a história está completa;
* existe descoberta;
* existe progressão;
* existe payoff;
* não há contexto relevante faltando;
* não há padding;
* a duração menor é natural;

então o roteiro pode ser aceito mesmo abaixo de `gpcg_narration_min_chars`.

Portanto:

**`min_chars` é uma constraint técnica contextual, não uma regra editorial absoluta.**

NÃO permitir que o sistema faça:

```text
script curto
→ adicionar palavras até atingir min_chars
```

O comportamento correto é:

```text
script curto
→ diagnosticar
→ enriquecer legitimamente se houver substância
OU
→ aceitar duração natural
OU
→ rejeitar a ideia
```

O `ScriptService` já possui bounds de caracteres (`gpcg_narration_min_chars`, `gpcg_narration_max_chars`) e instrui o LLM a expandir se curto.

MAS se o LLM ignorar e retornar um script curto, não há gate que impeça o prosseguimento.

Esse gap precisa ser corrigido: o bound de caracteres deve ser um sinal de diagnóstico, não uma sugestão de padding.

A validação de duração no `QAService` ocorre APÓS o render e é permissiva (um vídeo de 22s com target 60s passa com score 85).

Isso é tarde demais e permissivo demais.

## Duração curta pode ser correta (mas só depois de provar que está completa)

NÃO transforme a regra anti-padding no extremo oposto.

Um vídeo de 25–30 segundos pode ser excelente se a história realmente termina ali.

MAS aceitar uma duração curta NÃO pode virar um atalho operacional para o pipeline.

Antes de concluir que um vídeo curto é editorialmente adequado, o sistema deve ter evidência de que:

* a história está completa;
* existe descoberta;
* existe progressão suficiente;
* existe payoff;
* não há contexto factual relevante faltando;
* não há fonte complementar razoável que mudaria materialmente a compreensão;
* o roteiro não está curto por falha de pesquisa;
* o roteiro não está curto porque o Story Finder ou Editorial Planner gerou um conceito pobre;
* o roteiro não está curto porque o ScriptService resumiu demais;
* o `target_duration` não foi perdido no fluxo;
* não houve truncamento ou outro problema técnico.

### Curto porque a história é naturalmente curta

Isso pode ser válido.

### Curto porque o pipeline não desenvolveu a história suficientemente

Isso NÃO deve ser aceito automaticamente.

Antes de aprovar uma duração muito abaixo do target, o pipeline deve verificar se houve oportunidade legítima de:

* aprofundar contexto;
* buscar consequência;
* buscar impacto;
* conectar histórico relevante;
* buscar fonte complementar;
* melhorar o ângulo;
* selecionar outra ideia mais adequada ao formato.

Se nada disso adicionar valor real e a história estiver completa, a duração menor pode ser aceita.

A regra deve ser:

**duração curta é resultado editorial possível, não fallback preguiçoso.**

O problema NÃO é "vídeo curto".

O problema é:

* vídeo curto porque o pipeline não pesquisou ou desenvolveu o suficiente;
* ou vídeo longo artificialmente porque uma ideia pequena foi esticada.

O objetivo é encontrar a duração coerente com a história.

**Vídeo curto pode ser excelente, mas só depois de provar que está completo.**

**Vídeo longo só é válido se a duração vier acompanhada de substância real.**

## Hierarquia de decisão editorial

Inclua no pipeline uma regra clara de precedência editorial para resolver conflitos entre factualidade, duração e densidade:

**Factualidade > completude da história > densidade editorial > duração desejada > comprimento textual**

### Factualidade

Nunca inventar, exagerar ou distorcer informação para:

* aumentar retenção;
* preencher duração;
* enriquecer roteiro;
* atingir target;
* atingir `min_chars`.

### Completude da história

A história precisa estar completa.

NÃO cortar contexto essencial apenas para manter o vídeo curto.

### Densidade editorial

Cada parte do roteiro precisa acrescentar valor real.

NÃO adicionar conteúdo apenas para aumentar tempo.

### Duração desejada

`target_duration` é uma preferência/constraint de produto com tolerância.

Ela orienta o formato, mas NÃO supera a qualidade da história.

### Comprimento textual

`min_chars` e `max_chars` são mecanismos técnicos de apoio.

Eles são os últimos da hierarquia.

NÃO podem obrigar o sistema a:

* inventar conteúdo;
* repetir ideias;
* produzir padding;
* destruir uma história naturalmente curta e completa.

### Resumo

```text
Factualidade
> completude da história
> densidade editorial
> duração desejada
> comprimento textual
```

**`target_duration` orienta.
`min_chars` diagnostica.
A história decide.**

## Regra de decisão editorial

Conceitualmente, o fluxo deve buscar:

```
boa ideia
→ avaliar capacidade narrativa
→ pesquisar/enriquecer quando houver substância real
→ construir história
→ estimar duração natural
→ comparar com target
```

Se houver incompatibilidade forte:

```
enriquecer legitimamente
OU
selecionar outra ideia
OU
aceitar duração menor quando editorialmente apropriado
```

Nunca:

```
pedir ao LLM para falar mais até atingir o número desejado
```

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

# Notícias, artigos e fontes (matéria-prima editorial de fontes online)

A partir de agora, boa parte da matéria-prima editorial virá de ideias extraídas de fontes online.

Isso NÃO significa que o GPCG deve simplesmente resumir, traduzir ou reescrever notícias.

O fluxo precisa tratar artigos, notícias e fontes externas como **matéria-prima editorial**.

O fluxo desejado é:

```
fonte online
→ coleta
→ validação
→ compreensão
→ avaliação editorial
→ extração da ideia central
→ contextualização
→ Story Finding
→ narrativa
→ roteiro original
→ humanização
→ revisão
→ vídeo
```

Conteúdo coletado automaticamente não deve virar simples tradução ou cópia.

Quando uma boa fonte é selecionada, o pipeline precisa usá-la como matéria-prima.

A intenção é:

```
fonte
→ compreensão
→ seleção do que vale a pena contar
→ história/ângulo
→ narrativa
→ roteiro original em PT-BR
→ humanização/revisão
→ narração
```

Respeitando:

* factualidade;
* originalidade;
* anti-plágio;
* direção editorial;
* naturalidade.

O sistema já possui mecanismos de originalidade e pipeline editorial.

Reaproveite-os.

## Cuidado com o que entra no pipeline

Nem toda informação encontrada online merece virar vídeo.

Antes de uma ideia entrar de fato no pipeline editorial, valide se ela é:

* relevante;
* coerente;
* factual;
* suficientemente clara;
* relacionada ao universo de conteúdo do sistema;
* editorialmente aproveitável;
* sustentada pela própria fonte;
* não simplesmente clickbait da publicação original.

O sistema NÃO deve confiar automaticamente no título ou resumo de uma notícia.

A IA deve analisar o conteúdo da fonte e verificar se existe substância real por trás da chamada.

## Validação com IA antes do uso editorial

Use IA para avaliar a informação coletada antes de transformá-la em conteúdo.

A validação deve responder questões como:

* O que realmente aconteceu?
* Qual é a afirmação principal?
* A fonte sustenta essa afirmação?
* Existe contexto suficiente?
* O título exagera o conteúdo?
* A informação é relevante ou apenas promocional?
* Existe algo aqui que vale a pena explicar para o público?
* Há contradições ou pontos incertos?
* A informação parece opinião, rumor, anúncio, interpretação ou fato confirmado?
* Existe risco de o pipeline transformar uma nuance em afirmação absoluta?

NÃO invente certezas que a fonte não possui.

Quando houver incerteza, preserve a incerteza no tratamento editorial ou rejeite a ideia.

## Não copiar a notícia

A saída não deve ser:

> "Segundo a GameSpot, aconteceu X, depois Y, depois Z."

Também NÃO quero simples tradução para PT-BR.

O conteúdo original deve servir de base factual para construir um vídeo novo.

A transformação editorial deve encontrar:

* o que realmente importa;
* por que isso é interessante;
* qual é a implicação;
* qual é a surpresa;
* qual é o conflito;
* qual é a mudança;
* qual é a consequência;
* o que o espectador provavelmente ainda não percebeu;
* qual é a melhor forma de explicar aquilo.

## Aplicar a arquitetura editorial existente sobre a notícia

Depois da validação factual, a ideia deve passar normalmente pelos mecanismos já existentes no projeto.

NÃO crie um pipeline editorial paralelo para notícias.

A fonte online deve alimentar a mesma arquitetura:

```
content_planning
→ story_finding
→ editorial_planning
→ creative_engine
→ script
→ humanization
→ script_review
```

Os princípios já definidos continuam válidos:

* curiosity gap;
* familiarity;
* discovery;
* narrative progression;
* payoff;
* emoção;
* clareza;
* humanidade;
* factualidade;
* anti-plágio.

## A notícia não é a história

Um artigo pode conter dezenas de informações.

O sistema precisa encontrar qual delas realmente vira uma história.

Exemplo conceitual:

A fonte pode dizer:

> "Sony registrou aumento de lucro em parte por causa de reembolso de tarifas."

O vídeo não precisa simplesmente repetir o artigo.

Pode encontrar um ângulo como:

> "Os consumidores pagaram consoles mais caros por causa das tarifas. Agora a Sony recebeu parte desse dinheiro de volta — mas isso não significa que o consumidor vai receber algo."

Esse é apenas um exemplo conceitual.

O importante é:

extrair a tensão, consequência ou contradição real existente na fonte.

Nunca inventar uma tensão que a fonte não sustenta.

## Contexto adicional quando necessário

Uma notícia isolada pode ser insuficiente.

Se a ideia for boa, mas faltar contexto para produzir um vídeo realmente rico, o pipeline pode buscar informações complementares.

Por exemplo:

* histórico do assunto;
* declaração anterior da empresa;
* lançamento relacionado;
* contexto da franquia;
* comparação com evento anterior;
* impacto para jogadores;
* números;
* cronologia.

Mas toda expansão deve respeitar factualidade e proveniência.

O sistema precisa saber quais informações vieram de quais fontes.

NÃO use contexto adicional para "encher" o vídeo.

Use apenas quando isso melhora entendimento, narrativa ou relevância.

## Múltiplas fontes quando necessário

Para assuntos sensíveis, complexos ou potencialmente contraditórios, avalie a possibilidade de validar com mais de uma fonte antes de gerar o roteiro.

NÃO transforme isso em obrigação para qualquer curiosidade trivial.

Mas quando a afirmação central depender de:

* rumor;
* declaração controversa;
* números;
* política de empresa;
* processo judicial;
* lançamento ainda não confirmado;
* mudança de preço;
* informação potencialmente desatualizada;

a arquitetura deve permitir corroborar ou qualificar a informação antes da publicação.

## Diferenciar notícia, opinião, rumor e promoção

A IA precisa identificar a natureza da fonte.

Exemplos:

* notícia factual;
* artigo de opinião;
* review;
* rumor;
* vazamento;
* press release;
* conteúdo promocional;
* oferta comercial;
* patch notes;
* análise;
* entrevista.

NÃO trate todos esses formatos como equivalentes.

Uma promoção de pré-venda, por exemplo, não deve automaticamente virar vídeo apenas porque possui score alto.

O sistema precisa perguntar:

> "Existe uma história aqui ou apenas uma oferta comercial?"

## Relevância editorial acima do score bruto

NÃO permita que um score alto isolado faça qualquer notícia entrar no vídeo.

O score deve ser um sinal.

A decisão final deve considerar também:

* relevância para o público;
* familiaridade;
* potencial de descoberta;
* qualidade da fonte;
* profundidade;
* consequência;
* novidade real;
* contexto;
* possibilidade de narrativa.

Uma notícia tecnicamente "nova" pode continuar sendo editorialmente fraca.

## Explicação, não reprodução

O resultado precisa ser compreensível até para quem não leu a matéria original.

O vídeo deve explicar:

* o que aconteceu;
* por que aconteceu, quando houver informação suficiente;
* por que isso importa;
* o que muda;
* qual é a parte realmente interessante.

Evite pressupor que o espectador conhece a notícia.

Mas também evite despejar todo o contexto existente.

Explique apenas o necessário para que a descoberta funcione.

## Conteúdo chamativo sem clickbait

O objetivo continua sendo prender atenção.

Mas "chamativo" não significa exagerar fatos.

O hook pode explorar:

* contraste;
* ironia;
* consequência inesperada;
* mudança de contexto;
* pergunta legítima;
* detalhe surpreendente;
* contradição real.

Nunca:

* inventar urgência;
* exagerar consequência;
* afirmar algo que a fonte não confirma;
* esconder informação essencial apenas para prolongar artificialmente retenção.

A retenção precisa vir da qualidade da história.

## Preservar originalidade

O roteiro final não deve manter:

* estrutura do artigo;
* ordem dos parágrafos;
* frases traduzidas;
* expressões características;
* sequência narrativa da fonte.

A fonte fornece fatos.

O GPCG constrói uma narrativa própria.

Preserve e fortaleça os mecanismos anti-plágio já existentes.

## Rastreabilidade da fonte

Para cada vídeo derivado de conteúdo online, deve ser possível descobrir:

* qual ideia foi selecionada;
* qual fonte originou a ideia;
* quais fontes complementares foram utilizadas;
* quais fatos principais sustentaram o roteiro;
* qual foi a interpretação editorial aplicada.

NÃO necessariamente exiba tudo isso ao usuário final, mas preserve a rastreabilidade internamente.

Isso se conecta com a seção "Auditabilidade".

## Gate antes do Story Finding

A validação da matéria-prima ocorre conceitualmente **ANTES** do Story Finder:

```
fonte online
→ validação factual/relevância
→ extração da ideia
→ Story Finding
→ narrativa
→ roteiro
```

NÃO crie necessariamente um novo serviço se a arquitetura atual já tiver lugar adequado para isso.

O objetivo é evitar que:

* notícia crua;
* clickbait;
* matéria promocional;
* informação incoerente;
* conteúdo irrelevante;
* informação não sustentada pela própria fonte

chegue diretamente ao pipeline narrativo apenas porque foi coletado.

A fonte precisa primeiro provar que contém matéria-prima editorial válida.

O comportamento desejado é algo como:

```
source validation
→ factual extraction
→ relevance check
→ editorial viability
→ story finding
```

Se a fonte falhar nesses critérios:

* rejeitar;
* reduzir score;
* marcar como inadequada;
* ou impedir seleção.

Escolha o mecanismo de acordo com a arquitetura existente.

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

### Anti-padding

1. Uma ideia possui apenas material factual suficiente para aproximadamente 20–25 segundos.
2. `target_duration` está configurado para ~60 segundos.
3. Não existe contexto adicional relevante disponível.
4. O sistema NÃO gera ~60 segundos através de repetição ou reformulação.
5. O sistema deve aceitar duração menor (se editorialmente permitido) ou rejeitar a ideia e selecionar outra.

### Enriquecimento legítimo

1. Uma ideia inicialmente sustenta ~25 segundos.
2. Existem fontes complementares com contexto, consequência e impacto relevantes.
3. O sistema enriquece a história.
4. O roteiro cresce naturalmente.
5. Cada parte adicionada possui nova informação ou progressão real.
6. Esse caso é considerado enriquecimento válido, não padding.

### Beat sem função

1. Um roteiro possui um trecho que repete informação já estabelecida.
2. A remoção desse trecho não altera compreensão, progressão, emoção nem payoff.
3. O mecanismo editorial deve identificar esse trecho como redundante e revisar/remover.

### Target incompatível

1. Uma história completa naturalmente sustenta ~30 segundos.
2. O target está em ~60 segundos.
3. Não existe material adicional legítimo.
4. O pipeline não força expansão.
5. A decisão deve ser explicitamente aceitar duração menor ou selecionar outra ideia.

### Capacidade narrativa natural

1. Uma ideia possui uma única afirmação, pouco contexto, nenhuma consequência relevante.
2. O sistema avalia a capacidade narrativa natural.
3. Reconhece incompatibilidade com o target de ~60s.
4. NÃO pede ao LLM para simplesmente "expandir".

### Vídeo curto legítimo

1. Uma história sustenta naturalmente ~28 segundos.
2. Há descoberta, progressão e payoff.
3. Não existe contexto adicional relevante.
4. Fontes complementares não acrescentariam substância real.
5. O sistema aceita a duração menor sem tentar inflar o roteiro.

### Vídeo curto por subdesenvolvimento

1. Uma história sai com ~25 segundos.
2. Existem contexto, consequência e impacto relevantes ainda não usados.
3. O sistema NÃO aprova a duração curta imediatamente.
4. O pipeline tenta enriquecer ou escolhe outra ideia.

### Compressão editorial

1. Um roteiro possui 60 segundos.
2. Aproximadamente 20% pode ser removido sem perda de informação, progressão, emoção ou payoff.
3. O roteiro é considerado suspeito de padding.
4. O mecanismo editorial revisa ou rejeita antes do render.

### Roteiro denso

1. Um roteiro possui 45 segundos.
2. Remover qualquer parte relevante prejudica descoberta, contexto, progressão ou payoff.
3. O roteiro NÃO é penalizado apenas por estar abaixo de 60 segundos.

### `min_chars` abaixo do mínimo, mas história completa

1. Roteiro fica abaixo de `gpcg_narration_min_chars`.
2. História está completa, densa e sem padding.
3. Duração curta é editorialmente natural.
4. O sistema pode aceitar o roteiro.

### `min_chars` abaixo do mínimo por subdesenvolvimento

1. Roteiro fica abaixo do mínimo.
2. Existem contexto e substância relevantes ainda não utilizados.
3. O sistema NÃO aceita automaticamente.
4. Tenta enriquecer ou rejeita.

### Hierarquia editorial — factualidade vs target

1. Uma ideia precisa de distorção factual para atingir `target_duration`.
2. O sistema NÃO sacrifica factualidade para atingir o target.
3. A ideia é rejeitada ou enriquecida legitimamente.

### Hierarquia editorial — target não gera padding

1. `target_duration` = 60s.
2. História sustenta apenas 30s sem contexto adicional legítimo.
3. O sistema NÃO gera 60s de padding.
4. Aceita 30s ou seleciona outra ideia.

### Hierarquia editorial — min_chars não força repetição

1. Roteiro está abaixo de `min_chars`.
2. Não existe substância adicional legítima.
3. O sistema NÃO adiciona repetição para atingir `min_chars`.
4. Aceita duração natural ou rejeita.

### Hierarquia editorial — roteiro completo pode ser menor que target

1. Roteiro completo, denso, sem padding.
2. Duração natural é 35s.
3. `target_duration` = 60s.
4. O sistema aceita o roteiro sem forçar expansão.

### Hierarquia editorial — roteiro longo sem densidade é rejeitado

1. Roteiro possui 60s.
2. Mas ~20% pode ser removido sem perda de valor.
3. O sistema identifica padding.
4. O roteiro é revisado ou rejeitado.

## Matéria-prima de fontes online

Casos de regressão obrigatórios para a seção "Notícias, artigos e fontes":

### Validação de fonte — rejeição de clickbait

1. Uma fonte possui título chamativo mas conteúdo raso.
2. O sistema analisa o conteúdo (não confia no título).
3. A ideia é rejeitada ou reduzida no score por falta de substância.

### Validação de fonte — preservação de incerteza

1. Uma fonte apresenta informação ambígua/não confirmada.
2. O sistema NÃO transforma a nuance em afirmação absoluta.
3. A incerteza é preservada no tratamento editorial ou a ideia é rejeitada.

### Diferenciação de tipo de fonte

1. Uma promoção de pré-venda chega com score alto.
2. O sistema identifica que é conteúdo promocional, não notícia factual.
3. NÃO vira vídeo automaticamente apenas por score alto.

### Não cópia / não tradução

1. Um artigo em inglês é selecionado como matéria-prima.
2. O roteiro final NÃO mantém estrutura, ordem de parágrafos, frases traduzidas ou expressões características da fonte.
3. O anti-plágio detecta e corrige sobreposição excessiva.

### Múltiplas fontes para assunto sensível

1. Uma afirmação central depende de rumor não confirmado.
2. O sistema busca fonte corroborante antes de gerar o roteiro.
3. Se não corroborada, a afirmação é qualificada ou rejeitada.

### Rastreabilidade da fonte

1. Um vídeo é gerado a partir de uma fonte online.
2. É possível rastrear: ideia selecionada, fonte originária, fontes complementares, fatos principais, interpretação editorial aplicada.

### Gate antes do Story Finding

1. Uma fonte falha na validação (irrelevante, incoerente, ou não sustentada).
2. A ideia NÃO entra no Story Finder.
3. É rejeitada, reduzida no score, ou marcada como inadequada.

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

### Liberação ao excluir vídeo pendente

1. Um vídeo **pendente de aprovação** utiliza determinados segmentos.
2. O usuário exclui o vídeo.
3. Os trechos voltam **automaticamente** a ficar disponíveis — sem perguntar ao usuário.

### Liberação ao excluir vídeo publicado

1. Um vídeo **já publicado no YouTube** utiliza determinados segmentos.
2. O usuário exclui o registro local.
3. Os trechos **permanecem utilizados** — exclusão local NÃO libera automaticamente.
4. Se a arquitetura permitir liberação opcional, o usuário pode escolher explicitamente liberar.

### Manutenção ao excluir vídeo publicado

1. Um vídeo publicado utiliza determinados segmentos.
2. O usuário exclui o vídeo publicado e escolhe **manter os trechos como utilizados**.
3. O vídeo é removido, mas os intervalos continuam indisponíveis/penalizados conforme a política definida.

### Reserva versus falha

1. Um job reserva determinado intervalo.
2. O render falha antes de produzir o vídeo.
3. O sistema NÃO deve perder permanentemente aquele intervalo por causa de um job fracassado — ele volta a ser elegível.

### Stop durante geração

1. Job reserva `13:22 → 13:40`.
2. Usuário clica em STOP antes de existir vídeo pendente.
3. O job encerra.
4. O intervalo volta a ficar disponível.

### Falha de geração

1. Job reserva um trecho.
2. O pipeline falha antes de persistir vídeo válido.
3. O trecho volta ao pool.

### Vídeo pendente

1. Job gera vídeo com sucesso.
2. Vídeo é persistido no GPCG como pendente de aprovação.
3. Os trechos passam a ser considerados utilizados.

### Exclusão de pendente

1. Vídeo pendente utiliza determinados trechos.
2. Usuário exclui o vídeo.
3. Os intervalos voltam automaticamente a ficar disponíveis.

### Vídeo publicado

1. Vídeo foi publicado no YouTube.
2. Seus trechos permanecem utilizados.
3. Exclusão local não libera automaticamente esses trechos.

### Retry

1. Job reserva intervalo.
2. A tentativa falha.
3. Retry ocorre.
4. Não há consumo duplicado nem reserva órfã.

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
* duração natural/estimada da história, quando aplicável;
* duração final;
* quantidade de material factual usado;
* Story Concept (quando `StoryFinder` ativo);
* descoberta central;
* curiosity gap;
* payoff;
* evidência factual principal;
* progressão;
* avaliação do `ScriptCritic`;
* se houve enriquecimento;
* quais informações novas foram adicionadas no enriquecimento;
* se houve sinais de padding;
* se a duração curta foi considerada natural ou consequência de subdesenvolvimento;
* quais tentativas de enriquecimento legítimo foram possíveis;
* por que não havia contexto adicional relevante (quando aplicável);
* se o roteiro passou no teste de compressão;
* quais trechos foram identificados como dispensáveis, se houver;
* se houve sinais de padding residual;
* motivo de aprovação/rejeição.

### Critério qualitativo: "Por que este vídeo merece existir?"

Durante a homologação editorial, para cada vídeo avaliado, registre também:

* qual é a descoberta central;
* qual é a pergunta/lacuna que sustenta a atenção;
* qual é o payoff;
* qual é a evidência factual principal;
* **por que este vídeo merece existir**;
* por que ele não é apenas um resumo, curiosidade isolada, tradução de notícia ou conteúdo genérico.

Se não for possível responder claramente:

> "Por que este vídeo merece existir?"

trate isso como sinal de baixa densidade editorial e investigue antes de aprovar.

NÃO transforme isso obrigatoriamente em score numérico.

Pode ser um critério qualitativo de homologação/editorial review.

Se a duração maior foi obtida apenas repetindo conteúdo, o vídeo NÃO deve ser considerado aprovado editorialmente.

### Teste de compressão editorial (padding residual)

Na homologação editorial, adicione uma pergunta de controle:

> "Se eu remover aproximadamente 20% deste roteiro, a história perde informação, descoberta, contexto, progressão, emoção ou payoff relevante?"

A intenção NÃO é transformar "20%" em uma regra matemática rígida.

É um teste editorial de compressão.

Se uma parte significativa do roteiro puder ser removida sem perda real de valor, isso é um forte indício de:

* padding;
* repetição semântica;
* contexto dispensável;
* transições vazias;
* suspense artificial;
* conclusão redundante;
* baixa densidade por beat.

Esse critério complementa a regra já existente:

> "Se eu remover este trecho, a história perde alguma informação, progressão, emoção ou compreensão relevante?"

A diferença é que agora se avalia também o roteiro **como conjunto**, não apenas beat por beat.

Se o roteiro puder perder uma parte relevante do texto sem perder valor editorial, ele deve voltar para revisão.

Se o vídeo estiver muito abaixo do target e não houver justificativa clara de que a história realmente termina naquela duração, ele NÃO deve ser aprovado automaticamente.

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
* trechos reservados voltam a disponível quando geração falha, é cancelada, ou usuário clica em STOP antes de existir vídeo pendente;
* trechos só passam a "efetivamente utilizados" quando o vídeo está persistido no GPCG (pendente de aprovação ou publicado);
* vídeo pendente excluído libera trechos automaticamente (sem perguntar ao usuário);
* vídeo publicado no YouTube mantém trechos utilizados (exclusão local não libera automaticamente);
* vídeos editorialmente subdesenvolvidos (rasos, curtos demais, sem progressão, sem payoff) NÃO chegam ao render;
* `target_duration` é respeitado como constraint com tolerância, não como número rígido e não como incentivo ao padding;
* `min_chars` funciona como constraint técnica contextual (sinal de diagnóstico), não como regra editorial absoluta;
* scripts não forem esticados apenas para atingir `min_chars`;
* duração maior vier acompanhada de maior substância (não de padding);
* duração menor puder ser aceita quando a história estiver completa, mas só depois de provar que não há subdesenvolvimento;
* ideias incompatíveis com o target possam ser rejeitadas;
* expansão adicione informação/progressão real, não apenas caracteres;
* cada beat tenha função narrativa/editorial;
* a hierarquia editorial (factualidade > completude > densidade > duração > comprimento) seja respeitada;
* fontes inválidas sejam filtradas antes do Story Finder;
* seja possível justificar qualitativamente por que cada vídeo aprovado merece existir;
* o teste de compressão editorial (padding residual) seja aplicado na homologação;
* um roteiro que falha repetidamente no `ScriptCritic` não prossegue para render silenciosamente;
* componentes editoriais inativos são auditados e ativados (ou justificadamente mantidos inativos);
* fontes online são tratadas como matéria-prima (não roteiro), validadas antes do uso, e não copiadas/traduzidas;
* factualidade nunca é sacrificada por retenção — incerteza é preservada ou ideia é rejeitada;
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

# Disciplina de execução

Esta seção NÃO altera os requisitos funcionais do `REFACTORY_V2`.

Ela define **COMO** o plano deve ser executado.

## Não implemente a partir de suposições

Para qualquer requisito importante, classifique o estado encontrado como:

* `CONFIRMED_IN_CODE` — comportamento comprovado diretamente no código;
* `CONFIRMED_IN_DB/MODEL` — comprovado nos modelos/persistência;
* `CONFIRMED_IN_TEST` — comprovado por teste existente;
* `DOCUMENTED_ONLY` — aparece na documentação, mas não foi confirmado no código;
* `PARTIALLY_IMPLEMENTED` — existe parcialmente;
* `NOT_IMPLEMENTED`;
* `AMBIGUOUS`;
* `CONFLICTING_IMPLEMENTATIONS` — existem dois caminhos diferentes fazendo a mesma responsabilidade.

Nunca transforme `DOCUMENTED_ONLY` ou `AMBIGUOUS` em fato.

Quando houver diferença entre documentação e código:

**o código atual define o comportamento existente; o `REFACTORY_V2` define o comportamento desejado.**

Registre explicitamente a diferença.

## Evidence-first

Toda afirmação relevante no diagnóstico deve apontar para evidência concreta.

Não diga:

> "A configuração está sendo perdida no worker."

Diga algo equivalente a:

> `UserVideoSettings.subtitle_position` é persistido em X, serializado por Y, mas o payload criado por Z não inclui esse campo; em seguida `RenderPlanBuilder` aplica default W.

Sempre que possível, indique:

* arquivo;
* classe/função;
* campo;
* endpoint;
* model;
* teste;
* fluxo envolvido.

NÃO invente nomes que não existem.

## Matriz AS-IS → TO-BE antes de alterar código

Para cada domínio crítico, documente:

| Área | AS-IS comprovado | TO-BE exigido | Gap | Mudança mínima |
| ---- | ---------------- | ------------- | --- | -------------- |

Inclua pelo menos:

* contexto multiusuário;
* configuração;
* seleção de conteúdo;
* fontes;
* Story Finding;
* pipeline editorial;
* duração;
* gameplay;
* lifecycle dos segmentos;
* visibilidade pública/privada;
* publicação;
* retry/idempotência;
* worker;
* QA.

NÃO implemente até conseguir explicar claramente o gap de cada área que pretende alterar.

## Invariantes antes da implementação

Extraia do `REFACTORY_V2` invariantes que devem permanecer verdadeiros independentemente da implementação.

### Multiusuário

* Job de A nunca usa configuração privada de B.
* Job de A nunca usa gameplay privada de B.
* Job de A nunca publica no canal de B.
* Credencial de publicação precisa pertencer ao mesmo contexto do job.

### Gameplay

* `selected != used`.
* Reserva é escopada pelo consumidor.
* Usuários diferentes podem consumir o mesmo intervalo de gameplay pública.
* Dois jobs do mesmo consumidor não podem reservar o mesmo intervalo simultaneamente.
* STOP/falha/cancelamento antes de vídeo persistido libera reserva.
* Segmento só vira usado quando existe vídeo persistido no GPCG.
* Exclusão de vídeo pendente libera automaticamente seus segmentos.
* Vídeo publicado mantém segmentos usados segundo a política definida.
* Gameplay privada nunca entra no pool público.
* Gameplay própria tem prioridade sobre pública.

### Editorial

* factualidade > completude > densidade > `target_duration` > comprimento textual;
* `min_chars` não pode gerar padding;
* `target_duration` não pode gerar padding;
* fonte online não vira roteiro diretamente;
* Story Finder só recebe matéria-prima editorial válida;
* roteiro reprovado persistentemente não chega silenciosamente ao render.

Transforme essas invariantes em testes sempre que forem deterministicamente verificáveis.

## State machines explícitas

Antes de codificar lifecycle complexo, desenhe as máquinas de estado reais.

### Job

Mapeie os estados existentes no código e as transições permitidas.

### Video

Mapeie:

* inexistente;
* gerado;
* pendente;
* aprovado;
* aguardando publicação;
* publicado;
* excluído;

somente usando os estados reais existentes ou propondo mudança justificada.

### Gameplay interval

Conceitualmente:

```text
AVAILABLE
→ RESERVED
→ USED
```

Com retornos apropriados:

```text
RESERVED
→ AVAILABLE
```

em:

* failure;
* STOP;
* cancel;
* timeout;
* abandoned worker.

E:

```text
USED
→ AVAILABLE
```

quando vídeo pendente é excluído.

NÃO crie esses enums literalmente se a arquitetura não precisar deles.

O importante é tornar as transições explícitas e testáveis.

## Contratos entre Control Plane e Worker

Mapeie o payload real enviado ao worker.

Crie uma tabela com:

| Campo | Origem | Owner | Obrigatório | Default permitido? | Consumidor |
| ----- | ------ | ----- | ----------- | ------------------ | ---------- |

Inclua pelo menos:

* `user_id`;
* `job_id`;
* `config`/version;
* gameplay;
* voice;
* `target_duration`;
* channel/publish target;
* content/fact/source;
* editorial artifacts;
* render settings.

Para qualquer dado crítico multiusuário:

**NÃO aceite inferência tardia no worker quando ele puder ser enviado explicitamente pelo Control Plane.**

Evite padrões conceituais como:

* "current user";
* "default channel";
* "first available channel";
* "last loaded voice";
* configuração global residual.

## Source of truth para cada conceito

Antes de implementar, documente qual entidade é autoridade para cada informação.

```text
user ownership → ?
job context → ?
video settings → ?
voice → ?
channel → ?
gameplay visibility → ?
gameplay usage history → ?
selected source/fact → ?
publication state → ?
```

NÃO permita dois lugares independentes representando o mesmo estado sem uma regra explícita de precedência.

Se existirem duplicações atuais, determine qual deve ser a autoridade e como manter backward compatibility.

## NÃO introduza shadow architecture

Se encontrar um mecanismo parcialmente funcional:

primeiro tente fortalecê-lo.

NÃO crie:

* novo Story Finder paralelo;
* novo banco de ideias;
* novo sistema de configuração paralelo;
* novo gameplay analyzer;
* novo lifecycle de vídeo separado;
* nova persistência editorial duplicada;

sem demonstrar por que a estrutura existente é incapaz de atender ao requisito.

## Implementação em fases coerentes

NÃO faça dezenas de mudanças misturadas e só teste no final.

Organize a implementação em fases dependentes.

### Fase 1 — integridade e contratos

* ownership;
* user context;
* config propagation;
* Control Plane ↔ Worker;
* publish target;
* invariantes.

### Fase 2 — lifecycle de gameplay

* persistência;
* reservas;
* concorrência;
* STOP/failure/retry;
* pública/privada;
* fallback.

### Fase 3 — conteúdo/fontes

* pool real;
* source validation;
* provenance;
* conexão com Story Finder.

### Fase 4 — editorial

* feature flags;
* Story Finder;
* enrichment;
* ScriptCritic;
* duration;
* anti-padding;
* gate.

### Fase 5 — homologação end-to-end

Adapte as fases ao dependency graph real encontrado no código.

NÃO siga esta ordem cegamente se o repositório demonstrar dependências diferentes.

## Regressões após cada fase

NÃO espere o final para descobrir que uma migration quebrou o pipeline.

Para cada fase:

1. implementar;
2. testes unitários;
3. testes de integração;
4. regressões existentes;
5. validar invariantes;
6. somente então seguir.

## Teste failure paths, não apenas happy path

Especial atenção para:

* worker morre depois de reservar gameplay;
* worker morre depois do render;
* persistência do Video falha;
* usuário pressiona STOP;
* retry ocorre após timeout;
* publish retorna timeout depois de possível sucesso externo;
* gameplay pública fica privada enquanto job está em execução;
* dois jobs do mesmo usuário concorrem;
* dois usuários usam o mesmo gameplay público;
* config muda depois que job já foi criado;
* vídeo pendente é excluído;
* endpoint recebe ID pertencente a outro usuário.

Esses casos devem ter comportamento explicitamente definido.

## Teste propriedades negativas

NÃO teste apenas que algo acontece.

Teste também que coisas proibidas NÃO acontecem.

```text
A nunca publica em B.
```

```text
Gameplay privada de A nunca entra no candidate pool de B.
```

```text
STOP nunca deixa reservation órfã.
```

```text
Retry nunca duplica publicação.
```

```text
Video sem persistência nunca consome gameplay.
```

```text
min_chars nunca força padding.
```

Esses testes são críticos.

## Migrations e backward compatibility

Antes de qualquer alteração de schema:

* identifique dados existentes;
* defina migration;
* defina valores/defaults;
* defina tratamento para registros legados;
* teste upgrade sobre banco representativo;
* evite perda silenciosa.

Se novas relações forem necessárias para gameplay usage ou visibility, prove por que os campos atuais não são suficientes.

## Observabilidade mínima

O sistema precisa permitir reconstruir decisões importantes.

Para cada job, quando relevante, registre de forma estruturada:

* user;
* job;
* selected content;
* source;
* config snapshot ou referência estável;
* gameplay source;
* intervalos;
* reservation IDs/estado equivalente;
* worker;
* publish target;
* editorial verdict;
* retry attempt;
* reason for fallback;
* reason for rejection.

NÃO logue segredos ou tokens.

## Config snapshot

Investigue explicitamente se um job deve usar:

* configuração atual do usuário durante cada etapa;
* ou snapshot da configuração no momento de criação do job.

NÃO deixe isso implícito.

Para previsibilidade e retries, prefira uma semântica determinística baseada no desenho real do produto.

Documente a decisão e teste:

```text
job criado com config A
usuário muda para config B
job antigo continua com qual?
```

Esse comportamento precisa ser intencional.

## Idempotency keys e side effects

Identifique todos os side effects externos ou permanentes:

* criação de vídeo;
* persistência de usage;
* upload;
* publicação YouTube;
* exclusão;
* liberação de segmento.

Para cada um, determine:

* pode repetir?
* como detectar retry?
* qual é a idempotency key?
* qual é o efeito se a resposta externa se perder?

NÃO confie em "o código normalmente não chama duas vezes".

## NÃO esconda incerteza

Se durante a implementação encontrar algo que o `REFACTORY_V2` não define claramente:

NÃO invente silenciosamente.

Classifique como:

`UNRESOLVED_DECISION`

Explique:

* o que o código atual faz;
* quais opções existem;
* impacto de cada opção;
* qual solução você escolheu, se houver uma opção claramente mais segura e reversível.

Para decisões irreversíveis, juridicamente sensíveis ou que alterem comportamento de produto não especificado, NÃO invente política.

## Change budget

Prefira o menor conjunto de mudanças que produza invariantes fortes.

Antes de criar um novo abstraction/service/model:

responda:

1. qual problema concreto ele resolve?
2. por que a estrutura existente não resolve?
3. qual duplicação ele elimina?
4. como será testado?
5. qual custo de migration/backward compatibility?

## Revisão pós-implementação contra o documento

Ao terminar a implementação, releia o `REFACTORY_V2` do início ao fim.

Construa uma compliance matrix:

| Requisito | Implementado? | Evidência | Teste | Observação |
| --------- | ------------- | --------- | ----- | ---------- |

Nenhum requisito deve ser considerado concluído apenas com descrição textual.

Classifique:

* `PASS`;
* `PARTIAL`;
* `NOT_IMPLEMENTED`;
* `BLOCKED`;
* `NOT_APPLICABLE` com justificativa.

## Evidência end-to-end

A tarefa NÃO termina com unit tests.

Execute cenários reais/controlados cobrindo:

* dois usuários;
* configurações diferentes;
* gameplays privadas;
* gameplay pública;
* fallback;
* STOP;
* retry;
* vídeo pendente;
* exclusão;
* publicação;
* fonte online;
* conteúdo editorial rico;
* vídeo naturalmente curto;
* vídeo que precisaria de padding e deve ser rejeitado/enriquecido.

Para cada cenário, mostre input → decisões → estado persistido → output.

## Critério final de implementação

NÃO diga "done" enquanto não conseguir responder, com evidência:

* de quem é este job?
* qual configuração ele usou?
* de onde veio esta ideia?
* por que esta fonte foi aceita?
* por que este roteiro foi aprovado?
* por que este vídeo merece existir?
* qual duração natural foi considerada?
* quais gameplays foram candidatas?
* por que este trecho foi escolhido?
* para qual usuário esse trecho está usado?
* ele estava reservado ou utilizado?
* o que acontece se o worker morrer agora?
* para qual canal este vídeo pode ser publicado?
* o retry é seguro?
* como provar que outro usuário não pode receber esse output?

Se uma dessas respostas depender de estado implícito, guessing ou comportamento não testado, a implementação ainda NÃO está pronta.

## Princípio de execução

Nesta tarefa:

**NÃO adivinhe → descubra.**

**NÃO duplique → integre.**

**NÃO confie em descrição → prove no código.**

**NÃO teste só sucesso → teste falhas.**

**NÃO considere módulo isolado → prove o fluxo end-to-end.**

**NÃO considere "funciona" → prove que funciona para o usuário correto, com a configuração correta, no estado correto, inclusive sob retry, STOP e concorrência.**

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
