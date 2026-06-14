import { lazy, Suspense, useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import SkeletonLoader from './components/SkeletonLoader';
import Layout from './components/Layout';
import ErrorBoundary from './components/ErrorBoundary';
import { ToastProvider } from './components/Toast';
import ShortcutHelp, { GLOBAL_SHORTCUTS, TABLE_SHORTCUTS, CHART_SHORTCUTS } from './components/ShortcutHelp';
import { useGlobalShortcuts } from './hooks/useKeyboardShortcuts';

const Dashboard = lazy(() => import('./pages/Dashboard'));
const Signals = lazy(() => import('./pages/Signals'));
const StockDetail = lazy(() => import('./pages/StockDetail'));
const Portfolio = lazy(() => import('./pages/Portfolio'));
const Backtest = lazy(() => import('./pages/Backtest'));
const BacktestDetail = lazy(() => import('./pages/BacktestDetail'));
const Strategy = lazy(() => import('./pages/Strategy'));
const Monitor = lazy(() => import('./pages/Monitor'));
const Settings = lazy(() => import('./pages/Settings'));
const InstitutionalFlow = lazy(() => import('./pages/InstitutionalFlow'));
const FactorResearch = lazy(() => import('./pages/FactorResearch'));
const AlertHistory = lazy(() => import('./pages/AlertHistory'));
const GuruScores = lazy(() => import('./pages/GuruScores'));
const TwseAnalysis = lazy(() => import('./pages/TwseAnalysis'));
const TwseScreener = lazy(() => import('./pages/TwseScreener'));
const MarketDashboard = lazy(() => import('./pages/MarketDashboard'));

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 30_000 } },
});

function PageFallback() {
  return <div style={{ padding: '32px' }}><SkeletonLoader variant="card" /><SkeletonLoader variant="table" rows={6} /></div>;
}

function AnimatedOutlet({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  return <div key={location.pathname} className="route-page">{children}</div>;
}

function ShortcutHelpManager() {
  useGlobalShortcuts();
  
  const [showShortcutHelp, setShowShortcutHelp] = useState(false);
  const location = useLocation();

  useEffect(() => {
    const handler = () => setShowShortcutHelp(true);
    window.addEventListener('shortcut:show-help', handler);
    return () => window.removeEventListener('shortcut:show-help', handler);
  }, []);

  const getPageShortcuts = () => {
    const path = location.pathname;
    if (path === '/signals' || path === '/') {
      return TABLE_SHORTCUTS;
    }
    if (path === '/backtest') {
      return CHART_SHORTCUTS;
    }
    return [];
  };

  return (
    <ShortcutHelp
      isOpen={showShortcutHelp}
      onClose={() => setShowShortcutHelp(false)}
      globalShortcuts={GLOBAL_SHORTCUTS}
      pageShortcuts={getPageShortcuts()}
    />
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ErrorBoundary level="fullscreen">
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/" element={<AnimatedOutlet><Suspense fallback={<PageFallback />}><ErrorBoundary><Dashboard /></ErrorBoundary></Suspense></AnimatedOutlet>} />
              <Route path="/signals" element={<AnimatedOutlet><Suspense fallback={<PageFallback />}><ErrorBoundary><Signals /></ErrorBoundary></Suspense></AnimatedOutlet>} />
              <Route path="/signals/:id" element={<AnimatedOutlet><Suspense fallback={<PageFallback />}><ErrorBoundary><StockDetail /></ErrorBoundary></Suspense></AnimatedOutlet>} />
              <Route path="/portfolio" element={<AnimatedOutlet><Suspense fallback={<PageFallback />}><ErrorBoundary><Portfolio /></ErrorBoundary></Suspense></AnimatedOutlet>} />
              <Route path="/backtest" element={<AnimatedOutlet><Suspense fallback={<PageFallback />}><ErrorBoundary><Backtest /></ErrorBoundary></Suspense></AnimatedOutlet>} />
              <Route path="/backtest/:runId" element={<AnimatedOutlet><Suspense fallback={<PageFallback />}><ErrorBoundary><BacktestDetail /></ErrorBoundary></Suspense></AnimatedOutlet>} />
              <Route path="/strategy" element={<AnimatedOutlet><Suspense fallback={<PageFallback />}><ErrorBoundary><Strategy /></ErrorBoundary></Suspense></AnimatedOutlet>} />
              <Route path="/monitor" element={<AnimatedOutlet><Suspense fallback={<PageFallback />}><ErrorBoundary><Monitor /></ErrorBoundary></Suspense></AnimatedOutlet>} />
              <Route path="/settings" element={<AnimatedOutlet><Suspense fallback={<PageFallback />}><ErrorBoundary><Settings /></ErrorBoundary></Suspense></AnimatedOutlet>} />
              <Route path="/institutional-flow" element={<AnimatedOutlet><Suspense fallback={<PageFallback />}><ErrorBoundary><InstitutionalFlow /></ErrorBoundary></Suspense></AnimatedOutlet>} />
              <Route path="/factor-research" element={<AnimatedOutlet><Suspense fallback={<PageFallback />}><ErrorBoundary><FactorResearch /></ErrorBoundary></Suspense></AnimatedOutlet>} />
              <Route path="/alert-history" element={<AnimatedOutlet><Suspense fallback={<PageFallback />}><ErrorBoundary><AlertHistory /></ErrorBoundary></Suspense></AnimatedOutlet>} />
              <Route path="/guru-scores" element={<AnimatedOutlet><Suspense fallback={<PageFallback />}><ErrorBoundary><GuruScores /></ErrorBoundary></Suspense></AnimatedOutlet>} />
              <Route path="/twse-analysis" element={<AnimatedOutlet><Suspense fallback={<PageFallback />}><ErrorBoundary><TwseAnalysis /></ErrorBoundary></Suspense></AnimatedOutlet>} />
              <Route path="/twse-screener" element={<AnimatedOutlet><Suspense fallback={<PageFallback />}><ErrorBoundary><TwseScreener /></ErrorBoundary></Suspense></AnimatedOutlet>} />
              <Route path="/market-dashboard" element={<AnimatedOutlet><Suspense fallback={<PageFallback />}><ErrorBoundary><MarketDashboard /></ErrorBoundary></Suspense></AnimatedOutlet>} />
            </Route>
          </Routes>
          <ShortcutHelpManager />
        </BrowserRouter>
        </ErrorBoundary>
      </ToastProvider>
    </QueryClientProvider>
  );
}
