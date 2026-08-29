import React, { useState, useCallback, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  RefreshControl,
  TextInput,
  TouchableOpacity,
  Image,
  Alert,
  Linking,
  Modal,
  ScrollView,
} from 'react-native';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useLiveData } from '../hooks/useLiveData';
import { SafeAreaView } from 'react-native-safe-area-context';
import Video from 'react-native-video';
import Icon from 'react-native-vector-icons/MaterialCommunityIcons';
import { videosApi } from '../api/endpoints';
import { Card, Badge, Button, EmptyState, Spinner } from '../components/ui';
import { colors } from '../theme/colors';
import { fontSize, fontWeight, radius, spacing } from '../theme/spacing';
import { fmtDuration, fmtDate } from '../utils/format';
import { videoUrl, thumbUrl } from '../api/client';
import {
  downloadVideo,
  shareVideoFile,
  shareYouTubeUrl,
  copyVideoMetadata,
  getLocalVideoPath,
  VideoShareData,
} from '../utils/shareVideo';
import { useBackHandler } from '../hooks/useBackHandler';
import Toast from 'react-native-toast-message';

const VIDEO_STATUS_CONFIG: Record<string, { label: string; variant: any }> = {
  pending_approval: { label: 'Aguardando', variant: 'warning' },
  published: { label: 'Publicado', variant: 'success' },
  publish_failed: { label: 'Falha pub', variant: 'error' },
  draft: { label: 'Rascunho', variant: 'default' },
  failed: { label: 'Falhou', variant: 'error' },
  rendering: { label: 'Renderizando', variant: 'info' },
  pending: { label: 'Pendente', variant: 'default' },
  ready: { label: 'Pronto', variant: 'success' },
  qa_passed: { label: 'QA OK', variant: 'success' },
  qa_failed: { label: 'QA Falhou', variant: 'error' },
};

type DownloadState = 'idle' | 'checking' | 'downloading' | 'ready' | 'error';

export function VideosScreen() {
  const [search, setSearch] = useState('');
  const [playing, setPlaying] = useState<any | null>(null);
  const [publishVideo, setPublishVideo] = useState<any | null>(null);
  const [editVideo, setEditVideo] = useState<any | null>(null);
  const [sharing, setSharing] = useState(false);
  const [shareProgress, setShareProgress] = useState(0);
  const queryClient = useQueryClient();

  // Close modals on Android back button
  useBackHandler(() => {
    if (editVideo) { setEditVideo(null); return; }
    if (publishVideo) { setPublishVideo(null); return; }
    if (playing) { setPlaying(null); return; }
  }, !!playing || !!publishVideo || !!editVideo);

  const { data: videos, refetch, isRefetching, isLoading } = useLiveData(
    ['videos', search],
    () => videosApi.list(search || undefined),
    ['video.created', 'video.updated']
  );

  const handleDelete = useCallback((video: any) => {
    Alert.alert(
      'Deletar vídeo?',
      `Tem certeza que deseja deletar "${video.social_title || video.topic}"?`,
      [
        { text: 'Cancelar', style: 'cancel' },
        {
          text: 'Deletar',
          style: 'destructive',
          onPress: () => {
            // Second step: ask about releasing clips
            Alert.alert(
              'Liberar trechos?',
              'Liberar os trechos de gameplay usados neste vídeo para reutilização? A ideia também pode ser liberada para reuso.',
              [
                { text: 'Não liberar', style: 'cancel', onPress: () => doDelete(video, false) },
                { text: 'Liberar trechos', onPress: () => doDelete(video, true) },
              ],
            );
          },
        },
      ],
    );
  }, [queryClient]);

  const doDelete = async (video: any, releaseClips: boolean) => {
    try {
      await videosApi.delete(video.id, releaseClips);
      Toast.show({
        type: 'success',
        text1: 'Vídeo deletado',
        text2: releaseClips ? 'Trechos liberados para reuso' : undefined,
      });
      queryClient.invalidateQueries({ queryKey: ['videos'] });
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    }
  };

  const handlePublish = async (videoId: number) => {
    try {
      await videosApi.publish(videoId);
      Toast.show({ type: 'success', text1: 'Publicação no YouTube solicitada' });
      queryClient.invalidateQueries({ queryKey: ['videos'] });
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro ao publicar' });
    }
  };

  const handleRegenerate = async (video: any) => {
    Alert.alert(
      'Regenerar vídeo?',
      `Isso vai criar um novo job de geração para "${video.social_title || video.topic}".\n\nO vídeo atual não será removido — um novo será gerado em paralelo.`,
      [
        { text: 'Cancelar', style: 'cancel' },
        {
          text: 'Regenerar',
          onPress: async () => {
            try {
              await videosApi.regenerate(video.id);
              Toast.show({ type: 'success', text1: 'Regeneração solicitada' });
              queryClient.invalidateQueries({ queryKey: ['videos'] });
              queryClient.invalidateQueries({ queryKey: ['jobs'] });
            } catch (err: any) {
              Toast.show({ type: 'error', text1: err.message || 'Erro' });
            }
          },
        },
      ],
    );
  };

  const handleCopyMetadata = (video: any) => {
    copyVideoMetadata(video);
    Toast.show({
      type: 'success',
      text1: 'Legenda copiada',
      text2: 'Título, descrição e tags na área de transferência',
    });
  };

  const openPublishModal = (video: any) => {
    setPublishVideo(video);
  };

  // Direct share: download → copy metadata → open Android share sheet
  const handleDirectShare = async (video: any) => {
    const isPublished = video.status === 'published' && video.youtube_url;
    // For published videos, just share the YouTube URL (no download needed)
    if (isPublished) {
      try {
        await shareYouTubeUrl(video);
        Toast.show({
          type: 'success',
          text1: 'Legenda copiada',
          text2: 'Escolha o app para compartilhar',
        });
      } catch (err: any) {
        if (!String(err.message || '').includes('cancel') && err.message !== 'User did not share') {
          Toast.show({ type: 'error', text1: err.message || 'Erro ao compartilhar' });
        }
      }
      return;
    }

    // For unpublished videos: download first, then share
    setSharing(true);
    setShareProgress(0);
    try {
      // Check if already downloaded
      let path = await getLocalVideoPath(video.id);
      if (!path) {
        Toast.show({
          type: 'info',
          text1: 'Baixando vídeo...',
          text2: 'O compartilhamento vai abrir automaticamente',
        });
        path = await downloadVideo(video.id, (pct) => setShareProgress(pct));
      }
      // Copy metadata + open share sheet
      await shareVideoFile(video, path);
      Toast.show({
        type: 'success',
        text1: 'Legenda copiada',
        text2: 'Cole no app da rede social',
      });
    } catch (err: any) {
      if (!String(err.message || '').includes('cancel') && err.message !== 'User did not share') {
        Toast.show({ type: 'error', text1: err.message || 'Erro ao compartilhar' });
      }
    } finally {
      setSharing(false);
      setShareProgress(0);
    }
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Search */}
      <View style={styles.searchContainer}>
        <TextInput
          style={styles.searchInput}
          placeholder="Buscar vídeos..."
          placeholderTextColor={colors.textMuted}
          value={search}
          onChangeText={setSearch}
        />
      </View>

      <FlatList
        data={videos || []}
        keyExtractor={(v) => String(v.id)}
        numColumns={2}
        refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={() => { refetch(); }} tintColor={colors.accent} />}
        contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}
        ListEmptyComponent={
          isLoading ? (
            <View style={{ paddingVertical: spacing.xxl, alignItems: 'center' }}>
              <Spinner size="large" />
            </View>
          ) : (
            <Card>
              <EmptyState title="Nenhum vídeo" description="Os vídeos produzidos aparecerão aqui." />
            </Card>
          )
        }
        renderItem={({ item: v }) => {
          const title = v.social_title || v.topic || '—';
          const isPublished = v.status === 'published' && v.youtube_url;
          const canPublish = v.status === 'pending_approval' || v.status === 'publish_failed';
          const statusCfg = VIDEO_STATUS_CONFIG[v.status] || VIDEO_STATUS_CONFIG.pending;
          return (
            <TouchableOpacity style={styles.videoCard} onPress={() => setPlaying(v)} activeOpacity={0.8}>
              <View style={styles.thumbnail}>
                {v.thumbnail_path ? (
                  <Image source={{ uri: thumbUrl(v.id) }} style={styles.thumbImg} />
                ) : (
                  <View style={styles.thumbPlaceholder} />
                )}
                <View style={styles.qaBadge}>
                  <Badge label={`QA ${v.qa_score?.toFixed(0) || 0}`} variant={v.qa_passed ? 'success' : 'error'} />
                </View>
              </View>
              <View style={styles.videoInfo}>
                <Text style={styles.videoTitle} numberOfLines={2}>{title}</Text>
                <View style={styles.videoMeta}>
                  <Text style={styles.metaText}>{fmtDuration(v.duration)}</Text>
                  <Text style={styles.metaText}>{fmtDate(v.created_at)}</Text>
                </View>
                <Badge label={statusCfg.label} variant={statusCfg.variant} />
                <View style={styles.actions}>
                  <Button title="Publicar" size="sm" variant="outline" onPress={() => openPublishModal(v)} />
                  {isPublished && (
                    <Button
                      title="YouTube"
                      size="sm"
                      variant="outline"
                      onPress={() => Linking.openURL(v.youtube_url)}
                    />
                  )}
                  {canPublish && (
                    <Button title="Pub. YT" size="sm" variant="primary" onPress={() => handlePublish(v.id)} />
                  )}
                  <Button title="Editar" size="sm" variant="ghost" onPress={() => setEditVideo(v)} />
                  <Button title="Regenerar" size="sm" variant="ghost" onPress={() => handleRegenerate(v)} />
                  <Button title="Deletar" size="sm" variant="danger" onPress={() => handleDelete(v)} />
                </View>
              </View>
            </TouchableOpacity>
          );
        }}
      />

      {/* Video player modal with rich metadata */}
      <Modal
        visible={!!playing}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setPlaying(null)}
      >
        <SafeAreaView style={styles.playerContainer} edges={['top']}>
          <View style={styles.playerHeader}>
            <TouchableOpacity onPress={() => setPlaying(null)}>
              <Text style={styles.closeButton}>Fechar</Text>
            </TouchableOpacity>
            <View style={styles.playerHeaderActions}>
              {playing && (
                <TouchableOpacity
                  onPress={() => handleDirectShare(playing)}
                  disabled={sharing}
                  style={{ marginRight: spacing.md, flexDirection: 'row', alignItems: 'center', gap: 4 }}
                >
                  <Icon name="share-variant" size={18} color={colors.accent} />
                  <Text style={styles.shareButton}>{sharing ? 'Enviando...' : 'Compartilhar'}</Text>
                </TouchableOpacity>
              )}
              {playing && (
                <TouchableOpacity onPress={() => { setPlaying(null); openPublishModal(playing); }}>
                  <Text style={styles.shareButton}>Publicar</Text>
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
                </View>

                {/* Rich metadata */}
                <RichMetadata video={playing} />

                {/* YouTube publish / open buttons */}
                {playing.youtube_url ? (
                  <Button
                    title="Abrir no YouTube"
                    variant="outline"
                    onPress={() => Linking.openURL(playing.youtube_url)}
                    style={{ marginTop: spacing.sm }}
                  />
                ) : (playing.status === 'pending_approval' || playing.status === 'publish_failed') && (
                  <Button
                    title="Publicar no YouTube"
                    variant="primary"
                    onPress={() => { handlePublish(playing.id); setPlaying(null); }}
                    style={{ marginTop: spacing.sm }}
                  />
                )}

                {/* Direct share button — the mobile particularity */}
                {sharing ? (
                  <View style={styles.shareProgressWrap}>
                    <Text style={styles.shareProgressText}>Baixando vídeo... {shareProgress}%</Text>
                    <View style={styles.shareProgressBar}>
                      <View style={[styles.shareProgressFill, { width: `${shareProgress}%` }]} />
                    </View>
                    <Text style={styles.shareProgressHint}>
                      O menu de compartilhamento vai abrir automaticamente quando o download terminar.
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

                <Button
                  title="Editar metadados"
                  variant="ghost"
                  size="sm"
                  onPress={() => { const v = playing; setPlaying(null); setEditVideo(v); }}
                  style={{ marginTop: spacing.sm }}
                />
                <Button
                  title="Copiar legenda"
                  variant="ghost"
                  size="sm"
                  onPress={() => handleCopyMetadata(playing)}
                  style={{ marginTop: spacing.sm }}
                />
              </View>
            </ScrollView>
          )}
        </SafeAreaView>
      </Modal>

      {/* Publish / Share modal */}
      {publishVideo && (
        <PublishModal
          video={publishVideo}
          onClose={() => setPublishVideo(null)}
          onCopyMetadata={handleCopyMetadata}
        />
      )}

      {/* Edit metadata modal */}
      {editVideo && (
        <EditMetadataModal
          video={editVideo}
          onClose={() => setEditVideo(null)}
          onSaved={() => {
            setEditVideo(null);
            queryClient.invalidateQueries({ queryKey: ['videos'] });
          }}
        />
      )}
    </SafeAreaView>
  );
}

// ── Rich Metadata Component ──────────────────────────────────────────────────

function RichMetadata({ video }: { video: any }) {
  const hasRich = video.idea_title || video.game_name || video.content_plan_topic || video.script_text || video.clips_count;
  if (!hasRich) return null;

  return (
    <View style={styles.richMetaWrap}>
      {video.idea_title && (
        <View style={styles.richMetaRow}>
          <Icon name="lightbulb-outline" size={14} color={colors.textMuted} />
          <Text style={styles.richMetaLabel}>Ideia:</Text>
          <Text style={styles.richMetaValue} numberOfLines={2}>{video.idea_title}</Text>
        </View>
      )}
      {video.game_name && (
        <View style={styles.richMetaRow}>
          <Icon name="gamepad-variant" size={14} color={colors.textMuted} />
          <Text style={styles.richMetaLabel}>Jogo:</Text>
          <Text style={styles.richMetaValue}>{video.game_name}</Text>
        </View>
      )}
      {video.content_plan_topic && (
        <View style={styles.richMetaRow}>
          <Icon name="file-document-outline" size={14} color={colors.textMuted} />
          <Text style={styles.richMetaLabel}>Plano:</Text>
          <Text style={styles.richMetaValue} numberOfLines={2}>{video.content_plan_topic}</Text>
        </View>
      )}
      {video.creative_style && (
        <View style={styles.richMetaRow}>
          <Icon name="palette" size={14} color={colors.textMuted} />
          <Text style={styles.richMetaLabel}>Estilo:</Text>
          <Text style={styles.richMetaValue}>{video.creative_style}</Text>
        </View>
      )}
      {video.clips_count > 0 && (
        <View style={styles.richMetaRow}>
          <Icon name="film" size={14} color={colors.textMuted} />
          <Text style={styles.richMetaLabel}>Clips:</Text>
          <Text style={styles.richMetaValue}>{video.clips_count} trechos usados</Text>
        </View>
      )}
      {video.originality_score != null && (
        <View style={styles.richMetaRow}>
          <Icon name="shield-check" size={14} color={colors.textMuted} />
          <Text style={styles.richMetaLabel}>Originalidade:</Text>
          <Text style={styles.richMetaValue}>{video.originality_score.toFixed(0)}%</Text>
        </View>
      )}
      {video.script_text && (
        <View style={styles.scriptWrap}>
          <Text style={styles.scriptLabel}>Roteiro:</Text>
          <Text style={styles.scriptText} numberOfLines={6}>{video.script_text}</Text>
        </View>
      )}
    </View>
  );
}

// ── Edit Metadata Modal ──────────────────────────────────────────────────────

function EditMetadataModal({ video, onClose, onSaved }: { video: any; onClose: () => void; onSaved: () => void }) {
  const [title, setTitle] = useState(video.social_title || video.topic || '');
  const [description, setDescription] = useState(video.social_description || '');
  const [tags, setTags] = useState((video.social_tags || []).join(', '));
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      const parsedTags = tags.split(',').map((t: string) => t.trim().replace(/^#/, '')).filter(Boolean);
      await videosApi.updateMetadata(video.id, {
        title: title.trim(),
        description: description.trim(),
        tags: parsedTags,
      });
      Toast.show({ type: 'success', text1: 'Metadados salvos' });
      onSaved();
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal visible animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <SafeAreaView style={styles.publishContainer} edges={['top']}>
        <View style={styles.publishHeader}>
          <TouchableOpacity onPress={onClose}>
            <Text style={styles.closeButton}>Cancelar</Text>
          </TouchableOpacity>
          <Text style={styles.publishTitle}>Editar Metadados</Text>
          <View style={{ width: 60 }} />
        </View>
        <ScrollView contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
          <View>
            <Text style={styles.editLabel}>Título</Text>
            <TextInput
              style={styles.editInput}
              value={title}
              onChangeText={setTitle}
              placeholder="Título do vídeo"
              placeholderTextColor={colors.textMuted}
            />
          </View>
          <View>
            <Text style={styles.editLabel}>Descrição</Text>
            <TextInput
              style={styles.editTextArea}
              value={description}
              onChangeText={setDescription}
              placeholder="Descrição do vídeo..."
              placeholderTextColor={colors.textMuted}
              multiline
              numberOfLines={5}
              textAlignVertical="top"
            />
          </View>
          <View>
            <Text style={styles.editLabel}>Tags (separadas por vírgula)</Text>
            <TextInput
              style={styles.editInput}
              value={tags}
              onChangeText={setTags}
              placeholder="Ex: games, curiosidade, gameplay"
              placeholderTextColor={colors.textMuted}
            />
          </View>
          <Button title="Salvar" variant="primary" fullWidth onPress={handleSave} loading={saving} />
        </ScrollView>
      </SafeAreaView>
    </Modal>
  );
}

// ── Publish Modal ────────────────────────────────────────────────────────────

function PublishModal({
  video,
  onClose,
  onCopyMetadata,
}: {
  video: any;
  onClose: () => void;
  onCopyMetadata: (v: any) => void;
}) {
  const [downloadState, setDownloadState] = useState<DownloadState>('checking');
  const [progress, setProgress] = useState(0);
  const [localPath, setLocalPath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);

  const isPublished = video.status === 'published' && video.youtube_url;
  const title = video.social_title || video.topic || `Vídeo #${video.id}`;

  // Check if video is already downloaded on mount
  useEffect(() => {
    if (isPublished) {
      setDownloadState('ready');
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const path = await getLocalVideoPath(video.id);
        if (cancelled) return;
        if (path) {
          setLocalPath(path);
          setDownloadState('ready');
        } else {
          setDownloadState('idle');
        }
      } catch {
        if (!cancelled) setDownloadState('idle');
      }
    })();
    return () => { cancelled = true; };
  }, [video.id, isPublished]);

  const handleDownload = async () => {
    setDownloadState('downloading');
    setProgress(0);
    setError(null);
    try {
      const path = await downloadVideo(video.id, (pct) => setProgress(pct));
      setLocalPath(path);
      setDownloadState('ready');
      Toast.show({
        type: 'success',
        text1: 'Download concluído',
        text2: 'Vídeo pronto para publicar',
      });
    } catch (err: any) {
      setDownloadState('error');
      setError(err.message || 'Erro no download');
    }
  };

  const handleShare = async () => {
    setSharing(true);
    try {
      if (isPublished) {
        await shareYouTubeUrl(video);
      } else if (localPath) {
        await shareVideoFile(video, localPath);
      }
    } catch (err: any) {
      if (err.message !== 'User did not share' && !String(err.message || '').includes('cancel')) {
        Toast.show({ type: 'error', text1: err.message || 'Erro ao compartilhar' });
      }
    } finally {
      setSharing(false);
    }
  };

  return (
    <Modal visible animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <SafeAreaView style={styles.publishContainer} edges={['top']}>
        {/* Header */}
        <View style={styles.publishHeader}>
          <TouchableOpacity onPress={onClose}>
            <Text style={styles.closeButton}>Fechar</Text>
          </TouchableOpacity>
          <Text style={styles.publishTitle}>Publicar vídeo</Text>
          <View style={{ width: 60 }} />
        </View>

        <ScrollView
          style={styles.publishScroll}
          contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}
        >
          {/* Video info */}
          <View style={styles.publishVideoInfo}>
            <Text style={styles.publishVideoTitle}>{title}</Text>
            {video.social_description && (
              <Text style={styles.publishVideoDesc}>{video.social_description}</Text>
            )}
            {video.social_tags?.length > 0 && (
              <View style={styles.tagsRow}>
                {video.social_tags.map((tag: string, i: number) => (
                  <View key={i} style={styles.tag}>
                    <Text style={styles.tagText}>#{tag}</Text>
                  </View>
                ))}
              </View>
            )}
          </View>

          {/* Status card */}
          {isPublished ? (
            <View style={styles.statusCard}>
              <View style={styles.statusIconWrap}>
                <Text style={styles.statusIcon}>✓</Text>
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.statusTitle}>Vídeo no YouTube</Text>
                <Text style={styles.statusDesc}>
                  Este vídeo já está publicado. Você pode compartilhar o link do YouTube.
                </Text>
              </View>
            </View>
          ) : downloadState === 'checking' ? (
            <View style={styles.statusCard}>
              <Spinner />
              <Text style={styles.statusDesc}>Verificando se o vídeo já está no dispositivo...</Text>
            </View>
          ) : downloadState === 'idle' ? (
            <View style={styles.statusCardWarn}>
              <View style={styles.statusIconWrapWarn}>
                <Text style={styles.statusIconWarn}>↓</Text>
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.statusTitle}>Download necessário</Text>
                <Text style={styles.statusDesc}>
                  Para publicar nas redes sociais (Instagram, TikTok, etc.), o vídeo precisa ser baixado para o dispositivo. O download acontece em segundo plano — você pode sair do app e voltar depois.
                </Text>
              </View>
            </View>
          ) : downloadState === 'downloading' ? (
            <View style={styles.statusCard}>
              <View style={styles.downloadProgressWrap}>
                <Text style={styles.downloadPct}>{progress}%</Text>
                <View style={styles.downloadBar}>
                  <View style={[styles.downloadFill, { width: `${progress}%` }]} />
                </View>
                <Text style={styles.statusDesc}>
                  Baixando vídeo para o dispositivo...{'\n'}
                  Você pode sair do app que o download continua.
                </Text>
              </View>
            </View>
          ) : downloadState === 'ready' ? (
            <View style={styles.statusCardSuccess}>
              <View style={styles.statusIconWrapSuccess}>
                <Text style={styles.statusIconSuccess}>✓</Text>
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.statusTitle}>Vídeo pronto</Text>
                <Text style={styles.statusDesc}>
                  {isPublished
                    ? 'Link do YouTube pronto para compartilhar.'
                    : 'Vídeo baixado e pronto para publicar nas redes sociais.'}
                </Text>
              </View>
            </View>
          ) : downloadState === 'error' ? (
            <View style={styles.statusCardError}>
              <View style={styles.statusIconWrapError}>
                <Text style={styles.statusIconError}>✕</Text>
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.statusTitle}>Erro no download</Text>
                <Text style={styles.statusDesc}>{error || 'Tente novamente.'}</Text>
              </View>
            </View>
          ) : null}

          {/* Actions */}
          <View style={styles.publishActions}>
            {/* Download button */}
            {!isPublished && downloadState === 'idle' && (
              <Button
                title="Baixar vídeo"
                variant="primary"
                onPress={handleDownload}
              />
            )}
            {!isPublished && downloadState === 'error' && (
              <Button
                title="Tentar novamente"
                variant="primary"
                onPress={handleDownload}
              />
            )}

            {/* Share button — only when ready */}
            {downloadState === 'ready' && (
              <Button
                title={sharing ? 'Abrindo...' : isPublished ? 'Compartilhar link do YouTube' : 'Abrir redes sociais'}
                variant="primary"
                loading={sharing}
                onPress={handleShare}
              />
            )}

            {/* Copy metadata */}
            <Button
              title="Copiar legenda (título + descrição + tags)"
              variant="outline"
              size="sm"
              onPress={() => onCopyMetadata(video)}
              style={{ marginTop: spacing.sm }}
            />
          </View>

          {/* Info box */}
          <View style={styles.infoBox}>
            <Text style={styles.infoTitle}>Como funciona:</Text>
            <Text style={styles.infoText}>
              1. A legenda (título, descrição e tags) é copiada para a área de transferência.{'\n'}
              2. O vídeo é baixado para o dispositivo (não ocupa espaço permanente — é limpo automaticamente).{'\n'}
              3. O menu de compartilhamento do Android abre. Escolha Instagram, TikTok, etc.{'\n'}
              4. Quando for escrever a legenda no app da rede social, cole da área de transferência.
            </Text>
          </View>
        </ScrollView>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  searchContainer: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
  },
  searchInput: {
    height: 40,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    fontSize: fontSize.base,
    color: colors.text,
  },
  videoCard: {
    flex: 1,
    margin: spacing.xs,
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
  videoInfo: {
    padding: spacing.sm,
    gap: spacing.xs,
  },
  videoTitle: {
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
    color: colors.text,
    minHeight: 28,
  },
  videoMeta: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  metaText: {
    fontSize: 10,
    color: colors.textMuted,
  },
  actions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
    marginTop: spacing.xs,
  },
  // Player modal
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
  playerHeaderActions: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  closeButton: {
    fontSize: fontSize.base,
    color: colors.accent,
  },
  shareButton: {
    fontSize: fontSize.base,
    color: colors.accent,
    fontWeight: fontWeight.medium,
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
  // Share progress
  shareProgressWrap: {
    marginTop: spacing.md,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    gap: spacing.sm,
  },
  shareProgressText: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.semibold,
    color: colors.accent,
    textAlign: 'center',
  },
  shareProgressBar: {
    height: 6,
    backgroundColor: colors.surfaceElevated,
    borderRadius: radius.full,
    overflow: 'hidden',
  },
  shareProgressFill: {
    height: '100%',
    backgroundColor: colors.accent,
    borderRadius: radius.full,
  },
  shareProgressHint: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
    textAlign: 'center',
    lineHeight: 16,
  },
  // Rich metadata
  richMetaWrap: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    gap: spacing.sm,
  },
  richMetaRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.xs,
  },
  richMetaLabel: { fontSize: fontSize.xs, color: colors.textMuted, fontWeight: fontWeight.medium },
  richMetaValue: { fontSize: fontSize.xs, color: colors.textSecondary, flex: 1 },
  scriptWrap: {
    marginTop: spacing.xs,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: spacing.sm,
  },
  scriptLabel: { fontSize: fontSize.xs, color: colors.textMuted, fontWeight: fontWeight.medium, marginBottom: 4 },
  scriptText: { fontSize: fontSize.xs, color: colors.textSecondary, lineHeight: 18 },
  // Edit modal
  editLabel: { fontSize: fontSize.sm, fontWeight: fontWeight.medium, color: colors.textSecondary, marginBottom: 4 },
  editInput: {
    height: 44,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    fontSize: fontSize.base,
    color: colors.text,
  },
  editTextArea: {
    minHeight: 100,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    fontSize: fontSize.base,
    color: colors.text,
    textAlignVertical: 'top',
  },
  // Publish modal
  publishContainer: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  publishHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  publishTitle: {
    fontSize: fontSize.md,
    fontWeight: fontWeight.semibold,
    color: colors.text,
  },
  publishScroll: {
    flex: 1,
  },
  publishVideoInfo: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    gap: spacing.sm,
  },
  publishVideoTitle: {
    fontSize: fontSize.md,
    fontWeight: fontWeight.semibold,
    color: colors.text,
  },
  publishVideoDesc: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
  },
  // Status cards
  statusCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
  },
  statusCardWarn: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.md,
    backgroundColor: 'rgba(245,158,11,0.08)',
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: 'rgba(245,158,11,0.2)',
    padding: spacing.md,
  },
  statusCardSuccess: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    backgroundColor: 'rgba(16,185,129,0.08)',
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: 'rgba(16,185,129,0.2)',
    padding: spacing.md,
  },
  statusCardError: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    backgroundColor: 'rgba(239,68,68,0.08)',
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: 'rgba(239,68,68,0.2)',
    padding: spacing.md,
  },
  statusIconWrap: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: colors.accent,
    alignItems: 'center',
    justifyContent: 'center',
  },
  statusIcon: {
    color: '#fff',
    fontSize: 20,
    fontWeight: 'bold',
  },
  statusIconWrapWarn: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(245,158,11,0.15)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  statusIconWarn: {
    color: '#f59e0b',
    fontSize: 20,
    fontWeight: 'bold',
  },
  statusIconWrapSuccess: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(16,185,129,0.15)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  statusIconSuccess: {
    color: '#10b981',
    fontSize: 20,
    fontWeight: 'bold',
  },
  statusIconWrapError: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(239,68,68,0.15)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  statusIconError: {
    color: '#ef4444',
    fontSize: 20,
    fontWeight: 'bold',
  },
  statusTitle: {
    fontSize: fontSize.base,
    fontWeight: fontWeight.semibold,
    color: colors.text,
  },
  statusDesc: {
    fontSize: fontSize.sm,
    color: colors.textMuted,
    marginTop: 2,
    lineHeight: 20,
  },
  // Download progress
  downloadProgressWrap: {
    flex: 1,
    gap: spacing.sm,
  },
  downloadPct: {
    fontSize: fontSize.xl,
    fontWeight: fontWeight.bold,
    color: colors.accent,
    textAlign: 'center',
  },
  downloadBar: {
    height: 6,
    backgroundColor: colors.surfaceElevated,
    borderRadius: radius.full,
    overflow: 'hidden',
  },
  downloadFill: {
    height: '100%',
    backgroundColor: colors.accent,
    borderRadius: radius.full,
  },
  // Actions
  publishActions: {
    gap: spacing.sm,
  },
  // Info box
  infoBox: {
    backgroundColor: colors.surfaceElevated,
    borderRadius: radius.md,
    padding: spacing.md,
    marginTop: spacing.sm,
  },
  infoTitle: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.semibold,
    color: colors.textSecondary,
    marginBottom: spacing.xs,
  },
  infoText: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
    lineHeight: 18,
  },
});
