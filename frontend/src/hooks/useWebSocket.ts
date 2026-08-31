import { useEffect, useRef, useState, useCallback } from 'react';

export type WsStatus = 'connecting' | 'connected' | 'disconnected';

const WS_URL = `ws://${location.hostname}${location.port ? `:${location.port}` : ''}/ws/quotes`;
const MAX_RETRIES = 5;
const BASE_DELAY = 1000;

export interface QuoteUpdate {
  type: 'quote_update';
  timestamp: string;
  data: Record<string, {
    price: number;
    change_pct: number;
    pe_realtime: number;
    pb_realtime: number;
    volume: number;
  }>;
}

interface Options {
  onMessage?: (update: QuoteUpdate) => void;
  enabled?: boolean;
}

export function useWebSocket({ onMessage, enabled = true }: Options = {}) {
  const [status, setStatus] = useState<WsStatus>('disconnected');
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  const connect = useCallback(() => {
    if (!enabled) return;
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    setStatus('connecting');
    const ws = new WebSocket(WS_URL);

    ws.onopen = () => {
      setStatus('connected');
      retryRef.current = 0;
    };

    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as QuoteUpdate;
        if (payload.type === 'quote_update') {
          onMessageRef.current?.(payload);
        }
      } catch {
        // ignore parse errors
      }
    };

    ws.onclose = () => {
      setStatus('disconnected');
      wsRef.current = null;
      if (enabled && retryRef.current < MAX_RETRIES) {
        const delay = BASE_DELAY * Math.pow(2, retryRef.current);
        retryRef.current += 1;
        timerRef.current = setTimeout(connect, delay);
      }
    };

    ws.onerror = () => {
      ws.close();
    };

    wsRef.current = ws;
  }, [enabled]);

  const disconnect = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    retryRef.current = MAX_RETRIES; // prevent reconnect
    wsRef.current?.close();
    wsRef.current = null;
    setStatus('disconnected');
  }, []);

  useEffect(() => {
    if (enabled) {
      connect();
    }
    return () => {
      disconnect();
    };
  }, [enabled, connect, disconnect]);

  return { status, disconnect, reconnect: connect };
}
