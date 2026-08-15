import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useMarketAlerts } from '../hooks/useMarketAlerts';

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static OPEN = 1;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  url: string;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  close() {
    this.readyState = 3;
    this.onclose?.();
  }

  emitOpen() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  emitClose() {
    this.onclose?.();
  }

  emitAlert(data: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify({ type: 'alert_triggered', data }) });
  }

  emitOtherType() {
    this.onmessage?.({ data: JSON.stringify({ type: 'quote_update', data: {} }) });
  }

  emitInvalidJson() {
    this.onmessage?.({ data: '{not json' });
  }
}

function stubWebSocket() {
  vi.stubGlobal('WebSocket', MockWebSocket);
}

function flushMicrotasks() {
  return act(async () => {
    await Promise.resolve();
  });
}

describe('useMarketAlerts', () => {
  beforeEach(() => {
    stubWebSocket();
    MockWebSocket.instances = [];
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('starts disconnected with empty alerts', () => {
    const { result } = renderHook(() => useMarketAlerts({ enabled: false }));
    expect(result.current.status).toBe('disconnected');
    expect(result.current.alerts).toEqual([]);
    expect(result.current.unread).toBe(0);
  });

  it('connects and flips status to connected on open', async () => {
    const { result } = renderHook(() => useMarketAlerts());
    await flushMicrotasks();
    expect(MockWebSocket.instances.length).toBe(1);
    act(() => MockWebSocket.instances[0].emitOpen());
    expect(result.current.status).toBe('connected');
  });

  it('appends alert_triggered messages and increments unread', async () => {
    const { result } = renderHook(() => useMarketAlerts());
    await flushMicrotasks();
    act(() => {
      MockWebSocket.instances[0].emitAlert({ alert_type: 'WHALE_MOVE', stock_id: '2330', severiy: undefined });
      MockWebSocket.instances[0].emitAlert({ alert_type: 'VOLUME_SPIKE', stock_id: '2330' });
    });
    expect(result.current.alerts.length).toBe(2);
    expect(result.current.alerts[0].alert_type).toBe('VOLUME_SPIKE');
    expect(result.current.unread).toBe(2);
  });

  it('respects maxAlerts cap', async () => {
    const { result } = renderHook(() => useMarketAlerts({ maxAlerts: 2 }));
    await flushMicrotasks();
    act(() => {
      for (let i = 0; i < 4; i++) {
        MockWebSocket.instances[0].emitAlert({ alert_type: 'WHALE_MOVE', stock_id: `s${i}` });
      }
    });
    expect(result.current.alerts.length).toBe(2);
    expect(result.current.unread).toBe(4);
  });

  it('ignores non alert_triggered messages', async () => {
    const { result } = renderHook(() => useMarketAlerts());
    await flushMicrotasks();
    act(() => MockWebSocket.instances[0].emitOtherType());
    expect(result.current.alerts).toEqual([]);
    expect(result.current.unread).toBe(0);
  });

  it('ignores invalid JSON', async () => {
    const { result } = renderHook(() => useMarketAlerts());
    await flushMicrotasks();
    act(() => MockWebSocket.instances[0].emitInvalidJson());
    expect(result.current.alerts).toEqual([]);
  });

  it('sends desktop notification for NOTIFICATION_ALERT_TYPES when granted', async () => {
    const requestPermission = vi.fn();
    const NotificationMock = vi.fn();
    NotificationMock.mockImplementation(function (this: { title: string; options?: Record<string, unknown> }, title: string, options?: Record<string, unknown>) {
      this.title = title;
      this.options = options;
    });
    Object.assign(NotificationMock, { permission: 'granted', requestPermission });
    vi.stubGlobal('Notification', NotificationMock);

    renderHook(() => useMarketAlerts());
    await flushMicrotasks();
    act(() => {
      MockWebSocket.instances[0].emitOpen();
      MockWebSocket.instances[0].emitAlert({ alert_type: 'WHALE_MOVE', stock_id: '2330', severity: 'CRITICAL', stock_name: '台積電', message: '大戶移動' });
    });
    expect(NotificationMock).toHaveBeenCalledTimes(1);
    const call = NotificationMock.mock.calls[0];
    expect(call[0]).toContain('🚨');
    expect(call[1]?.body).toContain('台積電 (2330)');
  });

  it('does not notify for non-matching alert types', async () => {
    const NotificationMock = vi.fn();
    Object.assign(NotificationMock, { permission: 'granted', requestPermission: vi.fn() });
    vi.stubGlobal('Notification', NotificationMock);

    renderHook(() => useMarketAlerts());
    await flushMicrotasks();
    act(() => MockWebSocket.instances[0].emitAlert({ alert_type: 'TURNOVER_MONSTER', stock_id: '2330' }));
    expect(NotificationMock).not.toHaveBeenCalled();
  });

  it('reconnects with exponential backoff after close and caps at MAX_RETRIES', async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useMarketAlerts());
    // initial connect
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(MockWebSocket.instances.length).toBe(1);

    // 5 retries with delays 1s, 2s, 4s, 8s, 16s
    for (let retry = 0; retry < 5; retry++) {
      act(() => MockWebSocket.instances[MockWebSocket.instances.length - 1].emitClose());
      expect(result.current.status).toBe('disconnected');
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000 * Math.pow(2, retry));
      });
      expect(MockWebSocket.instances.length).toBe(2 + retry);
    }

    // 6th close → retries exhausted, no new connection
    act(() => MockWebSocket.instances[MockWebSocket.instances.length - 1].emitClose());
    const finalCount = MockWebSocket.instances.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100_000);
    });
    expect(MockWebSocket.instances.length).toBe(finalCount);
  });

  it('markAllRead resets unread counter', async () => {
    const { result } = renderHook(() => useMarketAlerts());
    await flushMicrotasks();
    act(() => MockWebSocket.instances[0].emitAlert({ alert_type: 'WHALE_MOVE', stock_id: '2330' }));
    expect(result.current.unread).toBe(1);
    act(() => result.current.markAllRead());
    expect(result.current.unread).toBe(0);
  });

  it('clearAlerts empties the list', async () => {
    const { result } = renderHook(() => useMarketAlerts());
    await flushMicrotasks();
    act(() => MockWebSocket.instances[0].emitAlert({ alert_type: 'WHALE_MOVE', stock_id: '2330' }));
    act(() => result.current.clearAlerts());
    expect(result.current.alerts).toEqual([]);
    expect(result.current.unread).toBe(0);
  });

  it('disconnect stops reconnection', async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useMarketAlerts());
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });

    act(() => {
      result.current.disconnect();
      MockWebSocket.instances[0].emitClose();
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(100_000); });
    expect(MockWebSocket.instances.length).toBe(1);
  });
});