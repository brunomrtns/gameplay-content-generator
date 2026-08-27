# GPCG Mobile — Plano Completo

> Documento de planejamento do app mobile nativo do GPCG
> Data: 2026-08-26
> Stack: React Native CLI 0.87 (sem Expo) + TypeScript

---

## 1. Visão Geral

O GPCG Mobile é o app nativo (Android + iOS) do Gameplay Content Generator. Ele consome a mesma API REST do GPCG que o frontend web consome hoje (`https://brunointegrations.com/gpcg/api/`), mas com UI nativa em React Native.

### Princípios

- **Sem Expo.** React Native CLI puro, build nativo via Gradle (Android) e CocoaPods/Xcode (iOS).
- **Mesma API.** Zero mudanças no backend. O app consome os mesmos endpoints.
- **Auth via SSO cookie.** O GPCG usa BI Identity SSO com cookie `bi_auth`. No mobile isso precisa de um WebView pra fazer o fluxo de login e capturar o cookie, ou um fluxo OAuth nativo (ver seção Auth).
- **Um codebase, duas plataformas.** Android é prioridade (você tem Android). iOS é secundário mas deve compilar.
- **Dark theme.** O GPCG web é dark (bg `#07070a`, accent teal `hsl(172,72%,44%)`). O app segue o mesmo.

---

## 2. Stack Tecnológico

### Core

| Componente | Tecnologia | Versão | Porquê |
|---|---|---|---|
| Framework | React Native | 0.87.x | Mais recente estável (ago/2026). Strict TypeScript API default, Metro 0.87, SPM support |
| CLI | @react-native-community/cli | 20.1.x | Compatível com RN 0.87 |
| Linguagem | TypeScript | 5.6+ | Strict mode (RN 0.87 torna default) |
| Bundler | Metro | 0.87 | Vem com RN |
| Node | 22.1+ | LTS | Requerido pelo RN 0.87 |

### Android

| Componente | Versão | Notas |
|---|---|---|
| Android Gradle Plugin | 9.x | Requerido pelo RN 0.87 |
| Kotlin | 2.0+ | Requerido pelo RN 0.87 |
| compileSdk | 35 (Android 15) | Default do RN 0.87 |
| minSdk | 24 (Android 7.0) | Default do RN 0.87 |
| targetSdk | 35 | Default |
| NDK | 27.x | Default |

### iOS

| Componente | Versão | Notas |
|---|---|---|
| Xcode | 16+ | Requerido pelo RN 0.87 |
| Swift | 5.x | |
| iOS minimum | 15.1 | Default do RN 0.87 |
| CocoaPods | 1.15+ | |
| Swift Package Manager | experimental | RN 0.87 adiciona suporte experimental |

### Libs recomendadas

| Categoria | Lib | Versão | Porquê |
|---|---|---|---|
| Navegação | @react-navigation/native + native-stack | 7.x | Padrão da comunidade RN CLI |
| HTTP | axios | 1.7.x | Interceptors pra auth, melhor que fetch pra cookie handling |
| Cookies | @react-native-community/cookies ou react-native-webview | latest | Capturar cookie SSO do BI Identity |
| Estado | @tanstack/react-query | 5.x | Mesmo papel do usePoll no web — polling, cache, refetch |
| Ícones | react-native-vector-icons | 10.x + @expo/vector-icons types | Mesmo papel do lucide-react no web |
| Toast | react-native-toast-message | 2.x | Equivalente ao sonner |
| Storage | @react-native-async-storage/async-storage | 2.x | Persistir domain, token, prefs |
| File upload | react-native-document-picker | 9.x | Picker nativo de arquivos (gameplay, voz, imagem) |
| Upload progress | XMLHttpRequest nativo do RN | — | Progress bar em uploads grandes |
| Video player | react-native-video | 6.x | Player nativo pra preview de vídeos |
| Image | FastImage (opcional) | 8.x | Cache de imagens se necessário |
| Gestures | react-native-gesture-handler + reanimated | 3.x | Drag-reorder da idea queue |
| Haptics | react-native-haptics (opcional) | 2.x | Feedback tátil em ações |
| Safe Area | react-native-safe-area-context | 5.x | Notch/status bar handling |
| SVG | react-native-svg | 15.x | Se precisar de SVGs custom |
| Config | react-native-config | 1.x | Env vars no build (API URL, etc) |

### Libs que NÃO usar

| Lib | Porquê não |
|---|---|
| Expo | Você não quer |
| redux/mobx | Tanstack Query + hooks locais bastam |
| native-base/paper | UI custom, não design system de terceiro |

---

## 3. Mapeamento da Interface Atual (Web → Mobile)

### 3.0 Regras de Negócio Críticas (NÃO ESQUECER)

Estas regras foram identificadas no review detalhado do código e DEVEM ser respeitadas no mobile:

1. **Domain Switch é destrutivo.** Trocar de games → kids (ou outro domínio) apaga TODO o estado de produção: jobs, vídeos não publicados, planos, fatos, documentos, knowledge items, gameplays. Vídeos já publicados no YouTube NÃO são removidos. A conexão YouTube não é alterada. Exige confirmação dupla com modal mostrando resumo do que foi deletado.

2. **Gameplay delete exige confirmação dupla.** Dois `confirm()` consecutivos avisando que TODOS os clips, eventos e arquivos físicos serão removidos. No mobile: dois Alert.alert consecutivos.

3. **Gameplay tem dois status simultâneos:**
   - `ingestion_status`: discovered → probing → ready → error
   - `processing_status`: uploading → uploaded → waiting_worker → downloading → downloaded → mapping → ready/mapped
   - Só pode solicitar mapeamento quando `ingestion_status=ready` E `processing_status=uploaded` (ou vazio)
   - Só mostra timeline de eventos quando `processing_status=ready` ou `mapped`

4. **Gameplay tem 3 controles:**
   - **Enabled** (power on/off): estaciona/disponibiliza pra automação
   - **Visibility** (eye): privada/pública
   - **Delete** (trash): remove tudo

5. **Gameplays públicas da comunidade** aparecem em seção separada, read-only, com badge "Pública". Outros usuários podem ver mas não editar.

6. **Mapping Timeline** — visualização expandível dos eventos VLM detectados:
   - Timeline bar colorido por tipo de evento (COMBAT=red, VEHICLE=blue, IDLE=gray, CUTSCENE=purple, MENU=yellow, EXPLORATION=green, DIALOGUE=cyan)
   - Lista de eventos com timestamp, tipo, descrição, transcript, interesting_score (★ se ≥0.7)
   - Carregamento lazy (só carrega quando usuário expande)

7. **Queue Mode (Games):**
   - `automatic`: fila + decisão editorial automática
   - `manual`: só produz da fila do usuário; quando esvazia, produção para
   - `auto_fill_queue`: reconciliador preenche fila automaticamente quando vazia
   - `max_queue_size`: limite do auto-preenchimento (1-50)

8. **Queue Mode (Kids):**
   - `manual`: usuário adiciona ideias manualmente
   - `auto`: sistema preenche com melhores ideias avaliadas
   - `kids_auto_fill_queue`: default true
   - `kids_max_queue_size`: default 10

9. **Reuse Policy:** `max_clip_uses` controla quantas vezes uma região de gameplay aparece:
   - 1 = cada trecho em apenas 1 vídeo (padrão)
   - 2 = permite uma reutilização
   - 3 = permite duas reutilizações
   - 0 = ilimitado

10. **Fallback Policy:** `fallback_policy` controla uso de gameplays públicas:
    - `stop` = só minhas gameplays
    - `allow_public` = permite gameplays públicas como fallback quando as minhas se esgotarem

11. **Video Status Flow:**
    - `pending_approval` ou `publish_failed` → pode publicar manualmente
    - `published` + `youtube_url` → tem link externo pro YouTube
    - QA score mostrado em todas as thumbnails (verde se passed, vermelho se failed)

12. **Channel Profile** tem 7 campos:
    - `channel_description` (textarea)
    - `niche` (input)
    - `target_audience` (input)
    - `tone_of_voice` (input)
    - `narrative_style` (input)
    - `content_goals` (textarea)
    - `special_rules` (textarea — regras especiais pra IA)

13. **Creative Styles (8 opções):** humor, absurd, sarcastic, storytelling, curiosity, nostalgia, dark_humor, high_energy

14. **YouTube Categories (5 opções):** Games(20), Pessoas e blogs(22), Entretenimento(24), Comédia(23), Educação(27)

15. **YouTube Privacy (3 opções):** public, unlisted (default), private

16. **Video Formats (4 opções):** 9:16, 16:9, 1:1, 4:5

17. **Subtitle Fonts (4 opções):** Padrão, DejaVuSans-Bold, DejaVuSans, LiberationSans-Bold

18. **Subtitle Colors (6 opções):** Padrão, white, yellow, cyan, red, lime

19. **Subtitle Positions (4 opções):** Padrão, bottom, middle, top

20. **Subtitle Cases (4 opções):** Padrão, upper, lower, none

21. **Transition Types (23 opções):** fade, fadeblack, fadewhite, wipeleft/right, slideleft/right/up/down, smoothleft/right/up/down, circleopen/close, dissolve, zoomin, hblur, diagtl/tr/bl/br

22. **Box Colors (6 opções):** Padrão, black@0.7/0.5/0.3, white@0.7/0.5

23. **Presentation Layer** (seção 6b da automação):
    - Toggle enable/disable
    - Thumbnail mode: `auto` (sistema escolhe frame) ou `fixed` (imagem enviada)
    - Thumbnail text: source (title/topic/custom), custom text, position, color, size, outline
    - Opening: mode (same_as_thumbnail/auto/fixed), duration, image
    - Preview toggle Video/Capa (segmented control) — só aparece quando presentation enabled

24. **Automation config cleanup:** ao salvar, valores vazios/zero são removidos exceto booleanos (false é válido).

25. **Domain config é dinâmico:** vem do servidor via `api.listDomains()`. Domínios não implementados aparecem como "em breve" e são disabled.

26. **Polling intervals específicos:**
    - Dashboard: 10s
    - Jobs: 5s
    - Gameplays (content): 5s
    - Automation config: 30s
    - Games list: 15s
    - Voices: 15s
    - Workers: 10s (WorkerStatusCard)

27. **Inbox scan:** botão "Escanear inbox" descobre gravações automaticamente da pasta inbox no HD do worker. Retorna count de arquivos encontrados.

28. **Domain labels:** games=Games, kids=Kids, movies=Filmes & Séries, conspiracy=Mistérios & Teorias, technology=Tecnologia

### 3.1 Navegação Principal

**Web:** Sidebar/topbar com NavLink (react-router-dom). Itens vindos do `domain-config.tsx`:
- Dashboard, Conteúdo, Ideias, Jobs, Automação, Vídeos
- Kids: Dashboard, Tópicos, Ideias, Jobs, Automação, Vídeos
- Admin (se is_admin)

**Mobile:** Bottom Tab Navigator (5 tabs) + Stack Navigator pra sub-páginas.

```
BottomTab
├── Dashboard (home)
├── Conteúdo (gameplay/kids topics)
├── Jobs
├── Vídeos
└── Mais (menu: Automação, Ideias, Admin, Perfil, Logout)
```

O domínio (games/kids) é detectado pelo `api.getDashboard().channel_domain` e ajusta labels/cores.

### 3.2 Páginas Mapeadas

#### Login (`login.tsx` → `LoginScreen`)

**Web:** Botão "Entrar com BI Identity" que redireciona pra `/id/login?redirect=/gpcg/dashboard`.

**Mobile:** Tela inicial com botão "Entrar". Abre WebView pro fluxo SSO do BI Identity. Após login, captura o cookie `bi_auth` e `bi_refresh`, armazena no AsyncStorage, e navega pro app.

**Desafio mobile:** O SSO é cookie-based com `credentials: "include"`. No RN, `fetch` não compartilha cookies com WebView por padrão. Opções:
1. **WebView login + cookie extraction** — abrir WebView, interceptar cookies após login, usar no axios
2. **Token-based auth** — adicionar endpoint `/api/auth/token` no backend que troca cookie por JWT (mudança mínima no backend)
3. **Cookie jar nativo** — usar `@react-native-community/cookies` pra compartilhar cookies entre WebView e axios

**Recomendação:** Opção 1 (WebView + cookie extraction) pra não mexer no backend.

**APIs:** `api.getMe()`, `api.ssoRedirect()`

---

#### Dashboard (`dashboard.tsx` → `DashboardScreen`)

**Web (453 linhas):**
- Stats cards: total gameplays, vídeos, jobs running, automação status
- Worker status card (GPU/CPU/RAM, online/offline, atividade atual)
- Automação toggle (start/pause) com botão
- YouTube connection status + botão conectar
- Últimos vídeos com thumbnail, status badge, botão publicar
- Video player modal (abre vídeo em overlay)
- Polling a cada 10s (`usePoll`)

**Mobile:**
- ScrollView com cards empilhados
- Stats em grid 2x2
- Worker status card (mesma info, layout responsivo)
- Toggle automação (Switch nativo)
- YouTube status (badge + botão)
- Lista de últimos vídeos (FlatList horizontal ou vertical)
- Video player: modal fullscreen com `react-native-video`
- Pull-to-refresh
- Polling via Tanstack Query (refetchInterval: 10000)

**APIs:** `api.getDashboard()`, `api.startAutomation()`, `api.pauseAutomation()`, `api.youtubeConnect()`, `api.youtubeStatus()`, `api.publishVideo()`, `api.videoUrl()`, `api.thumbUrl()`

**Componentes nativos:** Switch, FlatList, Modal, Video, RefreshControl

---

#### Conteúdo (`content.tsx` → `ContentScreen`)

**Web (798 linhas):** Duas tabs:
1. **Mídia:** Upload de gameplay (drag-drop + file input), lista de gameplays com status badge (discovered/probing/ready/error), processing status (uploading→mapped→ready), botão "Solicitar mapeamento", busca de jogo (GameSearchModal com catálogo IGDB), toggle visibility/enabled, delete, upload progress (chunked upload com hash + resume)
2. **Canal:** Perfil do canal (nome, descrição, tags), editar e salvar

**Mobile:**
- Tab navigator (Mídia / Canal)
- Upload de gameplay: botão que abre `DocumentPicker` → upload chunked com progress bar
- Lista de gameplays: FlatList com cards (nome, duração, status badge, thumbnail)
- Game search: modal com SearchInput + FlatList de resultados do catálogo
- Actions: swipe-to-delete ou botão delete, toggle switches
- Upload progress: barra nativa + porcentagem
- Canal: form com TextInput + botão salvar

**APIs:** `api.uploadGameplay()`, `api.listSources()`, `api.getSourceEvents()`, `api.assignGame()`, `api.assignGameByName()`, `api.searchCatalog()`, `api.autocompleteCatalog()`, `api.toggleGameplayVisibility()`, `api.toggleGameplayEnabled()`, `api.deleteSource()`, `api.createMappingJob()`, `api.getChannelProfile()`, `api.updateChannelProfile()`, `api.scanInbox()`

**Componentes nativos:** DocumentPicker, FlatList, Swipeable, ProgressViewIOS/ProgressBarAndroid, Modal, SearchInput

**Upload chunked:** O web faz upload em chunks de 5MB com hash + resume. No mobile, mesmo algoritmo mas usando `DocumentPicker` pra pegar o arquivo e XHR pra progress.

---

#### Ideias (`ideas.tsx` → `IdeasScreen`)

**Web (1393 linhas):** Sistema completo de knowledge items:
- Stats (total, fresh, by_type, by_status, by_source)
- Filtros (tipo, status, fonte, jogo)
- Lista de knowledge items com editorial_score, tags, franchise, developer
- Actions: reject, add to queue, reorder queue (drag-drop)
- Idea queue com drag-reorder
- Trigger content collection
- Create manual idea
- Gameplay availability checker por jogo

**Mobile:**
- Stats em cards no topo
- Filtros: chips horizontais scrolláveis
- Lista: FlatList com cards (título, content preview, score badge, tags)
- Actions: tap pra detalhe, swipe pra reject/add to queue
- Idea queue: FlatList com drag-reorder via `react-native-gesture-handler` + `reanimated`
- Trigger: botão floating action
- Manual idea: modal com form

**APIs:** `api.listKnowledgeItems()`, `api.getKnowledgeItemStats()`, `api.rejectKnowledgeItem()`, `api.triggerContentCollection()`, `api.createManualIdea()`, `api.getIdeaQueue()`, `api.addToIdeaQueue()`, `api.removeFromIdeaQueue()`, `api.reorderIdeaQueue()`, `api.updateIdeaQueueItem()`, `api.getGameplayAvailability()`, `api.getGameplaySourcesForGame()`

**Componentes nativos:** FlatList, DraggableFlatList (reorder), Swipeable, Modal, Chips

---

#### Jobs (`jobs.tsx` → `JobsScreen`)

**Web (201 linhas):**
- Filter tabs (queued/running/completed/failed) com counts
- Lista de jobs com: tipo (mapping/generate_short/curiosity_short), status badge, stage label, progress, worker assignment, timestamps
- Polling a cada 5s

**Mobile:**
- Filter chips no topo (scroll horizontal) com badges de count
- FlatList de job cards
- Cada card: ícone do tipo, status badge, stage atual, progress bar, worker, data
- Pull-to-refresh
- Polling via Tanstack Query (refetchInterval: 5000)

**APIs:** `api.listJobs()`, `api.getJob()`

**Componentes nativos:** FlatList, ProgressView, RefreshControl

---

#### Automação (`automation.tsx` → `AutomationScreen`)

**Web (994 linhas):** A página mais complexa:
- Toggle start/pause automação
- Botão salvar config
- Seções de configuração:
  - **Estilo:** criativo (humor/absurdo/sarcástico/etc), tom, energia
  - **Apresentação:** (Presentation Layer que acabamos de fazer) — toggle enable, thumbnail mode (auto/fixed), upload imagem, título text config (source, custom text, position, color, size, outline), opening config (duration, image mode, narration), preview toggle Video/Capa
  - **Vídeo:** formato (9:16/16:9/1:1/4:5), duração, scene duration
  - **Legendas:** fonte, tamanho, cor, outline, posição, caixa, cantos arredondados
  - **Transições:** tipo (xfade), duração
  - **Voz:** select de voz, upload de voz, delete voz
  - **Música:** mood, volume
  - **YouTube:** upload automático, visibilidade (public/unlisted/private), tags default, descrição default
- Live preview: SubtitlePreview (CSS-based) ou ThumbnailPreview (CSS-based)
- Polling automation config a cada 30s

**Mobile:**
- ScrollView com seções em cards acordeon (expand/collapse)
- Cada seção: form com inputs nativos
- Toggle/Switch nativo pra booleanos
- Select/Picker nativo ou modal picker pra dropdowns
- Upload de voz: DocumentPicker → upload com progress
- Upload de imagem de capa: DocumentPicker → upload
- Preview: 
  - Vídeo: preview CSS-based com View/Text (mesma lógica do SubtitlePreview)
  - Capa: preview CSS-based com View/Text (mesma lógica do ThumbnailPreview)
  - Toggle Video/Capa: segmented control nativo
- Botão salvar: fixed no bottom
- Botão start/pause: fixed no bottom

**APIs:** `api.getAutomation()`, `api.updateAutomation()`, `api.startAutomation()`, `api.pauseAutomation()`, `api.listVoices()`, `api.uploadVoice()`, `api.deleteVoice()`, `api.uploadPresentationImage()`, `api.presentationImageUrl()`, `api.getDashboard()`, `api.listGames()`

**Componentes nativos:** ScrollView, Switch, Picker (modal), TextInput, DocumentPicker, SegmentedControl, ProgressView

---

#### Vídeos (`videos.tsx` → `VideosScreen`)

**Web (816 linhas):**
- Grid de vídeos com thumbnail, título, status badge, duração
- Search input
- Click abre modal com:
  - Video player (HTML5 video)
  - Metadados editáveis (título, descrição, tags)
  - Botão publicar no YouTube
  - Botão deletar (com confirm de release clips)
  - Botão regenerar
  - Link externo pro YouTube se publicado
- Filter por status

**Mobile:**
- FlatList grid (2 colunas) ou lista vertical com thumbnail grande
- Search: TextInput no header
- Tap abre tela de detalhe (não modal — tela full):
  - Video player nativo (`react-native-video`)
  - Form de metadados (TextInput pra título/descrição/tags)
  - Botões: Publicar, Deletar, Regenerar
  - Confirm dialogs nativos (Alert)
  - Link externo: `Linking.openURL(youtubeUrl)`
- Pull-to-refresh

**APIs:** `api.listVideos()`, `api.videoUrl()`, `api.thumbUrl()`, `api.updateVideoMetadata()`, `api.publishVideo()`, `api.deleteVideo()`, `api.regenerateVideo()`

**Componentes nativos:** FlatList, Video, TextInput, Alert, Linking, RefreshControl

---

#### Admin (`admin.tsx` → `AdminScreen`)

**Web (151 linhas):**
- Lista de usuários
- Toggle is_active
- Delete user
- Acesso só pra is_admin

**Mobile:**
- FlatList de usuários
- Switch pra is_active
- Swipe-to-delete ou botão delete com confirm

**APIs:** `api.listUsers()`, `api.updateUser()`, `api.deleteUser()`

---

#### Kids (`kids.tsx` → `KidsScreen`)

**Web (973 linhas):** Duas tabs:
1. **Mídia:** Upload de imagens/vídeos pra library kids, lista de assets com thumbnail, status, tags, topic assignment, toggle visibility, delete, mapping job
2. **Config:** Topic library categories, seasonal calendar

**Mobile:**
- Tab navigator (Mídia / Config)
- Upload: DocumentPicker (imagens + vídeos) → upload com progress
- Lista: FlatList grid de thumbnails
- Tap: tela de detalhe com metadados, tags, topic assignment
- Config: lista de categorias, calendar view

**APIs:** `api.listKidsTopics()`, `api.createKidsTopic()`, `api.deleteKidsTopic()`, `api.listKidsLibraryAssets()`, `api.uploadKidsLibraryAsset()`, `api.patchKidsAsset()`, `api.listKidsAssets()`, `api.deleteKidsAsset()`, `api.getKidsAssetThumbnailUrl()`, `api.getKidsAssetEvents()`, `api.toggleKidsAssetVisibility()`, `api.createKidsMappingJob()`, `api.getTopicLibrary()`, `api.getSeasonalCalendar()`

---

#### Kids Ideias (`kids-ideas.tsx` → `KidsIdeasScreen`)

**Web (713 linhas):**
- Lista de ideias kids com score, status, categoria
- Actions: score, reject, convert to topic, produce
- Discover ideas (gera via IA)
- Idea queue com reorder

**Mobile:**
- FlatList de ideias
- Actions: tap pra detalhe, swipe pra reject/score
- Discover: modal com form (categorias, quantidade, seasonal)
- Queue: DraggableFlatList

**APIs:** `api.listKidsIdeas()`, `api.createKidsIdea()`, `api.scoreKidsIdea()`, `api.rejectKidsIdea()`, `api.convertKidsIdea()`, `api.produceKidsIdea()`, `api.getKidsIdeaProvenance()`, `api.discoverKidsIdeas()`, `api.getKidsIdeaQueue()`, `api.addKidsIdeaToQueue()`, `api.removeKidsIdeaFromQueue()`, `api.reorderKidsIdeaQueue()`, `api.reconcileKidsIdeaQueue()`

---

### 3.3 Componentes Compartilhados

#### Design System (`ui.tsx` → `components/ui.tsx`)

| Web | Mobile | Notas |
|---|---|---|
| Card (`div.card-premium`) | `Card` (View styled) | Mesmo visual: bg surface, border, radius |
| Button (5 variants) | `Button` (TouchableOpacity) | primary/outline/ghost/danger/default |
| Input | `TextInput` styled | |
| Select | `Picker` ou `ModalPicker` | No mobile, picker nativo ou modal bottom sheet |
| Label | `Text` styled | |
| Badge (5 variants) | `Badge` (View + Text) | success/warning/error/info/default |
| Spinner | `ActivityIndicator` | Nativo |
| EmptyState | `EmptyState` (View + Text + icon) | |

#### SubtitlePreview (`subtitle-preview.tsx`)

Preview CSS-based de como as legendas aparecem no vídeo. No mobile: View + Text com mesmas props de estilo (font, size, color, outline, position, box).

#### ThumbnailPreview (`thumbnail-preview.tsx`)

Preview CSS-based da capa/thumbnail. No mobile: View + Image + Text com mesmas props.

#### PresentationControls (`presentation-controls.tsx`)

Toda a config da Presentation Layer. No mobile: form com Switch, Picker, TextInput, DocumentPicker, color pickers.

#### WorkerStatus (`worker-status.tsx`)

Card de status do worker (online/offline, GPU/CPU/RAM, atividade). No mobile: card com mesmas info, ícones, badges.

#### UploadIndicator (`upload-indicator.tsx`)

Barra de progresso de uploads. No mobile: ProgressBar + texto de %.

#### GameSearchModal (`game-search-modal.tsx`)

Modal de busca de jogos no catálogo IGDB. No mobile: Modal fullscreen com TextInput + FlatList.

---

## 4. Arquitetura do App

### Estrutura de Pastas

```
gpcg-mobile/
├── android/                    # Projeto Android nativo (Gradle)
├── ios/                        # Projeto iOS nativo (Xcode/CocoaPods)
├── src/
│   ├── api/
│   │   ├── client.ts           # Axios instance + interceptors + cookie handling
│   │   ├── auth.ts             # SSO flow, cookie extraction, getMe
│   │   └── endpoints.ts        # Mesmos métodos do api.ts do web
│   ├── components/
│   │   ├── ui/                 # Design system (Button, Card, Badge, Input, etc)
│   │   ├── SubtitlePreview.tsx
│   │   ├── ThumbnailPreview.tsx
│   │   ├── PresentationControls.tsx
│   │   ├── WorkerStatusCard.tsx
│   │   ├── UploadProgress.tsx
│   │   └── GameSearchModal.tsx
│   ├── screens/
│   │   ├── LoginScreen.tsx
│   │   ├── DashboardScreen.tsx
│   │   ├── ContentScreen.tsx
│   │   ├── IdeasScreen.tsx
│   │   ├── JobsScreen.tsx
│   │   ├── AutomationScreen.tsx
│   │   ├── VideosScreen.tsx
│   │   ├── VideoDetailScreen.tsx
│   │   ├── AdminScreen.tsx
│   │   ├── KidsScreen.tsx
│   │   └── KidsIdeasScreen.tsx
│   ├── navigation/
│   │   ├── AppNavigator.tsx    # Stack + Tab navigators
│   │   └── types.ts            # Route types
│   ├── hooks/
│   │   ├── usePoll.ts          # Tanstack Query wrapper
│   │   └── useAuth.ts          # Auth state
│   ├── store/
│   │   ├── auth.ts             # Zustand ou context (cookie, user)
│   │   └── domain.ts           # Domain config (games/kids)
│   ├── theme/
│   │   ├── colors.ts           # Mesmas cores do domain-config
│   │   ├── spacing.ts
│   │   └── index.ts            # StyleSheet theme
│   └── utils/
│       ├── format.ts           # fmtDuration, fmtBytes, fmtDate
│       └── upload.ts           # Chunked upload logic
├── App.tsx                     # Entry point
├── package.json
├── tsconfig.json
├── metro.config.js
├── react-native.config.js
└── .env                        # API_URL, etc
```

### Fluxo de Dados

```
Telas (Screens)
    ↓
Hooks (usePoll = Tanstack Query, useAuth)
    ↓
API Client (axios + cookie interceptor)
    ↓
https://brunointegrations.com/gpcg/api/
```

### Auth Flow (Cookie SSO)

```
1. App abre → checa AsyncStorage por cookie bi_auth
2. Se tem cookie → api.getMe() pra validar
   ├── Válido → Dashboard
   └── Inválido → Login
3. Login → abre WebView pro BI Identity
4. Após login, extrai cookies (bi_auth, bi_refresh) do WebView
5. Armazena no AsyncStorage + axios cookie jar
6. Navega pro Dashboard
7. Em cada request 401 → tenta refresh via /id/api/auth/check
   ├── Refresh OK → retry request
   └── Refresh fail → volta pra Login
```

### Domain System

Mesma lógica do web: `api.getDashboard().channel_domain` retorna "games" ou "kids". O app ajusta:
- Cores do tema (teal pra games, roxo pra kids)
- Labels (gameplay vs imagem, trecho vs imagem)
- Items de navegação
- Features habilitadas

### Polling

O web usa `usePoll(callback, interval)`. No mobile, Tanstack Query com `refetchInterval`:
```ts
const { data, refetch } = useQuery({
  queryKey: ['dashboard'],
  queryFn: () => api.getDashboard(),
  refetchInterval: 10000,
});
```

---

## 5. Desafios Mobile Específicos

### 5.1 Upload de Gameplay (arquivos grandes)

O web faz upload chunked (5MB chunks) com hash + resume. No mobile:
- `DocumentPicker` pra selecionar arquivo
- Ler arquivo em chunks (RN FileSystem)
- Mesma lógica de hash + resume do web
- Progress bar nativa
- **Desafio:** arquivos de gameplay podem ser 1-5GB. No mobile, background upload é essencial (não pode travar o app). Usar `react-native-background-upload` ou equivalente.

### 5.2 Video Player

O web usa `<video>` HTML5. No mobile, `react-native-video` (native player):
- Streaming do `api.videoUrl(id)` 
- Fullscreen capable
- Controls nativos

### 5.3 Drag-Reorder (Idea Queue)

O web usa drag-drop HTML5. No mobile:
- `react-native-gesture-handler` + `react-native-reanimated`
- Ou `DraggableFlatList` (lib que encapsula isso)
- Gestos nativos, performance suave

### 5.4 SSO Cookie

O web usa `credentials: "include"` no fetch. No mobile, cookies não são compartilhados entre WebView e axios automaticamente. Solução:
1. WebView login
2. `CookieManager.setFromResponse()` pra extrair cookies do WebView
3. Axios interceptor adiciona cookie header manualmente
4. Ou usar `axios-cookiejar-support` + `tough-cookie`

### 5.5 File Picker

O web usa `<input type="file">`. No mobile:
- `react-native-document-picker` pra gameplay/voz/imagens
- `react-native-image-picker` pra fotos da câmera/galeria (se necessário)

### 5.6 Push Notifications (futuro)

Não está no escopo agora, mas pra futuro:
- `@react-native-firebase/messaging` (Android) 
- APNs (iOS)
- Notificar quando vídeo ficar pronto, job falhou, etc

---

## 6. Plano de Implementação

### Fase 0: Setup do Projeto (1-2 dias)

1. `npx @react-native-community/cli@latest init GpcgMobile --version 0.87`
2. Configurar TypeScript strict
3. Instalar deps: navigation, axios, tanstack-query, vector-icons, async-storage, safe-area, gesture-handler, reanimated, document-picker, video, toast-message
4. Configurar tema (cores do GPCG)
5. Configurar metro.config.js
6. Configurar Android: package ID `com.brunomrtns.gpcg`, ícone, orientação
7. Configurar iOS: bundle ID, ícone, orientação
8. Build de teste: `npx react-native run-android` (hello world)

### Fase 1: Auth + Navegação (2-3 dias)

1. API client (axios + interceptors)
2. Login screen com WebView SSO
3. Cookie extraction + storage
4. Auth state (context/zustand)
5. App navigator (Stack + Tab)
6. Dashboard screen básica (só validar que auth funciona)

### Fase 2: Dashboard + Jobs (2-3 dias)

1. Dashboard completo (stats, worker status, automação toggle, últimos vídeos)
2. Jobs screen (lista, filtros, polling)
3. Componentes: Card, Badge, Button, Spinner, WorkerStatusCard
4. Hooks: usePoll (Tanstack Query)

### Fase 3: Vídeos (2 dias)

1. Videos list (grid + search)
2. Video detail screen (player + metadados + publicar + deletar + regenerar)
3. react-native-video integration

### Fase 4: Conteúdo (3-4 dias)

1. Content screen (tabs Mídia/Canal)
2. Upload de gameplay (DocumentPicker + chunked upload + progress)
3. Lista de gameplays (FlatList + status badges)
4. Game search modal (catálogo IGDB)
5. Actions: mapeamento, visibility, delete
6. Channel profile form

### Fase 5: Automação (3-4 dias)

1. Automation screen (form completo)
2. Todas as seções: estilo, apresentação, vídeo, legendas, transições, voz, música, YouTube
3. Presentation controls (thumbnail + opening config)
4. Voice upload (DocumentPicker)
5. Image upload (DocumentPicker)
6. Live preview (SubtitlePreview + ThumbnailPreview em RN)
7. Toggle Video/Capa (segmented control)

### Fase 6: Ideias + Kids (3-4 dias)

1. Ideas screen (knowledge items + queue + drag reorder)
2. Kids screen (topics + media library + upload)
3. Kids ideas screen (discover + queue + actions)

### Fase 7: Admin + Polish (1-2 dias)

1. Admin screen (user management)
2. Empty states
3. Error states
4. Loading states
5. Haptics feedback
6. Pull-to-refresh em todas as listas

### Fase 8: Build + Deploy (2-3 dias)

1. Build Android APK/AAB (release)
2. Build iOS (se tiver Mac)
3. Teste em device real
4. Configurar CI/CD (opcional: Fastlane)
5. Play Store submission (se desejado)

**Total estimado:** 18-25 dias de trabalho focado.

---

## 7. Configuração de Build

### Android (`android/app/build.gradle`)

```gradle
apply plugin: "com.android.application"
apply plugin: "org.jetbrains.kotlin.android"
apply plugin: "com.facebook.react"

android {
    namespace "com.brunomrtns.gpcg"
    compileSdk 35

    defaultConfig {
        applicationId "com.brunomrtns.gpcg"
        minSdk 24
        targetSdk 35
        versionCode 1
        versionName "1.0.0"
    }

    buildTypes {
        release {
            minifyEnabled true
            proguardFiles getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro"
            signingConfig signingConfigs.debug // trocar por release key
        }
    }
}
```

### Permissões Android (`AndroidManifest.xml`)

```xml
<uses-permission android:name="android.permission.INTERNET" />
<uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE" />
<uses-permission android:name="android.permission.VIBRATE" />
<!-- Para upload em background no futuro -->
<uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
```

### iOS (`Info.plist`)

- `NSPhotoLibraryUsageDescription`: pra acessar galeria
- `NSDocumentPickerUsageDescription`: pra document picker
- App Transport Security: permitir `brunointegrations.com` (HTTPS já)

### Env vars (`.env`)

```
API_BASE_URL=https://brunointegrations.com/gpcg/api
SSO_LOGIN_URL=https://brunointegrations.com/id/login
```

---

## 8. API Endpoints Necessários

O app consome **exatamente os mesmos endpoints** que o web. Zero mudanças no backend. Lista completa:

### Auth
- `GET /auth/me` — usuário atual
- `POST /auth/logout` — logout
- `GET /auth/users` — lista users (admin)
- `DELETE /auth/users/{id}` — delete user (admin)
- `PATCH /auth/users/{id}` — update user (admin)

### Dashboard
- `GET /dashboard` — stats agregadas

### Automação
- `GET /automation` — config atual
- `PATCH /automation` — salvar config
- `POST /automation/start` — iniciar
- `POST /automation/pause` — pausar
- `GET /automation/current-job` — job atual

### YouTube
- `GET /youtube/connect` — URL OAuth
- `GET /youtube/status` — status conexão
- `POST /youtube/disconnect` — revogar

### Gameplays / Conteúdo
- `POST /gameplays/upload` — upload (multipart, chunked)
- `GET /gameplays` — listar sources
- `GET /gameplays/{id}/events` — eventos mapeados
- `POST /gameplays/{id}/assign-game` — assignar jogo
- `POST /gameplays/{id}/create-mapping-job` — solicitar mapeamento
- `PATCH /gameplays/{id}` — toggle visibility/enabled
- `DELETE /gameplays/{id}` — deletar
- `POST /inbox/scan` — escanear inbox

### Catálogo
- `GET /catalog/search?q=...` — buscar jogos IGDB
- `GET /catalog/autocomplete?q=...` — autocomplete

### Jobs
- `GET /jobs` — listar
- `GET /jobs/{id}` — detalhe

### Vídeos
- `GET /videos` — listar
- `GET /videos/{id}/file` — stream
- `GET /videos/{id}/thumbnail` — thumb
- `PATCH /videos/{id}` — update metadados
- `POST /videos/{id}/publish` — publicar YouTube
- `DELETE /videos/{id}` — deletar
- `POST /videos/{id}/regenerate` — regenerar

### Vozes
- `GET /voices` — listar
- `POST /voices/upload` — upload
- `DELETE /voices/{filename}` — deletar

### Presentation
- `POST /presentation/upload-image` — upload imagem capa
- `GET /presentation/image/{key}` — servir imagem

### Knowledge Items / Ideias
- `GET /knowledge-items` — listar
- `GET /knowledge-items/stats` — stats
- `POST /knowledge-items/{id}/reject` — rejeitar
- `POST /knowledge-items/{id}/queue` — add to queue
- `DELETE /knowledge-items/{id}/queue` — remove from queue
- `PUT /knowledge-items/queue/reorder` — reorder
- `POST /knowledge-items/trigger-collection` — coletar
- `POST /knowledge-items/manual` — criar manual
- `GET /knowledge-items/gameplay-availability` — disponibilidade

### Channel
- `GET /channel/profile` — perfil
- `PATCH /channel/profile` — atualizar

### Workers
- `GET /workers` — lista workers

### Kids
- `GET /kids/topics` — listar tópicos
- `POST /kids/topics` — criar
- `DELETE /kids/topics/{id}` — deletar
- `GET /kids/library-assets` — listar assets
- `POST /kids/library-assets` — upload
- `PATCH /kids/assets/{id}` — update
- `DELETE /kids/assets/{id}` — deletar
- `GET /kids/topics/{id}/assets` — assets do tópico
- `POST /kids/assets/{id}/mapping-job` — mapeamento
- `GET /kids/ideas` — listar ideias
- `POST /kids/ideas` — criar
- `POST /kids/ideas/{id}/score` — pontuar
- `POST /kids/ideas/{id}/reject` — rejeitar
- `POST /kids/ideas/{id}/convert` — converter
- `POST /kids/ideas/{id}/produce` — produzir
- `POST /kids/ideas/discover` — descobrir
- `GET /kids/idea-queue` — fila
- `POST /kids/idea-queue/{id}` — add
- `DELETE /kids/idea-queue/{id}` — remove
- `PUT /kids/idea-queue/reorder` — reorder
- `GET /kids/topic-library` — biblioteca
- `GET /kids/seasonal-calendar` — calendário

---

## 9. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| SSO cookie não funciona entre WebView e axios | Média | Alto | Testar cedo na Fase 1. Fallback: adicionar endpoint token-based no backend |
| Upload de gameplay grande trava app | Alta | Alto | Background upload lib. Limitar tamanho no mobile |
| react-native-video não streama bem | Baixa | Médio | Testar cedo. Fallback: abrir no browser |
| Drag-reorder performance ruim | Média | Baixo | Usar DraggableFlatList (otimizada) |
| iOS build sem Mac | Alta | Médio | Usar CI/CD (GitHub Actions com macOS runner) ou serviços cloud |
| RN 0.87 muito novo, libs incompatíveis | Baixa | Alto | Pinar versões. Se 0.87 quebrar libs, usar 0.86 |

---

## 10. Decisões Tomadas

1. **Auth:** Token endpoint — adicionar `/api/auth/token` no backend GPCG que troca cookie SSO por JWT. O app faz login via WebView SSO, captura o cookie, chama `/api/auth/token` pra receber um JWT, e usa o JWT em todas as requests subsequentes. Mais limpo que compartilhar cookies entre WebView e axios.
2. **iOS:** Só Android por agora. iOS fica pra futuro (CI com macOS runner ou serviço cloud).
3. **Domínio:** Games + Kids — suporte completo aos dois domínios, mesma lógica do web.
4. **Distribuição:** APK sideload agora. Play Store futuramente — signing config deixa preparado desde o início pra não ter retrabalho.
5. **Notificações:** Push notifications fora do escopo inicial. Futuro.
6. **Offline:** Sem funcionalidade offline no escopo inicial.

---

## 11. Próximos Passos

1. **Aprovar este plano**
2. **Responder as decisões pendentes (seção 10)**
3. **Fase 0:** Criar o projeto `gpcg-mobile` com RN CLI 0.87
4. **Fase 1:** Auth + navegação básica
5. **Iterar fase por fase**
