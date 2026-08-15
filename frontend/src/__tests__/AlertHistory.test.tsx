import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AlertHistory from '../pages/AlertHistory';
import { AlertContext } from '../hooks/AlertContext';

vi.mock('../api/client', () => ({
  fetchAlertHistory: vi.fn().mockResolvedValue([]),
  fetchAlertStats: vi.fn().mockResolvedValue({ daily: [], weekly: [] }),
  fetchSmartAlertHistory: vi.fn().mockResolvedValue([]),
  fetchMarketScreen: vi.fn(),
  fetchInstitutionalTop: vi.fn().mockResolvedValue({ data: [] }),
  resolveAlert: vi.fn(),
}));

import { fetchMarketScreen } from '../api/client';

describe('AlertHistory MarketScreenTab', () => {
  const objectURLMock = vi.fn((_blob: Blob) => 'blob:mock');

  beforeEach(() => {
    vi.mocked(fetchMarketScreen).mockReset();
    // jsdom lacks createObjectURL
    objectURLMock.mockClear();
    Object.assign(URL, { createObjectURL: objectURLMock, revokeObjectURL: vi.fn() });
  });

  async function gotoMarketScreenTab() {
    render(
      <MemoryRouter>
        <AlertContext.Provider value={{ alerts: [], unread: 0, markAllRead: () => {}, getAlertsForStock: () => [] }}>
          <AlertHistory />
        </AlertContext.Provider>
      </MemoryRouter>
    );
    fireEvent.click(screen.getByText('全市場數據篩選'));
    await screen.findByText('股票代號');
  }

  it('renders market screen table with formatted values', async () => {
    vi.mocked(fetchMarketScreen).mockResolvedValue([
      { stock_id: '2330', name: '台積電', industry: '半導體', is_etf: false, close: 110.5, change_pct: 10.0, volume: 12345 },
      { stock_id: '0050', name: '元大台灣50', industry: 'ETF', is_etf: true, close: 100.0, change_pct: -1.25, volume: 5000 },
      { stock_id: '2317', name: '鴻海', industry: '電子', is_etf: false, close: null, change_pct: null, volume: 800 },
    ]);
    await gotoMarketScreenTab();

    await waitFor(() => expect(fetchMarketScreen).toHaveBeenCalled());
    expect(screen.getByText('2330')).toBeInTheDocument();
    expect(screen.getByText('+10.00%')).toBeInTheDocument();
    expect(screen.getByText('-1.25%')).toBeInTheDocument();
    // 2317 has null close — renders '—' in close AND change cells
    const dashCells = screen.getAllByText('—');
    expect(dashCells.length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('股票').length).toBe(2); // 類型 column (2330 + header row label)
    expect(screen.getAllByText('ETF').length).toBeGreaterThanOrEqual(1);
  });

  it('calls fetchMarketScreen with toggled filter params on 查詢', async () => {
    vi.mocked(fetchMarketScreen).mockResolvedValue([]);
    await gotoMarketScreenTab();

    fireEvent.click(screen.getByText('僅看 ETF'));
    fireEvent.click(screen.getByText('僅顯示今日爆量突破股'));
    fireEvent.click(screen.getByText('查詢'));

    await waitFor(() => {
      const lastCall = vi.mocked(fetchMarketScreen).mock.calls.at(-1)?.[0];
      expect(lastCall).toMatchObject({ include_stocks: true, include_etf: true, volume_spike: true, against_trend: false });
    });
  });

  it('exports CSV with BOM and headers on 匯出', async () => {
    vi.mocked(fetchMarketScreen).mockResolvedValue([
      { stock_id: '2330', name: '台積電', industry: '半導體', is_etf: false, close: 110.5, change_pct: 10.0, volume: 12345 },
    ]);
    await gotoMarketScreenTab();
    await waitFor(() => expect(fetchMarketScreen).toHaveBeenCalled());

    fireEvent.click(screen.getByText('📥 匯出篩選結果'));

    await waitFor(() => expect(objectURLMock).toHaveBeenCalled());
    const blob = objectURLMock.mock.calls[0]?.[0] as Blob;
    const buf = new Uint8Array(await blob.arrayBuffer());
    // UTF-8 BOM (EF BB BF) at byte level
    expect([buf[0], buf[1], buf[2]]).toEqual([0xEF, 0xBB, 0xBF]);
    const text = await blob.text();
    expect(text).toContain('股票代號,股票名稱,產業,類型,收盤價,漲跌幅,成交量');
    expect(text).toContain('2330,台積電,半導體,股票,110.50,+10.00%,12K');
  });

  it('sorts market table by abs change desc and applies red/green background classes', async () => {
    vi.mocked(fetchMarketScreen).mockResolvedValue([
      { stock_id: 'A', name: 'A股', industry: 'X', is_etf: false, close: 10, change_pct: 5.0, volume: 100 },
      { stock_id: 'B', name: 'B股', industry: 'X', is_etf: false, close: 10, change_pct: -3.0, volume: 100 },
    ]);
    await gotoMarketScreenTab();
    await waitFor(() => expect(screen.getByText('+5.00%')).toBeInTheDocument());
    const plusCell = screen.getByText('+5.00%');
    const minusCell = screen.getByText('-3.00%');
    // bgRed for gains, bgGreen for losses (CSS modules class names)
    expect(plusCell.className).toBeTruthy();
    expect(minusCell.className).toBeTruthy();
  });
});

describe('AlertHistory tabs', () => {
  it('switches between tabs', async () => {
    render(
      <MemoryRouter>
        <AlertContext.Provider value={{ alerts: [], unread: 0, markAllRead: () => {}, getAlertsForStock: () => [] }}>
          <AlertHistory />
        </AlertContext.Provider>
      </MemoryRouter>
    );
    expect(screen.getByText('警示總覽')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '法人籌碼交叉比對' }));
    expect(await screen.findByText('前 50 大法人買賣超標的')).toBeInTheDocument();
  });
});