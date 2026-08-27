import axios, { AxiosInstance, AxiosError } from 'axios';
import AsyncStorage from '@react-native-async-storage/async-storage';

// Production API base — same as web frontend
const PROD_API_BASE = 'https://brunointegrations.com/gpcg/api';
// Dev API base (when running against local VPS)
const DEV_API_BASE = 'http://10.0.2.2:8787/api'; // Android emulator → host localhost

const API_BASE = __DEV__ ? DEV_API_BASE : PROD_API_BASE;

const TOKEN_KEY = '@gpcg/token';
const USER_KEY = '@gpcg/user';

// SSO cookies (extracted from WebView, sent to /auth/token)
const BI_AUTH_KEY = '@gpcg/bi_auth';
const BI_REFRESH_KEY = '@gpcg/bi_refresh';

export const client: AxiosInstance = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
});

// Request interceptor — attach Bearer token
client.interceptors.request.use(async (config) => {
  const token = await AsyncStorage.getItem(TOKEN_KEY);
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Response interceptor — on 401, clear token (user must re-login)
client.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    if (error.response?.status === 401) {
      await AsyncStorage.removeItem(TOKEN_KEY);
      await AsyncStorage.removeItem(USER_KEY);
    }
    return Promise.reject(error);
  },
);

// ── Token storage helpers ────────────────────────────────────────────────────

export async function saveToken(token: string, user: any): Promise<void> {
  await AsyncStorage.setItem(TOKEN_KEY, token);
  await AsyncStorage.setItem(USER_KEY, JSON.stringify(user));
}

export async function getToken(): Promise<string | null> {
  return AsyncStorage.getItem(TOKEN_KEY);
}

export async function getUser(): Promise<any | null> {
  const raw = await AsyncStorage.getItem(USER_KEY);
  return raw ? JSON.parse(raw) : null;
}

export async function clearAuth(): Promise<void> {
  await AsyncStorage.removeItem(TOKEN_KEY);
  await AsyncStorage.removeItem(USER_KEY);
  await AsyncStorage.removeItem(BI_AUTH_KEY);
  await AsyncStorage.removeItem(BI_REFRESH_KEY);
}

// ── SSO cookie storage (extracted from WebView) ──────────────────────────────

export async function saveSSOCookies(biAuth: string, biRefresh?: string): Promise<void> {
  await AsyncStorage.setItem(BI_AUTH_KEY, biAuth);
  if (biRefresh) {
    await AsyncStorage.setItem(BI_REFRESH_KEY, biRefresh);
  }
}

export async function getSSOCookies(): Promise<{ bi_auth: string; bi_refresh?: string } | null> {
  const biAuth = await AsyncStorage.getItem(BI_AUTH_KEY);
  if (!biAuth) return null;
  const biRefresh = await AsyncStorage.getItem(BI_REFRESH_KEY);
  return { bi_auth: biAuth, bi_refresh: biRefresh || undefined };
}

// ── URL helpers (for media) ──────────────────────────────────────────────────

export const MEDIA_BASE = __DEV__ ? 'http://10.0.2.2:8787' : 'https://brunointegrations.com/gpcg';

export function videoUrl(videoId: number): string {
  return `${MEDIA_BASE}/api/videos/${videoId}/file`;
}

export function thumbUrl(videoId: number): string {
  return `${MEDIA_BASE}/api/videos/${videoId}/thumbnail`;
}

export function presentationImageUrl(imageKey: string): string {
  return `${MEDIA_BASE}/api/presentation/image/${imageKey}`;
}

// SSO login URL for WebView
export const SSO_LOGIN_URL = __DEV__
  ? 'http://10.0.2.2:3000/id/login?redirect=/gpcg/dashboard'
  : 'https://brunointegrations.com/id/login?redirect=/gpcg/dashboard';

// Token exchange URL (absolute, used by WebView JS)
export const PROD_TOKEN_URL = 'https://brunointegrations.com/gpcg/api/auth/token';
export const DEV_TOKEN_URL = 'http://10.0.2.2:8787/api/auth/token';

// BI Identity login URL (for native email/password login)
export const PROD_IDENTITY_LOGIN = 'https://brunointegrations.com/id/api/auth/login';
export const DEV_IDENTITY_LOGIN = 'http://10.0.2.2:3000/id/api/auth/login';

// BI Identity Google login URL (for native Google Sign-In)
export const PROD_IDENTITY_GOOGLE = 'https://brunointegrations.com/id/api/auth/google';
export const DEV_IDENTITY_GOOGLE = 'http://10.0.2.2:3000/id/api/auth/google';

// Google OAuth Web Client ID — must match what BI Identity expects as audience.
// BI Identity uses this clientId to verify the id_token from Google.
// See: https://react-native-google-signin.github.io/docs/troubleshooting
// The Android OAuth client (with SHA-1) is used by Play Services for app
// verification, but the webClientId here must match the BI Identity config.
export const PROD_GOOGLE_CLIENT_ID = '364332376664-d7dp6jhe7gclqjqk3dt92gtl400q7bk3.apps.googleusercontent.com';
export const DEV_GOOGLE_CLIENT_ID = '364332376664-d7dp6jhe7gclqjqk3dt92gtl400q7bk3.apps.googleusercontent.com';

// Identity service URL (for cookie domain matching)
export const IDENTITY_DOMAIN = __DEV__ ? '10.0.2.2' : 'brunointegrations.com';
