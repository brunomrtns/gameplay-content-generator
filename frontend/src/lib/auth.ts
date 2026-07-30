import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface User {
  id: number;
  email: string;
  name: string | null;
  is_admin: boolean;
  is_active: boolean;
  has_youtube: boolean;
  channel_title: string | null;
  created_at: string;
}

interface AuthState {
  token: string | null;
  user: User | null;
  setAuth: (token: string, user: User) => void;
  logout: () => void;
  updateUser: (user: User) => void;
}

export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      setAuth: (token, user) => set({ token, user }),
      logout: () => set({ token: null, user: null }),
      updateUser: (user) => set({ user }),
    }),
    { name: "gpcg-auth" }
  )
);
