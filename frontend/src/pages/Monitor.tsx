import { useState, useEffect } from 'react';
import { fetchMonitorLogs, apiFetch, type LogEntry } from '../api/client';
import { DATASET_LABELS } from '../utils/color';
import SkeletonLoader from '../components/SkeletonLoader';
import styles from './Monitor.module.css';

interface MonitorStatus {
  system: {
    total_prices: number;
    polling_active: boolean;
    last_active_snapshot: string | null;
  };
  datasets: {
    dataset: string;
    ok_count: number;
    total_count: number;
    last_updated: string | null;
  }[];
}

export default function Monitor() {
  const [status, setStatus] = useState<MonitorStatus | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      const [s, l] = await Promise.all([
        apiFetch<MonitorStatus>('/api/v1/monitor/status'),
        fetchMonitorLogs().catch(() => [] as LogEntry[]),
      ]);
      setStatus(s);
      setLogs(l);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
  }, []);

  if (loading) return <div className={styles.page}><SkeletonLoader variant="card" /><SkeletonLoader variant="table" rows={6} /></div>;

  return (
    <div className={styles.page}>
      <h1 className={styles.title}>系統監控 Dashboard</h1>

      {/* Overview Cards */}
      <div className={styles.statsGrid}>
        <div className={styles.statCard}>
          <h3>Polling 服務</h3>
          <div className={`${styles.statusBadge} ${status?.system.polling_active ? styles.ok : styles.err}`}>
            {status?.system.polling_active ? '● 運作中' : '○ 已停止'}
          </div>
          <p className={styles.subText}>最後快照: {status?.system.last_active_snapshot ?? '無資料'}</p>
        </div>
        <div className={styles.statCard}>
          <h3>資料庫總筆數</h3>
          <div className={styles.statValue}>{status?.system.total_prices.toLocaleString()}</div>
          <p className={styles.subText}>daily_prices</p>
        </div>
      </div>

      {/* Datasets */}
      <h2 className={styles.sectionTitle}>資料集進度</h2>
      <div className={styles.datasetsGrid}>
        {status?.datasets.map((ds) => (
          <div key={ds.dataset} className={styles.dsCard}>
            <div className={styles.dsHeader}>
              <span>{DATASET_LABELS[ds.dataset] || ds.dataset}</span>
              <span className={ds.ok_count === ds.total_count ? styles.okText : styles.errText}>
                {ds.ok_count === ds.total_count ? '✓' : '⟳'}
              </span>
            </div>
            <div className={styles.dsCount}>
              {(ds.ok_count ?? 0).toLocaleString()} / {(ds.total_count ?? 0).toLocaleString()}
            </div>
            <div className={styles.dsUpdate}>更新: {ds.last_updated?.slice(0, 16) ?? '—'}</div>
          </div>
        ))}
      </div>

      {/* Logs */}
      <h2 className={styles.sectionTitle}>近期操作日誌</h2>
      <div className={styles.tableWrapper}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>時間</th><th>模組</th><th>事件</th><th>狀態</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((log, i) => (
              <tr key={log.id ?? i}>
                <td>{log.timestamp?.slice(0, 16)}</td>
                <td>{log.module}</td>
                <td>{log.event}</td>
                <td>{log.severity === 'info' ? '✓' : log.severity === 'warn' ? '⚠' : '✗'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
