import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import EmptyState from '../components/EmptyState';
import PortfolioPieCharts from '../components/PortfolioPieCharts';
import { useToast } from '../components/Toast';
import { formatNumber } from '../utils/format';
import { trendIcon } from '../utils/color';
import type { AlertLogEntry } from '../types';
import styles from './Portfolio.module.css';

const API = '';
async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, init);
  if (!res.ok) throw new Error(`API ${res.status}`);
  const body = await res.json();
  if (body.error) throw new Error(body.error.message || 'API error');
  return (body.data ?? body) as T;
}

interface Lot {
  id: string;
  stock_id: string;
  date: string;
  shares: number;
  cost: number;
}

interface AggregatedHolding {
  stock_id: string;
  lots: Lot[];
  totalShares: number;
  avgCost: number;
  current_price: number | null;
  name: string;
  pl_pct_thod: number | null;
  pl_thod: number | null;
  alert_enabled: boolean;
}

interface StockAlertConfig {
  pl_thod?: number;
  pl_pct_thod?: number;
  alert_enabled: boolean;
}

interface GlobalThresholds {
  pl_threshold: number | null;
  pl_percent_threshold: number | null;
}

const ALERT_CONFIG_KEY = 'tw_quant_alert_configs';

function loadLots(): Lot[] {
  return [];
}

function loadAlertConfigs(): Record<string, StockAlertConfig> {
  try {
    return JSON.parse(localStorage.getItem(ALERT_CONFIG_KEY) || '{}');
  } catch { return {}; }
}

export default function Portfolio() {
  const navigate = useNavigate();
  const { addToast } = useToast();
  const [lots, setLots] = useState<Lot[]>(loadLots);
  const [holdings, setHoldings] = useState<AggregatedHolding[]>([]);
  const [prices, setPrices] = useState<Record<string, { name: string; close: number | null }>>({});

  const refreshPortfolio = useCallback(async (realtime: boolean = false) => {
    try {
        const [data, lotsData] = await Promise.all([
            apiFetch<AggregatedHolding[]>('/api/v1/portfolio'),
            apiFetch<Lot[]>('/api/v1/lots')
        ]);
        setHoldings(data);
        setLots(lotsData);
        const ids = [...new Set(data.map((h) => h.stock_id))].join(',');
        if (ids) {
            const url = realtime 
                ? `/api/v1/stocks/prices?ids=${ids}&realtime=true`
                : `/api/v1/stocks/prices?ids=${ids}`;
            const p = await apiFetch<Record<string, { name: string; close: number | null }>>(url);
            setPrices(p);
        }
    } catch {
        // Fallback
    }
  }, []);

  useEffect(() => {
    refreshPortfolio();
  }, [refreshPortfolio]);

  useEffect(() => {
    const es = new EventSource(`${API}/api/v1/portfolio/events`);
    es.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === 'portfolio_update') {
          refreshPortfolio();
        } else if (payload.type === 'realtime_price_update') {
          // 收到即時價格更新事件，傳入 realtime=true
          refreshPortfolio(true);
        }
      } catch { /* ignore parse errors */ }
    };
    es.onerror = () => {
      // EventSource auto-reconnects on error
    };
    return () => es.close();
  }, [refreshPortfolio]);

  const [cashBalance, setCashBalance] = useState(() => {
    try { return Number(localStorage.getItem('tw_quant_cash') || '0'); }
    catch { return 0; }
  });
  const [stockId, setStockId] = useState('');
  const [shares, setShares] = useState('');
  const [cost, setCost] = useState('');
  const [date, setDate] = useState(new Date().toISOString().split('T')[0]);
  const [adding, setAdding] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [newRows, setNewRows] = useState<Set<string>>(new Set());
  const [errors, setErrors] = useState<Record<string, boolean>>({});

  const [globalThresholds, setGlobalThresholds] = useState<GlobalThresholds>({ pl_threshold: null, pl_percent_threshold: null });
  const [alertConfigs, setAlertConfigs] = useState<Record<string, StockAlertConfig>>(loadAlertConfigs);
  const [configuringStock, setConfiguringStock] = useState<string | null>(null);
  const [tempConfig, setTempConfig] = useState<StockAlertConfig | null>(null);
  const [alertLog, setAlertLog] = useState<AlertLogEntry[]>([]);
  const [alertLogPagination, setAlertLogPagination] = useState({ page: 1, page_size: 30, total: 0, total_pages: 0 });
  const [alertPageSize, setAlertPageSize] = useState(30);
  const [showAlertLog, setShowAlertLog] = useState(true);  // ✅ 改 true，默认展开
  const [selectedLogIds, setSelectedLogIds] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [confirmBatchDelete, setConfirmBatchDelete] = useState(false);

  const toggleLogSelection = (logId: string) => {
    setSelectedLogIds((prev) => {
      const next = new Set(prev);
      if (next.has(logId)) next.delete(logId);
      else next.add(logId);
      return next;
    });
  };

  const toggleSelectAll = () => {
    setSelectedLogIds((prev) => {
      const allSelected = alertLog.length > 0 && alertLog.every((l) => prev.has(l.log_id));
      if (allSelected) return new Set();
      return new Set(alertLog.map((l) => l.log_id));
    });
  };

  const deleteOneLog = async (logId: string) => {
    setDeleting(true);
    try {
      await fetch(`${API}/api/v1/alerts/log/${encodeURIComponent(logId)}`, { method: 'DELETE' });
      addToast('已刪除 1 筆通知記錄', 'low');
      setSelectedLogIds((prev) => {
        const next = new Set(prev);
        next.delete(logId);
        return next;
      });
      await fetchAlertLog(alertLogPagination.page, alertPageSize);
    } catch {
      addToast('刪除失敗', 'medium');
    } finally {
      setDeleting(false);
      setConfirmDeleteId(null);
    }
  };

  const deleteBatchLogs = async () => {
    if (selectedLogIds.size === 0) return;
    setDeleting(true);
    try {
      await fetch(`${API}/api/v1/alerts/log/batch`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ log_ids: Array.from(selectedLogIds) }),
      });
      addToast(`已批次刪除 ${selectedLogIds.size} 筆`, 'low');
      setSelectedLogIds(new Set());
      await fetchAlertLog(alertLogPagination.page, alertPageSize);
    } catch {
      addToast('批次刪除失敗', 'medium');
    } finally {
      setDeleting(false);
      setConfirmBatchDelete(false);
    }
  };

  useEffect(() => {
    apiFetch<{ key: string; value: string | null }[]>('/api/v1/settings/alerts').then(data => {
      const pl = data.find(s => s.key === 'PL_THRESHOLD');
      const pct = data.find(s => s.key === 'PL_PERCENT_THRESHOLD');
      setGlobalThresholds({
        pl_threshold: pl?.value ? Number(pl.value) : null,
        pl_percent_threshold: pct?.value ? Number(pct.value) : null,
      });
    }).catch(() => {});
  }, []);

  useEffect(() => { localStorage.setItem('tw_quant_alert_configs', JSON.stringify(alertConfigs)); }, [alertConfigs]);
  useEffect(() => { localStorage.setItem('tw_quant_cash', String(cashBalance)); }, [cashBalance]);

  const fetchAlertLog = useCallback(async (page: number, pageSize: number) => {
    console.log('[fetchAlertLog] 調用 API: page=', page, 'pageSize=', pageSize);
    try {
      const data = await apiFetch<{ items: AlertLogEntry[]; pagination: { page: number; page_size: number; total: number; total_pages: number } }>(
        `/api/v1/alerts/log?page=${page}&page_size=${pageSize}`
      );
      console.log('[fetchAlertLog] API 返回:', data);
      setAlertLog(data.items || []);
      setAlertLogPagination(data.pagination || { page: 1, page_size: pageSize, total: 0, total_pages: 0 });
      setSelectedLogIds(new Set());
    } catch (e) {
      console.error('[fetchAlertLog] 失敗:', e);
    }
  }, []);

  useEffect(() => {
    console.log('[useEffect] showAlertLog=', showAlertLog, 'alertPageSize=', alertPageSize);
    if (showAlertLog) {
      fetchAlertLog(1, alertPageSize);
    }
  }, [showAlertLog, alertPageSize, fetchAlertLog]);

  const addLot = async () => {
    const sid = stockId.trim();
    const sh = Number(shares);
    const co = Number(cost);
    if (!sid || sh <= 0 || co <= 0) {
        setErrors({ stockId: !sid, shares: sh <= 0, cost: co <= 0 });
        return;
    }
    setAdding(true);
    try {
      await fetch(`${API}/api/v1/lots`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stock_id: sid, date, shares: sh, cost: co })
      });
      await refreshPortfolio();
      addToast(`${sid} 已加入投組`, 'low');
      setNewRows((prev) => new Set(prev).add(sid));
      setTimeout(() => setNewRows((prev) => { const n = new Set(prev); n.delete(sid); return n; }), 500);
      await refreshPortfolio();
    } catch {
      addToast(`新增失敗`, 'medium');
    }
    setStockId(''); setShares(''); setCost(''); setErrors({});
    setAdding(false);
  };

  const removeLotById = async (id: string) => {
    try {
      await fetch(`${API}/api/v1/lots/${id}`, { method: 'DELETE' });
      await refreshPortfolio();
      addToast('逐筆已刪除', 'low');
    } catch {
      addToast('刪除失敗', 'medium');
    }
  };

  const removeStock = async (stockId: string) => {
    try {
        await fetch(`${API}/api/v1/portfolio/${stockId}`, { method: 'DELETE' });
        await refreshPortfolio();
        addToast('持股已刪除', 'low');
    } catch {
        addToast('刪除失敗', 'medium');
    }
  };

  const holdingsWithPrice = holdings.map(h => ({
    ...h,
    current_price: prices[h.stock_id]?.close ?? h.current_price
  }));

  const totalValue = holdingsWithPrice.reduce((s, h) => s + ((h.current_price ?? 0) || h.avgCost) * h.totalShares, 0);
  const totalCost = holdingsWithPrice.reduce((s, h) => s + h.avgCost * h.totalShares, 0);
  const totalPnl = totalValue - totalCost;

  const groups = lots.reduce<Record<string, AggregatedHolding>>((acc, l) => {
    if (!acc[l.stock_id]) acc[l.stock_id] = { stock_id: l.stock_id, lots: [], totalShares: 0, avgCost: 0, current_price: null, name: '', pl_pct_thod: null, pl_thod: null, alert_enabled: true };
    acc[l.stock_id].lots.push(l);
    acc[l.stock_id].totalShares += l.shares;
    return acc;
  }, {});

  const toggleExpand = (sid: string) => setExpanded(expanded === sid ? null : sid);

  const getBreachStatus = (h: AggregatedHolding, pnl: number, pnlPct: number): { breached: boolean; type: 'pl' | 'pct' | null; value: number | null } => {
    if (h.alert_enabled === false) return { breached: false, type: null, value: null };
    const plT = h.pl_thod ?? globalThresholds.pl_threshold;
    const pctT = h.pl_pct_thod ?? globalThresholds.pl_percent_threshold;
    if (plT != null && Math.abs(pnl) >= plT) return { breached: true, type: 'pl', value: plT };
    if (pctT != null && Math.abs(pnlPct * 100) >= pctT) return { breached: true, type: 'pct', value: pctT };
    return { breached: false, type: null, value: null };
  };

  const openConfig = (sid: string) => {
      const h = holdings.find(x => x.stock_id === sid);
      setConfiguringStock(sid);
      setTempConfig({
        pl_thod: h?.pl_thod ?? undefined,
        pl_pct_thod: h?.pl_pct_thod ?? undefined,
        alert_enabled: h?.alert_enabled ?? true,
      });
  }

  const saveConfig = async () => {
    if (configuringStock && tempConfig) {
        setAlertConfigs(prev => ({ ...prev, [configuringStock]: tempConfig }));
        try {
            await fetch(`${API}/api/v1/portfolio/${configuringStock}/thresholds`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pl_thod: tempConfig.pl_thod, pl_pct_thod: tempConfig.pl_pct_thod, alert_enabled: tempConfig.alert_enabled }),
            });
            await refreshPortfolio();
        } catch {}
        addToast('設定已儲存', 'low');
        setConfiguringStock(null);
    }
  };

  const clearConfig = async () => {
    if (configuringStock) {
        setAlertConfigs(prev => {
            const next = { ...prev };
            delete next[configuringStock];
            return next;
        });
        try {
            await fetch(`${API}/api/v1/portfolio/${configuringStock}/thresholds`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pl_thod: null, pl_pct_thod: null, alert_enabled: true }),
            });
            await refreshPortfolio();
        } catch {}
        setConfiguringStock(null);
        addToast('設定已清除', 'low');
    }
  };

  return (
    <div className={styles.page}>
      <h1 className={styles.title}>投組追蹤 Portfolio</h1>
      <div className={styles.addForm}>
        <input className={`${styles.input} ${errors.stockId ? 'input-error' : ''}`} placeholder="股號 (e.g. 2330)" value={stockId} onChange={(e) => setStockId(e.target.value.toUpperCase())} />
        <input className={`${styles.input} ${errors.shares ? 'input-error' : ''}`} type="number" placeholder="股數" value={shares} onChange={(e) => setShares(e.target.value)} />
        <input className={`${styles.input} ${errors.cost ? 'input-error' : ''}`} type="number" step="0.01" placeholder="每股成本" value={cost} onChange={(e) => setCost(e.target.value)} />
        <input className={styles.input} type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        <button className={`${styles.addBtn}${adding ? ' btn-loading' : ''}`} onClick={addLot} disabled={adding}>加入</button>
      </div>

      <div className={styles.summary}>
        {[
          { label: '總市值', value: totalValue },
          { label: '總成本', value: totalCost },
          { label: '總損益', value: totalPnl, colored: true },
          { label: '報酬率', value: totalCost > 0 ? totalPnl / totalCost : 0, pct: true, colored: true },
        ].map((s) => (
          <div key={s.label} className={styles.summaryCard}>
            <div className={styles.summaryLabel}>{s.label}</div>
            <div className={`${styles.summaryValue} ${s.colored && totalPnl >= 0 ? styles.bullText : s.colored && totalPnl < 0 ? styles.bearText : ''}`}>
              {s.pct ? formatNumber(s.value, { type: 'percent' }) : formatNumber(Number(s.value), { type: 'money' })}
            </div>
          </div>
        ))}
        <div className={styles.summaryCard}>
          <div className={styles.summaryLabel}>現金餘額</div>
          <input className={styles.cashInput} type="number" value={cashBalance} onChange={(e) => setCashBalance(Number(e.target.value))} />
        </div>
      </div>

      {/* 新增圓餅圖 */}
      <PortfolioPieCharts 
        holdings={holdings}
        prices={prices}
        onSliceClick={(stockId) => {
          navigate(`/signals/${stockId}`);
        }}
      />

      <h2 className={styles.sectionTitle}>持倉 Holdings</h2>
      <div className={styles.tableWrapper}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>股號</th><th>名稱</th><th data-type="number">合計股數</th><th data-type="number">均價</th>
              <th data-type="number">現價</th><th data-type="number">損益</th><th data-type="number">損益%</th>
              <th data-type="number">警示門檻 (金額/%)</th><th data-type="number">權重</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            {holdingsWithPrice.length === 0 ? (
              <tr><td colSpan={10} className={styles.emptyCell}><EmptyState scenario="initial">尚無持股，上方輸入股號加入</EmptyState></td></tr>
            ) : (
              holdingsWithPrice.map((h) => {
                const curPrice = h.current_price ?? h.avgCost;
                const pnl = (curPrice - h.avgCost) * h.totalShares;
                const pnlPct = (curPrice - h.avgCost) / h.avgCost;
                const weight = totalCost > 0 ? (h.avgCost * h.totalShares) / totalCost : 0;
                const breach = getBreachStatus(h, pnl, pnlPct);
                const isOpen = expanded === h.stock_id;

                return (
                  <tr key={h.stock_id} className={`${styles.dataRow} ${newRows.has(h.stock_id) ? 'row-new' : ''}`}>
                    <td className={styles.stockLink} onClick={() => navigate(`/signals/${h.stock_id}`)}>{h.stock_id}</td>
                    <td>{h.name}</td>
                    <td data-type="number">{h.totalShares.toLocaleString()}</td>
                    <td data-type="number">{formatNumber(h.avgCost, { type: 'price' })}</td>
                    <td data-type="number">{formatNumber(h.current_price, { type: 'price' })}</td>
                    <td data-type="number" className={pnl >= 0 ? styles.bullText : styles.bearText}>
                      <span className={styles.pnlIcon}>{trendIcon(pnl) || '—'}</span>
                      {formatNumber(pnl, { type: 'money' })}
                    </td>
                    <td data-type="number" className={pnl >= 0 ? styles.bullText : styles.bearText}>
                      {formatNumber(pnlPct, { type: 'percent' })}
                    </td>
                    <td data-type="number" className={breach.breached ? styles.alertBreached : ''}>
                      <button className={styles.alertBtn} onClick={() => openConfig(h.stock_id)}>
                        {h.alert_enabled === false
                          ? '🔴 關'
                          : `${formatNumber(h.pl_thod ?? globalThresholds.pl_threshold, { type: 'money' })} / ${h.pl_pct_thod ?? globalThresholds.pl_percent_threshold ?? '—'}%`}
                      </button>
                    </td>
                    <td data-type="number">{formatNumber(weight, { type: 'percent' })}</td>
                    <td>
                      <button className={styles.expandBtn} onClick={(e) => { e.stopPropagation(); toggleExpand(h.stock_id); }}>{isOpen ? '▲' : '▼'}</button>
                      <button className={styles.delBtn} onClick={(e) => { e.stopPropagation(); removeStock(h.stock_id); }}>✕</button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Expanded detail: per-lot breakdown */}
      {expanded && groups[expanded] && (
        <div className={styles.expandedSection}>
          <h3 className={styles.expandedTitle}>{expanded} {prices[expanded]?.name ?? ''} 逐筆明細</h3>
          <table className={styles.detailTable}>
            <thead>
              <tr><th data-type="number">日期</th><th data-type="number">股數</th><th data-type="number">成本</th><th data-type="number">現價</th><th data-type="number">損益</th><th data-type="number">損益%</th><th></th></tr>
            </thead>
            <tbody>
              {groups[expanded].lots.map((l: Lot) => {
                const cur = prices[expanded]?.close ?? null;
                const lp = (cur ?? l.cost) * l.shares;
                const lc = l.cost * l.shares;
                const lpnl = lp - lc;
                const lpnlPct = (cur ?? l.cost) / l.cost - 1;
                return (
                  <tr key={l.id} className={styles.detailRow}>
                    <td data-type="number">{l.date}</td>
                    <td data-type="number">{formatNumber(l.shares, { type: 'volume' }).replace('萬張', '')}</td>
                    <td data-type="number">{formatNumber(l.cost, { type: 'price' })}</td>
                    <td data-type="number">{formatNumber(cur, { type: 'price' })}</td>
                    <td data-type="number" className={lpnl >= 0 ? styles.bullText : styles.bearText}>
                      <span className={styles.pnlIcon}>{trendIcon(lpnl) || '—'}</span>
                      {formatNumber(lpnl, { type: 'money' })}
                    </td>
                    <td data-type="number" className={lpnl >= 0 ? styles.bullText : styles.bearText}>
                      {formatNumber(lpnlPct, { type: 'percent' })}
                    </td>
                    <td><button className={styles.delBtn} onClick={() => removeLotById(l.id)}>✕</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {configuringStock && tempConfig && (
        <div className={styles.configPanel}>
            <div className={styles.configHeader}>
                <span className={styles.configTitle}>設定 {configuringStock} 門檻</span>
                <button className={styles.configCloseBtn} onClick={() => setConfiguringStock(null)}>✕</button>
            </div>
            <div className={styles.configBody}>
              <div className={styles.configField}>
                <label className={styles.configLabel}>金額門檻 (±TWD)</label>
                <input className={styles.configInput} type="number" 
                    value={tempConfig.pl_thod ?? ''}
                    onChange={(e) => setTempConfig(prev => prev ? {...prev, pl_thod: Number(e.target.value)} : null)}
                />
              </div>
              <div className={styles.configField}>
                <label className={styles.configLabel}>百分比門檻 (±%)</label>
                <input className={styles.configInput} type="number" 
                    value={tempConfig.pl_pct_thod ?? ''}
                    onChange={(e) => setTempConfig(prev => prev ? {...prev, pl_pct_thod: Number(e.target.value)} : null)}
                />
              </div>
              <div className={styles.configField}>
                <label className={styles.configLabel}>啟用監控</label>
                <div className={styles.configToggleRow}>
                  <label className={styles.configToggleLabel}>
                    <input type="checkbox"
                      checked={tempConfig.alert_enabled}
                      onChange={(e) => setTempConfig(prev => prev ? {...prev, alert_enabled: e.target.checked} : null)}
                    />
                    {tempConfig.alert_enabled ? '已啟用' : '已停用'}
                  </label>
                </div>
              </div>
              <div className={styles.configActions}>
                  <button className={styles.configSaveBtn} onClick={saveConfig}>儲存設定</button>
                  <button className={styles.configClearBtn} onClick={clearConfig}>清除設定</button>
              </div>
            </div>
        </div>
      )}

      <button className={styles.alertLogToggle} onClick={() => setShowAlertLog(s => !s)}>
        {showAlertLog ? '▲' : '▼'} 通知記錄 ({alertLogPagination.total})
      </button>
      {showAlertLog && (
        <div className={styles.alertLogPanel}>
          <div className={styles.alertLogHeader}>
            <span>每頁筆數：</span>
            <select
              value={alertPageSize}
              onChange={(e) => setAlertPageSize(Number(e.target.value))}
              className={styles.pageSizeSelect}
            >
              <option value={5}>5</option>
              <option value={10}>10</option>
              <option value={20}>20</option>
              <option value={30}>30</option>
              <option value={40}>40</option>
              <option value={50}>50</option>
            </select>
            <button
              className={styles.batchDeleteBtn}
              disabled={selectedLogIds.size === 0 || deleting}
              onClick={() => setConfirmBatchDelete(true)}
            >
              🗑 刪除選中 ({selectedLogIds.size})
            </button>
            <span style={{ marginLeft: 'auto' }}>
              第 {alertLogPagination.page} 頁 / 共 {alertLogPagination.total_pages} 頁 ({alertLogPagination.total} 筆)
            </span>
          </div>
          {alertLog.length === 0 ? (
            <p className={styles.alertLogEmpty}>尚無通知記錄</p>
          ) : (
            <>
              <table className={styles.alertLogTable}>
                <thead>
                  <tr>
                    <th className={styles.alertLogCheckCell}>
                      <input
                        type="checkbox"
                        checked={alertLog.length > 0 && alertLog.every((l) => selectedLogIds.has(l.log_id))}
                        ref={(el) => {
                          if (el) el.indeterminate =
                            alertLog.some((l) => selectedLogIds.has(l.log_id)) &&
                            !alertLog.every((l) => selectedLogIds.has(l.log_id));
                        }}
                        onChange={toggleSelectAll}
                      />
                    </th>
                    <th data-type="number">時間</th>
                    <th>股號</th>
                    <th data-type="number">損益</th>
                    <th data-type="number">損益%</th>
                    <th data-type="number">金額門檻</th>
                    <th data-type="number">百分比門檻</th>
                    <th>狀態</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {alertLog.map((l: AlertLogEntry) => (
                    <tr key={l.log_id}>
                      <td className={styles.alertLogCheckCell}>
                        <input
                          type="checkbox"
                          checked={selectedLogIds.has(l.log_id)}
                          onChange={() => toggleLogSelection(l.log_id)}
                        />
                      </td>
                      <td data-type="number">{l.triggered_at?.slice(0, 16) ?? '—'}</td>
                      <td>{l.stock_id || '—'}</td>
                      <td data-type="number" className={(l.pnl ?? 0) >= 0 ? styles.bullText : styles.bearText}>
                        {l.pnl != null ? formatNumber(l.pnl, { type: 'money' }) : '—'}
                      </td>
                      <td data-type="number" className={(l.pnl_pct ?? 0) >= 0 ? styles.bullText : styles.bearText}>
                        {l.pnl_pct != null
                          ? `${l.pnl_pct >= 0 ? '+' : ''}${l.pnl_pct.toFixed(2)}%`
                          : '—'}
                      </td>
                      <td data-type="number">
                        {l.threshold_type === 'amount' && l.threshold_value != null
                          ? formatNumber(l.threshold_value, { type: 'money' })
                          : '—'}
                      </td>
                      <td data-type="number">
                        {l.threshold_type === 'percent' && l.threshold_value != null
                          ? `${l.threshold_value.toFixed(2)}%`
                          : '—'}
                      </td>
                      <td>{l.sent ? '✅ 已發送' : l.reason === 'disabled' ? '🔴 停用' : l.reason === 'cooldown' ? '⏳ 冷卻' : '❌ 失敗'}</td>
                      <td className={styles.alertLogActionCell}>
                        <button
                          className={styles.singleDeleteBtn}
                          onClick={() => setConfirmDeleteId(l.log_id)}
                          disabled={deleting}
                          title="刪除此筆"
                        >
                          🗑
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {/* 分頁控件 */}
              <div className={styles.pagination}>
                <button 
                  onClick={() => fetchAlertLog(alertLogPagination.page - 1, alertPageSize)}
                  disabled={alertLogPagination.page <= 1}
                  className={styles.pageBtn}
                >
                  ← 上一頁
                </button>
                <span className={styles.pageInfo}>
                  {alertLogPagination.page} / {alertLogPagination.total_pages}
                </span>
                <button 
                  onClick={() => fetchAlertLog(alertLogPagination.page + 1, alertPageSize)}
                  disabled={alertLogPagination.page >= alertLogPagination.total_pages}
                  className={styles.pageBtn}
                >
                  下一頁 →
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {confirmDeleteId && (
        <div className={styles.modalBackdrop} onClick={() => setConfirmDeleteId(null)}>
          <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
            <h4 className={styles.modalTitle}>確認刪除</h4>
            <p className={styles.modalBody}>確定要刪除這筆通知記錄嗎？此操作無法復原。</p>
            <div className={styles.modalActions}>
              <button className={styles.modalCancel} onClick={() => setConfirmDeleteId(null)}>取消</button>
              <button className={styles.modalConfirm} onClick={() => deleteOneLog(confirmDeleteId)} disabled={deleting}>
                {deleting ? '刪除中…' : '確認刪除'}
              </button>
            </div>
          </div>
        </div>
      )}

      {confirmBatchDelete && (
        <div className={styles.modalBackdrop} onClick={() => setConfirmBatchDelete(false)}>
          <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
            <h4 className={styles.modalTitle}>確認批次刪除</h4>
            <p className={styles.modalBody}>確定要刪除選中的 <strong>{selectedLogIds.size}</strong> 筆通知記錄嗎？此操作無法復原。</p>
            <div className={styles.modalActions}>
              <button className={styles.modalCancel} onClick={() => setConfirmBatchDelete(false)}>取消</button>
              <button className={styles.modalConfirm} onClick={deleteBatchLogs} disabled={deleting}>
                {deleting ? '刪除中…' : '確認刪除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
