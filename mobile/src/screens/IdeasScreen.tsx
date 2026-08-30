import React, { useState, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  RefreshControl,
  TouchableOpacity,
  Modal,
  TextInput,
  ScrollView,
  ActivityIndicator,
  Linking,
} from 'react-native';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useLiveData } from '../hooks/useLiveData';
import { SafeAreaView } from 'react-native-safe-area-context';
import Icon from 'react-native-vector-icons/MaterialCommunityIcons';
import { knowledgeApi, catalogApi } from '../api/endpoints';
import type { KnowledgeItem, GameAvailability, GameplaySourceInfo, CatalogGame } from '../api/endpoints';
import { Card, Badge, Button, Spinner, EmptyState } from '../components/ui';
import { colors } from '../theme/colors';
import { fontSize, fontWeight, radius, spacing } from '../theme/spacing';
import { fmtDate } from '../utils/format';
import Toast from 'react-native-toast-message';

// ── Constants (match web) ────────────────────────────────────────────────────

const TYPE_LABELS: Record<string, string> = {
  news: 'Notícia',
  curiosity: 'Curiosidade',
  lore: 'Lore',
  fact: 'Fact',
  manual: 'Manual',
};

const TYPE_BADGE: Record<string, any> = {
  news: 'info',
  curiosity: 'default',
  lore: 'warning',
  fact: 'success',
  manual: 'default',
};

/** Shorten a gameplay source filename for display in badges/tags.
 *  Removes extension, truncates long names at a reasonable length. */
function shortSourceName(filename: string | null | undefined, maxLen: number = 30): string {
  if (!filename) return 'Gameplay';
  let name = filename.replace(/\.[^.]+$/, '');
  name = name.replace(/\s*[｜|].*$/, '').replace(/\s*\[[^\]]*\]\s*$/, '').trim();
  if (name.length > maxLen) {
    name = name.slice(0, maxLen - 1).trim() + '…';
  }
  return name || 'Gameplay';
}

const STATUS_LABELS: Record<string, string> = {
  fresh: 'Disponível',
  used: 'Usado',
  rejected: 'Rejeitado',
};

const STATUS_BADGE: Record<string, any> = {
  fresh: 'success',
  used: 'info',
  rejected: 'error',
};

const AVAILABILITY_LABELS: Record<string, string> = {
  abundant: 'Bastante material',
  partial: 'Material parcial',
  low: 'Pouco material',
  none: 'Sem material novo',
  reuse_only: 'Apenas reutilização',
};

const AVAILABILITY_COLORS: Record<string, any> = {
  abundant: 'success',
  partial: 'warning',
  low: 'warning',
  none: 'error',
  reuse_only: 'default',
};

// ── Main Screen ──────────────────────────────────────────────────────────────

export function IdeasScreen() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [filterType, setFilterType] = useState<string>('');
  const [filterStatus, setFilterStatus] = useState<string>('fresh');
  const [minScore, setMinScore] = useState<number>(0);
  const [searchText, setSearchText] = useState<string>('');
  const [onlyWithGameplay, setOnlyWithGameplay] = useState<boolean>(false);
  const [filterGameId, setFilterGameId] = useState<number | null>(null);
  const [collecting, setCollecting] = useState(false);
  const [showManual, setShowManual] = useState(false);
  const [manualTitle, setManualTitle] = useState('');
  const [manualContent, setManualContent] = useState('');
  const [creating, setCreating] = useState(false);
  const [queueModalItem, setQueueModalItem] = useState<KnowledgeItem | null>(null);
  const [selectedGameplay, setSelectedGameplay] = useState<number | null>(null);
  const [selectedReuseOverride, setSelectedReuseOverride] = useState<string | null>(null);
  const [availableSources, setAvailableSources] = useState<GameplaySourceInfo[]>([]);
  const [selectedSource, setSelectedSource] = useState<number | null>(null);
  const [loadingSources, setLoadingSources] = useState(false);
  const [editQueueItem, setEditQueueItem] = useState<KnowledgeItem | null>(null);
  const [editGameplay, setEditGameplay] = useState<number | null>(null);
  const [editSources, setEditSources] = useState<GameplaySourceInfo[]>([]);
  const [editSelectedSource, setEditSelectedSource] = useState<number | null>(null);
  const [editLoadingSources, setEditLoadingSources] = useState(false);
  const [showFocusModal, setShowFocusModal] = useState(false);
  const [focusType, setFocusType] = useState<'game' | 'topic' | 'game+topic'>('game');
  const [focusGameSearch, setFocusGameSearch] = useState('');
  const [focusGameResults, setFocusGameResults] = useState<CatalogGame[]>([]);
  const [focusSelectedGame, setFocusSelectedGame] = useState<CatalogGame | null>(null);
  const [focusTopic, setFocusTopic] = useState('');
  const [focusItemTypes, setFocusItemTypes] = useState<string[]>([]);
  const [focusSaving, setFocusSaving] = useState(false);
  const [addingToQueue, setAddingToQueue] = useState(false);

  // ── Queries ──────────────────────────────────────────────────────────────

  const isManualFilter = filterType === 'manual';

  const { data: itemsData, refetch: refetchItems, isRefetching: refetchingItems, isLoading: itemsLoading } = useQuery({
    queryKey: ['knowledge-items', filterType, filterStatus, minScore, filterGameId, onlyWithGameplay, searchText],
    queryFn: async () => {
      const res = await knowledgeApi.list({
        item_type: isManualFilter ? undefined : filterType || undefined,
        source_type: isManualFilter ? 'manual' : undefined,
        status: filterStatus || undefined,
        limit: 200,
        min_score: minScore > 0 ? minScore : undefined,
        game_id: filterGameId ?? undefined,
      });
      let allItems: KnowledgeItem[] = res.items || [];
      // Client-side: filter by gameplay availability
      if (onlyWithGameplay && availabilityData?.games) {
        const gamesWithGameplay = new Set(
          (availabilityData.games as GameAvailability[])
            .filter((g: GameAvailability) => g.availability !== 'none' && g.availability !== 'reuse_only')
            .map((g: GameAvailability) => g.game_id),
        );
        allItems = allItems.filter((i: KnowledgeItem) => !i.game_id || gamesWithGameplay.has(i.game_id));
      }
      // Client-side: filter by search text
      if (searchText.trim()) {
        const q = searchText.toLowerCase().trim();
        allItems = allItems.filter((i: any) =>
          i.title?.toLowerCase().includes(q) ||
          i.content?.toLowerCase().includes(q) ||
          i.game_name?.toLowerCase().includes(q),
        );
      }
      return allItems;
    },
  });

  const { data: stats } = useQuery({
    queryKey: ['knowledge-stats'],
    queryFn: knowledgeApi.stats,
  });

  const { data: queueData, refetch: refetchQueue } = useLiveData(
    ['idea-queue'],
    knowledgeApi.getQueue,
    ['idea_queue.updated']
  );

  const { data: availabilityData } = useQuery({
    queryKey: ['gameplay-availability'],
    queryFn: knowledgeApi.getGameplayAvailability,
  });

  const { data: currentJobData } = useLiveData(
    ['current-job'],
    knowledgeApi.getCurrentJob,
    ['job.status_changed']
  );

  const { data: focusData } = useQuery({
    queryKey: ['collection-focus'],
    queryFn: knowledgeApi.getCollectionFocus,
  });

  const items = itemsData || [];
  const queue = queueData?.items || [];
  const queueIds = new Set((queueData?.queue || []).map((q: any) => (typeof q === 'object' ? q.ki_id : q)));
  const availability = availabilityData?.games || [];
  const currentJob = currentJobData?.job;
  const focus = focusData?.collection_focus;

  // ── Handlers ─────────────────────────────────────────────────────────────

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ['knowledge-items'] });
    queryClient.invalidateQueries({ queryKey: ['knowledge-stats'] });
    queryClient.invalidateQueries({ queryKey: ['idea-queue'] });
    queryClient.invalidateQueries({ queryKey: ['current-job'] });
  };

  const handleReject = async (id: number) => {
    try {
      await knowledgeApi.reject(id);
      Toast.show({ type: 'success', text1: 'Ideia rejeitada' });
      invalidateAll();
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    }
  };

  const handleCollect = async () => {
    setCollecting(true);
    try {
      await knowledgeApi.triggerCollection();
      Toast.show({ type: 'success', text1: 'Coleta disparada' });
      setTimeout(() => invalidateAll(), 3000);
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    } finally {
      setCollecting(false);
    }
  };

  const handleCreateManual = async () => {
    if (!manualTitle.trim() || !manualContent.trim()) {
      Toast.show({ type: 'error', text1: 'Título e conteúdo são obrigatórios' });
      return;
    }
    setCreating(true);
    try {
      await knowledgeApi.createManual({ title: manualTitle.trim(), content: manualContent.trim() });
      Toast.show({ type: 'success', text1: 'Ideia criada' });
      setShowManual(false);
      setManualTitle('');
      setManualContent('');
      invalidateAll();
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    } finally {
      setCreating(false);
    }
  };

  const openQueueModal = (item: any) => {
    setQueueModalItem(item);
    setSelectedGameplay(null);
    setSelectedReuseOverride(null);
    setAvailableSources([]);
    setSelectedSource(null);
  };

  const handleGameplaySelect = async (gameId: number | null) => {
    setSelectedGameplay(gameId);
    setSelectedReuseOverride(null);
    setAvailableSources([]);
    setSelectedSource(null);
    if (gameId) {
      setLoadingSources(true);
      try {
        const res = await knowledgeApi.getGameplaySourcesForGame(gameId);
        setAvailableSources(res.sources || []);
      } catch {
        setAvailableSources([]);
      } finally {
        setLoadingSources(false);
      }
    }
  };

  const handleAddToQueue = async () => {
    if (!queueModalItem) return;
    setAddingToQueue(true);
    try {
      await knowledgeApi.addToQueue(
        queueModalItem.id,
        selectedGameplay,
        selectedReuseOverride,
        selectedSource,
      );
      Toast.show({ type: 'success', text1: 'Adicionado à fila' });
      setQueueModalItem(null);
      setSelectedGameplay(null);
      setSelectedReuseOverride(null);
      setAvailableSources([]);
      setSelectedSource(null);
      invalidateAll();
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    } finally {
      setAddingToQueue(false);
    }
  };

  const handleRemoveFromQueue = async (id: number) => {
    try {
      await knowledgeApi.removeFromQueue(id);
      Toast.show({ type: 'success', text1: 'Removido da fila' });
      invalidateAll();
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    }
  };

  const openEditQueueGame = (item: any) => {
    setEditQueueItem(item);
    setEditGameplay(item.gameplay_preference ?? null);
    setEditSelectedSource(item.gameplay_source_id ?? null);
    setEditSources([]);
    if (item.gameplay_preference) {
      setEditLoadingSources(true);
      knowledgeApi
        .getGameplaySourcesForGame(item.gameplay_preference)
        .then((res) => setEditSources(res.sources || []))
        .catch(() => setEditSources([]))
        .finally(() => setEditLoadingSources(false));
    }
  };

  const handleEditGameplaySelect = async (gameId: number | null) => {
    setEditGameplay(gameId);
    setEditSources([]);
    setEditSelectedSource(null);
    if (gameId) {
      setEditLoadingSources(true);
      try {
        const res = await knowledgeApi.getGameplaySourcesForGame(gameId);
        setEditSources(res.sources || []);
      } catch {
        setEditSources([]);
      } finally {
        setEditLoadingSources(false);
      }
    }
  };

  const handleUpdateQueueGame = async () => {
    if (!editQueueItem) return;
    try {
      await knowledgeApi.updateQueueItem(
        editQueueItem.id,
        editGameplay,
        editQueueItem.reuse_override ?? null,
        editSelectedSource,
      );
      Toast.show({ type: 'success', text1: 'Jogo atualizado' });
      setEditQueueItem(null);
      setEditGameplay(null);
      setEditSources([]);
      setEditSelectedSource(null);
      invalidateAll();
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    }
  };

  const handleMoveQueue = async (index: number, direction: 'up' | 'down') => {
    const newQueue = [...queue];
    const swapIndex = direction === 'up' ? index - 1 : index + 1;
    if (swapIndex < 0 || swapIndex >= newQueue.length) return;
    [newQueue[index], newQueue[swapIndex]] = [newQueue[swapIndex], newQueue[index]];
    try {
      await knowledgeApi.reorderQueue(newQueue.map((i) => i.id));
      refetchQueue();
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
      refetchQueue();
    }
  };

  // ── Focus handlers ───────────────────────────────────────────────────────

  const handleFocusGameSearch = async (q: string) => {
    setFocusGameSearch(q);
    if (q.length < 2) {
      setFocusGameResults([]);
      return;
    }
    try {
      const res = await catalogApi.search(q);
      setFocusGameResults(res || []);
    } catch {
      setFocusGameResults([]);
    }
  };

  const handleSelectFocusGame = (game: any) => {
    setFocusSelectedGame(game);
    setFocusGameSearch(game.name);
    setFocusGameResults([]);
  };

  const handleSaveFocus = async () => {
    setFocusSaving(true);
    try {
      const payload: any = { type: focusType };
      if (focusType === 'game' || focusType === 'game+topic') {
        if (!focusSelectedGame) {
          Toast.show({ type: 'error', text1: 'Selecione um jogo' });
          setFocusSaving(false);
          return;
        }
        payload.game_id = focusSelectedGame.id;
        payload.game_name = focusSelectedGame.name;
      }
      if (focusType === 'topic' || focusType === 'game+topic') {
        if (!focusTopic.trim()) {
          Toast.show({ type: 'error', text1: 'Digite um tema' });
          setFocusSaving(false);
          return;
        }
        payload.topic = focusTopic.trim();
      }
      if (focusItemTypes.length > 0) payload.item_types = focusItemTypes;
      await knowledgeApi.setCollectionFocus(payload);
      Toast.show({ type: 'success', text1: 'Foco salvo' });
      setShowFocusModal(false);
      queryClient.invalidateQueries({ queryKey: ['collection-focus'] });
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    } finally {
      setFocusSaving(false);
    }
  };

  const handleClearFocus = async () => {
    try {
      await knowledgeApi.clearCollectionFocus();
      Toast.show({ type: 'success', text1: 'Foco removido' });
      queryClient.invalidateQueries({ queryKey: ['collection-focus'] });
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    }
  };

  const openFocusModal = () => {
    if (focus) {
      setFocusType(focus.type || 'game');
      if (focus.game_id) {
        setFocusSelectedGame({ id: focus.game_id, name: focus.game_name || '' });
        setFocusGameSearch(focus.game_name || '');
      }
      if (focus.topic) setFocusTopic(focus.topic);
      if (focus.item_types) setFocusItemTypes(focus.item_types);
    } else {
      setFocusType('game');
      setFocusGameSearch('');
      setFocusGameResults([]);
      setFocusSelectedGame(null);
      setFocusTopic('');
      setFocusItemTypes([]);
    }
    setShowFocusModal(true);
  };

  const toggleFocusItemType = (t: string) => {
    setFocusItemTypes((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]));
  };

  const focusLabel = (f: any): string => {
    if (!f) return '';
    if (f.type === 'game') return `Jogo: ${f.game_name}`;
    if (f.type === 'topic') return `Tema: ${f.topic}`;
    if (f.type === 'game+topic') return `${f.game_name} + ${f.topic}`;
    return '';
  };

  const scoreColor = (score: number) => {
    if (score >= 70) return colors.success;
    if (score >= 50) return colors.warning;
    if (score >= 30) return colors.accentWarm;
    return colors.error;
  };

  // ── Render ───────────────────────────────────────────────────────────────

  const typeFilters = [
    { value: '', label: 'Todos' },
    { value: 'news', label: 'Notícias' },
    { value: 'curiosity', label: 'Curiosidades' },
    { value: 'lore', label: 'Lore' },
    { value: 'manual', label: 'Manuais' },
  ];

  const statusFilters = [
    { value: '', label: 'Todos' },
    { value: 'fresh', label: 'Disponíveis' },
    { value: 'used', label: 'Usados' },
    { value: 'rejected', label: 'Rejeitados' },
  ];

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Ideias</Text>
          <Text style={styles.subtitle}>Banco de ideias de conteúdo</Text>
        </View>
        <Button title="+ Manual" variant="outline" size="sm" onPress={() => setShowManual(true)} />
        <Button
          title={collecting ? '...' : 'Coletar'}
          variant="primary"
          size="sm"
          onPress={handleCollect}
          loading={collecting}
        />
      </View>

      <FlatList
        data={items}
        keyExtractor={(item) => String(item.id)}
        refreshControl={
          <RefreshControl
            refreshing={refetchingItems}
            onRefresh={() => {
              refetchItems();
              refetchQueue();
            }}
            tintColor={colors.accent}
          />
        }
        contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}
        ListHeaderComponent={
          <View style={{ gap: spacing.md }}>
            {/* Stats */}
            {stats && (
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: spacing.sm }}>
                <StatChip label="Total" value={stats.total} />
                <StatChip label="Disponíveis" value={stats.fresh} color={colors.success} />
                <StatChip label="Usados" value={stats.by_status?.used || 0} color={colors.info} />
                <StatChip label="Na Fila" value={queue.length} color="#a855f7" />
                <StatChip label="Notícias" value={stats.by_type?.news || 0} color={colors.info} />
              </ScrollView>
            )}

            {/* Collection Focus Bar */}
            <View style={styles.focusBar}>
              <Icon name="flash" size={20} color={colors.accentWarm} />
              {focus ? (
                <>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.focusLabel}>Foco de coleta ativo</Text>
                    <Text style={styles.focusValue} numberOfLines={1}>
                      {focusLabel(focus)}
                      {(focus.item_types?.length ?? 0) > 0 && ` (${(focus.item_types || []).join(', ')})`}
                    </Text>
                  </View>
                  <TouchableOpacity onPress={openFocusModal} style={styles.focusBtn}>
                    <Text style={styles.focusBtnText}>Editar</Text>
                  </TouchableOpacity>
                  <TouchableOpacity onPress={handleClearFocus} style={styles.focusRemoveBtn}>
                    <Text style={styles.focusRemoveText}>Remover</Text>
                  </TouchableOpacity>
                </>
              ) : (
                <>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.focusLabel}>Sem foco — coleta geral</Text>
                  </View>
                  <TouchableOpacity onPress={openFocusModal} style={styles.focusSetBtn}>
                    <Text style={styles.focusSetText}>Definir Foco</Text>
                  </TouchableOpacity>
                </>
              )}
            </View>

            {/* Currently Processing */}
            {currentJob && (
              <View style={styles.processingCard}>
                <View style={styles.processingHeader}>
                  <View style={styles.pulseDot} />
                  <Text style={styles.processingTitle}>Em Processamento</Text>
                  <Text style={styles.processingJobId}>Job #{currentJob.id}</Text>
                </View>
                <View style={styles.processingBody}>
                  <View style={styles.processingIcon}>
                    <Icon name="cog" size={20} color={colors.accent} />
                  </View>
                  <View style={{ flex: 1 }}>
                    {currentJob.ki_title ? (
                      <>
                        <Text style={styles.processingItem} numberOfLines={1}>{currentJob.ki_title}</Text>
                        <View style={styles.processingMeta}>
                          {currentJob.ki_item_type && (
                            <Badge label={TYPE_LABELS[currentJob.ki_item_type] || currentJob.ki_item_type} variant={TYPE_BADGE[currentJob.ki_item_type] || 'default'} />
                          )}
                          <Text style={styles.processingStage}>
                            {t(`stages:${currentJob.stage}`, currentJob.stage_label || currentJob.stage) as string}
                          </Text>
                        </View>
                      </>
                    ) : (
                      <Text style={styles.processingItem}>
                        {t(`stages:${currentJob.stage}`, currentJob.stage_label || 'Processando...') as string}
                      </Text>
                    )}
                  </View>
                  {currentJob.progress > 0 && (
                    <View style={{ alignItems: 'flex-end' }}>
                      <Text style={styles.progressLabel}>Progresso</Text>
                      <Text style={styles.progressValue}>{Math.round(Math.min(currentJob.progress * 100, 100))}%</Text>
                    </View>
                  )}
                </View>
              </View>
            )}

            {/* Queue Section */}
            {queue.length > 0 && (
              <View style={styles.queueSection}>
                <View style={styles.queueHeader}>
                  <Icon name="playlist-play" size={20} color="#a855f7" />
                  <Text style={styles.queueTitle}>Fila de Produção</Text>
                  <Text style={styles.queueCount}>
                    {queue.length} {queue.length === 1 ? 'ideia' : 'ideias'}
                  </Text>
                </View>
                <View style={{ gap: spacing.sm }}>
                  {queue.map((item, index) => (
                    <QueueCard
                      key={item.id}
                      item={item}
                      index={index}
                      total={queue.length}
                      availability={availability}
                      onMoveUp={() => handleMoveQueue(index, 'up')}
                      onMoveDown={() => handleMoveQueue(index, 'down')}
                      onRemove={() => handleRemoveFromQueue(item.id)}
                      onEditGame={() => openEditQueueGame(item)}
                    />
                  ))}
                </View>
                <Text style={styles.queueHint}>
                  A automação consome estas ideias em ordem (primeiro = próximo vídeo). Use as setas para reordenar.
                </Text>
              </View>
            )}

            {/* Filters */}
            <View>
              {/* Search */}
              <TextInput
                style={styles.searchInput}
                placeholder="Buscar por título, conteúdo ou jogo..."
                placeholderTextColor={colors.textMuted}
                value={searchText}
                onChangeText={setSearchText}
              />

              {/* Score filter */}
              <Text style={[styles.filterLabel, { marginTop: spacing.sm }]}>Score mínimo</Text>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: spacing.sm, paddingVertical: spacing.xs }}>
                {[
                  { v: 0, label: 'Todos' },
                  { v: 30, label: '≥30' },
                  { v: 50, label: '≥50' },
                  { v: 70, label: '≥70' },
                ].map((s) => (
                  <FilterChip key={s.v} label={s.label} active={minScore === s.v} onPress={() => setMinScore(s.v)} />
                ))}
              </ScrollView>

              <Text style={[styles.filterLabel, { marginTop: spacing.sm }]}>Tipo</Text>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: spacing.sm, paddingVertical: spacing.xs }}>
                {typeFilters.map((f) => (
                  <FilterChip key={f.value} label={f.label} active={filterType === f.value} onPress={() => setFilterType(f.value)} />
                ))}
              </ScrollView>
              <Text style={[styles.filterLabel, { marginTop: spacing.sm }]}>Status</Text>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: spacing.sm, paddingVertical: spacing.xs }}>
                {statusFilters.map((f) => (
                  <FilterChip key={f.value} label={f.label} active={filterStatus === f.value} onPress={() => setFilterStatus(f.value)} />
                ))}
              </ScrollView>

              {/* Gameplay toggle */}
              <TouchableOpacity
                style={[styles.gameplayToggle, onlyWithGameplay && styles.gameplayToggleActive]}
                onPress={() => setOnlyWithGameplay(!onlyWithGameplay)}
              >
                <Icon name="gamepad-variant" size={14} color={onlyWithGameplay ? colors.accent : colors.textMuted} />
                <Text style={[styles.gameplayToggleText, onlyWithGameplay && styles.gameplayToggleTextActive]}>
                  Só com gameplay disponível
                </Text>
              </TouchableOpacity>

              {/* Game filter */}
              {availability.length > 0 && (
                <>
                  <Text style={[styles.filterLabel, { marginTop: spacing.sm }]}>Jogo</Text>
                  <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: spacing.sm, paddingVertical: spacing.xs }}>
                    <FilterChip label="Todos" active={filterGameId === null} onPress={() => setFilterGameId(null)} />
                    {availability.filter((g: any) => g.availability !== 'none').map((g: any) => (
                      <FilterChip key={g.game_id} label={g.game_name} active={filterGameId === g.game_id} onPress={() => setFilterGameId(g.game_id)} />
                    ))}
                  </ScrollView>
                </>
              )}

              {/* Result count */}
              <Text style={[styles.filterLabel, { marginTop: spacing.sm, color: colors.textMuted }]}>
                {items.length} {items.length === 1 ? 'ideia' : 'ideias'}
              </Text>
            </View>
          </View>
        }
        ListEmptyComponent={
          itemsLoading ? (
            <View style={{ paddingVertical: spacing.xxl, alignItems: 'center' }}>
              <Spinner size="large" />
            </View>
          ) : (
            <Card>
              <EmptyState
                icon={<Icon name="lightbulb-outline" size={40} color={colors.textMuted} />}
                title="Nenhuma ideia"
                description="Dispare a coleta ou crie uma ideia manual."
              />
            </Card>
          )
        }
        renderItem={({ item }) => (
          <IdeaCard
            item={item}
            inQueue={queueIds.has(item.id)}
            onReject={() => handleReject(item.id)}
            onAddToQueue={() => openQueueModal(item)}
            onRemoveFromQueue={() => handleRemoveFromQueue(item.id)}
            scoreColor={scoreColor}
          />
        )}
      />

      {/* ── Add to Queue Modal ─────────────────────────────────────────────── */}
      <Modal
        visible={!!queueModalItem}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setQueueModalItem(null)}
      >
        <SafeAreaView style={styles.modalContainer} edges={['top']}>
          <View style={styles.modalHeader}>
            <TouchableOpacity onPress={() => setQueueModalItem(null)}>
              <Text style={styles.closeButton}>Cancelar</Text>
            </TouchableOpacity>
            <Text style={styles.modalTitle}>Adicionar à Fila</Text>
            <View style={{ width: 60 }} />
          </View>
          <ScrollView contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
            {queueModalItem && (
              <Text style={styles.modalItemTitle} numberOfLines={2}>{queueModalItem.title}</Text>
            )}

            <Text style={styles.modalLabel}>Gameplay de fundo</Text>
            <View style={styles.pickerWrap}>
              {([
                { id: null as number | null, name: 'Automático (sistema escolhe)', availability: '' },
                ...availability.map((g: GameAvailability) => ({
                  id: g.game_id as number | null,
                  name: g.game_name,
                  availability: g.availability,
                })),
              ] as { id: number | null; name: string; availability: string }[]).map((g) => (
                <TouchableOpacity
                  key={g.id ?? 'auto'}
                  style={[
                    styles.pickerOption,
                    selectedGameplay === g.id && styles.pickerOptionActive,
                  ]}
                  onPress={() => handleGameplaySelect(g.id)}
                >
                  <Text
                    style={[
                      styles.pickerOptionText,
                      selectedGameplay === g.id && styles.pickerOptionTextActive,
                    ]}
                    numberOfLines={1}
                  >
                    {g.name}
                    {g.id && g.availability ? ` · ${AVAILABILITY_LABELS[g.availability] || g.availability}` : ''}
                  </Text>
                  {selectedGameplay === g.id && <Icon name="check-circle" size={16} color={colors.accent} />}
                </TouchableOpacity>
              ))}
            </View>

            {/* Specific source selector */}
            {selectedGameplay && (
              <View>
                {loadingSources ? (
                  <Text style={styles.muted}>Carregando gameplays disponíveis...</Text>
                ) : (
                  <>
                    <Text style={styles.modalLabel}>Gameplay específica (opcional)</Text>
                    <View style={styles.pickerWrap}>
                      <TouchableOpacity
                        style={[styles.pickerOption, !selectedSource && styles.pickerOptionActive]}
                        onPress={() => setSelectedSource(null)}
                      >
                        <Text style={[styles.pickerOptionText, !selectedSource && styles.pickerOptionTextActive]}>
                          Usar todas (recomendado)
                        </Text>
                        {!selectedSource && <Icon name="check-circle" size={16} color={colors.accent} />}
                      </TouchableOpacity>
                      {availableSources.map((s) => (
                        <TouchableOpacity
                          key={s.source_id}
                          style={[styles.pickerOption, selectedSource === s.source_id && styles.pickerOptionActive]}
                          onPress={() => setSelectedSource(s.source_id)}
                        >
                          <Text
                            style={[styles.pickerOptionText, selectedSource === s.source_id && styles.pickerOptionTextActive]}
                            numberOfLines={1}
                          >
                            {(s.filename || `Source #${s.source_id}`).slice(0, 50)} · {Math.floor(s.free_seconds / 60)}min livre
                          </Text>
                          {selectedSource === s.source_id && <Icon name="check-circle" size={16} color={colors.accent} />}
                        </TouchableOpacity>
                      ))}
                    </View>
                    {availableSources.length === 0 && (
                      <Text style={styles.muted}>
                        Nenhuma gameplay individual tem 2 min livre — o sistema usará todas automaticamente.
                      </Text>
                    )}
                    {selectedSource && (() => {
                      const src = availableSources.find((s) => s.source_id === selectedSource);
                      if (!src) return null;
                      const freeMin = Math.floor(src.free_seconds / 60);
                      const freeSec = Math.floor(src.free_seconds % 60);
                      const isLow = src.free_seconds < 180;
                      const isVeryLow = src.free_seconds < 120;
                      if (!isLow) return null;
                      return (
                        <View style={{
                          marginTop: 8,
                          padding: 8,
                          borderRadius: 8,
                          borderWidth: 1,
                          borderColor: isVeryLow ? 'rgba(239,68,68,0.3)' : 'rgba(245,158,11,0.3)',
                          backgroundColor: isVeryLow ? 'rgba(239,68,68,0.1)' : 'rgba(245,158,11,0.1)',
                        }}>
                          <Text style={{
                            fontSize: 11,
                            color: isVeryLow ? '#fca5a5' : '#fcd34d',
                          }}>
                            {isVeryLow ? '⚠' : '⚠'} Apenas {freeMin}:{freeSec.toString().padStart(2, '0')} de material livre.{'\n'}
                            Pode acabar antes da sua vez na fila — se isso acontecer, o sistema usará outra gameplay do mesmo jogo automaticamente.
                          </Text>
                        </View>
                      );
                    })()}
                  </>
                )}
              </View>
            )}

            {/* Reuse override */}
            {selectedGameplay && (() => {
              const game = availability.find((g) => g.game_id === selectedGameplay);
              if (!game) return null;
              const isLow = game.availability === 'none' || game.availability === 'low' || game.availability === 'reuse_only';
              if (!isLow) return null;
              return (
                <View style={styles.reuseBox}>
                  <Text style={styles.reuseTitle}>Pouco material. O que fazer?</Text>
                  {[
                    { v: null, label: 'Usar outra gameplay automaticamente (fallback)' },
                    { v: 'allow_reuse', label: 'Permitir reutilização excepcional' },
                    { v: 'skip', label: 'Não gerar enquanto não houver material' },
                  ].map((opt) => (
                    <TouchableOpacity
                      key={opt.v ?? 'default'}
                      style={styles.radioRow}
                      onPress={() => setSelectedReuseOverride(opt.v)}
                    >
                      <Icon
                        name={selectedReuseOverride === opt.v ? 'radiobox-marked' : 'radiobox-blank'}
                        size={20}
                        color={selectedReuseOverride === opt.v ? colors.accent : colors.textMuted}
                      />
                      <Text style={styles.radioText}>{opt.label}</Text>
                    </TouchableOpacity>
                  ))}
                </View>
              );
            })()}

            <Button
              title="Adicionar à Fila"
              variant="primary"
              fullWidth
              onPress={handleAddToQueue}
              loading={addingToQueue}
            />
          </ScrollView>
        </SafeAreaView>
      </Modal>

      {/* ── Edit Queue Game Modal ──────────────────────────────────────────── */}
      <Modal
        visible={!!editQueueItem}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setEditQueueItem(null)}
      >
        <SafeAreaView style={styles.modalContainer} edges={['top']}>
          <View style={styles.modalHeader}>
            <TouchableOpacity onPress={() => setEditQueueItem(null)}>
              <Text style={styles.closeButton}>Cancelar</Text>
            </TouchableOpacity>
            <Text style={styles.modalTitle}>Alterar Jogo</Text>
            <View style={{ width: 60 }} />
          </View>
          <ScrollView contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
            {editQueueItem && (
              <Text style={styles.modalItemTitle} numberOfLines={2}>{editQueueItem.title}</Text>
            )}

            <Text style={styles.modalLabel}>Gameplay de fundo</Text>
            <View style={styles.pickerWrap}>
              {([
                { id: null as number | null, name: 'Automático (sistema escolhe)', availability: '' },
                ...availability.map((g: GameAvailability) => ({
                  id: g.game_id as number | null,
                  name: g.game_name,
                  availability: g.availability,
                })),
              ] as { id: number | null; name: string; availability: string }[]).map((g) => (
                <TouchableOpacity
                  key={g.id ?? 'auto'}
                  style={[styles.pickerOption, editGameplay === g.id && styles.pickerOptionActive]}
                  onPress={() => handleEditGameplaySelect(g.id)}
                >
                  <Text
                    style={[styles.pickerOptionText, editGameplay === g.id && styles.pickerOptionTextActive]}
                    numberOfLines={1}
                  >
                    {g.name}
                    {g.id && g.availability ? ` · ${AVAILABILITY_LABELS[g.availability] || g.availability}` : ''}
                  </Text>
                  {editGameplay === g.id && <Icon name="check-circle" size={16} color={colors.accent} />}
                </TouchableOpacity>
              ))}
            </View>

            {editGameplay && (
              <View>
                {editLoadingSources ? (
                  <Text style={styles.muted}>Carregando gameplays...</Text>
                ) : (
                  <>
                    <Text style={styles.modalLabel}>Gameplay específica (opcional)</Text>
                    <View style={styles.pickerWrap}>
                      <TouchableOpacity
                        style={[styles.pickerOption, !editSelectedSource && styles.pickerOptionActive]}
                        onPress={() => setEditSelectedSource(null)}
                      >
                        <Text style={[styles.pickerOptionText, !editSelectedSource && styles.pickerOptionTextActive]}>
                          Usar todas (recomendado)
                        </Text>
                        {!editSelectedSource && <Icon name="check-circle" size={16} color={colors.accent} />}
                      </TouchableOpacity>
                      {editSources.map((s) => (
                        <TouchableOpacity
                          key={s.source_id}
                          style={[styles.pickerOption, editSelectedSource === s.source_id && styles.pickerOptionActive]}
                          onPress={() => setEditSelectedSource(s.source_id)}
                        >
                          <Text
                            style={[styles.pickerOptionText, editSelectedSource === s.source_id && styles.pickerOptionTextActive]}
                            numberOfLines={1}
                          >
                            {(s.filename || `Source #${s.source_id}`).slice(0, 50)} · {Math.floor(s.free_seconds / 60)}min livre
                          </Text>
                          {editSelectedSource === s.source_id && <Icon name="check-circle" size={16} color={colors.accent} />}
                        </TouchableOpacity>
                      ))}
                    </View>
                  </>
                )}
              </View>
            )}

            <Button title="Salvar" variant="primary" fullWidth onPress={handleUpdateQueueGame} />
          </ScrollView>
        </SafeAreaView>
      </Modal>

      {/* ── Manual Idea Modal ──────────────────────────────────────────────── */}
      <Modal
        visible={showManual}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setShowManual(false)}
      >
        <SafeAreaView style={styles.modalContainer} edges={['top']}>
          <View style={styles.modalHeader}>
            <TouchableOpacity onPress={() => setShowManual(false)}>
              <Text style={styles.closeButton}>Cancelar</Text>
            </TouchableOpacity>
            <Text style={styles.modalTitle}>Nova Ideia Manual</Text>
            <View style={{ width: 60 }} />
          </View>
          <ScrollView contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
            <View>
              <Text style={styles.modalLabel}>Título</Text>
              <TextInput
                style={styles.textInput}
                value={manualTitle}
                onChangeText={setManualTitle}
                placeholder="Título da ideia"
                placeholderTextColor={colors.textMuted}
              />
            </View>
            <View>
              <Text style={styles.modalLabel}>Conteúdo</Text>
              <TextInput
                style={styles.textArea}
                value={manualContent}
                onChangeText={setManualContent}
                placeholder="Descreva a ideia de conteúdo..."
                placeholderTextColor={colors.textMuted}
                multiline
                numberOfLines={5}
                textAlignVertical="top"
              />
            </View>
            <Button
              title="Criar Ideia"
              variant="primary"
              fullWidth
              onPress={handleCreateManual}
              loading={creating}
              disabled={!manualTitle.trim() || !manualContent.trim()}
            />
          </ScrollView>
        </SafeAreaView>
      </Modal>

      {/* ── Collection Focus Modal ─────────────────────────────────────────── */}
      <Modal
        visible={showFocusModal}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setShowFocusModal(false)}
      >
        <SafeAreaView style={styles.modalContainer} edges={['top']}>
          <View style={styles.modalHeader}>
            <TouchableOpacity onPress={() => setShowFocusModal(false)}>
              <Text style={styles.closeButton}>Cancelar</Text>
            </TouchableOpacity>
            <Text style={styles.modalTitle}>Foco de Coleta</Text>
            <View style={{ width: 60 }} />
          </View>
          <ScrollView contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
            <Text style={styles.muted}>
              Direcione a busca de ideias para um jogo, tema, ou ambos. As ideias coletadas aparecem na lista normal.
            </Text>

            {/* Focus type */}
            <Text style={styles.modalLabel}>Tipo de foco</Text>
            <View style={styles.focusTypeRow}>
              {([
                { v: 'game', label: 'Jogo' },
                { v: 'topic', label: 'Tema livre' },
                { v: 'game+topic', label: 'Jogo + Tema' },
              ] as const).map((opt) => (
                <TouchableOpacity
                  key={opt.v}
                  style={[styles.focusTypeBtn, focusType === opt.v && styles.focusTypeBtnActive]}
                  onPress={() => setFocusType(opt.v)}
                >
                  <Text style={[styles.focusTypeText, focusType === opt.v && styles.focusTypeTextActive]}>
                    {opt.label}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>

            {/* Game selector */}
            {(focusType === 'game' || focusType === 'game+topic') && (
              <View>
                <Text style={styles.modalLabel}>Jogo</Text>
                <TextInput
                  style={styles.textInput}
                  placeholder="Buscar jogo no catálogo..."
                  value={focusGameSearch}
                  onChangeText={handleFocusGameSearch}
                  placeholderTextColor={colors.textMuted}
                />
                {focusGameResults.length > 0 && (
                  <View style={styles.searchResults}>
                    {focusGameResults.map((g) => (
                      <TouchableOpacity
                        key={g.id}
                        style={styles.searchResult}
                        onPress={() => handleSelectFocusGame(g)}
                      >
                        <Text style={styles.searchResultText} numberOfLines={1}>{g.name}</Text>
                        {g.release_year && <Text style={styles.searchResultYear}>{g.release_year}</Text>}
                      </TouchableOpacity>
                    ))}
                  </View>
                )}
                {focusSelectedGame && (
                  <Text style={styles.selectedGame}>Selecionado: {focusSelectedGame.name}</Text>
                )}
              </View>
            )}

            {/* Topic input */}
            {(focusType === 'topic' || focusType === 'game+topic') && (
              <View>
                <Text style={styles.modalLabel}>Tema</Text>
                <TextInput
                  style={styles.textInput}
                  placeholder="Ex: mistérios, fatos sobre espaço..."
                  value={focusTopic}
                  onChangeText={setFocusTopic}
                  placeholderTextColor={colors.textMuted}
                />
              </View>
            )}

            {/* Item types */}
            <Text style={styles.modalLabel}>Tipos de ideia (opcional)</Text>
            <View style={styles.itemTypesRow}>
              {(['news', 'curiosity', 'lore', 'fact'] as const).map((t) => (
                <TouchableOpacity
                  key={t}
                  style={[styles.itemTypeChip, focusItemTypes.includes(t) && styles.itemTypeChipActive]}
                  onPress={() => toggleFocusItemType(t)}
                >
                  <Text style={[styles.itemTypeText, focusItemTypes.includes(t) && styles.itemTypeTextActive]}>
                    {TYPE_LABELS[t] || t}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>

            <Button
              title="Salvar Foco"
              variant="primary"
              fullWidth
              onPress={handleSaveFocus}
              loading={focusSaving}
            />
          </ScrollView>
        </SafeAreaView>
      </Modal>
    </SafeAreaView>
  );
}

// ── Sub-components ───────────────────────────────────────────────────────────

function StatChip({ label, value, color }: { label: string; value: number; color?: string }) {
  return (
    <View style={styles.statChip}>
      <Text style={[styles.statValue, color && { color }]}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}

function FilterChip({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  return (
    <TouchableOpacity
      style={[styles.chip, active && styles.chipActive]}
      onPress={onPress}
    >
      <Text style={[styles.chipText, active && styles.chipTextActive]}>{label}</Text>
    </TouchableOpacity>
  );
}

function QueueCard({
  item,
  index,
  total,
  availability,
  onMoveUp,
  onMoveDown,
  onRemove,
  onEditGame,
}: {
  item: KnowledgeItem;
  index: number;
  total: number;
  availability: GameAvailability[];
  onMoveUp: () => void;
  onMoveDown: () => void;
  onRemove: () => void;
  onEditGame: () => void;
}) {
  return (
    <View style={styles.queueCard}>
      {/* Order controls */}
      <View style={styles.queueOrderCol}>
        <TouchableOpacity onPress={onMoveUp} disabled={index === 0} style={[styles.orderBtn, index === 0 && styles.orderBtnDisabled]}>
          <Icon name="chevron-up" size={16} color={index === 0 ? colors.textMuted : colors.text} />
        </TouchableOpacity>
        <View style={styles.queueNumber}>
          <Text style={styles.queueNumberText}>{index + 1}</Text>
        </View>
        <TouchableOpacity onPress={onMoveDown} disabled={index === total - 1} style={[styles.orderBtn, index === total - 1 && styles.orderBtnDisabled]}>
          <Icon name="chevron-down" size={16} color={index === total - 1 ? colors.textMuted : colors.text} />
        </TouchableOpacity>
      </View>

      {/* Content */}
      <View style={{ flex: 1 }}>
        <View style={styles.queueItemBadges}>
          <Badge label={TYPE_LABELS[item.item_type] || item.item_type} variant={TYPE_BADGE[item.item_type] || 'default'} />
          {item.game_id && (
            <Badge label={item.game_name || `Jogo #${item.game_id}`} variant="default" />
          )}
          {item.gameplay_preference && item.gameplay_preference > 0 && (
            <TouchableOpacity onPress={onEditGame} style={styles.gameEditBadge}>
              <Text style={styles.gameEditText} numberOfLines={1}>
                {availability.find((g) => g.game_id === item.gameplay_preference)?.game_name || `Jogo #${item.gameplay_preference}`}
              </Text>
              <Icon name="pencil" size={10} color={colors.accent} />
            </TouchableOpacity>
          )}
          {!item.gameplay_preference && !item.game_id && (
            <TouchableOpacity onPress={onEditGame} style={styles.gameSetBadge}>
              <Icon name="plus" size={10} color={colors.textMuted} />
              <Text style={styles.gameSetText}>Definir jogo</Text>
            </TouchableOpacity>
          )}
          {item.reuse_override === 'allow_reuse' && (
            <Badge label="Reutilização" variant="warning" />
          )}
          {item.reuse_override === 'skip' && (
            <Badge label="Aguardar" variant="error" />
          )}
          {item.gameplay_source_id && (
            <View style={{
              flexDirection: 'row',
              alignItems: 'center',
              paddingHorizontal: 6,
              paddingVertical: 2,
              borderRadius: 12,
              borderWidth: 1,
              borderColor: 'rgba(168,85,247,0.3)',
              backgroundColor: 'rgba(168,85,247,0.1)',
            }}>
              <Text style={{ fontSize: 10, color: '#c084fc' }} numberOfLines={1}>
                🎮 {shortSourceName(item.gameplay_source_filename)}
              </Text>
            </View>
          )}
        </View>
        <Text style={styles.queueItemTitle} numberOfLines={1}>{item.title}</Text>
        {item.content && (
          <Text style={styles.queueItemContent} numberOfLines={2}>{item.content}</Text>
        )}
      </View>

      {/* Remove */}
      <TouchableOpacity onPress={onRemove} style={styles.queueRemoveBtn}>
        <Icon name="close" size={16} color={colors.textMuted} />
      </TouchableOpacity>
    </View>
  );
}

function IdeaCard({
  item,
  inQueue,
  onReject,
  onAddToQueue,
  onRemoveFromQueue,
  scoreColor,
}: {
  item: any;
  inQueue: boolean;
  onReject: () => void;
  onAddToQueue: () => void;
  onRemoveFromQueue: () => void;
  scoreColor: (s: number) => string;
}) {
  return (
    <Card padding={spacing.md} style={inQueue ? styles.inQueueCard : undefined}>
      <View style={styles.ideaCardHeader}>
        <View style={styles.ideaBadges}>
          <Badge label={TYPE_LABELS[item.item_type] || item.item_type} variant={TYPE_BADGE[item.item_type] || 'default'} />
          <Badge label={STATUS_LABELS[item.status] || item.status} variant={STATUS_BADGE[item.status] || 'default'} />
        </View>
        {item.editorial_score > 0 && (
          <Text style={[styles.score, { color: scoreColor(item.editorial_score) }]}>
            Score: {item.editorial_score.toFixed(0)}
          </Text>
        )}
      </View>

      <Text style={styles.ideaTitle}>{item.title}</Text>
      {item.content && <Text style={styles.ideaContent} numberOfLines={3}>{item.content}</Text>}

      <View style={styles.ideaMeta}>
        {item.game_name && <Text style={styles.ideaMetaText}>{item.game_name}</Text>}
        {item.source_name && <Text style={styles.ideaMetaText}>via {item.source_name}</Text>}
        {item.source_url && (
          <TouchableOpacity onPress={() => Linking.openURL(item.source_url)}>
            <Text style={styles.ideaSourceLink}>fonte ↗</Text>
          </TouchableOpacity>
        )}
        <Text style={styles.ideaMetaText}>{fmtDate(item.collected_at)}</Text>
      </View>

      <View style={styles.ideaActions}>
        {item.status === 'fresh' && !inQueue && (
          <Button title="+ Fila" size="sm" variant="primary" onPress={onAddToQueue} />
        )}
        {inQueue && (
          <Button title="Na Fila ✓" size="sm" variant="outline" onPress={onRemoveFromQueue} />
        )}
        {item.status === 'fresh' && (
          <Button title="Rejeitar" size="sm" variant="danger" onPress={onReject} />
        )}
      </View>
    </Card>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    gap: spacing.sm,
  },
  title: { fontSize: fontSize.xxxl, fontWeight: fontWeight.bold, color: colors.text },
  subtitle: { fontSize: fontSize.sm, color: colors.textMuted, marginTop: 2 },

  // Stats
  statChip: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    alignItems: 'center',
    minWidth: 70,
  },
  statValue: { fontSize: fontSize.lg, fontWeight: fontWeight.bold, color: colors.text },
  statLabel: { fontSize: fontSize.xs, color: colors.textMuted, marginTop: 2 },

  // Focus bar
  focusBar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  focusLabel: { fontSize: fontSize.xs, color: colors.textMuted },
  focusValue: { fontSize: fontSize.sm, fontWeight: fontWeight.medium, color: colors.text, marginTop: 2 },
  focusBtn: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
  },
  focusBtnText: { fontSize: fontSize.xs, color: colors.textSecondary },
  focusRemoveBtn: {
    borderWidth: 1,
    borderColor: 'rgba(239,68,68,0.3)',
    borderRadius: radius.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
  },
  focusRemoveText: { fontSize: fontSize.xs, color: colors.error },
  focusSetBtn: {
    borderWidth: 1,
    borderColor: 'rgba(245,158,11,0.3)',
    backgroundColor: 'rgba(245,158,11,0.1)',
    borderRadius: radius.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
  },
  focusSetText: { fontSize: fontSize.xs, color: colors.accentWarm, fontWeight: fontWeight.medium },

  // Processing
  processingCard: {
    borderWidth: 1,
    borderColor: 'rgba(45,212,191,0.3)',
    backgroundColor: 'rgba(45,212,191,0.05)',
    borderRadius: radius.md,
    padding: spacing.md,
  },
  processingHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    marginBottom: spacing.sm,
  },
  pulseDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: colors.accent,
  },
  processingTitle: { fontSize: fontSize.md, fontWeight: fontWeight.semibold, color: colors.text },
  processingJobId: { fontSize: fontSize.sm, color: colors.textMuted },
  processingBody: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: 'rgba(45,212,191,0.15)',
    padding: spacing.sm,
  },
  processingIcon: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(45,212,191,0.15)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  processingItem: { fontSize: fontSize.sm, fontWeight: fontWeight.medium, color: colors.text },
  processingMeta: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, marginTop: spacing.xs },
  processingStage: { fontSize: fontSize.xs, color: colors.accent, fontWeight: fontWeight.medium },
  progressLabel: { fontSize: 10, color: colors.textMuted },
  progressValue: { fontSize: fontSize.sm, fontWeight: fontWeight.medium, color: colors.accent },

  // Queue
  queueSection: {
    borderWidth: 1,
    borderColor: 'rgba(168,85,247,0.3)',
    backgroundColor: 'rgba(168,85,247,0.05)',
    borderRadius: radius.md,
    padding: spacing.md,
  },
  queueHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    marginBottom: spacing.sm,
  },
  queueTitle: { fontSize: fontSize.md, fontWeight: fontWeight.semibold, color: colors.text },
  queueCount: { fontSize: fontSize.sm, color: colors.textMuted },
  queueHint: { fontSize: 10, color: colors.textMuted, marginTop: spacing.sm, lineHeight: 16 },

  queueCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.sm,
  },
  queueOrderCol: {
    alignItems: 'center',
    gap: 2,
  },
  orderBtn: {
    padding: 2,
  },
  orderBtnDisabled: {
    opacity: 0.3,
  },
  queueNumber: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: 'rgba(168,85,247,0.2)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  queueNumberText: {
    fontSize: fontSize.xs,
    fontWeight: fontWeight.bold,
    color: '#a855f7',
  },
  queueItemBadges: {
    flexDirection: 'row',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: spacing.xs,
    marginBottom: spacing.xs,
  },
  gameEditBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    borderWidth: 1,
    borderColor: 'rgba(45,212,191,0.3)',
    backgroundColor: 'rgba(45,212,191,0.1)',
    borderRadius: radius.sm,
    paddingHorizontal: spacing.xs,
    paddingVertical: 2,
  },
  gameEditText: { fontSize: 10, color: colors.accent },
  gameSetBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    borderWidth: 1,
    borderStyle: 'dashed',
    borderColor: colors.border,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.xs,
    paddingVertical: 2,
  },
  gameSetText: { fontSize: 10, color: colors.textMuted },
  queueItemTitle: { fontSize: fontSize.sm, fontWeight: fontWeight.medium, color: colors.text },
  queueItemContent: { fontSize: 10, color: colors.textMuted, marginTop: 2 },
  queueRemoveBtn: {
    padding: spacing.xs,
  },

  // Filters
  filterLabel: { fontSize: fontSize.xs, fontWeight: fontWeight.medium, color: colors.textSecondary },
  searchInput: {
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    fontSize: fontSize.sm,
    color: colors.text,
  },
  gameplayToggle: {
    flexDirection: 'row' as const,
    alignItems: 'center' as const,
    gap: spacing.xs,
    marginTop: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    alignSelf: 'flex-start',
  },
  gameplayToggleActive: {
    borderColor: colors.accent + '60',
    backgroundColor: colors.accent + '15',
  },
  gameplayToggleText: {
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
    color: colors.textMuted,
  },
  gameplayToggleTextActive: {
    color: colors.accent,
  },
  chip: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radius.full,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  chipActive: {
    backgroundColor: 'rgba(45,212,191,0.1)',
    borderColor: 'rgba(45,212,191,0.3)',
  },
  chipText: { fontSize: fontSize.sm, color: colors.textSecondary },
  chipTextActive: { color: colors.accent, fontWeight: fontWeight.medium },

  // Idea cards
  inQueueCard: {
    borderColor: 'rgba(168,85,247,0.4)',
    backgroundColor: 'rgba(168,85,247,0.05)',
  },
  ideaCardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: spacing.sm,
  },
  ideaBadges: {
    flexDirection: 'row',
    gap: spacing.xs,
    flexWrap: 'wrap',
  },
  score: { fontSize: fontSize.xs, fontWeight: fontWeight.bold },
  ideaTitle: { fontSize: fontSize.base, fontWeight: fontWeight.semibold, color: colors.text },
  ideaContent: { fontSize: fontSize.sm, color: colors.textSecondary, marginTop: spacing.xs },
  ideaMeta: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.md,
    marginTop: spacing.sm,
  },
  ideaMetaText: { fontSize: 10, color: colors.textMuted },
  ideaSourceLink: { fontSize: 10, color: colors.accent, fontWeight: fontWeight.medium },
  ideaActions: {
    flexDirection: 'row',
    gap: spacing.sm,
    marginTop: spacing.md,
  },

  // Modal common
  modalContainer: { flex: 1, backgroundColor: colors.bg },
  modalHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  closeButton: { fontSize: fontSize.base, color: colors.accent },
  modalTitle: { fontSize: fontSize.md, fontWeight: fontWeight.semibold, color: colors.text },
  modalItemTitle: { fontSize: fontSize.base, fontWeight: fontWeight.medium, color: colors.text },
  modalLabel: { fontSize: fontSize.sm, fontWeight: fontWeight.medium, color: colors.textSecondary },
  muted: { fontSize: fontSize.sm, color: colors.textMuted },
  textInput: {
    height: 44,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    fontSize: fontSize.base,
    color: colors.text,
    marginTop: spacing.xs,
  },
  textArea: {
    minHeight: 100,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    fontSize: fontSize.base,
    color: colors.text,
    marginTop: spacing.xs,
  },

  // Picker
  pickerWrap: {
    gap: spacing.xs,
    marginTop: spacing.xs,
  },
  pickerOption: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  pickerOptionActive: {
    borderColor: colors.accent,
    backgroundColor: 'rgba(45,212,191,0.05)',
  },
  pickerOptionText: { fontSize: fontSize.sm, color: colors.textSecondary, flex: 1 },
  pickerOptionTextActive: { color: colors.text },

  // Reuse override
  reuseBox: {
    borderWidth: 1,
    borderColor: 'rgba(245,158,11,0.3)',
    backgroundColor: 'rgba(245,158,11,0.05)',
    borderRadius: radius.md,
    padding: spacing.md,
    gap: spacing.sm,
  },
  reuseTitle: { fontSize: fontSize.sm, color: colors.accentWarm, fontWeight: fontWeight.medium },
  radioRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  radioText: { fontSize: fontSize.sm, color: colors.text, flex: 1 },

  // Focus modal
  focusTypeRow: {
    flexDirection: 'row',
    gap: spacing.sm,
    marginTop: spacing.xs,
  },
  focusTypeBtn: {
    flex: 1,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingVertical: spacing.sm,
    alignItems: 'center',
  },
  focusTypeBtnActive: {
    borderColor: colors.accentWarm,
    backgroundColor: 'rgba(245,158,11,0.1)',
  },
  focusTypeText: { fontSize: fontSize.sm, color: colors.textMuted },
  focusTypeTextActive: { color: colors.accentWarm, fontWeight: fontWeight.medium },
  searchResults: {
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    marginTop: spacing.xs,
    maxHeight: 200,
  },
  searchResult: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  searchResultText: { fontSize: fontSize.sm, color: colors.text, flex: 1 },
  searchResultYear: { fontSize: fontSize.xs, color: colors.textMuted },
  selectedGame: { fontSize: fontSize.xs, color: colors.success, marginTop: spacing.xs },
  itemTypesRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
    marginTop: spacing.xs,
  },
  itemTypeChip: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.full,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  itemTypeChipActive: {
    borderColor: colors.accentWarm,
    backgroundColor: 'rgba(245,158,11,0.1)',
  },
  itemTypeText: { fontSize: fontSize.xs, color: colors.textMuted },
  itemTypeTextActive: { color: colors.accentWarm, fontWeight: fontWeight.medium },
});
