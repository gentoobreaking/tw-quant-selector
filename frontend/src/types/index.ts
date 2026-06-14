export interface ApiResponse<T> {
  data?: T;
  error?: { message: string };
}

export interface SignalItem {
  signal_date: string;
  stock_id: string;
  name: string;
  strategy: string;
  score: number | null;
  rank: number | null;
  is_selected: boolean;
  close_price: number | null;
  change: number | null;
  change_pct: number | null;
}

export interface ConfigHistoryEntry {
  config_id: number;
  changed_at: string;
  changed_by: string;
  weights: string | Record<string, number>;
  advanced_params?: string | Record<string, unknown>;
  universe_config?: string | Record<string, unknown>;
  note?: string;
}

export interface BacktestRun {
  run_id: string;
  start_date: string | null;
  end_date: string | null;
  total_return: number | null;
  cagr: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
  benchmark?: string;
}

export interface AlertLogEntry {
  id: number;
  log_id: string;
  triggered_at?: string;
  timestamp: string;
  stock_id: string;
  pnl: number | null;
  pnl_pct?: number | null;
  threshold: number | null;
  threshold_type?: string | null;
  threshold_value?: number | null;
  sent: boolean;
  reason?: string | null;
}

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

export interface AlertMessage {
  type: 'alert_triggered';
  timestamp: string;
  data: {
    alert_type: string;
    severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
    stock_id: string;
    stock_name: string;
    message: string;
    details: Record<string, unknown>;
  };
}
