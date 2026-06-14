import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from 'react';
import styles from './Toast.module.css';

type Severity = 'critical' | 'high' | 'medium' | 'low';

interface Toast {
  id: string;
  message: string;
  severity: Severity;
  duration: number | null;
}

interface ToastCtx {
  toasts: Toast[];
  addToast: (msg: string, severity?: Severity) => void;
  removeToast: (id: string) => void;
}

const ToastContext = createContext<ToastCtx>({ toasts: [], addToast: () => {}, removeToast: () => {} });

export function useToast() {
  return useContext(ToastContext);
}

const SEVERITY_DURATION: Record<Severity, number | null> = {
  critical: null,
  high: 10000,
  medium: 5000,
  low: 5000,
};

const SEVERITY_BORDER: Record<Severity, string> = {
  critical: 'var(--color-bear)',
  high: 'var(--color-bear-text)',
  medium: 'var(--color-accent)',
  low: 'var(--color-accent)',
};

let nextId = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const removeToast = useCallback((id: string) => {
    setToasts((t) => t.filter((x) => x.id !== id));
  }, []);

  const addToast = useCallback((message: string, severity: Severity = 'medium') => {
    const id = `toast-${++nextId}`;
    const duration = SEVERITY_DURATION[severity];
    setToasts((t) => {
      const next = [...t, { id, message, severity, duration }];
      return next.length > 3 ? next.slice(-3) : next;
    });
  }, []);

  return (
    <ToastContext.Provider value={{ toasts, addToast, removeToast }}>
      {children}
      <div className={styles.container}>
        {toasts.map((t) => (
          <ToastItem key={t.id} {...t} onDismiss={() => removeToast(t.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastItem({ id: _id, message, severity, duration, onDismiss }: Toast & { onDismiss: () => void }) {
  useEffect(() => {
    if (duration == null) return;
    const timer = setTimeout(onDismiss, duration);
    return () => clearTimeout(timer);
  }, [duration, onDismiss]);

  return (
    <div
      className={styles.toast}
      style={{ borderLeftColor: SEVERITY_BORDER[severity] }}
      role="alert"
    >
      <div className={styles.body}>{message}</div>
      <button className={styles.dismiss} onClick={onDismiss}>✕</button>
    </div>
  );
}
