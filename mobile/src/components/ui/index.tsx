export { Card } from './Card';
export { Button } from './Button';
export { Badge } from './Badge';

import React from 'react';
import { View, Text, ActivityIndicator, StyleSheet, TextInput, TextInputProps } from 'react-native';
import { colors } from '../../theme/colors';
import { fontSize, fontWeight, radius, spacing } from '../../theme/spacing';

// ── Spinner ──────────────────────────────────────────────────────────────────

export function Spinner({ size = 'small' }: { size?: 'small' | 'large' }) {
  return <ActivityIndicator size={size} color={colors.accent} />;
}

// ── EmptyState ───────────────────────────────────────────────────────────────

interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
}

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <View style={emptyStyles.container}>
      {icon}
      <Text style={emptyStyles.title}>{title}</Text>
      {description && <Text style={emptyStyles.description}>{description}</Text>}
      {action}
    </View>
  );
}

const emptyStyles = StyleSheet.create({
  container: {
    alignItems: 'center',
    paddingVertical: spacing.xxl,
    paddingHorizontal: spacing.xl,
  },
  title: {
    fontSize: fontSize.md,
    fontWeight: fontWeight.semibold,
    color: colors.text,
    marginTop: spacing.md,
    textAlign: 'center',
  },
  description: {
    fontSize: fontSize.sm,
    color: colors.textMuted,
    marginTop: spacing.sm,
    textAlign: 'center',
  },
});

// ── Input ────────────────────────────────────────────────────────────────────

interface InputProps extends TextInputProps {
  label?: string;
  placeholder?: string;
}

export function Input({ label, placeholder, style, ...rest }: InputProps) {
  return (
    <View style={inputStyles.wrapper}>
      {label && <Text style={inputStyles.label}>{label}</Text>}
      <TextInput
        placeholder={placeholder}
        placeholderTextColor={colors.textMuted}
        style={[inputStyles.input, style]}
        {...rest}
      />
    </View>
  );
}

const inputStyles = StyleSheet.create({
  wrapper: {
    gap: spacing.xs,
  },
  label: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.medium,
    color: colors.textSecondary,
  },
  input: {
    height: 44,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.bg,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    fontSize: fontSize.base,
    color: colors.text,
  },
});

// ── Toggle ───────────────────────────────────────────────────────────────────

import { Switch } from 'react-native';

interface ToggleProps {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: string;
}

export function Toggle({ checked, onChange, label }: ToggleProps) {
  return (
    <View style={toggleStyles.container}>
      {label && <Text style={toggleStyles.label}>{label}</Text>}
      <Switch
        value={checked}
        onValueChange={onChange}
        trackColor={{ false: colors.surfaceHover, true: colors.accent }}
        thumbColor="#fff"
      />
    </View>
  );
}

const toggleStyles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: spacing.sm,
  },
  label: {
    fontSize: fontSize.sm,
    color: colors.text,
    flex: 1,
  },
});
