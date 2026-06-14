import { useEffect, useState, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchLatestSignals, fetchDataStatus, type DataStatus } from '../api/client';
import { usePageCache } from '../hooks/usePageCache';
import { useWebSocket } from '../hooks/useWebSocket';
import type { QuoteUpdate } from '../hooks/useWebSocket';
import BaseTable from '../components/BaseTable';
import StatCard from '../components/StatCard';
import FactorMiniBar from '../components/FactorMiniBar';
import ErrorBoundary from '../components/ErrorBoundary';
import { useToast } from '../components/Toast';
import WebSocketStatus from '../components/WebSocketStatus';
import RealtimeValuationBadge from '../components/RealtimeValuationBadge';
import { formatNumber } from '../utils/format';
import { colorForChange, trendIcon, FACTOR_LABELS, DATASET_LABELS } from '../utils/color';
import MarketStatus from '../components/MarketStatus';
import SignalRowDetail from '../components/SignalRowDetail';
import type { ColumnDef } from '@tanstack/react-table';
import styles from './Dashboard.module.css';

interface SignalItem {
  stock_id: string;
  name?: string;
  score: number;
  rank: number;
  rank_change?: number | null;
  consecutive_days?: number | null;
  factor_scores?: Record<string, number> | null;
  close_price?: number | null;
  change?: number | null;
  change_pct?: number | null;
  pe?: number | null;  // T100: static PE from valuations table
  pb?: number | null;  // T100: static PB from valuations table
}

interface SignalsData {
  date: string;
  stocks: SignalItem[];
  etfs: SignalItem[];
}

export default function Dashboard() {
  const navigate = useNavigate();
  const [signals, setSignals] = useState<SignalsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dataStatus, setDataStatus] = useState<DataStatus | null>(null);
  const { addToast } = useToast();

  const { getCached, setCached } = usePageCache<SignalsData>('dashboard');
  const [stale, setStale] = useState(false);
  const [liveQuotes, setLiveQuotes] = useState<Record<string, { price: number; change_pct: number; pe_realtime: number | null; pb_realtime: number | null; volume: number }>>({});

  const { status: wsStatus } = useWebSocket({
    enabled: true,
    onMessage: (update: QuoteUpdate) => {
      setLiveQuotes((prev) => ({ ...prev, ...Object.fromEntries(
        Object.entries(update.data).map(([sid, q]) => [sid, { price: q.price, change_pct: q.change_pct, pe_realtime: q.pe_realtime, pb_realtime: q.pb_realtime, volume: q.volume }])
      ) }));
    },
  });

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setStale(false);
    try {
      const s = await fetchLatestSignals('composite', true);
      if (s) {
        setSignals(s as unknown as SignalsData);
        setCached(s as unknown as SignalsData);
      } else {
        const cached = getCached();
        if (cached) { setSignals(cached); setStale(true); }
        else setSignals(null);
      }
    } catch (e: unknown) {
      const cached = getCached();
      if (cached) { setSignals(cached); setStale(true); setError('無法更新，顯示快取資料'); addToast('無法更新，顯示快取資料', 'high'); }
      else { setError(e instanceof Error ? e.message : 'API 錯誤'); addToast(`載入失敗: ${e instanceof Error ? e.message : 'API 錯誤'}`, 'high'); }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { fetchDataStatus().then(setDataStatus).catch(() => {}); }, []);

  const today = new Date();
  const weekday = ['日', '一', '二', '三', '四', '五', '六'][today.getDay()];

  const allItems = [...(signals?.stocks || []), ...(signals?.etfs || [])];
  const etfIds = new Set(signals?.etfs?.map((e) => e.stock_id) || []);
  const displayData = allItems;

  // T118/T119 即時行情統計
  const upCount = useMemo(() => Object.values(liveQuotes).filter(q => q.change_pct > 0).length, [liveQuotes]);
  const downCount = useMemo(() => Object.values(liveQuotes).filter(q => q.change_pct < 0).length, [liveQuotes]);
  const flatCount = useMemo(() => Object.values(liveQuotes).filter(q => q.change_pct === 0).length, [liveQuotes]);
  const avgPE = useMemo(() => {
    const pes = Object.values(liveQuotes).map(q => q.pe_realtime).filter((v): v is number => v != null && v > 0);
    return pes.length > 0 ? parseFloat((pes.reduce((a, b) => a + b, 0) / pes.length).toFixed(1)) : null;
  }, [liveQuotes]);
  const hasLiveData = Object.keys(liveQuotes).length > 0;

  const columns: ColumnDef<SignalItem, any>[] = [
    {
      id: 'rank',
      header: '排名',
      accessorKey: 'rank',
      meta: { width: 48, align: 'right' as const },
      cell: ({ getValue }) => <span className={styles.rankCell}>#{getValue<number>()}</span>,
    },
    {
      id: 'stock_id',
      header: '股票',
      accessorKey: 'stock_id',
      meta: { width: 160 },
      cell: ({ row }) => (
        <span className={styles.stockLink} onClick={(e) => { e.stopPropagation(); navigate(`/signals/${row.original.stock_id}`); }}>
          {row.original.stock_id} {row.original.name || ''}
        </span>
      ),
    },
    {
      id: 'close_price',
      header: '收盤價',
      accessorKey: 'close_price',
      meta: { width: 88, align: 'right' as const },
      cell: ({ getValue }) => {
        const v = getValue<number | null>();
        return v != null ? <span className="font-data">{formatNumber(v, { type: 'price' })}</span> : <span className="font-data">—</span>;
      },
    },
    {
      id: 'change',
      header: '今日漲跌',
      accessorFn: (row: SignalItem) => row.change ?? 0,
      meta: { width: 80, align: 'right' as const },
      cell: ({ row }) => {
        const c = row.original.change;
        const cp = row.original.change_pct;
        if (c == null) return <span className="font-data">—</span>;
        const cl = colorForChange(c);
        const sym = trendIcon(c) || '—';
        return (
          <span className="font-data" style={{ color: cl }}>
            {sym} {formatNumber(Math.abs(c), { type: 'price' })}{cp != null ? ` (${cp > 0 ? '+' : ''}${cp.toFixed(1)}%)` : ''}
          </span>
        );
      },
    },
    {
      id: 'momentum',
      header: '動能',
      accessorFn: (row: SignalItem) => row.factor_scores?.momentum ?? row.score,
      meta: { width: 100, align: 'right' as const },
      cell: ({ row }) => <FactorMiniBar name="momentum" score={row.original.factor_scores?.momentum ?? row.original.score} />,
    },
    {
      id: 'value',
      header: '價值',
      accessorFn: (row: SignalItem) => row.factor_scores?.value ?? row.score * 0.8,
      meta: { width: 100, align: 'right' as const },
      cell: ({ row }) => <FactorMiniBar name="value" score={row.original.factor_scores?.value ?? row.original.score * 0.8} />,
    },
    {
      id: 'quality',
      header: '品質',
      accessorFn: (row: SignalItem) => row.factor_scores?.quality ?? row.score * 0.6,
      meta: { width: 100, align: 'right' as const },
      cell: ({ row }) => <FactorMiniBar name="quality" score={row.original.factor_scores?.quality ?? row.original.score * 0.6} />,
    },
    {
      id: 'growth',
      header: '成長',
      accessorFn: (row: SignalItem) => row.factor_scores?.growth ?? row.score * 0.4,
      meta: { width: 100, align: 'right' as const },
      cell: ({ row }) => <FactorMiniBar name="growth" score={row.original.factor_scores?.growth ?? row.original.score * 0.4} />,
    },
    {
      id: 'institutional',
      header: '法人',
      accessorFn: (row: SignalItem) => row.factor_scores?.institutional ?? row.score * 0.3,
      meta: { width: 100, align: 'right' as const },
      cell: ({ row }) => <FactorMiniBar name="institutional" score={row.original.factor_scores?.institutional ?? row.original.score * 0.3} />,
    },
    {
      id: 'pe',
      header: 'PE',
      accessorFn: (row: SignalItem) => {
        const q = liveQuotes[row.stock_id];
        return q?.pe_realtime ?? row.pe ?? null;
      },
      meta: { width: 72, align: 'right' as const },
      cell: ({ row }) => {
        const q = liveQuotes[row.original.stock_id];
        const peVal = q?.pe_realtime ?? row.original.pe ?? null;
        const pbVal = q?.pb_realtime ?? row.original.pb ?? null;
        if (peVal != null || pbVal != null) {
          return <RealtimeValuationBadge 
            stockId={row.original.stock_id}
            currentPrice={q?.price ?? row.original.close_price ?? 0}
            peRt={peVal}
            pbRt={pbVal}
            industryAvgPb={null}
          />;
        }
        return <span className="font-data" style={{ color: 'var(--text-muted)' }}>—</span>;
      },
    },
    {
      id: 'score',
      header: '綜合分數',
      accessorKey: 'score',
      meta: { width: 80, align: 'right' as const },
      cell: ({ getValue }) => (
        <span className={`font-data ${styles.compositeScore}`}>{formatNumber(getValue<number>(), { type: 'score' })}</span>
      ),
    },
  ];

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>今日總覽 Dashboard</h1>
        <span className={styles.headerDate}>
          {today.toISOString().slice(0, 10)}（{weekday}）
        </span>
        <MarketStatus />
        <WebSocketStatus status={wsStatus} />
        <button className={`${styles.refreshBtn}${loading ? ' btn-loading' : ''}`} onClick={load} disabled={loading}>
          {loading ? '⋯' : '↻ 重新整理'}
        </button>
      </div>

      {stale && (
        <div className={styles.errorBanner} style={{ background: 'var(--color-warning-dim, rgba(245,158,11,0.15))' }}>
          ⚠ 無法連線，顯示快取資料（資料可能已過時）
          <button className={styles.retryBtn} onClick={load}>重試</button>
        </div>
      )}
      {error && !stale && (
        <div className={styles.errorBanner}>
          ⚠ 載入失敗：{error}
          <button className={styles.retryBtn} onClick={load}>重試</button>
        </div>
      )}

      <div className={styles.kpiRow} role="group" aria-label="關鍵指標總覽">
        <ErrorBoundary level="component" name="今日選股"><StatCard label="今日選股" value={signals?.stocks.length ?? 0} variant="highlight" loading={loading} /></ErrorBoundary>
        <ErrorBoundary level="component" name="入選ETF"><StatCard label="入選ETF" value={signals?.etfs.length ?? 0} loading={loading} /></ErrorBoundary>
        <ErrorBoundary level="component" name="組合分數"><StatCard
          label="組合分數"
          value={signals?.stocks.reduce((s, i) => s + i.score, 0) ?? 0}
          format="raw"
          loading={loading}
        /></ErrorBoundary>
        <ErrorBoundary level="component" name="大盤概況"><StatCard
          label="大盤概況"
          value={loading ? 0 : '加權'}
          format="raw"
          loading={loading}
          delta={0.003}
          deltaLabel="vs 昨日"
        /></ErrorBoundary>
      </div>

      <div className={styles.weeklyPnl}>
        <span className={styles.pnlLabel}>本週持倉損益</span>
        <span className={styles.pnlBull}>▲ +2.4%</span>
        <span className={styles.pnlLabel}>vs 0050</span>
        <span className={styles.pnlMuted}>▲ +1.1%</span>
        <span className={styles.pnlLabel}>超額</span>
        <span className={styles.pnlBull}>▲ +1.3%</span>
      </div>

      <div className={styles.sectionHeader}>
        <h2>今日入選個股 Top {signals?.stocks.length || 20}</h2>
        <div className={styles.headerActions}>
          <button className={styles.actionBtn} onClick={() => navigate('/signals')}>詳細訊號</button>
          <button className={styles.actionBtn}>匯出CSV</button>
        </div>
      </div>

      <BaseTable<SignalItem>
        columns={columns}
        data={displayData}
        loading={loading}
        emptyMessage="今日沒有符合條件的選股結果"
        sortable={true}
        getRowId={(row) => row.stock_id}
        renderRowDetail={(row) => (
          <SignalRowDetail stockId={row.stock_id} />
        )}
        groupLabel={(row, i, all) => {
          if (i > 0 && etfIds.has(row.stock_id) && !etfIds.has(all[i - 1].stock_id)) return 'ETF';
          return null;
        }}
      />

      {/* T118/T119 即時行情總覽 */}
      {hasLiveData && (
        <div className={styles.marketOverview}>
          <h2>📊 即時行情總覽</h2>
          <div className={styles.marketGrid}>
            <StatCard label="上漲家數" value={upCount} loading={false} />
            <StatCard label="下跌家數" value={downCount} loading={false} />
            <StatCard label="持平家數" value={flatCount} loading={false} />
            <StatCard label="平均 PE" value={avgPE ?? '—'} format="raw" loading={false} />
          </div>
        </div>
      )}
      {!hasLiveData && wsStatus === 'connected' && (
        <div className={styles.marketOverview}>
          <p className={styles.muted}>⏳ 等待即時報價資料...</p>
        </div>
      )}

      <div className={styles.bottomGrid}>
        <div className={styles.panel}>
          <h3>因子貢獻摘要</h3>
          <div className={styles.factorContrib}>
            {['momentum', 'value', 'quality', 'growth', 'institutional'].map((f) => (
              <div key={f} className={styles.factorRow}>
                <span className={styles.factorLabel} style={{ color: `var(--color-${f})` }}>{FACTOR_LABELS[f]}</span>
                <div className={styles.factorBarBg}>
                  <div className={styles.factorBarFill} style={{
                    width: `${(f === 'momentum' ? 20 : f === 'value' ? 15 : f === 'quality' ? 15 : f === 'growth' ? 10 : 25)}%`,
                    background: `var(--color-${f})`,
                  }} />
                </div>
                <span className={styles.factorPct}>
                  {f === 'momentum' ? 20 : f === 'value' ? 15 : f === 'quality' ? 15 : f === 'growth' ? 10 : 25}%
                </span>
              </div>
            ))}
          </div>
        </div>
        <div className={styles.panel}>
          <h3>資料狀態 Data Status</h3>
          {dataStatus ? (
            <div className={styles.datasetList}>
              <div className={styles.datasetRow}>
                <span className={styles.datasetLabel}>價量</span>
                <span className={styles.datasetCount}>{dataStatus.stock_count} 檔</span>
                <span className={styles.datasetDate}>{dataStatus.last_price_update || '—'}</span>
                <span className={styles.datasetStatus}>{dataStatus.last_price_update ? '🟢' : '🔴'}</span>
              </div>
              <div className={styles.datasetRow}>
                <span className={styles.datasetLabel}>訊號</span>
                <span className={styles.datasetCount}>{dataStatus.signal_dates} 天</span>
                <span className={styles.datasetDate}>{dataStatus.latest_signal_date || '—'}</span>
                <span className={styles.datasetStatus}>{dataStatus.signal_dates > 0 ? '🟢' : '🔴'}</span>
              </div>
              {dataStatus.datasets.map((ds) => (
                <div key={ds.name} className={styles.datasetRow}>
                  <span className={styles.datasetLabel}>{DATASET_LABELS[ds.name] || ds.name}</span>
                  <span className={styles.datasetCount}>{ds.count} 筆</span>
                  <span className={styles.datasetDate}>{ds.last_updated?.slice(0, 10) || '—'}</span>
                  <span className={styles.datasetStatus}>
                    {ds.status === 'ok' ? '🟢' : ds.status === 'failed' ? '🔴' : '🟡'}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className={styles.muted}>載入中⋯</p>
          )}
          <a href="/monitor" className={styles.monitorLink}>查看完整監控 →</a>
        </div>
      </div>
    </div>
  );
}
