import { useState, useEffect, useCallback } from 'react';
import type { ConfigHistoryEntry } from '../types';
import SkeletonLoader from '../components/SkeletonLoader';
import Dropdown from '../components/Dropdown';
import { DesktopOnly, MobileMessage } from '../utils/responsive';
import { RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, ResponsiveContainer } from 'recharts';
import { formatNumber } from '../utils/format';
import styles from './Strategy.module.css';

const API = '';
async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, init);
  if (!res.ok) throw new Error(`API ${res.status}`);
  const body = await res.json();
  if (body.error) throw new Error(body.error.message || 'API error');
  return (body.data ?? body) as T;
}

interface Holding {
  stock_id: string;
  shares: number;
  is_etf?: boolean;
  price?: number;
}

interface PreviewResult {
  to_buy: { stock_id: string; score: number; rank: number }[];
  to_sell: Holding[];
  unchanged: Holding[];
  turnover_pct: number;
  cost: {
    buy_cost: number;
    sell_proceeds: number;
    total_cost: number;
    buy_count: number;
    sell_count: number;
  };
}

interface StrategyConfig {
  strategies: Record<string, {
    params: Record<string, number | boolean>;
    param_types: Record<string, string>;
  }>;
  default_weights: Record<string, number>;
  universe_defaults: {
    include_etf: boolean;
    min_market_cap: number;
    exclude_financial: boolean;
    top_n_stocks: number;
    top_n_etfs: number;
  };
}

const STRATEGY_LABELS: Record<string, string> = {
  momentum: '動能',
  value: '價值',
  quality: '品質',
  growth: '成長',
  guru: '大師評分',
  institutional: '法人',
};

const STRATEGY_COLORS: Record<string, string> = {
  momentum: 'var(--color-momentum)',
  value: 'var(--color-value)',
  quality: 'var(--color-quality)',
  growth: 'var(--color-growth)',
  guru: 'var(--color-guru)',
  institutional: 'var(--color-institutional)',
};

const GURU_PRESETS: Record<string, { label: string; color: string; weights: Record<string, number> }> = {
  buffett:  { label: '巴菲特', color: '#2563eb', weights: { momentum: 10, value: 35, quality: 35, growth: 20 } },
  graham:   { label: '葛拉漢', color: '#059669', weights: { momentum: 5, value: 50, quality: 30, growth: 15 } },
  lynch:    { label: '彼得林區', color: '#d97706', weights: { momentum: 15, value: 25, quality: 20, growth: 40 } },
  greenblatt: { label: '葛林布拉特', color: '#7c3aed', weights: { momentum: 10, value: 40, quality: 35, growth: 15 } },
  oneil:    { label: '歐尼爾', color: '#dc2626', weights: { momentum: 40, value: 10, quality: 15, growth: 35 } },
  fisher:   { label: '費雪', color: '#0891b2', weights: { momentum: 15, value: 15, quality: 35, growth: 35 } },
};

const UNIVERSE_BASE = 1300;

interface GuruCondition {
  name: string;
  source: string;
  threshold: string;
}

interface GuruData {
  id: string;
  nameCN: string;
  nameEN: string;
  color: string;
  compatibility: '高' | '中';
  tags: string[];
  conditions: GuruCondition[];
}

const GURU_LIST: GuruData[] = [
  { id: 'buffett', nameCN: '巴菲特', nameEN: 'Buffett', color: '#2563eb', compatibility: '高', tags: ['價值', '品質'],
    conditions: [
      { name: 'ROE > 15%', source: 'financials', threshold: '>15%' },
      { name: '負債比 < 50%', source: 'financials', threshold: '<50%' },
      { name: '毛利率 > 30%', source: 'financials', threshold: '>30%' },
      { name: '近3年 EPS 正成長', source: 'financials', threshold: '每年>0' },
      { name: 'PE < 25', source: 'valuations', threshold: '<25' },
      { name: 'FCF 近4季 > 0', source: 'financials', threshold: '>0' },
    ] },
  { id: 'graham', nameCN: '葛拉漢', nameEN: 'Graham', color: '#059669', compatibility: '高', tags: ['價值'],
    conditions: [
      { name: 'PE < 大盤 × 0.9', source: 'valuations', threshold: '<18' },
      { name: 'PB < 2.0', source: 'valuations', threshold: '<2.0' },
      { name: '流動比率 > 1.5', source: 'financials', threshold: '>1.5' },
      { name: '長期負債/流動資產 < 1', source: 'financials', threshold: '<1' },
      { name: '近5年 EPS > 0', source: 'financials', threshold: '每年>0' },
      { name: '近3年配息', source: 'financials', threshold: '每年配' },
    ] },
  { id: 'lynch', nameCN: '彼得林區', nameEN: 'Lynch', color: '#d97706', compatibility: '中', tags: ['成長', '價值'],
    conditions: [
      { name: 'PEG < 1', source: 'derived', threshold: '<1' },
      { name: '月營收 YoY > 15%', source: 'monthly_revenue', threshold: '>15%' },
      { name: 'EPS 年增率 > 20%', source: 'financials', threshold: '>20%' },
      { name: 'PE < EPS 成長率 × 100', source: 'valuations', threshold: 'GARP' },
      { name: '市值 < 1000 億', source: 'valuations', threshold: '<1000億' },
    ] },
  { id: 'greenblatt', nameCN: '葛林布拉特', nameEN: 'Greenblatt', color: '#7c3aed', compatibility: '高', tags: ['價值', '品質'],
    conditions: [
      { name: '排除金融/公用事業', source: 'universe', threshold: '固定' },
      { name: 'EY 排名前 20%', source: 'valuations', threshold: '前20%' },
      { name: 'ROIC 排名前 20%', source: 'derived', threshold: '前20%' },
      { name: '合計排名前 30', source: 'composite', threshold: '前30' },
    ] },
  { id: 'oneil', nameCN: '歐尼爾', nameEN: "O'Neil", color: '#dc2626', compatibility: '中', tags: ['動能', '成長'],
    conditions: [
      { name: 'EPS 年增率 > 25%', source: 'financials', threshold: '>25%' },
      { name: '近3年 EPS 每年 > 25%', source: 'financials', threshold: '每年>25%' },
      { name: '營收加速', source: 'monthly_revenue', threshold: '逐月增' },
      { name: '成交量確認', source: 'daily_prices', threshold: '量增' },
      { name: 'RS 排名前 25%', source: 'daily_prices', threshold: '前25%' },
    ] },
  { id: 'fisher', nameCN: '費雪', nameEN: 'Fisher', color: '#0891b2', compatibility: '中', tags: ['成長', '品質'],
    conditions: [
      { name: '營收 5年 CAGR > 15%', source: 'monthly_revenue', threshold: '>15%' },
      { name: '研發費用率 > 3%', source: 'financials', threshold: '>3%' },
      { name: '營業利益率標準差 < 3%', source: 'financials', threshold: '<3%' },
      { name: '董監持股 > 10%', source: 'stocks', threshold: '>10%' },
      { name: '毛利率逐季提升', source: 'financials', threshold: '逐季增' },
    ] },
];

type GuruMode = 'filter' | 'scoring' | 'preset';

const GURU_MODE_LABELS: Record<GuruMode, string> = {
  filter: '篩選器',
  scoring: '評分因子',
  preset: '快速預設',
};

const GURU_MODE_DESC: Record<GuruMode, string> = {
  filter: '以大師條件做為硬性過濾器，先篩選再評分',
  scoring: '將大師條件轉換為第五評分因子，納入綜合評分',
  preset: '載入大師的建議權重組合，可在此基礎上微調',
};

const STRATEGY_NAMES = ['momentum', 'value', 'quality', 'growth', 'guru', 'institutional'];
const STRATEGY_CN: Record<string, string> = {
  momentum: '動能', value: '價值', quality: '品質', growth: '成長', guru: '大師', institutional: '法人',
};

interface ParamField {
  key: string;
  label: string;
  min: number;
  max: number;
  step: number;
  suffix?: string;
}

const STRATEGY_PARAM_SCHEMAS: Record<string, { fields: ParamField[]; subFactors?: string[]; autoSum?: string }> = {
  momentum: {
    fields: [
      { key: 'lookback_long', label: '回顧期', min: 120, max: 504, step: 1, suffix: '天' },
      { key: 'lookback_short', label: '排除近期', min: 0, max: 63, step: 1, suffix: '天' },
      { key: 'min_data_days', label: '最少資料天數', min: 252, max: 252, step: 1, suffix: '天' },
    ],
  },
  value: {
    fields: [
      { key: 'max_pb', label: '本淨比上限', min: 5, max: 50, step: 1 },
      { key: 'max_pe', label: '本益比上限', min: 20, max: 200, step: 5 },
      { key: 'min_yield', label: '殖利率下限', min: 0, max: 5, step: 0.5, suffix: '%' },
    ],
  },
  quality: {
    fields: [
      { key: 'roe_weight', label: 'ROE 權重', min: 0, max: 100, step: 5, suffix: '%' },
      { key: 'leverage_weight', label: '槓桿權重', min: 0, max: 100, step: 5, suffix: '%' },
      { key: 'stability_weight', label: '穩定性權重', min: 0, max: 100, step: 5, suffix: '%' },
      { key: 'lookback_quarters', label: '回顧季數', min: 2, max: 8, step: 1, suffix: '季' },
    ],
    subFactors: ['roe_weight', 'leverage_weight', 'stability_weight'],
    autoSum: '100',
  },
  growth: {
    fields: [
      { key: 'rev_months', label: '營收均值月數', min: 1, max: 6, step: 1, suffix: '月' },
      { key: 'rev_weight', label: '營收因子權重', min: 0, max: 100, step: 5, suffix: '%' },
      { key: 'eps_weight', label: 'EPS 因子權重', min: 0, max: 100, step: 5, suffix: '%' },
    ],
    subFactors: ['rev_weight', 'eps_weight'],
    autoSum: '100',
  },
  guru: {
    fields: [
      { key: 'selected_guru', label: '參考大師', min: 0, max: 0, step: 0 },
    ],
  },
  institutional: {
    fields: [
      { key: 'foreign_weight', label: '外資權重', min: 0, max: 100, step: 5, suffix: '%' },
      { key: 'trust_weight', label: '投信權重', min: 0, max: 100, step: 5, suffix: '%' },
      { key: 'consec_weight', label: '連續權重', min: 0, max: 100, step: 5, suffix: '%' },
      { key: 'lookback_days', label: '回顧天數', min: 5, max: 60, step: 5, suffix: '天' },
      { key: 'min_flow_days', label: '最少天數', min: 1, max: 20, step: 1, suffix: '天' },
    ],
    subFactors: ['foreign_weight', 'trust_weight', 'consec_weight'],
    autoSum: '100',
  },
};

export function autoRebalance(
  weights: Record<string, number>,
  changedId: string,
  newVal: number,
  locked: Set<string>,
): Record<string, number> | null {
  const lockedKeys = [...locked].filter(k => k !== changedId);
  const unlocked = Object.keys(weights).filter(k => k !== changedId && !locked.has(k));
  if (unlocked.length === 0) return null;

  const lockedSum = lockedKeys.reduce((s, k) => s + weights[k], 0);
  const remaining = 100 - newVal - lockedSum;
  const unlockedSum = unlocked.reduce((s, k) => s + weights[k], 0);

  if (unlockedSum === 0) {
    const equalShare = Math.floor(remaining / unlocked.length / 5) * 5;
    const result: Record<string, number> = { ...weights, [changedId]: newVal };
    let total = newVal + lockedSum;
    for (const k of unlocked) {
      result[k] = Math.max(0, equalShare);
      total += result[k];
    }
    let diff = 100 - total;
    for (const k of unlocked) {
      if (diff === 0) break;
      const adj = result[k] + diff;
      if (adj >= 0) { result[k] = adj; diff = 0; }
    }
    return result;
  }

  const scale = remaining / unlockedSum;
  const result: Record<string, number> = { ...weights, [changedId]: newVal };
  let total = newVal + lockedSum;

  for (const k of unlocked) {
    const v = Math.round(weights[k] * scale / 5) * 5;
    result[k] = Math.max(0, v);
    total += result[k];
  }

  const diff = 100 - total;
  if (diff !== 0) {
    const adjustK = unlocked.find(k => result[k] > 0) || unlocked[0];
    if (adjustK) result[adjustK] = Math.max(0, result[adjustK] + diff);
  }

  return result;
}

export default function Strategy() {
  const [config, setConfig] = useState<StrategyConfig | null>(null);
  const [weights, setWeights] = useState<Record<string, number>>({});
  const [params, setParams] = useState<Record<string, Record<string, number | boolean | string>>>({});
  const [includeEft, setIncludeEft] = useState(false);
  const [minMarketCap, setMinMarketCap] = useState(3_000_000_000);
  const [minDailyVolume, setMinDailyVolume] = useState(0);
  const [excludeFinancial, setExcludeFinancial] = useState(true);
  const [excludeKY, setExcludeKY] = useState(false);
  const [topN, setTopN] = useState(20);
  const [topNEtf, setTopNEtf] = useState(3);
  const [lockedSliders, setLockedSliders] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem('tw_quant_locked_sliders') || '[]')); }
    catch { return new Set(); }
  });
  const [showModal, setShowModal] = useState(false);
  const [zeroWarn, setZeroWarn] = useState<{ id: string; label: string; prevWeights: Record<string, number> } | null>(null);
  const [lockedMsg, setLockedMsg] = useState('');
  const [selectedGuru, setSelectedGuru] = useState<string | null>(null);
  const [guruFilter, setGuruFilter] = useState<string | null>(null);
  const [guruMode, setGuruMode] = useState<GuruMode>('filter');
  const [guruFeedback, setGuruFeedback] = useState<string | null>(null);
  const [corrMatrix, setCorrMatrix] = useState<Record<string, Record<string, number>> | null>(null);
  const [configHistory, setConfigHistory] = useState<ConfigHistoryEntry[]>([]);
  const [historyNote, setHistoryNote] = useState('');
  const [showNoteInput, setShowNoteInput] = useState(false);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyPageSize, setHistoryPageSize] = useState(5);
  const [selectedHistoryIds, setSelectedHistoryIds] = useState<Set<number>>(new Set());
  const [historyDeleting, setHistoryDeleting] = useState(false);
  const [universeCount, setUniverseCount] = useState<number | null>(null);
  const [universeLoading, setUniverseLoading] = useState(false);
  const [universeError, setUniverseError] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiFetch<StrategyConfig>('/api/v1/strategies/config')
      .then((cfg) => {
        setConfig(cfg);
        const saved = (() => {
          try { return JSON.parse(localStorage.getItem('tw_quant_strategy_state') || '{}'); }
          catch { return {}; }
        })();
        setWeights(saved.weights || { ...cfg.default_weights });
        const p: Record<string, Record<string, number | boolean | string>> = saved.params || {};
        for (const [name, strat] of Object.entries(cfg.strategies)) {
          if (!p[name]) p[name] = { ...strat.params };
        }
        setParams(p);
        setIncludeEft(saved.includeEft ?? cfg.universe_defaults.include_etf);
        setMinMarketCap(saved.minMarketCap ?? cfg.universe_defaults.min_market_cap);
        setMinDailyVolume(saved.minDailyVolume ?? 0);
        setExcludeFinancial(saved.excludeFinancial ?? cfg.universe_defaults.exclude_financial);
        setExcludeKY(saved.excludeKY ?? false);
        setTopN(saved.topN ?? cfg.universe_defaults.top_n_stocks);
        setTopNEtf(saved.topNEtf ?? cfg.universe_defaults.top_n_etfs);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!config) return;
    try {
      localStorage.setItem('tw_quant_strategy_state', JSON.stringify({
        weights, params, includeEft, minMarketCap, minDailyVolume,
        excludeFinancial, excludeKY, topN, topNEtf,
      }));
    } catch { /* ignore */ }
  }, [weights, params, includeEft, minMarketCap, minDailyVolume,
      excludeFinancial, excludeKY, topN, topNEtf, config]);

  const loadPreset = (key: string) => {
    const preset = GURU_PRESETS[key];
    if (!preset) return;
    setWeights((w) => {
      const next = { ...w, ...preset.weights };
      const total = Object.values(next).reduce((s, v) => s + v, 0);
      if (total !== 100) {
        const diff = 100 - total;
        const adjustK = Object.keys(next).find(k => next[k] > 0) || 'momentum';
        next[adjustK] = Math.max(0, next[adjustK] + diff);
      }
      const changed = Object.entries(preset.weights)
        .map(([k, v]) => `${STRATEGY_LABELS[k] || k} ${w[k] || 0}% → ${v}%`)
        .join('、');
      setGuruFeedback(`已套用 ${preset.label} 權重：${changed}`);
      setTimeout(() => setGuruFeedback(null), 5000);
      return next;
    });
  };

  const updateWeight = (name: string, val: number) => {
    const clamped = Math.round(val / 5) * 5;
    if (lockedSliders.has(name)) {
      setWeights((w) => ({ ...w, [name]: clamped }));
      return;
    }
    const prev = weights;
    const result = autoRebalance(weights, name, clamped, lockedSliders);
    if (!result) {
      setLockedMsg(`無法調整：所有其他策略皆已鎖定，請先解鎖至少一項`);
      setTimeout(() => setLockedMsg(''), 3500);
      return;
    }
    const dropped = Object.entries(result).find(([k, v]) => v === 0 && prev[k] > 0);
    if (dropped) {
      setZeroWarn({ id: dropped[0], label: STRATEGY_LABELS[dropped[0]] || dropped[0], prevWeights: prev });
      setWeights(result);
    } else {
      setWeights(result);
    }
  };

  const confirmZero = () => {
    setZeroWarn(null);
  };

  const cancelZero = () => {
    if (zeroWarn) setWeights(zeroWarn.prevWeights);
    setZeroWarn(null);
  };

  const toggleLock = (name: string) => {
    setLockedSliders((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name); else next.add(name);
      localStorage.setItem('tw_quant_locked_sliders', JSON.stringify([...next]));
      return next;
    });
  };

  const resetLocks = () => {
    setLockedSliders(new Set());
    localStorage.setItem('tw_quant_locked_sliders', '[]');
  };

  useEffect(() => {
    localStorage.setItem('tw_quant_locked_sliders', JSON.stringify([...lockedSliders]));
  }, [lockedSliders]);

  useEffect(() => {
    localStorage.setItem('tw_quant_advanced_params', JSON.stringify(params));
  }, [params]);

  useEffect(() => {
    try {
      const saved = localStorage.getItem('tw_quant_advanced_params');
      if (saved) {
        const parsed = JSON.parse(saved);
        setParams((p) => {
          const merged = { ...p };
          for (const [strat, vals] of Object.entries(parsed)) {
            if (merged[strat]) merged[strat] = { ...merged[strat], ...(vals as Record<string, number | boolean>) };
          }
          return merged;
        });
      }
    } catch { /* ignore */ }
  }, []);

  const updateParam = (strategy: string, key: string, val: number | boolean | string) => {
    const numVal = typeof val === 'number' ? val : (val === true ? 1 : (val === false ? 0 : 0));
    setParams((p) => {
      const next = { ...p, [strategy]: { ...p[strategy], [key]: val } };
      const schema = STRATEGY_PARAM_SCHEMAS[strategy];
      if (schema?.subFactors && schema.autoSum) {
        const factorKeys = schema.subFactors;
        const targetTotal = Number(schema.autoSum);
        const currentSum = factorKeys.reduce((s, k) => s + Number(next[strategy]?.[k] ?? 0), 0);
        if (currentSum !== targetTotal) {
          const others = factorKeys.filter((k) => k !== key);
          const otherSum = others.reduce((s, k) => s + Number(next[strategy]?.[k] ?? 0), 0);
          const remaining = targetTotal - numVal;
          if (otherSum > 0 && others.length > 0) {
            const scale = remaining / otherSum;
            for (const k of others) {
              const v = Math.round(Number(next[strategy]?.[k] ?? 0) * scale / 5) * 5;
              next[strategy] = { ...next[strategy], [k]: Math.max(0, v) };
            }
          } else if (others.length > 0) {
            const share = Math.floor(remaining / others.length / 5) * 5;
            for (const k of others) {
              next[strategy] = { ...next[strategy], [k]: Math.max(0, share) };
            }
          }
          const newSum = factorKeys.reduce((s, k) => s + Number(next[strategy]?.[k] ?? 0), 0);
          if (newSum !== targetTotal && others.length > 0) {
            const diff = targetTotal - newSum;
            const adjust = others.find((k) => Number(next[strategy]?.[k] ?? 0) > 0) || others[0];
            next[strategy] = { ...next[strategy], [adjust]: Math.max(0, Number(next[strategy]?.[adjust] ?? 0) + diff) };
          }
        }
      }
      return next;
    });
  };

  const fetchUniverseCount = useCallback(async () => {
    setUniverseLoading(true);
    setUniverseError(null);
    try {
      const p = new URLSearchParams();
      if (minMarketCap > 0) p.append('min_market_cap', String(minMarketCap));
      if (minDailyVolume > 0) p.append('min_daily_volume', String(minDailyVolume / 10_000_000));
      if (excludeFinancial) p.append('exclude_financial', 'true');
      if (excludeKY) p.append('exclude_ky', 'true');
      if (includeEft) p.append('include_etf', 'true');
      const data = await apiFetch<{ count: number }>(`/api/v1/universe/count?${p.toString()}`);
      setUniverseCount(data.count);
    } catch (e) {
      setUniverseCount(null);
      setUniverseError(e instanceof Error ? e.message : 'API 請求失敗');
    } finally {
      setUniverseLoading(false);
    }
  }, [minMarketCap, minDailyVolume, excludeFinancial, excludeKY, includeEft]);

  const runPreview = useCallback(async () => {
    if (!config) return;
    try {
      const holdings: Holding[] = (() => {
        try { return JSON.parse(localStorage.getItem('tw_quant_lots') || '[]'); }
        catch { return []; }
      })();
      const data = await apiFetch<PreviewResult>('/api/v1/strategy/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          weights,
          strategy_params: params,
          top_n_stocks: topN,
          top_n_etfs: topNEtf,
          holdings,
        }),
      });
      setPreview(data);
    } catch {
      setPreview(null);
    }
  }, [config, weights, params, topN, topNEtf]);

  useEffect(() => {
    fetchUniverseCount();
  }, [fetchUniverseCount]);

  useEffect(() => {
    const timer = setTimeout(runPreview, 500);
    return () => clearTimeout(timer);
  }, [params, weights, minMarketCap, excludeFinancial, includeEft, topN, topNEtf]);

  useEffect(() => {
    apiFetch<{ matrix: Record<string, Record<string, number>> }>('/api/v1/strategy/correlation')
      .then(d => setCorrMatrix(d.matrix))
      .catch(() => {});
apiFetch<ConfigHistoryEntry[]>('/api/v1/strategy/config-history?limit=200')

      .then(d => setConfigHistory(d))

      .catch(() => {});
  }, []);

  const saveConfigHistory = useCallback(async (changedBy: string) => {
    try {
      await apiFetch('/api/v1/strategy/config-history', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          weights,
          advanced_params: params,
          guru_config: {},
          universe_config: { includeEft, minMarketCap, excludeFinancial, topN, topNEtf },
          changed_by: changedBy,
          note: historyNote,
        }),
      });
      setHistoryNote('');
      const updated = await apiFetch<ConfigHistoryEntry[]>('/api/v1/strategy/config-history?limit=200');
      setConfigHistory(updated);
    } catch { /* ignore */ }
  }, [weights, params, includeEft, minMarketCap, excludeFinancial, topN, topNEtf, historyNote]);

  const totalHistoryPages = Math.max(1, Math.ceil(configHistory.length / historyPageSize));
  const paginatedHistory = configHistory.slice(
    (historyPage - 1) * historyPageSize,
    historyPage * historyPageSize,
  );
  const allHistorySelected = selectedHistoryIds.size === configHistory.length && configHistory.length > 0;

  const toggleSelectHistory = (id: number) => {
    setSelectedHistoryIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleSelectAllHistory = () => {
    if (allHistorySelected) {
      setSelectedHistoryIds(new Set());
    } else {
      setSelectedHistoryIds(new Set(configHistory.map(h => h.config_id!)));
    }
  };

  const batchDeleteHistory = async () => {
    if (selectedHistoryIds.size === 0) return;
    setHistoryDeleting(true);
    try {
      await apiFetch('/api/v1/strategy/config-history/batch', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify([...selectedHistoryIds]),
      });
      setConfigHistory(prev => prev.filter(h => !selectedHistoryIds.has(h.config_id!)));
      setSelectedHistoryIds(new Set());
      setHistoryPage(1);
    } catch { /* ignore */ } finally {
      setHistoryDeleting(false);
    }
  };

  const resetToDefaults = () => {
    if (!config) return;
    setWeights({ ...config.default_weights });
    const p: Record<string, Record<string, number | boolean | string>> = {};
    for (const [name, strat] of Object.entries(config.strategies)) {
      p[name] = { ...strat.params };
    }
    setParams(p);
    setGuruFilter(null);
    setIncludeEft(config.universe_defaults.include_etf);
    setMinMarketCap(config.universe_defaults.min_market_cap);
    setMinDailyVolume(0);
    setExcludeFinancial(config.universe_defaults.exclude_financial);
    setExcludeKY(false);
    setTopN(config.universe_defaults.top_n_stocks);
    setTopNEtf(config.universe_defaults.top_n_etfs);
    resetLocks();
    setLockedMsg('');
  };

  const handleApply = async () => {
    setShowModal(true);
    if (!showNoteInput) setShowNoteInput(true);
    await runPreview();
  };

  const confirmApplyWithHistory = async () => {
    setShowModal(false);
    setShowNoteInput(false);
    await saveConfigHistory('user');
  };

  const strats = config ? Object.keys(config.strategies) : ['momentum', 'value', 'quality', 'growth'];

  if (loading) {
    return <div className={styles.page}><SkeletonLoader variant="card" /><SkeletonLoader variant="table" rows={4} /></div>;
  }

  return (
    <div className={styles.page}>
      <DesktopOnly>
        <div className={styles.header}>
          <h1 className={styles.title}>策略設定 Strategy</h1>
          <div className={styles.headerActions}>
            <button className={styles.resetBtn} onClick={resetToDefaults}>↺ 重設</button>
          </div>
        </div>
        {guruFeedback && <div className={styles.guruFeedback}>{guruFeedback}</div>}
        <div className={styles.presetBar}>
          <span className={styles.presetLabel}>快速預設：</span>
          {Object.entries(GURU_PRESETS).map(([key, g]) => (
            <button key={key} className={styles.presetBtn}
              style={{ '--preset-color': g.color } as React.CSSProperties}
              onClick={() => loadPreset(key)}>
              {g.label}
            </button>
          ))}
        </div>
        <div className={styles.grid}>
          <div className={styles.card}>
            <h3>策略權重</h3>
            <p className={styles.hint}></p>
            {strats.map((name) => (
              <div key={name} className={styles.sliderRow}>
                <label style={{ color: STRATEGY_COLORS[name] || 'var(--text-primary)' }}>
                  {STRATEGY_LABELS[name] || name}
                </label>
                <input type="range" min={0} max={100} step={5} value={weights[name] || 0}
                  onChange={(e) => updateWeight(name, Number(e.target.value))}
                  className={styles.slider} />
                <span className="font-data">{weights[name] || 0}%</span>
                <button className={`${styles.lockBtn} ${lockedSliders.has(name) ? styles.locked : ''}`}
                  onClick={() => toggleLock(name)}
                  title="鎖定此權重，其餘自動補足">
                  {lockedSliders.has(name) ? '🔒' : '🔓'}
                </button>
              </div>
            ))}
            <div className={styles.totalWeight}>
              總和: <span className={`font-data ${Object.values(weights).reduce((s, v) => s + v, 0) === 100 ? styles.ok : styles.warn}`}>
                {Object.values(weights).reduce((s, v) => s + v, 0)}%
              </span>
            </div>
          </div>
          <div className={styles.card}>
            <h3>各策略參數</h3>
            {strats.map((name) => {
              const schema = STRATEGY_PARAM_SCHEMAS[name];
              return (
                <details key={name} className={styles.stratDetails}>
                  <summary style={{ color: STRATEGY_COLORS[name] }}>
                    {STRATEGY_LABELS[name] || name}
                  </summary>
                  {schema?.fields.map((f) => {
                   const val = params[name]?.[f.key] ?? config?.strategies[name]?.params?.[f.key];
                   if (f.key === 'selected_guru') {
                     // 建立大師選項清單
                     const guruOptions = GURU_LIST.map(g => ({
                       value: g.id,
                       label: `${g.nameCN} (${g.nameEN})`
                     }));
                     
                     return (
                       <div key={f.key} className={styles.paramRow}>
                         <label>{f.label}</label>
                         <Dropdown
                           options={guruOptions}
                           value={String(val || 'buffett')}
                           onChange={(value) => updateParam(name, f.key, value)}
                           className={styles.select}
                         />
                       </div>
                     );
                   }
                   const v = Number(val ?? 0);
                   const isFixed = f.min === f.max;
                   return (
                     <div key={f.key} className={styles.paramRow}>
                       <label>{f.label}</label>
                       {isFixed ? (
                         <span className="font-data">{v}{f.suffix || ''}</span>
                       ) : (
                         <div className={styles.filterSliderGroup}>
                           <input type="range" min={f.min} max={f.max} step={f.step} value={v}
                             onChange={(e) => updateParam(name, f.key, Number(e.target.value))}
                             className={styles.slider} />
                           <span className="font-data">{v}{f.suffix || ''}</span>
                         </div>
                       )}
                     </div>
                   );
                  })}                </details>
              );
            })}
          </div>
          <div className={styles.card}>
            <h3>篩選條件</h3>
            <div className={styles.filterRow}>
              <span>排除全額交割股</span>
              <span className={styles.filterFixed} title="固定強制條件">🔒</span>
            </div>
            <div className={styles.filterRow}>
              <label>包含 ETF</label>
              <input type="checkbox" checked={includeEft} onChange={(e) => setIncludeEft(e.target.checked)} />
            </div>
            <div className={styles.filterRow}>
              <label>最低市值</label>
              <div className={styles.filterSliderGroup}>
                <input type="range" min={0} max={200} step={5} value={minMarketCap / 100_000_000}
                  onChange={(e) => setMinMarketCap(Number(e.target.value) * 100_000_000)}
                  className={styles.slider} />
                <span className="font-data">{minMarketCap >= 1_000_000_000 ? formatNumber(minMarketCap, { type: 'market_cap' }) : '不限'}</span>
              </div>
            </div>
            <div className={styles.filterRow}>
              <label>日均成交額</label>
              <div className={styles.filterSliderGroup}>
                <input type="range" min={0} max={5000} step={100} value={minDailyVolume}
                  onChange={(e) => setMinDailyVolume(Number(e.target.value))}
                  className={styles.slider} />
                <span className="font-data">{minDailyVolume > 0 ? `${minDailyVolume}萬` : '不限'}</span>
              </div>
            </div>
            <div className={styles.filterRow}>
              <label>排除金融股</label>
              <input type="checkbox" checked={excludeFinancial} onChange={(e) => setExcludeFinancial(e.target.checked)} />
            </div>
            <div className={styles.filterRow}>
              <label>排除 KY 股</label>
              <input type="checkbox" checked={excludeKY} onChange={(e) => setExcludeKY(e.target.checked)} />
            </div>
            <div className={styles.filterRow}>
              <label>選股數量</label>
              <input type="number" value={topN} min={1} max={100}
                onChange={(e) => setTopN(Number(e.target.value))} />
            </div>
            <div className={styles.filterRow}>
              <label>ETF 數量</label>
              <input type="number" value={topNEtf} min={0} max={20}
                onChange={(e) => setTopNEtf(Number(e.target.value))} />
            </div>
            {guruFilter && (
              <div className={styles.filterRow}>
                <label>大師篩選器</label>
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <span className={styles.highlight} style={{ fontSize: 'var(--font-size-sm)' }}>
                    {GURU_LIST.find(g => g.id === guruFilter)?.nameCN}
                  </span>
                  <button onClick={() => setGuruFilter(null)}
                    style={{ background: 'none', border: 'none', color: 'var(--color-negative)', cursor: 'pointer', padding: 0 }}>
                    ✕
                  </button>
                </div>
              </div>
            )}
            <div className={styles.filterEstimate}>
              {universeLoading ? (
                <>計算中<span className={styles.loadingDots}>...</span></>
              ) : universeCount !== null ? (
                <>
                  篩選結果：<strong className={styles.highlight}>{universeCount}</strong> 檔
                  <span className={styles.muted}>（全市場 {UNIVERSE_BASE} 檔）</span>
                </>
              ) : (
                <span className={styles.errorFallback}>{universeError ?? '無法計算篩選結果'}</span>
              )}
            </div>
          </div>
          <div className={styles.card} style={{ gridColumn: '1 / -1' }}>
            <h3>因子權重雷達圖</h3>
            <div className={styles.radarContainer}>
              {(() => {
                const radarData = STRATEGY_NAMES.map(name => ({
                  factor: STRATEGY_LABELS[name] || name,
                  權重: weights[name] || 0,
                }));
                return (
                  <ResponsiveContainer width="100%" height={320}>
                    <RadarChart data={radarData} margin={{ top: 10, right: 30, bottom: 10, left: 30 }}>
                      <PolarGrid stroke="var(--bg-border)" />
                      <PolarAngleAxis dataKey="factor" tick={{ fill: 'var(--text-secondary)', fontSize: 13 }} />
                      <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fill: 'var(--text-muted)', fontSize: 10 }} />
                      <Radar name="權重" dataKey="權重" stroke="#a78bfa" fill="#a78bfa" fillOpacity={0.2} strokeWidth={2} />
                    </RadarChart>
                  </ResponsiveContainer>
                );
              })()}
            </div>
          </div>
          <div className={styles.card}>
            <h3>再平衡預覽</h3>
            {preview ? (
              <div className={styles.previewPanel}>
                <div className={styles.previewSummary}>
                  <span className={preview.to_buy.length > 0 ? styles.textBuy : ''}>買 {preview.to_buy.length}</span>
                  <span className={preview.to_sell.length > 0 ? styles.textSell : ''}>賣 {preview.to_sell.length}</span>
                  <span className={styles.textHold}>留 {preview.unchanged.length}</span>
                  <span className={styles.textTurnover}>換手 {preview.turnover_pct}%</span>
                </div>
                <div className={styles.previewCost}>
                  預估成本：手續費+稅 ${formatNumber(preview.cost.buy_cost, { type: 'market_cap' })} / 入帳 ${formatNumber(preview.cost.sell_proceeds, { type: 'market_cap' })}
                </div>
                {preview.to_buy.length > 0 && (
                  <details className={styles.previewDetails}>
                    <summary>買入清單 ({preview.to_buy.length})</summary>
                    <div className={styles.previewList}>
                      {preview.to_buy.map(s => (
                        <span key={s.stock_id} className={styles.previewChip}>{s.stock_id}</span>
                      ))}
                    </div>
                  </details>
                )}
                {preview.to_sell.length > 0 && (
                  <details className={styles.previewDetails}>
                    <summary>賣出清單 ({preview.to_sell.length})</summary>
                    <div className={styles.previewList}>
                      {preview.to_sell.map(h => (
                        <span key={h.stock_id} className={styles.previewChip}>{h.stock_id}</span>
                      ))}
                    </div>
                  </details>
                )}
              </div>
            ) : (
              <p className={styles.muted}>調整權重或參數後自動計算</p>
            )}
            {lockedMsg && <p className={styles.lockedMsg}>{lockedMsg}</p>}
            <button className={`${styles.applyBtn} ${Object.values(weights).reduce((s, v) => s + v, 0) !== 100 ? styles.applyDisabled : ''}`}
              onClick={handleApply}
              disabled={Object.values(weights).reduce((s, v) => s + v, 0) !== 100}>
              ▶ 套用設定
            </button>
          </div>
        </div>
        <div className={styles.guruSection}>
          <div className={styles.guruHeader}>
            <h3>大師策略庫 Guru Strategies</h3>
            <div className={styles.guruModeTabs}>
              {(Object.entries(GURU_MODE_LABELS) as [GuruMode, string][]).map(([mode, label]) => (
                <button key={mode}
                  className={`${styles.guruModeTab} ${guruMode === mode ? styles.guruModeActive : ''}`}
                  onClick={() => setGuruMode(mode)}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <p className={styles.guruModeDesc}>{GURU_MODE_DESC[guruMode]}</p>
          <div className={styles.guruGrid}>
            {GURU_LIST.map((guru) => (
              <div key={guru.id}
                className={`${styles.guruCard} ${selectedGuru === guru.id ? styles.guruSelected : ''}`}
                style={{ borderColor: selectedGuru === guru.id ? guru.color : undefined }}
                onClick={() => setSelectedGuru(selectedGuru === guru.id ? null : guru.id)}>
                <div className={styles.guruCardTop}>
                  <span className={styles.guruAvatar} style={{ background: guru.color }}>
                    {guru.nameCN[0]}
                  </span>
                  <span className={`${styles.guruCompat} ${guru.compatibility === '高' ? styles.guruCompatHigh : styles.guruCompatMid}`}>
                    {guru.compatibility}
                  </span>
                </div>
                <div className={styles.guruName}>
                  <span className={styles.guruNameCN}>{guru.nameCN}</span>
                  <span className={styles.guruNameEN}>{guru.nameEN}</span>
                </div>
                <div className={styles.guruTags}>
                  {guru.tags.map(t => (
                    <span key={t} className={styles.guruTag}>{t}</span>
                  ))}
                </div>
                {selectedGuru === guru.id && (
                  <div className={styles.guruConditions}>
                    {guru.conditions.map(c => (
                      <div key={c.name} className={styles.guruConditionRow}>
                        <span className={styles.guruCondName}>{c.name}</span>
                        <span className={styles.guruCondSource}>{c.source}</span>
                        <span className={styles.guruCondThreshold}>{c.threshold}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
          <button className={`${styles.applyBtn} ${styles.guruApplyBtn} ${!selectedGuru ? styles.applyDisabled : ''}`}
            disabled={!selectedGuru}
            onClick={() => {
              if (!selectedGuru) return;
              if (guruMode === 'preset') {
                loadPreset(selectedGuru);
              } else if (guruMode === 'filter') {
                setGuruFilter(selectedGuru);
                setGuruFeedback(`已套用 ${GURU_PRESETS[selectedGuru]?.label || selectedGuru} 為硬性篩選器（將先過濾符合大師條件的股票）`);
                setTimeout(() => setGuruFeedback(null), 5000);
              } else if (guruMode === 'scoring') {
                setWeights(w => {
                  if (w['guru'] > 0) return w;
                  const res = autoRebalance(w, 'guru', 20, lockedSliders);
                  return res || { ...w, guru: 20 };
                });
                setParams(prev => ({
                  ...prev,
                  guru: { ...(prev.guru || {}), selected_guru: selectedGuru }
                }));
                setGuruFeedback(`已套用 ${GURU_PRESETS[selectedGuru]?.label || selectedGuru} 為評分因子（權重已自動調整，請確認下方參數）`);
                setTimeout(() => setGuruFeedback(null), 5000);
              }
            }}>
            {guruMode === 'filter' ? '▶ 套用為篩選器' : guruMode === 'scoring' ? '▶ 套用為評分因子' : '▶ 套用權重組合'}
          </button>
        </div>
        <div className={styles.historySection}>
          <h3>設定歷史 Config History</h3>
          {configHistory.length === 0 ? (
            <p className={styles.muted}>尚無歷史記錄</p>
          ) : (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, cursor: 'pointer' }}>
                  <input type="checkbox" checked={allHistorySelected} onChange={toggleSelectAllHistory} />
                  全選
                </label>
                <button
                  onClick={batchDeleteHistory}
                  disabled={selectedHistoryIds.size === 0 || historyDeleting}
                  style={{
                    padding: '2px 10px', fontSize: 12, cursor: selectedHistoryIds.size > 0 ? 'pointer' : 'default',
                    background: 'var(--color-negative)', color: '#fff', border: 'none', borderRadius: 'var(--radius-btn)',
                    opacity: selectedHistoryIds.size > 0 ? 1 : 0.4,
                  }}
                >
                  {historyDeleting ? '刪除中...' : `刪除所選 (${selectedHistoryIds.size})`}
                </button>
              </div>
              <div className={styles.historyList}>
                {paginatedHistory.map((h: ConfigHistoryEntry) => {
                  let diffParts: string[] = [];
                  try {
                    const w = typeof h.weights === 'string' ? JSON.parse(h.weights) : h.weights;
                    if (w) {
                      diffParts = Object.entries(w as Record<string, number>)
                        .map(([k, v]) => `${STRATEGY_LABELS[k] || k} ${v}%`);
                    }
                    const u = typeof h.universe_config === 'string' ? JSON.parse(h.universe_config) : h.universe_config;
                    if (u?.topN) diffParts.push(`選股 ${u.topN}檔`);
                    if (u?.includeEft) diffParts.push('含ETF');
                    if (u?.excludeFinancial) diffParts.push('排除金融');
                  } catch {}
                  const isSelected = selectedHistoryIds.has(h.config_id!);
                  return (
                    <div key={h.config_id} className={`${styles.historyItem}${isSelected ? ' ' + styles.historyItemSelected : ''}`}>
                      <label style={{ display: 'flex', alignItems: 'center', cursor: 'pointer', marginRight: 8 }}>
                        <input type="checkbox" checked={isSelected} onChange={() => toggleSelectHistory(h.config_id!)} />
                      </label>
                      <div style={{ flex: 1 }}>
                        <div className={styles.historyMeta}>
                          <span className={styles.historyTime}>{h.changed_at?.slice(0, 19).replace('T', ' ')}</span>
                          <span className={styles.historyBy}>{h.changed_by}</span>
                          {h.note && <span className={styles.historyNote}>{h.note}</span>}
                        </div>
                        {diffParts.length > 0 && (
                          <div className={styles.historyDiff}>
                            {diffParts.map((d, i) => <span key={i} className={styles.diffChip}>{d}</span>)}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
                  <span>每頁</span>
                  <Dropdown
                    options={[
                      { value: '5', label: '5' },
                      { value: '10', label: '10' },
                      { value: '20', label: '20' },
                      { value: '30', label: '30' },
                      { value: '50', label: '50' },
                    ]}
                    value={String(historyPageSize)}
                    onChange={v => { setHistoryPageSize(Number(v)); setHistoryPage(1); }}
                  />
                  <span>筆</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
                  <span>第 {historyPage} 頁 / 共 {totalHistoryPages} 頁 ({configHistory.length} 筆)</span>
                  <button
                    onClick={() => setHistoryPage(p => Math.max(1, p - 1))}
                    disabled={historyPage <= 1}
                    style={{
                      padding: '2px 8px', fontSize: 12, cursor: historyPage > 1 ? 'pointer' : 'default',
                      background: 'var(--bg-elevated)', border: '1px solid var(--bg-border)',
                      borderRadius: 'var(--radius-btn)', color: 'var(--text-primary)',
                      opacity: historyPage > 1 ? 1 : 0.4,
                    }}
                  >
                    ◀
                  </button>
                  <span>{historyPage} / {totalHistoryPages}</span>
                  <button
                    onClick={() => setHistoryPage(p => Math.min(totalHistoryPages, p + 1))}
                    disabled={historyPage >= totalHistoryPages}
                    style={{
                      padding: '2px 8px', fontSize: 12, cursor: historyPage < totalHistoryPages ? 'pointer' : 'default',
                      background: 'var(--bg-elevated)', border: '1px solid var(--bg-border)',
                      borderRadius: 'var(--radius-btn)', color: 'var(--text-primary)',
                      opacity: historyPage < totalHistoryPages ? 1 : 0.4,
                    }}
                  >
                    ▶
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
        {corrMatrix && (
          <div className={styles.corrSection}>
            <div className={styles.corrCard}>
              <div className={styles.corrHeaderRow}>
                <h3>因子相關性矩陣 Factor Correlation</h3>
                <div className={styles.corrLegend}>
                  <span className={styles.legendItem}><span className={styles.legendBox} style={{ background: '#ea580c' }}></span> 正相關</span>
                  <span className={styles.legendItem}><span className={styles.legendBox} style={{ background: '#525252' }}></span> 低相關</span>
                  <span className={styles.legendItem}><span className={styles.legendBox} style={{ background: '#059669' }}></span> 負相關</span>
                </div>
              </div>
              <p className={styles.corrHint}>
                此矩陣顯示各策略因子評分之間的 Pearson 相關係數（近一年歷史資料）。<br />
                值域 -1 ~ 1：<strong>正相關</strong>（同漲同跌）→ 分散效果差；<strong>負相關</strong>（互補）→ 分散效果佳。<br />
                調整策略參數或權重後，因子間的相關性會隨之改變，你可在此觀察變化。
              </p>
              
              <div className={styles.corrGrid}>
                <div className={styles.corrRow}>
                  <div className={styles.corrLabel}></div>
                  {STRATEGY_NAMES.map(s => (
                    <div key={s} className={styles.corrHeader} style={{ color: STRATEGY_COLORS[s] }}>
                      {STRATEGY_CN[s]}
                    </div>
                  ))}
                </div>
                {STRATEGY_NAMES.map(s1 => (
                  <div key={s1} className={styles.corrRow}>
                    <div className={styles.corrLabel} style={{ color: STRATEGY_COLORS[s1] }}>
                      {STRATEGY_CN[s1]}
                    </div>
                    {STRATEGY_NAMES.map(s2 => {
                      const v = corrMatrix[s1]?.[s2] ?? 0;
                      const absV = Math.abs(v);
                      // Diagonal is always 1.0 (self-correlation)
                      const isSelf = s1 === s2;
                      
                      const bg = isSelf ? 'var(--color-accent)' :
                        v > 0.5 ? '#ea580c' : 
                        v > 0.2 ? '#f97316' : 
                        v < -0.2 ? '#059669' : '#525252';
                        
                      const textColor = (isSelf || absV > 0.2) ? '#fff' : 'var(--text-secondary)';
                      const displayVal = isSelf ? '1.00' : v.toFixed(2);
                      
                      return (
                        <div key={s2} className={styles.corrCell}
                          style={{ background: bg, color: textColor }}
                          title={`${STRATEGY_CN[s1]} 與 ${STRATEGY_CN[s2]} 的相關性: ${v.toFixed(4)}`}>
                          {displayVal}
                        </div>
                      );
                    })}
                  </div>
                ))}
              </div>
              
              <div className={styles.corrAdvice}>
                {(() => {
                  const pairs: { s1: string; s2: string; v: number }[] = [];
                  for (const s1 of STRATEGY_NAMES) {
                    for (const s2 of STRATEGY_NAMES) {
                      if (s1 < s2) pairs.push({ s1, s2, v: corrMatrix[s1]?.[s2] ?? 0 });
                    }
                  }
                  const high = pairs.filter(p => Math.abs(p.v) > 0.5);
                  const low = pairs.filter(p => Math.abs(p.v) < 0.2);
                  
                  return (
                    <>
                      {high.length > 0 && (
                        <div className={styles.adviceItem}>
                          <span className={styles.adviceIcon}>⚠️</span>
                          <span><strong>{high.map(p => `${STRATEGY_CN[p.s1]}-${STRATEGY_CN[p.s2]}`).join('、')}</strong> 相關性較高，配置時權重建議分散。</span>
                        </div>
                      )}
                      {low.length > 0 && (
                        <div className={styles.adviceItem}>
                          <span className={styles.adviceIcon}>✅</span>
                          <span><strong>{low.map(p => `${STRATEGY_CN[p.s1]}-${STRATEGY_CN[p.s2]}`).join('、')}</strong> 低度相關，組合分散效果佳。</span>
                        </div>
                      )}
                      {high.length === 0 && low.length === 0 && (
                        <div className={styles.adviceItem}>各因子間相關性適中。</div>
                      )}
                    </>
                  );
                })()}
              </div>
            </div>
          </div>
        )}
        {zeroWarn && (
          <div className={styles.modalOverlay} onClick={cancelZero}>
            <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
              <h3>策略將被停用</h3>
              <p className={styles.impactText}>
                <strong>{zeroWarn.label}</strong> 策略將被完全停用（權重降至 0%），確認嗎？
              </p>
              <div className={styles.modalActions}>
                <button className={styles.resetBtn} onClick={cancelZero}>取消</button>
                <button className={styles.applyBtn} onClick={confirmZero}>確認</button>
              </div>
            </div>
          </div>
        )}
        {showModal && (
          <div className={styles.modalOverlay} onClick={() => setShowModal(false)}>
            <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
              <h3>確認套用</h3>
              {preview && (
                <p className={styles.impactText}>
                  買 {preview.to_buy.length} / 賣 {preview.to_sell.length} / 留 {preview.unchanged.length}
                  ｜換手 {preview.turnover_pct}% ｜成本 ${formatNumber(preview.cost.total_cost, { type: 'market_cap' })}
                </p>
              )}
              <div className={styles.filterRow}>
                <label>備註（選填）</label>
                <input type="text" className={styles.noteInput} value={historyNote}
                  onChange={(e) => setHistoryNote(e.target.value)} placeholder="記錄此次變更原因" />
              </div>
              <div className={styles.modalActions}>
                <button className={styles.resetBtn} onClick={() => setShowModal(false)}>取消</button>
                <button className={styles.applyBtn} onClick={confirmApplyWithHistory}>確認套用</button>
              </div>
            </div>
          </div>
        )}
      </DesktopOnly>
      <MobileMessage message="請在桌面環境設定策略" />
    </div>
  );
}
