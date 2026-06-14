import { useState, useEffect, useRef, useMemo } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';
import { searchStocks, type StockSearchResult } from '../api/client';
import Sidebar from './Sidebar';
import MarketStatus from './MarketStatus';
import AlertSidebar from './AlertSidebar';
import { useMarketAlerts } from '../hooks/useMarketAlerts';
import { AlertContext } from '../hooks/AlertContext';
import styles from './Layout.module.css';

export default function Layout() {
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(() => {
    if (typeof window === 'undefined') return false;
    return window.matchMedia('(max-width: 768px)').matches;
  });
  const [offline, setOffline] = useState(!navigator.onLine);
  const [alertSidebarOpen, setAlertSidebarOpen] = useState(false);
  const { alerts, unread, markAllRead, clearAlerts } = useMarketAlerts();

  const alertCtx = useMemo(() => ({
    alerts,
    unread,
    markAllRead,
    getAlertsForStock: (stockId: string) =>
      alerts.filter((a) => a.stock_id === stockId),
  }), [alerts, unread, markAllRead]);
  const [searchQ, setSearchQ] = useState('');
  const [searchResults, setSearchResults] = useState<StockSearchResult[]>([]);
  const [showSearch, setShowSearch] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);
  const toastShown = useRef(false);

  // T056: 首次進入提示（3秒後顯示快捷鍵提示）
  useEffect(() => {
    if (toastShown.current) return;
    const dismissed = localStorage.getItem('shortcut-help-dismissed');
    if (dismissed) return;
    
    const timer = setTimeout(() => {
      // 使用全域 toast 系統（透過 ToastContext）
      const event = new CustomEvent('toast:add', {
        detail: { message: '💡 按 ⌘K 可快速搜尋股票', severity: 'low' }
      });
      window.dispatchEvent(event);
      toastShown.current = true;
    }, 3000);
    
    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 768px)');
    const handler = (e: MediaQueryListEvent) => setCollapsed(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);

  useEffect(() => {
    const on = () => setOffline(false);
    const off = () => setOffline(true);
    window.addEventListener('online', on);
    window.addEventListener('offline', off);
    return () => { window.removeEventListener('online', on); window.removeEventListener('offline', off); };
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'b') {
        e.preventDefault();
        setCollapsed((c) => !c);
      }
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        document.querySelector<HTMLInputElement>('[data-search]')?.focus();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) setShowSearch(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  useEffect(() => {
    if (searchQ.length < 1) { setSearchResults([]); return; }
    const t = setTimeout(() => {
      searchStocks(searchQ).then(setSearchResults).catch(() => setSearchResults([]));
    }, 200);
    return () => clearTimeout(t);
  }, [searchQ]);

  const goStock = (id: string) => {
    setShowSearch(false);
    setSearchQ('');
    navigate(`/signals/${id}`);
  };

  return (
    <AlertContext.Provider value={alertCtx}>
    <div className={styles.layout}>
      {offline && <div className={styles.offlineBanner} role="alert">⚠ 離線中，顯示快取資料</div>}
      <div className={styles.body}>
        <Sidebar
          collapsed={collapsed}
          onToggle={() => setCollapsed((c) => !c)}
          alertOnMonitor={false}
          alertUnread={unread}
          onAlertSidebarToggle={() => setAlertSidebarOpen((o) => !o)}
        />
        <main className={styles.content}>
          <div className={styles.topBar}>
            <MarketStatus />
            <div className={styles.searchWrap} ref={searchRef}>
            <input
              data-search
              className={styles.searchInput}
              type="text"
              placeholder="🔍 搜尋股票代號或名稱…  (⌘K)"
              value={searchQ}
              onChange={(e) => setSearchQ(e.target.value)}
              onFocus={() => setShowSearch(true)}
            />
            {showSearch && searchResults.length > 0 && (
              <div className={styles.searchDropdown}>
                {searchResults.map((s) => (
                  <div key={s.stock_id} className={styles.searchItem} onClick={() => goStock(s.stock_id)}>
                    <span className={styles.searchId}>{s.stock_id}</span>
                    <span className={styles.searchName}>{s.name}</span>
                    <span className={styles.searchMeta}>{s.market} {s.is_etf ? 'ETF' : ''} {s.industry ?? ''}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
          </div>
          <div aria-live="polite" aria-atomic="true" className="sr-only" />
          <Outlet />
        </main>
      </div>
      <AlertSidebar
        open={alertSidebarOpen}
        onClose={() => setAlertSidebarOpen(false)}
        alerts={alerts}
        onMarkAllRead={markAllRead}
        onClear={clearAlerts}
      />
    </div>
    </AlertContext.Provider>
  );
}
