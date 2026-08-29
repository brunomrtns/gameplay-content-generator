import React, { useState, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  RefreshControl,
  TouchableOpacity,
  Alert,
  Modal,
  TextInput,
  FlatList,
  Image,
} from 'react-native';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useLiveData } from '../hooks/useLiveData';
import { SafeAreaView } from 'react-native-safe-area-context';
import DocumentPicker from 'react-native-document-picker';
import Icon from 'react-native-vector-icons/MaterialCommunityIcons';
import { kidsApi, channelApi } from '../api/endpoints';
import { Card, Badge, Button, EmptyState, Spinner } from '../components/ui';
import { colors } from '../theme/colors';
import { fontSize, fontWeight, radius, spacing } from '../theme/spacing';
import { fmtDuration } from '../utils/format';
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

const KIDS_PROCESSING_STATUS_CONFIG: Record<string, { label: string; variant: any }> = {
  uploading: { label: 'Enviando', variant: 'info' },
  queued: { label: 'Na fila', variant: 'info' },
  processing: { label: 'Processando', variant: 'info' },
  mapping: { label: 'Mapeando', variant: 'info' },
  ready: { label: 'Pronto', variant: 'success' },
  failed: { label: 'Falhou', variant: 'error' },
};

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

type Tab = 'media' | 'config';

// ── Main Screen ──────────────────────────────────────────────────────────────

export function KidsScreen() {
  const [tab, setTab] = useState<Tab>('media');
  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <Text style={styles.title}>Conteúdo Kids</Text>
      </View>
      <View style={styles.tabBar}>
        <TouchableOpacity style={[styles.tab, tab === 'media' && styles.tabActive]} onPress={() => setTab('media')}>
          <Icon name="film" size={16} color={tab === 'media' ? colors.accent : colors.textMuted} />
          <Text style={[styles.tabText, tab === 'media' && styles.tabTextActive]}>Mídias</Text>
        </TouchableOpacity>
        <TouchableOpacity style={[styles.tab, tab === 'config' && styles.tabActive]} onPress={() => setTab('config')}>
          <Icon name="cog" size={16} color={tab === 'config' ? colors.accent : colors.textMuted} />
          <Text style={[styles.tabText, tab === 'config' && styles.tabTextActive]}>Configuração do Canal</Text>
        </TouchableOpacity>
      </View>
      {tab === 'media' ? <KidsMediaTab /> : <KidsConfigTab />}
    </SafeAreaView>
  );
}

// ── Media Library Tab ────────────────────────────────────────────────────────

function KidsMediaTab() {
  const queryClient = useQueryClient();
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [filterKind, setFilterKind] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [showPublic, setShowPublic] = useState(false);
  const [editingAsset, setEditingAsset] = useState<any | null>(null);

  const queryParams: any = {};
  if (filterKind) queryParams.media_kind = filterKind;
  if (filterStatus) queryParams.status = filterStatus;
  queryParams.include_public = showPublic;

  const { data: assets, refetch, isRefetching, isLoading } = useLiveData(
    ['kids-assets', filterKind, filterStatus, showPublic],
    () => kidsApi.listLibraryAssets(queryParams),
    ['gameplay.status_changed', 'job.status_changed']
  );

  const ownAssets = (assets || []).filter((a: any) => a.is_own !== false);
  const publicAssets = (assets || []).filter((a: any) => a.is_own === false);
  const processingCount = ownAssets.filter((a: any) =>
    a.processing_status === 'processing' || a.processing_status === 'mapping' || a.processing_status === 'queued',
  ).length;

  const handleUpload = async () => {
    try {
      const result = await DocumentPicker.pick({
        type: [DocumentPicker.types.video, DocumentPicker.types.images],
        allowMultiSelection: true,
      });
      setUploading(true);
      setUploadProgress(0);
      for (let i = 0; i < result.length; i++) {
        const file = result[i];
        await kidsApi.uploadAsset(
          { uri: file.uri, name: file.name ?? 'file', type: file.type ?? 'video/mp4' },
          {},
          (pct) => setUploadProgress(Math.round((i / result.length) * 100 + pct / result.length)),
        );
      }
      Toast.show({ type: 'success', text1: `${result.length} arquivo(s) enviado(s)` });
      queryClient.invalidateQueries({ queryKey: ['kids-assets'] });
    } catch (err: any) {
      if (!DocumentPicker.isCancel(err)) Toast.show({ type: 'error', text1: err.message || 'Erro' });
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
  };

  const handleDelete = (asset: any) => {
    Alert.alert('Excluir mídia?', `Excluir "${asset.filename}"?`, [
      { text: 'Cancelar', style: 'cancel' },
      {
        text: 'Excluir',
        style: 'destructive',
        onPress: async () => {
          try {
            await kidsApi.deleteAsset(asset.id);
            Toast.show({ type: 'success', text1: 'Mídia excluída' });
            queryClient.invalidateQueries({ queryKey: ['kids-assets'] });
          } catch (err: any) {
            Toast.show({ type: 'error', text1: err.message || 'Erro' });
          }
        },
      },
    ]);
  };

  const handleToggleVisibility = async (asset: any) => {
    try {
      await kidsApi.patchAsset(asset.id, { is_public: !asset.is_public });
      Toast.show({
        type: 'success',
        text1: `"${asset.filename}" agora é ${!asset.is_public ? 'pública' : 'privada'}`,
      });
      queryClient.invalidateQueries({ queryKey: ['kids-assets'] });
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    }
  };

  const handleCreateMappingJob = async (assetId: number) => {
    try {
      await kidsApi.createMappingJob(assetId);
      Toast.show({ type: 'success', text1: 'Mapeamento solicitado', text2: 'O worker vai analisar (VLM + ASR)' });
      queryClient.invalidateQueries({ queryKey: ['kids-assets'] });
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    }
  };

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['kids-assets'] });

  return (
    <ScrollView
      style={styles.tabContent}
      refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={() => { void refetch(); }} tintColor={colors.accent} />}
    >
      {/* Upload card */}
      <View style={styles.uploadCard}>
        <View style={{ flex: 1 }}>
          <Text style={styles.uploadTitle}>Biblioteca de Mídias</Text>
          <Text style={styles.uploadHint}>Envie imagens ou vídeos para o canal Kids</Text>
          {processingCount > 0 && (
            <View style={styles.processingBadge}>
              <Icon name="loading" size={12} color={colors.accentWarm} />
              <Text style={styles.processingText}>{processingCount} processando</Text>
            </View>
          )}
        </View>
        <Button title="Enviar" variant="primary" onPress={handleUpload} loading={uploading} />
      </View>

      {uploading && (
        <View style={styles.progressWrap}>
          <View style={styles.progressBar}>
            <View style={[styles.progressFill, { width: `${uploadProgress}%` }]} />
          </View>
          <Text style={styles.progressText}>{uploadProgress}%</Text>
        </View>
      )}

      {/* Filters */}
      <View style={styles.filterRow}>
        <Text style={styles.filterLabel}>Tipo:</Text>
        {[
          { v: '', l: 'Todos' },
          { v: 'image', l: 'Imagens' },
          { v: 'video', l: 'Vídeos' },
        ].map((f) => (
          <TouchableOpacity
            key={f.v}
            style={[styles.filterChip, filterKind === f.v && styles.filterChipActive]}
            onPress={() => setFilterKind(f.v)}
          >
            <Text style={[styles.filterChipText, filterKind === f.v && styles.filterChipTextActive]}>{f.l}</Text>
          </TouchableOpacity>
        ))}
      </View>
      <View style={styles.filterRow}>
        <Text style={styles.filterLabel}>Status:</Text>
        {[
          { v: '', l: 'Todos' },
          { v: 'ready', l: 'Prontos' },
          { v: 'processing', l: 'Processando' },
          { v: 'failed', l: 'Falhas' },
        ].map((f) => (
          <TouchableOpacity
            key={f.v}
            style={[styles.filterChip, filterStatus === f.v && styles.filterChipActive]}
            onPress={() => setFilterStatus(f.v)}
          >
            <Text style={[styles.filterChipText, filterStatus === f.v && styles.filterChipTextActive]}>{f.l}</Text>
          </TouchableOpacity>
        ))}
        <TouchableOpacity
          style={[styles.filterChip, showPublic && styles.filterChipActive]}
          onPress={() => setShowPublic(!showPublic)}
        >
          <Icon name="eye" size={12} color={showPublic ? colors.accent : colors.textMuted} />
          <Text style={[styles.filterChipText, showPublic && styles.filterChipTextActive]}>Públicas</Text>
        </TouchableOpacity>
      </View>

      {/* Assets list */}
      <Text style={styles.sectionTitle}>Minhas mídias ({ownAssets.length})</Text>
      {isLoading ? (
        <View style={{ paddingVertical: spacing.xxl, alignItems: 'center' }}>
          <Spinner size="large" />
        </View>
      ) : ownAssets.length === 0 ? (
        <Card>
          <EmptyState
            icon={<Icon name="film-off" size={40} color={colors.textMuted} />}
            title="Nenhuma mídia"
            description="Envie imagens ou vídeos para começar."
          />
        </Card>
      ) : (
        <View style={{ gap: spacing.md, marginBottom: spacing.lg }}>
          {ownAssets.map((a: any) => (
            <MediaLibraryCard
              key={a.id}
              asset={a}
              onDeleted={invalidate}
              onCreateMappingJob={handleCreateMappingJob}
              onEdit={() => setEditingAsset(a)}
              onToggleVisibility={() => handleToggleVisibility(a)}
              onDelete={() => handleDelete(a)}
            />
          ))}
        </View>
      )}

      {/* Public assets */}
      {publicAssets.length > 0 && (
        <>
          <Text style={styles.sectionTitle}>Biblioteca pública ({publicAssets.length})</Text>
          <View style={{ gap: spacing.md, marginBottom: spacing.lg }}>
            {publicAssets.map((a: any) => (
              <MediaLibraryCard
                key={a.id}
                asset={a}
                readOnly
                onDeleted={invalidate}
                onCreateMappingJob={() => {}}
                onEdit={() => {}}
                onToggleVisibility={() => {}}
                onDelete={() => {}}
              />
            ))}
          </View>
        </>
      )}

      {/* Edit modal */}
      {editingAsset && (
        <EditAssetModal
          asset={editingAsset}
          onClose={() => setEditingAsset(null)}
          onSaved={() => {
            setEditingAsset(null);
            invalidate();
          }}
        />
      )}
    </ScrollView>
  );
}

// ── Media Library Card ───────────────────────────────────────────────────────

function MediaLibraryCard({
  asset,
  onDeleted,
  onCreateMappingJob,
  readOnly,
  onEdit,
  onToggleVisibility,
  onDelete,
}: {
  asset: any;
  onDeleted: () => void;
  onCreateMappingJob: (assetId: number) => void;
  readOnly?: boolean;
  onEdit: () => void;
  onToggleVisibility: () => void;
  onDelete: () => void;
}) {
  const [showTimeline, setShowTimeline] = useState(false);

  const procCfg = KIDS_PROCESSING_STATUS_CONFIG[asset.processing_status] || KIDS_PROCESSING_STATUS_CONFIG.uploading;
  const isProcessing = asset.processing_status === 'processing';
  const isMapping = asset.processing_status === 'mapping' || asset.processing_status === 'queued';
  const isReady = asset.processing_status === 'ready';
  const isFailed = asset.processing_status === 'failed';
  const canMap = isReady && asset.media_kind === 'video' && (asset.event_count === 0 || asset.event_count === undefined);

  return (
    <Card padding={spacing.md}>
      <View style={styles.assetRow}>
        {/* Thumbnail / icon */}
        <View style={styles.assetIconWrap}>
          {asset.thumbnail_key ? (
            <Image
              source={{ uri: `${asset.thumbnail_key}` }}
              style={styles.assetThumb}
            />
          ) : isMapping ? (
            <Icon name="cpu" size={20} color={colors.accent} />
          ) : isReady ? (
            <Icon name={asset.media_kind === 'image' ? 'image' : 'check-circle'} size={20} color={colors.accent} />
          ) : isProcessing ? (
            <Icon name="loading" size={20} color={colors.accentWarm} />
          ) : isFailed ? (
            <Icon name="alert-circle" size={20} color={colors.error} />
          ) : (
            <Icon name={asset.media_kind === 'image' ? 'image' : 'video'} size={20} color={colors.textMuted} />
          )}
        </View>

        {/* Info */}
        <View style={{ flex: 1 }}>
          <Text style={styles.assetFilename} numberOfLines={1}>{asset.filename}</Text>
          <View style={styles.assetMeta}>
            {asset.media_kind === 'video' && asset.duration > 0 && (
              <Text style={styles.assetMetaText}>{fmtDuration(asset.duration)}</Text>
            )}
            {asset.width > 0 && asset.height > 0 && (
              <Text style={styles.assetMetaText}>{asset.width}×{asset.height}</Text>
            )}
            {asset.file_size > 0 && (
              <Text style={styles.assetMetaText}>{(asset.file_size / 1024 / 1024).toFixed(1)}MB</Text>
            )}
            {isReady && asset.media_kind === 'video' && asset.event_count > 0 && (
              <Text style={[styles.assetMetaText, { color: colors.accent }]}>
                {asset.event_count} eventos
              </Text>
            )}
          </View>
          {asset.tags && asset.tags.length > 0 ? (
            <View style={styles.tagsRow}>
              {asset.tags.slice(0, 3).map((tag: string, i: number) => (
                <View key={i} style={styles.tagBadge}>
                  <Text style={styles.tagBadgeText}>{tag}</Text>
                </View>
              ))}
              {asset.tags.length > 3 && (
                <Text style={styles.tagMore}>+{asset.tags.length - 3}</Text>
              )}
            </View>
          ) : (
            <Text style={styles.noTags}>Geral — sem tags</Text>
          )}
        </View>

        {/* Status + actions */}
        <View style={styles.assetActions}>
          <Badge label={procCfg.label} variant={procCfg.variant} />
          {readOnly ? (
            <Badge label="Pública" variant="default" />
          ) : isReady ? (
            <View style={styles.iconActions}>
              <TouchableOpacity onPress={onEdit} style={styles.iconBtn}>
                <Icon name="tag" size={16} color={colors.textMuted} />
              </TouchableOpacity>
              <TouchableOpacity onPress={onToggleVisibility} style={styles.iconBtn}>
                <Icon name="eye" size={16} color={asset.is_public ? colors.accent : colors.textMuted} />
              </TouchableOpacity>
              <TouchableOpacity onPress={onDelete} style={styles.iconBtn}>
                <Icon name="trash-can" size={16} color={colors.textMuted} />
              </TouchableOpacity>
            </View>
          ) : null}
        </View>
      </View>

      {/* Progress bar during processing/mapping */}
      {(isProcessing || isMapping) && (
        <View style={styles.assetProgress}>
          <View style={[styles.assetProgressFill, isMapping ? { width: '66%', backgroundColor: colors.accent } : { width: '33%', backgroundColor: colors.accentWarm }]} />
        </View>
      )}

      {/* Error message */}
      {isFailed && asset.process_error && (
        <Text style={styles.assetError}>{asset.process_error}</Text>
      )}

      {/* Solicitar mapeamento */}
      {canMap && !readOnly && (
        <View style={styles.mappingRow}>
          <Button
            title="Solicitar mapeamento"
            variant="outline"
            size="sm"
            onPress={() => onCreateMappingJob(asset.id)}
          />
          <Text style={styles.mappingHint}>Envia para o worker analisar (VLM + ASR)</Text>
        </View>
      )}

      {/* Mapping timeline (expandable) */}
      {isReady && asset.media_kind === 'video' && (
        <KidsMappingTimeline assetId={asset.id} expanded={showTimeline} onToggle={() => setShowTimeline(!showTimeline)} />
      )}
    </Card>
  );
}

// ── Kids Mapping Timeline ────────────────────────────────────────────────────

function KidsMappingTimeline({ assetId, expanded, onToggle }: { assetId: number; expanded: boolean; onToggle: () => void }) {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ['kids-asset-events', assetId],
    queryFn: () => kidsApi.getAssetEvents(assetId),
    enabled: expanded,
  });

  const events = data?.events || [];

  return (
    <View style={styles.timelineWrap}>
      <TouchableOpacity style={styles.timelineHeader} onPress={onToggle}>
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
            {events.map((e: any, i: number) => {
              const color = EVENT_TYPE_COLORS[e.event_type] || EVENT_TYPE_COLORS.UNKNOWN;
              return (
                <View key={i} style={styles.eventRow}>
                  <Text style={styles.eventTime}>{e.start_time.toFixed(0)}s</Text>
                  <View style={[styles.eventTypeBadge, { backgroundColor: `${color}20`, borderColor: `${color}60` }]}>
                    <Text style={[styles.eventTypeText, { color }]}>{e.event_type}</Text>
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.eventDesc} numberOfLines={2}>{e.description}</Text>
                    {e.transcript && (
                      <Text style={styles.eventTranscript} numberOfLines={1}>"{e.transcript.substring(0, 80)}..."</Text>
                    )}
                  </View>
                  {e.interesting_score >= 0.7 && (
                    <Text style={styles.eventScore}>★ {e.interesting_score.toFixed(1)}</Text>
                  )}
                </View>
              );
            })}
          </View>
        </View>
      )}
    </View>
  );
}

// ── Edit Asset Modal ─────────────────────────────────────────────────────────

function EditAssetModal({ asset, onClose, onSaved }: { asset: any; onClose: () => void; onSaved: () => void }) {
  const [tags, setTags] = useState((asset.tags || []).join(', '));
  const [desc, setDesc] = useState(asset.description || '');
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await kidsApi.patchAsset(asset.id, {
        tags: tags.split(',').map((t: string) => t.trim()).filter(Boolean).join(','),
        description: desc,
      });
      Toast.show({ type: 'success', text1: 'Mídia atualizada' });
      onSaved();
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal visible animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <SafeAreaView style={styles.modalContainer} edges={['top']}>
        <View style={styles.modalHeader}>
          <TouchableOpacity onPress={onClose}>
            <Text style={styles.closeButton}>Cancelar</Text>
          </TouchableOpacity>
          <Text style={styles.modalTitle}>Editar Mídia</Text>
          <View style={{ width: 60 }} />
        </View>
        <ScrollView contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
          <Text style={styles.modalItemTitle} numberOfLines={1}>{asset.filename}</Text>
          <View>
            <Text style={styles.modalLabel}>Tags (separadas por vírgula)</Text>
            <TextInput
              style={styles.textInput}
              value={tags}
              onChangeText={setTags}
              placeholder="Ex: animais, natureza, selva"
              placeholderTextColor={colors.textMuted}
            />
          </View>
          <View>
            <Text style={styles.modalLabel}>Descrição</Text>
            <TextInput
              style={styles.textArea}
              value={desc}
              onChangeText={setDesc}
              placeholder="Descrição da mídia..."
              placeholderTextColor={colors.textMuted}
              multiline
              numberOfLines={3}
              textAlignVertical="top"
            />
          </View>
          <Button title="Salvar" variant="primary" fullWidth onPress={handleSave} loading={saving} />
        </ScrollView>
      </SafeAreaView>
    </Modal>
  );
}

// ── Channel Config Tab ───────────────────────────────────────────────────────

function KidsConfigTab() {
  const queryClient = useQueryClient();
  const [profile, setProfile] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const [profileForm, setProfileForm] = useState({
    channel_description: '',
    niche: '',
    target_audience: '',
    tone_of_voice: '',
    narrative_style: '',
    content_goals: '',
    special_rules: '',
  });

  const [kidsMeta, setKidsMeta] = useState({
    age_range: '3-6',
    categories: [] as string[],
    target_duration: 45,
  });

  const { data: topics } = useQuery({ queryKey: ['kids-topics'], queryFn: kidsApi.listTopics });
  const { data: library } = useQuery({ queryKey: ['kids-topic-library'], queryFn: kidsApi.getTopicLibrary });
  const { data: calendar } = useQuery({ queryKey: ['kids-calendar'], queryFn: kidsApi.getSeasonalCalendar });

  React.useEffect(() => {
    channelApi
      .getProfile()
      .then((p) => {
        setProfile(p);
        setProfileForm({
          channel_description: p.channel_description || '',
          niche: p.niche || '',
          target_audience: p.target_audience || '',
          tone_of_voice: p.tone_of_voice || '',
          narrative_style: p.narrative_style || '',
          content_goals: p.content_goals || '',
          special_rules: p.special_rules || '',
        });
        const meta = p.metadata || {};
        setKidsMeta({
          age_range: meta.age_range || meta.kids_age_range || '3-6',
          categories: meta.categories || [],
          target_duration: meta.target_duration || 45,
        });
      })
      .catch((err) => Toast.show({ type: 'error', text1: err.message }))
      .finally(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      await channelApi.updateProfile({
        ...profileForm,
        metadata: {
          age_range: kidsMeta.age_range,
          categories: kidsMeta.categories,
          target_duration: kidsMeta.target_duration,
        },
      });
      Toast.show({ type: 'success', text1: 'Perfil editorial salvo' });
      queryClient.invalidateQueries({ queryKey: ['channel-profile'] });
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro ao salvar' });
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center' }}>
        <Spinner size="large" />
      </View>
    );
  }

  const isProfileEmpty = !profile?.niche && !profile?.target_audience && !profile?.tone_of_voice;

  return (
    <ScrollView style={styles.tabContent} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      {/* Onboarding Alert */}
      {isProfileEmpty && (
        <View style={styles.onboardingAlert}>
          <Icon name="brain" size={20} color={colors.accentWarm} />
          <View style={{ flex: 1 }}>
            <Text style={styles.onboardingTitle}>Configure seu canal para começar</Text>
            <Text style={styles.onboardingText}>
              Preencha o perfil editorial abaixo para que a IA gere ideias relevantes.
            </Text>
          </View>
        </View>
      )}

      {/* Editorial Profile */}
      <Card padding={spacing.md}>
        <View style={styles.configHeader}>
          <View style={{ flex: 1 }}>
            <View style={styles.configTitleRow}>
              <Icon name="brain" size={16} color={colors.accent} />
              <Text style={styles.configTitle}>Identidade do Canal</Text>
            </View>
            <Text style={styles.configHint}>Define como a IA personaliza ideias e roteiros</Text>
          </View>
          <Button title="Salvar" size="sm" onPress={handleSave} loading={saving} />
        </View>

        <View style={{ gap: spacing.md, marginTop: spacing.md }}>
          <View>
            <Text style={styles.label}>Descrição do canal</Text>
            <TextInput
              style={styles.textArea}
              value={profileForm.channel_description}
              onChangeText={(v) => setProfileForm({ ...profileForm, channel_description: v })}
              placeholder="Ex: Canal educativo infantil sobre ciência e natureza..."
              placeholderTextColor={colors.textMuted}
              multiline
              numberOfLines={3}
              textAlignVertical="top"
            />
          </View>
          <View style={styles.fieldRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.label}>Nicho</Text>
              <TextInput
                style={styles.textInput}
                value={profileForm.niche}
                onChangeText={(v) => setProfileForm({ ...profileForm, niche: v })}
                placeholder="Ex: Ciência e natureza"
                placeholderTextColor={colors.textMuted}
              />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.label}>Público-alvo</Text>
              <TextInput
                style={styles.textInput}
                value={profileForm.target_audience}
                onChangeText={(v) => setProfileForm({ ...profileForm, target_audience: v })}
                placeholder="Ex: Crianças 6-10 anos"
                placeholderTextColor={colors.textMuted}
              />
            </View>
          </View>
          <View style={styles.fieldRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.label}>Tom de voz</Text>
              <TextInput
                style={styles.textInput}
                value={profileForm.tone_of_voice}
                onChangeText={(v) => setProfileForm({ ...profileForm, tone_of_voice: v })}
                placeholder="Ex: amigável, curioso"
                placeholderTextColor={colors.textMuted}
              />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.label}>Estilo narrativa</Text>
              <TextInput
                style={styles.textInput}
                value={profileForm.narrative_style}
                onChangeText={(v) => setProfileForm({ ...profileForm, narrative_style: v })}
                placeholder="Ex: perguntas e respostas"
                placeholderTextColor={colors.textMuted}
              />
            </View>
          </View>
          <View>
            <Text style={styles.label}>Objetivos de conteúdo</Text>
            <TextInput
              style={styles.textInput}
              value={profileForm.content_goals}
              onChangeText={(v) => setProfileForm({ ...profileForm, content_goals: v })}
              placeholder="Ex: Educar e entreter"
              placeholderTextColor={colors.textMuted}
            />
          </View>
        </View>
      </Card>

      {/* Kids-specific Config */}
      <Card padding={spacing.md}>
        <View style={styles.configHeader}>
          <View style={{ flex: 1 }}>
            <View style={styles.configTitleRow}>
              <Icon name="sparkles" size={16} color={colors.accent} />
              <Text style={styles.configTitle}>Configuração Kids</Text>
            </View>
            <Text style={styles.configHint}>Faixa etária, categorias e duração</Text>
          </View>
          <Button title="Salvar" size="sm" onPress={handleSave} loading={saving} />
        </View>

        <View style={{ gap: spacing.md, marginTop: spacing.md }}>
          <View style={styles.fieldRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.label}>Faixa etária</Text>
              <View style={styles.pickerWrap}>
                {[
                  { v: '3-6', l: '3-6 anos' },
                  { v: '6-10', l: '6-10 anos' },
                  { v: '7-10', l: '7-10 anos' },
                  { v: 'all', l: 'Todas' },
                ].map((o) => (
                  <TouchableOpacity
                    key={o.v}
                    style={[styles.pickerOption, kidsMeta.age_range === o.v && styles.pickerOptionActive]}
                    onPress={() => setKidsMeta({ ...kidsMeta, age_range: o.v })}
                  >
                    <Text style={[styles.pickerOptionText, kidsMeta.age_range === o.v && styles.pickerOptionTextActive]}>
                      {o.l}
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.label}>Duração alvo: {kidsMeta.target_duration}s</Text>
              <View style={styles.durationRow}>
                {[15, 30, 45, 60, 75, 90].map((d) => (
                  <TouchableOpacity
                    key={d}
                    style={[styles.durationChip, kidsMeta.target_duration === d && styles.durationChipActive]}
                    onPress={() => setKidsMeta({ ...kidsMeta, target_duration: d })}
                  >
                    <Text style={[styles.durationText, kidsMeta.target_duration === d && styles.durationTextActive]}>
                      {d}s
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>
            </View>
          </View>

          <View>
            <Text style={styles.label}>Categorias de interesse (vazio = todas)</Text>
            <View style={styles.categoriesWrap}>
              {TOPIC_LIBRARY_CATEGORIES.map((c) => (
                <TouchableOpacity
                  key={c.value}
                  style={[styles.categoryChip, kidsMeta.categories.includes(c.value) && styles.categoryChipActive]}
                  onPress={() => {
                    setKidsMeta((prev) => ({
                      ...prev,
                      categories: prev.categories.includes(c.value)
                        ? prev.categories.filter((v) => v !== c.value)
                        : [...prev.categories, c.value],
                    }));
                  }}
                >
                  <Text style={[styles.categoryText, kidsMeta.categories.includes(c.value) && styles.categoryTextActive]}>
                    {c.label}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>
        </View>
      </Card>

      {/* Topics */}
      <Card padding={spacing.md}>
        <Text style={styles.configTitle}>Tópicos ({topics?.length || 0})</Text>
        <View style={{ gap: spacing.sm, marginTop: spacing.md }}>
          {topics?.length === 0 ? (
            <Text style={styles.muted}>Nenhum tópico criado</Text>
          ) : (
            topics?.map((t: any) => (
              <View key={t.id} style={styles.topicRow}>
                <Text style={styles.topicName}>{t.name}</Text>
                <Badge label={t.category || '—'} variant="default" />
              </View>
            ))
          )}
        </View>
      </Card>

      {/* Topic Library */}
      {library && (
        <Card padding={spacing.md}>
          <Text style={styles.configTitle}>Biblioteca de Tópicos</Text>
          <Text style={styles.muted}>Disponível no servidor para descoberta</Text>
        </Card>
      )}

      {/* Seasonal Calendar */}
      {calendar && (
        <Card padding={spacing.md}>
          <Text style={styles.configTitle}>Calendário Sazonal</Text>
          <Text style={styles.muted}>Disponível no servidor</Text>
        </Card>
      )}
    </ScrollView>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

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
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.xs,
    paddingVertical: spacing.md,
    borderBottomWidth: 2,
    borderBottomColor: 'transparent',
  },
  tabActive: { borderBottomColor: colors.accent },
  tabText: { fontSize: fontSize.sm, color: colors.textMuted },
  tabTextActive: { color: colors.accent, fontWeight: fontWeight.medium },
  tabContent: { flex: 1 },

  // Upload card
  uploadCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    margin: spacing.lg,
    marginBottom: 0,
  },
  uploadTitle: { fontSize: fontSize.md, fontWeight: fontWeight.semibold, color: colors.text },
  uploadHint: { fontSize: fontSize.xs, color: colors.textMuted, marginTop: 2 },
  processingBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    marginTop: spacing.xs,
  },
  processingText: { fontSize: 10, color: colors.accentWarm },

  // Progress
  progressWrap: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
  },
  progressBar: {
    flex: 1,
    height: 4,
    backgroundColor: colors.surfaceElevated,
    borderRadius: radius.full,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    backgroundColor: colors.accent,
    borderRadius: radius.full,
  },
  progressText: { fontSize: fontSize.xs, color: colors.accent, fontWeight: fontWeight.medium },

  // Filters
  filterRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    flexWrap: 'wrap',
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.xs,
  },
  filterLabel: { fontSize: fontSize.xs, color: colors.textSecondary, fontWeight: fontWeight.medium },
  filterChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderRadius: radius.full,
    borderWidth: 1,
    borderColor: 'transparent',
  },
  filterChipActive: {
    backgroundColor: 'rgba(45,212,191,0.1)',
    borderColor: 'rgba(45,212,191,0.3)',
  },
  filterChipText: { fontSize: fontSize.xs, color: colors.textMuted },
  filterChipTextActive: { color: colors.accent, fontWeight: fontWeight.medium },

  // Section
  sectionTitle: {
    fontSize: fontSize.lg,
    fontWeight: fontWeight.semibold,
    color: colors.text,
    marginHorizontal: spacing.lg,
    marginTop: spacing.md,
    marginBottom: spacing.sm,
  },

  // Asset card
  assetRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.md,
  },
  assetIconWrap: {
    width: 48,
    height: 48,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceElevated,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
  assetThumb: {
    width: '100%',
    height: '100%',
  },
  assetFilename: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.medium,
    color: colors.text,
    fontFamily: 'monospace',
  },
  assetMeta: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.md,
    marginTop: 2,
  },
  assetMetaText: { fontSize: 10, color: colors.textMuted },
  tagsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 4,
    marginTop: 4,
  },
  tagBadge: {
    backgroundColor: colors.surfaceElevated,
    borderRadius: radius.sm,
    paddingHorizontal: 6,
    paddingVertical: 1,
  },
  tagBadgeText: { fontSize: 10, color: colors.textSecondary },
  tagMore: { fontSize: 10, color: colors.textMuted, alignSelf: 'center' },
  noTags: {
    fontSize: 10,
    color: colors.textMuted,
    marginTop: 4,
    borderWidth: 1,
    borderStyle: 'dashed',
    borderColor: colors.border,
    borderRadius: radius.full,
    paddingHorizontal: 6,
    paddingVertical: 1,
    alignSelf: 'flex-start',
  },
  assetActions: {
    alignItems: 'flex-end',
    gap: 4,
  },
  iconActions: {
    flexDirection: 'row',
    gap: 4,
  },
  iconBtn: {
    padding: 4,
  },
  assetProgress: {
    height: 2,
    backgroundColor: colors.surfaceElevated,
    borderRadius: radius.full,
    marginTop: spacing.sm,
    overflow: 'hidden',
  },
  assetProgressFill: {
    height: '100%',
    borderRadius: radius.full,
  },
  assetError: {
    fontSize: fontSize.xs,
    color: colors.error,
    marginTop: spacing.xs,
  },
  mappingRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    marginTop: spacing.sm,
    flexWrap: 'wrap',
  },
  mappingHint: { fontSize: 10, color: colors.textMuted },

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
  eventTranscript: { fontSize: 10, color: colors.textMuted, fontStyle: 'italic', marginTop: 2 },
  eventScore: { fontSize: 9, color: colors.accent, fontWeight: fontWeight.bold },

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
  modalItemTitle: { fontSize: fontSize.base, fontWeight: fontWeight.medium, color: colors.text, fontFamily: 'monospace' },
  modalLabel: { fontSize: fontSize.sm, fontWeight: fontWeight.medium, color: colors.textSecondary },
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

  // Config
  onboardingAlert: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.md,
    backgroundColor: 'rgba(245,158,11,0.08)',
    borderWidth: 1,
    borderColor: 'rgba(245,158,11,0.3)',
    borderRadius: radius.lg,
    padding: spacing.md,
  },
  onboardingTitle: { fontSize: fontSize.sm, fontWeight: fontWeight.semibold, color: colors.accentWarm },
  onboardingText: { fontSize: fontSize.xs, color: colors.textMuted, marginTop: 2 },
  configHeader: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: spacing.sm,
  },
  configTitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
  },
  configTitle: { fontSize: fontSize.md, fontWeight: fontWeight.semibold, color: colors.text },
  configHint: { fontSize: fontSize.xs, color: colors.textMuted, marginTop: 2 },
  label: { fontSize: fontSize.xs, fontWeight: fontWeight.medium, color: colors.textSecondary, marginBottom: 2 },
  fieldRow: {
    flexDirection: 'row',
    gap: spacing.md,
  },
  pickerWrap: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
    marginTop: spacing.xs,
  },
  pickerOption: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
  },
  pickerOptionActive: {
    borderColor: colors.accent,
    backgroundColor: 'rgba(45,212,191,0.1)',
  },
  pickerOptionText: { fontSize: fontSize.xs, color: colors.textMuted },
  pickerOptionTextActive: { color: colors.accent, fontWeight: fontWeight.medium },
  durationRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
    marginTop: spacing.xs,
  },
  durationChip: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
  },
  durationChipActive: {
    borderColor: colors.accent,
    backgroundColor: 'rgba(45,212,191,0.1)',
  },
  durationText: { fontSize: fontSize.xs, color: colors.textMuted },
  durationTextActive: { color: colors.accent, fontWeight: fontWeight.medium },
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

  // Topics
  topicRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  topicName: { fontSize: fontSize.base, color: colors.text },
  muted: { fontSize: fontSize.sm, color: colors.textMuted, marginTop: spacing.xs },
});
