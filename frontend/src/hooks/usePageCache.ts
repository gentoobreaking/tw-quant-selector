import { useCallback } from 'react';

const CACHE_PREFIX = 'tw_quant_page_cache_';

export function usePageCache<T>(pageKey: string) {
  const cacheKey = CACHE_PREFIX + pageKey;

  const getCached = useCallback((): T | null => {
    try {
      const raw = sessionStorage.getItem(cacheKey);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }, [cacheKey]);

  const setCached = useCallback((data: T) => {
    try {
      sessionStorage.setItem(cacheKey, JSON.stringify(data));
    } catch {
      // sessionStorage full — silently ignore
    }
  }, [cacheKey]);

  const clearCache = useCallback(() => {
    try {
      sessionStorage.removeItem(cacheKey);
    } catch { /* ignore */ }
  }, [cacheKey]);

  return { getCached, setCached, clearCache };
}
