export function formatPrice(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  // 台股股價格式：<10 三位小數、>=10 兩位小數 (確保千元以上也支援小數點，如台積電)
  if (v < 10) return v.toFixed(3);
  return v.toFixed(2);
}

export function formatReturn(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  const sign = v >= 0 ? '+' : '';
  return `${sign}${(v * 100).toFixed(2)}%`;
}

export function formatFactorScore(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return v.toFixed(2);
}

export function formatSharpe(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return v.toFixed(2);
}

export function formatPercentile(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `${(v * 100).toFixed(1)}%`;
}

export function formatMarketCap(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  const zhao = v / 1_000_000_000_000;
  if (zhao >= 1) return `${zhao.toFixed(2)}兆`;
  const yi = v / 100_000_000;
  return `${yi.toFixed(1)}億`;
}

export function formatVolume(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  const wanZhang = v / 10000;
  return `${wanZhang.toFixed(1)}萬張`;
}

export function formatMoney(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `$${v.toLocaleString()}`;
}

// ============================================
// T050 新增功能（原有函式完全不動，只追加以下）
// ============================================

export type NumberFormatType = 'price' | 'percent' | 'score' | 'market_cap' | 'volume' | 'ratio' | 'days' | 'money';

export interface FormatNumberOptions {
  type: NumberFormatType;
  compact?: boolean;
  decimals?: number;
  loading?: boolean;
}

export interface ColorizeResult {
  text: string;
  className: string;
  ariaLabel: string;
}

/**
 * 統一數字格式化函式
 * 內部呼叫上方原有函式，保持結果一致
 */
export function formatNumber(
  value: number | null | undefined,
  opts: FormatNumberOptions
): string {
  if (opts.loading) return '—';
  if (value == null) return '—';
  if (Number.isNaN(value)) return '—';
  if (!Number.isFinite(value)) return '∞';
  if (Object.is(value, -0)) value = 0;

  // 呼叫對應的原有函式
  switch (opts.type) {
    case 'price':
      return formatPrice(value);
    case 'percent':
      return formatReturn(value);
    case 'score':
      return formatFactorScore(value);
    case 'market_cap':
      return formatMarketCap(value);
    case 'volume':
      return formatVolume(value);
    case 'ratio':
      return formatRatio(value, opts);
    case 'days':
      return formatDays(value, opts);
    case 'money':
      return `$${value.toLocaleString(undefined, { 
        minimumFractionDigits: 2, 
        maximumFractionDigits: 2 
      })}`;
    default:
      return String(value);
  }
}

function formatRatio(v: number, opts: FormatNumberOptions): string {
  const decimals = opts.decimals ?? 2;
  return v.toFixed(decimals);
}

function formatDays(v: number, _opts: FormatNumberOptions): string {
  return `${Math.round(v)} 天`;
}

/**
 * 數字顏色化
 * 回傳 {text, className, ariaLabel}
 */
export function colorize(
  value: number | null | undefined,
  type: NumberFormatType
): ColorizeResult {
  if (value == null || Number.isNaN(value)) {
    return { text: '—', className: 'text-muted', ariaLabel: '資料缺失' };
  }
  if (!Number.isFinite(value)) {
    return { text: '∞', className: 'text-warning', ariaLabel: '無限值' };
  }
  if (Object.is(value, -0)) value = 0;

  const formatted = formatNumber(value, { type, compact: false });

  let className: string;
  let ariaLabel: string;

  if (value > 0) {
    className = 'text-positive';
    ariaLabel = `正 ${formatted}`;
  } else if (value < 0) {
    className = 'text-negative';
    ariaLabel = `負 ${formatted}`;
  } else {
    className = 'text-neutral';
    ariaLabel = `零 ${formatted}`;
  }

  if (type === 'score') {
    if (value >= 80) className = 'text-excellent';
    else if (value >= 60) className = 'text-good';
    else if (value >= 40) className = 'text-neutral';
    else className = 'text-poor';
  }

  return { text: formatted, className, ariaLabel };
}

export type DateFormatType = 'full' | 'compact' | 'relative';

export function formatDate(
  date: Date | string | null | undefined,
  type: DateFormatType = 'full'
): string {
  if (!date) return '—';
  const d = typeof date === 'string' ? new Date(date) : date;
  if (isNaN(d.getTime())) return '—';

  switch (type) {
    case 'full': {
      const weekdays = ['日', '一', '二', '三', '四', '五', '六'];
      const weekday = weekdays[d.getDay()];
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} (${weekday})`;
    }
    case 'compact':
      return `${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}`;
    case 'relative':
      return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    default:
      return d.toISOString();
  }
}

export type MarketStatus = 'trading' | 'pre_market' | 'post_market' | 'closed' | 'holiday';

export interface MarketStatusInfo {
  status: MarketStatus;
  label: string;
  className: string;
  lastUpdated: string;
  nextOpen: string;
}

function nextMarketOpen(now: Date): string {
  const day = now.getDay();
  const hour = now.getHours();
  const minute = now.getMinutes();
  const currentTime = hour * 60 + minute;
  const marketOpen = 9 * 60;
  const marketClose = 13 * 60 + 30;

  if (day === 0 || day === 6 || currentTime >= marketClose) {
    let daysUntil = 1;
    if (day === 6) daysUntil = 2;
    else if (day === 5 && currentTime >= marketClose) daysUntil = 3;
    const next = new Date(now);
    next.setDate(next.getDate() + daysUntil);
    next.setHours(9, 0, 0, 0);
    return formatDate(next, 'relative');
  }
  if (currentTime < marketOpen) {
    const open = new Date(now);
    open.setHours(9, 0, 0, 0);
    return formatDate(open, 'relative');
  }
  const close = new Date(now);
  close.setHours(13, 30, 0, 0);
  return formatDate(close, 'relative');
}

export function getMarketStatus(now: Date = new Date()): MarketStatusInfo {
  const hour = now.getHours();
  const minute = now.getMinutes();
  const day = now.getDay();
  const base = { lastUpdated: formatDate(now, 'relative'), nextOpen: nextMarketOpen(now) };

  if (day === 0 || day === 6) {
    return { status: 'holiday', label: '休市中', className: 'market-closed', ...base };
  }

  const currentTime = hour * 60 + minute;
  const marketOpen = 9 * 60;
  const marketClose = 13 * 60 + 30;

  if (currentTime >= marketOpen && currentTime <= marketClose) {
    return { status: 'trading', label: '交易中', className: 'market-trading', ...base };
  }

  if (currentTime >= marketClose) {
    return { status: 'post_market', label: '收盤中', className: 'market-closed', ...base };
  }

  return { status: 'pre_market', label: '已收盤', className: 'market-closed', ...base };
}
