const BULL = 'var(--color-bull-text)';
const BEAR = 'var(--color-bear-text)';
const NEUTRAL = 'var(--text-secondary)';
const MUTED = 'var(--text-muted)';
const BULL_DIM = 'var(--color-bull-dim)';
const BEAR_DIM = 'var(--color-bear-dim)';

export function colorForChange(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return MUTED;
  if (v > 0) return BULL;
  if (v < 0) return BEAR;
  return NEUTRAL;
}

export function trendIcon(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '';
  if (v > 0) return '▲';
  if (v < 0) return '▼';
  return '';
}

export function bgForExtreme(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return 'transparent';
  if (v > 0.05) return BULL_DIM;
  if (v < -0.05) return BEAR_DIM;
  return 'transparent';
}

export function colorForPe(pb: number | null): string {
  if (pb == null) return MUTED;
  if (pb < 1) return 'var(--color-positive)';
  if (pb > 3) return 'var(--color-bear-text)';
  return NEUTRAL;
}

export function colorForDivergence(pe_rt: number | null, pe_close: number | null): string | null {
  if (pe_rt == null || pe_close == null || pe_close === 0) return null;
  const diff = Math.abs(pe_rt - pe_close) / pe_close;
  if (diff > 0.05) return BEAR;
  return null;
}

export const FACTOR_COLORS: Record<string, string> = {
  momentum: 'var(--color-momentum)',
  value: 'var(--color-value)',
  quality: 'var(--color-quality)',
  growth: 'var(--color-growth)',
  institutional: 'var(--color-institutional)',
};

export const FACTOR_LABELS: Record<string, string> = {
  momentum: '動能',
  value: '價值',
  quality: '品質',
  growth: '成長',
  institutional: '法人',
};

export const DATASET_LABELS: Record<string, string> = {
  daily_prices: '股價',
  price: '股價',
  valuations: '本益比/淨值比',
  per: '本益比/淨值比',
  monthly_revenue: '月營收',
  revenue: '月營收',
  financials: '財報',
  signals: '訊號',
  backtest_runs: '回測',
  stocks: '股票',
  ingestion_tracker: '追蹤',
};
