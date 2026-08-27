import React from 'react';
import {
  TouchableOpacity,
  Text,
  ActivityIndicator,
  StyleSheet,
  TextStyle,
  ViewStyle,
} from 'react-native';
import { colors } from '../../theme/colors';
import { fontSize, fontWeight, radius, spacing } from '../../theme/spacing';

type ButtonVariant = 'default' | 'primary' | 'ghost' | 'outline' | 'danger';
type ButtonSize = 'sm' | 'md' | 'lg';

interface ButtonProps {
  title: string;
  onPress?: () => void;
  variant?: ButtonVariant;
  size?: ButtonSize;
  disabled?: boolean;
  loading?: boolean;
  icon?: React.ReactNode;
  style?: ViewStyle;
  fullWidth?: boolean;
}

export function Button({
  title,
  onPress,
  variant = 'default',
  size = 'md',
  disabled,
  loading,
  icon,
  style,
  fullWidth,
}: ButtonProps) {
  const variantStyles = getVariantStyles(variant);
  const sizeStyles = getSizeStyles(size);

  return (
    <TouchableOpacity
      onPress={onPress}
      disabled={disabled || loading}
      style={[
        styles.base,
        variantStyles.container,
        sizeStyles.container,
        fullWidth && { width: '100%' },
        (disabled || loading) && styles.disabled,
        style,
      ]}
      activeOpacity={0.7}
    >
      {loading ? (
        <ActivityIndicator size="small" color={variantStyles.text.color} />
      ) : (
        <>
          {icon}
          <Text style={[styles.text, variantStyles.text, sizeStyles.text]}>{title}</Text>
        </>
      )}
    </TouchableOpacity>
  );
}

function getVariantStyles(variant: ButtonVariant) {
  switch (variant) {
    case 'primary':
      return {
        container: { backgroundColor: colors.accent },
        text: { color: '#fff' },
      };
    case 'danger':
      return {
        container: { backgroundColor: 'rgba(239,68,68,0.1)', borderWidth: 1, borderColor: 'rgba(239,68,68,0.3)' },
        text: { color: colors.error },
      };
    case 'outline':
      return {
        container: { borderWidth: 1, borderColor: colors.border },
        text: { color: colors.text },
      };
    case 'ghost':
      return {
        container: { backgroundColor: 'transparent' },
        text: { color: colors.textSecondary },
      };
    default:
      return {
        container: { backgroundColor: colors.surfaceElevated, borderWidth: 1, borderColor: colors.border },
        text: { color: colors.text },
      };
  }
}

function getSizeStyles(size: ButtonSize) {
  switch (size) {
    case 'sm':
      return {
        container: { height: 32, paddingHorizontal: 14 },
        text: { fontSize: fontSize.xs },
      };
    case 'lg':
      return {
        container: { height: 48, paddingHorizontal: 32 },
        text: { fontSize: fontSize.lg },
      };
    default:
      return {
        container: { height: 40, paddingHorizontal: 20 },
        text: { fontSize: fontSize.base },
      };
  }
}

const styles = StyleSheet.create({
  base: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
    borderRadius: radius.md,
  },
  text: {
    fontWeight: fontWeight.medium,
  },
  disabled: {
    opacity: 0.5,
  },
});
