import { useState, useEffect, useCallback } from 'react';
import { Linking, AppState, AppStateStatus } from 'react-native';
import DeviceInfo from 'react-native-device-info';
import { appApi, AppVersionInfo } from '../api/endpoints';

// Base URL for download
const DOWNLOAD_BASE = __DEV__
  ? 'http://10.0.2.2:8787'
  : 'https://brunointegrations.com/gpcg';

export interface UpdateInfo {
  hasUpdate: boolean;
  version: string | null;
  versionCode: number | null;
  changelog: string | null;
  sizeBytes: number | null;
  releasedAt: string | null;
}

export function useAppUpdate() {
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null);
  const [checking, setChecking] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  // Track which versionCode was dismissed so re-checking after update
  // doesn't re-show the banner for the same version
  const [dismissedCode, setDismissedCode] = useState<number | null>(null);

  const checkForUpdate = useCallback(async () => {
    setChecking(true);
    try {
      const info: AppVersionInfo = await appApi.getVersion();
      if (!info.available || !info.version || !info.versionCode) {
        setUpdateInfo({
          hasUpdate: false,
          version: null,
          versionCode: null,
          changelog: null,
          sizeBytes: null,
          releasedAt: null,
        });
        return;
      }

      // Get installed version info from the OS
      // getBuildNumber() returns versionCode on Android
      const currentVersionCode = parseInt(DeviceInfo.getBuildNumber(), 10) || 0;
      const currentVersion = DeviceInfo.getVersion();

      // Only show update if server versionCode is STRICTLY greater
      // than installed versionCode. This prevents false positives when:
      // - User installs the latest APK (codes match → no banner)
      // - Server returns same version (not greater → no banner)
      const hasUpdate = info.versionCode > currentVersionCode;

      // Debug log to help diagnose false positives
      console.log('[useAppUpdate]', {
        serverVersionCode: info.versionCode,
        currentVersionCode,
        currentVersion,
        hasUpdate,
      });

      // Auto-clear dismissed state if a newer version appears
      // (user dismissed v5, now v6 is available → show banner again)
      if (hasUpdate && dismissedCode !== null && info.versionCode > dismissedCode) {
        setDismissed(false);
        setDismissedCode(null);
      }

      setUpdateInfo({
        hasUpdate,
        version: info.version,
        versionCode: info.versionCode,
        changelog: info.changelog,
        sizeBytes: info.size_bytes,
        releasedAt: info.released_at,
      });
    } catch {
      // Silent fail — don't bother user if version check fails
      setUpdateInfo(null);
    } finally {
      setChecking(false);
    }
  }, [dismissedCode]);

  // Check on mount
  useEffect(() => {
    checkForUpdate();
  }, [checkForUpdate]);

  // Re-check when app returns to foreground (user might have just installed update)
  useEffect(() => {
    const handler = (nextState: AppStateStatus) => {
      if (nextState === 'active') {
        checkForUpdate();
      }
    };
    const sub = AppState.addEventListener('change', handler);
    return () => sub.remove();
  }, [checkForUpdate]);

  const openDownloadPage = useCallback(() => {
    const url = `${DOWNLOAD_BASE}/api/app/download`;
    Linking.openURL(url).catch(() => {});
  }, []);

  const dismiss = useCallback(() => {
    setDismissed(true);
    if (updateInfo?.versionCode) {
      setDismissedCode(updateInfo.versionCode);
    }
  }, [updateInfo]);

  return {
    updateInfo,
    checking,
    dismissed,
    checkForUpdate,
    openDownloadPage,
    dismiss,
    currentVersion: DeviceInfo.getVersion(),
  };
}
