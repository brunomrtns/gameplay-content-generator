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
  onboarding_completed: boolean;
  created_at: string;
}

interface AuthState {
  user: User | null;
  setUser: (user: User) => void;
  logout: () => void;
  updateUser: (user: User) => void;
}

export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      setUser: (user) => set({ user }),
      logout: () => set({ user: null }),
      updateUser: (user) => set({ user }),
    }),
    { name: "gpcg-auth" }
  )
);
