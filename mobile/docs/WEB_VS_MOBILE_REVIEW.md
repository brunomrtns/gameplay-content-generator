# GPCG Mobile — Review: Web vs Mobile

Comparação exaustiva entre a interface web (`frontend/src/pages/*`) e o app mobile (`GpcgMobile/src/screens/*`). Cada item marca a severidade e o que está faltando/errado/incompleto.

Severidades:
- **CRITICAL** — funcionalidade principal ausente ou quebrada
- **MAJOR** — funcionalidade importante ausente ou UX quebrada
- **MINOR** — detalhe visual ou comportamento menor

---

## 1. Dashboard (`DashboardScreen.tsx` vs `dashboard.tsx`)

- [MAJOR] **WorkerStatusCard ausente** — A web mostra um card com status do worker (online/busy/offline, GPU/CPU/RAM, atividade atual, capabilities, heartbeat). O mobile não tem nada disso.
- [MAJOR] **Card "Atalhos" ausente** — A web tem um card "Atalhos" com botões rápidos para "Criar conteúdo", "Configurar produção", "Ver vídeos". O mobile não tem.
- [MAJOR] **Botão "Configurar automação" ausente** — O card de automação na web tem um botão "Configurar automação" que navega para `/automation`. O mobile não tem esse link.
- [MINOR] **Link "Ver todos →" ausente** — A web tem um link "Ver todos →" ao lado do título "Vídeos produzidos" que navega para a tela de vídeos. O mobile não tem.
- [MAJOR] **Player de vídeo sem scroll** — O modal do player no Dashboard tem o mesmo problema que o VideosScreen tinha: `videoPlayer` com `flex: 1` empurra a info para fora da tela. Não foi corrigido aqui (só foi corrigido no VideosScreen).
- [MINOR] **Estatísticas sem ícones** — A web mostra ícones (Film, Loader2, Video, Send) ao lado de cada stat card. O mobile só mostra texto.
- [MINOR] **Status do vídeo incompleto** — O mobile não tem `ready`, `qa_passed`, `qa_failed` no `VIDEO_STATUS_CONFIG`. Só tem `pending_approval`, `published`, `publish_failed`, `draft`, `failed`, `rendering`, `pending`.
- [MINOR] **Sem dimensões do vídeo** — A web mostra `{width}×{height}` no modal. O mobile não mostra.
- [MINOR] **Sem link YouTube no modal** — A web tem link "Abrir no YouTube" quando publicado. O mobile não tem.
- [MINOR] **Sem botão "Iniciar automação" no empty state** — A web tem um botão "Iniciar automação" dentro do empty state de vídeos. O mobile não tem.

---

## 2. Conteúdo (`ContentScreen.tsx` vs `content.tsx`)

- [CRITICAL] **Mapping Timeline ausente** — A web tem um componente expansível "Ver análise do mapeamento" que mostra timeline visual colorida por tipo de evento (COMBAT, VEHICLE, IDLE, CUTSCENE, etc.) + lista de eventos com timestamp, tipo, descrição, transcript e interesting score. O mobile não tem nada disso.
- [MAJOR] **Botão "Definir jogo" ausente quando não há jogo** — A web mostra um botão "Definir jogo" (com borda tracejada) quando a gameplay não tem jogo associado. O mobile só mostra o jogo quando ele existe; não tem como definir um jogo para uma gameplay sem jogo.
- [MAJOR] **Badge "Estacionada" ausente no status** — A web tem `needs_review` e `duplicate` no `STATUS_CONFIG`. O mobile não tem esses dois status.
- [MAJOR] **Barra de progresso durante processamento ausente** — A web mostra uma barra de progresso animada quando a gameplay está em processamento ou mapeamento. O mobile não tem.
- [MAJOR] **Sem indicador visual de processamento** — A web mostra ícones diferentes (Cpu animado para mapeamento, Loader2 girando para processamento, AlertCircle para erro, CheckCircle para pronto). O mobile só mostra badges de texto.
- [MINOR] **Sem botão "Solicitar mapeamento" com explicação** — A web mostra o botão + texto "Envia para o worker analisar (VLM + ASR)". O mobile só tem o botão sem explicação.
- [MINOR] **Game badge não é clicável visualmente** — Na web, o game badge é clicável e tem ícone de lápis indicando que pode ser editado. No mobile é um Badge simples sem indicação de edição.
- [MINOR] **Sem "Como funciona" banner** — A web não tem isso no content.tsx (só no kids), mas o mobile também não tem. N/A.

---

## 3. Automação (`AutomationScreen.tsx` vs `automation.tsx`)

- [CRITICAL] **DomainSection ausente** — A web tem uma seção "Domínio do Canal" dentro da página de automação (com seletor de domínio, confirmação destrutiva, warning, resumo da limpeza). O mobile só tem isso no MoreScreen. Pode ser OK por design, mas a web tem nas duas.
- [MAJOR] **isKidsDomain hardcoded como `false`** — Linha 255: `const isKidsDomain = false; // TODO: detect from dashboard`. Isso significa que no domínio Kids, as seções "Conteúdo" (gameplay) e "Fila Kids" não aparecem corretamente. A seção de gameplay aparece mesmo em Kids, e a fila Kids nunca aparece.
- [MAJOR] **Preview ao vivo (visual) ausente** — A web tem um painel lateral com preview ao vivo da legenda (SubtitlePreview) e da capa (ThumbnailPreview) com toggle Vídeo/Capa. O mobile só tem um resumo textual expansível.
- [MAJOR] **Sem painel "Resumo" lateral** — A web tem um resumo lateral com formato, cena, voz, estilo, transição, YouTube, fila. O mobile tem isso no card expansível, mas sem o botão Salvar/Pausar/Iniciar no painel.
- [MINOR] **Sem upload de imagem de capa no preview** — A web permite ver a thumbnail em tempo real. O mobile só faz upload mas não preview.

---

## 4. Jobs (`JobsScreen.tsx` vs `jobs.tsx`)

- [MINOR] **Filtro "Tentando novamente" (retrying) ausente** — A web tem `retrying` no `JOB_STATUS_CONFIG`. O mobile não tem filtro nem label para `retrying`.
- [MINOR] **Filtro "Cancelado" (cancelled) ausente** — A web tem `cancelled` no `JOB_STATUS_CONFIG`. O mobile não tem filtro nem label para `cancelled`.
- [MINOR] **Stage labels incompletos** — O mobile não tem `confirm_download` ("Confirmando download"), `story_finding` ("Story Finder"), `humanization` ("Humanização") no `STAGE_LABELS`. A web também não tem todos, mas o mobile tem `presentation` que a web não tem.
- [MINOR] **Sem ícone animado para running** — A web mostra o ícone do status girando (animate-spin) quando running. O mobile não anima o ícone.
- [MINOR] **Sem botão "Solicitar mapeamentos na aba Conteúdo ou inicie a automação"** — O empty state da web sugere ações. O mobile só diz "Nenhum job".

---

## 5. Ideias (`IdeasScreen.tsx` vs `ideas.tsx`)

- [MAJOR] **Loading state não mostra spinner centralizado** — A web mostra um spinner grande centralizado quando `loading === true`. O mobile mostra spinner no `ListEmptyComponent` mas o header/stats/fila não mostram loading. Pode confundir (parece que não tem dados quando na verdade está carregando).
- [MINOR] **Sem "via {source_name}"** — A web mostra "via {source_name}" ao lado do score. O mobile pode não mostrar isso nos cards.
- [MINOR] **Sem link "Ver fonte →"** — A web tem um link clicável para a `source_url` da ideia. O mobile não tem (ou não é clicável de forma útil).
- [MINOR] **Sem polling do current job a cada 10s** — A web faz poll do `currentJob` a cada 10s quando há um job rodando. O mobile precisa verificar se faz isso.
- [MINOR] **Filtro "Manuais" pode não funcionar** — A web tem um filtro especial "manual" que filtra por `source_type === 'manual'` (não é um `item_type`). Verificar se o mobile faz o mesmo.

---

## 6. Vídeos (`VideosScreen.tsx` vs `videos.tsx`)

- [CRITICAL] **Edição de metadados ausente** — A web tem modo de edição no modal: botão "Editar" que permite alterar título, descrição e tags do vídeo antes de publicar. O mobile não tem edição de metadados.
- [CRITICAL] **Publicação com overrides ausente** — A web permite publicar no YouTube enviando os metadados editados como overrides (`handlePublishFromModal`). O mobile só publica sem overrides.
- [CRITICAL] **Metadados rich ausentes no modal** — A web mostra no modal:
  - Ideia (KnowledgeItem) com título, tags, fonte
  - Jogo (game_name)
  - Plano editorial (video_type, humor, narrative_beats)
  - Script critic (verdict, score)
  - Clips usados (lista com source, start/end time)
  - Roteiro (script_final) expansível
  O mobile só mostra título, descrição, tags, duração e data.
- [MAJOR] **Confirmação de delete em 2 passos ausente** — A web tem um fluxo de delete em 2 etapas: (1) confirmar delete, (2) perguntar se quer liberar trechos e ideia para reuso. O mobile usa `Alert.alert` simples sem a opção de liberar trechos.
- [MAJOR] **Confirmação de regeneração ausente** — A web tem um modal de confirmação detalhado para regenerar vídeo (explica que a ideia volta para o final da fila, trechos são liberados, vídeo atual não é deletado). O mobile usa `Alert.alert` simples.
- [MAJOR] **Botão "Regenerar" só aparece se `knowledge_item` existe** — A web só mostra o botão regenerar se `v.knowledge_item` existe. O mobile sempre mostra "Regenerar" no card.
- [MAJOR] **Sem botão "Publicar no YouTube" no modal** — A web tem um botão grande vermelho "Publicar no YouTube" no modal (quando `canPublishModal`). O mobile só tem "Publicar nas redes sociais" (que abre o share sheet).
- [MAJOR] **Sem indicador de status de publicação no modal** — A web mostra "Publicado no YouTube" (verde) ou "Publicação falhou" (vermelho) no modal. O mobile não mostra.
- [MINOR] **Sem dimensões do vídeo** — A web mostra `{width}×{height}` no card e no modal. O mobile não mostra.
- [MINOR] **Sem badge YouTube no card** — A web mostra um badge "YouTube" no canto superior esquerdo do thumbnail quando publicado. O mobile não tem isso no VideosScreen (só no Dashboard).
- [MINOR] **Sem ícone de play overlay** — A web mostra um overlay com ícone de play ao passar o mouse no thumbnail. No mobile não faz sentido (hover), mas um indicador visual de que é clicável seria bom.

---

## 7. Kids (`KidsScreen.tsx` vs `kids.tsx` + `kids-ideas.tsx`)

- [CRITICAL] **Tela Kids extremamente simplificada** — O mobile tem apenas 145 linhas vs 973 (kids.tsx) + 713 (kids-ideas.tsx) = 1686 linhas na web. Faltam muitas funcionalidades.
- [CRITICAL] **Upload sem tags e descrição** — A web permite adicionar tags e descrição antes do upload (campos `tagsInput` e `descInput`). O mobile faz upload sem metadados.
- [CRITICAL] **Sem edição de tags/descrição das mídias** — A web tem modo de edição inline em cada card de mídia (editar tags e descrição). O mobile não tem.
- [CRITICAL] **Sem Mapping Timeline Kids** — A web tem `KidsMappingTimeline` expansível com timeline visual colorida por tipo de evento (VISUAL_ACTION, NARRATION, ANIMATION, etc.). O mobile não tem.
- [CRITICAL] **Sem "Solicitar mapeamento" para vídeos sem eventos** — A web tem botão "Solicitar mapeamento" quando vídeo está ready mas sem eventos. O mobile não tem.
- [CRITICAL] **Sem filtros de mídia** — A web tem filtros: Todos / Imagens / Vídeos / Só prontos. O mobile não tem filtros.
- [CRITICAL] **Sem toggle de visibilidade (pública/privada)** — A web tem botão para tornar mídia pública/privada. O mobile não tem.
- [CRITICAL] **Sem indicadores visuais de processamento** — A web mostra ícones diferentes (Cpu animado, Loader2, AlertCircle, CheckCircle, ImageIcon, VideoIcon). O mobile só mostra texto.
- [CRITICAL] **Sem barra de progresso** — A web mostra barra animada durante processamento/mapeamento. O mobile não tem.
- [CRITICAL] **Sem badge de eventos** — A web mostra "{event_count} eventos" para vídeos mapeados. O mobile não tem.
- [CRITICAL] **Sem tags visuais nos cards** — A web mostra tags (com ícone Tag) ou "Geral — sem tags (fallback)". O mobile não mostra tags.
- [CRITICAL] **Sem "Como funciona" banner** — A web tem um banner explicativo no topo. O mobile não tem.
- [CRITICAL] **Sem banner de processamento** — A web mostra "{count} mídia(s) em processamento". O mobile não tem.
- [CRITICAL] **Sem mídias públicas da comunidade** — A web separa e mostra mídias públicas. O mobile não filtra nem mostra.
- [CRITICAL] **Configuração do Canal Kids ausente** — A web tem `ChannelConfigSection` com:
  - Perfil editorial completo (descrição, nicho, público-alvo, tom, narrativa, objetivos)
  - Faixa etária alvo (3-6, 6-10, 7-10, todas)
  - Duração alvo (slider 15-90s)
  - Categorias de interesse (14 categorias toggle)
  - Onboarding alert quando perfil está vazio
  O mobile só lista tópicos e mostra "Calendário Sazonal — Disponível no servidor".
- [CRITICAL] **Tela de Ideias Kids completamente ausente** — A web tem `kids-ideas.tsx` (713 linhas) com:
  - Lista de ideias kids
  - Fila de produção kids
  - Descoberta de ideias (com categorias e count)
  - Avaliação de ideias (scoring)
  - Reconciliador
  - Criação manual de ideia (título, descrição, categoria)
  - Reordenação da fila (drag-and-drop + up/down)
  - Polling de jobs de descoberta/scoring
  - Filtros por status
  - Status labels (discovered, evaluated, queued, converted, rejected, expired)
  - Source labels (IA, Biblioteca, Sazonal, Manual)
  O mobile não tem NENHUMA dessas funcionalidades.

---

## 8. Admin (`AdminScreen.tsx` vs `admin.tsx`)

- [MINOR] **Sem info banner** — A web tem um card explicativo sobre BI Identity SSO. O mobile não tem.
- [MINOR] **Sem avatar com inicial** — A web mostra um círculo com a inicial do nome. O mobile não tem.
- [MINOR] **Sem badge "você"** — A web mostra um badge "você" ao lado do próprio usuário. O mobile não tem.
- [MINOR] **Sem proteção contra auto-delete** — A web impede que o usuário delete a si mesmo. O mobile não tem essa proteção.
- [MINOR] **Sem proteção contra auto-desativar** — A web desabilita o botão "Desativar" para o próprio usuário. O mobile não tem.

---

## 9. More / Navegação (`MoreScreen.tsx`)

- [MAJOR] **Sem YouTube card/status** — A web mostra status do YouTube no Dashboard. O mobile tem no Dashboard mas não no More. Pode ser OK, mas o usuário pode querer gerenciar YouTube no More.
- [MINOR] **Sem info sobre BI Identity** — A web menciona BI Identity no Admin. O mobile não tem essa info em lugar nenhum.

---

## 10. Issues transversais (afetam múltiplas telas)

- [CRITICAL] **Botão voltar do Android não fecha modais em várias telas** — Foi corrigido em VideosScreen, MoreScreen, ContentScreen, DashboardScreen e LoginScreen, mas ainda falta verificar:
  - IdeasScreen (4 modais: queue-add, edit-queue-game, manual-idea, collection-focus)
  - AutomationScreen (PickerField modal — não é um `Modal` nativo, é uma View absoluta; pode não responder ao back)
- [MAJOR] **Loading state inconsistente** — Algumas telas mostram spinner no empty state (Videos, Ideas), outras mostram spinner centralizado (Automation), outras não mostram nada (Kids). Padronizar.
- [MAJOR] **isKidsDomain detectado de formas diferentes** —
  - DashboardScreen: `dash?.channel_domain === 'kids'`
  - AutomationScreen: `false` (hardcoded!)
  - MoreScreen: `user?.channel_domain || domainData?.current`
  Padronizar e usar o mesmo source de truth.
- [MAJOR] **Sem pull-to-refresh em algumas telas** — Videos, Content, Jobs, Dashboard têm. Kids NÃO tem pull-to-refresh na aba de mídias (só na config, que não tem dados para refresh).
- [MINOR] **NativeEventEmitter warnings** — Logs mostram warnings sobre `NativeEventEmitter` chamado sem `addListener`/`removeListeners`. Provavelmente de uma dependência nativa. Não é crash mas afeta qualidade.
- [MINOR] **Sem indicadores de erro global** — A web tem banners de erro inline em várias telas. O mobile usa Toast (que desaparece). Considere adicionar banners de erro persistentes onde fizer sentido.

---

## 11. API / Endpoints — Verificar compatibilidade

- [MAJOR] **`jobsApi.list(filter)` — verificar se passa `status` como query param** — A web faz `api.listJobs()` sem filtro e filtra client-side. O mobile passa `filter` para `jobsApi.list(filter || undefined)`. Verificar se o backend aceita `?status=queued` ou se ignora e retorna tudo.
- [MAJOR] **`videosApi.list(search)` — verificar se passa `search` como query param** — A web faz `api.listVideos()` e filtra client-side. O mobile passa `search` para `videosApi.list(search || undefined)`. Verificar se o backend aceita `?search=...`.
- [MAJOR] **`knowledgeApi.list(params)` — verificar response shape** — O mobile espera `{ items: [...] }`. A web faz `itemsRes.items`. Verificar se o backend retorna isso ou se retorna array direto.
- [MAJOR] **`knowledgeApi.stats()` — verificar response shape** — O mobile espera `{ total, fresh, by_type, by_status, by_source }`. Verificar se o backend retorna isso.
- [MAJOR] **`idea-queue` response — verificar shape** — O mobile espera `{ queue, items }`. A web faz `queueRes.items` e `queueRes.queue`. Verificar.
- [MAJOR] **`gameplay-availability` response — verificar shape** — O mobile espera `{ games: [...] }`. A web faz `availRes.games`. Verificar.
- [MAJOR] **`current-job` response — verificar shape** — O mobile espera `{ job: {...} }`. A web faz `jobRes.job`. Verificar.
- [MAJOR] **`collection-focus` response — verificar shape** — O mobile espera `{ collection_focus: {...} }`. A web faz `focusRes.collection_focus`. Verificar.
- [MAJOR] **Progress calculation** — `Math.round(job.progress * 100)` assume 0-1. Verificar se o backend retorna 0-1 ou 0-100. Se for 0-100, vai mostrar 5000%.
- [MAJOR] **`videosApi.delete(videoId, releaseClips)` — verificar se passa `release_clips`** — A web envia `release_clips` como parâmetro. O mobile pode não estar enviando isso.

---

## 12. Visual / UX geral

- [MAJOR] **Cards de Ideias podem estar oversized** — O usuário reportou que "os cards ali estão gigantes com o número em bem pequeno dentro". Verificar se o `IdeaCard` está com padding/height excessivo.
- [MAJOR] **Score pode estar ilegível** — O usuário reportou score "bem pequeno dentro" de cards gigantes. Verificar tamanho da fonte do score.
- [MINOR] **Sem animações de transição** — A web usa `animate-fade-in` em várias páginas. O mobile não tem animações de entrada.
- [MINOR] **Sem hover states** — N/A no mobile (touch), mas feedback tátil (opacity change) já está implementado via `activeOpacity`.

---

## Resumo por severidade

| Severidade | Count |
|------------|-------|
| CRITICAL   | 19    |
| MAJOR      | 28    |
| MINOR      | 22    |
| **Total**  | **69** |

## Prioridades sugeridas

### P0 (corrigir antes do próximo build)
1. Botão voltar fecha TODOS os modais (IdeasScreen, AutomationScreen PickerField)
2. `isKidsDomain` hardcoded no AutomationScreen
3. Dashboard player modal sem scroll (mesmo bug do VideosScreen)
4. Loading state na tela de Ideias (spinner visível)
5. Verificar shapes de API responses (knowledge-items, idea-queue, gameplay-availability, current-job, collection-focus)
6. Verificar cálculo de progress (0-1 vs 0-100)

### P1 (próxima sprint)
1. Tela de Kids completa (upload com tags, edição, mapping timeline, filtros, config do canal)
2. Tela de Ideias Kids completa
3. Edição de metadados de vídeo no modal
4. Metadados rich no modal de vídeo (ideia, jogo, plano editorial, clips, roteiro)
5. WorkerStatusCard no Dashboard
6. Delete de vídeo em 2 passos com opção de liberar trechos
7. Confirmação de regeneração detalhada

### P2 (backlog)
1. Preview ao vivo na Automação (subtitle/thumbnail)
2. DomainSection na Automação (já tem no More)
3. Mapping Timeline no Conteúdo
4. Card "Atalhos" no Dashboard
5. Melhorias visuais menores (ícones, badges, animações)
6. Proteções no Admin (auto-delete, auto-desativar)
