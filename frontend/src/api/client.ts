const API_BASE = '';

async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const opts: RequestInit = { method };
  if (body) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(`${API_BASE}${path}`, opts);
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  const json = await res.json();
  if (json.error) throw new Error(json.error.message || 'API error');
  return (json.data ?? json) as T;
}

export function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  return request<T>(path, method, body);
}

export const apiFetch = api;

export interface DashboardData {
  table_counts: Record<string, number>;
  price_date_range: { min: string | null; max: string | null };
  val_date_range: { min: string | null; max: string | null };
  tracker: { dataset: string; status: string; count: number }[];
  top_stocks: { stock_id: string; days: number }[];
}

export function fetchDashboard(): Promise<DashboardData> {
  return request<DashboardData>('/api/v1/dashboard');
}

export function fetchLatestSignals(strategy = 'composite', includeEtf = true): Promise<unknown> {
  return request(`/api/v1/signals/latest?strategy=${encodeURIComponent(strategy)}&include_etf=${includeEtf}`);
}

export function fetchStockDetail(stockId: string): Promise<unknown> {
  return request(`/api/v1/stock/${stockId}`);
}

export function fetchSignalCalendar(): Promise<string[]> {
  return request<string[]>('/api/v1/signals/calendar');
}

export function fetchSignalsByDate(date: string, strategy = 'composite', includeEtf = true): Promise<unknown> {
  return request(`/api/v1/signals/${date}?strategy=${encodeURIComponent(strategy)}&include_etf=${includeEtf}`);
}

export interface LatestValuation {
  stock_id: string;
  pb: number | null;
  industry: string | null;
}

export function fetchLatestValuations(): Promise<LatestValuation[]> {
  return request<LatestValuation[]>('/api/v1/valuations/latest');
}

export interface FactorHistoryPoint {
  date: string;
  momentum: number | null;
  value: number | null;
  quality: number | null;
  growth: number | null;
  guru: number | null;
  institutional: number | null;
}

export function fetchFactorHistory(stockId: string): Promise<FactorHistoryPoint[]> {
  return request<FactorHistoryPoint[]>(`/api/v1/stock/${stockId}/factor-history`);
}

export function fetchStrategyConfig() {
  return request('/api/v1/strategies/config');
}

export interface LogEntry {
  id?: number;
  timestamp: string;
  module: string;
  event: string;
  severity: string;
}

export interface DatasetInfo {
  dataset: string;
  status: string;
  count: number;
  last_updated: string | null;
}

export function fetchMonitorLogs(): Promise<LogEntry[]> {
  return request<LogEntry[]>('/api/v1/monitor/logs');
}

export function fetchMonitorDatasets(): Promise<DatasetInfo[]> {
  return request<DatasetInfo[]>('/api/v1/monitor/datasets');
}

export interface StockSearchResult {
  stock_id: string;
  name: string;
  market: string;
  is_etf: boolean;
  industry: string | null;
}

export function searchStocks(q: string): Promise<StockSearchResult[]> {
  return request<StockSearchResult[]>(`/api/v1/stocks/search?q=${encodeURIComponent(q)}`);
}

export interface EquityPoint {
  date: string;
  value: number;
  benchmark: number | null;
  drawdown: number | null;
}

export function fetchBacktestEquity(runId: string): Promise<EquityPoint[]> {
  return request<EquityPoint[]>(`/api/v1/backtest/${runId}/equity`);
}

export interface BacktestTrade {
  date: string;
  stock_id: string;
  action: string;
  shares: number;
  price: number | null;
  value: number | null;
  weight: number | null;
}

export interface BacktestDetail {
  run_id: string;
  created_at: string | null;
  start_date: string | null;
  end_date: string | null;
  metrics: {
    total_return: number | null;
    cagr: number | null;
    sharpe: number | null;
    max_drawdown: number | null;
    calmar: number | null;
    turnover: number | null;
    total_trades: number;
  };
  trades: BacktestTrade[];
}

export function fetchBacktestDetail(runId: string): Promise<BacktestDetail> {
  return request<BacktestDetail>(`/api/v1/backtest/${runId}/detail`);
}

export interface DatasetStatus {
  name: string;
  status: string;
  count: number;
  last_updated: string | null;
}

export interface DataStatus {
  last_price_update: string | null;
  stock_count: number;
  signal_dates: number;
  latest_signal_date: string | null;
  datasets: DatasetStatus[];
}

export function fetchDataStatus(): Promise<DataStatus> {
  return api('/api/v1/data/status');
}

// ── Research / Analysis endpoints ──

export function fetchScoreTrend(stockId: string, days = 30): Promise<{ signal_date: string; strategy: string; score: number | null }[]> {
  return api(`/api/v1/stocks/${stockId}/score-trend?days=${days}`);
}

export function fetchInstitutionalFlows(stockId: string, startDate = '', endDate = ''): Promise<{
  trade_date: string; foreign_net: number; sity_net: number; dealer_net: number; total_net: number; close: number | null;
}[]> {
  const params = new URLSearchParams({ stock_id: stockId });
  if (startDate) params.set('start_date', startDate);
  if (endDate) params.set('end_date', endDate);
  return api(`/api/v1/institutional/flows?${params}`);
}

export function fetchInstitutionalSummary(): Promise<{ foreign_net: number; sity_net: number; dealer_net: number }> {
  return api('/api/v1/institutional/summary');
}

export interface InstTopItem {
  stock_id: string; stock_name: string;
  foreign_net: number; sity_net: number; dealer_net: number;
  total_net: number; close: number | null;
}

export interface InstTopResult {
  date: string; sort_by: string; order: string; data: InstTopItem[];
}

export function fetchInstitutionalTop(topN = 10, date = '', sortBy = 'total_net', order = 'desc'): Promise<InstTopResult> {
  const params = new URLSearchParams({ top_n: String(topN), sort_by: sortBy, order });
  if (date) params.set('date', date);
  return api(`/api/v1/institutional/top?${params}`);
}

export function runSensitivityAnalysis(params: {
  start_date: string; end_date?: string; parameter: string; values: number[];
}): Promise<{ param_value: number; sharpe: number | null; cagr: number | null; total_return: number | null; max_drawdown: number | null }[]> {
  return api('/api/v1/backtest/sensitivity', 'POST', params);
}

export function fetchIcAnalysis(days = 365): Promise<{ signal_date: string; strategy: string; ic: number }[]> {
  return api(`/api/v1/factor/ic-analysis?days=${days}`);
}

export function fetchQuintileReturns(days = 730): Promise<{ strategy: string; quintile: number; avg_return: number }[]> {
  return api(`/api/v1/factor/quintile-returns?days=${days}`);
}

export function fetchFactorCorrelation(): Promise<{ strategies: string[]; matrix: (number | null)[][] }> {
  return api('/api/v1/factor/correlation');
}

export function runInstitutionalValidation(nDays = 10): Promise<{ buy: { count: number; avg_excess_return: number }; sell: { count: number; avg_excess_return: number } }> {
  return api('/api/v1/factor/institutional-validation', 'POST', { n_days: nDays });
}

export function fetchAlertHistory(params?: {
  severity?: string; rule_name?: string; start_date?: string; end_date?: string; unresolved_only?: boolean; limit?: number;
}): Promise<{
  id: string; rule_name: string; severity: string; message: string; context_data: unknown; triggered_at: string | null; resolved_at: string | null; resolution_note: string | null;
}[]> {
  const q = new URLSearchParams();
  if (params?.severity) q.set('severity', params.severity);
  if (params?.rule_name) q.set('rule_name', params.rule_name);
  if (params?.start_date) q.set('start_date', params.start_date);
  if (params?.end_date) q.set('end_date', params.end_date);
  if (params?.unresolved_only) q.set('unresolved_only', 'true');
  if (params?.limit) q.set('limit', String(params.limit));
  return api(`/api/v1/alerts/history?${q}`);
}

export function resolveAlert(alertId: string, note = ''): Promise<{ resolved: string }> {
  return api(`/api/v1/alerts/${alertId}/resolve`, 'POST', { note });
}

export function fetchAlertStats(startDate = '', endDate = ''): Promise<{
  daily: { date: string; severity: string; count: number }[];
  weekly: { week: string; severity: string; count: number }[];
}> {
  const q = new URLSearchParams();
  if (startDate) q.set('start_date', startDate);
  if (endDate) q.set('end_date', endDate);
  return api(`/api/v1/alerts/stats?${q}`);
}

export function fetchIntradaySnapshots(stockId: string): Promise<{ snapshot_time: string; price: number | null; volume: number }[]> {
  return api(`/api/v1/intraday/${stockId}`);
}

// ── Market Screen (T124) ──

export interface MarketScreenItem {
  stock_id: string;
  name: string;
  industry: string;
  is_etf: boolean;
  close: number | null;
  change_pct: number | null;
  volume: number | null;
}

export function fetchMarketScreen(params?: {
  include_stocks?: boolean;
  include_etf?: boolean;
  volume_spike?: boolean;
  against_trend?: boolean;
  limit?: number;
}): Promise<MarketScreenItem[]> {
  const q = new URLSearchParams();
  if (params?.include_stocks !== undefined) q.set('include_stocks', String(params.include_stocks));
  if (params?.include_etf !== undefined) q.set('include_etf', String(params.include_etf));
  if (params?.volume_spike) q.set('volume_spike', 'true');
  if (params?.against_trend) q.set('against_trend', 'true');
  if (params?.limit) q.set('limit', String(params.limit));
  return api(`/api/v1/market/screen?${q}`);
}

// ── Smart Alert History (T124) ──

export function fetchSmartAlertHistory(limit = 50): Promise<{
  type: string; timestamp: string; data: Record<string, unknown>;
}[]> {
  return api(`/api/v1/smart-alerts/history?limit=${limit}`);
}

// ── Intraday K-line (T132) ──

export interface IntradayKlinePoint {
  k_time: string;
  period_min: number;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number;
}

export function fetchIntradayKline(stockId: string, periodMin = 60, days = 1): Promise<IntradayKlinePoint[]> {
  return api(`/api/v1/intraday/${stockId}/kline?period_min=${periodMin}&days=${days}`);
}

// ── Guru Scores (T126) ──

export interface GuruScoreItem {
  stock_id: string;
  name: string | null;
  score_date: string;
  score: number | null;
  pass_filter: boolean;
  criteria_detail: Record<string, unknown> | null;
}

export interface GuruScoreParams {
  guru?: string;
  date?: string;
  min_score?: number;
  pass_filter?: boolean;
  limit?: number;
  offset?: number;
}

// ── Alert Rules Config (T131) ──

export interface AlertRuleItem {
  rule_name: string;
  enabled: boolean;
  threshold: number | null;
  cooldown_seconds: number;
  severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  description: string | null;
  updated_at: string | null;
  config_json: string;
  message_template: string | null;
}

export function fetchAlertRules(enabled?: boolean): Promise<{ rules: AlertRuleItem[]; count: number }> {
  const q = enabled !== undefined ? `?enabled=${enabled}` : '';
  return api(`/api/v1/alerts/rules${q}`);
}

export function updateAlertRule(ruleName: string, body: Partial<Pick<AlertRuleItem, 'enabled' | 'threshold' | 'cooldown_seconds' | 'severity' | 'description' | 'config_json' | 'message_template'>>): Promise<AlertRuleItem> {
  return api(`/api/v1/alerts/rules/${ruleName}`, 'PUT', body);
}

export function fetchGuruScores(params?: GuruScoreParams): Promise<GuruScoreItem[]> {
  const q = new URLSearchParams();
  if (params?.guru) q.set('guru', params.guru);
  if (params?.date) q.set('date', params.date);
  if (params?.min_score !== undefined) q.set('min_score', String(params.min_score));
  if (params?.pass_filter !== undefined) q.set('pass_filter', String(params.pass_filter));
  if (params?.limit) q.set('limit', String(params.limit));
  if (params?.offset) q.set('offset', String(params.offset));
  return api(`/api/v1/guru-scores?${q}`);
}
