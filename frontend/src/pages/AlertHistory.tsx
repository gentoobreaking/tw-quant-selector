/* eslint-disable react-hooks/set-state-in-effect */
import { useState, useEffect, useCallback, useContext } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer,
  ScatterChart, Scatter, CartesianGrid,
} from 'recharts';
import { AlertContext } from '../hooks/AlertContext';
import {
  fetchAlertHistory, fetchAlertStats, resolveAlert,
  fetchMarketScreen, type MarketScreenItem,
  fetchSmartAlertHistory,
  fetchInstitutionalTop, type InstTopItem,
} from '../api/client';
import styles from './AlertHistory.module.css';

interface AlertRecord {
  id: string; rule_name: string; severity: string; message: string;
  triggered_at: string | null; resolved_at?: string | null;
}

const SEVERITY_COLORS: Record<string, string> = {
  CRITICAL: '#d32f2f', HIGH: '#f57c00', MEDIUM: '#1976d2', LOW: '#388e3c',
};

const ALERT_ICONS: Record<string, string> = {
  VOLUME_SPIKE: '⚡', HIGH_VOL_NO_MOVE: '📉', TURNOVER_MONSTER: '🐋',
  INTRADAY_VOLATILITY: '📈', INDUSTRY_MOMENTUM: '🌊', AGAINST_TREND: '🛡️',
  LOW_PRICE_JUNK_RALLY: '🚨', ETF_PREMIUM_DISCOUNT: '💰', WHALE_MOVE: '🐋',
  ACTIVE_ETF_HYPE: '🔄',
};

function TabBar({ tabs, active, onChange }: {
  tabs: string[]; active: string; onChange: (v: string) => void;
}) {
  return (
    <div className={styles.tabBar}>
      {tabs.map(t => (
        <button key={t} className={`${styles.tab} ${active === t ? styles.tabActive : ''}`} onClick={() => onChange(t)}>{t}</button>
      ))}
    </div>
  );
}

function changeBgClass(pct: number | null): string {
  if (pct == null) return '';
  return pct > 0 ? styles.bgRed : pct < 0 ? styles.bgGreen : '';
}

function formatPct(v: number | null): string {
  if (v == null) return '—';
  const sign = v >= 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}%`;
}

function formatVol(v: number | null): string {
  if (v == null) return '—';
  return `${(v / 1000).toFixed(0)}K`;
}

function downloadCSV(headers: string[], rows: string[][], filename: string) {
  const csv = [headers.join(','), ...rows.map(r => r.join(','))].join('\n');
  const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ── Tab 1: 全市場數據篩選 ──
function MarketScreenTab() {
  const [data, setData] = useState<MarketScreenItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [includeStocks, setIncludeStocks] = useState(true);
  const [includeEtf, setIncludeEtf] = useState(false);
  const [volumeSpike, setVolumeSpike] = useState(false);
  const [againstTrend, setAgainstTrend] = useState(false);

  const load = () => {
    setLoading(true);
    fetchMarketScreen({ include_stocks: includeStocks, include_etf: includeEtf, volume_spike: volumeSpike, against_trend: againstTrend, limit: 200 })
      .then(setData).catch(() => {}).finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleExport = () => {
    const headers = ['股票代號', '股票名稱', '產業', '類型', '收盤價', '漲跌幅', '成交量'];
    const rows = data.map(d => [
      d.stock_id, d.name, d.industry, d.is_etf ? 'ETF' : '股票',
      d.close?.toFixed(2) ?? '', formatPct(d.change_pct), formatVol(d.volume),
    ]);
    downloadCSV(headers, rows, 'market_screen.csv');
  };

  return (
    <div>
      <div className={styles.filters}>
        <label className={styles.checkLabel}><input type="checkbox" checked={includeStocks} onChange={e => setIncludeStocks(e.target.checked)} />僅看一般股票</label>
        <label className={styles.checkLabel}><input type="checkbox" checked={includeEtf} onChange={e => setIncludeEtf(e.target.checked)} />僅看 ETF</label>
        <label className={styles.checkLabel}><input type="checkbox" checked={volumeSpike} onChange={e => setVolumeSpike(e.target.checked)} />僅顯示今日爆量突破股</label>
        <label className={styles.checkLabel}><input type="checkbox" checked={againstTrend} onChange={e => setAgainstTrend(e.target.checked)} />僅顯示逆市英雄</label>
        <button onClick={load} className={styles.btn}>查詢</button>
        <button onClick={handleExport} className={styles.btn}>📥 匯出篩選結果</button>
      </div>
      {loading && <p className={styles.loading}>載入中...</p>}
      {!loading && (
        <table className={styles.table}>
          <thead><tr>
            <th>股票代號</th><th>名稱</th><th>產業</th><th>類型</th>
            <th>收盤價</th><th>漲跌幅</th><th>成交量</th>
          </tr></thead>
          <tbody>
            {data.map(d => (
              <tr key={d.stock_id}>
                <td>{d.stock_id}</td>
                <td>{d.name}</td>
                <td>{d.industry}</td>
                <td>{d.is_etf ? 'ETF' : '股票'}</td>
                <td>{d.close?.toFixed(2) ?? '—'}</td>
                <td className={changeBgClass(d.change_pct)}>{formatPct(d.change_pct)}</td>
                <td>{formatVol(d.volume)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ── Tab 2: 法人籌碼交叉比對 ──
function InstitutionalCrossTab() {
  const [topData, setTopData] = useState<InstTopItem[]>([]);
  const [loading, setLoading] = useState(false);

  const load = () => {
    setLoading(true);
    fetchInstitutionalTop(50, '', 'total_net', 'desc')
      .then(r => setTopData(r.data)).catch(() => {}).finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const scatterData = topData.map(d => ({
    x: d.foreign_net,
    y: d.sity_net,
    name: `${d.stock_id} ${d.stock_name}`,
    total: d.total_net,
  }));

  const handleExport = () => {
    const headers = ['股票代號', '股票名稱', '外資買賣超', '投信買賣超', '總計'];
    const rows = topData.map(d => [
      d.stock_id, d.stock_name, d.foreign_net.toFixed(0), d.sity_net.toFixed(0), d.total_net.toFixed(0),
    ]);
    downloadCSV(headers, rows, 'institutional_cross.csv');
  };

  return (
    <div>
      <div className={styles.filters}>
        <span className={styles.filterLabel}>前 50 大法人買賣超標的</span>
        <button onClick={load} className={styles.btn}>重新整理</button>
        <button onClick={handleExport} className={styles.btn}>📥 匯出完整 CSV</button>
      </div>
      {loading && <p className={styles.loading}>載入中...</p>}
      {!loading && (
        <>
          <div className={styles.section}>
            <h3>外資 vs 投信買賣超散佈圖</h3>
            <ResponsiveContainer width="100%" height={350}>
              <ScatterChart margin={{ top: 10, right: 30, bottom: 10, left: 30 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis type="number" dataKey="x" name="外資買賣超" tick={{ fontSize: 10 }} />
                <YAxis type="number" dataKey="y" name="投信買賣超" tick={{ fontSize: 10 }} />
                <Tooltip
                  formatter={(value) => [Number(value).toLocaleString(), '']}
                  labelFormatter={(label) => String(label)}
                />
                <Scatter data={scatterData} fill="#1976d2" opacity={0.7} />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
          <div className={styles.section}>
            <h3>法人同買因子標的</h3>
            <table className={styles.table}>
              <thead><tr>
                <th>股票</th><th>名稱</th><th>外資買賣超</th><th>投信買賣超</th><th>總計</th>
              </tr></thead>
              <tbody>
                {topData.filter(d => d.foreign_net > 0 && d.sity_net > 0).map(d => (
                  <tr key={d.stock_id}>
                    <td>{d.stock_id}</td>
                    <td>{d.stock_name}</td>
                    <td className={d.foreign_net > 0 ? styles.textRed : styles.textGreen}>{d.foreign_net.toLocaleString()}</td>
                    <td className={d.sity_net > 0 ? styles.textRed : styles.textGreen}>{d.sity_net.toLocaleString()}</td>
                    <td className={d.total_net > 0 ? styles.textRed : styles.textGreen}>{d.total_net.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

// ── Tab 3: 智慧警示即時牆 ──
function SmartAlertWallTab() {
  const ctx = useContext(AlertContext);
  const wsAlerts = ctx?.alerts ?? [];
  const [historyAlerts, setHistoryAlerts] = useState<Record<string, unknown>[]>([]);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const loadHistory = useCallback(() => {
    fetchSmartAlertHistory(50).then(setHistoryAlerts).catch(() => {});
  }, []);

  useEffect(() => {
    loadHistory();
    const interval = setInterval(loadHistory, 10000);
    return () => clearInterval(interval);
  }, [loadHistory]);

  const allAlerts = [
    ...wsAlerts.map((a, i) => ({ key: `ws-${i}`, ts: '', stockId: a.stock_id ?? '', stockName: a.stock_name ?? '', alertType: a.alert_type ?? '', severity: a.severity ?? '', message: a.message ?? '', details: a.details ?? {} })),
    ...historyAlerts.flatMap((a, i) => {
      const d = a.data as Record<string, unknown> | undefined;
      return d ? [{
        key: `hist-${i}`, ts: a.timestamp as string, stockId: (d.stock_id ?? '') as string, stockName: (d.stock_name ?? '') as string, alertType: (d.alert_type ?? '') as string, severity: (d.severity ?? '') as string, message: (d.message ?? '') as string, details: (d.details ?? {}) as Record<string, unknown>,
      }] : [];
    }),
  ].slice(0, 50);

  const toggleExpand = (idx: number) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx); else next.add(idx);
      return next;
    });
  };

  return (
    <div>
      <div className={styles.filters}>
        <span className={styles.filterLabel}>最近 50 條警示（每 10 秒自動刷新）</span>
        <button onClick={loadHistory} className={styles.btn}>重新整理</button>
      </div>
      {allAlerts.length === 0 && <p className={styles.loading}>暫無警示</p>}
      {allAlerts.map((a, idx) => (
        <div key={a.key} className={styles.alertCard} onClick={() => toggleExpand(idx)}>
          <div className={styles.alertCardHeader}>
            <span className={styles.alertIcon}>{ALERT_ICONS[a.alertType] ?? '🔔'}</span>
            <span className={styles.alertTime}>{a.ts?.slice(0, 19) ?? ''}</span>
            <span className={styles.alertStock}>{a.stockName} ({a.stockId})</span>
            <span className={styles.badge} style={{ background: SEVERITY_COLORS[a.severity] || '#999' }}>{a.severity}</span>
            <span className={styles.alertType}>{a.alertType}</span>
          </div>
          <div className={styles.alertMsg}>{a.message}</div>
          {expanded.has(idx) && Object.keys(a.details).length > 0 && (
            <pre className={styles.alertDetails}>{JSON.stringify(a.details, null, 2)}</pre>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Tab 4: 警示歷史（原有內容） ──
function AlertHistoryTab() {
  const [alerts, setAlerts] = useState<AlertRecord[]>([]);
  const [stats, setStats] = useState<{ daily: { date: string; severity: string; count: number }[]; weekly: { week: string; severity: string; count: number }[] } | null>(null);
  const [severityFilter, setSeverityFilter] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [unresolvedOnly, setUnresolvedOnly] = useState(false);
  const [loading, setLoading] = useState(false);
  const [resolveId, setResolveId] = useState('');
  const [resolveNote, setResolveNote] = useState('');
  const [statsView, setStatsView] = useState<'daily' | 'weekly'>('daily');

  const load = () => {
    setLoading(true);
    Promise.all([
      fetchAlertHistory({
        severity: severityFilter || undefined,
        start_date: startDate || undefined,
        end_date: endDate || undefined,
        unresolved_only: unresolvedOnly,
      }),
      fetchAlertStats(startDate, endDate),
    ]).then(([a, s]) => {
      setAlerts(a);
      setStats(s);
    }).catch(() => {}).finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleResolve = async () => {
    if (!resolveId) return;
    await resolveAlert(resolveId, resolveNote);
    setResolveId('');
    setResolveNote('');
    load();
  };

  return (
    <div>
      <div className={styles.filters}>
        <select value={severityFilter} onChange={e => setSeverityFilter(e.target.value)}>
          <option value="">全部等級</option>
          <option value="CRITICAL">CRITICAL</option>
          <option value="HIGH">HIGH</option>
          <option value="MEDIUM">MEDIUM</option>
          <option value="LOW">LOW</option>
        </select>
        <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} placeholder="開始日期" />
        <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} placeholder="結束日期" />
        <label className={styles.checkLabel}>
          <input type="checkbox" checked={unresolvedOnly} onChange={e => setUnresolvedOnly(e.target.checked)} />
          僅未解決
        </label>
        <button onClick={load} className={styles.btn}>查詢</button>
      </div>
      {loading && <p className={styles.loading}>載入中...</p>}
      {!loading && stats && (
        <div className={styles.section}>
          <div className={styles.statsHeader}>
            <h2>統計 — {statsView === 'daily' ? '每日視圖' : '每週視圖'}</h2>
            <div className={styles.statsToggle}>
              <button onClick={() => setStatsView('daily')} className={statsView === 'daily' ? styles.active : ''}>每日</button>
              <button onClick={() => setStatsView('weekly')} className={statsView === 'weekly' ? styles.active : ''}>每週</button>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={(() => {
              const raw = stats[statsView] as { date?: string; week?: string; severity: string; count: number }[];
              const grouped: Record<string, Record<string, number>> = {};
              for (const r of raw) {
                const k = r.date ?? r.week ?? '';
                if (!grouped[k]) grouped[k] = { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0 };
                grouped[k][r.severity] = (grouped[k][r.severity] || 0) + r.count;
              }
              return Object.entries(grouped).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => ({ date: k, ...v }));
            })()}>
              <XAxis dataKey="date" tick={{ fontSize: 10 }} />
              <YAxis allowDecimals={false} />
              <Tooltip /><Legend />
              {(['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] as const).map(s => (
                <Bar key={s} dataKey={s} fill={SEVERITY_COLORS[s]} name={s} stackId="severities" />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
      {!loading && (
        <div className={styles.section}>
          <h2>警示列表 ({alerts.length})</h2>
          <table className={styles.table}>
            <thead><tr><th>時間</th><th>規則</th><th>等級</th><th>訊息</th><th>狀態</th></tr></thead>
            <tbody>
              {alerts.map(a => (
                <tr key={a.id}>
                  <td className={styles.cellTime}>{a.triggered_at?.slice(0, 19) ?? '—'}</td>
                  <td>{a.rule_name}</td>
                  <td><span className={styles.badge} style={{ background: SEVERITY_COLORS[a.severity] || '#999' }}>{a.severity}</span></td>
                  <td className={styles.cellMsg}>{a.message}</td>
                  <td>{a.resolved_at ? `✓ ${a.resolved_at.slice(0, 10)}` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className={styles.section}>
        <h2>解決警示</h2>
        <div className={styles.resolveRow}>
          <select value={resolveId} onChange={e => setResolveId(e.target.value)}>
            <option value="">選擇警示 ID</option>
            {alerts.filter(a => !a.resolved_at).map(a => (
              <option key={a.id} value={a.id}>{a.rule_name} @ {a.triggered_at?.slice(0, 10) ?? '—'}</option>
            ))}
          </select>
          <input value={resolveNote} onChange={e => setResolveNote(e.target.value)} placeholder="解決備註" />
          <button onClick={handleResolve} className={styles.btn} disabled={!resolveId}>解決</button>
        </div>
      </div>
    </div>
  );
}

// ── Main ──
const TABS = ['全市場數據篩選', '法人籌碼交叉比對', '智慧警示即時牆', '警示歷史'];

export default function AlertHistory() {
  const [activeTab, setActiveTab] = useState(TABS[3]);

  const renderTab = () => {
    switch (activeTab) {
      case TABS[0]: return <MarketScreenTab />;
      case TABS[1]: return <InstitutionalCrossTab />;
      case TABS[2]: return <SmartAlertWallTab />;
      default: return <AlertHistoryTab />;
    }
  };

  return (
    <div className={styles.page}>
      <h1>警示總覽</h1>
      <p className={styles.description}>
        智慧警示系統整合 <strong>全市場即時數據</strong>、<strong>法人籌碼分析</strong> 與 <strong>警示歷史</strong> 三大面向。
        <br />使用上方的頁籤切換不同功能視角。
      </p>
      <TabBar tabs={TABS} active={activeTab} onChange={setActiveTab} />
      {renderTab()}
    </div>
  );
}