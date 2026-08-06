# Análise: Oportunidades de IA no GPCG

> **Status**: Documento de referência para expansão de IA
> **Versão**: v0.3.13 (2026-08-06)
> **Escopo**: Levantamento completo de possibilidades de IA, classificadas por risco ao sistema
> **Base**: Auditoria de 20 componentes de IA existentes + 15 oportunidades não implementadas

---

## 1. Componentes de IA Já Existentes

O GPCG já tem uma camada rica de IA. Antes de adicionar mais, é importante entender o que já existe:

### 1.1 Pipeline Editorial (texto)

| Componente | Arquivo | Modelo | O que faz |
|------------|---------|--------|-----------|
| LLM Client | `infrastructure/llm.py` | `llama3.1:8b` | Cliente Ollama para texto |
| Curiosity Scorer | `application/curiosity_scorer.py` | LLM | Score de curiosidade (5 sub-scores) |
| Story Finder | `application/story_finder.py` | LLM | Transforma fact em story (ângulo) |
| Editorial Planner | `application/editorial_planner.py` | LLM | Decide formato + estratégia |
| Creative Engine | `application/creative_engine.py` | `qwen3:14b` | Hooks, angles, punchlines |
| Script Service | `application/script_service.py` | LLM | Gera roteiro |
| Humanization | `application/humanization.py` | LLM + regex | Quebra padrões de IA |
| Script Critic | `application/script_critic.py` | LLM | Avalia roteiro (6 dimensões) |
| Metadata Generator | `application/metadata_generator.py` | LLM | Título, descrição, tags |
| QA Service | `application/qa_service.py` | LLM | QA de coerência |
| Originality Checker | `domain/originality.py` | Determinístico | Anti-plágio (n-gram overlap) |

### 1.2 Pipeline de Gameplay (visão + áudio)

| Componente | Arquivo | Modelo | O que faz |
|------------|---------|--------|-----------|
| VLM (Vision) | `infrastructure/vision_analyzer.py` | `gemma3:12b` | Análise visual de frames |
| ASR | `infrastructure/asr_transcriber.py` | `large-v3` (Whisper) | Transcrição de áudio |
| Player Detector | `infrastructure/player_detector.py` | YOLOv8 | Detecção de personagem |
| Gameplay Analyzer | `application/gameplay_analyzer.py` | VLM + ASR + YOLO | Pipeline completo de análise |
| Gameplay Retriever | `application/gameplay_retriever.py` | Embeddings | Busca semântica de clips |

### 1.3 Inteligência Editorial V2 (novo)

| Componente | Arquivo | O que faz |
|------------|---------|-----------|
| Editorial Profile | `application/editorial_profile_service.py` | Identidade do canal (estruturada) |
| Editorial Intent | `application/editorial_intent_builder.py` | O que produzir agora |
| Editorial Brief | `application/editorial_brief_builder.py` | Como encontrar conteúdo |
| Composite Scorer | `application/composite_scorer.py` | Score 3 camadas (quality × fit × timing) |
| Feedback Propagator | `application/feedback_propagator.py` | Aprendizado via embeddings |
| Embedding Service | `application/embedding_service.py` | `nomic-embed-text` |

### 1.4 Feature Flags de IA

| Flag | Default | Descrição |
|------|---------|-----------|
| `gpcg_creative_engine_enabled` | True | Motor criativo |
| `gpcg_gameplay_analysis_enabled` | True | Análise VLM de gameplay |
| `gpcg_editorial_planning_enabled` | True | Planejamento editorial |
| `gpcg_curiosity_scoring_enabled` | True | Scoring de curiosidade |
| `gpcg_story_finder_enabled` | True | Story finder |
| `gpcg_humanization_enabled` | True | Humanização |
| `gpcg_script_critic_enabled` | True | Crítica de scripts |
| `gpcg_composite_scoring_enabled` | False | Scoring composto V2 |
| `gpcg_feedback_loop_enabled` | False | Loop de feedback |
| `gpcg_editorial_brief_enabled` | False | Editorial Brief V2 |

---

## 2. Oportunidades de IA — Classificadas por Risco

### Risco BAIXO (seguro implementar, alto valor)

#### [B01] Detecção Automática de Problemas no Inventário
- **Risco**: Baixo — só detecta, não modifica dados
- **Impacto**: Alto — previne loops de erro como o do GTA IV
- **Onde**: Novo serviço `ProblemDetectorService`
- **O que faz**:
  - Detecta GameplaySource com status=ready mas 0 GameplayAssets
  - Detecta GameplaySource com 0 GameplayEvents (não mapeada)
  - Detecta KnowledgeItems com game_id de jogo sem clips para o usuário
  - Detecta jobs presos (running > 1h sem heartbeat)
  - Detecta fila com KIs rejeitadas que não foram limpas
- **Ações**: Alertar na UI, auto-limpar KIs rejeitadas, auto-falhar jobs presos
- **Custo**: Zero LLM — queries DB + cálculo temporal
- **Feature flag**: `gpcg_problem_detection_enabled`

#### [B02] Detecção de "Dead Air" em Gameplay
- **Risco**: Baixo — análise passiva, não modifica gameplay
- **Impacto**: Médio — melhora seleção de clips
- **Onde**: `gameplay_analyzer.py` (durante análise)
- **O que faz**: Detecta períodos de baixa atividade (walking sem evento, menus, loading, idle)
- **Custo**: Zero LLM — análise de `activity_level` + duração de eventos existentes
- **Feature flag**: `gpcg_dead_air_detection_enabled`

#### [B03] Detecção de Clickbait Excessivo em Metadata
- **Risco**: Baixo — pós-processamento, não bloqueia pipeline
- **Impacto**: Médio — protege reputação do canal
- **Onde**: `metadata_generator.py` (após geração de título)
- **O que faz**: LLM avalia se título é enganoso vs conteúdo real, sugere correções
- **Custo**: 1 chamada LLM por vídeo
- **Feature flag**: `gpcg_clickbait_detection_enabled`

#### [B04] Detecção de Conteúdo Duplicado
- **Risco**: Baixo — análise passiva
- **Impacto**: Alto — previne vídeos canibalizando views um do outro
- **Onde**: Novo serviço `DuplicateDetector`
- **O que faz**: Compara embeddings de scripts/legendas de vídeos do canal, detecta sobreposição
- **Custo**: Zero LLM — dot product de embeddings já gerados
- **Feature flag**: `gpcg_duplicate_detection_enabled`

### Risco MÉDIO (requer testes, valor significativo)

#### [M01] Avaliação de Qualidade do Vídeo Final (VLM QA)
- **Risco**: Médio — adiciona etapa ao pipeline, pode atrasar entrega
- **Impacto**: Alto — garante qualidade antes de publicar
- **Onde**: `qa_service.py` (após render)
- **O que faz**: VLM analisa frames amostrados do vídeo renderizado
  - Sincronização áudio-vídeo
  - Legibilidade de legendas
  - Qualidade visual geral
  - Momentos de baixa qualidade
- **Custo**: 1 chamada VLM por vídeo (gemma3:12b, ~10s)
- **Feature flag**: `gpcg_video_vlm_qa_enabled`
- **Mitigação**: Se VLM falhar, prosseguir com QA técnico apenas

#### [M02] Otimização Automática de Thumbnails
- **Risco**: Médio — pode selecionar frame ruim se VLM errar
- **Impacto**: Alto — thumbnail é o maior fator de CTR no YouTube
- **Onde**: `infrastructure/media.py` (substituir `generate_thumbnail()`)
- **O que faz**: VLM seleciona melhor frame para thumbnail
  - Frame com ação dramática
  - Frame com personagem visível
  - Frame com boa iluminação
- **Custo**: 1 chamada VLM por vídeo
- **Feature flag**: `gpcg_thumbnail_optimization_enabled`
- **Mitigação**: Fallback para frame do meio (comportamento atual)

#### [M03] Avaliação de Qualidade de Clips (Clip Quality Scorer)
- **Risco**: Médio — pode rejeitar clips válidos se VLM for conservador
- **Impacto**: Médio — melhora qualidade visual dos vídeos
- **Onde**: `gameplay_analyzer.py` (após análise)
- **O que faz**: VLM avalia qualidade técnica do clip
  - Resolução adequada
  - Frame rate estável
  - Ausência de glitches visuais
  - Iluminação adequada
- **Custo**: 1 chamada VLM por clip (pode ser batch)
- **Feature flag**: `gpcg_clip_quality_scoring_enabled`
- **Mitigação**: Score é bonus, não hard filter

#### [M04] Alinhamento Script-Gameplay
- **Risco**: Médio — pode gerar reescritas desnecessárias
- **Impacto**: Alto — vídeo com narração desconectada do visual é ruim
- **Onde**: `script_service.py` (após geração, antes do critic)
- **O que faz**: VLM verifica se script combina com gameplay selecionado
  - Se gameplay mostra combate, script deve mencionar ação
  - Se gameplay é explorativo, script deve ser mais calmo
- **Custo**: 1 chamada VLM por vídeo
- **Feature flag**: `gpcg_gameplay_script_alignment_enabled`
- **Mitigação**: Só sugere ajustes, não força reescrita

#### [M05] Detecção de Gameplay de Baixa Qualidade
- **Risco**: Médio — pode marcar gameplay válida como baixa qualidade
- **Impacto**: Médio — evita desperdício de GPU em gameplay ruim
- **Onde**: `ingestion_service.py` (após probe)
- **O que faz**: VLM analisa amostras do vídeo para detectar
  - Gameplay muito lento/idle
  - Telas de menu/loading excessivas
  - Gameplay repetitivo
- **Custo**: 1 chamada VLM por gameplay (na ingestão)
- **Feature flag**: `gpcg_low_quality_detection_enabled`
- **Mitigação**: Apenas alerta, não bloqueia uso

### Risco ALTO (pode quebrar pipeline, requer cuidado)

#### [A01] Recomendação de Re-mapeamento Automático
- **Risco**: Alto — pode disparar re-mapeamento desnecessário (custo GPU alto)
- **Impacto**: Médio — mantém gameplay atualizada com modelos melhores
- **Onde**: `gameplay_analyzer.py` (após análise)
- **O que faz**: Detecta quando re-análise é necessária
  - Mudança no modelo VLM
  - Gameplay com baixo interesting_score médio
  - Gameplay com muitos eventos "UNKNOWN"
- **Custo**: Pode disparar job de mapeamento (custo GPU + tempo)
- **Feature flag**: `gpcg_remap_recommendation_enabled`
- **Mitigação**: Só recomenda, não executa automaticamente. Usuário aprova.

#### [A02] Sugestão de Melhores Momentos (Highlight Suggester)
- **Risco**: Alto — pode mudar seleção de clips e quebrar expectativa
- **Impacto**: Alto — vídeos com momentos épicos retêm mais
- **Onde**: `gameplay_analyzer.py` (integração com análise)
- **O que faz**: VLM identifica momentos "épicos" ou engraçados
  - Kills/Mortes dramáticas
  - Glitches engraçados
  - Momentos de tensão
- **Custo**: +1 chamada VLM por evento candidato
- **Feature flag**: `gpcg_highlight_suggestion_enabled`
- **Mitigação**: Score é bonus no retriever, não substitui semantic search

#### [A03] Análise de Performance do Canal (YouTube Analytics + IA)
- **Risco**: Alto — requer integração com YouTube Analytics API (OAuth complexo)
- **Impacto**: Muito alto — fecha o loop de aprendizado
- **Onde**: Novo serviço `AnalyticsAnalyzer`
- **O que faz**:
  - Integra com YouTube Analytics API
  - LLM analisa padrões de performance
  - Correlaciona tópicos com views/retention
  - Sugere ajustes no Editorial Profile
  - Detecta "format fatigue"
- **Custo**: Chamadas API + LLM
- **Feature flag**: `gpcg_analytics_analysis_enabled`
- **Mitigação**: Começar com análise manual (dashboard), depois automatizar

#### [A04] Análise de Sentimento de Comentários
- **Risco**: Alto — requer YouTube Comments API + pode gerar insights errados
- **Impacto**: Médio — feedback qualitativo do público
- **Onde**: Novo serviço `CommentAnalyzer`
- **O que faz**: LLM analisa comentários do YouTube
  - Detecta sentimentos predominantes
  - Identifica temas recorrentes
  - Sugere ajustes no conteúdo
- **Custo**: Chamadas API + LLM
- **Feature flag**: `gpcg_comment_analysis_enabled`
- **Mitigação**: Só analisa, não altera Editorial Profile automaticamente

#### [A05] Sugestão de Novos Jogos para o Canal
- **Risco**: Alto — pode sugerir jogos que usuário não tem gameplay
- **Impacto**: Médio — expansão estratégica do canal
- **Onde**: `editorial_strategy.py`
- **O que faz**: Baseado no perfil + performance, sugere novos jogos
- **Custo**: 1 chamada LLM
- **Feature flag**: `gpcg_game_suggestion_enabled`
- **Mitigação**: Só sugere, não adiciona automaticamente

#### [A06] Expansão Inteligente de Tags
- **Risco**: Alto — pode sugerir tags irrelevantes ou spammy
- **Impacto**: Baixo — tags têm impacto marginal no YouTube
- **Onde**: `metadata_generator.py`
- **O que faz**: Expande tags baseado em tendências + análise de tags similares
- **Custo**: Chamadas API + LLM
- **Feature flag**: `gpcg_smart_tag_expansion_enabled`
- **Mitigação**: Limitar a 5 tags extras, validar contra policy do YouTube

---

## 3. Matriz de Decisão

| ID | Risco | Impacto | Custo GPU | Custo LLM | Recomendação |
|----|-------|---------|-----------|-----------|--------------|
| B01 | Baixo | Alto | Zero | Zero | **Implementar agora** |
| B02 | Baixo | Médio | Zero | Zero | Implementar após B01 |
| B03 | Baixo | Médio | Zero | 1 chamada | Implementar com M01 |
| B04 | Baixo | Alto | Zero | Zero | **Implementar agora** |
| M01 | Médio | Alto | 1 VLM | Zero | Implementar após P0 |
| M02 | Médio | Alto | 1 VLM | Zero | Implementar com M01 |
| M03 | Médio | Médio | 1 VLM | Zero | Implementar após M01 |
| M04 | Médio | Alto | 1 VLM | Zero | Implementar após M02 |
| M05 | Médio | Médio | 1 VLM | Zero | Avaliar ROI |
| A01 | Alto | Médio | Alto | Zero | Só após estabilidade |
| A02 | Alto | Alto | Médio | Zero | Só após M03 |
| A03 | Alto | Muito alto | Zero | Médio | Roadmap futuro |
| A04 | Alto | Médio | Zero | Médio | Roadmap futuro |
| A05 | Alto | Médio | Zero | 1 chamada | Roadmap futuro |
| A06 | Alto | Baixo | Zero | Médio | Não priorizar |

---

## 4. Roadmap Sugerido

### Fase 1 — Estabilidade (agora)
- P0-01 a P0-04 do TODO_LIST.md
- B01: Detecção automática de problemas
- B04: Detecção de conteúdo duplicado

### Fase 2 — Qualidade Visual (após estabilidade)
- M01: VLM QA do vídeo final
- M02: Otimização de thumbnails
- B03: Detecção de clickbait

### Fase 3 — Qualidade de Gameplay (após Fase 2)
- M03: Clip quality scorer
- M04: Alinhamento script-gameplay
- B02: Dead air detection
- M05: Low-quality detector

### Fase 4 — Analytics e Aprendizado (futuro)
- A03: YouTube Analytics + IA
- A04: Análise de comentários
- A01: Re-mapeamento automático
- A02: Highlight suggester

### Não priorizar
- A05: Sugestão de novos jogos (baixo ROI)
- A06: Expansão de tags (baixo impacto)

---

## 5. Modelos Disponíveis (Ollama)

| Modelo | Uso | VRAM | Status |
|--------|-----|------|--------|
| `llama3.1:8b` | LLM geral | ~5GB | ✅ Configurado |
| `gemma3:12b` | VLM (visão) | ~8GB | ✅ Configurado |
| `qwen3:14b` | Creative engine | ~9GB | ✅ Configurado |
| `nomic-embed-text` | Embeddings | ~1GB | ✅ Configurado |
| `large-v3` (Whisper) | ASR | ~3GB | ✅ Configurado |
| YOLOv8 | Player detection | ~1GB | ✅ Configurado |

**Total VRAM usado**: ~27GB (RTX 3060 12GB precisa de swap/offload)

**Modelos sugeridos para futuro**:
- `qwen3-vl` — VLM mais avançado que gemma3
- `llama3.1:70b` — raciocínio mais complexo (requer mais VRAM)
- `clip-vit` — embeddings visuais para thumbnails

---

## 6. Padrão Arquitetural para Novos Componentes

```python
# src/gpcg/application/novo_componente.py
class NovoComponente:
    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient()
        self.settings = get_settings()

    def process(self, ...) -> Result:
        if not self.settings.gpcg_novo_componente_enabled:
            return fallback_result  # graceful degradation
        # Lógica principal
```

**Integração no pipeline** (`generation_service.py`):
```python
if self.settings.gpcg_novo_componente_enabled:
    resultado = self.novo_componente.process(...)
    job.artifacts["novo_componente"] = resultado.to_dict()
```

**Config** (`config.py`):
```python
gpcg_novo_componente_enabled: bool = False  # sempre off por default
gpcg_novo_componente_model: str = "llama3.1:8b"
```

**Princípios**:
1. Toda nova IA tem feature flag (default: off)
2. Toda nova IA tem fallback graceful (não quebra pipeline se falhar)
3. Toda nova IA persiste resultado em `job.artifacts` (auditabilidade)
4. Toda nova IA tem teste unitário
