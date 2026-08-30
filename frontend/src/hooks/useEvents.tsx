/**
 * SSE EventSource hook for the web frontend.
 *
 * Connects to /api/events/stream using the browser's native EventSource.
 * The bi_auth cookie is sent automatically (same-site, SameSite=Lax).
 *
 * Features:
 * - Automatic reconnection (native EventSource behavior)
 * - Event deduplication (ignores event IDs already seen — last 100)
 * - Connection status (connected/disconnected)
 * - Context provider that distributes events via useContext
 *
 * Usage:
 *   <EventsProvider>
 *     <App />
 *   </EventsProvider>
 *
 *   function MyComponent() {
 *     const events = useEvents();
 *     // events.connected, events.lastEvent
 *   }
 */

import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";

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

// SSE endpoint: /api/events/stream in dev, /gpcg/api/events/stream in prod
const SSE_URL = import.meta.env.PROD ? "/gpcg/api/events/stream" : "/api/events/stream";

export function EventsProvider({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState<GpcgEvent | null>(null);
  const seenIds = useRef<Set<string>>(new Set());
  const eventSourceRef = useRef<EventSource | null>(null);
  const hasConnectedBefore = useRef(false);
  const queryClient = useQueryClient();

  useEffect(() => {
    let mounted = true;

    function connect() {
      if (!mounted) return;

      // EventSource sends cookies with withCredentials (same-site)
      const es = new EventSource(SSE_URL, { withCredentials: true });
      eventSourceRef.current = es;

      es.onopen = () => {
        if (!mounted) return;
        setConnected(true);
        // On RECONNECT (not first connect), invalidate all queries to
        // recover state missed during the disconnection.
        if (hasConnectedBefore.current) {
          queryClient.invalidateQueries();
        }
        hasConnectedBefore.current = true;
      };

      es.onerror = () => {
        if (mounted) setConnected(false);
        // EventSource reconnects automatically — we just update the status
      };

      es.onmessage = (msg) => {
        if (!mounted) return;
        try {
          const event: GpcgEvent = JSON.parse(msg.data);
          // Deduplication: ignore events we've already seen
          if (seenIds.current.has(event.id)) return;
          seenIds.current.add(event.id);
          // Keep only last 100 IDs to prevent unbounded memory growth
          if (seenIds.current.size > 100) {
            const first = seenIds.current.values().next().value;
            if (first) seenIds.current.delete(first);
          }
          setLastEvent(event);
        } catch {
          // Ignore malformed events
        }
      };
    }

    connect();

    return () => {
      mounted = false;
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
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
