import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  Alert,
  Modal,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import Icon from 'react-native-vector-icons/MaterialCommunityIcons';
import { colors } from '../theme/colors';
import { fontSize, fontWeight, radius, spacing } from '../theme/spacing';
import { domainsApi } from '../api/endpoints';
import { useAuth } from '../hooks/useAuth';
import { useBackHandler } from '../hooks/useBackHandler';
import Toast from 'react-native-toast-message';
import { OnboardingModal } from '../components/OnboardingModal';
import { authApi } from '../api/endpoints';

const DOMAIN_LABELS: Record<string, string> = {
  games: 'Games',
  kids: 'Kids',
  movies: 'Filmes & Séries',
  conspiracy: 'Mistérios & Teorias',
  technology: 'Tecnologia',
};

interface MoreScreenProps {
  navigation: any;
  user: any;
  onLogout: () => Promise<void>;
}

export function MoreScreen({ navigation, user, onLogout }: MoreScreenProps) {
  const queryClient = useQueryClient();
  const { refresh } = useAuth();
  const [domainModal, setDomainModal] = useState(false);
  const [confirmDomain, setConfirmDomain] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const [tutorialOpen, setTutorialOpen] = useState(false);

  const { data: domainData } = useQuery({
    queryKey: ['domains'],
    queryFn: () => domainsApi.list(),
  });

  const currentDomain = user?.channel_domain || domainData?.current || 'games';
  const isKidsDomain = currentDomain === 'kids';

  useBackHandler(() => {
    if (confirmDomain) { setConfirmDomain(null); return; }
    if (domainModal) { setDomainModal(false); return; }
  }, !!domainModal || !!confirmDomain);

  const handleLogout = () => {
    Alert.alert(
      'Sair da conta?',
      'Você precisará fazer login novamente.',
      [
        { text: 'Cancelar', style: 'cancel' },
        {
          text: 'Sair',
          style: 'destructive',
          onPress: () => onLogout(),
        },
      ],
    );
  };

  const handleDomainSelect = (domain: string) => {
    if (domain === currentDomain) return;
    setConfirmDomain(domain);
  };

  const handleConfirmReset = async () => {
    if (!confirmDomain) return;
    setResetting(true);
    try {
      await domainsApi.reset(confirmDomain, true);
      Toast.show({
        type: 'success',
        text1: `Domínio alterado para ${DOMAIN_LABELS[confirmDomain] || confirmDomain}`,
      });
      setConfirmDomain(null);
      setDomainModal(false);
      // Invalidate all queries (everything changes on domain switch)
      queryClient.invalidateQueries();
      // Refresh auth to get new domain
      await refresh();
    } catch (err: any) {
      Toast.show({ type: 'error', text1: err.message || 'Erro ao trocar domínio' });
    } finally {
      setResetting(false);
    }
  };

  // Build menu items based on domain
  const MENU_ITEMS = [
    { route: 'Automacao', icon: 'robot', label: 'Automação', desc: 'Configurar geração de vídeos' },
    { route: 'Jobs', icon: 'clipboard-list', label: 'Jobs', desc: 'Fila de processamento' },
  ];

  const handleOpenTutorial = async () => {
    try {
      await authApi.resetOnboarding();
    } catch {}
    setTutorialOpen(true);
  };

  // Only show Kids menu if the channel is in kids domain
  if (isKidsDomain) {
    MENU_ITEMS.push({ route: 'Kids', icon: 'baby-face', label: 'Kids', desc: 'Conteúdo infantil' });
    MENU_ITEMS.push({ route: 'KidsIdeas', icon: 'lightbulb-on', label: 'Ideias Kids', desc: 'Descoberta e fila de ideias' });
  }

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView style={styles.scroll} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
        {/* User info */}
        <View style={styles.userCard}>
          <View style={styles.avatar}>
            <Icon name="account" size={32} color={colors.accent} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.userName}>{user?.name || user?.email || 'Usuário'}</Text>
            <Text style={styles.userEmail}>{user?.email}</Text>
            {user?.is_admin && (
              <View style={styles.adminBadge}>
                <Text style={styles.adminText}>Administrador</Text>
              </View>
            )}
          </View>
        </View>

        {/* Domain card */}
        <TouchableOpacity style={styles.domainCard} onPress={() => setDomainModal(true)}>
          <View style={styles.domainIcon}>
            <Icon name="earth" size={24} color={colors.accent} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.domainLabel}>Domínio do canal</Text>
            <Text style={styles.domainValue}>{DOMAIN_LABELS[currentDomain] || currentDomain}</Text>
          </View>
          <Icon name="chevron-right" size={24} color={colors.textMuted} />
        </TouchableOpacity>

        {/* Menu items */}
        {MENU_ITEMS.map((item) => (
          <TouchableOpacity
            key={item.route}
            style={styles.menuItem}
            onPress={() => navigation.navigate(item.route)}
          >
            <View style={styles.menuIcon}>
              <Icon name={item.icon as any} size={24} color={colors.accent} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.menuLabel}>{item.label}</Text>
              <Text style={styles.menuDesc}>{item.desc}</Text>
            </View>
            <Icon name="chevron-right" size={24} color={colors.textMuted} />
          </TouchableOpacity>
        ))}

        {/* Admin */}
        {user?.is_admin && (
          <TouchableOpacity
            style={styles.menuItem}
            onPress={() => navigation.navigate('Admin')}
          >
            <View style={styles.menuIcon}>
              <Icon name="shield-account" size={24} color={colors.accent} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.menuLabel}>Admin</Text>
              <Text style={styles.menuDesc}>Gerenciar usuários</Text>
            </View>
            <Icon name="chevron-right" size={24} color={colors.textMuted} />
          </TouchableOpacity>
        )}

        {/* Tutorial */}
        <TouchableOpacity
          style={styles.menuItem}
          onPress={handleOpenTutorial}
        >
          <View style={styles.menuIcon}>
            <Icon name="help-circle" size={24} color={colors.accent} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.menuLabel}>Tutorial</Text>
            <Text style={styles.menuDesc}>Aprenda a usar o app</Text>
          </View>
          <Icon name="chevron-right" size={24} color={colors.textMuted} />
        </TouchableOpacity>

        {/* Logout */}
        <TouchableOpacity style={styles.logoutButton} onPress={handleLogout}>
          <View style={styles.menuIcon}>
            <Icon name="logout" size={24} color={colors.error} />
          </View>
          <Text style={styles.logoutText}>Sair da conta</Text>
        </TouchableOpacity>
      </ScrollView>

      {/* Domain selector modal */}
      <Modal
        visible={domainModal}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setDomainModal(false)}
      >
        <SafeAreaView style={styles.modalContainer} edges={['top']}>
          <View style={styles.modalHeader}>
            <TouchableOpacity onPress={() => setDomainModal(false)}>
              <Text style={styles.closeButton}>Fechar</Text>
            </TouchableOpacity>
            <Text style={styles.modalTitle}>Domínio do Canal</Text>
            <View style={{ width: 60 }} />
          </View>

          <ScrollView style={styles.modalScroll} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
            {/* Current domain */}
            <View style={styles.currentDomainCard}>
              <Icon name="check-circle" size={20} color={colors.accent} />
              <View>
                <Text style={styles.currentDomainLabel}>Domínio atual</Text>
                <Text style={styles.currentDomainValue}>{DOMAIN_LABELS[currentDomain] || currentDomain}</Text>
              </View>
            </View>

            {/* Domain options */}
            <Text style={styles.sectionLabel}>Trocar para</Text>
            <View style={styles.domainGrid}>
              {(domainData?.domains || []).map((d: any) => {
                const isActive = d.value === currentDomain;
                const isImplemented = d.implemented;
                return (
                  <TouchableOpacity
                    key={d.value}
                    style={[
                      styles.domainOption,
                      isActive && styles.domainOptionActive,
                      !isImplemented && styles.domainOptionDisabled,
                    ]}
                    onPress={() => isImplemented && handleDomainSelect(d.value)}
                    disabled={isActive || !isImplemented}
                  >
                    <Text style={[
                      styles.domainOptionText,
                      isActive && styles.domainOptionTextActive,
                      !isImplemented && styles.domainOptionTextDisabled,
                    ]}>
                      {d.label}
                    </Text>
                    {isActive ? (
                      <Icon name="check-circle" size={16} color={colors.accent} />
                    ) : !isImplemented ? (
                      <Text style={styles.domainSoon}>em breve</Text>
                    ) : null}
                  </TouchableOpacity>
                );
              })}
            </View>

            {/* Warning */}
            <View style={styles.warningBox}>
              <Icon name="alert" size={16} color="#f59e0b" />
              <Text style={styles.warningText}>
                Trocar de domínio apaga todo o estado de produção do canal (mídias, jobs, conteúdo não publicado, conhecimento). Vídeos já publicados no YouTube não são removidos.
              </Text>
            </View>
          </ScrollView>
        </SafeAreaView>
      </Modal>

      {/* Confirmation modal */}
      <Modal
        visible={!!confirmDomain}
        animationType="fade"
        transparent
        onRequestClose={() => setConfirmDomain(null)}
      >
        <View style={styles.confirmOverlay}>
          <View style={styles.confirmCard}>
            <View style={styles.confirmIconWrap}>
              <Icon name="alert-octagon" size={40} color="#ef4444" />
            </View>
            <Text style={styles.confirmTitle}>Trocar para {DOMAIN_LABELS[confirmDomain || ''] || confirmDomain}?</Text>
            <Text style={styles.confirmDesc}>
              Esta operação é DESTRUTIVA e não pode ser desfeita.{'\n\n'}
              O que será perdido:{'\n'}
              • Todas as mídias e gameplays{'\n'}
              • Jobs em andamento{'\n'}
              • Ideias e base de conhecimento{'\n'}
              • Configurações de automação{'\n\n'}
              O que NÃO é afetado:{'\n'}
              • Vídeos já publicados no YouTube{'\n'}
              • Conexão com Google/YouTube
            </Text>
            <View style={styles.confirmActions}>
              <TouchableOpacity
                style={styles.confirmCancelBtn}
                onPress={() => setConfirmDomain(null)}
                disabled={resetting}
              >
                <Text style={styles.confirmCancelText}>Cancelar</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.confirmDangerBtn}
                onPress={handleConfirmReset}
                disabled={resetting}
              >
                <Text style={styles.confirmDangerText}>
                  {resetting ? 'Trocando...' : 'Sim, trocar domínio'}
                </Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>

      {/* Onboarding tutorial */}
      <OnboardingModal
        visible={tutorialOpen}
        onClose={() => setTutorialOpen(false)}
        onNavigate={(tab) => {
          setTutorialOpen(false);
          // Navigate from MoreStack to the tab — need to go up to parent
          navigation.navigate(tab);
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
  scroll: {
    flex: 1,
  },
  userCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.lg,
  },
  avatar: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: colors.surfaceElevated,
    alignItems: 'center',
    justifyContent: 'center',
  },
  userName: {
    fontSize: fontSize.md,
    fontWeight: fontWeight.semibold,
    color: colors.text,
  },
  userEmail: {
    fontSize: fontSize.sm,
    color: colors.textMuted,
    marginTop: 2,
  },
  adminBadge: {
    marginTop: spacing.xs,
    backgroundColor: 'rgba(239,68,68,0.1)',
    borderWidth: 1,
    borderColor: 'rgba(239,68,68,0.2)',
    borderRadius: radius.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    alignSelf: 'flex-start',
  },
  adminText: {
    fontSize: fontSize.xs,
    color: colors.error,
    fontWeight: fontWeight.medium,
  },
  domainCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
  },
  domainIcon: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: colors.surfaceElevated,
    alignItems: 'center',
    justifyContent: 'center',
  },
  domainLabel: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
  },
  domainValue: {
    fontSize: fontSize.base,
    fontWeight: fontWeight.semibold,
    color: colors.text,
    marginTop: 2,
  },
  menuItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
  },
  menuIcon: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: colors.surfaceElevated,
    alignItems: 'center',
    justifyContent: 'center',
  },
  menuLabel: {
    fontSize: fontSize.base,
    fontWeight: fontWeight.medium,
    color: colors.text,
  },
  menuDesc: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
    marginTop: 2,
  },
  logoutButton: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: 'rgba(239,68,68,0.2)',
    padding: spacing.md,
    marginTop: spacing.sm,
  },
  logoutText: {
    fontSize: fontSize.base,
    fontWeight: fontWeight.medium,
    color: colors.error,
    flex: 1,
  },
  // Domain modal
  modalContainer: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  modalHeader: {
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
  modalTitle: {
    fontSize: fontSize.md,
    fontWeight: fontWeight.semibold,
    color: colors.text,
  },
  modalScroll: {
    flex: 1,
  },
  currentDomainCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    backgroundColor: 'rgba(16,185,129,0.08)',
    borderWidth: 1,
    borderColor: 'rgba(16,185,129,0.2)',
    borderRadius: radius.lg,
    padding: spacing.md,
  },
  currentDomainLabel: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
  },
  currentDomainValue: {
    fontSize: fontSize.base,
    fontWeight: fontWeight.semibold,
    color: colors.text,
    marginTop: 2,
  },
  sectionLabel: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.medium,
    color: colors.textSecondary,
  },
  domainGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
  },
  domainOption: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.sm,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm + 2,
    minWidth: 140,
  },
  domainOptionActive: {
    borderColor: 'rgba(16,185,129,0.4)',
    backgroundColor: 'rgba(16,185,129,0.08)',
  },
  domainOptionDisabled: {
    opacity: 0.5,
  },
  domainOptionText: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.medium,
    color: colors.textSecondary,
  },
  domainOptionTextActive: {
    color: colors.accent,
  },
  domainOptionTextDisabled: {
    color: colors.textMuted,
  },
  domainSoon: {
    fontSize: 10,
    color: colors.textMuted,
  },
  warningBox: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.sm,
    backgroundColor: 'rgba(245,158,11,0.08)',
    borderWidth: 1,
    borderColor: 'rgba(245,158,11,0.2)',
    borderRadius: radius.md,
    padding: spacing.md,
    marginTop: spacing.sm,
  },
  warningText: {
    flex: 1,
    fontSize: fontSize.xs,
    color: colors.textMuted,
    lineHeight: 18,
  },
  // Confirm modal
  confirmOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.8)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.lg,
  },
  confirmCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.xl,
    padding: spacing.xl,
    width: '100%',
    maxWidth: 400,
  },
  confirmIconWrap: {
    alignItems: 'center',
    marginBottom: spacing.md,
  },
  confirmTitle: {
    fontSize: fontSize.lg,
    fontWeight: fontWeight.bold,
    color: colors.text,
    textAlign: 'center',
    marginBottom: spacing.md,
  },
  confirmDesc: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
    lineHeight: 20,
    marginBottom: spacing.lg,
  },
  confirmActions: {
    flexDirection: 'row',
    gap: spacing.md,
  },
  confirmCancelBtn: {
    flex: 1,
    paddingVertical: spacing.md,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: 'center',
  },
  confirmCancelText: {
    fontSize: fontSize.base,
    color: colors.textSecondary,
    fontWeight: fontWeight.medium,
  },
  confirmDangerBtn: {
    flex: 1,
    paddingVertical: spacing.md,
    borderRadius: radius.md,
    backgroundColor: colors.error,
    alignItems: 'center',
  },
  confirmDangerText: {
    fontSize: fontSize.base,
    color: '#fff',
    fontWeight: fontWeight.semibold,
  },
});
