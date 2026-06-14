import { useEffect, useRef, useState, useCallback } from 'react';
import type { AlertMessage } from '../types';

const WS_URL = `ws://${location.hostname}:8000/ws/alerts`;
const MAX_RETRIES = 5;
const BASE_DELAY = 1000;

export type WsStatus = 'connecting' | 'connected' | 'disconnected';

const NOTIFICATION_ALERT_TYPES = new Set(['AGAINST_TREND', 'WHALE_MOVE', 'LOW_PRICE_JUNK_RALLY']);

function requestNotificationPermission() {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'default') {
    Notification.requestPermission();
  }
}

function sendDesktopNotification(msg: AlertMessage['data']) {
  if (!('Notification' in window)) return;
  if (Notification.permission !== 'granted') return;
  const severityIcon: Record<string, string> = {
    CRITICAL: '🚨', HIGH: '⚠️', MEDIUM: '📌', LOW: '📊',
  };
  new Notification(
    `${severityIcon[msg.severity] || '🔔'} 智慧警示触发`,
    { body: `${msg.stock_name} (${msg.stock_id}): ${msg.message}` },
  );
}

interface UseMarketAlertsOptions {
  maxAlerts?: number;
  enabled?: boolean;
}

export function useMarketAlerts({ maxAlerts = 50, enabled = true }: UseMarketAlertsOptions = {}) {
  const [alerts, setAlerts] = useState<AlertMessage['data'][]>([]);
  const [status, setStatus] = useState<WsStatus>('disconnected');
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unreadRef = useRef(0);
  const [unread, setUnread] = useState(0);

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
        const payload = JSON.parse(event.data) as AlertMessage;
        if (payload.type !== 'alert_triggered') return;
        const alert = payload.data;
        setAlerts((prev) => {
          const next = [alert, ...prev];
          return next.slice(0, maxAlerts);
        });
        unreadRef.current += 1;
        setUnread(unreadRef.current);

        if (NOTIFICATION_ALERT_TYPES.has(alert.alert_type)) {
          sendDesktopNotification(alert);
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
  }, [enabled, maxAlerts]);

  const disconnect = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    retryRef.current = MAX_RETRIES;
    wsRef.current?.close();
    wsRef.current = null;
    setStatus('disconnected');
  }, []);

  const markAllRead = useCallback(() => {
    unreadRef.current = 0;
    setUnread(0);
  }, []);

  const clearAlerts = useCallback(() => {
    setAlerts([]);
    markAllRead();
  }, [markAllRead]);

  useEffect(() => {
    requestNotificationPermission();
    if (enabled) {
      connect();
    }
    return () => {
      disconnect();
    };
  }, [enabled, connect, disconnect]);

  return { alerts, status, unread, markAllRead, clearAlerts, disconnect, reconnect: connect };
}
