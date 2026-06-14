import { Component, type ReactNode } from 'react';

type ErrorBoundaryLevel = 'component' | 'page' | 'fullscreen';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  level?: ErrorBoundaryLevel;
  name?: string;
  onRetry?: () => void;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

type ErrorCategory = 'network' | 'not_found' | 'server_error' | 'timeout' | 'unknown';

function classifyError(error: Error): ErrorCategory {
  const msg = error.message || '';
  if (msg.includes('NetworkError') || msg.includes('Failed to fetch') || msg.includes('fetch')) return 'network';
  if (msg.includes('404') || msg.includes('Not Found')) return 'not_found';
  if (msg.includes('500') || msg.includes('503') || msg.includes('502')) return 'server_error';
  if (msg.includes('timeout') || msg.includes('Timeout') || msg.includes('abort')) return 'timeout';
  return 'unknown';
}

const ERROR_MESSAGES: Record<ErrorCategory, { title: string; reason: string; action: string }> = {
  network: {
    title: '網路連線中斷',
    reason: '無法連接到伺服器，可能是網路不穩定或伺服器暫時離線。',
    action: '請檢查網路連線後重試。',
  },
  not_found: {
    title: '查無資料',
    reason: '請求的資料不存在，可能已被移除或連結有誤。',
    action: '請確認連結是否正確，或回到首頁重新開始。',
  },
  server_error: {
    title: '伺服器暫時無法處理請求',
    reason: '伺服器發生暫時性錯誤，這通常會自動恢復。',
    action: '請稍後再試，若持續發生請聯繫技術人員。',
  },
  timeout: {
    title: '請求逾時',
    reason: '伺服器回應時間過長，可能暫時負載過高。',
    action: '請稍後再試，或重新整理頁面。',
  },
  unknown: {
    title: '發生意外錯誤',
    reason: '系統發生非預期錯誤，我們已記錄此問題。',
    action: '請重新整理頁面，若持續發生請聯繫技術人員。',
  },
};

const COMPACT_STYLE: React.CSSProperties = {
  padding: '16px',
  textAlign: 'center',
  color: 'var(--text-muted)',
  border: '1px solid var(--bg-border)',
  borderRadius: 'var(--radius-card)',
  background: 'var(--bg-overlay)',
  fontSize: '12px',
};

const PAGE_STYLE: React.CSSProperties = {
  padding: '32px',
  textAlign: 'center',
  maxWidth: '480px',
  margin: '40px auto',
};

const FULLSCREEN_CONTAINER: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  height: '100vh',
  width: '100vw',
  background: 'var(--bg-base)',
  padding: '24px',
};

const FULLSCREEN_CARD: React.CSSProperties = {
  textAlign: 'center',
  maxWidth: '420px',
};

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
    this.handleRetry = this.handleRetry.bind(this);
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error) {
    console.error(`[ErrorBoundary${this.props.name ? `:${this.props.name}` : ''}]`, error);
  }

  handleRetry() {
    this.setState({ hasError: false, error: null });
    this.props.onRetry?.();
  }

  renderComponentError(error: Error) {
    if (this.props.fallback) return this.props.fallback;
    const cat = classifyError(error);
    const msg = ERROR_MESSAGES[cat];
    return (
      <div style={COMPACT_STYLE} role="alert">
        <div style={{ marginBottom: '8px' }}>
          <span style={{ color: 'var(--color-negative)', fontSize: '13px', fontWeight: 500 }}>⚠ </span>
          <span style={{ fontSize: '13px' }}>{msg.title}</span>
        </div>
        {this.props.name && (
          <div style={{ fontSize: '11px', marginBottom: '8px', color: 'var(--text-muted)' }}>
            {this.props.name}
          </div>
        )}
        <button onClick={this.handleRetry} style={{
          padding: '4px 16px', fontSize: '12px', background: 'var(--bg-elevated)',
          border: '1px solid var(--bg-border)', borderRadius: 'var(--radius-btn)',
          color: 'var(--text-primary)', cursor: 'pointer',
        }}>
          重試
        </button>
      </div>
    );
  }

  renderPageError(error: Error) {
    const cat = classifyError(error);
    const msg = ERROR_MESSAGES[cat];
    return (
      <div style={PAGE_STYLE} role="alert">
        <div style={{ fontSize: '32px', marginBottom: '12px' }}>⚠️</div>
        <h2 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '8px', color: 'var(--text-primary)' }}>
          {msg.title}
        </h2>
        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '6px', lineHeight: 1.5 }}>
          {msg.reason}
        </p>
        <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '20px', lineHeight: 1.5 }}>
          {msg.action}
        </p>
        <div style={{ display: 'flex', gap: '8px', justifyContent: 'center', marginBottom: '16px' }}>
          <button onClick={this.handleRetry} style={{
            padding: '8px 24px', fontSize: '13px', fontWeight: 500,
            background: 'var(--color-accent)', border: 'none',
            borderRadius: 'var(--radius-btn)', color: '#fff', cursor: 'pointer',
          }}>
            重試
          </button>
          <button onClick={() => window.location.reload()} style={{
            padding: '8px 24px', fontSize: '13px',
            background: 'var(--bg-elevated)', border: '1px solid var(--bg-border)',
            borderRadius: 'var(--radius-btn)', color: 'var(--text-primary)', cursor: 'pointer',
          }}>
            重新整理
          </button>
        </div>
        <details style={{ fontSize: '11px', color: 'var(--text-muted)', cursor: 'pointer' }}>
          <summary style={{ marginBottom: '4px' }}>技術細節</summary>
          <pre style={{
            textAlign: 'left', padding: '8px', background: 'var(--bg-overlay)',
            borderRadius: 'var(--radius-card)', overflowX: 'auto',
            fontSize: '11px', lineHeight: 1.4, whiteSpace: 'pre-wrap',
          }}>
            {error.name}: {error.message}
            {error.stack ? `\n\n${error.stack}` : ''}
          </pre>
        </details>
      </div>
    );
  }

  renderFullscreenError(error: Error) {
    const cat = classifyError(error);
    const msg = ERROR_MESSAGES[cat];
    return (
      <div style={FULLSCREEN_CONTAINER} role="alert">
        <div style={FULLSCREEN_CARD}>
          <div style={{ fontSize: '48px', marginBottom: '16px' }}>⚠️</div>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '12px', color: 'var(--text-primary)' }}>
            系統發生錯誤
          </h1>
          <p style={{ fontSize: '14px', color: 'var(--text-secondary)', marginBottom: '8px', lineHeight: 1.5 }}>
            {msg.reason}
          </p>
          <p style={{ fontSize: '14px', color: 'var(--text-muted)', marginBottom: '24px', lineHeight: 1.5 }}>
            {msg.action}
          </p>
          <button onClick={() => window.location.reload()} style={{
            padding: '10px 32px', fontSize: '14px', fontWeight: 500,
            background: 'var(--color-accent)', border: 'none',
            borderRadius: 'var(--radius-btn)', color: '#fff', cursor: 'pointer',
          }}>
            重新整理頁面
          </button>
          <details style={{ fontSize: '12px', color: 'var(--text-muted)', cursor: 'pointer', marginTop: '20px' }}>
            <summary style={{ marginBottom: '4px' }}>技術細節</summary>
            <pre style={{
              textAlign: 'left', padding: '10px', background: 'var(--bg-overlay)',
              borderRadius: 'var(--radius-card)', overflowX: 'auto',
              fontSize: '12px', lineHeight: 1.4, whiteSpace: 'pre-wrap',
            }}>
              {error.name}: {error.message}
            </pre>
          </details>
        </div>
      </div>
    );
  }

  render() {
    if (!this.state.hasError) return this.props.children;
    const error = this.state.error!;
    const level = this.props.level || 'page';
    if (level === 'component') return this.renderComponentError(error);
    if (level === 'fullscreen') return this.renderFullscreenError(error);
    return this.renderPageError(error);
  }
}
