import { format, formatDistanceToNow } from 'date-fns';
import { ptBR, enUS, type Locale } from 'date-fns/locale';

const locales: Record<string, Locale> = { 'pt-BR': ptBR, en: enUS };

export function fmtDate(iso: string | null | undefined, lng = 'pt-BR'): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return format(d, 'P p', { locale: locales[lng] ?? ptBR });
}

export function fmtRelative(iso: string | null | undefined, lng = 'pt-BR'): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return formatDistanceToNow(d, { addSuffix: true, locale: locales[lng] ?? ptBR });
}
