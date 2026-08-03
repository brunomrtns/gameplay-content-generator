# Architecture Readiness Review — GPCG V2

**Data:** 2026-08-02
**Revisor:** Arquiteto de Software Principal
**Documento revisado:** `docs/ARCHITECTURE_V2.md` (Blueprint Definitivo)
**Código cruzado:** `src/gpcg/domain/game_resolver.py`, `src/gpcg/worker/local_db_sync.py`,
`src/gpcg/application/gameplay_retriever.py`, `src/gpcg/application/gameplay_index_service.py`,
`src/gpcg/api/routes.py`

**Postura:** revisão adversarial final antes de implementação. Após esta
revisão, mudanças estruturais terão custo elevado. Só proponho alterações
que reduzem risco, simplificam a arquitetura ou aumentam robustez de
forma mensurável.

---

## Seção 1 — Problemas Obrigatórios

Problemas que **bloqueiam implementação** ou **garantem dívida técnica
imediata** se não corrigidos antes da Fase 1.

---

### P1: Cross-game gameplay no Remote Worker é impossível com a sincronização atual

**Problema:** O blueprint descreve cross-game gameplay (§7.2) como
estensão do `GameplayRetriever` para aceitar `game_ids: list[int]`. Mas
a seleção de gameplay roda no **Remote Worker** (PC local com GPU), que
popula um SQLite temporário via `local_db_sync.py`. Este sync só popula
**um** `Game` e suas `GameplaySource`/`GameplayEvent` — os dados do job
atual. O `_expand_game_ids` retornaria `[game_id, game_id_2, ...]`, mas
a DB local não tem `GameplaySource` nem `GameplayEvent` dos outros jogos.

**Por que acontece:** A arquitetura Control Plane/Compute Plane separa
VPS (dados completos) de Worker (dados do job). O `get_job_data`
(§13.1) só envia dados do jogo do job. O blueprint não descreve como
o worker obtém gameplay de outros jogos.

**Impacto futuro:** A Fase 4 (Gameplay Intelligence) não pode funcionar
como descrita. O experimento A/B para `GPCG_CROSS_GAME_GAMEPLAY_ENABLED`
é impossível. A feature inteira fica bloqueada.

**Solução:** Duas alternativas, escolher uma:

**(A) Pré-resolver gameplay no VPS.** Mover a seleção de gameplay
cross-game para o VPS (Control Plane). O VPS resolve os `game_ids`
expandidos, seleciona os clips (eventos + time ranges), e envia apenas
os **caminhos dos arquivos + time ranges** ao worker. O worker baixa
os arquivos de gameplay adicionais e faz o render. Isto mantém o worker
leve e move a lógica de matching para onde os dados existem.

**(B) Sincronizar gameplay adicional no `get_job_data`.** Quando
`GPCG_CROSS_GAME_GAMEPLAY_ENABLED=on`, o `get_job_data` inclui
`GameplaySource` + `GameplayEvent` + embeddings dos jogos expandidos.
O worker recebe tudo e o retriever funciona localmente. Custo: payload
maior, sync mais complexo.

**Recomendação:** **(A)** é arquiteturalmente mais limpa. A seleção de
gameplay é uma decisão editorial, não uma operação de GPU. Mover para
o VPS alinha responsabilidade com localização de dados. O worker só
precisa dos arquivos de vídeo (que já baixa hoje) e dos time ranges.

**Afeta compatibilidade com blueprint:** Sim. §7.2, §13.1, §13.2
precisam ser revisados para descrever onde a seleção ocorre.

---

### P2: Integração do Game Resolver com `game_aliases` não descrita

**Problema:** O `GameResolver` existente (`domain/game_resolver.py`)
faz `resolve_l1` carregando **todos** os Games e iterando sobre
`game.aliases` (JSON). O blueprint cria `game_aliases` como tabela
separada (§4.2) mas **não descreve** como o resolver existente migra
para usar a nova tabela. O resolver continua lendo `Game.aliases`
(JSON deprecated), ignorando `game_aliases`.

**Por que acontece:** O blueprint foca no Game Registry como CRUD +
dedup na criação, mas não integra com o fluxo de resolução existente
que é o **consumidor principal** de aliases.

**Impacto futuro:** Aliases adicionados via enriquecimento (em
`game_aliases`) não são vistos pelo resolver. Uploads com nomes
alternativos não deduplicam. A tabela `game_aliases` é criada mas
seu consumidor mais importante não a usa.

**Solução:** O blueprint deve especificar explicitamente que
`resolve_l1` é reescrito para consultar `game_aliases` por
`LOWER(alias) = ?` (O(log n) via índice) em vez de iterar sobre
`Game.aliases` JSON (O(n) scan). O `Game.aliases` JSON torna-se
read-only legacy e eventualmente é removido.

**Afeta compatibilidade:** Sim. §4.2 e §4.3 devem mencionar a reescrita
do resolver. Fase 1 deve incluir "rewrite resolve_l1 to use
game_aliases table" nos entregáveis.

---

### P3: `knowledge_items.franchise`/`developer` denormalizados sem mecanismo de sync

**Problema:** O blueprint adiciona `franchise` e `developer` como
colunas em `knowledge_items` (§6.1), descritas como "denormalização
para filtro sem JOIN". Mas o `collect_rss` escreve esses campos a
partir de `Game.franchise`/`Game.developer` no momento da coleta. Se
o jogo é enriquecido **depois** da coleta, os KnowledgeItems existentes
ficam com `franchise=NULL`/`developer=NULL` e nunca são atualizados.

**Por que acontece:** Denormalização sem mecanismo de sync é uma
violação do Princípio 2 ("Uma fonte de verdade"). A denormalização
cria uma segunda fonte que diverge.

**Impacto futuro:** Items coletados antes do enriquecimento são
invisíveis em queries por escopo franchise/developer. O pipeline
editorial com `content_scope=franchise` não encontra esses items.
Comportamento não-determinístico dependente de ordem de operações.

**Solução:** **Remover `franchise` e `developer` de `knowledge_items`.**
Usar JOIN na query:

```sql
SELECT ki.* FROM knowledge_items ki
JOIN games g ON ki.game_id = g.id
WHERE g.franchise = ?
  AND ki.status = 'fresh'
ORDER BY ki.editorial_score DESC
```

Um JOIN simples não justifica denormalização. Se performance for
comprovadamente insuficiente em escala, reintroduzir com sync
(backfill após enrichment).

**Afeta compatibilidade:** Sim. §6.1 — remover 2 colunas e 2 índices.
Simplifica o schema. Reduz 2 índices.

---

### P4: `KnowledgeItemType.fact` contradiz D3 (sem dual-write)

**Problema:** O enum `KnowledgeItemType` inclui `fact` (§9.3), mas a
decisão D3 diz explicitamente que KnowledgeItem é "exclusivamente para
conteúdo externo" e Facts não são espelhados. Quando um KnowledgeItem
seria criado com `item_type='fact'`? Nunca — Facts são armazenados em
`Fact`, não em `KnowledgeItem`.

**Por que acontece:** O enum foi herdado da proposta original (que
tinha dual-write) sem remover o valor que contradiz a decisão final.

**Impacto futuro:** Implementador confunde-se: cria KnowledgeItems com
`item_type='fact'` para espelhar Facts, reintroduzindo dual-write
silenciosamente. Ou o valor fica morto no enum, gerando dúvida.

**Solução:** Remover `fact` de `KnowledgeItemType`. Se a unificação
em `get_content_ideas` precisa distinguir Facts de KnowledgeItems no
formato unificado, usar um campo separado no DTO (não no enum
persistido).

**Afeta compatibilidade:** Sim. §9.3 — remover 1 valor de enum.

---

### P5: `knowledge_items.tags` é um campo morto

**Problema:** `knowledge_items` tem `tags JSON DEFAULT '[]'` (§6.1).
Nenhum fluxo descrito no blueprint popula `tags`: `collect_rss` não
menciona tags, `score_knowledge_item` não menciona tags, nenhum
endpoint filtra por tags.

**Por que acontece:** Campo herdado da proposta original sem
consumidor definido.

**Impacto futuro:** Campo morto que implementador precisa criar na
migração, indexar (ou não), e manter em `to_dict()` sem uso. Confunde
consulta de schema.

**Solução:** Remover `tags` de `knowledge_items`. Reintroduzir quando
um conector produzir tags E o pipeline editorial as utilizar.

**Afeta compatibilidade:** Sim. §6.1 — remover 1 coluna.

---

### P6: Condição de corrida na criação simultânea de Game com mesmo slug

**Problema:** Dois uploads simultâneos para o mesmo jogo inexistente
executam o algoritmo de dedup (§4.3) em paralelo. Ambos chegam ao
passo 5 ("criar novo Game"). Ambos geram o mesmo slug. O `slug UNIQUE`
causa `IntegrityError` em um dos dois. O blueprint não descreve retry.

**Por que acontece:** O algoritmo de dedup é descrito como sequencial
mas executa em requests concorrentes.

**Impacto futuro:** Uploads falham intermitentemente com erro de
constraint em vez de vincularem ao Game recém-criado. Usuário vê erro
inesperado.

**Solução:** Após `IntegrityError` na criação de Game, **re-executar
a resolução** (passos 2-4) — o Game agora existe (criado pelo request
concorrente) e será encontrado por slug. Envolver a criação em
try/except com retry único.

**Afeta compatibilidade:** Não estrutural. §4.3 deve documentar o
retry. Adiciona robustez sem mudar schema.

---

### P7: Enriquecimento sem fronteira transacional — falha parcial deixa Game em estado híbrido

**Problema:** `enrich_game` (§5.1) faz: (1) Wikidata SPARQL, (2)
Wikipedia REST, (3) LLM lore_summary, (4) persistir em Game. Se a
etapa 2 falha após a 1, ou a 3 falha após a 2, o Game pode ter
`developer`/`publisher` escritos mas `description`/`lore_summary`
nulos. O blueprint não define se a persistência é transacional (tudo
ou nada) ou incremental.

**Por que acontece:** A função é descrita como sequência de I/O
externo + persistência, sem especificar o boundary transacional.

**Impacto futuro:** Games em estado parcialmente enriquecido têm
`enriched_at` NULL (falhou) mas campos populados. Re-enriquecimento
pode sobrescrever alguns campos e não outros. Comportamento
não-determinístico.

**Solução:** **Todas as escritas em Game acontecem em uma única
transação no final.** As etapas 1-3 coletam dados em variáveis locais.
A etapa 4 faz `session.begin()`, escreve todos os campos, commit. Se
qualquer etapa 1-3 falha, nenhum campo é escrito. `enrichment_error`
é setado com a mensagem de erro, `enriched_at` permanece NULL.

**Afeta compatibilidade:** Não estrutural. §5.1 deve especificar o
boundary transacional.

---

### P8: `editorial_score=0` em falha de LLM durante coleta esconde items permanentemente

**Problema:** O scoring (§6.6) é feito "no momento da coleta". Se o
LLM está indisponível durante o job `content_collect`, o item é criado
com `editorial_score=0.0` (default). O pipeline editorial filtra por
`min_editorial_score` (default 50). Items com score 0 são invisíveis.
O blueprint menciona "recomputação manual via endpoint" mas não
descreve re-scoring automático.

**Por que acontece:** Falha de LLM é tratada como "score zero" em vez
de "score nulo/não computado".

**Impacto futuro:** Items coletados durante janelas de indisponibilidade
do LLM são permanentemente perdidos do pipeline (a menos que alguém
manualmente dispare re-scoring). Em um sistema automatizado, isto é
silencioso.

**Solução:** `editorial_score` default = `NULL` (não 0.0). Items com
score NULL são **excluídos do ranking** mas **marcados como
"pending_score"**. O próximo ciclo de `content_collect` (ou um job
dedicado `rescore_items`) computa scores para items com `editorial_score
IS NULL`. Só items com score ≥ `min_editorial_score` são considerados
pelo pipeline.

**Afeta compatibilidade:** Sim. §6.1 — mudar default de `editorial_score`
de `0.0` para `NULL`. Adicionar lógica de re-scoring no ciclo de coleta.

---

### P9: `game_aliases` UNIQUE em `LOWER(alias)` impede jogos com mesmo nome (Doom 1993 vs Doom 2016)

**Problema:** `CREATE UNIQUE INDEX idx_game_aliases_lower ON
game_aliases(LOWER(alias))` (§4.2) significa que apenas **um** Game
pode ter "doom" como alias. Mas "Doom" (1993) e "Doom" (2016) são
jogos diferentes com o mesmo nome. O segundo não pode ter "doom"
como alias. O algoritmo de dedup (§4.3) encontraria o primeiro e
vincularia incorretamente.

**Por que acontece:** O blueprint assume que nomes de jogos são
únicos, o que não é verdade.

**Impacto futuro:** Jogos com nomes idênticos são impossíveis de
representar corretamente. Uploads de "Doom" (2016) são vinculados ao
"Doom" (1993). Dados incorretos.

**Solução:** O slug deve incluir **disambiguation** quando há colisão.
O algoritmo de dedup deve gerar slugs como `doom-1993` e `doom-2016`
usando `release_date` (do enriquecimento) ou um sufixo ordinal quando
`release_date` não está disponível. O `UNIQUE` em `LOWER(alias)`
deve ser removido — aliases não são globalmente únicos, são únicos
**por game**: `UNIQUE(game_id, LOWER(alias))`. A dedup por alias
retorna **múltiplos candidatos** e exige desambiguação (por ano ou
confirmação do usuário).

**Afeta compatibilidade:** Sim. §4.2 — mudar o unique index. §4.3 —
adicionar desambiguação. Aumenta robustez do registry.

---

### P10: Jobs `game_enrich` concorrentes para o mesmo Game

**Problema:** Um upload cria um job `game_enrich` automático (§5.4).
Antes de ser processado, o usuário clica "Enriquecer" na UI, criando
um segundo job `game_enrich` para o mesmo game. Ambos são claimable.
Dois workers (ou o mesmo worker em momentos diferentes) processam o
mesmo jogo em paralelo. Ambos fazem requests Wikidata/Wikipedia,
ambos escrevem no mesmo Game.

**Por que acontece:** Não há dedup de jobs pendentes para o mesmo
`game_id` + `job_type`.

**Impacto futuro:** Wasted HTTP requests, race condition na escrita
(último write ganha, possivelmente inconsistente), wasted LLM calls.

**Solução:** Antes de criar um job `game_enrich`, verificar se já
existe um job `game_enrich` pendente (status=queued) para o mesmo
`game_id`. Se sim, não criar duplicata. O endpoint manual de
enriquecimento faz a mesma verificação. Alternativa: claim atômico
com `WHERE game_id=? AND job_type='game_enrich' AND status='queued'`
— apenas um worker processa por vez, mas isto não previne criação
duplicada.

**Recomendação:** Verificação na criação (não criar se já existe
pendente) + idempotência no processamento (enrich é transacional,
P7).

**Afeta compatibilidade:** Não estrutural. Lógica de criação de jobs.

---

### P11: `local_db_sync.py` cria `Game` com `user_id` — contradiz deprecation

**Problema:** O blueprint depreca `Game.user_id` (§4.1, §4.4). Mas
`local_db_sync.py` linha 128 cria `Game(user_id=user_id, ...)`. Após
a migração (user_id → NULL), o sync do worker ainda tenta criar Game
com user_id, que seria NULL mas o código ainda passa o valor.

**Por que acontece:** O blueprint não lista `local_db_sync.py` como
arquivo a ser modificado na deprecation de user_id.

**Impacto futuro:** Worker cria Games locais com user_id não-NULL,
inconsistente com VPS onde user_id é NULL. Queries que assumem
user_id=NULL no Game global falham localmente.

**Solução:** Listar `local_db_sync.py` como arquivo a ser modificado
na Fase 1. Remover `user_id=user_id` da criação de Game no sync.

**Afeta compatibilidade:** Não estrutural. Fase 1 entregáveis devem
incluir `local_db_sync.py`.

---

### P12: Mecanismo de execução dos jobs no VPS é ambíguo

**Problema:** §12.2 diz "O worker legacy no VPS (ou um processador
leve integrado à API) processa `game_enrich` e `content_collect`."
"Worker legacy **ou** processador leve" — qual? O `AGENTS.md` descreve
o worker legacy como "inbox watcher + job processor". Este worker
está rodando? Tem capabilities? Sabe processar `game_enrich`?

**Por que acontece:** O blueprint deixa a execução em aberto com
"ou".

**Impacto futuro:** Implementador não sabe se deve modificar o worker
legacy existente, criar um novo processo, ou integrar na API. Cada
caminho tem implicações diferentes (deploy, monitoring, restart).

**Solução:** Decidir explicitamente. **Recomendação:** processador
integrado na API (FastAPI `BackgroundTasks` ou um thread pool leve
no processo da API). Razões: (1) evita manter um segundo processo
no VPS; (2) o processo da API já tem acesso ao DB; (3) jobs
`game_enrich`/`content_collect` são I/O-bound (HTTP + LLM leve), não
precisam de processo separado; (4) o worker legacy pode ser
descontinuado. O remote worker (GPU) continua separado.

**Afeta compatibilidade:** Sim. §12.2 deve especificar o mecanismo.
Fase 2 e Fase 3 devem incluir a implementação do processador.

---

## Seção 2 — Melhorias Altamente Recomendadas

Problemas que **não bloqueiam** implementação mas causarão dor
operacional, bugs sutis, ou retrabalho se não corrigidos.

---

### R1: `search_events` precisa de modificação para cross-game — não especificado

**Problema:** `search_events` (em `gameplay_index_service.py`) recebe
`source_id: int` (single source). O `GameplayRetriever` itera sobre
sources e chama `search_events` por source. Para cross-game, o
retriever precisa iterar sobre sources de **múltiplos** jogos. O
blueprint diz "reusando `search_events` existente" mas não descreve
a mudança.

**Solução:** O retriever já itera sobre sources (linha 151-167 de
`gameplay_retriever.py`). A mudança é: `GameplaySource.game_id ==
game_id` → `GameplaySource.game_id.in_(game_ids)`. `search_events`
não muda (continua per-source). Documentar isto explicitamente no
blueprint §7.2.

**Afeta compatibilidade:** Não. Esclarecimento.

---

### R2: `Video.knowledge_item_id` cria assimetria com vídeos baseados em Fact

**Problema:** `Video.knowledge_item_id` (D13) rastreia vídeos baseados
em KnowledgeItem. Mas vídeos baseados em Fact não têm `Video.fact_id`
— o rastreamento é via `job.artifacts` ou `ContentPlan`. Duas formas
de rastreamento para o mesmo conceito ("de onde veio a ideia deste
vídeo").

**Solução:** Adicionar `Video.fact_id` (nullable FK) simétrico a
`knowledge_item_id`. Ou: adicionar `Video.source_type` (fact|
knowledge_item) + `Video.source_id` (polymorphic). A primeira é mais
simples e consistente com o estilo do projeto.

**Recomendação:** Adicionar `Video.fact_id` (nullable FK → `facts.id`).
Ambos nullable. Um vídeo tem um ou outro (ou nenhum, para vídeos
legados).

**Afeta compatibilidade:** Sim. §9.2 — adicionar 1 coluna em `videos`.

---

### R3: Script critic pode gerar falsos positivos para notícias

**Problema:** O gate de factual accuracy (D14) valida o roteiro contra
`KnowledgeItem.content`. Para notícias RSS, `content` é o summary do
feed (1-2 frases). O roteiro naturalmente expande a notícia com
contexto, implicação, opinião — factualmente correto mas não presente
literalmente no `content`. O critic flagga como "inventado".

**Solução:** O prompt do critic deve distinguir: "o roteiro pode
elaborar e adicionar contexto sobre o item fonte, desde que não
invente **fatos** (números, datas, eventos, mecânicas) não presentes
no fonte. Contexto e implicação são permitidos; invenção de fatos
não." Ajuste de prompt, não de arquitetura.

**Afeta compatibilidade:** Não. §8.3 — esclarecer o prompt do critic.

---

### R4: Formato de unificação Facts + KnowledgeItems não definido

**Problema:** `get_content_ideas` (§6.4) faz `_merge_sources(facts,
items)` mas não define o formato comum. `Fact` tem `claim`, `category`,
`quality_score`, `novelty_score`. `KnowledgeItem` tem `title`,
`content`, `editorial_score`, `item_type`. O LLM precisa de um
formato consistente para decidir.

**Solução:** Definir um DTO `ContentIdea`:

```python
@dataclass
class ContentIdea:
    id: str  # "fact:123" ou "ki:456"
    title: str  # Fact.claim ou KnowledgeItem.title
    content: str  # Fact.claim ou KnowledgeItem.content
    source_type: str  # "user_doc" ou KnowledgeItem.source_type
    item_type: str  # "fact" ou KnowledgeItem.item_type
    score: float  # Fact.quality*novelty ou KnowledgeItem.editorial_score
```

O LLM recebe uma lista de `ContentIdea` e não sabe se veio de Fact ou
KnowledgeItem.

**Afeta compatibilidade:** Não. §6.4 — definir o DTO.

---

### R5: `content_collect` deveria ser global, não per-usuário

**Problema:** O ciclo de coleta (§6.5) diz "Para cada jogo com gameplay
disponível do usuário: chama `collect_rss`." Se 10 usuários têm
gameplay do mesmo jogo, o RSS é fetched 10 vezes. Items são globais
(`user_id=NULL`), então o primeiro cria e os outros encontram tudo
deduplicado — requests desperdiçados.

**Solução:** A coleta deve ser **global**: o job `content_collect`
(periódico, não associado a um usuário) itera sobre todos os jogos
que **qualquer** usuário tem gameplay. Items são criados com
`user_id=NULL`. O `content_intelligence.enabled` da automação
controla se o **pipeline editorial** consulta KnowledgeItems, não se
a **coleta** acontece. A coleta é global; o consumo é por-usuário
(filtrado por escopo configurado).

**Afeta compatibilidade:** Sim. §6.5 e §12.3 — coleta é global, não
per-user. O job `content_collect` não tem `user_id`.

---

### R6: Algoritmo de `content_hash` não especifica normalização

**Problema:** `content_hash = SHA256(normalize(title) +
normalize(content[:500]))` (§6.5). "normalize" não é definido.
Diferentes normalizações (com/sem acentos, com/sem case, com/sem
whitespace extra) produzem hashes diferentes. Dedup falha ou
over-deduplicates.

**Solução:** Definir explicitamente: `normalize(s) =
s.strip().lower().replace(/\s+/g, ' ')`. Sem remoção de acentos
(títulos em português com acentos são distintos de sem acentos).
Sem remoção de pontuação (pode causar over-dedup). Documentar em §6.5.

**Afeta compatibilidade:** Não. Esclarecimento.

---

### R7: Formato do BLOB de embedding não especificado

**Problema:** `embedding BLOB` (§6.2, §7.1). O formato de serialização
não é definido: `struct.pack` de floats? `numpy.tobytes()`? JSON
serializado? Pickle? O formato afeta: (a) leitura em Python; (b)
migração para pgvector; (c) interoperabilidade.

**Solução:** Especificar: `numpy.float32` array → `ndarray.tobytes()`.
Leitura: `np.frombuffer(blob, dtype=np.float32)`. Formato compacto
(768 × 4 bytes = 3072 bytes por embedding), sem overhead de JSON/pickle,
compatível com pgvector (`vector` type usa float32 internamente).

**Afeta compatibilidade:** Não. Esclarecimento crítico para implementação.

---

### R8: `KnowledgeItem.status` não tem transição `used` → `fresh` em falha

**Problema:** Um item é marcado `used` quando um vídeo é gerado. Mas
se a geração falha (após marcar `used`), o item fica `used`
permanentemente sem vídeo. Não há transição de volta.

**Solução:** Marcar `used` **apenas após sucesso completo do job**
(status=completed), não no início do pipeline. Ou: se o job falha,
reverter status para `fresh`. O primeiro é mais simples: setar
`Video.knowledge_item_id` no final (que já é quando o Video é criado)
e mudar status para `used` no mesmo commit.

**Afeta compatibilidade:** Não. Esclarecimento de fluxo.

---

### R9: `Game.enriched_at` + `enrichment_error` podem coexistir (estado ambíguo)

**Problema:** Se um jogo é enriquecido com sucesso (`enriched_at` set,
`enrichment_error` NULL) e um re-enriquecimento manual falha, o blueprint
não diz se `enriched_at` é limpo. Resultado: ambos non-null — "enriquecido
E com erro"? Ambíguo.

**Solução:** Regra: `enriched_at` e `enrichment_error` são mutuamente
exclusivos. Em sucesso: set `enriched_at`, clear `enrichment_error`.
Em falha: set `enrichment_error`, **preservar `enriched_at`** se
existir (o jogo tinha dados válidos antes da falha do re-enrich), mas
o estado visível é "erro no último enrichment" (porque
`enrichment_error IS NOT NULL`). Query de "jogos enriquecidos":
`WHERE enriched_at IS NOT NULL AND enrichment_error IS NULL`.

**Afeta compatibilidade:** Não. Esclarecimento de semântica.

---

### R10: `collect_rss` sem timeout/retry — fetch travado bloqueia job

**Problema:** `feedparser.parse(url)` (§6.5) é síncrono. Se Google
News está lento/down, o job `content_collect` bloqueia indefinidamente.
Sem timeout especificado.

**Solução:** `feedparser.parse(url, request_headers={...})` com
timeout via `httpx` pré-fetch (buscar o XML com httpx + timeout de
15s, depois `feedparser.parse(content)`). Retry com backoff
exponencial (3 tentativas). Falha de fetch não falha o job — loga
erro e continua para o próximo jogo.

**Afeta compatibilidade:** Não. Esclarecimento de robustez.

---

### R11: `GET /api/games` vs `GET /api/games/registry` — duplicação

**Problema:** §10.1 cria `GET /api/games/registry` (lista jogos
canônicos). §10.2 altera `GET /api/games` (retorna dados enriquecidos).
Dois endpoints para listar jogos. Qual o frontend usa? Qual a
diferença?

**Solução:** Não criar `/api/games/registry`. Estender `GET /api/games`
para retornar dados enriquecidos + paginação + busca. O endpoint
existente já lista jogos; adicionar campos enriquecidos ao response.
Remover `GET /api/games/registry` do blueprint. Manter `GET
/api/games/search` como endpoint separado (query diferente).

**Afeta compatibilidade:** Sim. §10.1 — remover 1 endpoint. Reduz de
6 para 5 no router.

---

### R12: `game_aliases.alias_type` e `source` sem consumidor em V2

**Problema:** `game_aliases` tem `alias_type` (default 'alternative')
e `source` (default 'manual'). Nenhum fluxo em V2 lê estes campos.
A dedup (§4.3) busca por `LOWER(alias)`, não filtra por `alias_type`
ou `source`.

**Solução:** Manter `source` (proveniência é valiosa para debug e
para saber quais aliases vieram do enriquecimento vs manual).
Remover `alias_type` (nenhum consumidor, nenhuma decisão baseada
nele em V2). Reintroduzir se um fluxo futuro distinguir tipos de
alias.

**Afeta compatibilidade:** Sim. §4.2 — remover 1 coluna de
`game_aliases`.

---

### R13: Ordem de dependência entre feature flags não documentada

**Problema:** Três flags (§8.4) têm dependências implícitas:
`GPCG_CONTENT_INTELLIGENCE_ENABLED` sem enriquecimento → KnowledgeItems
sem `franchise`/`developer` para scoping. `GPCG_CROSS_GAME_GAMEPLAY_ENABLED`
sem enriquecimento → `_expand_game_ids` retorna `[game_id]` (franchise
NULL). O blueprint não documenta estas dependências.

**Solução:** Adicionar nota em §8.4: "Ordem recomendada de ativação:
(1) `GPCG_GAME_ENRICHMENT_ENABLED` → esperar enriquecimento dos jogos
existentes → (2) `GPCG_CONTENT_INTELLIGENCE_ENABLED` → (3)
`GPCG_CROSS_GAME_GAMEPLAY_ENABLED`. Ativar flags fora de ordem não
quebra o sistema, mas pode produzir resultados sub-ótimos (items sem
scoping, cross-game sem expansão)."

**Afeta compatibilidade:** Não. Esclarecimento.

---

### R14: `EditorialStrategyService` interação com KnowledgeItems subespecificado

**Problema:** §14.3 diz que `decide_next_video()` é estendido para
"considerar KnowledgeItems além de Facts, filtrando por content_scope
e min_editorial_score". Mas não descreve a lógica: se um jogo tem
gameplay mas nenhum KnowledgeItem fresh, o que decide? Se tem
KnowledgeItem mas não gameplay? A decisão editorial atual é baseada
em inventário de gameplay + facts disponíveis.

**Solução:** Definir a lógica de decisão:
1. Candidatos: jogos com gameplay pronto E (Facts disponíveis OU
   KnowledgeItems fresh com score ≥ min_editorial_score).
2. Se `content_intelligence.enabled=false`: comportamento atual
   (apenas Facts).
3. Se `enabled=true`: para cada jogo candidato, contar KnowledgeItems
   fresh + Facts disponíveis. Priorizar jogos com mais material
   disponível (variety). Notícias quentes (published_at recente) têm
   bônus.

**Afeta compatibilidade:** Não. Esclarecimento de fluxo.

---

### R15: Backfill de embeddings para GameplayEvents existentes

**Problema:** Eventos mapeados antes da V2 não têm embeddings. A busca
semântica cross-game (§7) os exclui silenciosamente (JOIN com
`gameplay_event_embeddings` exclui eventos sem embedding). Gameplays
legadas não participam de cross-game.

**Solução:** Job de backfill: iterar sobre GameplayEvents sem
embedding, gerar embedding da `description` existente via
`nomic-embed-text`. Pode rodar no worker local (GPU) como job
`knowledge_index` (capability existente) ou como sub-tarefa do
mapping. Documentar como entregável da Fase 4.

**Afeta compatibilidade:** Não. Adiciona entregável à Fase 4.

---

### R16: Dedup de jobs `content_collect` pendentes

**Problema:** Similar a P10. O endpoint `/api/automation/check` pode
criar múltiplos jobs `content_collect` se chamado em rápida sucessão
(antes do job anterior ser processado).

**Solução:** Verificar se existe job `content_collect` com
status=queued/running antes de criar novo. Se sim, não criar.

**Afeta compatibilidade:** Não. Lógica de criação de jobs.

---

### R17: `collect_rss` sem parâmetro `since` efetivo — re-fetch desnecessário

**Problema:** `collect_rss(session, game_id, since=None)` (§6.5). Se
`since=None`, todas as entradas do feed são processadas. O
`content_hash` previne duplicatas, mas o HTTP fetch + parse +
normalização de entradas antigas é desperdício a cada ciclo.

**Solução:** `since` deve ser setado para `max(collected_at)` dos
KnowledgeItems existentes para aquele `game_id` + `source_type='rss'`.
O feed RSS é filtrado por data (Google News RSS retorna itens
recentes por padrão, mas `since` permite skip de entradas antigas
no parse).

**Afeta compatibilidade:** Não. Otimização de coleta.

---

### R18: Payload de embeddings no `submit_mapping_result`

**Problema:** Embeddings de GameplayEvents são gerados no worker e
enviados ao VPS via `submit_mapping_result` (§13.3). Para uma gameplay
com 100 eventos, são 100 × 3072 bytes (float32) ≈ 300KB adicionais no
payload. O blueprint não aborda se isto vai no mesmo request ou
separado.

**Solução:** Mesmo request é aceitável (300KB é pequeno). Serializar
como lista de `{event_id, embedding_b64}` no JSON do mapping result.
O VPS decodifica base64 → BLOB e persiste em `gameplay_event_embeddings`.
Documentar o formato em §13.3.

**Afeta compatibilidade:** Não. Esclarecimento.

---

## Seção 3 — Melhorias Opcionais

Melhorias que adicionam robustez mas não são urgentes. Implementar
se o custo for baixo durante a implementação natural da fase.

---

### O1: Algoritmo `slugify` não especificado

**Problema:** §4.4 diz "slug = slugify(canonical_name)" mas não define
o algoritmo. Diferentes implementações produzem resultados diferentes.

**Solução:** Especificar: lowercase → remover acentos (NFD
normalization) → substituir não-alphanumeric por `-` → collapse
múltiplos `-` → strip `-` nas pontas. Ex: "Bully: Scholarship
Edition" → "bully-scholarship-edition". Usar `python-slugify` ou
implementação manual documentada.

---

### O2: Localização do flag de migrações não definida

**Problema:** §9.5 diz "guardado por flag em
`metadata_json.schema_migrations`". Mas `metadata_json` em qual tabela?
Não há tabela de metadados do schema.

**Solução:** Criar uma tabela `_schema_migrations(id TEXT PRIMARY KEY,
applied_at DATETIME)` mínima, ou usar um registro em uma tabela
`_meta` existente (se houver). Verificar o mecanismo existente no
`init_db()` atual e seguir o padrão.

---

### O3: `POST /api/knowledge-items/collect` — parâmetros não especificados

**Problema:** O endpoint de coleta manual (§10.1) não diz se coleta
para um jogo específico, todos os jogos, ou um escopo.

**Solução:** `POST /api/knowledge-items/collect` com body opcional
`{"game_id": int}` (se omitido, coleta para todos os jogos com
gameplay). Retorna `{"items_collected": int}`.

---

### O4: Idioma dos nomes canônicos do Wikidata

**Problema:** §5.3 diz "nomes canônicos do Wikidata" mas não especifica
idioma. Wikidata retorna labels no idioma requisitado. "Rockstar Games"
é igual em EN e PT, mas "Capcom" vs "CAPCOM" pode variar.

**Solução:** Sempre buscar labels em inglês (`en`) no Wikidata. Nomes
de empresas de games são predominantemente em inglês. Para
consistência de matching, um idioma canônico é melhor que
multi-idioma.

---

### O5: Dimensão do embedding assumida (768) é model-specific

**Problema:** §15.2 assume `vector(768)` para pgvector. Mas
`nomic-embed-text` produz 768 dims. Se o modelo mudar, a dimensão
muda. O `model` column nas tabelas de embedding permite detectar, mas
a migração pgvector assume uma dimensão fixa.

**Solução:** Documentar que a dimensão é determinada pelo modelo
especificado em `GPCG_EMBEDDING_MODEL` (config existente). A migração
pgvector deve ler a dimensão do modelo atual, não hardcode 768. Se
houver embeddings de modelos diferentes, re-embed tudo com o modelo
atual antes da migração.

---

### O6: Mecanismo de re-embedding quando modelo muda

**Problema:** Se `nomic-embed-text` for trocado por outro modelo,
embeddings antigos são incompatíveis com queries do novo modelo. O
`model` column permite detectar, mas não há mecanismo de re-embedding.

**Solução:** Job de re-embedding: iterar sobre embeddings onde
`model != current_model`, re-gerar, atualizar. Pode ser um job
`knowledge_index` (capability existente) estendido. Documentar como
operação de manutenção, não como fluxo automático.

---

### O7: Algoritmo de dedup subespecificado para variantes de subtitle

**Problema:** §4.3 menciona "remover sufixos de plataforma ('PS2',
'PC', 'Scholarship Edition' se for subtitle distinto — heurística
simples)". "Scholarship Edition" não é plataforma — é subtitle. A
heurística não define quais sufixos remover: "Game of the Year
Edition", "Director's Cut", "Remastered", "Deluxe Edition", "Definitive
Edition"?

**Solução:** Lista explícita de sufixos a remover na normalização:
PS2, PS3, PS4, PS5, PC, Xbox, Xbox 360, Xbox One, Xbox Series X/S,
Switch, Wii, Wii U, N64, GameCube, PSP, PS Vita, 3DS, Mobile, iOS,
Android. **Não remover** subtitles ("Scholarship Edition", "Director's
Cut") — estes são aliases, não sufixos de plataforma. O slug de
"Bully: Scholarship Edition" é "bully-scholarship-edition", distinto
de "bully". A dedup por alias cuida do resto (se "Bully Scholarship
Edition" for adicionado como alias de "Bully", a dedup funciona).

---

## Seção 4 — Aprovação Final da Arquitetura

### Veredito: Aprovada com correções obrigatórias

A arquitetura V2 é **sólida em sua filosofia e decisões fundamentais**.
As 14 decisões (D1-D14) são coerentes, bem justificadas, e alinhadas
com o `EDITORIAL_EVALUATION.md`. A estratégia de evolução (strings →
tabelas, embeddings separados, feature flags) preserva evolução
futura sem over-engineering.

**No entanto, a arquitetura NÃO pode ser implementada como está.**
Os 12 problemas obrigatórios (P1-P12) devem ser corrigidos no
blueprint antes do início da implementação. Destes, três são
críticos:

1. **P1 (cross-game no worker)** — sem correção, a Fase 4 é
   impossível. É o problema mais sério: o blueprint descreve uma
   feature que a arquitetura de sync atual não suporta.

2. **P2 (resolver integration)** — sem correção, a tabela
   `game_aliases` é criada mas seu consumidor principal não a usa.

3. **P3 (denormalização sem sync)** — sem correção, items coletados
   antes do enriquecimento são invisíveis em queries por escopo.

Os demais problemas obrigatórios (P4-P12) são correções de
especificidade (campos mortos, race conditions, ambiguidades) que
causarão bugs sutis ou confusão de implementação se não resolvidos.

As 18 melhorias altamente recomendadas (R1-R18) devem ser
incorporadas ao blueprint como esclarecimentos ou pequenas mudanças
de schema. Nenhuma exige repensar a arquitetura — são refinamentos.

As 7 melhorias opcionais (O1-O7) podem ser incorporadas durante a
implementação, conforme o custo.

### Resumo de mudanças no blueprint

| Categoria | Quantidade | Impacto no schema |
|-----------|-----------|-------------------|
| Problemas obrigatórios | 12 | P3: -2 colunas em KI; P4: -1 valor de enum; P5: -1 coluna em KI; P9: mudar unique index; P11: modificar local_db_sync |
| Melhorias recomendadas | 18 | R2: +1 coluna em videos; R5: coleta global; R11: -1 endpoint; R12: -1 coluna em game_aliases |
| Melhorias opcionais | 7 | Esclarecimentos |

**Impacto líquido no schema:** -4 colunas, -1 valor de enum, -1
endpoint, +1 coluna (`Video.fact_id`), mudança em 1 unique index.
O schema fica **menor e mais robusto** após as correções.

### Próximo passo

Aplicar as correções P1-P12 e R1-R18 ao `ARCHITECTURE_V2.md`,
produzindo a versão 2.1. Após essa atualização, a arquitetura está
pronta para implementação começar pela Fase 1.

---

**Fim da revisão.**
