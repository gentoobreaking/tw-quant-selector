import { useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, CartesianGrid, LineChart, Line } from 'recharts';
import { fetchIcAnalysis, fetchQuintileReturns, fetchFactorCorrelation, runInstitutionalValidation } from '../api/client';
import styles from './FactorResearch.module.css';

type IcPoint = { signal_date: string; strategy: string; ic: number };

export default function FactorResearch() {
  const [tab, setTab] = useState<'ic' | 'quintile' | 'correlation' | 'inst'>('ic');
  const [icData, setIcData] = useState<IcPoint[]>([]);
  const [quintileData, setQuintileData] = useState<{ strategy: string; quintile: number; avg_return: number }[]>([]);
  const [corrData, setCorrData] = useState<{ strategies: string[]; matrix: (number | null)[][] } | null>(null);
  const [instVal, setInstVal] = useState<{ buy: { count: number; avg_excess_return: number }; sell: { count: number; avg_excess_return: number } } | null>(null);
  const [instDays, setInstDays] = useState(10);
  const [loading, setLoading] = useState(false);

  const loadTab = (t: string) => {
    setLoading(true);
    setTab(t as typeof tab);
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

  const strategies = [...new Set(icData.map(d => d.strategy))];

  return (
    <div className={styles.page}>
      <h1>因子研究</h1>
      <div className={styles.tabs}>
        {(['ic', 'quintile', 'correlation', 'inst'] as const).map(t => (
          <button key={t} className={`${styles.tab} ${tab === t ? styles.active : ''}`} onClick={() => loadTab(t)}>
            {t === 'ic' ? 'IC 分析' : t === 'quintile' ? '分層報酬' : t === 'correlation' ? '相關性' : '法人驗證'}
          </button>
        ))}
      </div>

      {loading && <p className={styles.loading}>載入中...</p>}

      {!loading && tab === 'ic' && (
        <div className={styles.section}>
          <h2>因子 IC 時序</h2>
          {strategies.map(s => (
            <div key={s} className={styles.chartBox}>
              <h3>{s}</h3>
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

      {!loading && tab === 'quintile' && quintileData.length > 0 && (
        <div className={styles.section}>
          <h2>因子分層報酬 (20日後)</h2>
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
        </div>
      )}

      {!loading && tab === 'correlation' && corrData && (
        <div className={styles.section}>
          <h2>因子相關性矩陣</h2>
          <table className={styles.corrTable}>
            <thead>
              <tr><th></th>{corrData.strategies.map(s => <th key={s}>{s}</th>)}</tr>
            </thead>
            <tbody>
              {corrData.strategies.map((s, i) => (
                <tr key={s}>
                  <td><strong>{s}</strong></td>
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
      )}

      {!loading && tab === 'inst' && (
        <div className={styles.section}>
          <h2>法人因子驗證</h2>
          <div className={styles.instCtrl}>
            <label>觀察天數:</label>
            <select value={instDays} onChange={e => setInstDays(Number(e.target.value))}>
              {[1, 5, 10, 20].map(n => <option key={n} value={n}>{n}天</option>)}
            </select>
            <button onClick={() => loadTab('inst')} className={styles.btn}>計算</button>
          </div>
          {instVal && (
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
          )}
        </div>
      )}
    </div>
  );
}
