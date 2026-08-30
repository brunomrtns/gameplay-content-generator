import React, { useState, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  RefreshControl,
  TouchableOpacity,
  FlatList,
  Image,
  Modal,
  Linking,
} from 'react-native';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useLiveData } from '../hooks/useLiveData';
import { SafeAreaView } from 'react-native-safe-area-context';
import { dashboardApi, automationApi, videosApi, youtubeApi, workersApi } from '../api/endpoints';
import { Card, Button, Badge, Spinner, EmptyState } from '../components/ui';
import Icon from 'react-native-vector-icons/MaterialCommunityIcons';
import { colors } from '../theme/colors';
import { fontSize, fontWeight, radius, spacing } from '../theme/spacing';
import { fmtDuration, fmtDate } from '../utils/format';
import { videoUrl, thumbUrl } from '../api/client';
import {
  downloadVideo,
  shareVideoFile,
  shareYouTubeUrl,
  getLocalVideoPath,
} from '../utils/shareVideo';
import Toast from 'react-native-toast-message';
import Video from 'react-native-video';

const VIDEO_STATUS_CONFIG: Record<string, { label: string; variant: any }> = {
  pending: { label: 'Pendente', variant: 'default' },
  ready: { label: 'Pronto', variant: 'info' },
  qa_passed: { label: 'QA OK', variant: 'success' },
  qa_failed: { label: 'QA Falhou', variant: 'error' },
  pending_approval: { label: 'Aguardando publicação', variant: 'warning' },
  published: { label: 'Publicado', variant: 'success' },
  publish_failed: { label: 'Publicação falhou', variant: 'error' },
  draft: { label: 'Rascunho', variant: 'default' },
  failed: { label: 'Falhou', variant: 'error' },
  rendering: { label: 'Renderizando', variant: 'info' },
};

export function DashboardScreen() {
  const queryClient = useQueryClient();
  const [toggling, setToggling] = useState(false);
  const [publishing, setPublishing] = useState<number | null>(null);
  const [playing, setPlaying] = useState<any | null>(null);
  const [ytConnecting, setYtConnecting] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [shareProgress, setShareProgress] = useState(0);

  const { data: dash, refetch, isRefetching } = useLiveData(
    ['dashboard'],
    dashboardApi.get,
    ['job.status_changed', 'video.created', 'automation.status_changed']
  );

  const { data: workers } = useLiveData(
    ['workers'],
    workersApi.list,
    ['worker.status_changed']
  );

  const handleToggleAutomation = async () => {
    setToggling(true);
    try {
      if (dash?.automation_status === 'running') {
        await automationApi.pause();
        Toast.show({ type: 'success', text1: 'Automação pausada' });
      } else {
        await automationApi.start();
        Toast.show({ type: 'success', text1: 'Automação iniciada' });
      }
      queryClient.invalidateQueries({ queryKey: ['dashboard'] });
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    } finally {
      setToggling(false);
    }
  };

  const handlePublish = async (videoId: number) => {
    setPublishing(videoId);
    try {
      await videosApi.publish(videoId);
      Toast.show({ type: 'success', text1: 'Vídeo publicado no YouTube' });
      queryClient.invalidateQueries({ queryKey: ['dashboard'] });
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro ao publicar' });
    } finally {
      setPublishing(null);
    }
  };

  const handleConnectYouTube = async () => {
    setYtConnecting(true);
    try {
      const url = await youtubeApi.connectUrl();
      // Open OAuth in external browser — Google OAuth doesn't work well in WebView
      await Linking.openURL(url);
      Toast.show({
        type: 'info',
        text1: 'Abrindo Google...',
        text2: 'Autorize e volte ao app',
      });
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro ao conectar' });
    } finally {
      setYtConnecting(false);
    }
  };

  const handleDisconnectYouTube = async () => {
    try {
      await youtubeApi.disconnect();
      Toast.show({ type: 'success', text1: 'YouTube desconectado' });
      queryClient.invalidateQueries({ queryKey: ['dashboard'] });
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    }
  };

  // Direct share: download → copy metadata → open Android share sheet
  const handleDirectShare = async (video: any) => {
    const isPublished = video.status === 'published' && video.youtube_url;
    if (isPublished) {
      try {
        await shareYouTubeUrl(video);
        Toast.show({ type: 'success', text1: 'Legenda copiada', text2: 'Escolha o app para compartilhar' });
      } catch (err: any) {
        if (!String(err.message || '').includes('cancel') && err.message !== 'User did not share') {
          Toast.show({ type: 'error', text1: err.message || 'Erro' });
        }
      }
      return;
    }
    setSharing(true);
    setShareProgress(0);
    try {
      let path = await getLocalVideoPath(video.id);
      if (!path) {
        Toast.show({ type: 'info', text1: 'Baixando vídeo...', text2: 'Compartilhamento vai abrir automaticamente' });
        path = await downloadVideo(video.id, (pct) => setShareProgress(pct));
      }
      await shareVideoFile(video, path);
      Toast.show({ type: 'success', text1: 'Legenda copiada', text2: 'Cole no app da rede social' });
    } catch (err: any) {
      if (!String(err.message || '').includes('cancel') && err.message !== 'User did not share') {
        Toast.show({ type: 'error', text1: err.message || 'Erro' });
      }
    } finally {
      setSharing(false);
      setShareProgress(0);
    }
  };

  const gameplays = dash?.gameplays || { total: 0, processing: 0, ready: 0 };
  const videos = dash?.videos || { total: 0, published: 0 };
  const jobs = dash?.jobs || { total: 0, running: 0 };
  const recentVideos = dash?.recent_videos || [];
  const isKidsDomain = dash?.channel_domain === 'kids';
  const automationRunning = dash?.automation_status === 'running';

  const stats = isKidsDomain
    ? [
        { label: 'Tópicos', value: dash?.kids?.total_topics || 0, sub: `${dash?.kids?.ready_assets || 0} mídias prontas` },
        { label: 'Mídias', value: dash?.kids?.total_assets || 0, sub: `${dash?.kids?.ready_assets || 0} prontas` },
        { label: 'Vídeos', value: videos.total, sub: jobs.running > 0 ? 'produzindo' : 'em pausa' },
        { label: 'Publicados', value: videos.published, sub: 'no YouTube' },
      ]
    : [
        { label: 'Gameplays', value: gameplays.total, sub: `${gameplays.ready} prontos` },
        { label: 'Processando', value: gameplays.processing, sub: gameplays.processing > 0 ? 'em análise' : 'tudo ok' },
        { label: 'Vídeos', value: videos.total, sub: jobs.running > 0 ? 'produzindo' : 'em pausa' },
        { label: 'Publicados', value: videos.published, sub: 'no YouTube' },
      ];

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView
        style={styles.scroll}
        refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={() => { refetch(); }} tintColor={colors.accent} />}
      >
        {/* Header */}
        <View style={styles.header}>
          <View style={{ flex: 1 }}>
            <Text style={styles.title}>Dashboard</Text>
            <Text style={styles.subtitle}>Sua máquina de produção de conteúdo</Text>
            {dash?.channel_domain && (
              <View style={styles.domainBadge}>
                <Text style={styles.domainText}>{dash.channel_domain}</Text>
              </View>
            )}
          </View>
          <Button
            title={automationRunning ? 'Pausar' : 'Iniciar'}
            variant={automationRunning ? 'danger' : 'primary'}
            onPress={handleToggleAutomation}
            loading={toggling}
            size="sm"
          />
        </View>

        {/* Stats grid */}
        <View style={styles.statsGrid}>
          {stats.map((s, i) => (
            <Card key={i} style={styles.statCard} padding={spacing.md}>
              <Text style={styles.statLabel}>{s.label}</Text>
              <Text style={styles.statValue}>{s.value}</Text>
              <Text style={styles.statSub}>{s.sub}</Text>
            </Card>
          ))}
        </View>

        {/* YouTube card */}
        <Card style={styles.sectionCard}>
          <View style={styles.cardHeader}>
            <Text style={styles.cardTitle}>YouTube</Text>
          </View>
          {dash?.youtube_connected ? (
            <View>
              <View style={styles.row}>
                <Badge label="Conectado" variant="success" />
                <Text style={styles.channelName}>{dash?.youtube_channel || 'Conectado'}</Text>
              </View>
              <Text style={styles.muted}>Vídeos serão publicados automaticamente.</Text>
              <Button
                title="Desconectar"
                variant="danger"
                size="sm"
                onPress={handleDisconnectYouTube}
                style={{ marginTop: spacing.sm }}
              />
            </View>
          ) : (
            <View>
              <Text style={styles.muted}>Não conectado</Text>
              <Button
                title="Conectar YouTube"
                variant="outline"
                size="sm"
                onPress={handleConnectYouTube}
                loading={ytConnecting}
                style={{ marginTop: spacing.sm }}
              />
            </View>
          )}
        </Card>

        {/* Automation status */}
        <Card style={styles.sectionCard}>
          <View style={styles.cardHeader}>
            <Text style={styles.cardTitle}>Automação</Text>
            <TouchableOpacity onPress={() => queryClient.invalidateQueries({ queryKey: ['dashboard'] })}>
              <Icon name="refresh" size={16} color={colors.textMuted} />
            </TouchableOpacity>
          </View>
          <View style={styles.row}>
            <Text style={styles.muted}>Status</Text>
            <Badge
              label={automationRunning ? 'Produzindo' : dash?.automation_status === 'paused' ? 'Pausada' : 'Parada'}
              variant={automationRunning ? 'success' : 'default'}
            />
          </View>
          <View style={styles.row}>
            <Text style={styles.muted}>Sendo produzido</Text>
            <Text style={styles.valueText}>{jobs.running} {jobs.running === 1 ? 'vídeo' : 'vídeos'}</Text>
          </View>
        </Card>

        {/* Worker Status */}
        {workers && workers.length > 0 && (
          <Card style={styles.sectionCard}>
            <View style={styles.cardHeader}>
              <Text style={styles.cardTitle}>Worker</Text>
            </View>
            {workers.map((w: any, i: number) => {
              const isOnline = w.status === 'online' || w.status === 'busy';
              const isBusy = w.status === 'busy';
              return (
                <View key={w.worker_id || i} style={styles.workerRow}>
                  <View style={[styles.workerDot, { backgroundColor: isBusy ? colors.accentWarm : isOnline ? colors.success : colors.textMuted }]} />
                  <View style={{ flex: 1 }}>
                    <Text style={styles.workerName}>{w.hostname || w.worker_id}</Text>
                    <Text style={styles.workerStatus}>
                      {isBusy ? `Ocupado: ${w.current_activity || 'processando'}` : isOnline ? 'Online' : 'Offline'}
                    </Text>
                  </View>
                  {w.gpu_name && (
                    <View style={styles.gpuBadge}>
                      <Icon name="chip" size={10} color={colors.textMuted} />
                      <Text style={styles.gpuText} numberOfLines={1}>{w.gpu_name}</Text>
                    </View>
                  )}
                </View>
              );
            })}
          </Card>
        )}

        {/* Recent videos */}
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>Vídeos produzidos</Text>
          {recentVideos.length > 5 && (
            <TouchableOpacity onPress={() => queryClient.invalidateQueries({ queryKey: ['videos'] })}>
              <Text style={styles.seeAllText}>Ver todos</Text>
            </TouchableOpacity>
          )}
        </View>
        {recentVideos.length === 0 ? (
          <Card>
            <EmptyState
              title="Nenhum vídeo produzido ainda"
              description="Inicie a automação para começar a produzir conteúdo."
            />
          </Card>
        ) : (
          <FlatList
            horizontal
            data={recentVideos.slice(0, 5)}
            keyExtractor={(v) => String(v.id)}
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={{ gap: spacing.md, paddingHorizontal: spacing.lg }}
            renderItem={({ item: v }) => {
              const title = v.social_title || v.topic || '—';
              const isPublished = v.status === 'published' && v.youtube_url;
              const canPublish = v.status === 'pending_approval' || v.status === 'publish_failed';
              const statusCfg = VIDEO_STATUS_CONFIG[v.status] || VIDEO_STATUS_CONFIG.pending;
              return (
                <TouchableOpacity style={styles.videoCard} onPress={() => setPlaying(v)}>
                  <View style={styles.thumbnail}>
                    {v.thumbnail_path ? (
                      <Image source={{ uri: thumbUrl(v.id) }} style={styles.thumbImg} />
                    ) : (
                      <View style={styles.thumbPlaceholder} />
                    )}
                    <View style={styles.qaBadge}>
                      <Badge
                        label={`QA ${v.qa_score?.toFixed(0) || 0}`}
                        variant={v.qa_passed ? 'success' : 'error'}
                      />
                    </View>
                    {isPublished && (
                      <View style={styles.ytBadge}>
                        <Badge label="YouTube" variant="info" />
                      </View>
                    )}
                  </View>
                  <Text style={styles.videoTitle} numberOfLines={2}>{title}</Text>
                  <View style={styles.videoMeta}>
                    <Text style={styles.metaText}>{fmtDuration(v.duration)}</Text>
                    <Text style={styles.metaText}>{fmtDate(v.created_at)}</Text>
                  </View>
                  <View style={styles.videoActions}>
                    <Badge label={statusCfg.label} variant={statusCfg.variant} />
                    {canPublish && (
                      <Button
                        title="Publicar"
                        size="sm"
                        variant="outline"
                        loading={publishing === v.id}
                        onPress={() => handlePublish(v.id)}
                      />
                    )}
                  </View>
                </TouchableOpacity>
              );
            }}
          />
        )}
      </ScrollView>

      {/* Video player modal */}
      <Modal visible={!!playing} animationType="slide" presentationStyle="pageSheet" onRequestClose={() => setPlaying(null)}>
        <SafeAreaView style={styles.playerContainer} edges={['top']}>
          <View style={styles.playerHeader}>
            <TouchableOpacity onPress={() => setPlaying(null)}>
              <Text style={styles.closeButton}>Fechar</Text>
            </TouchableOpacity>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.md }}>
              {playing && (
                <TouchableOpacity
                  onPress={() => handleDirectShare(playing)}
                  disabled={sharing}
                  style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}
                >
                  <Icon name="share-variant" size={18} color={colors.accent} />
                  <Text style={styles.shareButton}>{sharing ? 'Enviando...' : 'Compartilhar'}</Text>
                </TouchableOpacity>
              )}
              {playing?.youtube_url && (
                <TouchableOpacity onPress={() => Linking.openURL(playing.youtube_url)}>
                  <Text style={styles.shareButton}>YouTube</Text>
                </TouchableOpacity>
              )}
            </View>
          </View>
          {playing && (
            <ScrollView style={styles.playerScroll} contentContainerStyle={{ flexGrow: 1 }}>
              <View style={styles.videoPlayer}>
                <Video
                  source={{ uri: videoUrl(playing.id) }}
                  style={styles.video}
                  controls
                  resizeMode="contain"
                />
              </View>
              <View style={styles.playerInfo}>
                <Text style={styles.playerTitle}>
                  {playing.social_title || playing.topic || `Vídeo #${playing.id}`}
                </Text>
                {playing.social_description && (
                  <Text style={styles.playerDesc}>{playing.social_description}</Text>
                )}
                {playing.social_tags?.length > 0 && (
                  <View style={styles.tagsRow}>
                    {playing.social_tags.map((tag: string, i: number) => (
                      <View key={i} style={styles.tag}>
                        <Text style={styles.tagText}>#{tag}</Text>
                      </View>
                    ))}
                  </View>
                )}
                <View style={styles.playerMeta}>
                  <Text style={styles.metaText}>{fmtDuration(playing.duration)}</Text>
                  {playing.width > 0 && <Text style={styles.metaText}>{playing.width}×{playing.height}</Text>}
                  <Text style={styles.metaText}>{fmtDate(playing.created_at)}</Text>
                  {playing.youtube_url && (
                    <TouchableOpacity onPress={() => Linking.openURL(playing.youtube_url)}>
                      <Text style={styles.ytLink}>Abrir no YouTube</Text>
                    </TouchableOpacity>
                  )}
                </View>

                {/* Direct share — the mobile particularity */}
                {sharing ? (
                  <View style={styles.dashShareProgress}>
                    <Text style={styles.dashShareProgressText}>Baixando vídeo... {shareProgress}%</Text>
                    <View style={styles.dashShareProgressBar}>
                      <View style={[styles.dashShareProgressFill, { width: `${shareProgress}%` }]} />
                    </View>
                    <Text style={styles.dashShareProgressHint}>
                      O menu de compartilhamento vai abrir automaticamente.
                    </Text>
                  </View>
                ) : (
                  <Button
                    title="Compartilhar nas redes sociais"
                    variant="primary"
                    icon={<Icon name="share-variant" size={18} color="#fff" />}
                    onPress={() => handleDirectShare(playing)}
                    style={{ marginTop: spacing.md }}
                  />
                )}
              </View>
            </ScrollView>
          )}
        </SafeAreaView>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  scroll: {
    flex: 1,
    paddingHorizontal: spacing.lg,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    paddingVertical: spacing.lg,
  },
  title: {
    fontSize: fontSize.xxxl,
    fontWeight: fontWeight.bold,
    color: colors.text,
  },
  subtitle: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
    marginTop: spacing.xs,
  },
  domainBadge: {
    marginTop: spacing.xs,
    backgroundColor: 'rgba(45,212,191,0.1)',
    borderWidth: 1,
    borderColor: 'rgba(45,212,191,0.2)',
    borderRadius: radius.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    alignSelf: 'flex-start',
  },
  domainText: {
    fontSize: fontSize.xs,
    color: colors.accent,
    fontWeight: fontWeight.medium,
  },
  statsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.md,
    marginBottom: spacing.lg,
  },
  statCard: {
    flex: 1,
    minWidth: '45%',
  },
  statLabel: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
    fontWeight: fontWeight.medium,
    textTransform: 'uppercase',
  },
  statValue: {
    fontSize: fontSize.xxxl,
    fontWeight: fontWeight.bold,
    color: colors.text,
    marginTop: spacing.xs,
  },
  statSub: {
    fontSize: fontSize.xs,
    color: colors.accent,
    marginTop: spacing.xs,
  },
  sectionCard: {
    marginBottom: spacing.md,
  },
  cardHeader: {
    marginBottom: spacing.md,
  },
  cardTitle: {
    fontSize: fontSize.md,
    fontWeight: fontWeight.semibold,
    color: colors.text,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: spacing.xs,
  },
  muted: {
    fontSize: fontSize.sm,
    color: colors.textMuted,
  },
  channelName: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.medium,
    color: colors.text,
  },
  valueText: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.medium,
    color: colors.text,
  },
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginVertical: spacing.md,
  },
  sectionTitle: {
    fontSize: fontSize.lg,
    fontWeight: fontWeight.semibold,
    color: colors.text,
  },
  seeAllText: {
    fontSize: fontSize.sm,
    color: colors.accent,
    fontWeight: fontWeight.medium,
  },
  // Worker status
  workerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingVertical: spacing.xs,
  },
  workerDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  workerName: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.medium,
    color: colors.text,
  },
  workerStatus: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
    marginTop: 2,
  },
  gpuBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: colors.surfaceElevated,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.xs,
    paddingVertical: 2,
    maxWidth: 120,
  },
  gpuText: {
    fontSize: 10,
    color: colors.textMuted,
  },
  videoCard: {
    width: 160,
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    overflow: 'hidden',
  },
  thumbnail: {
    width: '100%',
    aspectRatio: 9 / 16,
    backgroundColor: colors.surfaceElevated,
    position: 'relative',
  },
  thumbImg: {
    width: '100%',
    height: '100%',
  },
  thumbPlaceholder: {
    width: '100%',
    height: '100%',
  },
  qaBadge: {
    position: 'absolute',
    bottom: spacing.xs,
    right: spacing.xs,
  },
  ytBadge: {
    position: 'absolute',
    top: spacing.xs,
    left: spacing.xs,
  },
  videoTitle: {
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
    color: colors.text,
    padding: spacing.sm,
    minHeight: 32,
  },
  videoMeta: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.sm,
    paddingBottom: spacing.xs,
  },
  metaText: {
    fontSize: 10,
    color: colors.textMuted,
  },
  videoActions: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.sm,
    paddingBottom: spacing.sm,
  },
  playerContainer: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  playerScroll: {
    flex: 1,
  },
  playerHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  closeButton: {
    fontSize: fontSize.base,
    color: colors.accent,
  },
  shareButton: {
    fontSize: fontSize.base,
    color: colors.youtube,
    fontWeight: fontWeight.medium,
  },
  ytLink: {
    fontSize: 10,
    color: colors.youtube,
  },
  // Share progress
  dashShareProgress: {
    marginTop: spacing.md,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    gap: spacing.sm,
  },
  dashShareProgressText: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.semibold,
    color: colors.accent,
    textAlign: 'center',
  },
  dashShareProgressBar: {
    height: 6,
    backgroundColor: colors.surfaceElevated,
    borderRadius: radius.full,
    overflow: 'hidden',
  },
  dashShareProgressFill: {
    height: '100%',
    backgroundColor: colors.accent,
    borderRadius: radius.full,
  },
  dashShareProgressHint: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
    textAlign: 'center',
    lineHeight: 16,
  },
  videoPlayer: {
    width: '100%',
    height: 420,
    backgroundColor: '#000',
  },
  video: {
    width: '100%',
    height: 420,
  },
  playerInfo: {
    padding: spacing.lg,
    gap: spacing.md,
  },
  playerTitle: {
    fontSize: fontSize.md,
    fontWeight: fontWeight.semibold,
    color: colors.text,
  },
  playerDesc: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
  },
  tagsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
  },
  tag: {
    backgroundColor: colors.surfaceElevated,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  tagText: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
  },
  playerMeta: {
    flexDirection: 'row',
    gap: spacing.md,
  },
});
