import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import AlertRulesPanel from '../components/AlertRulesPanel';
import { ToastProvider } from '../components/Toast';

vi.mock('../api/client', () => ({
  fetchAlertRules: vi.fn(),
  updateAlertRule: vi.fn(),
}));

import { fetchAlertRules, updateAlertRule, type AlertRuleItem } from '../api/client';

const TECH_MA_RULE: AlertRuleItem = {
  rule_name: 'TECH_MA_CROSS',
  updated_at: '2026-06-05T10:00:00+08:00',
  enabled: true,
  threshold: 0.0,
  cooldown_seconds: 3600,
  severity: 'MEDIUM',
  description: '價格站上/跌破移動平均線（60分MA）',
  config_json: '{"period": 60, "direction": "above"}',
  message_template: null,
};

const VOLUME_RULE: AlertRuleItem = {
  rule_name: 'VOLUME_SPIKE',
  updated_at: '2026-06-05T10:00:00+08:00',
  enabled: true,
  threshold: 10.0,
  cooldown_seconds: 3600,
  severity: 'MEDIUM',
  description: '成交量暴增',
  config_json: '{}',
  message_template: null,
};

function renderPanel(rules = [TECH_MA_RULE, VOLUME_RULE]) {
  vi.mocked(fetchAlertRules).mockResolvedValue({ rules: [...rules], count: rules.length });
  return render(
    <ToastProvider>
      <AlertRulesPanel />
    </ToastProvider>
  );
}

describe('AlertRulesPanel', () => {
  beforeEach(() => {
    vi.mocked(fetchAlertRules).mockReset();
    vi.mocked(updateAlertRule).mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders rule count and grouped categories', async () => {
    renderPanel();
    expect(await screen.findByText('共 2 條警示規則，可個別啟用/停用並調整閾值與參數。')).toBeInTheDocument();
    expect(screen.getByText('技術分析 Technical')).toBeInTheDocument();
    expect(screen.getByText('智慧警示 Smart Alerts')).toBeInTheDocument();
    expect(screen.getByText('TECH_MA_CROSS')).toBeInTheDocument();
    expect(screen.getByText('VOLUME_SPIKE')).toBeInTheDocument();
  });

  it('shows empty state with retry when no rules', async () => {
    renderPanel([]);
    expect(await screen.findByText('目前無警示規則資料。請重新整理頁面或執行後端排程以初始化規則。')).toBeInTheDocument();
  });

  it('toggles enabled via checkbox and calls updateAlertRule', async () => {
    vi.mocked(updateAlertRule).mockResolvedValue({ ...VOLUME_RULE, enabled: false });
    renderPanel();
    await screen.findByText('VOLUME_SPIKE');

    // All checkboxes are unnamed; smart alerts card renders before tech card
    const checkboxes = screen.getAllByRole('checkbox');
    expect(checkboxes.length).toBe(2);
    fireEvent.click(checkboxes[0]);

    await waitFor(() => expect(updateAlertRule).toHaveBeenCalledWith('VOLUME_SPIKE', { enabled: false }));
  });

  it('renders tech config fields for TECH_MA_CROSS (direction / period)', async () => {
    renderPanel();
    await screen.findByText('TECH_MA_CROSS');

    expect(screen.getByText('方向')).toBeInTheDocument();
    expect(screen.getByText('MA 週期')).toBeInTheDocument();
    const directionSelect = screen.getAllByRole('combobox').find(s =>
      Array.from(s.querySelectorAll('option')).some(o => o.value === 'above'));
    expect(directionSelect).toBeDefined();
    expect(directionSelect).toHaveValue('above');
    // cooldown (3600s -> 60min) also shows 60; period input is the one with min=5
    const periodInput = screen.getAllByDisplayValue('60').find(i => (i as HTMLInputElement).min === '5');
    expect(periodInput).toBeInTheDocument();
  });

  it('updates tech config_json on period change', async () => {
    vi.mocked(updateAlertRule).mockResolvedValue(TECH_MA_RULE);
    renderPanel();
    await screen.findByText('TECH_MA_CROSS');

    const periodInput = screen.getAllByDisplayValue('60').find(i => (i as HTMLInputElement).min === '5');
    expect(periodInput).toBeInTheDocument();

    vi.useFakeTimers();
    fireEvent.change(periodInput!, { target: { value: '120' } });
    act(() => { vi.advanceTimersByTime(800); });
    await act(async () => { await Promise.resolve(); });

    expect(updateAlertRule).toHaveBeenCalledWith(
      'TECH_MA_CROSS',
      { config_json: '{"period":120,"direction":"above"}' }
    );
    vi.useRealTimers();
  });

  it('shows cooldown in minutes', async () => {
    renderPanel([{ ...VOLUME_RULE, cooldown_seconds: 1800 }]);
    await screen.findByText('VOLUME_SPIKE');
    expect(screen.getByDisplayValue('30')).toBeInTheDocument();
  });

  it('shows severity label in Chinese', async () => {
    renderPanel();
    await screen.findByText('TECH_MA_CROSS');
    expect(screen.getAllByText('中').length).toBeGreaterThanOrEqual(1);
  });
});