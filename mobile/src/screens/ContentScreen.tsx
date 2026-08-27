import React, { useState, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  RefreshControl,
  ScrollView,
  TouchableOpacity,
  Alert,
  TextInput,
  Modal,
} from 'react-native';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { SafeAreaView } from 'react-native-safe-area-context';
import DocumentPicker from 'react-native-document-picker';
import { gameplaysApi, channelApi, catalogApi } from '../api/endpoints';
import { Card, Badge, Button, EmptyState, Spinner, Toggle } from '../components/ui';
import Icon from 'react-native-vector-icons/MaterialCommunityIcons';
import { colors } from '../theme/colors';
import { fontSize, fontWeight, radius, spacing } from '../theme/spacing';
import { fmtDuration, fmtBytes } from '../utils/format';
import { useBackHandler } from '../hooks/useBackHandler';
import Toast from 'react-native-toast-message';

type Tab = 'media' | 'channel';

const STATUS_CONFIG: Record<string, { label: string; variant: any }> = {
  discovered: { label: 'Descoberto', variant: 'default' },
  probing: { label: 'Analisando', variant: 'info' },
  ready: { label: 'Pronto', variant: 'success' },
  error: { label: 'Erro', variant: 'error' },
};

const PROCESSING_CONFIG: Record<string, { label: string; variant: any }> = {
  uploading: { label: 'Enviando', variant: 'info' },
  uploaded: { label: 'Enviado', variant: 'default' },
  waiting_worker: { label: 'Aguardando worker', variant: 'default' },
  downloading: { label: 'Baixando', variant: 'info' },
  downloaded: { label: 'Baixado', variant: 'default' },
  mapping: { label: 'Mapeando', variant: 'info' },
  mapped: { label: 'Mapeado', variant: 'success' },
  ready: { label: 'Pronto', variant: 'success' },
};

export function ContentScreen() {
  const [tab, setTab] = useState<Tab>('media');

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <Text style={styles.title}>Conteúdo</Text>
      </View>

      {/* Tabs */}
      <View style={styles.tabBar}>
        <TouchableOpacity style={[styles.tab, tab === 'media' && styles.tabActive]} onPress={() => setTab('media')}>
          <Text style={[styles.tabText, tab === 'media' && styles.tabTextActive]}>Gravações</Text>
        </TouchableOpacity>
        <TouchableOpacity style={[styles.tab, tab === 'channel' && styles.tabActive]} onPress={() => setTab('channel')}>
          <Text style={[styles.tabText, tab === 'channel' && styles.tabTextActive]}>Identidade do Canal</Text>
        </TouchableOpacity>
      </View>

      {tab === 'media' ? <MediaTab /> : <ChannelTab />}
    </SafeAreaView>
  );
}

function MediaTab() {
  const queryClient = useQueryClient();
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [scanning, setScanning] = useState(false);
  const [searchModal, setSearchModal] = useState<number | null>(null);

  const { data: allSources, refetch, isRefetching, isLoading, error } = useQuery({
    queryKey: ['gameplays'],
    queryFn: () => gameplaysApi.list(true),
    refetchInterval: 5000,
  });

  const sources = (allSources || []).filter((s: any) => s.is_own !== false);
  const publicSources = (allSources || []).filter((s: any) => s.is_own === false);

  const handleUpload = async () => {
    try {
      const result = await DocumentPicker.pick({
        type: [DocumentPicker.types.video],
        allowMultiSelection: true,
      });
      setUploading(true);
      setUploadProgress(0);
      for (const file of result) {
        await gameplaysApi.upload(
          { uri: file.uri, name: file.name ?? 'file', type: file.type ?? 'video/mp4' },
          (pct) => setUploadProgress(pct),
        );
      }
      Toast.show({ type: 'success', text1: `${result.length} arquivo(s) enviado(s)` });
      queryClient.invalidateQueries({ queryKey: ['gameplays'] });
    } catch (err: any) {
      if (!DocumentPicker.isCancel(err)) {
        Toast.show({ type: 'error', text1: err.message || 'Erro no upload' });
      }
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
  };

  const handleScan = async () => {
    setScanning(true);
    try {
      const r = await gameplaysApi.scanInbox();
      Toast.show({ type: 'success', text1: `${r.discovered} arquivo(s) encontrado(s)` });
      queryClient.invalidateQueries({ queryKey: ['gameplays'] });
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    } finally {
      setScanning(false);
    }
  };

  const handleDelete = (s: any) => {
    Alert.alert(
      'Deletar gameplay?',
      `Tem certeza que deseja deletar "${s.filename}"?\n\nIsso vai remover TODOS os clips, eventos e arquivos físicos associados.`,
      [
        { text: 'Cancelar', style: 'cancel' },
        {
          text: 'Deletar',
          style: 'destructive',
          onPress: () => {
            Alert.alert('Confirmar novamente', 'Os arquivos no HD serão apagados permanentemente.', [
              { text: 'Cancelar', style: 'cancel' },
              {
                text: 'Deletar definitivamente',
                style: 'destructive',
                onPress: async () => {
                  try {
                    await gameplaysApi.delete(s.id);
                    Toast.show({ type: 'success', text1: 'Gameplay deletada' });
                    queryClient.invalidateQueries({ queryKey: ['gameplays'] });
                  } catch (err: any) {
                    Toast.show({ type: 'error', text1: err.message || 'Erro' });
                  }
                },
              },
            ]);
          },
        },
      ],
    );
  };

  const handleToggleEnabled = async (s: any) => {
    try {
      await gameplaysApi.toggleEnabled(s.id, !s.enabled);
      queryClient.invalidateQueries({ queryKey: ['gameplays'] });
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    }
  };

  const handleToggleVisibility = async (s: any) => {
    try {
      await gameplaysApi.toggleVisibility(s.id, !s.is_public);
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    }
  };

  const handleCreateMapping = async (s: any) => {
    try {
      await gameplaysApi.createMappingJob(s.id);
      Toast.show({ type: 'success', text1: 'Mapeamento solicitado' });
      queryClient.invalidateQueries({ queryKey: ['gameplays'] });
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    }
  };

  return (
    <ScrollView
      style={styles.tabContent}
      refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={() => { refetch(); }} tintColor={colors.accent} />}
    >
      {/* Upload zone */}
      <Card style={{ marginBottom: spacing.md }}>
        <View style={styles.rowBetween}>
          <Text style={styles.cardTitle}>Enviar gravações</Text>
          <Button title="Escanear inbox" variant="outline" size="sm" loading={scanning} onPress={handleScan} />
        </View>
        {uploading ? (
          <View style={styles.uploadProgress}>
            <Text style={styles.muted}>Enviando... {uploadProgress}%</Text>
            <View style={styles.progressBar}>
              <View style={[styles.progressFill, { width: `${uploadProgress}%` }]} />
            </View>
          </View>
        ) : (
          <Button title="Selecionar arquivos de gameplay" variant="primary" onPress={handleUpload} style={{ marginTop: spacing.md }} />
        )}
        <Text style={styles.hint}>MP4, MKV, MOV, AVI · Análise automática após o upload</Text>
      </Card>

      {/* Gameplays list */}
      <Text style={styles.sectionTitle}>Gravações ({sources.length})</Text>
      {isLoading ? (
        <View style={{ paddingVertical: spacing.xxl, alignItems: 'center' }}>
          <Spinner size="large" />
        </View>
      ) : error ? (
        <Card>
          <EmptyState title="Erro ao carregar" description="Toque para tentar novamente." action={<Button title="Tentar novamente" variant="outline" size="sm" onPress={() => refetch()} />} />
        </Card>
      ) : sources.length === 0 ? (
        <Card>
          <EmptyState title="Nenhuma gravação" description="Envie arquivos ou escaneie a pasta inbox." />
        </Card>
      ) : (
        <View style={{ gap: spacing.md, marginBottom: spacing.lg }}>
          {sources.map((s: any) => {
            const cfg = STATUS_CONFIG[s.ingestion_status] || STATUS_CONFIG.discovered;
            const isProcessing = ['discovered', 'probing'].includes(s.ingestion_status);
            const procCfg = PROCESSING_CONFIG[s.processing_status] || PROCESSING_CONFIG.uploaded;
            const isMapping = ['uploading', 'uploaded', 'waiting_worker', 'downloading', 'downloaded', 'mapping'].includes(s.processing_status);
            const canMap = s.ingestion_status === 'ready' && (s.processing_status === 'uploaded' || !s.processing_status);
            const isReady = s.processing_status === 'ready' || s.processing_status === 'mapped';
            return (
              <Card key={s.id} padding={spacing.md}>
                <View style={styles.sourceRow}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.filename} numberOfLines={1}>{s.filename}</Text>
                    <View style={styles.sourceMeta}>
                      <Text style={styles.metaText}>{fmtDuration(s.duration)}</Text>
                      {s.width > 0 && <Text style={styles.metaText}>{s.width}×{s.height}</Text>}
                      {s.file_size > 0 && <Text style={styles.metaText}>{fmtBytes(s.file_size)}</Text>}
                      {s.game_name && (
                        <TouchableOpacity onPress={() => setSearchModal(s.id)}>
                          <Badge label={s.game_name} variant="info" />
                        </TouchableOpacity>
                      )}
                      {isReady && s.event_count > 0 && (
                        <Text style={[styles.metaText, { color: colors.accent }]}>
                          {s.event_count} eventos
                        </Text>
                      )}
                    </View>
                  </View>
                  <View style={styles.sourceBadges}>
                    <Badge label={cfg.label} variant={cfg.variant} />
                    {s.processing_status && s.processing_status !== 'ready' && (
                      <Badge label={procCfg.label} variant={procCfg.variant} />
                    )}
                    {s.enabled === false && <Badge label="Estacionada" variant="warning" />}
                  </View>
                </View>

                {/* Progress bar during mapping/processing */}
                {isMapping && (
                  <View style={styles.mappingProgress}>
                    <View style={[styles.mappingProgressFill, { width: '50%' }]} />
                  </View>
                )}

                {/* Definir jogo button when no game is assigned and ready */}
                {!s.game_name && s.ingestion_status === 'ready' && !isMapping && (
                  <View style={{ marginTop: spacing.sm }}>
                    <Button
                      title="Definir jogo"
                      size="sm"
                      variant="outline"
                      icon={<Icon name="gamepad-variant" size={14} color={colors.accent} />}
                      onPress={() => setSearchModal(s.id)}
                    />
                  </View>
                )}

                {!isProcessing && !isMapping && (
                  <View style={styles.sourceActions}>
                    <View style={styles.toggleRow}>
                      <View style={styles.toggleItem}>
                        <Text style={styles.toggleLabel}>Disponível</Text>
                        <Toggle
                          checked={s.enabled !== false}
                          onChange={() => handleToggleEnabled(s)}
                        />
                      </View>
                      <View style={styles.toggleItem}>
                        <Text style={styles.toggleLabel}>Pública</Text>
                        <Toggle
                          checked={!!s.is_public}
                          onChange={() => handleToggleVisibility(s)}
                        />
                      </View>
                    </View>
                    <Button title="Deletar" size="sm" variant="danger" onPress={() => handleDelete(s)} style={{ marginTop: spacing.sm, alignSelf: 'flex-start' }} />
                  </View>
                )}

                {canMap && (
                  <View style={{ marginTop: spacing.sm }}>
                    <Button title="Solicitar mapeamento" size="sm" variant="outline" onPress={() => handleCreateMapping(s)} />
                  </View>
                )}

                {/* Mapping Timeline for ready gameplays with events */}
                {isReady && s.event_count > 0 && (
                  <MappingTimeline sourceId={s.id} filename={s.filename} />
                )}

                {s.error_message && <Text style={styles.errorText}>{s.error_message}</Text>}
              </Card>
            );
          })}
        </View>
      )}

      {/* Public gameplays */}
      {publicSources.length > 0 && (
        <View style={{ marginBottom: spacing.lg }}>
          <Text style={styles.sectionTitle}>Gameplays públicas da comunidade ({publicSources.length})</Text>
          {publicSources.map((s: any) => (
            <Card key={s.id} padding={spacing.md} style={{ marginBottom: spacing.sm, opacity: 0.8 }}>
              <View style={styles.sourceRow}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.filename} numberOfLines={1}>{s.filename}</Text>
                  <View style={styles.sourceMeta}>
                    <Text style={styles.metaText}>{fmtDuration(s.duration)}</Text>
                    {s.game_name && <Text style={styles.metaText}>{s.game_name}</Text>}
                  </View>
                </View>
                <Badge label="Pública" variant="default" />
              </View>
            </Card>
          ))}
        </View>
      )}

      <GameSearchModal
        visible={searchModal !== null}
        onClose={() => setSearchModal(null)}
        onSelect={async (game) => {
          if (searchModal === null) return;
          try {
            await gameplaysApi.assignGameByName(searchModal, game.name, game.slug);
            Toast.show({ type: 'success', text1: `Jogo: ${game.name}` });
            queryClient.invalidateQueries({ queryKey: ['gameplays'] });
          } catch (err: any) {
            Toast.show({ type: 'error', text1: err.message || 'Erro' });
          }
          setSearchModal(null);
        }}
      />
    </ScrollView>
  );
}

function GameSearchModal({ visible, onClose, onSelect }: { visible: boolean; onClose: () => void; onSelect: (game: any) => void }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<any[]>([]);
  const [searching, setSearching] = useState(false);

  useBackHandler(() => onClose(), visible);

  const search = async (q: string) => {
    setQuery(q);
    if (q.length < 2) { setResults([]); return; }
    setSearching(true);
    try {
      const r = await catalogApi.search(q);
      setResults(r);
    } catch {
      setResults([]);
    } finally {
      setSearching(false);
    }
  };

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <SafeAreaView style={styles.container} edges={['top']}>
        <View style={styles.modalHeader}>
          <TouchableOpacity onPress={onClose}>
            <Text style={styles.closeButton}>Cancelar</Text>
          </TouchableOpacity>
          <Text style={styles.modalTitle}>Definir Jogo</Text>
          <View style={{ width: 60 }} />
        </View>
        <View style={{ padding: spacing.lg }}>
          <TextInput
            style={styles.searchInput}
            placeholder="Buscar jogo..."
            placeholderTextColor={colors.textMuted}
            value={query}
            onChangeText={search}
            autoFocus
          />
        </View>
        {searching ? (
          <Spinner />
        ) : (
          <FlatList
            data={results}
            keyExtractor={(g, i) => String(g.id || i)}
            contentContainerStyle={{ paddingHorizontal: spacing.lg, gap: spacing.sm }}
            renderItem={({ item: g }) => (
              <TouchableOpacity style={styles.searchResult} onPress={() => onSelect(g)}>
                <Text style={styles.searchResultText}>{g.name}</Text>
                {g.slug && <Text style={styles.muted}>{g.slug}</Text>}
              </TouchableOpacity>
            )}
          />
        )}
      </SafeAreaView>
    </Modal>
  );
}

// ── Mapping Timeline (expandable) ────────────────────────────────────────────

const EVENT_TYPE_COLORS: Record<string, string> = {
  VISUAL_ACTION: '#ef4444',
  NARRATION: '#06b6d4',
  ANIMATION: '#a855f7',
  STATIC_IMAGE: '#71717a',
  TEXT_OVERLAY: '#eab308',
  TRANSITION: '#3b82f6',
  CHARACTER_INTRO: '#22c55e',
  EDUCATIONAL_DEMO: '#2dd4bf',
  UNKNOWN: '#71717a',
};

function MappingTimeline({ sourceId, filename }: { sourceId: number; filename: string }) {
  const [expanded, setExpanded] = useState(false);
  const { data, isLoading, error } = useQuery({
    queryKey: ['gameplay-events', sourceId],
    queryFn: () => gameplaysApi.getEvents(sourceId),
    enabled: expanded,
  });

  const events = data?.events || [];

  return (
    <View style={styles.timelineWrap}>
      <TouchableOpacity style={styles.timelineHeader} onPress={() => setExpanded(!expanded)}>
        <Icon name="chart-timeline-variant" size={14} color={colors.textSecondary} />
        <Text style={styles.timelineHeaderText}>
          {isLoading ? 'Carregando...' : error ? 'Erro ao carregar' : expanded ? events.length === 0 ? 'Nenhum evento' : `${events.length} eventos detectados` : 'Ver análise do mapeamento'}
        </Text>
        <Icon name={expanded ? 'chevron-up' : 'chevron-down'} size={14} color={colors.textMuted} />
      </TouchableOpacity>

      {expanded && events.length > 0 && (
        <View style={styles.timelineBody}>
          {/* Timeline bar */}
          <View style={styles.timelineBar}>
            {events.map((e: any, i: number) => {
              const color = EVENT_TYPE_COLORS[e.event_type] || EVENT_TYPE_COLORS.UNKNOWN;
              return (
                <View
                  key={i}
                  style={{
                    flex: Math.max(1, (e.end_time - e.start_time) / 10),
                    backgroundColor: color,
                  }}
                />
              );
            })}
          </View>

          {/* Event list */}
          <View style={styles.eventList}>
            {events.slice(0, 20).map((e: any, i: number) => {
              const color = EVENT_TYPE_COLORS[e.event_type] || EVENT_TYPE_COLORS.UNKNOWN;
              return (
                <View key={i} style={styles.eventRow}>
                  <Text style={styles.eventTime}>{e.start_time.toFixed(0)}s</Text>
                  <View style={[styles.eventTypeBadge, { backgroundColor: `${color}20`, borderColor: `${color}60` }]}>
                    <Text style={[styles.eventTypeText, { color }]}>{e.event_type}</Text>
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.eventDesc} numberOfLines={2}>{e.description}</Text>
                  </View>
                  {e.interesting_score >= 0.7 && (
                    <Text style={styles.eventScore}>★ {e.interesting_score.toFixed(1)}</Text>
                  )}
                </View>
              );
            })}
            {events.length > 20 && (
              <Text style={styles.eventMore}>+{events.length - 20} eventos...</Text>
            )}
          </View>
        </View>
      )}
    </View>
  );
}

function ChannelTab() {
  const [form, setForm] = useState({
    channel_description: '',
    niche: '',
    target_audience: '',
    tone_of_voice: '',
    narrative_style: '',
    content_goals: '',
    special_rules: '',
  });
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const { data: profile } = useQuery({
    queryKey: ['channel-profile'],
    queryFn: channelApi.getProfile,
  });

  React.useEffect(() => {
    if (profile && !loaded) {
      setForm({
        channel_description: profile.channel_description || '',
        niche: profile.niche || '',
        target_audience: profile.target_audience || '',
        tone_of_voice: profile.tone_of_voice || '',
        narrative_style: profile.narrative_style || '',
        content_goals: profile.content_goals || '',
        special_rules: profile.special_rules || '',
      });
      setLoaded(true);
    }
  }, [profile, loaded]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await channelApi.updateProfile(form);
      Toast.show({ type: 'success', text1: 'Perfil salvo' });
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <ScrollView style={styles.tabContent} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      <Card>
        <Text style={styles.cardTitle}>Identidade do Canal</Text>
        <Text style={styles.hint}>Define como a IA personaliza os roteiros</Text>

        <View style={{ gap: spacing.md, marginTop: spacing.md }}>
          <View>
            <Text style={styles.label}>Descrição do canal</Text>
            <TextInput style={styles.textArea} value={form.channel_description} onChangeText={(v) => setForm({ ...form, channel_description: v })} placeholder="Ex: Meu canal é focado em análises de partidas competitivas..." placeholderTextColor={colors.textMuted} multiline numberOfLines={3} />
          </View>
          <View>
            <Text style={styles.label}>Nicho</Text>
            <TextInput style={styles.input} value={form.niche} onChangeText={(v) => setForm({ ...form, niche: v })} placeholder="Ex: FPS competitivo" placeholderTextColor={colors.textMuted} />
          </View>
          <View>
            <Text style={styles.label}>Público-alvo</Text>
            <TextInput style={styles.input} value={form.target_audience} onChangeText={(v) => setForm({ ...form, target_audience: v })} placeholder="Ex: Jogadores casuais que querem melhorar" placeholderTextColor={colors.textMuted} />
          </View>
          <View>
            <Text style={styles.label}>Tom de voz</Text>
            <TextInput style={styles.input} value={form.tone_of_voice} onChangeText={(v) => setForm({ ...form, tone_of_voice: v })} placeholder="Ex: educativo, analítico" placeholderTextColor={colors.textMuted} />
          </View>
          <View>
            <Text style={styles.label}>Estilo de narrativa</Text>
            <TextInput style={styles.input} value={form.narrative_style} onChangeText={(v) => setForm({ ...form, narrative_style: v })} placeholder="Ex: storytelling, análise direta" placeholderTextColor={colors.textMuted} />
          </View>
          <View>
            <Text style={styles.label}>Objetivos do canal</Text>
            <TextInput style={styles.textArea} value={form.content_goals} onChangeText={(v) => setForm({ ...form, content_goals: v })} placeholder="Ex: Crescer como autoridade..." placeholderTextColor={colors.textMuted} multiline numberOfLines={2} />
          </View>
          <View>
            <Text style={styles.label}>Regras especiais para a IA</Text>
            <TextInput style={styles.textArea} value={form.special_rules} onChangeText={(v) => setForm({ ...form, special_rules: v })} placeholder="Ex: Nunca usar gírias de CS:GO se o vídeo é sobre Valorant." placeholderTextColor={colors.textMuted} multiline numberOfLines={2} />
          </View>
        </View>

        <Button title="Salvar" variant="primary" loading={saving} onPress={handleSave} style={{ marginTop: spacing.lg }} />
      </Card>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  header: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md },
  title: { fontSize: fontSize.xxxl, fontWeight: fontWeight.bold, color: colors.text },
  tabBar: {
    flexDirection: 'row',
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  tab: {
    flex: 1,
    paddingVertical: spacing.md,
    alignItems: 'center',
    borderBottomWidth: 2,
    borderBottomColor: 'transparent',
  },
  tabActive: { borderBottomColor: colors.accent },
  tabText: { fontSize: fontSize.sm, color: colors.textMuted },
  tabTextActive: { color: colors.accent, fontWeight: fontWeight.medium },
  tabContent: { flex: 1 },
  rowBetween: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  cardTitle: { fontSize: fontSize.md, fontWeight: fontWeight.semibold, color: colors.text },
  muted: { fontSize: fontSize.sm, color: colors.textMuted },
  hint: { fontSize: fontSize.xs, color: colors.textMuted, marginTop: spacing.xs },
  uploadProgress: { marginTop: spacing.md, gap: spacing.xs },
  progressBar: { height: 4, backgroundColor: colors.surfaceElevated, borderRadius: radius.full, overflow: 'hidden' },
  progressFill: { height: '100%', backgroundColor: colors.accent, borderRadius: radius.full },
  sectionTitle: { fontSize: fontSize.lg, fontWeight: fontWeight.semibold, color: colors.text, marginVertical: spacing.md },
  sourceRow: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: spacing.sm },
  filename: { fontSize: fontSize.sm, fontWeight: fontWeight.medium, color: colors.text, fontFamily: 'monospace' },
  sourceMeta: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md, marginTop: spacing.xs },
  metaText: { fontSize: fontSize.xs, color: colors.textMuted },
  sourceBadges: { alignItems: 'flex-end', gap: spacing.xs },
  sourceActions: { marginTop: spacing.sm },
  toggleRow: { flexDirection: 'row', gap: spacing.lg },
  toggleItem: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  toggleLabel: { fontSize: fontSize.sm, color: colors.textSecondary, fontWeight: fontWeight.medium },
  errorText: { fontSize: fontSize.xs, color: colors.error, marginTop: spacing.xs },
  modalHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderBottomWidth: 1, borderBottomColor: colors.border },
  closeButton: { fontSize: fontSize.base, color: colors.accent },
  modalTitle: { fontSize: fontSize.md, fontWeight: fontWeight.semibold, color: colors.text },
  searchInput: { height: 44, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface, borderRadius: radius.md, paddingHorizontal: spacing.md, fontSize: fontSize.base, color: colors.text },
  searchResult: { paddingVertical: spacing.md, borderBottomWidth: 1, borderBottomColor: colors.border },
  searchResultText: { fontSize: fontSize.base, color: colors.text, fontWeight: fontWeight.medium },
  label: { fontSize: fontSize.sm, fontWeight: fontWeight.medium, color: colors.textSecondary, marginBottom: spacing.xs },
  input: { height: 44, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.bg, borderRadius: radius.md, paddingHorizontal: spacing.md, fontSize: fontSize.base, color: colors.text },
  textArea: { minHeight: 80, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.bg, borderRadius: radius.md, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, fontSize: fontSize.base, color: colors.text, textAlignVertical: 'top' },
  // Mapping progress
  mappingProgress: {
    height: 2,
    backgroundColor: colors.surfaceElevated,
    borderRadius: radius.full,
    marginTop: spacing.sm,
    overflow: 'hidden',
  },
  mappingProgressFill: {
    height: '100%',
    backgroundColor: colors.accent,
    borderRadius: radius.full,
  },
  // Timeline
  timelineWrap: {
    marginTop: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: spacing.sm,
  },
  timelineHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
  },
  timelineHeaderText: { fontSize: fontSize.xs, color: colors.textSecondary, flex: 1 },
  timelineBody: {
    marginTop: spacing.sm,
    gap: spacing.sm,
  },
  timelineBar: {
    flexDirection: 'row',
    height: 8,
    borderRadius: radius.full,
    overflow: 'hidden',
    backgroundColor: colors.surfaceElevated,
  },
  eventList: {
    maxHeight: 256,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    padding: spacing.sm,
    gap: spacing.xs,
  },
  eventRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.xs,
  },
  eventTime: {
    fontSize: 10,
    color: colors.textMuted,
    fontFamily: 'monospace',
    width: 32,
  },
  eventTypeBadge: {
    borderWidth: 1,
    borderRadius: radius.sm,
    paddingHorizontal: 6,
    paddingVertical: 1,
  },
  eventTypeText: { fontSize: 9, fontWeight: fontWeight.bold },
  eventDesc: { fontSize: fontSize.xs, color: colors.textSecondary, lineHeight: 16 },
  eventScore: { fontSize: 9, color: colors.accent, fontWeight: fontWeight.bold },
  eventMore: { fontSize: 10, color: colors.textMuted, textAlign: 'center', paddingVertical: spacing.xs },
});
