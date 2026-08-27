import { useEffect } from 'react';
import { BackHandler } from 'react-native';

/**
 * Hook to handle the Android hardware back button.
 * When `enabled` is true, pressing back will call `onBack` instead
 * of navigating back. This is useful for modals and overlays.
 *
 * @param onBack Called when back is pressed and `enabled` is true.
 * @param enabled Whether the handler is active (e.g. modal is visible).
 */
export function useBackHandler(onBack: () => void, enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;

    const subscription = BackHandler.addEventListener(
      'hardwareBackPress',
      () => {
        onBack();
        return true; // Prevent default navigation
      },
    );

    return () => subscription.remove();
  }, [onBack, enabled]);
}
