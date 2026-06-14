import { useMemo } from 'react';
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts';
import type { IntradayKlinePoint } from '../api/client';
import { computeSMA } from '../utils/indicators';
import SkeletonScreen from './SkeletonScreen';
import styles from './IntradayKlineChart.module.css';

interface Props {
  data: IntradayKlinePoint[] | null;
  loading: boolean;
  stockId: string;
  maPeriods?: number[];
}

export default function IntradayKlineChart({ data, loading, stockId, maPeriods = [5, 20, 60] }: Props) {
  const chartData = useMemo(() => {
    if (!data || data.length === 0) return [];
    const closes = data.map(d => d.close ?? 0);
    const mas: Record<number, (number | null)[]> = {};
    for (const p of maPeriods) {
      mas[p] = computeSMA(closes, p);
    }
    return data.map((d, i) => ({
      time: d.k_time.slice(11, 16),
      o: d.open ?? 0,
      h: d.high ?? 0,
      l: d.low ?? 0,
      c: d.close ?? 0,
      v: d.volume,
      ...Object.fromEntries(maPeriods.map(p => [`ma${p}`, mas[p][i]])),
    }));
  }, [data, maPeriods]);

  const domainMin = useMemo(() => {
    if (chartData.length === 0) return 0;
    let min = Infinity;
    for (const d of chartData) {
      if (d.l < min) min = d.l;
    }
    return Math.max(min * 0.998, 0);
  }, [chartData]);

  const domainMax = useMemo(() => {
    if (chartData.length === 0) return 100;
    let max = 0;
    for (const d of chartData) {
      if (d.h > max) max = d.h;
    }
    return max * 1.002;
  }, [chartData]);

  const maColors = ['#f39c12', '#e74c3c', '#9b59b6'];

  if (loading) {
    return <SkeletonScreen loading variant="card" rows={3} width="100%" height={400}><div /></SkeletonScreen>;
  }
  if (!data || data.length === 0) {
    return <div className={styles.empty}>今日尚無 K 線資料（需累積至少 1 根 60 分 K 線）</div>;
  }

  const latest = chartData[chartData.length - 1];

  return (
    <div className={styles.card}>
      <div className={styles.header}>
        <h3>60 分 K 線圖 — {stockId}</h3>
        <span className={styles.hint}>
          收 {latest.c?.toFixed(2)} / 高 {latest.h?.toFixed(2)} / 低 {latest.l?.toFixed(2)} / 開 {latest.o?.toFixed(2)}
        </span>
      </div>
      <ResponsiveContainer width="100%" height={360}>
        <ComposedChart data={chartData} margin={{ top: 10, right: 60, bottom: 10, left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--bg-border)" />
          <XAxis dataKey="time" tick={{ fontSize: 10 }} />
          <YAxis domain={[domainMin, domainMax]} tick={{ fontSize: 10 }} orientation="right" />
          <Tooltip content={<CandleTooltip />} />
          <Bar dataKey="h" shape={<CandleShape domainMin={domainMin} domainMax={domainMax} />} isAnimationActive={false} />
          {maPeriods.map((p, idx) => (
            <Line
              key={p}
              type="monotone"
              dataKey={`ma${p}`}
              stroke={maColors[idx % maColors.length]}
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
          ))}
        </ComposedChart>
      </ResponsiveContainer>
      <div className={styles.legend}>
        {maPeriods.map((p, idx) => (
          <span key={p} className={styles.legendItem}>
            <span className={styles.legendDot} style={{ background: maColors[idx % maColors.length] }} />
            MA({p})
          </span>
        ))}
      </div>
    </div>
  );
}

function CandleShape({ x, y, width, height, payload, domainMin, domainMax }: {
  x?: number; y?: number; width?: number; height?: number; payload?: Record<string, unknown>;
  domainMin: number; domainMax: number;
}) {
  if (!payload || x == null || width == null || y == null || height == null) return null;
  const o = payload.o as number;
  const h = payload.h as number;
  const l = payload.l as number;
  const c = payload.c as number;
  if (o == null || h == null || l == null || c == null) return null;
  if (domainMin >= domainMax || h <= domainMin) return null;

  const isUp = c >= o;
  const color = isUp ? '#e74c3c' : '#27ae60';

  // pxPerValue: pixel per unit in the visible [domainMin, domainMax] range
  // The Bar's height and y are computed from the visible portion only
  const visibleValueRange = h - domainMin;
  const pxPerValue = visibleValueRange > 0 ? height / visibleValueRange : 0;

  const cx = x + width / 2;
  const bw = Math.max(width * 0.6, 1);

  // Map a value to its pixel y-coordinate (origin at top-left)
  const p = (v: number) => y + (h - v) * pxPerValue;

  const bodyTop = Math.max(o, c);
  const bodyBot = Math.min(o, c);

  return (
    <g>
      <line x1={cx} y1={p(h)} x2={cx} y2={p(l)} stroke={color} strokeWidth={1} />
      <rect
        x={cx - bw / 2}
        y={p(bodyTop)}
        width={bw}
        height={Math.max(p(bodyBot) - p(bodyTop), 1)}
        fill={color}
      />
    </g>
  );
}

function CandleTooltip({ active, payload, label }: {
  active?: boolean; payload?: { payload: Record<string, unknown> }[]; label?: string;
}) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className={styles.tooltip}>
      <div className={styles.tooltipTime}>{label}</div>
      <div className={styles.tooltipRow}>開: {Number(d.o).toFixed(2)}</div>
      <div className={styles.tooltipRow}>高: {Number(d.h).toFixed(2)}</div>
      <div className={styles.tooltipRow}>低: {Number(d.l).toFixed(2)}</div>
      <div className={styles.tooltipRow}>收: {Number(d.c).toFixed(2)}</div>
      {d.v != null && <div className={styles.tooltipRow}>量: {Number(d.v).toLocaleString()}</div>}
    </div>
  );
}
