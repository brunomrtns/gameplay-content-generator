import React from 'react';
import { View, Text, StyleSheet, ActivityIndicator } from 'react-native';
import { colors } from '../theme/colors';
import { fontSize, fontWeight, spacing } from '../theme/spacing';

export function LoadingScreen() {
  return (
    <View style={styles.container}>
      <View style={styles.logoWrap}>
        <Text style={styles.logo}>GPCG</Text>
        <Text style={styles.subtitle}>Gameplay Content Generator</Text>
      </View>
      <ActivityIndicator size="large" color={colors.accent} style={styles.spinner} />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
    justifyContent: 'center',
    alignItems: 'center',
  },
  logoWrap: {
    alignItems: 'center',
  },
  logo: {
    fontSize: 42,
    fontWeight: fontWeight.bold,
    color: colors.accent,
    letterSpacing: 3,
  },
  subtitle: {
    fontSize: fontSize.sm,
    color: colors.textMuted,
    marginTop: spacing.xs,
    letterSpacing: 0.5,
  },
  spinner: {
    marginTop: spacing.xl,
  },
});
