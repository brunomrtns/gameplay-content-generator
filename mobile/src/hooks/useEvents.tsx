/**
 * SSE EventSource hook for React Native (mobile).
 *
 * Uses react-native-sse which supports custom Authorization headers
 * (unlike browser EventSource which can't set headers).
 *
 * Features:
 * - Sends JWT Bearer token as Authorization header
 * - Automatic reconnection (react-native-sse built-in)
 * - Event deduplication (ignores event IDs already seen — last 100)
 * - Connection status (connected/disconnected)
 * - Context provider that distributes events via useContext
 *
 * Usage:
 *   <EventsProvider>
 *     <App />
 *   </EventsProvider>
 *
 *   function MyScreen() {
 *     const events = useEvents();
 *     // events.connected, events.lastEvent
 *   }
 */

import React, {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react';
import EventSource from 'react-native-sse';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { getToken } from '../api/client';
import { useQueryClient } from '@tanstack/react-query';

export interface GpcgEvent {
  id: string;
  type: string;
  payload: Record<string, unknown>;
  ts: number;
  channel: string;
}

interface EventsContextValue {
  connected: boolean;
  lastEvent: GpcgEvent | null;
}

const EventsContext = createContext<EventsContextValue>({
  connected: false,
  lastEvent: null,
});

const TOKEN_KEY = '@gpcg/token';

// SSE endpoint: same base as API client
const PROD_SSE_URL = 'https://brunointegrations.com/gpcg/api/events/stream';
const DEV_SSE_URL = 'http://10.0.2.2:8787/api/events/stream';
const SSE_URL = __DEV__ ? DEV_SSE_URL : PROD_SSE_URL;

export function EventsProvider({ children }: { children: React.ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState<GpcgEvent | null>(null);
  const seenIds = useRef<Set<string>>(new Set());
  const esRef = useRef<EventSource | null>(null);
  const hasConnectedBefore = useRef(false);
  const queryClient = useQueryClient();

  useEffect(() => {
    let mounted = true;

    async function connect() {
      if (!mounted) return;

      const token = await AsyncStorage.getItem(TOKEN_KEY);
      if (!token) {
        // No token — don't connect (user not logged in)
        return;
      }

      const es = new EventSource(SSE_URL, {
        headers: { Authorization: `Bearer ${token}` },
        timeout: 0, // no timeout — keep connection alive
      });
      esRef.current = es;

      es.addEventListener('open', () => {
        if (!mounted) return;
        setConnected(true);
        // On RECONNECT (not first connect), invalidate all queries to
        // recover state missed during the disconnection.
        if (hasConnectedBefore.current) {
          queryClient.invalidateQueries();
        }
        hasConnectedBefore.current = true;
      });

      es.addEventListener('error', () => {
        if (mounted) setConnected(false);
        // react-native-sse reconnects automatically
      });

      es.addEventListener('message', (msg: any) => {
        if (!mounted) return;
        try {
          const event: GpcgEvent = JSON.parse(msg.data);
          // Deduplication
          if (seenIds.current.has(event.id)) return;
          seenIds.current.add(event.id);
          if (seenIds.current.size > 100) {
            const first = seenIds.current.values().next().value;
            if (first) seenIds.current.delete(first);
          }
          setLastEvent(event);
        } catch {
          // Ignore malformed events
        }
      });
    }

    connect();

    return () => {
      mounted = false;
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
    };
  }, []);

  return (
    <EventsContext.Provider value={{ connected, lastEvent }}>
      {children}
    </EventsContext.Provider>
  );
}

export function useEvents() {
  return useContext(EventsContext);
}
