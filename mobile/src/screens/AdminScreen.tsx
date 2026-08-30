// i18n: aligned with web i18n migration cycle
import React from 'react';
import { View, Text, StyleSheet, FlatList, RefreshControl, Alert } from 'react-native';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useLiveData } from '../hooks/useLiveData';
import { SafeAreaView } from 'react-native-safe-area-context';
import { authApi } from '../api/endpoints';
import { Card, Badge, Button, EmptyState } from '../components/ui';
import Icon from 'react-native-vector-icons/MaterialCommunityIcons';
import { useAuth } from '../hooks/useAuth';
import { colors } from '../theme/colors';
import { fontSize, fontWeight, radius, spacing } from '../theme/spacing';
import { fmtDate } from '../utils/format';
import Toast from 'react-native-toast-message';

export function AdminScreen() {
  const queryClient = useQueryClient();
  const { user: currentUser } = useAuth();
  const { data: users, refetch, isRefetching } = useLiveData(
    ['users'],
    authApi.listUsers,
    []
  );

  const handleToggleActive = (user: any) => {
    const isSelf = currentUser?.id === user.id;
    Alert.alert(
      user.is_active ? 'Desativar usuário?' : 'Ativar usuário?',
      isSelf
        ? 'Você está tentando desativar sua própria conta. Você perderá acesso imediatamente.'
        : `${user.email}`,
      [
        { text: 'Cancelar', style: 'cancel' },
        {
          text: 'Confirmar',
          style: user.is_active ? 'destructive' : 'default',
          onPress: async () => {
            try {
              await authApi.updateUser(user.id, { is_active: !user.is_active });
              Toast.show({ type: 'success', text1: 'Atualizado' });
              queryClient.invalidateQueries({ queryKey: ['users'] });
            } catch (err: any) {
              Toast.show({ type: 'error', text1: err.message || 'Erro' });
            }
          },
        },
      ],
    );
  };

  const handleDelete = (user: any) => {
    const isSelf = currentUser?.id === user.id;
    if (isSelf) {
      Alert.alert(
        'Operação bloqueada',
        'Você não pode deletar sua própria conta pelo painel admin. Use a troca de domínio ou peça a outro admin para deletar.',
        [{ text: 'Entendi' }],
      );
      return;
    }
    Alert.alert(
      'Deletar usuário?',
      `Deletar ${user.email}?\n\nIsso vai remover TODOS os dados do usuário (gameplays, vídeos, jobs, ideias). Esta ação não pode ser desfeita.`,
      [
        { text: 'Cancelar', style: 'cancel' },
        {
          text: 'Deletar',
          style: 'destructive',
          onPress: () => {
            // Second confirmation
            Alert.alert('Confirmar novamente', `Tem certeza absoluta? ${user.email} e todos os dados serão perdidos.`, [
              { text: 'Cancelar', style: 'cancel' },
              {
                text: 'Deletar definitivamente',
                style: 'destructive',
                onPress: async () => {
                  try {
                    await authApi.deleteUser(user.id);
                    Toast.show({ type: 'success', text1: 'Usuário deletado' });
                    queryClient.invalidateQueries({ queryKey: ['users'] });
                  } catch (err: any) {
                    Toast.show({ type: 'error', text1: err.message || 'Erro' });
                  }
                },
              },
            ]);
          },
        },
      ],
    );
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <Text style={styles.title}>Admin</Text>
        <Text style={styles.subtitle}>Gerenciar usuários da plataforma</Text>
      </View>

      {/* Info banner */}
      <View style={styles.infoBanner}>
        <Icon name="information" size={16} color={colors.info} />
        <Text style={styles.infoText}>
          {users?.length || 0} usuário(s) registrado(s). Admins podem ativar/desativar e deletar contas.
          Auto-exclusão é bloqueada por segurança.
        </Text>
      </View>

      <FlatList
        data={users || []}
        keyExtractor={(u) => String(u.id)}
        refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={() => { refetch(); }} tintColor={colors.accent} />}
        contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}
        ListEmptyComponent={<Card><EmptyState title="Nenhum usuário" /></Card>}
        renderItem={({ item: u }) => {
          const isSelf = currentUser?.id === u.id;
          return (
            <Card padding={spacing.md}>
              <View style={styles.userRow}>
                {/* Avatar */}
                <View style={styles.avatar}>
                  <Text style={styles.avatarText}>
                    {(u.name || u.email || '?').charAt(0).toUpperCase()}
                  </Text>
                </View>
                <View style={{ flex: 1 }}>
                  <View style={styles.nameRow}>
                    <Text style={styles.userName}>{u.name || u.email}</Text>
                    {isSelf && (
                      <View style={styles.youBadge}>
                        <Text style={styles.youBadgeText}>Você</Text>
                      </View>
                    )}
                  </View>
                  <Text style={styles.userEmail}>{u.email}</Text>
                  <Text style={styles.metaText}>{fmtDate(u.created_at)}</Text>
                </View>
                <View style={styles.badges}>
                  {u.is_admin && <Badge label="Admin" variant="info" />}
                  <Badge label={u.is_active ? 'Ativo' : 'Inativo'} variant={u.is_active ? 'success' : 'warning'} />
                </View>
              </View>
              <View style={styles.actions}>
                <Button
                  title={u.is_active ? 'Desativar' : 'Ativar'}
                  size="sm"
                  variant="outline"
                  onPress={() => handleToggleActive(u)}
                />
                <Button
                  title="Deletar"
                  size="sm"
                  variant="danger"
                  onPress={() => handleDelete(u)}
                  disabled={isSelf}
                />
              </View>
            </Card>
          );
        }}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  header: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md },
  title: { fontSize: fontSize.xxxl, fontWeight: fontWeight.bold, color: colors.text },
  subtitle: { fontSize: fontSize.sm, color: colors.textMuted, marginTop: 2 },
  // Info banner
  infoBanner: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.sm,
    backgroundColor: 'rgba(59,130,246,0.08)',
    borderWidth: 1,
    borderColor: 'rgba(59,130,246,0.2)',
    borderRadius: radius.md,
    padding: spacing.md,
    marginHorizontal: spacing.lg,
  },
  infoText: {
    flex: 1,
    fontSize: fontSize.xs,
    color: colors.textSecondary,
    lineHeight: 18,
  },
  // User card
  userRow: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm },
  avatar: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: colors.surfaceElevated,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarText: {
    fontSize: fontSize.md,
    fontWeight: fontWeight.bold,
    color: colors.accent,
  },
  nameRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
  },
  userName: { fontSize: fontSize.base, fontWeight: fontWeight.semibold, color: colors.text },
  youBadge: {
    backgroundColor: 'rgba(45,212,191,0.15)',
    borderWidth: 1,
    borderColor: 'rgba(45,212,191,0.3)',
    borderRadius: radius.sm,
    paddingHorizontal: 6,
    paddingVertical: 1,
  },
  youBadgeText: {
    fontSize: 10,
    color: colors.accent,
    fontWeight: fontWeight.medium,
  },
  userEmail: { fontSize: fontSize.sm, color: colors.textSecondary, marginTop: 2 },
  metaText: { fontSize: fontSize.xs, color: colors.textMuted, marginTop: 2 },
  badges: { alignItems: 'flex-end', gap: spacing.xs },
  actions: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.md },
});
