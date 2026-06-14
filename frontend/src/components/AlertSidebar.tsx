import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import type { AlertMessage } from '../types';
import styles from './AlertSidebar.module.css';

const SEVERITY_ORDER: Record<string, number> = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 };
const ALERT_ICONS: Record<string, string> = {
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

interface AlertSidebarProps {
  open: boolean;
  onClose: () => void;
  alerts: AlertMessage['data'][];
  onMarkAllRead: () => void;
  onClear: () => void;
}

export default function AlertSidebar({ open, onClose, alerts, onMarkAllRead, onClear }: AlertSidebarProps) {
  const navigate = useNavigate();
  const [filterSeverity, setFilterSeverity] = useState<string>('all');
  const [filterType, setFilterType] = useState<string>('all');

  const severityOptions = useMemo(() => {
    const set = new Set(alerts.map((a) => a.severity));
    return ['all', ...Array.from(set).sort((a, b) => (SEVERITY_ORDER[a] ?? 9) - (SEVERITY_ORDER[b] ?? 9))];
  }, [alerts]);

  const typeOptions = useMemo(() => {
    const set = new Set(alerts.map((a) => a.alert_type));
    return ['all', ...Array.from(set).sort()];
  }, [alerts]);

  const filtered = useMemo(() => {
    let items = alerts.slice(0, 20);
    if (filterSeverity !== 'all') items = items.filter((a) => a.severity === filterSeverity);
    if (filterType !== 'all') items = items.filter((a) => a.alert_type === filterType);
    return items;
  }, [alerts, filterSeverity, filterType]);

  return (
    <aside className={`${styles.sidebar} ${open ? styles.open : styles.closed}`}>
      <div className={styles.header}>
        <h3 className={styles.title}>智慧警示</h3>
        <button className={styles.closeBtn} onClick={onClose} title="關閉">✕</button>
      </div>
      <div className={styles.actions}>
        <button className={styles.actionBtn} onClick={onMarkAllRead}>已讀全部</button>
        <button className={styles.actionBtn} onClick={onClear}>清空</button>
      </div>
      <div className={styles.filters}>
        <select className={styles.filterSelect} value={filterSeverity} onChange={(e) => setFilterSeverity(e.target.value)}>
          {severityOptions.map((s) => (
            <option key={s} value={s}>{s === 'all' ? '全部層級' : s}</option>
          ))}
        </select>
        <select className={styles.filterSelect} value={filterType} onChange={(e) => setFilterType(e.target.value)}>
          {typeOptions.map((t) => (
            <option key={t} value={t}>{t === 'all' ? '全部類型' : t}</option>
          ))}
        </select>
      </div>
      <div className={styles.list}>
        {filtered.length === 0 && (
          <div className={styles.empty}>目前無警示</div>
        )}
        {filtered.map((alert, i) => {
          const icon = ALERT_ICONS[alert.alert_type] || '🔔';
          const sevClass = styles[`sev_${alert.severity.toLowerCase()}`] || '';
          return (
            <div
              key={`${alert.stock_id}-${i}`}
              className={`${styles.item} ${sevClass}`}
              onClick={() => { navigate(`/signals/${alert.stock_id}`); onClose(); }}
              role="button"
              tabIndex={0}
            >
              <span className={styles.itemIcon}>{icon}</span>
              <div className={styles.itemBody}>
                <div className={styles.itemHeader}>
                  <span className={styles.itemStock}>{alert.stock_name || alert.stock_id}</span>
                  <span className={`${styles.itemSeverity} ${sevClass}`}>{alert.severity}</span>
                </div>
                <div className={styles.itemMsg}>{alert.alert_type}</div>
              </div>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
