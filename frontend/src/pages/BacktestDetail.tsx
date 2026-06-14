import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { fetchBacktestDetail, type BacktestDetail as BacktestDetailData } from '../api/client';
import StatCard from '../components/StatCard';
import SkeletonLoader from '../components/SkeletonLoader';
import EmptyState from '../components/EmptyState';
import { formatNumber } from '../utils/format';
import styles from './BacktestDetail.module.css';

export default function BacktestDetail() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const [data, setData] = useState<BacktestDetailData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!runId) return;
    setLoading(true);
    setError(false);
    fetchBacktestDetail(runId)
      .then((d) => { setData(d); setLoading(false); })
      .catch(() => { setError(true); setLoading(false); });
  }, [runId]);

  if (loading) {
    return (
      <div className={styles.page}>
        <SkeletonLoader variant="card" />
        <div className={styles.metricsGrid}>
          {[1, 2, 3, 4, 5, 6].map((i) => <SkeletonLoader key={i} variant="card" />)}
        </div>
        <SkeletonLoader variant="table" rows={5} />
      </div>
    );
  }

  if (error) {
    return (
      <div className={styles.page}>
        <EmptyState scenario="failed" onRetry={() => window.location.reload()}>
          無法載入回測結果
        </EmptyState>
      </div>
    );
  }

  if (!data) {
    return (
      <div className={styles.page}>
        <EmptyState scenario="notrade">回測資料不存在</EmptyState>
      </div>
    );
  }

  const { metrics, trades, created_at, start_date, end_date } = data;
  const totalReturn = metrics.total_return;
  const cagr = metrics.cagr;
  const md = metrics.max_drawdown;
  const sharpe = metrics.sharpe;
  const calmar = metrics.calmar;

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <button className={`${styles.backBtn} no-print`} onClick={() => navigate('/backtest')}>← 回列表</button>
        <div className={styles.headerInfo}>
          <h1 className={styles.title}>回測詳情</h1>
          <span className={styles.runId}>{runId}</span>
        </div>
        <div className={styles.headerMeta}>
          <span>建立：{created_at?.slice(0, 19).replace('T', ' ') || '—'}</span>
          <span>區間：{start_date || '?'} ~ {end_date || '?'}</span>
        </div>
        <button className={`${styles.printBtn} no-print`} onClick={() => window.print()}>🖨 列印報告</button>
      </div>

      <div className={styles.metricsGrid}>
        <StatCard label="總報酬率" value={totalReturn != null ? totalReturn * 100 : 0} format="percent" variant={totalReturn != null && totalReturn > 0 ? 'highlight' : 'alert'} delta={totalReturn ?? undefined} />
        <StatCard label="年化報酬 (CAGR)" value={cagr != null ? cagr * 100 : 0} format="percent" variant={cagr != null && cagr > 0 ? 'highlight' : 'alert'} />
        <StatCard label="最大回撤 (MDD)" value={md != null ? md * 100 : 0} format="percent" variant={md != null && md < -20 ? 'alert' : 'default'} delta={md != null ? -md : undefined} />
        <StatCard label="夏普比率" value={sharpe ?? 0} format="raw" variant={sharpe != null && sharpe > 1 ? 'highlight' : 'default'} />
        <StatCard label="卡瑪比率" value={calmar ?? 0} format="raw" />
        <StatCard label="交易次數" value={metrics.total_trades} format="number" />
        <StatCard label="換手率" value={metrics.turnover != null ? metrics.turnover : 0} format="percent" />
      </div>

      <div className={styles.section}>
        <h3>交易明細 ({trades.length})</h3>
        {trades.length === 0 ? (
          <p className={styles.muted}>尚無交易記錄</p>
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.tradeTable}>
              <thead>
                <tr>
                  <th>日期</th>
                  <th>股號</th>
                  <th>動作</th>
                  <th>股數</th>
                  <th>價格</th>
                  <th>成交值</th>
                  <th>權重</th>
                </tr>
              </thead>
              <tbody>
                {trades.slice(0, 200).map((t, i) => (
                  <tr key={i}>
                    <td>{t.date}</td>
                    <td><span className={styles.stockLink} onClick={() => navigate(`/signals/${t.stock_id}`)}>{t.stock_id}</span></td>
                    <td><span className={t.action === 'buy' ? styles.actionBuy : styles.actionSell}>{t.action === 'buy' ? '買' : '賣'}</span></td>
                    <td className="font-data">{t.shares.toLocaleString()}</td>
                    <td className="font-data">{t.price != null ? formatNumber(t.price, { type: 'price' }) : '—'}</td>
                    <td className="font-data">{t.value != null ? formatNumber(t.value, { type: 'market_cap' }) : '—'}</td>
                    <td className="font-data">{t.weight != null ? (t.weight * 100).toFixed(1) + '%' : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
