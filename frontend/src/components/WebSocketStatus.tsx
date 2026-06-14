import type { WsStatus } from '../hooks/useWebSocket';
import styles from './WebSocketStatus.module.css';

interface Props {
  status: WsStatus;
}

const STATUS_LABELS: Record<WsStatus, string> = {
  connected: '已連線',
  connecting: '連線中',
  disconnected: '離線',
};

export default function WebSocketStatus({ status }: Props) {
  return (
    <span
      className={`${styles.badge} ${styles[status]}`}
      title={STATUS_LABELS[status]}
    >
      <span className={styles.dot} />
      {STATUS_LABELS[status]}
    </span>
  );
}
