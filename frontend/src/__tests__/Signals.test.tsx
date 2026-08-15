import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Signals from '../pages/Signals';
import { ToastProvider } from '../components/Toast';
import { AlertContext } from '../hooks/AlertContext';
import type { AlertMessage } from '../types';

vi.mock('../api/client', () => ({
  fetchSignalCalendar: vi.fn(),
  fetchSignalsByDate: vi.fn(),
  fetchStockDetail: vi.fn().mockResolvedValue({}),
}));

import { fetchSignalCalendar, fetchSignalsByDate } from '../api/client';

const DATE = '2026-06-05';

const STOCKS = [
  { stock_id: '2330', name: '台積電', score: 95.5, rank: 1, rank_change: 2, consecutive_days: 3, close_price: 950, change: 10, change_pct: 1.06 },
  { stock_id: '2317', name: '鴻海', score: 90.1, rank: 2, rank_change: -1, consecutive_days: null, close_price: 180, change: -2.5, change_pct: -1.37 },
  { stock_id: '0050', name: '元大台灣50', score: 60.0, rank: 3, rank_change: null, consecutive_days: 0, close_price: 190, change: 1, change_pct: 0.53 },
];

function makeAlert(type: string, severity = 'MEDIUM'): AlertMessage['data'] {
  return {
    alert_type: type,
    severity,
    stock_id: '2330',
    stock_name: '台積電',
    message: '警示',
    details: {},
    triggered_at: `2026-06-05T10:00:00+08:00`,
  } as AlertMessage['data'];
}

function renderSignals(alertsForStock: (id: string) => AlertMessage['data'][]) {
  vi.mocked(fetchSignalCalendar).mockResolvedValue([DATE]);
  vi.mocked(fetchSignalsByDate).mockResolvedValue({ date: DATE, stocks: STOCKS, etfs: [] });
  return render(
    <MemoryRouter initialEntries={['/signals']}>
      <AlertContext.Provider value={{ alerts: [], unread: 0, markAllRead: () => {}, getAlertsForStock: alertsForStock }}>
        <ToastProvider>
          <Signals />
        </ToastProvider>
      </AlertContext.Provider>
    </MemoryRouter>
  );
}

describe('Signals alert row icons', () => {
  beforeEach(() => {
    vi.mocked(fetchSignalCalendar).mockReset();
    vi.mocked(fetchSignalsByDate).mockReset();
    Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  });

  it('renders signal rows after calendar and data load', async () => {
    renderSignals(() => []);
    await screen.findByText(/台積電/);
    expect(screen.getByText(/台積電/)).toBeInTheDocument();
    expect(screen.getByText(/鴻海/)).toBeInTheDocument();
    expect(screen.getByText(/元大台灣50/)).toBeInTheDocument();
  });

  it('maps WHALE_MOVE alert to whale icon', async () => {
    renderSignals((id) => (id === '2330' ? [makeAlert('WHALE_MOVE')] : []));
    await screen.findByText(/台積電/);
    expect(screen.getByText('🐋')).toBeInTheDocument();
  });

  it('falls back to bell icon for unknown alert types', async () => {
    renderSignals((id) => (id === '2330' ? [makeAlert('UNKNOWN_TYPE')] : []));
    await screen.findByText(/台積電/);
    expect(screen.getByText('🔔')).toBeInTheDocument();
  });

  it('dedupes multiple alerts of the same type into one icon', async () => {
    renderSignals((id) => (id === '2330' ? [makeAlert('WHALE_MOVE'), makeAlert('WHALE_MOVE', 'HIGH')] : []));
    await screen.findByText(/台積電/);
    const whaleIcons = screen.getAllByText('🐋');
    expect(whaleIcons.length).toBe(1);
  });

  it('renders at most 2 distinct icons per stock', async () => {
    renderSignals((id) => (id === '2330'
      ? [makeAlert('WHALE_MOVE'), makeAlert('LOW_PRICE_JUNK_RALLY'), makeAlert('ETF_PREMIUM_DISCOUNT')]
      : []));
    await screen.findByText(/台積電/);
    const cell = screen.getByText(/台積電/).closest('tr')!;
    const iconText = Array.from(cell.querySelectorAll('span'))
      .map(s => s.textContent ?? '')
      .join('|');
    expect(iconText).toContain('🐋');
    expect(iconText).toContain('🚨');
    expect(iconText).not.toContain('💰');
  });

  it('wraps alert icons in a Tooltip and renders tooltip content on hover', async () => {
    renderSignals((id) => (id === '2330' ? [makeAlert('WHALE_MOVE', 'HIGH')] : []));
    await screen.findByText(/台積電/);
    const whale = screen.getByText('🐋');
    expect(whale).toHaveStyle({ cursor: 'help' });

    vi.useFakeTimers();
    fireEvent.mouseOver(whale);
    fireEvent.mouseOver(screen.getByText('動能'));
    await act(async () => { vi.advanceTimersByTime(400); });

    try {
      const tips = screen.getAllByRole('tooltip');
      expect(tips.length).toBeGreaterThanOrEqual(1);
      expect(tips.some(t => t.textContent?.includes('動能因子'))).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});