import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  RefreshControl,
  ScrollView,
  TouchableOpacity,
} from 'react-native';
import { useQuery } from '@tanstack/react-query';
import { useLiveData } from '../hooks/useLiveData';
import { SafeAreaView } from 'react-native-safe-area-context';
import Icon from 'react-native-vector-icons/MaterialCommunityIcons';
import { jobsApi } from '../api/endpoints';
import { Card, Badge, Spinner, EmptyState } from '../components/ui';
import { colors } from '../theme/colors';
import { fontSize, fontWeight, radius, spacing } from '../theme/spacing';
import { fmtDate } from '../utils/format';

const FILTERS = [
  { value: '', label: 'Todos' },
  { value: 'queued', label: 'Fila' },
  { value: 'running', label: 'Executando' },
  { value: 'completed', label: 'Concluídos' },
  { value: 'failed', label: 'Falhas' },
  { value: 'retrying', label: 'Retentando' },
  { value: 'cancelled', label: 'Cancelados' },
];

const JOB_TYPE_LABELS: Record<string, string> = {
  mapping: 'Mapeamento',
  generate_short: 'Vídeo Curto',
  curiosity_short: 'Curiosidade',
  kids_generate: 'Vídeo Kids',
};

const STAGE_LABELS: Record<string, string> = {
  download: 'Download',
  mapping: 'Mapeamento',
  content_planning: 'Planejamento',
  story_finding: 'História',
  editorial_planning: 'Editorial',
  creative_engine: 'Criativo',
  script: 'Roteiro',
  humanization: 'Humanização',
  script_review: 'Revisão',
  tts: 'TTS',
  gameplay_selection: 'Gameplay',
  visual_selection: 'Visual',
  music_selection: 'Música',
  render_plan: 'Plano Render',
  render: 'Render',
  qa: 'QA',
  metadata_generation: 'Metadata',
  youtube_upload: 'YouTube',
  output: 'Output',
  done: 'Concluído',
  presentation: 'Apresentação',
  upload: 'Upload',
  probe: 'Análise',
};

const STATUS_VARIANT: Record<string, any> = {
  queued: 'default',
  running: 'info',
  completed: 'success',
  failed: 'error',
  retrying: 'warning',
  cancelled: 'default',
};

export function JobsScreen({ navigation }: { navigation?: any }) {
  const [filter, setFilter] = useState('');

  const { data: jobs, refetch, isRefetching, isLoading } = useLiveData(
    ['jobs', filter],
    () => jobsApi.list(filter || undefined),
    ['job.status_changed', 'job.created']
  );

  const counts = {
    queued: (jobs || []).filter((j: any) => j.status === 'queued').length,
    running: (jobs || []).filter((j: any) => j.status === 'running').length,
    completed: (jobs || []).filter((j: any) => j.status === 'completed').length,
    failed: (jobs || []).filter((j: any) => j.status === 'failed').length,
    retrying: (jobs || []).filter((j: any) => j.status === 'retrying').length,
    cancelled: (jobs || []).filter((j: any) => j.status === 'cancelled').length,
  };

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
          <Text style={styles.headerTitle}>Jobs</Text>
          <Text style={styles.headerSubtitle}>Fila de processamento</Text>
        </View>
      </View>

      {/* Filter chips with counts */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.filterBar} contentContainerStyle={{ gap: spacing.sm, paddingHorizontal: spacing.lg }}>
        {FILTERS.map((f) => {
          const count = f.value === '' ? (jobs?.length || 0) : (counts as any)[f.value] || 0;
          return (
            <TouchableOpacity
              key={f.value}
              onPress={() => setFilter(f.value)}
              style={[styles.chip, filter === f.value && styles.chipActive]}
            >
              <Text style={[styles.chipText, filter === f.value && styles.chipTextActive]}>{f.label}</Text>
              <View style={[styles.chipCount, filter === f.value && styles.chipCountActive]}>
                <Text style={[styles.chipCountText, filter === f.value && styles.chipCountTextActive]}>{count}</Text>
              </View>
            </TouchableOpacity>
          );
        })}
      </ScrollView>

      <FlatList
        data={jobs || []}
        keyExtractor={(j) => String(j.id)}
        refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={() => { refetch(); }} tintColor={colors.accent} />}
        contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}
        ListEmptyComponent={
          isLoading ? (
            <View style={{ paddingVertical: spacing.xxl, alignItems: 'center' }}>
              <Spinner size="large" />
            </View>
          ) : (
            <Card>
              <EmptyState title="Nenhum job" description="Não há jobs nesta categoria." />
            </Card>
          )
        }
        renderItem={({ item: job }) => {
          const typeLabel = JOB_TYPE_LABELS[job.type] || job.type;
          const stageLabel = STAGE_LABELS[job.stage] || job.stage || '—';
          const progress = job.progress != null ? Math.round(Math.min(job.progress, 100)) : 0;
          const isRunning = job.status === 'running';
          return (
            <Card padding={spacing.md}>
              <View style={styles.jobHeader}>
                <View style={styles.jobIconWrap}>
                  <Icon
                    name={job.type === 'mapping' ? 'cpu' : 'video'}
                    size={20}
                    color={colors.accent}
                  />
                </View>
                <View style={{ flex: 1 }}>
                  <View style={styles.jobTitleRow}>
                    <Text style={styles.jobId}>#{job.id}</Text>
                    <Text style={styles.jobType}>{typeLabel}</Text>
                  </View>
                  {job.game_name && (
                    <Text style={styles.jobGame} numberOfLines={1}>{job.game_name}</Text>
                  )}
                </View>
                <Badge label={job.status} variant={STATUS_VARIANT[job.status] || 'default'} />
              </View>

              {isRunning && (
                <View style={styles.progressSection}>
                  <View style={styles.progressLabelRow}>
                    <Text style={styles.stageText}>{stageLabel}</Text>
                    <Text style={styles.progressPct}>{progress}%</Text>
                  </View>
                  <View style={styles.progressTrack}>
                    <View style={[styles.progressFill, { width: `${Math.min(progress, 100)}%` }]} />
                  </View>
                </View>
              )}

              {!isRunning && job.stage && (
                <View style={styles.stageRow}>
                  <Text style={styles.muted}>Etapa:</Text>
                  <Text style={styles.stageText}>{stageLabel}</Text>
                </View>
              )}

              {job.status === 'failed' && job.error && (
                <Text style={styles.errorText} numberOfLines={2}>{job.error}</Text>
              )}

              {job.worker_id && (
                <View style={styles.workerRow}>
                  <Icon name="cpu" size={12} color={colors.textMuted} />
                  <Text style={styles.metaText}>Worker: {job.worker_id}</Text>
                </View>
              )}

              <View style={styles.jobMeta}>
                <Text style={styles.metaText}>Criado: {fmtDate(job.created_at)}</Text>
                {job.completed_at && <Text style={styles.metaText}>Concluído: {fmtDate(job.completed_at)}</Text>}
                {job.attempts > 1 && <Text style={styles.metaText}>Tentativas: {job.attempts}</Text>}
              </View>
            </Card>
          );
        }}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    gap: spacing.md,
  },
  backBtn: {
    padding: spacing.xs,
  },
  headerTitle: {
    fontSize: fontSize.xxxl,
    fontWeight: fontWeight.bold,
    color: colors.text,
  },
  headerSubtitle: {
    fontSize: fontSize.sm,
    color: colors.textMuted,
    marginTop: 2,
  },
  filterBar: {
    paddingVertical: spacing.sm,
    maxHeight: 60,
  },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radius.full,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  chipActive: {
    backgroundColor: 'rgba(16,185,129,0.1)',
    borderColor: 'rgba(16,185,129,0.3)',
  },
  chipText: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
  },
  chipTextActive: {
    color: colors.accent,
    fontWeight: fontWeight.medium,
  },
  chipCount: {
    backgroundColor: colors.surfaceElevated,
    borderRadius: radius.sm,
    paddingHorizontal: 6,
    paddingVertical: 1,
  },
  chipCountActive: {
    backgroundColor: 'rgba(16,185,129,0.2)',
  },
  chipCountText: {
    fontSize: 10,
    color: colors.textMuted,
  },
  chipCountTextActive: {
    color: colors.accent,
  },
  jobHeader: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: spacing.sm,
    marginBottom: spacing.sm,
  },
  jobIconWrap: {
    width: 40,
    height: 40,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceElevated,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: 'center',
    justifyContent: 'center',
  },
  jobTitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
  },
  jobId: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
    fontFamily: 'monospace',
  },
  jobType: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.medium,
    color: colors.text,
  },
  jobGame: {
    fontSize: fontSize.xs,
    color: colors.textSecondary,
    marginTop: 2,
  },
  progressSection: {
    marginTop: spacing.sm,
    marginBottom: spacing.sm,
    gap: spacing.xs,
  },
  progressLabelRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  progressPct: {
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
    color: colors.accent,
  },
  stageRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    marginBottom: spacing.sm,
  },
  muted: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
  },
  stageText: {
    fontSize: fontSize.xs,
    color: colors.accent,
    fontWeight: fontWeight.medium,
  },
  errorText: {
    fontSize: fontSize.xs,
    color: colors.error,
    marginTop: spacing.xs,
    marginBottom: spacing.xs,
  },
  workerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    marginTop: spacing.xs,
  },
  progressTrack: {
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
  jobMeta: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.md,
    marginTop: spacing.xs,
  },
  metaText: {
    fontSize: 10,
    color: colors.textMuted,
  },
});
