import { formatNumber } from '../utils/format';
import { colorForDivergence } from '../utils/color';
import styles from './RealtimeValuationBadge.module.css';

/**
 * T121 - 即時 PE/PB UI 整合
 * 小標籤顯示 RT-PE 與 RT-PB，並根據估值區間動態切換顏色。
 */
export interface RealtimeValuationBadgeProps {
  stockId: string;
  currentPrice: number | null;
  peRt: number | null;
  pbRt: number | null;
  industryAvgPb: number | null;
  dividendYield?: number | null;
  lastClosePe?: number | null;
}

function isMarketOpen(): boolean {
  const now = new Date();
  const h = now.getHours();
  const m = now.getMinutes();
  const t = h * 60 + m;
  return t >= 9 * 60 && t <= 13 * 60 + 30;
}

export default function RealtimeValuationBadge({
  peRt,
  pbRt,
  industryAvgPb,
  dividendYield,
  lastClosePe,
}: RealtimeValuationBadgeProps) {
  const marketOpen = isMarketOpen();

  // 顏色切換邏輯
  // 1. 若 pbRt < 1.0 → 文字顏色 = 綠色 (低估亮點)
  // 2. 若 industryAvgPb !== null && pbRt > industryAvgPb * 2 → 文字顏色 = 黃橘色 (高估警訊)
  let pbColor = 'var(--text-primary)';
  if (pbRt !== null) {
    if (pbRt < 1.0) {
      pbColor = 'var(--color-positive)'; // 綠色
    } else if (industryAvgPb !== null && pbRt > industryAvgPb * 2) {
      pbColor = 'var(--color-neutral)'; // 黃橘色
    }
  }

  // PE 顯示處理 (若 peRt > 200 → 顯示「> 200」)
  const peDisplay = peRt === null ? '—' : peRt > 200 ? '> 200' : formatNumber(peRt, { type: 'ratio', decimals: 1 });
  const pbDisplay = pbRt === null ? '—' : formatNumber(pbRt, { type: 'ratio', decimals: 1 });
  
  const divergenceColor = marketOpen ? colorForDivergence(peRt, lastClosePe ?? null) : null;

  return (
    <div className={styles.badgeRow}>
      {marketOpen && <span className={styles.indicator} />}
      <span className={styles.label}>RT-PE:</span>
      <span className={styles.value}>{peDisplay}</span>
      {divergenceColor && (
        <span className={styles.divWarn} title="即時 PE 與昨收差異 > 5%">⚠</span>
      )}
      <span className={styles.sep}>|</span>
      <span className={styles.label}>RT-PB:</span>
      <span className={styles.value} style={{ color: pbColor }}>
        {pbDisplay}
      </span>
      {dividendYield != null && (
        <>
          <span className={styles.sep}>|</span>
          <span className={styles.label}>殖利率:</span>
          <span className={styles.value}>{formatNumber(dividendYield, { type: 'percent' })}</span>
        </>
      )}
      {industryAvgPb !== null && pbRt !== null && pbRt > industryAvgPb * 2 && (
        <span className={styles.warn} title={`高於同業平均 2 倍 (同業 ${industryAvgPb.toFixed(2)})`}>
          高於同業
        </span>
      )}
    </div>
  );
}
