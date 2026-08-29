/**
 * React Query setup — QueryClientProvider wrapper.
 *
 * Provides a shared QueryClient with sensible defaults:
 * - staleTime: 30s (fallback when SSE events don't arrive)
 * - retry: 1 (don't hammer the server on failure)
 * - refetchOnWindowFocus: false (SSE handles updates)
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

export function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      })
  );

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
