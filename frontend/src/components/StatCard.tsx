import { useEffect, useRef, useState } from 'react';
import { animateNumber } from '../utils/animate';
import {
  formatNumber,
  colorize,
  type NumberFormatType,
  type FormatNumberOptions
} from '../utils/format';
import styles from './StatCard.module.css';

interface StatCardProps {
  label: string;
  value: string | number;
  delta?: number;
  deltaLabel?: string;
  format?: 'number' | 'percent' | 'currency' | 'raw' | NumberFormatType;
  formatOpts?: FormatNumberOptions;
  variant?: 'default' | 'highlight' | 'alert';
  loading?: boolean;
  error?: boolean;
}

// T050: 改用統一 formatNumber / colorize
function formatValue(v: string | number, fmt: StatCardProps['format'], opts?: FormatNumberOptions): string {
  // 支援新的 NumberFormatType
  if (fmt && ['price', 'percent', 'score', 'market_cap', 'volume', 'ratio', 'days'].includes(fmt as string)) {
    if (typeof v === 'number') {
      return formatNumber(v, { type: fmt as NumberFormatType, ...(opts || {}) });
    }
    return formatNumber(null, { type: fmt as NumberFormatType });
  }
  
  // 舊版相容
  if (fmt === 'percent' && typeof v === 'number') {
    const sign = v >= 0 ? '+' : '';
    return `${sign}${(v * 100).toFixed(2)}%`;
  }
  if (fmt === 'currency' && typeof v === 'number') {
    return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2 })}`;
  }
  if (typeof v === 'number') return v.toLocaleString();
  return String(v);
}

function deltaColorClass(delta: number | undefined): string {
  if (delta == null) return '';
  if (delta > 0) return styles.bull;
  if (delta < 0) return styles.bear;
  return styles.neutral;
}

function deltaIcon(delta: number | undefined): string {
  if (delta == null) return '';
  return delta > 0 ? '▲' : delta < 0 ? '▼' : '';
}

export default function StatCard({
  label, value, delta, deltaLabel, format = 'number',
  formatOpts,
  variant = 'default', loading = false, error = false,
}: StatCardProps) {
  if (loading) {
    return (
      <div className={`${styles.card} ${styles[variant]}`}>
        <div className={styles.skelLabel} />
        <div className={styles.skelValue} />
        <div className={styles.skelDelta} />
      </div>
    );
  }

  // T050: 使用 colorize 取得 className
  const colorizeResult = typeof value === 'number' && !error
    ? colorize(value, (format as NumberFormatType) || 'score')
    : null;
  
  const formatted = error ? '—' : formatValue(value, format, formatOpts);
  const errorStyle = error ? styles.error : '';
  const valueClassName = colorizeResult ? `${styles.value} font-data ${colorizeResult.className}` : `${styles.value} font-data`;
  const valueRef = useRef<HTMLDivElement>(null);
  const prevValue = useRef(value);
  const [flashing, setFlashing] = useState(false);

  useEffect(() => {
    if (loading || error || value === prevValue.current) return;
    const el = valueRef.current;
    if (!el) return;
    const fmt = (v: number) => formatValue(v, format, formatOpts);
    const isNumeric = typeof value === 'number' && typeof prevValue.current === 'number';
    const cancel = isNumeric
      ? animateNumber(el, prevValue.current as number, value as number, 400, fmt)
      : (() => { el.textContent = formatted; return () => {}; })();
    prevValue.current = value;
    setFlashing(true);
    const flashTimer = setTimeout(() => setFlashing(false), 500);
    return () => { cancel(); clearTimeout(flashTimer); };
  }, [value, format, formatOpts, loading, error, formatted]);

  const ariaDesc = delta != null
    ? `${label}: ${formatted}（${deltaIcon(delta)} ${Math.abs(delta * 100).toFixed(2)}%）`
    : `${label}: ${formatted}${colorizeResult ? '，' + colorizeResult.ariaLabel : ''}`;
  
  return (
    <div className={`${styles.card} ${styles[variant]} ${errorStyle}`} role="figure" aria-label={ariaDesc}>
      <div className={styles.label}>{label}</div>
      <div ref={valueRef} className={`${valueClassName} ${flashing ? 'value-update' : ''}`} aria-hidden="true">{formatted}</div>
      {delta != null && (
        <div className={`${styles.delta} ${deltaColorClass(delta)}`} aria-hidden="true">
          {deltaIcon(delta)} {deltaLabel ? `${formatValue(Math.abs(delta), 'percent', formatOpts)} ${deltaLabel}` : formatValue(delta, 'percent', formatOpts)}
        </div>
      )}
    </div>
  );
}
