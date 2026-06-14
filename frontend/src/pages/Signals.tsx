import { useState, useEffect, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { fetchSignalCalendar, fetchSignalsByDate } from '../api/client';
import BaseTable from '../components/BaseTable';
import FactorMiniBar from '../components/FactorMiniBar';
import Tooltip from '../components/Tooltip';
import Dropdown from '../components/Dropdown';
import ExportModal from '../components/ExportModal';
import EmptyState from '../components/EmptyState';
import MissingDataSummary from '../components/MissingDataSummary';
import { useToast } from '../components/Toast';
import { formatNumber, colorize } from '../utils/format';
import { colorForChange, trendIcon } from '../utils/color';
import SignalRowDetail from '../components/SignalRowDetail';
import { useAlertContext } from '../hooks/AlertContext';
import type { ColumnDef } from '@tanstack/react-table';
import styles from './Signals.module.css';

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
}

interface SignalsResponse {
  date: string;
  stocks: SignalItem[];
  etfs: SignalItem[];
}

const ALERT_ROW_ICONS: Record<string, string> = {
  VOLUME_SPIKE: '⚡',
  HIGH_VOL_NO_MOVE: '📉',
  TURNOVER_MONSTER: '🐋',
  INTRADAY_VOLATILITY: '📈',
  INDUSTRY_MOMENTUM: '🌊',
  AGAINST_TREND: '🛡️',
  LOW_PRICE_JUNK_RALLY: '🚨',
  ETF_PREMIUM_DISCOUNT: '💰',
  WHALE_MOVE: '🐋',
  ACTIVE_ETF_HYPE: '🔄',
};

export default function Signals() {
  const navigate = useNavigate();
  const { getAlertsForStock } = useAlertContext();
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState<SignalsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dates, setDates] = useState<string[]>([]);
  const [calLoading, setCalLoading] = useState(true);
  const sortKey = searchParams.get('sort') || 'score';
  const sortDir = (searchParams.get('dir') as 'asc' | 'desc') || 'desc';
  const [showEtf, setShowEtf] = useState(searchParams.get('etf') !== '0');
  const [expandedRow, setExpandedRow] = useState<string | null>(searchParams.get('stock'));
  const [strategy, setStrategy] = useState(searchParams.get('strategy') || 'composite');
  const [selectedDate, setSelectedDate] = useState(searchParams.get('date') || '');
  const { addToast } = useToast();
  const [showExport, setShowExport] = useState(false);
  const [exporting, setExporting] = useState(false);

  const dateSet = useMemo(() => new Set(dates), [dates]);
  const minDate = dates.length ? dates[dates.length - 1] : '';
  const maxDate = dates.length ? dates[0] : '';

  useEffect(() => {
    fetchSignalCalendar().then((d) => {
      setDates(d);
      setCalLoading(false);
      const urlDate = searchParams.get('date');
      if (urlDate && d.includes(urlDate)) {
        setSelectedDate(urlDate);
      } else if (!selectedDate) {
        setSelectedDate(d[0] || '');
      }
    }).catch(() => setCalLoading(false));
  }, []);

  useEffect(() => {
    if (!selectedDate) return;
    setLoading(true);
    setError(null);
    fetchSignalsByDate(selectedDate, strategy, true)
      .then((d) => { setData(d as unknown as SignalsResponse); setLoading(false); })
      .catch((e: Error) => { setData(null); setError(e.message); setLoading(false); });
  }, [selectedDate, strategy]);

  useEffect(() => {
    setSearchParams(prev => {
      prev.set('sort', sortKey);
      prev.set('dir', sortDir);
      prev.set('strategy', strategy);
      if (selectedDate) prev.set('date', selectedDate);
      if (showEtf) prev.delete('etf'); else prev.set('etf', '0');
      if (expandedRow) prev.set('stock', expandedRow); else prev.delete('stock');
      return prev;
    }, { replace: true });
  }, [sortKey, sortDir, strategy, showEtf, expandedRow, selectedDate]);

  const goPrevDay = () => {
    const idx = dates.indexOf(selectedDate);
    if (idx < dates.length - 1) setSelectedDate(dates[idx + 1]);
  };
  const goNextDay = () => {
    const idx = dates.indexOf(selectedDate);
    if (idx > 0) setSelectedDate(dates[idx - 1]);
  };
  const idx = dates.indexOf(selectedDate);
  const hasPrev = idx < dates.length - 1;
  const hasNext = idx > 0;

  const allItems = [...(data?.stocks || []), ...(showEtf ? data?.etfs || [] : [])];

  const etfIds = new Set(data?.etfs?.map((e) => e.stock_id) || []);

  const handleExport = async (format: 'csv' | 'json', _columns: string[]) => {
    setExporting(true);
    const dateStr = (data?.date || selectedDate).replace(/-/g, '');
    const url = `/api/v1/signals/export.${format}${format === 'csv' ? '' : '?'}`;
    try {
      const res = await fetch(url);
      if (!res.ok) { addToast(`匯出失敗 (${res.status})`, 'high'); return; }
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `tw_signals_${dateStr}.${format}`;
      a.click();
      URL.revokeObjectURL(a.href);
      addToast(`已匯出 ${format.toUpperCase()}`, 'low');
    } catch (e: unknown) {
      addToast(`匯出失敗: ${e instanceof Error ? e.message : '未知錯誤'}`, 'high');
    } finally {
      setExporting(false);
      setShowExport(false);
    }
  };

  const strategyLabel = (s: string) => {
    const map: Record<string, string> = { composite: '全部策略', momentum: '動能', value: '價值', quality: '品質', growth: '成長', institutional: '法人' };
    return map[s] || s;
  };

  const FACTOR_TOOLTIPS: Record<string, string> = {
    momentum: '動能因子：近 3、6、12 個月累計報酬率加權，高者代表近期表現強勢',
    value: '價值因子：本益比、淨值比、殖利率綜合評分，低本益比/高殖利率者分數高',
    quality: '品質因子：ROE、毛利率、負債比綜合評估，高獲利能力且低負債者分數高',
    growth: '成長因子：營收年增率與 EPS 年增率加權，雙成長者分數高',
    institutional: '法人因子：外資/投信累積買賣超、連續買賣超天數加權，法人同步買超者分數高',
  };

  const makeFactorCol = (key: string, label: string, fallbackMul = 1): ColumnDef<SignalItem, any> => ({
    id: key,
    header: () => <Tooltip content={FACTOR_TOOLTIPS[key]}>{label}</Tooltip>,
    accessorFn: (row: SignalItem) => row.factor_scores?.[key] ?? row.score * fallbackMul,
    meta: { width: 100, align: 'right' as const },
    cell: ({ row }) => <FactorMiniBar name={key} score={row.original.factor_scores?.[key] ?? row.original.score * fallbackMul} />,
  });

  const columns: ColumnDef<SignalItem, any>[] = [
    {
      id: 'rank',
      header: '排名',
      accessorKey: 'rank',
      meta: { width: 48, align: 'right' as const },
      cell: ({ getValue }) => <span className={styles.rankCell}>#{getValue<number>()}</span>,
    },
    {
      id: 'rank_change',
      header: '變動',
      accessorKey: 'rank_change',
      meta: { width: 48, align: 'right' as const },
      cell: ({ getValue }) => {
        const rc = getValue<number | null | undefined>();
        if (rc == null) return '';
        const str = rc > 0 ? `▲${rc}` : rc < 0 ? `▼${Math.abs(rc)}` : '—';
        return <span className={colorize(rc, 'score').className}>{str}</span>;
      },
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
      id: 'alert_icon',
      header: '',
      enableSorting: false,
      meta: { width: 36, align: 'center' as const },
      cell: ({ row }) => {
        const stockAlerts = getAlertsForStock(row.original.stock_id);
        if (stockAlerts.length === 0) return null;
        const icons = [...new Set(stockAlerts.map((a) => ALERT_ROW_ICONS[a.alert_type] || '🔔'))];
        const tooltip = stockAlerts.map((a) => `${a.alert_type} [${a.severity}]`).join('\n');
        return (
          <Tooltip content={tooltip}>
            <span style={{ cursor: 'help', fontSize: 'var(--font-size-base)' }}>{icons.slice(0, 2).join(' ')}</span>
          </Tooltip>
        );
      },
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
      header: '漲跌',
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
    makeFactorCol('momentum', '動能', 1),
    makeFactorCol('value', '價值', 0.8),
    makeFactorCol('quality', '品質', 0.6),
    makeFactorCol('growth', '成長', 0.4),
    makeFactorCol('institutional', '法人', 0),
    {
      id: 'score',
      header: '綜合',
      accessorKey: 'score',
      meta: { width: 80, align: 'right' as const },
      cell: ({ getValue }) => (
        <span className={`font-data ${styles.compositeScore}`}>{formatNumber(getValue<number>(), { type: 'score' })}</span>
      ),
    },
    {
      id: 'consecutive_days',
      header: '天數',
      accessorKey: 'consecutive_days',
      meta: { width: 50, align: 'right' as const },
      cell: ({ getValue }) => {
        const v = getValue<number | null | undefined>();
        return <span className="font-data" style={{ color: 'var(--text-muted)' }}>{v != null ? v : '—'}</span>;
      },
    },
    {
      id: 'holdings',
      header: '持倉',
      enableSorting: false,
      meta: { width: 60 },
      cell: () => <span style={{ display: 'block', textAlign: 'center', color: 'var(--text-muted)' }}>○</span>,
    },
  ];

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>選股訊號 Signals</h1>
        <div className={styles.headerMeta}>
          <div className={styles.dateNav}>
            <button className={styles.navBtn} onClick={goPrevDay} disabled={!hasPrev || calLoading} title="前一天">◀</button>
            <input
              type="date"
              value={selectedDate}
              min={minDate}
              max={maxDate}
              onChange={(e) => { if (dateSet.has(e.target.value)) setSelectedDate(e.target.value); }}
              className={styles.datePicker}
              list="signal-dates"
            />
            <datalist id="signal-dates">
              {dates.map((d) => <option key={d} value={d} />)}
            </datalist>
            <button className={styles.navBtn} onClick={goNextDay} disabled={!hasNext || calLoading} title="下一天">▶</button>
          </div>
          <Dropdown
            options={[
              { value: 'composite', label: '全部策略' },
              { value: 'momentum', label: '動能' },
              { value: 'value', label: '價值' },
              { value: 'quality', label: '品質' },
              { value: 'growth', label: '成長' },
              { value: 'institutional', label: '法人' },
            ]}
            value={strategy}
            onChange={setStrategy}
            className={styles.select}
          />
          <label className={styles.toggle}>
            <input type="checkbox" checked={showEtf} onChange={() => setShowEtf((x) => !x)} />
            ETF
          </label>
          <button className={styles.actionBtn} onClick={() => setShowExport(true)}>
            {exporting ? '匯出中...' : '匯出'}
          </button>
        </div>
      </div>

      {error ? (
        <EmptyState scenario="failed" onRetry={() => window.location.reload()}>
          <strong>{strategyLabel(strategy)}</strong> {data ? '該日無資料' : '尚無資料'} — 請執行排程器或切換日期
        </EmptyState>
      ) : (
        <>
          <BaseTable<SignalItem>
            columns={columns}
            data={allItems}
            loading={loading}
            emptyMessage={selectedDate ? `${selectedDate} 沒有符合條件的選股結果` : '請選擇日期'}
            getRowId={(row) => row.stock_id}
            expandedRow={expandedRow}
            onExpandedChange={(id) => setExpandedRow(id)}
            renderRowDetail={(row) => (
              <SignalRowDetail stockId={row.stock_id} />
            )}
            groupLabel={(row, i, all) => {
              if (i > 0 && etfIds.has(row.stock_id) && !etfIds.has(all[i - 1].stock_id)) return 'ETF';
              return null;
            }}
          />
          {allItems.length > 0 && (
            <MissingDataSummary missing={{
              連續天數: allItems.filter(s => s.consecutive_days == null).length,
              因子分數: allItems.filter(s => !s.factor_scores || Object.keys(s.factor_scores).length === 0).length,
              排名變動: allItems.filter(s => s.rank_change == null).length,
            }} />
          )}
        </>
      )}

      {showExport && (
        <ExportModal
          defaultColumns={[
            { key: 'rank', label: '排名', visible: true },
            { key: 'stock_id', label: '股號', visible: true },
            { key: 'name', label: '名稱', visible: true },
            { key: 'score', label: '綜合分數', visible: true },
            { key: 'momentum', label: '動能', visible: true },
            { key: 'value', label: '價值', visible: true },
            { key: 'quality', label: '品質', visible: true },
            { key: 'growth', label: '成長', visible: true },
            { key: 'institutional', label: '法人', visible: true },
            { key: 'market_cap', label: '市值', visible: false },
            { key: 'pe_ratio', label: '本益比', visible: false },
          ]}
          onExport={handleExport}
          onClose={() => setShowExport(false)}
        />
      )}
    </div>
  );
}
