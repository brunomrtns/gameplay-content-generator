import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  RefreshControl,
  TouchableOpacity,
  Modal,
  TextInput,
  FlatList,
} from 'react-native';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useLiveData } from '../hooks/useLiveData';
import { SafeAreaView } from 'react-native-safe-area-context';
import Icon from 'react-native-vector-icons/MaterialCommunityIcons';
import { kidsApi, jobsApi } from '../api/endpoints';
import { Card, Badge, Button, EmptyState, Spinner } from '../components/ui';
import { colors } from '../theme/colors';
import { fontSize, fontWeight, radius, spacing } from '../theme/spacing';
import { fmtDate } from '../utils/format';
import Toast from 'react-native-toast-message';

// ── Constants (match web) ────────────────────────────────────────────────────

const TOPIC_LIBRARY_CATEGORIES = [
  { value: 'animals', label: 'Animais' },
  { value: 'science', label: 'Ciência' },
  { value: 'space', label: 'Espaço' },
  { value: 'dinosaurs', label: 'Dinossauros' },
  { value: 'nature', label: 'Natureza' },
  { value: 'ocean', label: 'Oceano' },
  { value: 'human_body', label: 'Corpo Humano' },
  { value: 'history', label: 'História' },
  { value: 'geography', label: 'Geografia' },
  { value: 'vehicles', label: 'Veículos' },
  { value: 'food', label: 'Comida' },
  { value: 'colors', label: 'Cores' },
  { value: 'numbers', label: 'Números' },
  { value: 'curiosity', label: 'Curiosidades' },
];

const IDEA_STATUS_LABELS: Record<string, string> = {
  discovered: 'Descoberta',
  evaluated: 'Avaliada',
  queued: 'Na Fila',
  converted: 'Convertida',
  rejected: 'Rejeitada',
  expired: 'Expirada',
};

const IDEA_STATUS_BADGE: Record<string, any> = {
  discovered: 'info',
  evaluated: 'success',
  queued: 'default',
  converted: 'success',
  rejected: 'error',
  expired: 'default',
};

const IDEA_SOURCE_LABELS: Record<string, string> = {
  ai_ideation: 'IA',
  topic_library: 'Biblioteca',
  seasonal: 'Sazonal',
  manual: 'Manual',
};

// ── Main Screen ──────────────────────────────────────────────────────────────

export function KidsIdeasScreen({ navigation }: { navigation?: any }) {
  const queryClient = useQueryClient();
  const [filterStatus, setFilterStatus] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [showDiscover, setShowDiscover] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [newCategory, setNewCategory] = useState('general');
  const [creating, setCreating] = useState(false);
  const [discoverCategories, setDiscoverCategories] = useState<string[]>([]);
  const [discoverCount, setDiscoverCount] = useState(3);
  const [discovering, setDiscovering] = useState(false);
  const [scoring, setScoring] = useState<Record<number, number | null>>({});
  const [reconciling, setReconciling] = useState(false);

  const { data: ideasData, isLoading, refetch, isRefetching } = useLiveData(
    ['kids-ideas', filterStatus],
    () => kidsApi.listIdeas({ status: filterStatus || undefined, limit: 100 }),
    ['kids_idea.updated']
  );

  const { data: queueData, refetch: refetchQueue } = useLiveData(
    ['kids-idea-queue'],
    kidsApi.getIdeaQueue,
    ['idea_queue.updated']
  );

  const { data: stats } = useQuery({
    queryKey: ['kids-idea-stats'],
    queryFn: kidsApi.getIdeaStats,
  });

  const ideas = ideasData?.ideas || [];
  const queue = queueData?.items || [];
  const queueIds = new Set(queue.map((q: any) => q.id));

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ['kids-ideas'] });
    queryClient.invalidateQueries({ queryKey: ['kids-idea-queue'] });
    queryClient.invalidateQueries({ queryKey: ['kids-idea-stats'] });
  };

  // ── Handlers ────────────────────────────────────────────────────────────

  const handleDiscover = async () => {
    setDiscovering(true);
    try {
      const result = await kidsApi.discoverIdeas({
        categories: discoverCategories.length > 0 ? discoverCategories : undefined,
        ideas_per_category: discoverCount,
        include_seasonal: true,
        include_topic_library: true,
      });
      Toast.show({
        type: 'info',
        text1: `Job #${result.job_id} na fila`,
        text2: 'O worker vai processar a descoberta',
      });
      setShowDiscover(false);
      setTimeout(() => invalidateAll(), 5000);
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro na descoberta' });
    } finally {
      setDiscovering(false);
    }
  };

  const handleScore = async (id: number) => {
    try {
      const result = await kidsApi.scoreIdea(id);
      const jobId = result.job_id;
      if (!jobId) {
        Toast.show({ type: 'error', text1: 'Erro ao agendar avaliação' });
        return;
      }
      setScoring((prev) => ({ ...prev, [id]: jobId }));
      Toast.show({ type: 'info', text1: `Avaliação na fila (job #${jobId})` });
      // Poll for completion
      const pollJob = async () => {
        try {
          const job = await jobsApi.get(jobId);
          if (job.status === 'completed') {
            Toast.show({ type: 'success', text1: 'Ideia avaliada' });
            setScoring((prev) => ({ ...prev, [id]: null }));
            invalidateAll();
          } else if (job.status === 'failed') {
            Toast.show({ type: 'error', text1: 'Falha na avaliação' });
            setScoring((prev) => ({ ...prev, [id]: null }));
          } else {
            setTimeout(pollJob, 5000);
          }
        } catch { /* ignore */ }
      };
      setTimeout(pollJob, 5000);
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro ao avaliar' });
    }
  };

  const handleReject = async (id: number) => {
    try {
      await kidsApi.rejectIdea(id);
      Toast.show({ type: 'success', text1: 'Ideia rejeitada' });
      invalidateAll();
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    }
  };

  const handleProduce = async (id: number) => {
    try {
      const result = await kidsApi.produceIdea(id);
      Toast.show({
        type: 'success',
        text1: `Job #${result.job_id} criado!`,
        text2: `Tópico #${result.topic_id}`,
      });
      invalidateAll();
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro ao produzir' });
    }
  };

  const handleCreateIdea = async () => {
    if (!newTitle.trim()) {
      Toast.show({ type: 'error', text1: 'Título é obrigatório' });
      return;
    }
    setCreating(true);
    try {
      await kidsApi.createIdea({
        title: newTitle.trim(),
        content: newDesc.trim(),
        description: newDesc.trim(),
        category: newCategory,
      });
      Toast.show({ type: 'success', text1: 'Ideia criada' });
      setShowCreate(false);
      setNewTitle('');
      setNewDesc('');
      setNewCategory('general');
      invalidateAll();
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    } finally {
      setCreating(false);
    }
  };

  const handleAddToQueue = async (id: number) => {
    try {
      await kidsApi.addToIdeaQueue(id);
      Toast.show({ type: 'success', text1: 'Adicionada à fila' });
      invalidateAll();
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    }
  };

  const handleRemoveFromQueue = async (id: number) => {
    try {
      await kidsApi.removeFromIdeaQueue(id);
      Toast.show({ type: 'success', text1: 'Removida da fila' });
      invalidateAll();
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    }
  };

  const handleReconcile = async () => {
    setReconciling(true);
    try {
      await kidsApi.reconcileIdeaQueue();
      Toast.show({ type: 'success', text1: 'Fila reconciliada' });
      invalidateAll();
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    } finally {
      setReconciling(false);
    }
  };

  const handleMoveQueue = async (index: number, direction: 'up' | 'down') => {
    const newQueue = [...queue];
    const swapIndex = direction === 'up' ? index - 1 : index + 1;
    if (swapIndex < 0 || swapIndex >= newQueue.length) return;
    [newQueue[index], newQueue[swapIndex]] = [newQueue[swapIndex], newQueue[index]];
    try {
      await kidsApi.reorderIdeaQueue(newQueue.map((q: any) => q.id));
      refetchQueue();
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
      refetchQueue();
    }
  };

  const scoreColor = (score: number) => {
    if (score >= 70) return colors.success;
    if (score >= 50) return colors.warning;
    if (score >= 30) return colors.accentWarm;
    return colors.error;
  };

  const statusFilters = [
    { value: '', label: 'Todos' },
    { value: 'discovered', label: 'Descobertas' },
    { value: 'evaluated', label: 'Avaliadas' },
    { value: 'queued', label: 'Na Fila' },
    { value: 'converted', label: 'Convertidas' },
    { value: 'rejected', label: 'Rejeitadas' },
  ];

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        {navigation && (
          <TouchableOpacity onPress={() => navigation.goBack()} style={styles.backBtn}>
            <Icon name="arrow-left" size={24} color={colors.text} />
          </TouchableOpacity>
        )}
        <View style={{ flex: 1 }}>
          <Text style={styles.headerTitle}>Ideias Kids</Text>
          <Text style={styles.headerSubtitle}>Descubra, avalie e produza vídeos educativos</Text>
        </View>
        <Button title="+ Nova" variant="outline" size="sm" onPress={() => setShowCreate(true)} />
        <Button
          title={discovering ? '...' : 'Descobrir'}
          variant="primary"
          size="sm"
          onPress={() => setShowDiscover(true)}
          loading={discovering}
        />
      </View>

      <FlatList
        data={ideas}
        keyExtractor={(i) => String(i.id)}
        refreshControl={
          <RefreshControl
            refreshing={isRefetching}
            onRefresh={() => { refetch(); refetchQueue(); }}
            tintColor={colors.accent}
          />
        }
        contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}
        ListHeaderComponent={
          <View style={{ gap: spacing.md }}>
            {/* Stats */}
            {stats && (
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: spacing.sm }}>
                <StatChip label="Total" value={ideas.length} />
                <StatChip label="Na Fila" value={queue.length} color="#a855f7" />
                <StatChip label="Avaliadas" value={ideas.filter((i: any) => i.status === 'evaluated').length} color={colors.success} />
                <StatChip label="Descobertas" value={ideas.filter((i: any) => i.status === 'discovered').length} color={colors.info} />
              </ScrollView>
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
                  <View style={{ flex: 1 }} />
                  <TouchableOpacity onPress={handleReconcile} disabled={reconciling} style={styles.reconcileBtn}>
                    <Icon name="refresh" size={12} color={reconciling ? colors.textMuted : colors.textSecondary} />
                    <Text style={styles.reconcileText}>Reconciliar</Text>
                  </TouchableOpacity>
                </View>
                <View style={{ gap: spacing.sm }}>
                  {queue.map((item: any, index: number) => (
                    <QueueCard
                      key={item.id}
                      item={item}
                      index={index}
                      total={queue.length}
                      onMoveUp={() => handleMoveQueue(index, 'up')}
                      onMoveDown={() => handleMoveQueue(index, 'down')}
                      onRemove={() => handleRemoveFromQueue(item.id)}
                      scoreColor={scoreColor}
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
              <Text style={styles.filterLabel}>Status</Text>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: spacing.sm, paddingVertical: spacing.xs }}>
                {statusFilters.map((f) => (
                  <FilterChip key={f.value} label={f.label} active={filterStatus === f.value} onPress={() => setFilterStatus(f.value)} />
                ))}
              </ScrollView>
            </View>
          </View>
        }
        ListEmptyComponent={
          isLoading ? (
            <View style={{ paddingVertical: spacing.xxl, alignItems: 'center' }}>
              <Spinner size="large" />
            </View>
          ) : (
            <Card>
              <EmptyState
                icon={<Icon name="lightbulb-outline" size={40} color={colors.textMuted} />}
                title="Nenhuma ideia"
                description="Clique em Descobrir para gerar ideias via IA, biblioteca de tópicos e calendário sazonal."
              />
            </Card>
          )
        }
        renderItem={({ item: idea }) => (
          <IdeaCard
            idea={idea}
            inQueue={queueIds.has(idea.id)}
            scoring={!!scoring[idea.id]}
            onScore={() => handleScore(idea.id)}
            onReject={() => handleReject(idea.id)}
            onProduce={() => handleProduce(idea.id)}
            onAddToQueue={() => handleAddToQueue(idea.id)}
            onRemoveFromQueue={() => handleRemoveFromQueue(idea.id)}
            scoreColor={scoreColor}
          />
        )}
      />

      {/* ── Create Idea Modal ────────────────────────────────────────────── */}
      <Modal visible={showCreate} animationType="slide" presentationStyle="pageSheet" onRequestClose={() => setShowCreate(false)}>
        <SafeAreaView style={styles.modalContainer} edges={['top']}>
          <View style={styles.modalHeader}>
            <TouchableOpacity onPress={() => setShowCreate(false)}>
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
                value={newTitle}
                onChangeText={setNewTitle}
                placeholder="Título da ideia"
                placeholderTextColor={colors.textMuted}
              />
            </View>
            <View>
              <Text style={styles.modalLabel}>Descrição (opcional)</Text>
              <TextInput
                style={styles.textArea}
                value={newDesc}
                onChangeText={setNewDesc}
                placeholder="Descrição da ideia..."
                placeholderTextColor={colors.textMuted}
                multiline
                numberOfLines={3}
                textAlignVertical="top"
              />
            </View>
            <View>
              <Text style={styles.modalLabel}>Categoria</Text>
              <View style={styles.categoriesWrap}>
                {TOPIC_LIBRARY_CATEGORIES.map((c) => (
                  <TouchableOpacity
                    key={c.value}
                    style={[styles.categoryChip, newCategory === c.value && styles.categoryChipActive]}
                    onPress={() => setNewCategory(c.value)}
                  >
                    <Text style={[styles.categoryText, newCategory === c.value && styles.categoryTextActive]}>
                      {c.label}
                    </Text>
                  </TouchableOpacity>
                ))}
                <TouchableOpacity
                  style={[styles.categoryChip, newCategory === 'general' && styles.categoryChipActive]}
                  onPress={() => setNewCategory('general')}
                >
                  <Text style={[styles.categoryText, newCategory === 'general' && styles.categoryTextActive]}>
                    Geral
                  </Text>
                </TouchableOpacity>
              </View>
            </View>
            <Button title="Criar Ideia" variant="primary" fullWidth onPress={handleCreateIdea} loading={creating} disabled={!newTitle.trim()} />
          </ScrollView>
        </SafeAreaView>
      </Modal>

      {/* ── Discover Modal ───────────────────────────────────────────────── */}
      <Modal visible={showDiscover} animationType="slide" presentationStyle="pageSheet" onRequestClose={() => setShowDiscover(false)}>
        <SafeAreaView style={styles.modalContainer} edges={['top']}>
          <View style={styles.modalHeader}>
            <TouchableOpacity onPress={() => setShowDiscover(false)}>
              <Text style={styles.closeButton}>Cancelar</Text>
            </TouchableOpacity>
            <Text style={styles.modalTitle}>Descoberta de Ideias</Text>
            <View style={{ width: 60 }} />
          </View>
          <ScrollView contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
            <Text style={styles.muted}>
              A IA vai gerar ideias baseadas nas categorias selecionadas, na biblioteca de tópicos e no calendário sazonal.
            </Text>
            <View>
              <Text style={styles.modalLabel}>Categorias (vazio = todas)</Text>
              <View style={styles.categoriesWrap}>
                {TOPIC_LIBRARY_CATEGORIES.map((c) => (
                  <TouchableOpacity
                    key={c.value}
                    style={[styles.categoryChip, discoverCategories.includes(c.value) && styles.categoryChipActive]}
                    onPress={() => {
                      setDiscoverCategories((prev) =>
                        prev.includes(c.value) ? prev.filter((v) => v !== c.value) : [...prev, c.value],
                      );
                    }}
                  >
                    <Text style={[styles.categoryText, discoverCategories.includes(c.value) && styles.categoryTextActive]}>
                      {c.label}
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>
            </View>
            <View>
              <Text style={styles.modalLabel}>Ideias por categoria: {discoverCount}</Text>
              <View style={styles.countRow}>
                {[1, 2, 3, 5, 8, 10].map((n) => (
                  <TouchableOpacity
                    key={n}
                    style={[styles.countChip, discoverCount === n && styles.countChipActive]}
                    onPress={() => setDiscoverCount(n)}
                  >
                    <Text style={[styles.countText, discoverCount === n && styles.countTextActive]}>{n}</Text>
                  </TouchableOpacity>
                ))}
              </View>
            </View>
            <Button title="Descobrir" variant="primary" fullWidth onPress={handleDiscover} loading={discovering} />
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
    <TouchableOpacity style={[styles.chip, active && styles.chipActive]} onPress={onPress}>
      <Text style={[styles.chipText, active && styles.chipTextActive]}>{label}</Text>
    </TouchableOpacity>
  );
}

function QueueCard({
  item,
  index,
  total,
  onMoveUp,
  onMoveDown,
  onRemove,
  scoreColor,
}: {
  item: any;
  index: number;
  total: number;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onRemove: () => void;
  scoreColor: (s: number) => string;
}) {
  return (
    <View style={styles.queueCard}>
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
      <View style={{ flex: 1 }}>
        <View style={styles.queueItemBadges}>
          {item.category && <Badge label={item.category} variant="default" />}
          {item.editorial_score != null && (
            <Text style={[styles.scoreText, { color: scoreColor(item.editorial_score) }]}>
              Score: {item.editorial_score.toFixed(0)}
            </Text>
          )}
        </View>
        <Text style={styles.queueItemTitle} numberOfLines={1}>{item.title}</Text>
      </View>
      <TouchableOpacity onPress={onRemove} style={styles.queueRemoveBtn}>
        <Icon name="close" size={16} color={colors.textMuted} />
      </TouchableOpacity>
    </View>
  );
}

function IdeaCard({
  idea,
  inQueue,
  scoring,
  onScore,
  onReject,
  onProduce,
  onAddToQueue,
  onRemoveFromQueue,
  scoreColor,
}: {
  idea: any;
  inQueue: boolean;
  scoring: boolean;
  onScore: () => void;
  onReject: () => void;
  onProduce: () => void;
  onAddToQueue: () => void;
  onRemoveFromQueue: () => void;
  scoreColor: (s: number) => string;
}) {
  return (
    <Card padding={spacing.md} style={inQueue ? styles.inQueueCard : undefined}>
      <View style={styles.ideaBadges}>
        <Badge label={IDEA_STATUS_LABELS[idea.status] || idea.status} variant={IDEA_STATUS_BADGE[idea.status] || 'default'} />
        <Badge label={IDEA_SOURCE_LABELS[idea.source] || idea.source} variant="default" />
        {idea.category && <Badge label={idea.category} variant="default" />}
        {idea.editorial_score != null && (
          <Text style={[styles.scoreText, { color: scoreColor(idea.editorial_score) }]}>
            Score: {idea.editorial_score.toFixed(0)}
          </Text>
        )}
        {idea.safety_score != null && (
          <Text style={[styles.safetyText, { color: idea.safety_score >= 0.7 ? colors.success : colors.error }]}>
            Safety: {(idea.safety_score * 100).toFixed(0)}%
          </Text>
        )}
      </View>

      <Text style={styles.ideaTitle}>{idea.title}</Text>
      {idea.description && <Text style={styles.ideaDesc} numberOfLines={3}>{idea.description}</Text>}
      {idea.safety_flags?.length > 0 && (
        <Text style={styles.safetyFlags}>Flags: {idea.safety_flags.join(', ')}</Text>
      )}

      <View style={styles.ideaMeta}>
        <Text style={styles.ideaMetaText}>{fmtDate(idea.created_at)}</Text>
      </View>

      <View style={styles.ideaActions}>
        {idea.status === 'discovered' && (
          <Button title={scoring ? 'Avaliando...' : 'Avaliar'} size="sm" variant="primary" onPress={onScore} loading={scoring} />
        )}
        {idea.status === 'evaluated' && !inQueue && (
          <Button title="+ Fila" size="sm" variant="primary" onPress={onAddToQueue} />
        )}
        {inQueue && (
          <Button title="Na Fila ✓" size="sm" variant="outline" onPress={onRemoveFromQueue} />
        )}
        {(idea.status === 'evaluated' || idea.status === 'converted') && (
          <Button title="Produzir" size="sm" variant="outline" onPress={onProduce} />
        )}
        {(idea.status === 'discovered' || idea.status === 'evaluated' || idea.status === 'queued') && (
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
  backBtn: { padding: spacing.xs },
  headerTitle: { fontSize: fontSize.xxxl, fontWeight: fontWeight.bold, color: colors.text },
  headerSubtitle: { fontSize: fontSize.sm, color: colors.textMuted, marginTop: 2 },

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
  reconcileBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
  },
  reconcileText: { fontSize: 10, color: colors.textSecondary },
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
  queueOrderCol: { alignItems: 'center', gap: 2 },
  orderBtn: { padding: 2 },
  orderBtnDisabled: { opacity: 0.3 },
  queueNumber: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: 'rgba(168,85,247,0.2)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  queueNumberText: { fontSize: fontSize.xs, fontWeight: fontWeight.bold, color: '#a855f7' },
  queueItemBadges: {
    flexDirection: 'row',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: spacing.xs,
    marginBottom: spacing.xs,
  },
  queueItemTitle: { fontSize: fontSize.sm, fontWeight: fontWeight.medium, color: colors.text },
  queueRemoveBtn: { padding: spacing.xs },

  // Filters
  filterLabel: { fontSize: fontSize.xs, fontWeight: fontWeight.medium, color: colors.textSecondary },
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

  // Idea card
  inQueueCard: {
    borderColor: 'rgba(168,85,247,0.4)',
    backgroundColor: 'rgba(168,85,247,0.05)',
  },
  ideaBadges: {
    flexDirection: 'row',
    gap: spacing.xs,
    flexWrap: 'wrap',
    marginBottom: spacing.sm,
  },
  scoreText: { fontSize: fontSize.xs, fontWeight: fontWeight.bold },
  safetyText: { fontSize: 10, fontWeight: fontWeight.medium },
  ideaTitle: { fontSize: fontSize.base, fontWeight: fontWeight.semibold, color: colors.text },
  ideaDesc: { fontSize: fontSize.sm, color: colors.textSecondary, marginTop: spacing.xs },
  safetyFlags: { fontSize: fontSize.xs, color: colors.error, marginTop: spacing.xs },
  ideaMeta: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.md,
    marginTop: spacing.sm,
  },
  ideaMetaText: { fontSize: 10, color: colors.textMuted },
  ideaActions: {
    flexDirection: 'row',
    gap: spacing.sm,
    marginTop: spacing.md,
    flexWrap: 'wrap',
  },

  // Modal
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
    minHeight: 80,
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
  categoriesWrap: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
    marginTop: spacing.xs,
  },
  categoryChip: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  categoryChipActive: {
    borderColor: colors.accent,
    backgroundColor: 'rgba(45,212,191,0.1)',
  },
  categoryText: { fontSize: fontSize.xs, color: colors.textMuted },
  categoryTextActive: { color: colors.accent, fontWeight: fontWeight.medium },
  countRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
    marginTop: spacing.xs,
  },
  countChip: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  countChipActive: {
    borderColor: colors.accent,
    backgroundColor: 'rgba(45,212,191,0.1)',
  },
  countText: { fontSize: fontSize.sm, color: colors.textMuted },
  countTextActive: { color: colors.accent, fontWeight: fontWeight.medium },
});
