import { useState, useEffect, type ReactNode } from 'react';

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === 'undefined') return false;
    return window.matchMedia(query).matches;
  });
  useEffect(() => {
    const mq = window.matchMedia(query);
    const handler = (e: MediaQueryListEvent) => setMatches(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, [query]);
  return matches;
}

export function DesktopOnly({ children }: { children: ReactNode }) {
  const isDesktop = useMediaQuery('(min-width: 769px)');
  if (!isDesktop) return null;
  return <>{children}</>;
}

export function MobileOnly({ children }: { children: ReactNode }) {
  const isMobile = useMediaQuery('(max-width: 768px)');
  if (!isMobile) return null;
  return <>{children}</>;
}

export function MobileMessage({ message = '請在桌面環境使用此功能' }: { message?: string }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      minHeight: 240, textAlign: 'center', padding: '32px 16px',
    }}>
      <p style={{ color: 'var(--text-muted)', fontSize: 'var(--font-size-sm)', lineHeight: 1.6 }}>
        📱 {message}
      </p>
    </div>
  );
}
