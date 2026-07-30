import { useState } from "react";
import { api } from "@/lib/api";
import { usePoll } from "@/hooks/usePoll";
import { useAuth } from "@/lib/auth";
import { Badge, Button, Card, Input, Label, Spinner, EmptyState } from "@/components/ui";
import { fmtDate } from "@/lib/utils";
import { toast } from "sonner";
import {
  Shield,
  UserPlus,
  KeyRound,
  Trash2,
  CheckCircle2,
  XCircle,
  Mail,
  Lock,
} from "lucide-react";

export function AdminPage() {
  const { user: currentUser } = useAuth();
  const { data: users, setData, loading } = usePoll(() => api.listUsers(), 10000);

  // Create user form
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [creating, setCreating] = useState(false);

  // Reset password state
  const [resetting, setResetting] = useState<number | null>(null);
  const [newPassword, setNewPassword] = useState("");

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

  const createUser = async () => {
    if (!email.trim() || !password) {
      toast.error("Email e senha são obrigatórios");
      return;
    }
    setCreating(true);
    try {
      await api.register(email.trim(), name.trim(), password);
      toast.success(`Usuário "${email}" criado`);
      setEmail(""); setName(""); setPassword("");
      const updated = await api.listUsers();
      setData(updated);
    } catch (err: any) {
      toast.error(err.message);
    } finally {
      setCreating(false);
    }
  };

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

  const toggleAdmin = async (u: any) => {
    try {
      await api.updateUser(u.id, { is_admin: !u.is_admin });
      toast.success(u.is_admin ? "Admin removido" : "Admin concedido");
      const updated = await api.listUsers();
      setData(updated);
    } catch (err: any) {
      toast.error(err.message);
    }
  };

  const resetPassword = async (id: number) => {
    if (!newPassword || newPassword.length < 6) {
      toast.error("Senha deve ter no mínimo 6 caracteres");
      return;
    }
    try {
      await api.resetPassword(id, newPassword);
      toast.success("Senha redefinida");
      setResetting(null);
      setNewPassword("");
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
        <p className="mt-1 text-sm text-text-secondary">Gerencie usuários e permissões da plataforma</p>
      </div>

      {/* Create user */}
      <Card>
        <div className="flex items-center gap-2 mb-4">
          <UserPlus className="h-5 w-5 text-accent" />
          <h2 className="text-sm font-semibold">Criar novo usuário</h2>
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          <div>
            <Label>Email</Label>
            <div className="relative">
              <Mail className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
              <Input value={email} onChange={setEmail} placeholder="voce@email.com" className="pl-11" disabled={creating} />
            </div>
          </div>
          <div>
            <Label>Nome</Label>
            <Input value={name} onChange={setName} placeholder="Nome (opcional)" disabled={creating} />
          </div>
          <div>
            <Label>Senha</Label>
            <div className="relative">
              <Lock className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
              <Input type="password" value={password} onChange={setPassword} placeholder="••••••••" className="pl-11" disabled={creating} />
            </div>
          </div>
        </div>
        <div className="mt-4">
          <Button variant="primary" onClick={createUser} disabled={creating}>
            {creating ? <><Spinner className="h-4 w-4" /> Criando...</> : <><UserPlus className="h-4 w-4" /> Criar usuário</>}
          </Button>
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
                      variant="outline"
                      onClick={() => toggleAdmin(u)}
                      disabled={u.id === currentUser?.id}
                    >
                      {u.is_admin ? "Remover admin" : "Tornar admin"}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => { setResetting(resetting === u.id ? null : u.id); setNewPassword(""); }}
                    >
                      <KeyRound className="h-3.5 w-3.5" /> Senha
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

                {/* Reset password inline */}
                {resetting === u.id && (
                  <div className="mt-4 flex items-end gap-2 rounded-lg border border-border bg-surface-elevated/50 p-3">
                    <div className="flex-1">
                      <Label>Nova senha</Label>
                      <Input type="password" value={newPassword} onChange={setNewPassword} placeholder="Mínimo 6 caracteres" />
                    </div>
                    <Button size="sm" variant="primary" onClick={() => resetPassword(u.id)}>Redefinir</Button>
                    <Button size="sm" variant="ghost" onClick={() => { setResetting(null); setNewPassword(""); }}>Cancelar</Button>
                  </div>
                )}
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
