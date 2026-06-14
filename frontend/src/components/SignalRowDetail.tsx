import { useEffect, useState } from 'react';
import { fetchStockDetail } from '../api/client';
import { formatNumber } from '../utils/format';
import { FACTOR_LABELS } from '../utils/color';
import FactorMiniBar from './FactorMiniBar';
import styles from './SignalRowDetail.module.css';

interface SignalRowDetailProps {
  stockId: string;
}

interface PricePoint {
  c: number;
}

interface StockDetailData {
  prices?: PricePoint[];
  factor_scores?: Record<string, number> | null;
}

export default function SignalRowDetail({ stockId }: SignalRowDetailProps) {
  const [data, setData] = useState<StockDetailData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchStockDetail(stockId)
      .then((d: unknown) => {
        setData(d as StockDetailData);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [stockId]);

  if (loading) return <div className={styles.loading}>載入圖表中...</div>;
  if (!data || !data.prices || data.prices.length === 0) return <div className={styles.empty}>暫無圖表資料</div>;

  const prices = data.prices.slice(0, 30).reverse();
  const minPrice = Math.min(...prices.map((p: PricePoint) => p.c));
  const maxPrice = Math.max(...prices.map((p: PricePoint) => p.c));
  const range = maxPrice - minPrice || 1;

  const pricePath = prices.map((p: PricePoint, i: number) => {
    const x = (i / (prices.length - 1)) * 300;
    const y = 80 - ((p.c - minPrice) / range) * 60 - 10;
    return `${i === 0 ? 'M' : 'L'}${x},${y}`;
  }).join(' ');

  return (
    <div className={styles.container}>
      <div className={styles.chartSection}>
        <div className={styles.chartHeader}>
          <span className={styles.chartTitle}>近 30 日股價走勢</span>
          <span className={styles.priceRange}>
            Low: {formatNumber(minPrice, { type: 'price' })} - High: {formatNumber(maxPrice, { type: 'price' })}
          </span>
        </div>
        <div className={styles.svgWrapper}>
          <svg viewBox="0 0 300 80" className={styles.svg}>
            <path d={pricePath} fill="none" stroke="var(--color-bull)" strokeWidth="2" />
            {/* Area under curve */}
            <path d={`${pricePath} L300,80 L0,80 Z`} fill="var(--color-bull)" fillOpacity="0.1" />
          </svg>
        </div>
      </div>

      <div className={styles.factorSection}>
        <div className={styles.chartHeader}>
          <span className={styles.chartTitle}>因子百分位數趨勢</span>
        </div>
        <div className={styles.factorGrid}>
          {['momentum', 'value', 'quality', 'growth', 'institutional'].map((f) => {
             const score = data.factor_scores?.[f] ?? null;
             return (
              <div key={f} className={styles.factorItem}>
                <span className={styles.factorName} style={{ color: `var(--color-${f})` }}>
                  {FACTOR_LABELS[f] || f}
                </span>
                {score != null ? (
                  <FactorMiniBar name={f} score={score} showLabels />
                ) : (
                  <span className={styles.noData}>—</span>
                )}
              </div>
             );
          })}
        </div>
        <p className={styles.hint}>※ 目前僅顯示當前因子百分位數，歷史趨勢將隨每日更新自動累積</p>
      </div>
    </div>
  );
}
