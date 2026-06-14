import { formatNumber } from '../utils/format';
import styles from './FactorMiniBar.module.css';

const FACTOR_COLORS: Record<string, string> = {
  momentum: '#a78bfa',
  value: '#34d399',
  quality: '#f59e0b',
  growth: '#38bdf8',
  institutional: '#f472b6',
};

const FACTOR_LABELS: Record<string, string> = {
  momentum: '動能',
  value: '價值',
  quality: '品質',
  growth: '成長',
  institutional: '法人',
};

interface FactorMiniBarProps {
  name: string;
  score: number;
  showLabels?: boolean;
}

export default function FactorMiniBar({ name, score, showLabels }: FactorMiniBarProps) {
  const pct = Math.max(0, Math.min(100, ((score + 3) / 6) * 100));
  const extreme = score > 2;
  const color = FACTOR_COLORS[name] || '#666';

  const label = FACTOR_LABELS[name] || name;
  return (
    <div
      className={`${styles.bar} ${showLabels ? styles.withLabels : ''} font-data`}
      role="img"
      aria-label={`${label} 因子：分數 ${formatNumber(score, { type: 'score' })}，百分位 ${pct.toFixed(1)}%，趨勢 ${score > 0 ? '上升' : '下降'}`}
    >
      <div
        className={`${styles.fill} ${extreme ? styles.extreme : ''}`}
        style={{ width: `${pct}%`, backgroundColor: color }}
        aria-hidden="true"
      />
      {showLabels && (
        <div className={styles.labels}>
          <span className={styles.scoreVal} style={{ color }}>{formatNumber(score, { type: 'score' })}</span>
          <span className={styles.pctVal}>{pct.toFixed(0)}%</span>
        </div>
      )}
    </div>
  );
}
