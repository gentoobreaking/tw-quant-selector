/* eslint-disable react-hooks/set-state-in-effect */
import { useState, useEffect, useCallback, useRef } from 'react';
import { fetchAlertRules, updateAlertRule, type AlertRuleItem } from '../api/client';
import SkeletonScreen from './SkeletonScreen';
import { useToast } from './Toast';
import styles from './AlertRulesPanel.module.css';

const CATEGORIES: { key: string; label: string; color: string; rules: string[] }[] = [
  { key: 'data', label: '資料新鮮度 Data Freshness', color: '#4a90d9', rules: ['DATA_PRICE_DELAY', 'DATA_INSTITUTIONAL_DELAY', 'DATA_PRICE_MISSING'] },
  { key: 'system', label: '系統健康 System Health', color: '#e67e22', rules: ['SYS_DB_UNREACHABLE', 'SYS_SCHEDULER_STOPPED', 'SYS_DISK_SPACE', 'SIGNALS_EMPTY'] },
  { key: 'inst', label: '法人 Institutional', color: '#2ecc71', rules: ['INST_HEAVY_BUY', 'INST_HEAVY_SELL', 'INST_DIVERGENCE', 'INST_CONSEC_BUY', 'INST_QUARTER_END'] },
  { key: 'price', label: '即時價格 Real-time Price', color: '#e74c3c', rules: ['PRICE_LIMIT_UP', 'PRICE_LIMIT_DOWN', 'PRICE_UNUSUAL_VOLUME', 'PRICE_PE_EXTREME', 'PRICE_STOP_LOSS', 'PRICE_MIS_UNAVAILABLE'] },
  { key: 'smart', label: '智慧警示 Smart Alerts', color: '#9b59b6', rules: ['VOLUME_SPIKE', 'HIGH_VOL_NO_MOVE', 'TURNOVER_MONSTER', 'INTRADAY_VOLATILITY', 'INDUSTRY_MOMENTUM', 'AGAINST_TREND', 'LOW_PRICE_JUNK_RALLY', 'ETF_PREMIUM_DISCOUNT', 'WHALE_MOVE', 'ACTIVE_ETF_HYPE'] },
  { key: 'tech', label: '技術分析 Technical', color: '#e67e22', rules: ['TECH_MA_CROSS', 'TECH_KD_CROSS'] },
  { key: 'index', label: '大盤指數 TAIEX', color: '#1abc9c', rules: ['TECH_INDEX_MA', 'TECH_INDEX_KD'] },
];

const SEVERITY_LABELS: Record<string, string> = { CRITICAL: '關鍵', HIGH: '高', MEDIUM: '中', LOW: '低' };

function groupByCategory(rules: AlertRuleItem[]): Map<string, AlertRuleItem[]> {
  const map = new Map<string, AlertRuleItem[]>();
  for (const cat of CATEGORIES) {
    const items = rules.filter(r => cat.rules.includes(r.rule_name));
    if (items.length) map.set(cat.key, items);
  }
  return map;
}

export default function AlertRulesPanel() {
  const [rules, setRules] = useState<AlertRuleItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [savingRule, setSavingRule] = useState<string | null>(null);
  const { addToast } = useToast();
  const debounceTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const loadRules = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchAlertRules();
      setRules(data.rules);
    } catch (e: unknown) {
      addToast(`載入警示規則失敗: ${e instanceof Error ? e.message : String(e)}`, 'high');
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  useEffect(() => { loadRules(); }, [loadRules]);

  const saveRule = useCallback(async (ruleName: string, updates: Partial<AlertRuleItem>) => {
    setSavingRule(ruleName);
    try {
      const updated = await updateAlertRule(ruleName, updates);
      setRules(prev => prev.map(r => r.rule_name === ruleName ? updated : r));
      addToast(`已更新 ${ruleName}`, 'low');
    } catch (e: unknown) {
      addToast(`更新 ${ruleName} 失敗: ${e instanceof Error ? e.message : String(e)}`, 'high');
      loadRules();
    } finally {
      setSavingRule(null);
    }
  }, [addToast, loadRules]);

  const handleToggle = (rule: AlertRuleItem) => {
    saveRule(rule.rule_name, { enabled: !rule.enabled });
  };

  const handleChange = (ruleName: string, field: string, value: number | string | null) => {
    setRules(prev => prev.map(r => r.rule_name === ruleName ? { ...r, [field]: value } : r));
    const existing = debounceTimers.current.get(ruleName);
    if (existing) clearTimeout(existing);
    const timer = setTimeout(() => {
      saveRule(ruleName, { [field]: value });
      debounceTimers.current.delete(ruleName);
    }, 800);
    debounceTimers.current.set(ruleName, timer);
  };

  const grouped = groupByCategory(rules);

  return (
    <SkeletonScreen loading={loading} variant="card" rows={5} width="100%" height={800}>
      <div className={styles.panel}>
        <p className={styles.hint}>共 {rules.length} 條警示規則，可個別啟用/停用並調整閾值與參數。</p>
        {rules.length === 0 ? (
          <div className={styles.empty}>
            <p>目前無警示規則資料。請重新整理頁面或執行後端排程以初始化規則。</p>
            <button className={styles.retryBtn} onClick={loadRules}>重新載入</button>
          </div>
        ) : (
        <div className={styles.grid}>
          {CATEGORIES.map(cat => {
            const catRules = grouped.get(cat.key);
            if (!catRules) return null;
            return (
              <div key={cat.key} className={styles.card}>
                <div className={styles.cardHeader} style={{ borderLeftColor: cat.color }}>
                  <span className={styles.cardTitle}>{cat.label}</span>
                  <span className={styles.cardCount}>{catRules.length} 條規則</span>
                </div>
                <div className={styles.ruleList}>
                  {catRules.map(rule => (
                    <RuleRow
                      key={rule.rule_name}
                      rule={rule}
                      saving={savingRule === rule.rule_name}
                      onToggle={handleToggle}
                      onChange={handleChange}
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
        )}
      </div>
    </SkeletonScreen>
  );
}

function RuleRow({ rule, saving, onToggle, onChange }: {
  rule: AlertRuleItem;
  saving: boolean;
  onToggle: (r: AlertRuleItem) => void;
  onChange: (name: string, field: string, value: number | string | null) => void;
}) {
  const thresholdHint = getThresholdHint(rule.rule_name);
  const cooldownMin = Math.round(rule.cooldown_seconds / 60);
  const isTech = rule.rule_name.startsWith('TECH_');

  let config: Record<string, string | number> = {};
  try { config = JSON.parse(rule.config_json || '{}'); } catch { config = {}; }

  const handleConfigChange = (key: string, value: string | number) => {
    const next = { ...config, [key]: value };
    onChange(rule.rule_name, 'config_json', JSON.stringify(next));
  };

  return (
    <div className={`${styles.ruleRow} ${!rule.enabled ? styles.disabled : ''}`}>
      <div className={styles.ruleHeader}>
        <div className={styles.ruleNameRow}>
          <span className={styles.ruleName}>{rule.rule_name}</span>
          <span className={`${styles.severityBadge} ${styles[`sev_${rule.severity}`]}`}>
            {SEVERITY_LABELS[rule.severity] ?? rule.severity}
          </span>
          {saving && <span className={styles.saving}>儲存中…</span>}
        </div>
        <label className={styles.toggle}>
          <input type="checkbox" checked={rule.enabled} onChange={() => onToggle(rule)} />
          <span className={styles.toggleSlider} />
        </label>
      </div>
      {rule.description && <p className={styles.ruleDesc}>{rule.description}</p>}

      <div className={styles.ruleFields}>
        <div className={styles.field}>
          <label className={styles.fieldLabel}>嚴重度</label>
          <select
            value={rule.severity}
            onChange={e => onChange(rule.rule_name, 'severity', e.target.value)}
            className={styles.select}
          >
            <option value="LOW">低 LOW</option>
            <option value="MEDIUM">中 MEDIUM</option>
            <option value="HIGH">高 HIGH</option>
            <option value="CRITICAL">關鍵 CRITICAL</option>
          </select>
        </div>

        <div className={styles.field}>
          <label className={styles.fieldLabel}>
            冷卻時間
            <span className={styles.fieldUnit}>分鐘</span>
          </label>
          <input
            type="number"
            min={0}
            value={cooldownMin}
            onChange={e => onChange(rule.rule_name, 'cooldown_seconds', Number(e.target.value) * 60)}
            className={styles.input}
          />
        </div>

        {thresholdHint !== null && (
          <div className={styles.field}>
            <label className={styles.fieldLabel}>
              閾值
              <span className={styles.fieldUnit}>{thresholdHint.unit}</span>
            </label>
            <input
              type="number"
              step={thresholdHint.step}
              value={rule.threshold ?? ''}
              onChange={e => onChange(rule.rule_name, 'threshold', e.target.value === '' ? null : Number(e.target.value))}
              className={styles.input}
              placeholder="無"
            />
          </div>
        )}
      </div>

      <div className={styles.field} style={{ marginTop: 'var(--space-2)', flexBasis: '100%' }}>
        <label className={styles.fieldLabel}>
          告警訊息模板
          <span className={styles.fieldUnit}>可使用 {'{variable_name}'} 變數</span>
        </label>
        <input
          type="text"
          value={rule.message_template ?? ''}
          onChange={e => onChange(rule.rule_name, 'message_template', e.target.value || null)}
          className={styles.input}
          placeholder="留空則使用預設訊息"
          style={{ width: '100%', fontFamily: 'var(--font-data)' }}
        />
      </div>

      {isTech && (
        <div className={styles.techConfig}>
          {(rule.rule_name === 'TECH_MA_CROSS' || rule.rule_name === 'TECH_INDEX_MA') && (
            <>
              <div className={styles.field}>
                <label className={styles.fieldLabel}>方向</label>
                <select
                  value={String(config.direction || 'above')}
                  onChange={e => handleConfigChange('direction', e.target.value)}
                  className={styles.select}
                >
                  <option value="above">站上 MA</option>
                  <option value="below">跌破 MA</option>
                </select>
              </div>
              <div className={styles.field}>
                <label className={styles.fieldLabel}>
                  MA 週期
                  <span className={styles.fieldUnit}>根 K</span>
                </label>
                <input
                  type="number"
                  min={5}
                  max={240}
                  value={config.period ?? 60}
                  onChange={e => handleConfigChange('period', Number(e.target.value))}
                  className={styles.input}
                />
              </div>
            </>
          )}
          {(rule.rule_name === 'TECH_KD_CROSS' || rule.rule_name === 'TECH_INDEX_KD') && (
            <>
              <div className={styles.field}>
                <label className={styles.fieldLabel}>區域</label>
                <select
                  value={String(config.zone || 'overbought')}
                  onChange={e => handleConfigChange('zone', e.target.value)}
                  className={styles.select}
                >
                  <option value="overbought">超買（K{'>'}80）</option>
                  <option value="oversold">超賣（K{'<'}20）</option>
                </select>
              </div>
              <div className={styles.field}>
                <label className={styles.fieldLabel}>
                  KD N
                  <span className={styles.fieldUnit}>週期</span>
                </label>
                <input
                  type="number"
                  min={5}
                  max={240}
                  value={config.kd_n ?? 60}
                  onChange={e => handleConfigChange('kd_n', Number(e.target.value))}
                  className={styles.input}
                />
              </div>
              <div className={styles.field}>
                <label className={styles.fieldLabel}>
                  K1
                  <span className={styles.fieldUnit}>平滑</span>
                </label>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={config.kd_k1 ?? 3}
                  onChange={e => handleConfigChange('kd_k1', Number(e.target.value))}
                  className={styles.input}
                />
              </div>
              <div className={styles.field}>
                <label className={styles.fieldLabel}>
                  D1
                  <span className={styles.fieldUnit}>平滑</span>
                </label>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={config.kd_d1 ?? 3}
                  onChange={e => handleConfigChange('kd_d1', Number(e.target.value))}
                  className={styles.input}
                />
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function getThresholdHint(ruleName: string): { unit: string; step: number } | null {
  if (['DATA_PRICE_DELAY', 'DATA_INSTITUTIONAL_DELAY'].includes(ruleName)) return { unit: '小時', step: 0.5 };
  if (ruleName === 'DATA_PRICE_MISSING') return { unit: '天', step: 1 };
  if (ruleName === 'SYS_SCHEDULER_STOPPED') return { unit: '小時', step: 1 };
  if (ruleName === 'SYS_DISK_SPACE') return { unit: '%', step: 1 };
  if (['INST_HEAVY_BUY', 'INST_HEAVY_SELL', 'INST_DIVERGENCE', 'INST_CONSEC_BUY'].includes(ruleName)) return { unit: '天', step: 1 };
  if (ruleName === 'INST_QUARTER_END') return { unit: '天', step: 1 };
  if (['PRICE_LIMIT_UP', 'PRICE_STOP_LOSS'].includes(ruleName)) return { unit: '%', step: 0.1 };
  if (ruleName === 'PRICE_LIMIT_DOWN') return { unit: '%', step: 0.1 };
  if (ruleName === 'PRICE_UNUSUAL_VOLUME') return { unit: 'x 均量', step: 0.1 };
  if (ruleName === 'PRICE_PE_EXTREME') return { unit: '百分位', step: 1 };
  if (ruleName === 'PRICE_MIS_UNAVAILABLE') return { unit: '分鐘', step: 1 };
  if (ruleName === 'VOLUME_SPIKE') return { unit: 'x 中位數', step: 0.5 };
  if (ruleName === 'HIGH_VOL_NO_MOVE') return { unit: '名次', step: 1 };
  if (ruleName === 'TURNOVER_MONSTER') return { unit: '佔比', step: 0.001 };
  if (ruleName === 'INTRADAY_VOLATILITY') return { unit: '%', step: 0.5 };
  if (ruleName === 'INDUSTRY_MOMENTUM') return { unit: '比率', step: 0.05 };
  if (ruleName === 'AGAINST_TREND') return { unit: '%', step: 0.1 };
  if (ruleName === 'LOW_PRICE_JUNK_RALLY') return { unit: '比率', step: 0.05 };
  if (ruleName === 'ETF_PREMIUM_DISCOUNT') return { unit: '溢價', step: 0.001 };
  if (ruleName === 'WHALE_MOVE') return { unit: '%', step: 0.1 };
  if (ruleName === 'ACTIVE_ETF_HYPE') return { unit: '分位', step: 0.05 };
  if (ruleName === 'TECH_MA_CROSS') return { unit: '% 偏移', step: 0.1 };
  if (ruleName === 'TECH_KD_CROSS') return { unit: 'K值', step: 1 };
  if (ruleName === 'TECH_INDEX_MA') return { unit: '% 偏移', step: 0.1 };
  if (ruleName === 'TECH_INDEX_KD') return { unit: 'K值', step: 1 };
  return null;
}
