import { useEffect, useState } from "react";

export function usePoll<T>(fn: () => Promise<T>, intervalMs: number, deps: any[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const run = async () => {
      try {
        const d = await fn();
        if (active) {
          setData(d);
          setError(null);
        }
      } catch (e: any) {
        if (active) setError(e.message || String(e));
      } finally {
        if (active) {
          setLoading(false);
          timer = setTimeout(run, intervalMs);
        }
      }
    };
    run();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, loading, error, setData };
}
