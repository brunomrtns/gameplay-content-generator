import React, { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  Modal,
  Dimensions,
  Animated,
  Easing,
} from 'react-native';
import Icon from 'react-native-vector-icons/MaterialCommunityIcons';
import { colors } from '../theme/colors';
import { fontSize, fontWeight, radius, spacing } from '../theme/spacing';
import { authApi } from '../api/endpoints';
import { useAuth } from '../hooks/useAuth';

interface TourStep {
  tab: string;
  icon: string;
  title: string;
  description: string;
}

const STEPS: TourStep[] = [
  {
    tab: 'Conteudo',
    icon: 'film',
    title: 'Conteúdo & Identidade do Canal',
    description:
      'Aqui você envia suas gravações de gameplay e configura o perfil do canal — nicho, público-alvo, tom de voz e narrativa. A IA usa essas informações para personalizar os roteiros.',
  },
  {
    tab: 'Automacao',
    icon: 'cog',
    title: 'Automação',
    description:
      'Configure como seus vídeos serão gerados: formato, voz da narração (TTS), legendas, transições e estilo criativo. Você só precisa fazer isso uma vez.',
  },
  {
    tab: 'Ideias',
    icon: 'lightbulb-on',
    title: 'Ideias & Fila de Jobs',
    description:
      'A IA gera ideias de conteúdo automaticamente. Quando você aprova uma ideia, ela entra na fila de processamento e o worker gera o vídeo.',
  },
  {
    tab: 'Videos',
    icon: 'video',
    title: 'Seus Vídeos',
    description:
      'Quando os vídeos estão prontos, eles aparecem aqui. Você pode publicar diretamente no YouTube, compartilhar ou baixar.',
  },
];

interface OnboardingModalProps {
  visible: boolean;
  onClose: () => void;
  onNavigate?: (tab: string) => void;
}

export function OnboardingModal({ visible, onClose, onNavigate }: OnboardingModalProps) {
  const [step, setStep] = useState(0);
  const fadeAnim = useState(new Animated.Value(0))[0];
  const { refresh } = useAuth();

  const current = STEPS[step];
  const isLast = step === STEPS.length - 1;
  const screenWidth = Dimensions.get('window').width;

  useEffect(() => {
    if (visible) {
      setStep(0);
      Animated.timing(fadeAnim, {
        toValue: 1,
        duration: 200,
        easing: Easing.ease,
        useNativeDriver: true,
      }).start();
    } else {
      fadeAnim.setValue(0);
    }
  }, [visible]);

  const handleClose = useCallback(async (completed: boolean) => {
    if (completed) {
      try {
        await authApi.completeOnboarding();
        await refresh();
      } catch {}
    }
    onClose();
  }, [onClose, refresh]);

  const handleNext = () => {
    if (isLast) {
      handleClose(true);
    } else {
      setStep(step + 1);
    }
  };

  const handlePrev = () => {
    if (step > 0) setStep(step - 1);
  };

  const handleNavigateToStep = () => {
    if (onNavigate) {
      onNavigate(current.tab);
    }
    handleClose(true);
  };

  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      onRequestClose={() => handleClose(true)}
    >
      <View style={styles.overlay}>
        <Animated.View
          style={[
            styles.card,
            {
              opacity: fadeAnim,
              transform: [{
                scale: fadeAnim.interpolate({
                  inputRange: [0, 1],
                  outputRange: [0.95, 1],
                }),
              }],
            },
          ]}
        >
          {/* Header */}
          <View style={styles.header}>
            <View style={styles.headerLeft}>
              <Icon name="help-circle" size={20} color={colors.accent} />
              <Text style={styles.headerText}>
                Tutorial · Passo {step + 1} de {STEPS.length}
              </Text>
            </View>
            <TouchableOpacity
              onPress={() => handleClose(true)}
              style={styles.closeBtn}
              hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
            >
              <Icon name="close" size={20} color={colors.textMuted} />
            </TouchableOpacity>
          </View>

          {/* Progress bar */}
          <View style={styles.progressTrack}>
            <View
              style={[
                styles.progressBar,
                { width: `${((step + 1) / STEPS.length) * 100}%` },
              ]}
            />
          </View>

          {/* Content */}
          <View style={styles.content}>
            <View style={styles.iconWrap}>
              <Icon name={current.icon} size={32} color={colors.accent} />
            </View>
            <Text style={styles.title}>{current.title}</Text>
            <Text style={styles.description}>{current.description}</Text>
          </View>

          {/* Navigate button */}
          <TouchableOpacity
            onPress={handleNavigateToStep}
            style={styles.navigateBtn}
          >
            <Text style={styles.navigateText}>Ir para esta tela</Text>
            <Icon name="arrow-right" size={16} color={colors.accent} />
          </TouchableOpacity>

          {/* Footer */}
          <View style={styles.footer}>
            <TouchableOpacity
              onPress={() => handleClose(true)}
              style={styles.skipBtn}
            >
              <Text style={styles.skipText}>Pular tutorial</Text>
            </TouchableOpacity>
            <View style={styles.footerRight}>
              {step > 0 && (
                <TouchableOpacity
                  onPress={handlePrev}
                  style={styles.prevBtn}
                >
                  <Icon name="chevron-left" size={20} color={colors.textSecondary} />
                  <Text style={styles.prevText}>Voltar</Text>
                </TouchableOpacity>
              )}
              <TouchableOpacity
                onPress={handleNext}
                style={styles.nextBtn}
              >
                <Text style={styles.nextText}>
                  {isLast ? 'Concluir' : 'Próximo'}
                </Text>
                <Icon
                  name={isLast ? 'check' : 'chevron-right'}
                  size={20}
                  color="#fff"
                />
              </TouchableOpacity>
            </View>
          </View>
        </Animated.View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.6)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.lg,
  },
  card: {
    width: '100%',
    maxWidth: 440,
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    overflow: 'hidden',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.md,
    paddingTop: spacing.md,
    paddingBottom: spacing.sm,
  },
  headerLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
  },
  headerText: {
    fontSize: fontSize.xs,
    fontWeight: fontWeight.semibold,
    color: colors.textSecondary,
  },
  closeBtn: {
    width: 32,
    height: 32,
    justifyContent: 'center',
    alignItems: 'center',
    borderRadius: radius.md,
  },
  progressTrack: {
    height: 3,
    backgroundColor: colors.surfaceElevated,
    marginHorizontal: spacing.md,
    marginBottom: spacing.md,
    borderRadius: radius.full,
    overflow: 'hidden',
  },
  progressBar: {
    height: '100%',
    backgroundColor: colors.accent,
    borderRadius: radius.full,
  },
  content: {
    paddingHorizontal: spacing.md,
    paddingBottom: spacing.md,
  },
  iconWrap: {
    width: 56,
    height: 56,
    borderRadius: radius.lg,
    backgroundColor: colors.surfaceElevated,
    borderWidth: 1,
    borderColor: colors.accent + '30',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: spacing.md,
  },
  title: {
    fontSize: fontSize.lg,
    fontWeight: fontWeight.bold,
    color: colors.text,
    marginBottom: spacing.xs,
  },
  description: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
    lineHeight: 20,
  },
  navigateBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.xs,
    marginHorizontal: spacing.md,
    marginBottom: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.accent + '40',
    backgroundColor: colors.accent + '15',
  },
  navigateText: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.semibold,
    color: colors.accent,
  },
  footer: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
  },
  skipBtn: {
    paddingVertical: spacing.sm,
  },
  skipText: {
    fontSize: fontSize.sm,
    color: colors.textMuted,
  },
  footerRight: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
  },
  prevBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.sm,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
  },
  prevText: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
  },
  nextBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
    borderRadius: radius.md,
    backgroundColor: colors.accent,
  },
  nextText: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.semibold,
    color: '#fff',
  },
});
