import { useCallback, useEffect, useRef, useState } from "react";

export function usePoll<T>(fn: () => Promise<T>, intervalMs: number, deps: any[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const runRef = useRef<() => Promise<void>>(async () => {});

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const run = async () => {
      try {
        const d = await fnRef.current();
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
    runRef.current = run;
    run();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const refetch = useCallback(async () => {
    await runRef.current();
  }, []);

  return { data, loading, error, setData, refetch };
}
