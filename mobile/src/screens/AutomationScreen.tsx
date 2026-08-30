// i18n: aligned with web i18n migration cycle
import React, { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TextInput,
  TouchableOpacity,
  Alert,
  Modal,
  BackHandler,
} from 'react-native';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useLiveData } from '../hooks/useLiveData';
import { SafeAreaView } from 'react-native-safe-area-context';
import DocumentPicker from 'react-native-document-picker';
import { automationApi, gamesApi, voicesApi, presentationApi, dashboardApi } from '../api/endpoints';
import { Card, Button, Badge, Spinner, Toggle } from '../components/ui';
import { colors } from '../theme/colors';
import { fontSize, fontWeight, radius, spacing } from '../theme/spacing';
import Toast from 'react-native-toast-message';

// ── Constants (match web) ────────────────────────────────────────────────────

const CREATIVE_STYLES = [
  { value: '', label: 'Padrão (sem estilo)' },
  { value: 'humor', label: 'Humor' },
  { value: 'absurd', label: 'Absurdo' },
  { value: 'sarcastic', label: 'Sarcástico' },
  { value: 'storytelling', label: 'Narrativa' },
  { value: 'curiosity', label: 'Curiosidade' },
  { value: 'nostalgia', label: 'Nostalgia' },
  { value: 'dark_humor', label: 'Humor negro' },
  { value: 'high_energy', label: 'Alta energia' },
];

const YOUTUBE_PRIVACY = [
  { value: 'public', label: 'Público' },
  { value: 'unlisted', label: 'Não listado' },
  { value: 'private', label: 'Privado' },
];

const YOUTUBE_CATEGORIES = [
  { value: '20', label: 'Games' },
  { value: '22', label: 'Pessoas e blogs' },
  { value: '24', label: 'Entretenimento' },
  { value: '23', label: 'Comédia' },
  { value: '27', label: 'Educação' },
];

const VIDEO_FORMATS = [
  { value: '9:16', label: '9:16 Vertical (Shorts/TikTok)' },
  { value: '16:9', label: '16:9 Horizontal (YouTube)' },
  { value: '1:1', label: '1:1 Quadrado (Instagram)' },
  { value: '4:5', label: '4:5 Retrato (Reels)' },
];

const SUBTITLE_FONTS = [
  { value: '', label: 'Padrão do perfil' },
  { value: 'DejaVuSans-Bold', label: 'DejaVu Sans Bold' },
  { value: 'DejaVuSans', label: 'DejaVu Sans' },
  { value: 'LiberationSans-Bold', label: 'Liberation Sans Bold' },
];

const SUBTITLE_COLORS = [
  { value: '', label: 'Padrão' },
  { value: 'white', label: 'Branco' },
  { value: 'yellow', label: 'Amarelo' },
  { value: 'cyan', label: 'Ciano' },
  { value: 'red', label: 'Vermelho' },
  { value: 'lime', label: 'Verde limão' },
];

const SUBTITLE_POSITIONS = [
  { value: '', label: 'Padrão' },
  { value: 'bottom', label: 'Baixo' },
  { value: 'middle', label: 'Meio' },
  { value: 'top', label: 'Topo' },
];

const SUBTITLE_CASES = [
  { value: '', label: 'Padrão' },
  { value: 'upper', label: 'MAIÚSCULAS' },
  { value: 'lower', label: 'minúsculas' },
  { value: 'none', label: 'Como escrito' },
];

const TRANSITION_TYPES = [
  { value: '', label: 'Padrão (smoothleft)' },
  { value: 'fade', label: 'Fade' },
  { value: 'fadeblack', label: 'Fade Preto' },
  { value: 'fadewhite', label: 'Fade Branco' },
  { value: 'wipeleft', label: 'Wipe Esquerda' },
  { value: 'wiperight', label: 'Wipe Direita' },
  { value: 'slideleft', label: 'Slide Esquerda' },
  { value: 'slideright', label: 'Slide Direita' },
  { value: 'slideup', label: 'Slide Cima' },
  { value: 'slidedown', label: 'Slide Baixo' },
  { value: 'smoothleft', label: 'Smooth Esquerda' },
  { value: 'smoothright', label: 'Smooth Direita' },
  { value: 'smoothup', label: 'Smooth Cima' },
  { value: 'smoothdown', label: 'Smooth Baixo' },
  { value: 'circleopen', label: 'Círculo Abre' },
  { value: 'circleclose', label: 'Círculo Fecha' },
  { value: 'dissolve', label: 'Dissolve' },
  { value: 'zoomin', label: 'Zoom In' },
  { value: 'hblur', label: 'Blur Horizontal' },
  { value: 'diagtl', label: 'Diagonal TL' },
  { value: 'diagtr', label: 'Diagonal TR' },
  { value: 'diagbl', label: 'Diagonal BL' },
  { value: 'diagbr', label: 'Diagonal BR' },
];

const BOX_COLORS = [
  { value: '', label: 'Padrão' },
  { value: 'black@0.7', label: 'Preto 70%' },
  { value: 'black@0.5', label: 'Preto 50%' },
  { value: 'black@0.3', label: 'Preto 30%' },
  { value: 'white@0.7', label: 'Branco 70%' },
  { value: 'white@0.5', label: 'Branco 50%' },
];

export function AutomationScreen() {
  const queryClient = useQueryClient();
  const [config, setConfig] = useState<any>({});
  const [saving, setSaving] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);

  const { data: automation } = useLiveData(
    ['automation'],
    automationApi.get,
    ['automation.status_changed', 'job.status_changed']
  );

  const { data: games } = useLiveData(
    ['games-list'],
    gamesApi.list,
    ['game.enriched']
  );

  const { data: voices } = useLiveData(
    ['voices'],
    voicesApi.list,
    []
  );

  const { data: dash } = useLiveData(
    ['dashboard'],
    dashboardApi.get,
    ['job.status_changed', 'video.created']
  );

  useEffect(() => {
    if (automation && !loaded) {
      setConfig(automation.config || {});
      setLoaded(true);
    }
  }, [automation, loaded]);

  const update = (key: string, value: any) => {
    setConfig((prev: any) => {
      const next = { ...prev, [key]: value };
      // Clean up empty/zero values but preserve booleans
      Object.keys(next).forEach((k) => {
        const v = next[k];
        if (typeof v === 'boolean') return;
        if (v === '' || v === 0 || v === undefined || v === null) delete next[k];
      });
      return next;
    });
  };

  const updatePresentation = (key: string, value: any) => {
    setConfig((prev: any) => ({
      ...prev,
      presentation: { ...(prev.presentation || {}), [key]: value },
    }));
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await automationApi.update(config);
      Toast.show({ type: 'success', text1: 'Configuração salva' });
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    } finally {
      setSaving(false);
    }
  };

  const handleToggle = async () => {
    setToggling(true);
    try {
      if (automation?.status === 'running') {
        await automationApi.pause();
        Toast.show({ type: 'success', text1: 'Automação pausada' });
      } else {
        await automationApi.start();
        Toast.show({ type: 'success', text1: 'Automação iniciada' });
      }
      queryClient.invalidateQueries({ queryKey: ['automation'] });
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro' });
    } finally {
      setToggling(false);
    }
  };

  const handleUploadVoice = async () => {
    try {
      const result = await DocumentPicker.pick({ type: ['audio/wav', 'audio/mpeg', 'audio/ogg', 'audio/flac', 'audio/mp4'] });
      const file = result[0];
      await voicesApi.upload({ uri: file.uri, name: file.name ?? 'file', type: file.type ?? 'audio/wav' });
      Toast.show({ type: 'success', text1: `Voz "${file.name ?? 'file'}" enviada` });
      queryClient.invalidateQueries({ queryKey: ['voices'] });
    } catch (err: any) {
      if (!DocumentPicker.isCancel(err)) Toast.show({ type: 'error', text1: err.message || 'Erro' });
    }
  };

  const handleDeleteVoice = (filename: string) => {
    Alert.alert('Excluir voz?', `Excluir "${filename}"?`, [
      { text: 'Cancelar', style: 'cancel' },
      {
        text: 'Excluir',
        style: 'destructive',
        onPress: async () => {
          try {
            await voicesApi.delete(filename);
            Toast.show({ type: 'success', text1: 'Voz excluída' });
            queryClient.invalidateQueries({ queryKey: ['voices'] });
            if (config.voice === filename) update('voice', '');
          } catch (err: any) {
            Toast.show({ type: 'error', text1: err.message || 'Erro' });
          }
        },
      },
    ]);
  };

  const handleUploadPresentationImage = async () => {
    try {
      const result = await DocumentPicker.pick({ type: ['image/*'] });
      const file = result[0];
      const r = await presentationApi.uploadImage({ uri: file.uri, name: file.name ?? 'file', type: file.type ?? 'image/jpeg' });
      updatePresentation('thumbnail_image_path', r.key);
      Toast.show({ type: 'success', text1: 'Imagem enviada' });
    } catch (err: any) {
      if (!DocumentPicker.isCancel(err)) Toast.show({ type: 'error', text1: err.message || 'Erro' });
    }
  };

  if (!loaded) {
    return (
      <View style={styles.center}>
        <Spinner size="large" />
      </View>
    );
  }

  const isKidsDomain = dash?.channel_domain === 'kids';

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <Text style={styles.title}>Automação</Text>
        <View style={styles.headerActions}>
          <Button title="Salvar" variant="outline" size="sm" loading={saving} onPress={handleSave} />
          <Button
            title={automation?.status === 'running' ? 'Pausar' : 'Iniciar'}
            variant={automation?.status === 'running' ? 'danger' : 'primary'}
            size="sm"
            loading={toggling}
            onPress={handleToggle}
          />
        </View>
      </View>

      <ScrollView style={styles.scroll} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md, paddingBottom: 100 }}>
        {/* Preview resumo (expansível) */}
        <TouchableOpacity style={styles.previewCard} onPress={() => setPreviewOpen(!previewOpen)} activeOpacity={0.7}>
          <View style={styles.previewHeader}>
            <View style={{ flex: 1 }}>
              <Text style={styles.previewTitle}>Resumo da configuração</Text>
              <Text style={styles.previewSubtitle}>
                {config.video_format || '9:16'} · {config.creative_style ? CREATIVE_STYLES.find(s => s.value === config.creative_style)?.label : 'Padrão'} · {config.voice || 'Voz padrão'}
              </Text>
            </View>
            <Text style={styles.previewArrow}>{previewOpen ? '▲' : '▼'}</Text>
          </View>
          {previewOpen && (
            <View style={styles.previewBody}>
              <PreviewRow label="Formato" value={VIDEO_FORMATS.find(f => f.value === (config.video_format || ''))?.label || '9:16 Vertical'} />
              <PreviewRow label="Estilo criativo" value={CREATIVE_STYLES.find(s => s.value === (config.creative_style || ''))?.label || 'Padrão'} />
              <PreviewRow label="Voz" value={config.voice || 'Padrão do sistema'} />
              <PreviewRow label="Duração da cena" value={config.scene_duration ? `${config.scene_duration}s` : 'Automático'} />
              <PreviewRow label="Transição" value={TRANSITION_TYPES.find(t => t.value === (config.transition_type || ''))?.label || 'Padrão'} />
              <PreviewRow label="Legenda - Fonte" value={SUBTITLE_FONTS.find(f => f.value === (config.subtitle_font || ''))?.label || 'Padrão'} />
              <PreviewRow label="Legenda - Cor" value={SUBTITLE_COLORS.find(c => c.value === (config.subtitle_color || ''))?.label || 'Padrão'} />
              <PreviewRow label="Legenda - Posição" value={SUBTITLE_POSITIONS.find(p => p.value === (config.subtitle_position || ''))?.label || 'Padrão'} />
              <PreviewRow label="YouTube - Privacidade" value={YOUTUBE_PRIVACY.find(p => p.value === (config.youtube_privacy || 'unlisted'))?.label || 'Não listado'} />
              <PreviewRow label="Auto-publicar" value={config.auto_publish === false ? 'Não' : 'Sim'} />
              <PreviewRow label="Apresentação" value={config.presentation?.enabled ? 'Ativa' : 'Inativa'} />
              {config.presentation?.enabled && (
                <>
                  <PreviewRow label="  Thumbnail" value={config.presentation.thumbnail_mode === 'fixed' ? 'Imagem fixa' : 'Automático'} />
                  <PreviewRow label="  Abertura" value={config.presentation.opening_duration ? `${config.presentation.opening_duration}s` : 'Sem abertura'} />
                </>
              )}
              <PreviewRow label="Fila" value={config.queue_mode === 'manual' ? 'Manual' : 'Automática'} />
            </View>
          )}
        </TouchableOpacity>

        {/* Conteúdo (games only) */}
        {!isKidsDomain && (
          <Card>
            <SectionTitle title="Conteúdo" desc="Qual gameplay usar como fonte" />
            <View style={{ gap: spacing.md }}>
              <PickerField
                label="Jogo"
                value={config.game_id ? String(config.game_id) : ''}
                options={[{ value: '', label: 'Qualquer jogo (aleatório)' }, ...(games || []).map((g: any) => ({ value: String(g.id), label: g.canonical_name }))]}
                onChange={(v) => update('game_id', v ? Number(v) : null)}
              />
              <PickerField
                label="Reutilização de cenas"
                value={String(config.max_clip_uses ?? 1)}
                options={[
                  { value: '1', label: '1 vez (cada trecho em 1 vídeo)' },
                  { value: '2', label: '2 vezes' },
                  { value: '3', label: '3 vezes' },
                  { value: '0', label: 'Ilimitado' },
                ]}
                onChange={(v) => update('max_clip_uses', Number(v))}
              />
              <PickerField
                label="Gameplay pública"
                value={config.fallback_policy || (config.accept_public_gameplays ? 'allow_public' : 'stop')}
                options={[
                  { value: 'stop', label: 'Apenas minhas gameplays' },
                  { value: 'allow_public', label: 'Permitir públicas como fallback' },
                ]}
                onChange={(v) => update('fallback_policy', v)}
              />
            </View>
          </Card>
        )}

        {/* Formato */}
        <Card>
          <SectionTitle title="Formato" desc="Dimensões e duração" />
          <View style={{ gap: spacing.md }}>
            <PickerField label="Formato" value={config.video_format || ''} options={VIDEO_FORMATS} onChange={(v) => update('video_format', v)} />
            <NumberField label="Duração de cada cena (s)" value={config.scene_duration} onChange={(v) => update('scene_duration', v)} placeholder="0 = automático" />
          </View>
        </Card>

        {/* Legenda */}
        <Card>
          <SectionTitle title="Legenda" desc="Estilo das legendas" />
          <View style={{ gap: spacing.md }}>
            <PickerField label="Fonte" value={config.subtitle_font || ''} options={SUBTITLE_FONTS} onChange={(v) => update('subtitle_font', v)} />
            <NumberField label="Tamanho" value={config.subtitle_font_size} onChange={(v) => update('subtitle_font_size', v)} placeholder="0 = auto" />
            <PickerField label="Cor do texto" value={config.subtitle_color || ''} options={SUBTITLE_COLORS} onChange={(v) => update('subtitle_color', v)} />
            <PickerField label="Cor do contorno" value={config.subtitle_outline_color || ''} options={[{ value: '', label: 'Padrão (preto)' }, { value: 'black', label: 'Preto' }, { value: 'white', label: 'Branco' }, { value: 'red', label: 'Vermelho' }]} onChange={(v) => update('subtitle_outline_color', v)} />
            <PickerField label="Posição" value={config.subtitle_position || ''} options={SUBTITLE_POSITIONS} onChange={(v) => update('subtitle_position', v)} />
            <PickerField label="Caixa (case)" value={config.subtitle_case || ''} options={SUBTITLE_CASES} onChange={(v) => update('subtitle_case', v)} />
            <Toggle checked={config.subtitle_box_enabled ?? false} onChange={(v) => update('subtitle_box_enabled', v)} label="Ativar fundo (box)" />
            {config.subtitle_box_enabled && (
              <>
                <PickerField label="Cor do fundo" value={config.subtitle_box_color || ''} options={BOX_COLORS} onChange={(v) => update('subtitle_box_color', v)} />
                <NumberField label="Padding" value={config.subtitle_box_padding} onChange={(v) => update('subtitle_box_padding', v)} placeholder="0 = padrão" />
                <Toggle checked={config.subtitle_rounded_box ?? false} onChange={(v) => update('subtitle_rounded_box', v)} label="Fundo arredondado" />
              </>
            )}
            <PickerField label="Cor do traço" value={config.subtitle_stroke_color || ''} options={[{ value: '', label: 'Padrão' }, { value: 'black', label: 'Preto' }, { value: 'white', label: 'Branco' }, { value: 'red', label: 'Vermelho' }, { value: 'blue', label: 'Azul' }]} onChange={(v) => update('subtitle_stroke_color', v)} />
            <NumberField label="Largura do traço" value={config.subtitle_stroke_width} onChange={(v) => update('subtitle_stroke_width', v)} placeholder="0 = padrão" />
          </View>
        </Card>

        {/* Transição */}
        <Card>
          <SectionTitle title="Transição" desc="Entre cenas" />
          <View style={{ gap: spacing.md }}>
            <PickerField label="Tipo" value={config.transition_type || ''} options={TRANSITION_TYPES} onChange={(v) => update('transition_type', v)} />
            <NumberField label="Duração (s)" value={config.transition_duration} onChange={(v) => update('transition_duration', v)} placeholder="0 = 0.5s" />
          </View>
        </Card>

        {/* Voz */}
        <Card>
          <SectionTitle title="Voz" desc="Voz da narração (TTS por clonagem)" />
          <View style={{ gap: spacing.md }}>
            <View style={styles.rowBetween}>
              <Text style={styles.label}>Voz selecionada</Text>
              <Button title="Upload voz" variant="outline" size="sm" onPress={handleUploadVoice} />
            </View>
            <PickerField
              label=""
              value={config.voice || ''}
              options={[{ value: '', label: 'Padrão do sistema' }, ...(voices || []).map((v: any) => ({ value: v.filename, label: `${v.filename} (${v.file_size_kb} KB)` }))]}
              onChange={(v) => update('voice', v)}
            />
            {config.voice && (
              <TouchableOpacity onPress={() => handleDeleteVoice(config.voice)}>
                <Text style={styles.deleteText}>Excluir "{config.voice}"</Text>
              </TouchableOpacity>
            )}
          </View>
        </Card>

        {/* Estilo */}
        <Card>
          <SectionTitle title="Estilo" desc="Estilo criativo do roteiro" />
          <PickerField label="" value={config.creative_style || ''} options={CREATIVE_STYLES} onChange={(v) => update('creative_style', v)} />
        </Card>

        {/* Presentation Layer */}
        <Card>
          <SectionTitle title="Apresentação" desc="Thumbnail e abertura visual" />
          <Toggle checked={config.presentation?.enabled ?? false} onChange={(v) => updatePresentation('enabled', v)} label="Ativar Presentation Layer" />
          {config.presentation?.enabled && (
            <View style={{ gap: spacing.md, marginTop: spacing.md }}>
              <PickerField
                label="Modo thumbnail"
                value={config.presentation.thumbnail_mode || 'auto'}
                options={[
                  { value: 'auto', label: 'Automático (frame do gameplay)' },
                  { value: 'fixed', label: 'Imagem fixa' },
                ]}
                onChange={(v) => updatePresentation('thumbnail_mode', v)}
              />
              {config.presentation.thumbnail_mode === 'fixed' && (
                <Button title="Enviar imagem de capa" variant="outline" size="sm" onPress={handleUploadPresentationImage} />
              )}
              <Toggle checked={config.presentation.thumbnail_text_enabled ?? false} onChange={(v) => updatePresentation('thumbnail_text_enabled', v)} label="Texto na capa" />
              {config.presentation.thumbnail_text_enabled && (
                <>
                  <PickerField label="Fonte do texto" value={config.presentation.thumbnail_text_source || 'title'} options={[{ value: 'title', label: 'Título' }, { value: 'topic', label: 'Tópico' }, { value: 'custom', label: 'Customizado' }]} onChange={(v) => updatePresentation('thumbnail_text_source', v)} />
                  {config.presentation.thumbnail_text_source === 'custom' && (
                    <TextInput style={styles.input} value={config.presentation.thumbnail_text_custom || ''} onChangeText={(v) => updatePresentation('thumbnail_text_custom', v)} placeholder="Texto customizado" placeholderTextColor={colors.textMuted} />
                  )}
                  <PickerField label="Posição" value={config.presentation.thumbnail_text_position || 'bottom'} options={[{ value: 'bottom', label: 'Baixo' }, { value: 'top', label: 'Topo' }, { value: 'center', label: 'Centro' }]} onChange={(v) => updatePresentation('thumbnail_text_position', v)} />
                  <PickerField label="Cor" value={config.presentation.thumbnail_text_color || 'white'} options={SUBTITLE_COLORS} onChange={(v) => updatePresentation('thumbnail_text_color', v)} />
                  <NumberField label="Tamanho" value={config.presentation.thumbnail_text_size} onChange={(v) => updatePresentation('thumbnail_text_size', v)} placeholder="0 = auto" />
                </>
              )}
              <PickerField
                label="Modo abertura"
                value={config.presentation.opening_mode || 'same_as_thumbnail'}
                options={[
                  { value: 'same_as_thumbnail', label: 'Mesma da thumbnail' },
                  { value: 'auto', label: 'Automático' },
                  { value: 'fixed', label: 'Imagem fixa' },
                ]}
                onChange={(v) => updatePresentation('opening_mode', v)}
              />
              <NumberField label="Duração abertura (s)" value={config.presentation.opening_duration} onChange={(v) => updatePresentation('opening_duration', v)} placeholder="0 = sem abertura" />
            </View>
          )}
        </Card>

        {/* Fila de Produção (games) */}
        {!isKidsDomain && (
          <Card>
            <SectionTitle title="Fila de Produção" desc="Como escolher o próximo vídeo" />
            <View style={{ gap: spacing.md }}>
              <PickerField
                label="Modo da fila"
                value={config.queue_mode || 'automatic'}
                options={[
                  { value: 'automatic', label: 'Automático' },
                  { value: 'manual', label: 'Manual' },
                ]}
                onChange={(v) => update('queue_mode', v)}
              />
              {config.queue_mode !== 'manual' && (
                <>
                  <Toggle checked={config.auto_fill_queue ?? false} onChange={(v) => update('auto_fill_queue', v)} label="Auto-preencher fila" />
                  {config.auto_fill_queue && (
                    <NumberField label="Tamanho máximo" value={config.max_queue_size} onChange={(v) => update('max_queue_size', v)} placeholder="10" />
                  )}
                </>
              )}
            </View>
          </Card>
        )}

        {/* Fila Kids */}
        {isKidsDomain && (
          <Card>
            <SectionTitle title="Fila de Produção Kids" />
            <View style={{ gap: spacing.md }}>
              <PickerField label="Modo" value={config.kids_queue_mode || 'manual'} options={[{ value: 'manual', label: 'Manual' }, { value: 'auto', label: 'Automático' }]} onChange={(v) => update('kids_queue_mode', v)} />
              <Toggle checked={config.kids_auto_fill_queue ?? true} onChange={(v) => update('kids_auto_fill_queue', v)} label="Preencher automaticamente" />
              <NumberField label="Tamanho máximo" value={config.kids_max_queue_size} onChange={(v) => update('kids_max_queue_size', v)} placeholder="10" />
            </View>
          </Card>
        )}

        {/* YouTube */}
        <Card>
          <SectionTitle title="YouTube" desc="Configurações de publicação" />
          <View style={{ gap: spacing.md }}>
            <PickerField label="Privacidade" value={config.youtube_privacy || 'unlisted'} options={YOUTUBE_PRIVACY} onChange={(v) => update('youtube_privacy', v)} />
            <PickerField label="Categoria" value={config.youtube_category_id || '20'} options={YOUTUBE_CATEGORIES} onChange={(v) => update('youtube_category_id', v)} />
            <Toggle checked={config.auto_publish ?? true} onChange={(v) => update('auto_publish', v)} label="Publicar automaticamente" />
          </View>
        </Card>
      </ScrollView>

      {/* Fixed bottom actions */}
      <View style={styles.bottomBar}>
        <Button title="Salvar" variant="outline" loading={saving} onPress={handleSave} style={{ flex: 1 }} />
        <Button
          title={automation?.status === 'running' ? 'Pausar' : 'Iniciar'}
          variant={automation?.status === 'running' ? 'danger' : 'primary'}
          loading={toggling}
          onPress={handleToggle}
          style={{ flex: 1 }}
        />
      </View>
    </SafeAreaView>
  );
}

// ── Helper components ────────────────────────────────────────────────────────

function PreviewRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.previewRow}>
      <Text style={styles.previewLabel}>{label}</Text>
      <Text style={styles.previewValue}>{value}</Text>
    </View>
  );
}

function SectionTitle({ title, desc }: { title: string; desc?: string }) {
  return (
    <View style={{ marginBottom: spacing.md }}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {desc && <Text style={styles.hint}>{desc}</Text>}
    </View>
  );
}

function PickerField({ label, value, options, onChange }: { label: string; value: string; options: { value: string; label: string }[]; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  const selected = options.find((o) => o.value === value);
  return (
    <View>
      {label !== '' && <Text style={styles.label}>{label}</Text>}
      <TouchableOpacity style={styles.picker} onPress={() => setOpen(true)}>
        <Text style={styles.pickerText}>{selected?.label || options[0]?.label || 'Selecionar'}</Text>
        <Text style={styles.pickerArrow}>▼</Text>
      </TouchableOpacity>
      <Modal
        visible={open}
        transparent
        animationType="fade"
        onRequestClose={() => setOpen(false)}
      >
        <View style={styles.pickerModalOverlay}>
          <View style={styles.pickerModalContent}>
            <View style={styles.pickerModalHeader}>
              <Text style={styles.pickerModalTitle}>{label || 'Selecionar'}</Text>
              <TouchableOpacity onPress={() => setOpen(false)}>
                <Text style={styles.closeButton}>Fechar</Text>
              </TouchableOpacity>
            </View>
            <ScrollView style={{ maxHeight: 400 }}>
              {options.map((o) => (
                <TouchableOpacity
                  key={o.value}
                  style={[styles.pickerOption, o.value === value && styles.pickerOptionActive]}
                  onPress={() => { onChange(o.value); setOpen(false); }}
                >
                  <Text style={[styles.pickerOptionText, o.value === value && styles.pickerOptionTextActive]}>{o.label}</Text>
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>
        </View>
      </Modal>
    </View>
  );
}

function NumberField({ label, value, onChange, placeholder }: { label: string; value: any; onChange: (v: number) => void; placeholder?: string }) {
  return (
    <View>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        style={styles.input}
        value={value ? String(value) : ''}
        onChangeText={(v) => onChange(Number(v) || 0)}
        placeholder={placeholder}
        placeholderTextColor={colors.textMuted}
        keyboardType="numeric"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.bg },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: spacing.lg, paddingVertical: spacing.md },
  title: { fontSize: fontSize.xxxl, fontWeight: fontWeight.bold, color: colors.text },
  headerActions: { flexDirection: 'row', gap: spacing.sm },
  scroll: { flex: 1 },
  previewCard: { backgroundColor: colors.surface, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.md, overflow: 'hidden' },
  previewHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  previewTitle: { fontSize: fontSize.md, fontWeight: fontWeight.semibold, color: colors.text },
  previewSubtitle: { fontSize: fontSize.xs, color: colors.textMuted, marginTop: 2 },
  previewArrow: { fontSize: 12, color: colors.textMuted, paddingLeft: spacing.sm },
  previewBody: { marginTop: spacing.md, paddingTop: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, gap: spacing.xs },
  previewRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 4 },
  previewLabel: { fontSize: fontSize.sm, color: colors.textMuted },
  previewValue: { fontSize: fontSize.sm, color: colors.text, fontWeight: fontWeight.medium, textAlign: 'right', flex: 1, marginLeft: spacing.md },
  sectionTitle: { fontSize: fontSize.md, fontWeight: fontWeight.semibold, color: colors.text },
  hint: { fontSize: fontSize.xs, color: colors.textMuted, marginTop: 2 },
  label: { fontSize: fontSize.sm, fontWeight: fontWeight.medium, color: colors.textSecondary, marginBottom: spacing.xs },
  input: { height: 44, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.bg, borderRadius: radius.md, paddingHorizontal: spacing.md, fontSize: fontSize.base, color: colors.text },
  rowBetween: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  deleteText: { fontSize: fontSize.xs, color: colors.error },
  picker: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', height: 44, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.bg, borderRadius: radius.md, paddingHorizontal: spacing.md },
  pickerText: { fontSize: fontSize.base, color: colors.text },
  pickerArrow: { fontSize: 10, color: colors.textMuted },
  pickerModalOverlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.8)', justifyContent: 'center', padding: spacing.lg },
  pickerModalContent: { backgroundColor: colors.surface, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, maxHeight: 500 },
  pickerModalHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', padding: spacing.md, borderBottomWidth: 1, borderBottomColor: colors.border },
  pickerModalTitle: { fontSize: fontSize.md, fontWeight: fontWeight.semibold, color: colors.text },
  closeButton: { fontSize: fontSize.base, color: colors.accent },
  pickerOption: { paddingVertical: spacing.md, paddingHorizontal: spacing.lg, borderBottomWidth: 1, borderBottomColor: colors.border },
  pickerOptionActive: { backgroundColor: 'rgba(45,212,191,0.1)' },
  pickerOptionText: { fontSize: fontSize.base, color: colors.textSecondary },
  pickerOptionTextActive: { color: colors.accent, fontWeight: fontWeight.medium },
  bottomBar: { position: 'absolute', bottom: 0, left: 0, right: 0, flexDirection: 'row', gap: spacing.sm, padding: spacing.lg, backgroundColor: colors.bg, borderTopWidth: 1, borderTopColor: colors.border },
});
