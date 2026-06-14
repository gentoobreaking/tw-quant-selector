import { Link } from 'react-router-dom';
import styles from './EmptyState.module.css';

type Scenario = 'initial' | 'filter' | 'notrade' | 'failed';

interface Props {
  scenario?: Scenario;
  icon?: string;
  title?: string;
  message?: string;
  children?: React.ReactNode;
  reasons?: string[];
  actionLabel?: string;
  actionHref?: string;
  onAction?: () => void;
  onRetry?: () => void;
}

const SCENARIO_ICONS: Record<Scenario, string> = {
  initial: `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>`,
  filter: `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>`,
  notrade: `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>`,
  failed: `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`,
};

export default function EmptyState({
  scenario,
  icon,
  title,
  message,
  children,
  reasons,
  actionLabel,
  actionHref = '/monitor',
  onAction,
  onRetry,
}: Props) {
  const iconHtml = scenario ? SCENARIO_ICONS[scenario] : null;
  const handleAction = onAction || onRetry;
  const resolvedActionLabel = actionLabel || (onRetry ? '重新嘗試' : '前往資料監控');

  return (
    <div className={styles.root}>
      {iconHtml ? (
        <span className={styles.icon} dangerouslySetInnerHTML={{ __html: iconHtml }} />
      ) : (
        <span className={styles.icon}>{icon || ''}</span>
      )}
      {title && <h3 className={styles.title}>{title}</h3>}
      <p className={styles.message}>{message || children}</p>
      {reasons && reasons.length > 0 && (
        <ul className={styles.reasons}>
          {reasons.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}
      {handleAction ? (
        <button className={styles.action} onClick={handleAction}>{resolvedActionLabel}</button>
      ) : (
        actionHref.startsWith('/') ? (
          <Link className={styles.action} to={actionHref}>{resolvedActionLabel}</Link>
        ) : (
          <a className={styles.action} href={actionHref}>{resolvedActionLabel}</a>
        )
      )}
    </div>
  );
}
