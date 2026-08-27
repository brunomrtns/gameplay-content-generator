import React, { useRef, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Modal,
  TextInput,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  Alert,
} from 'react-native';
import { WebView, WebViewNavigation } from 'react-native-webview';
import {
  GoogleSignin,
  statusCodes,
} from '@react-native-google-signin/google-signin';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useAuth } from '../hooks/useAuth';
import {
  SSO_LOGIN_URL,
  saveToken,
  PROD_TOKEN_URL,
  DEV_TOKEN_URL,
  PROD_IDENTITY_LOGIN,
  DEV_IDENTITY_LOGIN,
  PROD_GOOGLE_CLIENT_ID,
  DEV_GOOGLE_CLIENT_ID,
  PROD_IDENTITY_GOOGLE,
  DEV_IDENTITY_GOOGLE,
} from '../api/client';
import { colors } from '../theme/colors';
import { fontSize, fontWeight, spacing, radius } from '../theme/spacing';
import { Button } from '../components/ui';

const TOKEN_URL = __DEV__ ? DEV_TOKEN_URL : PROD_TOKEN_URL;
const IDENTITY_LOGIN_URL = __DEV__ ? DEV_IDENTITY_LOGIN : PROD_IDENTITY_LOGIN;
const IDENTITY_GOOGLE_URL = __DEV__ ? DEV_IDENTITY_GOOGLE : PROD_IDENTITY_GOOGLE;
const GOOGLE_CLIENT_ID = __DEV__ ? DEV_GOOGLE_CLIENT_ID : PROD_GOOGLE_CLIENT_ID;

// Configure Google Sign-In
GoogleSignin.configure({
  webClientId: GOOGLE_CLIENT_ID,
  offlineAccess: false,
});

// ── WebView token exchange JS (fallback for Google login) ────────────────────
const TOKEN_EXCHANGE_JS = `
(function() {
  var attempts = 0;
  var maxAttempts = 5;
  function tryExchange() {
    attempts++;
    var xhr = new XMLHttpRequest();
    xhr.open('POST', TOKEN_URL_PLACEHOLDER, true);
    xhr.withCredentials = true;
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.timeout = 10000;
    xhr.ontimeout = function() {
      if (attempts < maxAttempts) { setTimeout(tryExchange, 1000); }
      else { window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'error', error: 'Timeout' })); }
    };
    xhr.onerror = function() {
      if (attempts < maxAttempts) { setTimeout(tryExchange, 1000); }
      else { window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'error', error: 'Network error' })); }
    };
    xhr.onload = function() {
      if (xhr.status === 200) {
        try {
          var data = JSON.parse(xhr.responseText);
          if (data && data.token) {
            window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'token', token: data.token, user: data.user }));
          } else {
            window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'error', error: 'Sem token: ' + xhr.responseText.substring(0, 200) }));
          }
        } catch(e) {
          window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'error', error: 'JSON error: ' + e.message }));
        }
      } else {
        if (attempts < maxAttempts) { setTimeout(tryExchange, 1000); }
        else { window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'error', error: 'HTTP ' + xhr.status }); }
      }
    };
    xhr.send('{}');
  }
  tryExchange();
})();
true;
`;

export function LoginScreen() {
  const { setAuthenticated } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [showWebView, setShowWebView] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const webViewRef = useRef<any>(null);
  const injectedRef = useRef(false);

  const tokenExchangeJs = TOKEN_EXCHANGE_JS.replace('TOKEN_URL_PLACEHOLDER', TOKEN_URL);

  // ── Native login (email/password) ──────────────────────────────────────────
  // POST to BI Identity /auth/login → get bi_auth + bi_refresh tokens
  // POST to GPCG /auth/token with those tokens → get mobile JWT
  const handleNativeLogin = async () => {
    if (!email.trim() || !password) {
      setError('Preencha email e senha');
      return;
    }

    setLoading(true);
    setError(null);

    try {
      // Step 1: Login at BI Identity
      const loginResp = await fetch(IDENTITY_LOGIN_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim(), password }),
      });

      const loginData = await loginResp.json();

      if (!loginResp.ok) {
        setError(loginData.error || 'Credenciais inválidas');
        return;
      }

      const biAuth = loginData.token;
      const biRefresh = loginData.refreshToken;

      if (!biAuth) {
        setError('BI Identity não retornou token');
        return;
      }

      // Step 2: Exchange at GPCG
      const tokenResp = await fetch(TOKEN_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bi_auth: biAuth, bi_refresh: biRefresh }),
      });

      const tokenData = await tokenResp.json();

      if (!tokenResp.ok || !tokenData.token) {
        setError(tokenData.detail || 'GPCG rejeitou o token');
        return;
      }

      // Step 3: Save and navigate
      await saveToken(tokenData.token, tokenData.user);
      setAuthenticated(tokenData.user);
    } catch (err: any) {
      setError('Erro de rede: ' + (err?.message || 'verifique sua conexão'));
    } finally {
      setLoading(false);
    }
  };

  // ── Google Sign-In (native) ────────────────────────────────────────────────
  // Uses @react-native-google-signin to sign in with the Google account
  // already configured on the device. Gets an id_token, sends it to BI
  // Identity /auth/google, gets bi_auth + bi_refresh, exchanges at GPCG.
  const [googleLoading, setGoogleLoading] = useState(false);

  const handleGoogleLogin = async () => {
    setGoogleLoading(true);
    setError(null);

    try {
      // Step 1: Check if device supports Google Sign-In
      await GoogleSignin.hasPlayServices();

      // Step 2: Sign in and get id_token
      const response = await GoogleSignin.signIn();
      if (response.type !== 'success') {
        // User cancelled or no success
        return;
      }
      const idToken = response.data?.idToken;

      if (!idToken) {
        setError('Google não retornou id_token');
        return;
      }

      // Step 3: Send id_token to BI Identity /auth/google
      const googleResp = await fetch(IDENTITY_GOOGLE_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          credential: idToken,
          redirect: '/gpcg/dashboard',
        }),
      });

      const googleData = await googleResp.json();

      if (!googleResp.ok) {
        setError(googleData.error || 'BI Identity rejeitou o token do Google');
        return;
      }

      const biAuth = googleData.token;
      const biRefresh = googleData.refreshToken;

      if (!biAuth) {
        setError('BI Identity não retornou token');
        return;
      }

      // Step 4: Exchange at GPCG
      const tokenResp = await fetch(TOKEN_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bi_auth: biAuth, bi_refresh: biRefresh }),
      });

      const tokenData = await tokenResp.json();

      if (!tokenResp.ok || !tokenData.token) {
        setError(tokenData.detail || 'GPCG rejeitou o token');
        return;
      }

      // Step 5: Save and navigate
      await saveToken(tokenData.token, tokenData.user);
      setAuthenticated(tokenData.user);
    } catch (err: any) {
      if (err.code === statusCodes.SIGN_IN_CANCELLED) {
        // User cancelled — no error
      } else if (err.code === statusCodes.PLAY_SERVICES_NOT_AVAILABLE) {
        setError('Google Play Services não disponível');
      } else {
        setError('Google: ' + (err?.message || 'erro desconhecido'));
      }
    } finally {
      setGoogleLoading(false);
    }
  };

  // ── WebView fallback (for Google login) ────────────────────────────────────
  const getPath = (url: string): string => {
    try {
      const u = new URL(url);
      return u.pathname;
    } catch {
      return url.split('?')[0].split('#')[0];
    }
  };

  const isGpcgAppUrl = (url: string): boolean => {
    const path = getPath(url);
    return path.includes('/gpcg') && !path.includes('/id/');
  };

  const handleShouldStartLoad = (request: { url: string }): boolean => {
    if (!isGpcgAppUrl(request.url)) return true;
    if (!injectedRef.current) {
      injectedRef.current = true;
      setTimeout(() => {
        webViewRef.current?.injectJavaScript(tokenExchangeJs);
      }, 300);
    }
    return false;
  };

  const handleNavigation = (nav: WebViewNavigation) => {
    if (isGpcgAppUrl(nav.url) && !nav.loading && !injectedRef.current) {
      injectedRef.current = true;
      setTimeout(() => {
        webViewRef.current?.injectJavaScript(tokenExchangeJs);
      }, 500);
    }
  };

  const handleMessage = (event: { nativeEvent: { data: string } }) => {
    try {
      const msg = JSON.parse(event.nativeEvent.data);
      if (msg.type === 'token' && msg.token) {
        setShowWebView(false);
        saveToken(msg.token, msg.user).then(() => {
          setAuthenticated(msg.user);
        });
      } else if (msg.type === 'error') {
        setError('WebView: ' + (msg.error || 'erro'));
        setShowWebView(false);
        injectedRef.current = false;
      }
    } catch {
      // ignore
    }
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.content}>
          {/* Logo */}
          <View style={styles.logoContainer}>
            <Text style={styles.logoText}>GPCG</Text>
            <Text style={styles.subtitle}>Gameplay Content Generator</Text>
          </View>

          <Text style={styles.description}>
            Faça login para gerenciar sua produção de vídeos.
          </Text>

          {error && (
            <View style={styles.errorBox}>
              <Text style={styles.errorText}>{error}</Text>
            </View>
          )}

          {/* Email/Password form */}
          <View style={styles.form}>
            <Text style={styles.label}>Email</Text>
            <TextInput
              style={styles.input}
              placeholder="seu@email.com"
              placeholderTextColor={colors.textMuted}
              keyboardType="email-address"
              autoCapitalize="none"
              autoCorrect={false}
              value={email}
              onChangeText={setEmail}
            />

            <Text style={styles.label}>Senha</Text>
            <TextInput
              style={styles.input}
              placeholder="••••••••"
              placeholderTextColor={colors.textMuted}
              secureTextEntry
              value={password}
              onChangeText={setPassword}
              onSubmitEditing={handleNativeLogin}
            />

            <Button
              title="Entrar"
              onPress={handleNativeLogin}
              variant="primary"
              size="lg"
              fullWidth
              loading={loading}
            />
          </View>

          {/* Divider */}
          <View style={styles.divider}>
            <View style={styles.dividerLine} />
            <Text style={styles.dividerText}>ou</Text>
            <View style={styles.dividerLine} />
          </View>

          {/* Google login — native Google Sign-In */}
          <Button
            title="Entrar com Google"
            onPress={handleGoogleLogin}
            variant="outline"
            size="lg"
            fullWidth
            loading={googleLoading}
          />

          <Text style={styles.hint}>
            Use email/senha ou entre com Google através do navegador seguro.
          </Text>
        </View>
      </ScrollView>

      {/* WebView modal for Google login */}
      <Modal
        visible={showWebView}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => { setShowWebView(false); injectedRef.current = false; }}
      >
        <View style={styles.webviewContainer}>
          <View style={styles.webviewHeader}>
            <TouchableOpacity
              onPress={() => {
                setShowWebView(false);
                injectedRef.current = false;
              }}
            >
              <Text style={styles.closeButton}>Cancelar</Text>
            </TouchableOpacity>
            <Text style={styles.webviewTitle}>Login BI Identity</Text>
            <View style={{ width: 60 }} />
          </View>
          <WebView
            ref={webViewRef}
            source={{ uri: SSO_LOGIN_URL }}
            onShouldStartLoadWithRequest={handleShouldStartLoad}
            onNavigationStateChange={handleNavigation}
            onMessage={handleMessage}
            sharedCookiesEnabled={true}
            thirdPartyCookiesEnabled={true}
            style={styles.webview}
          />
        </View>
      </Modal>
    </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  scrollContent: {
    flexGrow: 1,
    justifyContent: 'center',
    paddingHorizontal: spacing.xl,
    paddingVertical: spacing.xl,
  },
  content: {
    gap: spacing.lg,
  },
  logoContainer: {
    alignItems: 'center',
    gap: spacing.xs,
    marginBottom: spacing.md,
  },
  logoText: {
    fontSize: 48,
    fontWeight: fontWeight.bold,
    color: colors.accent,
    letterSpacing: 2,
  },
  subtitle: {
    fontSize: fontSize.sm,
    color: colors.textMuted,
  },
  description: {
    fontSize: fontSize.base,
    color: colors.textSecondary,
    textAlign: 'center',
    lineHeight: 22,
  },
  errorBox: {
    backgroundColor: colors.error + '20',
    borderRadius: radius.md,
    padding: spacing.md,
  },
  errorText: {
    fontSize: fontSize.sm,
    color: colors.error,
    textAlign: 'center',
  },
  form: {
    gap: spacing.xs,
  },
  label: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
    fontWeight: fontWeight.medium,
    marginBottom: 2,
  },
  input: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    fontSize: fontSize.base,
    color: colors.text,
    marginBottom: spacing.sm,
  },
  divider: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
  },
  dividerLine: {
    flex: 1,
    height: 1,
    backgroundColor: colors.border,
  },
  dividerText: {
    fontSize: fontSize.sm,
    color: colors.textMuted,
  },
  hint: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
    textAlign: 'center',
  },
  webviewContainer: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  webviewHeader: {
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
  webviewTitle: {
    fontSize: fontSize.md,
    fontWeight: fontWeight.semibold,
    color: colors.text,
  },
  webview: {
    flex: 1,
  },
});
