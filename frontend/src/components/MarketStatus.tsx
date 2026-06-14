import { useEffect, useState } from 'react';
import { getMarketStatus, type MarketStatusInfo } from '../utils/format';
import styles from './MarketStatus.module.css';

const STATUS_CONFIG: Record<string, { icon: string; label: string }> = {
  trading: { icon: '🟢', label: '交易中' },
  pre_market: { icon: '🟡', label: '已收盤' },
  post_market: { icon: '🟠', label: '收盤中' },
  closed: { icon: '⚫', label: '已收盤' },
  holiday: { icon: '🔴', label: '休市中' },
};

export default function MarketStatus({ compact }: { compact?: boolean }) {
  const [statusInfo, setStatusInfo] = useState<MarketStatusInfo>(() => getMarketStatus());

  useEffect(() => {
    const timer = setInterval(() => {
      setStatusInfo(getMarketStatus());
    }, 60000);

    return () => clearInterval(timer);
  }, []);

  const config = STATUS_CONFIG[statusInfo.status] || STATUS_CONFIG.closed;

  return (
    <div className={`${styles.container} ${styles[statusInfo.status]} ${compact ? styles.compact : ''}`}>
      <div className={styles.statusRow}>
        <span className={styles.icon}>{config.icon}</span>
        <span className={styles.label}>{config.label}</span>
        {!compact && (
          <>
            <span className={styles.updateTime}>{statusInfo.lastUpdated}</span>
            {statusInfo.status !== 'trading' && (
              <span className={styles.nextOpen}>{statusInfo.nextOpen}</span>
            )}
          </>
        )}
      </div>
    </div>
  );
}
