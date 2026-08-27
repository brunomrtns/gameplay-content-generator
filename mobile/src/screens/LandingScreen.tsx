import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  Linking,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useNavigation } from '@react-navigation/native';
import Icon from 'react-native-vector-icons/MaterialCommunityIcons';
import { colors } from '../theme/colors';
import { fontSize, fontWeight, radius, spacing } from '../theme/spacing';
import { appApi, AppVersionInfo } from '../api/endpoints';

export function LandingScreen() {
  const navigation = useNavigation<any>();
  const [appInfo, setAppInfo] = useState<AppVersionInfo | null>(null);

  useEffect(() => {
    appApi.getVersion().then(setAppInfo).catch(() => {});
  }, []);

  const handleLogin = () => {
    navigation.navigate('Login');
  };

  const handleDownload = () => {
    Linking.openURL('https://brunointegrations.com/gpcg/api/app/download');
  };

  const apkSizeMB = appInfo?.size_bytes
    ? Math.round(appInfo.size_bytes / 1048576)
    : null;

  const features = [
    { icon: 'view-dashboard', title: 'Dashboard', desc: 'Status do worker em tempo real' },
    { icon: 'video', title: 'Vídeos', desc: 'Galeria, metadados e compartilhamento' },
    { icon: 'upload', title: 'Upload', desc: 'Gameplays direto do celular' },
    { icon: 'share-variant', title: 'Compartilhar', desc: 'Instagram, TikTok e mais' },
    { icon: 'robot', title: 'IA', desc: 'Roteiros e narração automáticos' },
    { icon: 'youtube', title: 'YouTube', desc: 'Publicação direta do app' },
  ];

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView style={styles.scroll} contentContainerStyle={styles.content}>
        {/* Hero */}
        <View style={styles.hero}>
          <View style={styles.logoWrap}>
            <Icon name="lightning-bolt" size={48} color={colors.accent} />
          </View>
          <Text style={styles.title}>GPCG</Text>
          <Text style={styles.subtitle}>Gameplay Content Generator</Text>
          <Text style={styles.tagline}>
            Transforme gameplay em vídeos prontos para o YouTube
          </Text>

          <TouchableOpacity style={styles.loginButton} onPress={handleLogin}>
            <Icon name="login" size={20} color="#fff" />
            <Text style={styles.loginButtonText}>Entrar</Text>
          </TouchableOpacity>
        </View>

        {/* Features */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>O que o app faz</Text>
          <View style={styles.featuresGrid}>
            {features.map((f, i) => (
              <View key={i} style={styles.featureCard}>
                <View style={styles.featureIconWrap}>
                  <Icon name={f.icon} size={22} color={colors.accent} />
                </View>
                <Text style={styles.featureTitle}>{f.title}</Text>
                <Text style={styles.featureDesc}>{f.desc}</Text>
              </View>
            ))}
          </View>
        </View>

        {/* Download section */}
        {appInfo?.available && (
          <View style={styles.downloadSection}>
            <Text style={styles.sectionTitle}>Baixar app</Text>
            <View style={styles.downloadCard}>
              <Icon name="cellphone-arrow-down" size={32} color={colors.accent} />
              <View style={styles.downloadInfo}>
                <Text style={styles.downloadVersion}>
                  Versão {appInfo.version}
                </Text>
                {apkSizeMB && (
                  <Text style={styles.downloadSize}>{apkSizeMB} MB · Android 8.0+</Text>
                )}
              </View>
              <TouchableOpacity style={styles.downloadButton} onPress={handleDownload}>
                <Icon name="download" size={20} color="#fff" />
                <Text style={styles.downloadButtonText}>Baixar</Text>
              </TouchableOpacity>
            </View>
          </View>
        )}

        {/* Footer */}
        <View style={styles.footer}>
          <Text style={styles.footerText}>
            GPCG · Bruno Integrations
          </Text>
        </View>
      </ScrollView>
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
  },
  content: {
    flexGrow: 1,
    paddingBottom: spacing.xl,
  },
  hero: {
    alignItems: 'center',
    paddingTop: spacing.xxl,
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.xl,
  },
  logoWrap: {
    width: 80,
    height: 80,
    borderRadius: radius.xl,
    backgroundColor: colors.surfaceElevated,
    borderWidth: 1,
    borderColor: colors.border,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: spacing.md,
  },
  title: {
    fontSize: 36,
    fontWeight: fontWeight.bold,
    color: colors.text,
    letterSpacing: 2,
  },
  subtitle: {
    fontSize: fontSize.sm,
    color: colors.textMuted,
    marginTop: 4,
  },
  tagline: {
    fontSize: fontSize.base,
    color: colors.textSecondary,
    textAlign: 'center',
    marginTop: spacing.md,
    lineHeight: 22,
  },
  loginButton: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    backgroundColor: colors.accent,
    paddingHorizontal: spacing.xl,
    paddingVertical: spacing.md,
    borderRadius: radius.lg,
    marginTop: spacing.xl,
  },
  loginButtonText: {
    fontSize: fontSize.base,
    fontWeight: fontWeight.semibold,
    color: '#fff',
  },
  section: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.xl,
  },
  sectionTitle: {
    fontSize: fontSize.lg,
    fontWeight: fontWeight.bold,
    color: colors.text,
    marginBottom: spacing.md,
  },
  featuresGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
  },
  featureCard: {
    width: '48%',
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    gap: spacing.xs,
  },
  featureIconWrap: {
    width: 36,
    height: 36,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceElevated,
    justifyContent: 'center',
    alignItems: 'center',
  },
  featureTitle: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.semibold,
    color: colors.text,
  },
  featureDesc: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
    lineHeight: 16,
  },
  downloadSection: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.xl,
  },
  downloadCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    gap: spacing.md,
  },
  downloadInfo: {
    flex: 1,
  },
  downloadVersion: {
    fontSize: fontSize.base,
    fontWeight: fontWeight.semibold,
    color: colors.text,
  },
  downloadSize: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
    marginTop: 2,
  },
  downloadButton: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: colors.accent,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radius.md,
  },
  downloadButtonText: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.semibold,
    color: '#fff',
  },
  footer: {
    alignItems: 'center',
    paddingTop: spacing.xxl,
  },
  footerText: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
  },
});
