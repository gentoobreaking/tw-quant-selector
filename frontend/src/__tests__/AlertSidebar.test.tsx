import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AlertSidebar from '../components/AlertSidebar';
import type { AlertMessage } from '../types';

function makeAlert(overrides: Partial<AlertMessage['data']>, i = 0): AlertMessage['data'] {
  return {
    alert_type: 'WHALE_MOVE',
    severity: 'CRITICAL',
    stock_id: `2330-${i}`,
    stock_name: '台積電',
    message: '大戶移動',
    details: {},
    ...overrides,
  };
}

function renderSidebar(alerts: AlertMessage['data'][] = [], props = {}) {
  return render(
    <MemoryRouter>
      <AlertSidebar
        open={true}
        onClose={vi.fn()}
        alerts={alerts}
        onMarkAllRead={vi.fn()}
        onClear={vi.fn()}
        {...props}
      />
    </MemoryRouter>
  );
}

describe('AlertSidebar', () => {
  it('renders severity-fixed empty state', () => {
    renderSidebar([]);
    expect(screen.getByText('目前無警示')).toBeInTheDocument();
  });

  it('renders alerts with icon and severity badge', () => {
    const alerts = [makeAlert({ alert_type: 'WHALE_MOVE', severity: 'CRITICAL' }, 1)];
    renderSidebar(alerts);
    expect(screen.getByText('台積電')).toBeInTheDocument();
    expect(screen.getByText('🐋')).toBeInTheDocument();
    // severity badge is in a span (option text also matches CRITICAL)
    const badge = screen.getAllByText('CRITICAL').find(el => el.closest('span'));
    expect(badge).toBeInTheDocument();
  });

  it('uses default bell icon for unknown alert types', () => {
    const alerts = [makeAlert({ alert_type: 'UNKNOWN_TYPE', severity: 'LOW' }, 2)];
    renderSidebar(alerts);
    expect(screen.getByText('🔔')).toBeInTheDocument();
  });

  it('filters by severity', () => {
    const alerts = [
      makeAlert({ severity: 'CRITICAL' }, 1),
      makeAlert({ severity: 'LOW' }, 2),
      makeAlert({ severity: 'MEDIUM' }, 3),
    ];
    renderSidebar(alerts);
    const severitySelect = screen.getAllByRole('combobox')[0];
    fireEvent.change(severitySelect, { target: { value: 'LOW' } });
    expect(screen.getAllByText('台積電')).toHaveLength(1);
    // LOW appears in the severity select option AND as the badge
    const badge = screen.getByText((_t, el) => el?.tagName === 'SPAN' && el.textContent === 'LOW');
    expect(badge).toBeInTheDocument();
    expect(screen.queryByText((_t, el) => el?.tagName === 'SPAN' && el.textContent === 'CRITICAL')).not.toBeInTheDocument();
  });

  it('filters by type', () => {
    const alerts = [
      makeAlert({ alert_type: 'WHALE_MOVE' }, 1),
      makeAlert({ alert_type: 'VOLUME_SPIKE' }, 2),
    ];
    renderSidebar(alerts);
    const typeSelect = screen.getAllByRole('combobox')[1];
    fireEvent.change(typeSelect, { target: { value: 'VOLUME_SPIKE' } });
    expect(screen.getAllByText('台積電')).toHaveLength(1);
    expect(screen.getByText('⚡')).toBeInTheDocument();
    expect(screen.queryByText('🐋')).not.toBeInTheDocument();
  });

  it('builds severity options in CRITICAL > HIGH > MEDIUM > LOW order', () => {
    const alerts = [
      makeAlert({ severity: 'LOW' }, 1),
      makeAlert({ severity: 'CRITICAL' }, 2),
      makeAlert({ severity: 'MEDIUM' }, 3),
      makeAlert({ severity: 'HIGH' }, 4),
    ];
    renderSidebar(alerts);
    const severitySelect = screen.getAllByRole('combobox')[0];
    const options = Array.from(severitySelect.querySelectorAll('option')).map(o => o.value);
    expect(options).toEqual(['all', 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW']);
  });

  it('only shows first 20 alerts', () => {
    const alerts = Array.from({ length: 25 }, (_, i) =>
      makeAlert({ stock_id: `s${i}` }, i)
    );
    renderSidebar(alerts);
    // 25 alert items rendered, 20 visible
    const items = screen.getAllByRole('button');
    const stockCount = screen.getAllByText(/台積電/).length;
    expect(stockCount).toBe(20);
    expect(items.length).toBeGreaterThanOrEqual(20);
  });

  it('navigates to stock page on click', () => {
    const onClose = vi.fn();
    const alerts = [makeAlert({ stock_id: '2330' }, 1)];
    renderSidebar(alerts, { onClose });
    fireEvent.click(screen.getByText('台積電'));
    expect(onClose).toHaveBeenCalled();
  });

  it('calls onMarkAllRead and onClear', () => {
    const onMarkAllRead = vi.fn();
    const onClear = vi.fn();
    renderSidebar([makeAlert({}, 1)], { onMarkAllRead, onClear });
    fireEvent.click(screen.getByText('已讀全部'));
    fireEvent.click(screen.getByText('清空'));
    expect(onMarkAllRead).toHaveBeenCalledTimes(1);
    expect(onClear).toHaveBeenCalledTimes(1);
  });
});