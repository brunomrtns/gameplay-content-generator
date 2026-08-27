import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { colors } from '../../theme/colors';
import { fontSize, fontWeight, radius, spacing } from '../../theme/spacing';

type BadgeVariant = 'default' | 'success' | 'warning' | 'error' | 'info';

interface BadgeProps {
  label: string;
  variant?: BadgeVariant;
  icon?: React.ReactNode;
}

export function Badge({ label, variant = 'default', icon }: BadgeProps) {
  const variantStyle = getVariantStyle(variant);
  return (
    <View style={[styles.badge, variantStyle.container]}>
      {icon}
      <Text style={[styles.text, variantStyle.text]}>{label}</Text>
    </View>
  );
}

function getVariantStyle(variant: BadgeVariant) {
  switch (variant) {
    case 'success':
      return {
        container: { backgroundColor: 'rgba(34,197,94,0.15)', borderColor: 'rgba(34,197,94,0.3)' },
        text: { color: colors.success },
      };
    case 'warning':
      return {
        container: { backgroundColor: 'rgba(234,179,8,0.15)', borderColor: 'rgba(234,179,8,0.3)' },
        text: { color: colors.warning },
      };
    case 'error':
      return {
        container: { backgroundColor: 'rgba(239,68,68,0.15)', borderColor: 'rgba(239,68,68,0.3)' },
        text: { color: colors.error },
      };
    case 'info':
      return {
        container: { backgroundColor: 'rgba(59,130,246,0.15)', borderColor: 'rgba(59,130,246,0.3)' },
        text: { color: colors.info },
      };
    default:
      return {
        container: { backgroundColor: colors.surfaceElevated, borderColor: colors.border },
        text: { color: colors.textSecondary },
      };
  }
}

const styles = StyleSheet.create({
  badge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    borderRadius: radius.sm,
    borderWidth: 1,
    alignSelf: 'flex-start',
  },
  text: {
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
  },
});
