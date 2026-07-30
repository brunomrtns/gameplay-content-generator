import { useState } from "react";
import { api } from "@/lib/api";
import { usePoll } from "@/hooks/usePoll";
import { useAuth } from "@/lib/auth";
import { Badge, Button, Card, EmptyState, Spinner } from "@/components/ui";
import { fmtDate } from "@/lib/utils";
import { toast } from "sonner";
import {
  Shield,
  Trash2,
  CheckCircle2,
  XCircle,
} from "lucide-react";

export function AdminPage() {
  const { user: currentUser } = useAuth();
  const { data: users, setData, loading } = usePoll(() => api.listUsers(), 10000);

  if (!currentUser?.is_admin) {
    return (
      <Card>
        <EmptyState
          icon={<Shield className="h-10 w-10" />}
          title="Acesso negado"
          description="Você precisa ser administrador para acessar esta página."
        />
      </Card>
    );
  }

  const toggleActive = async (u: any) => {
    try {
      await api.updateUser(u.id, { is_active: !u.is_active });
      toast.success(u.is_active ? "Usuário desativado" : "Usuário ativado");
      const updated = await api.listUsers();
      setData(updated);
    } catch (err: any) {
      toast.error(err.message);
    }
  };

  const deleteUser = async (u: any) => {
    if (u.id === currentUser?.id) {
      toast.error("Você não pode excluir seu próprio usuário");
      return;
    }
    if (!confirm(`Excluir usuário "${u.email}"? Esta ação não pode ser desfeita.`)) return;
    try {
      await api.deleteUser(u.id);
      toast.success("Usuário excluído");
      const updated = await api.listUsers();
      setData(updated);
    } catch (err: any) {
      toast.error(err.message);
    }
  };

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Administração</h1>
        <p className="mt-1 text-sm text-text-secondary">Gerencie usuários da plataforma</p>
      </div>

      {/* Info banner */}
      <Card>
        <div className="flex items-start gap-3">
          <Shield className="h-5 w-5 text-accent shrink-0 mt-0.5" />
          <div className="text-sm text-text-secondary">
            <p className="font-medium text-text">Autenticação via BI Identity</p>
            <p className="mt-1">
              Usuários e credenciais são gerenciados pelo Brunointegrations Identity Service.
              Novos usuários são criados automaticamente no primeiro login via SSO.
              Aqui você pode ativar/desativar e excluir usuários locais.
            </p>
          </div>
        </div>
      </Card>

      {/* Users list */}
      <div>
        <h2 className="mb-4 text-lg font-semibold">
          Usuários {users && `(${users.length})`}
        </h2>
        {loading && !users ? (
          <div className="flex justify-center py-16"><Spinner className="h-8 w-8" /></div>
        ) : !users || users.length === 0 ? (
          <Card><EmptyState title="Nenhum usuário" /></Card>
        ) : (
          <div className="space-y-3">
            {users.map((u: any) => (
              <Card key={u.id} className="!p-4">
                <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                  {/* User info */}
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-accent/10 text-accent text-sm font-bold">
                      {(u.name || u.email).charAt(0).toUpperCase()}
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-medium truncate">{u.name || u.email}</p>
                        {u.id === currentUser?.id && <Badge variant="info">você</Badge>}
                      </div>
                      <p className="text-xs text-text-muted truncate">{u.email}</p>
                      <p className="text-[10px] text-text-muted mt-0.5">Criado em {fmtDate(u.created_at)}</p>
                    </div>
                  </div>

                  {/* Badges */}
                  <div className="flex items-center gap-2">
                    {u.is_admin ? (
                      <Badge variant="warning"><Shield className="h-3 w-3" /> Admin</Badge>
                    ) : (
                      <Badge variant="default">Usuário</Badge>
                    )}
                    {u.is_active ? (
                      <Badge variant="success"><CheckCircle2 className="h-3 w-3" /> Ativo</Badge>
                    ) : (
                      <Badge variant="error"><XCircle className="h-3 w-3" /> Inativo</Badge>
                    )}
                  </div>

                  {/* Actions */}
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => toggleActive(u)}
                      disabled={u.id === currentUser?.id}
                    >
                      {u.is_active ? "Desativar" : "Ativar"}
                    </Button>
                    <Button
                      size="sm"
                      variant="danger"
                      onClick={() => deleteUser(u)}
                      disabled={u.id === currentUser?.id}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
