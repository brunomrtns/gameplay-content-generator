/**
 * useLiveData — wraps useQuery with SSE event invalidation for React Native.
 *
 * When a relevant event arrives via SSE, the query is invalidated,
 * triggering a refetch. This replaces refetchInterval polling.
 *
 * Features:
 * - Event-driven invalidation (no polling)
 * - staleTime: 30s fallback (if no events arrive, query goes stale)
 * - Deduplication via useEvents (prevents double-invalidation on reconnect)
 *
 * Usage:
 *   const { data, isLoading, error, refetch } = useLiveData(
 *     ['dashboard'],
 *     () => dashboardApi.get(),
 *     ['job.status_changed', 'video.created']
 *   );
 */

import { useEffect, useRef } from 'react';
import { useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { useEvents } from './useEvents';

export function useLiveData<T>(
  queryKey: QueryKey,
  queryFn: () => Promise<T>,
  eventTypes: string[] = []
) {
  const queryClient = useQueryClient();
  const { lastEvent } = useEvents();
  const lastProcessedId = useRef<string | null>(null);

  useEffect(() => {
    if (!lastEvent) return;
    if (lastProcessedId.current === lastEvent.id) return;
    lastProcessedId.current = lastEvent.id;

    if (eventTypes.includes(lastEvent.type)) {
      queryClient.invalidateQueries({ queryKey });
    }
  }, [lastEvent, eventTypes, queryKey, queryClient]);

  return useQuery({
    queryKey,
    queryFn,
    staleTime: 30_000,
  });
}
