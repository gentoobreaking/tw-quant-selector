import { useState, useEffect, useCallback } from 'react';
import styles from './MarketDashboard.module.css';

// ── Types ──

interface QuoteData {
  price: number;
  prev: number;
  change: number;
  changePct: number;
}

interface QuoteEntry {
  symbol: string;
  name: string;
  en: string;
  section: string;
  data: QuoteData | null;
}

type QuotesMap = Record<string, QuoteEntry>;

interface QuotesResponse {
  ok: boolean;
  updatedAt: string;
  quotes: QuotesMap;
}

// ── Constants ──

const SECTION_ORDER = ['us_index', 'us_stocks', 'oil', 'gold', 'tw'] as const;

const SECTION_LABELS: Record<string, { icon: string; label: string }> = {
  us_index: { icon: '📈', label: '美股走勢指標' },
  us_stocks: { icon: '🔭', label: '美股觀察' },
  oil: { icon: '🛢️', label: '油價走勢指標' },
  gold: { icon: '🏅', label: '黃金走勢指標' },
  tw: { icon: '🇹🇼', label: '台股走勢指標' },
};

// ── Helpers ──

function fmtPrice(p: number): string {
  if (p >= 10000) return p.toLocaleString('en-US', { maximumFractionDigits: 2 });
  if (p >= 100) return p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 });
}

// ── Card Component ──

function QuoteCard({ entry }: { entry: QuoteEntry }) {
  const d = entry.data;
  const dir: 'up' | 'down' | 'neutral' = d
    ? d.change > 0 ? 'up' : d.change < 0 ? 'down' : 'neutral'
    : 'neutral';

  const sign = d?.change != null && d.change > 0 ? '+' : '';
  const cardClass = `${styles.card} ${dir !== 'neutral' ? styles[dir] : ''}`;

  const yahooUrl = `https://tw.stock.yahoo.com/quote/${encodeURIComponent(entry.symbol)}`;

  return (
    <a
      href={yahooUrl}
      target="_blank"
      rel="noopener noreferrer"
      className={cardClass}
    >
      <div className={styles.cardHead}>
        <div className={styles.cardMeta}>
          <div className={styles.cardName}>{entry.name}</div>
          <div className={styles.cardSymbol}>{entry.symbol} · {entry.en}</div>
        </div>
        <div className={styles.cardArrow}>
          {dir === 'up' ? '▲' : dir === 'down' ? '▼' : '—'}
        </div>
      </div>
      <div className={`${styles.cardPrice} ${!d ? styles.placeholder : ''}`}>
        {d ? fmtPrice(d.price) : '—'}
      </div>
      <div className={styles.cardChange}>
        {d ? (
          <>
            <span className={`${styles.chg} ${styles[dir]}`}>
              {sign}{d.change.toFixed(2)}
            </span>
            <span className={`${styles.badge} ${styles[dir]}`}>
              {sign}{d.changePct.toFixed(2)}%
            </span>
          </>
        ) : (
          <span className={`${styles.chg} ${styles.neutral}`}>N/A</span>
        )}
      </div>
      <div className={styles.cardLinkHint}>↗ 開啟 Yahoo Finance TW</div>
    </a>
  );
}

// ── Main Component ──

export default function MarketDashboard() {
  const [quotes, setQuotes] = useState<QuotesMap | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string>('—');
  const [status, setStatus] = useState<'loading' | 'ok' | 'err'>('loading');
  const [errorBanner, setErrorBanner] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setStatus('loading');
    try {
      const res = await fetch('/api/market/quotes', { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json: QuotesResponse = await res.json();

      setQuotes(json.quotes);
      setUpdatedAt(new Date(json.updatedAt).toLocaleTimeString('zh-TW', {
        hour: '2-digit', minute: '2-digit', second: '2-digit',
      }));

      let fails = 0;
      const total = Object.keys(json.quotes).length;
      for (const q of Object.values(json.quotes)) {
        if (!q.data) fails++;
      }

      setStatus(fails === total ? 'err' : 'ok');

      if (fails > 0 && fails < total) {
        setErrorBanner(`⚠ ${okCount(json.quotes)}/${total} 筆成功，${fails} 筆失敗（休市或暫時無法取得）`);
      } else {
        setErrorBanner(null);
      }
    } catch {
      setStatus('err');
      setErrorBanner('⚠ 無法連線後端，請確認伺服器已啟動');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  // Group quotes by section
  const groups: Record<string, QuoteEntry[]> = {};
  for (const s of SECTION_ORDER) groups[s] = [];
  if (quotes) {
    for (const q of Object.values(quotes)) {
      if (groups[q.section]) groups[q.section].push(q);
    }
  }

  return (
    <div className={styles.container}>
      {/* Header */}
      <div className={styles.header}>
        <div className={styles.headerLeft}>
          <div className={styles.logoDot} />
          <span className={styles.headerTitle}>市場走勢儀表板</span>
        </div>
        <div className={styles.headerRight}>
          <span className={styles.lastUpdated}>
            Updated: <span id="last-update-time">{updatedAt}</span>
          </span>
          <button
            className={`${styles.refreshBtn} ${loading ? styles.loading : ''}`}
            onClick={fetchAll}
            disabled={loading}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M23 4v6h-6M1 20v-6h6" />
              <path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15" />
            </svg>
            REFRESH
          </button>
        </div>
      </div>

      {/* Status Bar */}
      <div className={styles.statusBar}>
        <div className={`${styles.statusDot} ${styles[status]}`} />
        <span className={styles.statusText}>
          {status === 'ok' ? '後端連線正常' : status === 'err' ? '後端離線' : '連線中…'}
        </span>
      </div>

      {/* Error Banner */}
      {errorBanner && (
        <div className={`${styles.banner} ${styles.bannerErr} ${styles.show}`}>
          {errorBanner}
        </div>
      )}

      {/* Sections */}
      <div className={styles.main}>
        {SECTION_ORDER.map((section) => {
          const items = groups[section] || [];
          const info = SECTION_LABELS[section];
          return (
            <div key={section} className={styles.sectionBox}>
              <div className={styles.sectionTitleBar}>
                <span className={styles.sectionIcon}>{info.icon}</span>
                <span className={styles.sectionLabel}>{info.label}</span>
                <span className={styles.sectionCount}>{items.length} 項</span>
              </div>
              <div className={styles.sectionBody}>
                <div className={styles.cardGrid}>
                  {items.map((entry) => (
                    <QuoteCard key={entry.symbol} entry={entry} />
                  ))}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function okCount(quotes: QuotesMap): number {
  let count = 0;
  for (const q of Object.values(quotes)) {
    if (q.data) count++;
  }
  return count;
}
