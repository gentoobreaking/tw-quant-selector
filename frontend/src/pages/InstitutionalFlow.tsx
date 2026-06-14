import { useState, useEffect } from 'react';
import { XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, ComposedChart, Bar, Line } from 'recharts';
import { fetchInstitutionalSummary, fetchInstitutionalFlows, fetchInstitutionalTop, type InstTopItem } from '../api/client';
import styles from './InstitutionalFlow.module.css';

export default function InstitutionalFlow() {
  const [summary, setSummary] = useState<{ foreign_net: number; sity_net: number; dealer_net: number } | null>(null);
  const [stockId, setStockId] = useState('2330');
  const [flows, setFlows] = useState<{ trade_date: string; foreign_net: number; sity_net: number; dealer_net: number; close: number | null }[]>([]);
  const [loading, setLoading] = useState(false);
  const [topData, setTopData] = useState<InstTopItem[]>([]);
  const [topLoading, setTopLoading] = useState(false);
  const [topSort, setTopSort] = useState<'total_net' | 'foreign_investors_net' | 'sity_investors_net' | 'dealer_net'>('total_net');
  const [topOrder, setTopOrder] = useState<'desc' | 'asc'>('desc');

  const SORT_LABELS: Record<string, string> = { total_net: '合計', foreign_investors_net: '外資', sity_investors_net: '投信', dealer_net: '自營商' };

  useEffect(() => {
    fetchInstitutionalSummary().then(setSummary).catch(() => {});
    loadFlows('2330');
    loadTop('total_net', 'desc');
  }, []);

  const loadFlows = (sid: string) => {
    setLoading(true);
    fetchInstitutionalFlows(sid).then(setFlows).catch(() => setFlows([])).finally(() => setLoading(false));
  };

  const loadTop = (sortBy: string, order: string) => {
    setTopLoading(true);
    fetchInstitutionalTop(10, '', sortBy, order)
      .then((res) => { setTopData(res.data); setTopLoading(false); })
      .catch(() => { setTopData([]); setTopLoading(false); });
  };

  const handleTopChange = (sortBy: string, order: string) => {
    setTopSort(sortBy as typeof topSort);
    setTopOrder(order as typeof topOrder);
    loadTop(sortBy, order);
  };

  const formatNet = (v: number) => `${v >= 0 ? '+' : ''}${(v / 1e8).toFixed(2)}億`;

  return (
    <div className={styles.page}>
      <h1>法人動向</h1>
      <div className={styles.kpiRow}>
        {(['foreign_net', 'sity_net', 'dealer_net'] as const).map(key => (
          <div key={key} className={styles.kpi}>
            <span className={styles.kpiLabel}>{key === 'foreign_net' ? '外資' : key === 'sity_net' ? '投信' : '自營商'}</span>
            <span className={styles.kpiValue} style={{ color: (summary?.[key] ?? 0) >= 0 ? '#d32f2f' : '#388e3c' }}>
              {summary ? formatNet(summary[key]) : '—'}
            </span>
          </div>
        ))}
      </div>

      <div className={styles.searchRow}>
        <input value={stockId} onChange={e => setStockId(e.target.value)} placeholder="股票代號" className={styles.input} />
        <button onClick={() => loadFlows(stockId)} className={styles.btn}>查詢</button>
      </div>

      {loading && <p className={styles.loading}>載入中...</p>}
      {!loading && flows.length === 0 && <p className={styles.empty}>無資料</p>}

      {flows.length > 0 && (
        <div className={styles.chartWrap}>
          <ResponsiveContainer width="100%" height={400}>
            <ComposedChart data={flows}>
              <XAxis dataKey="trade_date" tick={{ fontSize: 10 }} />
              <YAxis yAxisId="left" />
              <YAxis yAxisId="right" orientation="right" />
              <Tooltip />
              <Legend />
              <Bar yAxisId="left" dataKey="foreign_net" fill="#1976d2" name="外資" />
              <Bar yAxisId="left" dataKey="sity_net" fill="#388e3c" name="投信" />
              <Bar yAxisId="left" dataKey="dealer_net" fill="#f57c00" name="自營商" />
              <Line yAxisId="right" type="monotone" dataKey="close" stroke="#d32f2f" name="收盤價" dot={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* TopN 買賣超排行 */}
      <div className={styles.topSection}>
        <h2>📋 買賣超排行</h2>
        <div className={styles.topControls}>
          {(Object.entries(SORT_LABELS) as [string, string][]).map(([key, label]) => {
            const isActive = topSort === key && topOrder === 'desc';
            return (
              <button
                key={key}
                className={`${styles.topBtn} ${topSort === key ? styles.topBtnActive : ''}`}
                onClick={() => handleTopChange(key, topSort === key && topOrder === 'desc' ? 'asc' : 'desc')}
              >
                {isActive ? `🟢 ${label}買超` : topSort === key ? `🔴 ${label}賣超` : label}
              </button>
            );
          })}
        </div>
        {topLoading && <p className={styles.loading}>載入中...</p>}
        {!topLoading && topData.length === 0 && <p className={styles.empty}>無排行資料</p>}
        {topData.length > 0 && (
          <table className={styles.topTable}>
            <thead>
              <tr>
                <th>#</th>
                <th>股票</th>
                <th>外資</th>
                <th>投信</th>
                <th>自營</th>
                <th>合計</th>
                <th>收盤</th>
              </tr>
            </thead>
            <tbody>
              {topData.map((item, i) => (
                <tr key={item.stock_id}>
                  <td className={styles.rankCell}>{i + 1}</td>
                  <td className={styles.nameCell}>
                    <span className={styles.stockId}>{item.stock_id}</span>
                    <span className={styles.stockName}>{item.stock_name}</span>
                  </td>
                  <td style={{ color: item.foreign_net >= 0 ? 'var(--color-bear-text)' : 'var(--color-positive)' }}>
                    {formatNet(item.foreign_net)}
                  </td>
                  <td style={{ color: item.sity_net >= 0 ? 'var(--color-bear-text)' : 'var(--color-positive)' }}>
                    {formatNet(item.sity_net)}
                  </td>
                  <td style={{ color: item.dealer_net >= 0 ? 'var(--color-bear-text)' : 'var(--color-positive)' }}>
                    {formatNet(item.dealer_net)}
                  </td>
                  <td style={{ fontWeight: 600, color: item.total_net >= 0 ? 'var(--color-bear-text)' : 'var(--color-positive)' }}>
                    {formatNet(item.total_net)}
                  </td>
                  <td className={styles.closeCell}>{item.close?.toFixed(2) ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
