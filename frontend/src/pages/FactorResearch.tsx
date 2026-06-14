import { useState, useEffect, useRef } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, CartesianGrid, LineChart, Line } from 'recharts';
import { fetchIcAnalysis, fetchQuintileReturns, fetchFactorCorrelation, runInstitutionalValidation } from '../api/client';
import styles from './FactorResearch.module.css';

type IcPoint = { signal_date: string; strategy: string; ic: number };

const TABS = ['ic', 'quintile', 'correlation', 'inst'] as const;
type Tab = (typeof TABS)[number];

const TAB_LABELS: Record<Tab, string> = {
  ic: 'IC 分析',
  quintile: '分層報酬',
  correlation: '相關性',
  inst: '法人驗證',
};

const STRATEGY_LABELS: Record<string, string> = {
  composite: '綜合',
  momentum: '動能',
  value: '價值',
  quality: '品質',
  growth: '成長',
  guru: '大師',
  institutional: '法人',
};

function strategyLabel(s: string): string {
  return STRATEGY_LABELS[s] ?? s;
}

export default function FactorResearch() {
  const [tab, setTab] = useState<Tab>('ic');
  const [icData, setIcData] = useState<IcPoint[]>([]);
  const [quintileData, setQuintileData] = useState<{ strategy: string; quintile: number; avg_return: number }[]>([]);
  const [corrData, setCorrData] = useState<{ strategies: string[]; matrix: (number | null)[][] } | null>(null);
  const [instVal, setInstVal] = useState<{ buy: { count: number; avg_excess_return: number }; sell: { count: number; avg_excess_return: number } } | null>(null);
  const [instDays, setInstDays] = useState(10);
  const [loading, setLoading] = useState(false);

  // Track which tabs we've already loaded so switching doesn't re-fetch
  const loadedTabs = useRef(new Set<Tab>());

  const fetchTab = (t: Tab) => {
    setLoading(true);
    setTab(t);
    if (t === 'ic') {
      fetchIcAnalysis().then(d => setIcData(d)).catch(() => setIcData([])).finally(() => setLoading(false));
    } else if (t === 'quintile') {
      fetchQuintileReturns().then(d => setQuintileData(d)).catch(() => setQuintileData([])).finally(() => setLoading(false));
    } else if (t === 'correlation') {
      fetchFactorCorrelation().then(d => setCorrData(d)).catch(() => setCorrData(null)).finally(() => setLoading(false));
    } else if (t === 'inst') {
      runInstitutionalValidation(instDays).then(d => setInstVal(d)).catch(() => setInstVal(null)).finally(() => setLoading(false));
    }
  };

  const loadTab = (t: Tab, force = false) => {
    if (!force && loadedTabs.current.has(t)) {
      setTab(t);
      return;
    }
    loadedTabs.current.add(t);
    if (t === 'inst' && !force) {
      setTab(t);
      return;
    }
    fetchTab(t);
  };

  useEffect(() => {
    loadTab('ic');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const strategies = [...new Set(icData.map(d => d.strategy))];

  const renderEmptyState = (message = '目前尚無資料') => (
    <p className={styles.empty}>{message}</p>
  );

  return (
    <div className={styles.page}>
      <h1>因子研究</h1>
      <div className={styles.tabs}>
        {TABS.map(t => (
          <button
            key={t}
            className={`${styles.tab} ${tab === t ? styles.active : ''}`}
            onClick={() => loadTab(t)}
          >
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>

      {loading && <p className={styles.loading}>載入中...</p>}

      {/* ── IC Analysis ── */}
      {!loading && tab === 'ic' && (
        <div className={styles.section}>
          <h2>因子 IC 時序</h2>
          {strategies.length === 0
            ? renderEmptyState()
            : strategies.map(s => (
                <div key={s} className={styles.chartBox}>
                  <h3>{strategyLabel(s)}</h3>
                  <ResponsiveContainer width="100%" height={200}>
                    <LineChart data={icData.filter(d => d.strategy === s)}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="signal_date" tick={{ fontSize: 10 }} />
                      <YAxis domain={[-1, 1]} />
                      <Tooltip />
                      <Line type="monotone" dataKey="ic" stroke="#1976d2" dot={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              ))}
        </div>
      )}

      {/* ── Quintile Returns ── */}
      {!loading && tab === 'quintile' && (
        <div className={styles.section}>
          <h2>因子分層報酬（T+20 日）</h2>
          <p className={styles.corrDesc}>
            將各因子分數由高到低切分為五等分（Q1–Q5），計算每一組在 20 個交易日後的平均報酬。
            若高分組報酬明顯高於低分組，表示該因子具有良好的預測區分力。
            {quintileData.length === 0 && ' 目前因歷史股價資料涵蓋不足，尚無足夠數據計算分層報酬，請等待 daily_prices 補全。'}
          </p>
          {quintileData.length === 0 ? (
            renderEmptyState()
          ) : (
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={quintileData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="quintile" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Bar dataKey="avg_return" fill="#1976d2" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      )}

      {/* ── Correlation Matrix ── */}
      {!loading && tab === 'correlation' && (
        <div className={styles.section}>
          <h2>因子相關性矩陣</h2>
          <p className={styles.corrDesc}>
            此矩陣顯示各因子之間分數的皮爾森相關係數（Pearson r）。數值愈接近 ±1 表示因子高度共線，
            選股時可考慮降低重疊；數值接近 0 表示因子彼此獨立，組合後可增加多元性。
          </p>
          {corrData ? (
            <div className={styles.corrContainer}>
              <table className={styles.corrTable}>
                <thead>
                  <tr><th></th>{corrData.strategies.map(s => <th key={s}>{strategyLabel(s)}</th>)}</tr>
                </thead>
                <tbody>
                  {corrData.strategies.map((s, i) => (
                    <tr key={s}>
                      <td><strong>{strategyLabel(s)}</strong></td>
                      {corrData.matrix[i].map((v, j) => (
                        <td key={j} style={{
                          background: v !== null ? `rgba(25, 118, 210, ${Math.abs(v)})` : undefined,
                          color: v !== null && v < 0 ? '#d32f2f' : undefined,
                        }}>
                          {v !== null ? v.toFixed(2) : '—'}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            renderEmptyState()
          )}
        </div>
      )}

      {/* ── Institutional Validation ── */}
      {!loading && tab === 'inst' && (
        <div className={styles.section}>
          <h2>法人因子驗證</h2>
          <div className={styles.instCtrl}>
            <label>觀察天數:</label>
            <select value={instDays} onChange={e => setInstDays(Number(e.target.value))}>
              {[1, 5, 10, 20].map(n => <option key={n} value={n}>{n}天</option>)}
            </select>
            <button onClick={() => loadTab('inst', true)} className={styles.btn}>計算</button>
          </div>
          {instVal ? (
            <div className={styles.instResult}>
              <div className={styles.instCard}>
                <span className={styles.instLabel}>買超後平均報酬</span>
                <span className={styles.instValue}>{(instVal.buy.avg_excess_return * 100).toFixed(2)}%</span>
                <span className={styles.instCount}>({instVal.buy.count} 次)</span>
              </div>
              <div className={styles.instCard}>
                <span className={styles.instLabel}>賣超後平均報酬</span>
                <span className={styles.instValue}>{(instVal.sell.avg_excess_return * 100).toFixed(2)}%</span>
                <span className={styles.instCount}>({instVal.sell.count} 次)</span>
              </div>
            </div>
          ) : (
            renderEmptyState('點擊「計算」按鈕進行法人因子驗證')
          )}
        </div>
      )}
    </div>
  );
}