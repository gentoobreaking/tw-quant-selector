import { useState, useEffect, useMemo } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { fetchStockDetail, fetchFactorHistory, fetchIntradaySnapshots, fetchIntradayKline, fetchLatestValuations, type IntradayKlinePoint } from '../api/client';
import type { FactorHistoryPoint, LatestValuation } from '../api/client';
import FactorMiniBar from '../components/FactorMiniBar';
import IntradayKlineChart from '../components/IntradayKlineChart';
import SkeletonLoader from '../components/SkeletonLoader';
import EmptyState from '../components/EmptyState';
import { formatNumber, colorize } from '../utils/format';
import { colorForChange } from '../utils/color';
import RealtimeValuationBadge from '../components/RealtimeValuationBadge';
import { calc_industry_avg_pb } from '../utils/industryAverages';
import styles from './StockDetail.module.css';

interface StockInfo {
  stock_id: string; name: string; market: string; is_etf: boolean; industry: string | null;
}
interface PricePoint { d: string; o: number | null; h: number | null; l: number | null; c: number | null; v: number | null; }
interface ValPoint { d: string; pe: number | null; pb: number | null; dy: number | null; }
interface FinPoint { yq: string; rev: number | null; eps: number | null; roe: number | null; gm: number | null; de: number | null; }
interface RevPoint { ym: string; rev: number | null; yoy: number | null; }
interface IntradayPoint { snapshot_time: string; price: number | null; volume: number; }

interface RealtimeValuationData {
  price: number | null;
  pe: number | null;
  pb: number | null;
  dividend_yield: number | null;
  ttm_eps: number | null;
  bvps: number | null;
  data_as_of: string | null;
  pe_detail: string | null;
  pb_detail: string | null;
  last_close_pe?: number | null;
  last_close_pb?: number | null;
}

export default function StockDetail() {
  const { id } = useParams<{ id: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState<{
    info: StockInfo; prices: PricePoint[]; valuations: ValPoint[];
    financials: FinPoint[]; revenue: RevPoint[]; factor_scores: Record<string, number> | null;
    realtime_valuation: RealtimeValuationData | null;
  } | null>(null);
  const [factorHistory, setFactorHistory] = useState<FactorHistoryPoint[] | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState(false);
  const [intradayData, setIntradayData] = useState<IntradayPoint[] | null>(null);
  const [intradayLoading, setIntradayLoading] = useState(false);
  const [klineData, setKlineData] = useState<IntradayKlinePoint[] | null>(null);
  const [klineLoading, setKlineLoading] = useState(false);
  const [allValuations, setAllValuations] = useState<LatestValuation[] | null>(null);
  
  const validTabs = ['factors', 'financials', 'history', 'intraday', 'kline'] as const;
  const rawTab = searchParams.get('tab');
  const initialTab = validTabs.includes(rawTab as typeof validTabs[number]) ? rawTab as 'factors' | 'financials' | 'history' | 'intraday' | 'kline' : 'factors';
  const [tab, setTab] = useState<'factors' | 'financials' | 'history' | 'intraday' | 'kline'>(initialTab);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    setHistoryLoading(true);
    setHistoryError(false);
    fetchStockDetail(id).then((d: unknown) => { setData(d as typeof data); setLoading(false); }).catch(() => setLoading(false));
    fetchFactorHistory(id).then((h) => { setFactorHistory(h); setHistoryLoading(false); }).catch(() => { setHistoryError(true); setHistoryLoading(false); });
    
    // Fetch latest valuations for industry average calculation
    fetchLatestValuations().then(v => setAllValuations(v)).catch(() => setAllValuations(null));
  }, [id]);

  useEffect(() => {
    if (!id || tab !== 'intraday') return;
    setIntradayLoading(true);
    fetchIntradaySnapshots(id)
      .then(data => { setIntradayData(data); setIntradayLoading(false); })
      .catch(() => { setIntradayData(null); setIntradayLoading(false); });
  }, [id, tab]);

  useEffect(() => {
    if (!id || tab !== 'kline') return;
    setKlineLoading(true);
    fetchIntradayKline(id, 60, 1)
      .then(data => { setKlineData(data); setKlineLoading(false); })
      .catch(() => { setKlineData(null); setKlineLoading(false); });
  }, [id, tab]);

  useEffect(() => {
    setSearchParams(prev => { prev.set('tab', tab); return prev; }, { replace: true });
  }, [tab]);

  const industryAvgPb = useMemo(() => {
    if (!id || !allValuations) return null;
    return calc_industry_avg_pb(id, allValuations);
  }, [id, allValuations]);

  if (loading) {
    return <div className={styles.page}><SkeletonLoader variant="card" /><SkeletonLoader variant="table" rows={3} /></div>;
  }

  if (!data) {
    return <div className={styles.page}><EmptyState scenario="notrade">查無此股票資料</EmptyState></div>;
  }

  const { info, prices, valuations, financials, revenue, factor_scores, realtime_valuation } = data;
  const lastPrice = prices[0]?.c;

  return (
    <div className={styles.page}>
      {/* Header */}
      <div className={styles.header}>
        <div>
          <span className={styles.stockId}>{info.stock_id}</span>
          <span className={styles.stockName}>{info.name}</span>
          <span className={`${styles.badge} ${info.is_etf ? styles.etfBadge : styles.stockBadge}`}>
            {info.is_etf ? 'ETF' : '股票'}
          </span>
          <span className={styles.meta}>{info.market} {info.industry || ''}</span>
        </div>
        <div className={styles.priceSection}>
          <span className={styles.price}>{formatNumber(lastPrice, { type: 'price' })}</span>
          <span className={styles.change} style={{ color: colorForChange(0) }}>▲—</span>
          <div className={styles.rtBadgeWrap}>
            <RealtimeValuationBadge 
              stockId={info.stock_id}
              currentPrice={lastPrice ?? 0}
              peRt={realtime_valuation?.pe ?? null}
              pbRt={realtime_valuation?.pb ?? null}
              industryAvgPb={industryAvgPb}
              dividendYield={realtime_valuation?.dividend_yield}
              lastClosePe={realtime_valuation?.last_close_pe}
            />
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className={styles.tabs}>
          {(['factors', 'financials', 'history', 'intraday', 'kline'] as const).map((t) => (
          <button key={t} className={`${styles.tab} ${tab === t ? styles.activeTab : ''}`} onClick={() => setTab(t)}>
            {t === 'factors' ? '因子分析' : t === 'financials' ? '財務摘要' : t === 'history' ? '歷史入選紀錄' : t === 'intraday' ? '即時走勢' : 'K 線圖'}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {tab === 'factors' && (
        <div className={styles.tabContent}>
          {historyError ? (
            <EmptyState scenario="failed">無法載入因子歷史資料</EmptyState>
          ) : historyLoading ? (
            <div className={styles.factorGrid}>
              {[1, 2, 3, 4, 5, 6].map((i) => (
                <div key={i} className={styles.sparkCard}>
                  <SkeletonLoader variant="chart" />
                </div>
              ))}
            </div>
          ) : !factorHistory || factorHistory.length === 0 ? (
            <EmptyState scenario="notrade">尚無因子歷史資料</EmptyState>
          ) : (
            <FactorSparklines history={factorHistory} scores={factor_scores ?? {}} />
          )}
        </div>
      )}

      {tab === 'financials' && (
        <div className={styles.tabContent}>
          <div className={styles.finGrid}>
            <div className={styles.finPanel}>
              <h3>本益比/淨值比 PE/PB</h3>
              <table className={styles.finTable}>
                <thead><tr><th>日期</th><th>PE</th><th>PB</th><th>殖利率</th></tr></thead>
                <tbody>
                  {valuations.length === 0 ? (
                    <tr><td colSpan={4} className={styles.emptyCell}>尚無本益比資料，資料排程 ingesting 中</td></tr>
                  ) : valuations.slice(0, 8).map((v) => (
                    <tr key={v.d}><td>{v.d}</td><td className="font-data">{v.pe ?? '—'}</td><td className="font-data">{v.pb ?? '—'}</td>
                      <td className="font-data">{formatNumber(v.dy, { type: 'percent' })}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className={styles.finPanel}>
              <h3>財報 Financials</h3>
              <table className={styles.finTable}>
                <thead><tr><th>季度</th><th>營收</th><th>EPS</th><th>ROE</th><th>毛利率</th><th>負債比</th></tr></thead>
                <tbody>
                  {financials.length === 0 ? (
                    <tr><td colSpan={6} className={styles.emptyCell}>尚無財報資料，資料排程 ingesting 中</td></tr>
                  ) : financials.slice(0, 8).map((f) => (
                    <tr key={f.yq}>
                      <td>{f.yq}</td>
                      <td className="font-data">{formatNumber(f.rev, { type: 'market_cap' })}</td>
                      <td className="font-data">{f.eps ?? '—'}</td>
                      <td className="font-data">{formatNumber(f.roe, { type: 'percent' })}</td>
                      <td className="font-data">{formatNumber(f.gm, { type: 'percent' })}</td>
                      <td className="font-data">{formatNumber(f.de, { type: 'ratio', decimals: 2 })}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className={styles.finPanel}>
              <h3>月營收 Monthly Revenue</h3>
              <table className={styles.finTable}>
                <thead><tr><th>月份</th><th>營收</th><th>年增率 YoY</th></tr></thead>
                <tbody>
                  {revenue.length === 0 ? (
                    <tr><td colSpan={3} className={styles.emptyCell}>尚無月營收資料，資料排程 ingesting 中</td></tr>
                  ) : revenue.slice(0, 12).map((r) => (
                    <tr key={r.ym}><td>{r.ym}</td>
                      <td className="font-data">{formatNumber(r.rev, { type: 'market_cap' })}</td>
                      <td className={`font-data ${r.yoy != null ? colorize(r.yoy, 'percent').className : ''}`}>
                        {formatNumber(r.yoy, { type: 'percent' })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {tab === 'intraday' && (
        <div className={styles.tabContent}>
          <IntradayChart data={intradayData} loading={intradayLoading} stockId={id ?? ''} />
        </div>
      )}

      {tab === 'kline' && (
        <div className={styles.tabContent}>
          <IntradayKlineChart data={klineData} loading={klineLoading} stockId={id ?? ''} />
        </div>
      )}

      {tab === 'history' && (
        <div className={styles.tabContent}>
          <div className={styles.finPanel}>
            <h3>歷史入選紀錄</h3>
            <table className={styles.finTable}>
              <thead><tr><th>入選日期</th><th>排名</th><th>持有期間</th><th>期間報酬</th></tr></thead>
              <tbody>
                <tr><td colSpan={4} className={styles.emptyCell}>暫無歷史入選紀錄，待選股訊號累積後自動產生</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function FactorSparklines({ history, scores }: { history: FactorHistoryPoint[]; scores: Record<string, number> }) {
  const factors = ['momentum', 'value', 'quality', 'growth', 'guru', 'institutional'] as const;
  const labels: Record<string, string> = { 
    momentum: '動能', value: '價值', quality: '品質', 
    growth: '成長', guru: '大師', institutional: '法人' 
  };

  const series = useMemo(() => {
    return factors.map((f) => {
      const vals: number[] = [];
      for (let i = history.length - 1; i >= 0; i--) {
        const v = history[i][f];
        if (v != null) vals.push(v);
      }
      return { key: f, values: vals };
    });
  }, [history]);

  return (
    <div className={styles.factorGrid}>
      {series.map(({ key, values }) => {
        const score = scores[key] ?? 0;
        const { path, fillPath, dotY, pathLen } = buildSparklinePath(values);
        const gradId = `grad-${key}`;
        const hasData = values.length >= 2;
        return (
          <div key={key} className={styles.sparkCard}>
            <div className={styles.sparkHeader}>
              <span style={{ color: `var(--color-${key})`, fontWeight: 600 }}>
                {labels[key]}
              </span>
              <FactorMiniBar name={key} score={score} showLabels />
            </div>
            <div className={styles.sparkline}>
              {hasData ? (
                <svg width="100%" height="80" viewBox="0 0 300 80" preserveAspectRatio="none">
                  <defs>
                    <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={`var(--color-${key})`} stopOpacity="0.3" />
                      <stop offset="100%" stopColor={`var(--color-${key})`} stopOpacity="0" />
                    </linearGradient>
                  </defs>
                  <line x1="0" y1="40" x2="300" y2="40" stroke="var(--bg-border)" strokeWidth="1" strokeDasharray="4 2" />
                  {fillPath && <path d={fillPath} fill={`url(#${gradId})`} />}
                  {path && <path d={path} fill="none" stroke={`var(--color-${key})`} strokeWidth="1.5" className="sparkline-path" style={{ '--path-len': pathLen } as React.CSSProperties} />}
                  {dotY != null && <circle cx="300" cy={dotY} r="3" fill={`var(--color-${key})`} />}
                </svg>
              ) : (
                <div className={styles.sparklineEmpty}>尚無資料</div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function buildSparklinePath(values: number[]) {
  if (values.length < 2) return { path: '', fillPath: '', dotY: null, pathLen: 0 };

  const w = 300, h = 80, pad = 4;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const n = values.length;

  const pts = values.map((v, i) => {
    const x = (i / (n - 1)) * w;
    const y = h - pad - ((v - min) / range) * (h - 2 * pad);
    return [x, y] as [number, number];
  });

  const line = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('');
  const fill = `M${pts[0][0].toFixed(1)},${h}L${pts.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('L')}L${pts[n - 1][0].toFixed(1)},${h}Z`;

  return { path: line, fillPath: fill, dotY: pts[n - 1][1], pathLen: Math.round(w * 1.5) };
}

function IntradayChart({ data, loading, stockId }: { data: IntradayPoint[] | null; loading: boolean; stockId: string }) {
  if (loading) {
    return <div className={styles.intradayPlaceholder}>載入中...</div>;
  }
  if (!data || data.length === 0) {
    return <div className={styles.intradayPlaceholder}>今日尚無即時報價資料</div>;
  }

  const chartData = data.map(p => ({
    time: p.snapshot_time.slice(11, 19),
    price: p.price,
    volume: p.volume,
  }));

  const firstPrice = chartData[0]?.price ?? 0;
  const lastPrice = chartData[chartData.length - 1]?.price ?? 0;
  const change = lastPrice - firstPrice;
  const color = colorForChange(change);

  return (
    <div className={styles.intradayCard}>
      <div className={styles.intradayHeader}>
        <h3>今日即時走勢 — {stockId}</h3>
        <span className={styles.intradayPrice} style={{ color }}>
          {formatNumber(lastPrice, { type: 'price' })}
          <span className={styles.intradayChange}>
            {formatNumber(change / lastPrice, { type: 'percent' })}
          </span>
        </span>
      </div>
      <ResponsiveContainer width="100%" height={300}>
        <AreaChart data={chartData}>
          <defs>
            <linearGradient id="intradayGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.3} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis dataKey="time" tick={{ fontSize: 11 }} />
          <YAxis domain={['dataMin - 1', 'dataMax + 1']} tick={{ fontSize: 11 }} />
          <Tooltip />
          <Area type="monotone" dataKey="price" stroke={color} strokeWidth={2} fill="url(#intradayGrad)" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
