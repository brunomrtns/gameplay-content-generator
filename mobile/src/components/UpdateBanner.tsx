import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import Icon from 'react-native-vector-icons/MaterialCommunityIcons';
import { colors } from '../theme/colors';
import { fontSize, fontWeight, radius, spacing } from '../theme/spacing';
import { UpdateInfo } from '../hooks/useAppUpdate';

interface UpdateBannerProps {
  updateInfo: UpdateInfo;
  onDownload: () => void;
  onDismiss: () => void;
}

export function UpdateBanner({ updateInfo, onDownload, onDismiss }: UpdateBannerProps) {
  if (!updateInfo.hasUpdate) return null;

  const sizeMB = updateInfo.sizeBytes
    ? Math.round(updateInfo.sizeBytes / 1048576)
    : null;

  return (
    <View style={styles.container}>
      <View style={styles.iconWrap}>
        <Icon name="cellphone-arrow-down" size={24} color={colors.accent} />
      </View>
      <View style={styles.content}>
        <Text style={styles.title}>
          Atualização disponível — v{updateInfo.version}
        </Text>
        <Text style={styles.subtitle}>
          {sizeMB ? `${sizeMB}MB · ` : ''}
          Toque para baixar e instalar
        </Text>
        {updateInfo.changelog && (
          <Text style={styles.changelog} numberOfLines={2}>
            {updateInfo.changelog}
          </Text>
        )}
      </View>
      <View style={styles.actions}>
        <TouchableOpacity onPress={onDownload} style={styles.downloadBtn}>
          <Icon name="download" size={20} color="#fff" />
          <Text style={styles.downloadText}>Baixar</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={onDismiss} style={styles.dismissBtn}>
          <Icon name="close" size={18} color={colors.textMuted} />
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    gap: spacing.sm,
  },
  iconWrap: {
    width: 40,
    height: 40,
    borderRadius: radius.full,
    backgroundColor: colors.surfaceElevated,
    justifyContent: 'center',
    alignItems: 'center',
  },
  content: {
    flex: 1,
  },
  title: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.semibold,
    color: colors.text,
  },
  subtitle: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
    marginTop: 2,
  },
  changelog: {
    fontSize: fontSize.xs,
    color: colors.textSecondary,
    marginTop: 2,
    fontStyle: 'italic',
  },
  actions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
  },
  downloadBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: colors.accent,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radius.md,
  },
  downloadText: {
    fontSize: fontSize.xs,
    fontWeight: fontWeight.semibold,
    color: '#fff',
  },
  dismissBtn: {
    width: 32,
    height: 32,
    justifyContent: 'center',
    alignItems: 'center',
  },
});
