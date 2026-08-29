import { useState, useEffect, useRef, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Search, X, Gamepad2, Loader2 } from "lucide-react";

export interface CatalogGame {
  id: number;
  name: string;
  slug: string;
  cover_url: string | null;
  total_rating: number | null;
  total_rating_count: number | null;
  release_year: number | null;
  genres: string[];
}

interface GameSearchModalProps {
  open: boolean;
  onClose: () => void;
  onSelect: (game: CatalogGame) => void;
  title?: string;
  subtitle?: string;
  /** Optional: show a "clear/remove" button to unset the game */
  allowClear?: boolean;
  onClear?: () => void;
}

export function GameSearchModal({
  open,
  onClose,
  onSelect,
  title = "Escolher Jogo",
  subtitle = "Busque pelo nome ou nome alternativo (GTA, Witcher, etc.)",
  allowClear = false,
  onClear,
}: GameSearchModalProps) {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [highlightIndex, setHighlightIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Focus input on open
  useEffect(() => {
    if (open) {
      setQuery("");
      setDebouncedQuery("");
      setHighlightIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  // Debounce query — update debouncedQuery 250ms after user stops typing
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedQuery(query), 250);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query]);

  // React Query — cached search results with staleTime so repeated searches are instant
  const { data, isLoading } = useQuery({
    queryKey: ["catalog-search", debouncedQuery],
    queryFn: async () => {
      const res = await api.autocompleteCatalog(debouncedQuery.trim(), 10);
      return (res.results || []) as CatalogGame[];
    },
    enabled: open && debouncedQuery.trim().length >= 2,
    staleTime: 60_000, // cache results for 1 minute (catalog data rarely changes)
    placeholderData: (prev) => prev, // keep previous results while loading new ones
  });

  const results = data || [];

  // Reset highlight when results change
  useEffect(() => {
    setHighlightIndex(0);
  }, [debouncedQuery]);

  // Reset on close
  if (!open) return null;

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlightIndex((i) => Math.min(i + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlightIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && results[highlightIndex]) {
      e.preventDefault();
      handleSelect(results[highlightIndex]);
    } else if (e.key === "Escape") {
      onClose();
    }
  };

  const handleSelect = (game: CatalogGame) => {
    onSelect(game);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/70 backdrop-blur-sm pt-20"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-xl border border-border-bright bg-surface-elevated shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div className="flex items-center gap-2">
            <Gamepad2 className="h-4 w-4 text-accent" />
            <h3 className="text-sm font-semibold text-text">{title}</h3>
          </div>
          <button
            onClick={onClose}
            className="text-text-muted hover:text-text transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Search input */}
        <div className="p-4">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-text-muted" />
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Digite o nome do jogo..."
              className="w-full rounded-lg border border-border bg-surface pl-9 pr-3 py-2.5 text-sm text-text placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
            />
            {isLoading && (
              <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-accent animate-spin" />
            )}
          </div>
          <p className="mt-1.5 text-xs text-text-muted">{subtitle}</p>
        </div>

        {/* Results */}
        <div className="max-h-80 overflow-y-auto px-4 pb-4">
          {query.trim().length < 2 ? (
            <div className="py-8 text-center text-sm text-text-muted">
              Digite pelo menos 2 caracteres para buscar
            </div>
          ) : results.length === 0 && !isLoading ? (
            <div className="py-8 text-center text-sm text-text-muted">
              Nenhum jogo encontrado para "{query}"
            </div>
          ) : (
            <div className="space-y-1">
              {results.map((game, i) => (
                <button
                  key={game.id}
                  onClick={() => handleSelect(game)}
                  onMouseEnter={() => setHighlightIndex(i)}
                  className={`flex w-full items-center gap-3 rounded-lg border p-2.5 text-left transition-all ${
                    i === highlightIndex
                      ? "border-accent/40 bg-accent/10"
                      : "border-border bg-surface hover:border-accent/20"
                  }`}
                >
                  {/* Cover thumbnail */}
                  {game.cover_url ? (
                    <img
                      src={game.cover_url}
                      alt=""
                      className="h-10 w-10 shrink-0 rounded object-cover"
                      loading="lazy"
                    />
                  ) : (
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded bg-surface-elevated">
                      <Gamepad2 className="h-4 w-4 text-text-muted" />
                    </div>
                  )}
                  {/* Info */}
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-text">{game.name}</p>
                    <div className="flex items-center gap-2 text-xs text-text-muted">
                      {game.release_year && <span>{game.release_year}</span>}
                      {game.genres && game.genres.length > 0 && (
                        <span className="truncate">{game.genres.slice(0, 2).join(", ")}</span>
                      )}
                      {game.total_rating && (
                        <span className="text-accent">★ {game.total_rating.toFixed(0)}</span>
                      )}
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Footer with clear option */}
        {allowClear && (
          <div className="border-t border-border px-4 py-3">
            <button
              onClick={() => {
                onClear?.();
                onClose();
              }}
              className="text-xs text-text-muted hover:text-text transition-colors"
            >
              Remover associação de jogo
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
