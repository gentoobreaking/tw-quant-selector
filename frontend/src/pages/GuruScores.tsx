import { useQuery } from '@tanstack/react-query';
import { useState, useMemo } from 'react';
import { fetchGuruScores } from '../api/client';
import SkeletonLoader from '../components/SkeletonLoader';
import styles from './GuruScores.module.css';

const CRITERIA_LABELS: Record<string, string> = {
  roa_positive: 'ROA > 0',
  cf_positive: '營業現金流 > 0',
  delta_roa_positive: 'ΔROA > 0',
  accruals_negative: '應計項目 < 0',
  delta_leverage_negative: 'Δ槓桿 < 0',
  delta_current_ratio_positive: 'Δ流動比率 > 0',
  no_new_shares: '無新股發行',
  delta_gross_margin_positive: 'Δ毛利率 > 0',
  delta_asset_turnover_positive: 'Δ資產週轉率 > 0',
};

const CRITERIA_ORDER = [
  'roa_positive',
  'cf_positive',
  'delta_roa_positive',
  'accruals_negative',
  'delta_leverage_negative',
  'delta_current_ratio_positive',
  'no_new_shares',
  'delta_gross_margin_positive',
  'delta_asset_turnover_positive',
];

interface CriterionDetail {
  key: string;
  label: string;
  category: string;
}

const CRITERIA_DETAILS: CriterionDetail[] = [
  { key: 'roa_positive', label: 'ROA > 0', category: '獲利能力' },
  { key: 'cf_positive', label: '營業現金流 > 0', category: '獲利能力' },
  { key: 'delta_roa_positive', label: 'ΔROA > 0', category: '獲利能力' },
  { key: 'accruals_negative', label: '應計項目 < 0', category: '獲利能力' },
  { key: 'delta_leverage_negative', label: 'Δ槓桿 < 0', category: '財務結構' },
  { key: 'delta_current_ratio_positive', label: 'Δ流動比率 > 0', category: '財務結構' },
  { key: 'no_new_shares', label: '無新股發行', category: '財務結構' },
  { key: 'delta_gross_margin_positive', label: 'Δ毛利率 > 0', category: '營運效率' },
  { key: 'delta_asset_turnover_positive', label: 'Δ資產週轉率 > 0', category: '營運效率' },
];

function fmt(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') {
    if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(2) + ' 億';
    if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(0) + ' 萬';
    if (Math.abs(v) >= 1) return v.toFixed(4);
    return v.toFixed(4);
  }
  return String(v);
}

function pct(v: unknown): string {
  if (v === null || v === undefined || typeof v !== 'number') return '—';
  return (v * 100).toFixed(2) + '%';
}

function ScoreBadge({ score }: { score: number | null }) {
  if (score === null) return <span className={styles.na}>N/A</span>;
  const level = score >= 7 ? 'high' : score >= 5 ? 'mid' : 'low';
  return <span className={`${styles.badge} ${styles[level]}`}>{score}/9</span>;
}

function CriteriaRow({ criteria }: { criteria: Record<string, unknown> | null }) {
  if (!criteria) return <span className={styles.na}>—</span>;
  return (
    <div className={styles.criteriaGrid}>
      {CRITERIA_ORDER.map((key) => {
        const val = criteria[key];
        if (val === undefined) return null;
        return (
          <span
            key={key}
            className={`${styles.criterion} ${val ? styles.pass : styles.fail}`}
            title={CRITERIA_LABELS[key]}
          >
            {val ? '✓' : '✗'} {CRITERIA_LABELS[key]}
          </span>
        );
      })}
    </div>
  );
}

function DetailRow({ criteria }: { criteria: Record<string, unknown> | null }) {
  if (!criteria) return null;
  const rows: { label: string; result: string; display: string }[] = [];
  let lastCategory = '';

  for (const cd of CRITERIA_DETAILS) {
    const boolVal = criteria[cd.key];
    const result = boolVal === true ? '✓ 通過' : '✗ 未過';
    let display = '';

    switch (cd.key) {
      case 'roa_positive':
        display = `ROA = ${pct(criteria.roa_value)}  > 0`;
        break;
      case 'cf_positive':
        display = `Net Income = ${fmt(criteria.cf_value)}  > 0`;
        break;
      case 'delta_roa_positive': {
        const cur = criteria.roa_value;
        const prv = criteria.delta_roa_last;
        const d = criteria.delta_roa_value;
        if (cur != null && prv != null) {
          display = `今年 ROA = ${pct(cur)}，去年 ROA = ${pct(prv)}，Δ = ${pct(d)}  > 0`;
        }
        break;
      }
      case 'accruals_negative':
        display = `Net Income = ${fmt(criteria.accruals_value)}  > 0（替代條件）`;
        break;
      case 'delta_leverage_negative': {
        const cur = criteria.delta_leverage_value;
        const prv = criteria.delta_leverage_last;
        if (cur != null && prv != null) {
          display = `今年 D/E = ${fmt(cur)}，去年 D/E = ${fmt(prv)}，Δ = ${fmt(Number(cur) - Number(prv))}  < 0`;
        }
        break;
      }
      case 'delta_current_ratio_positive':
        display = String(criteria.delta_current_ratio_label ?? '—');
        break;
      case 'no_new_shares':
        display = String(criteria.no_new_shares_label ?? '—');
        break;
      case 'delta_gross_margin_positive': {
        const cur = criteria.delta_gross_margin_value;
        const prv = criteria.delta_gross_margin_last;
        if (cur != null && prv != null) {
          display = `今年毛利率 = ${pct(cur)}，去年毛利率 = ${pct(prv)}，Δ = ${pct(Number(cur) - Number(prv))}  > 0`;
        }
        break;
      }
      case 'delta_asset_turnover_positive': {
        const cur = criteria.delta_asset_turnover_value;
        const prv = criteria.delta_asset_turnover_last;
        if (cur != null && prv != null) {
          display = `今年週轉率 = ${fmt(cur)}，去年週轉率 = ${fmt(prv)}，Δ = ${fmt(Number(cur) - Number(prv))}  > 0`;
        }
        break;
      }
    }

    const showCategory = cd.category !== lastCategory;
    lastCategory = cd.category;

    if (showCategory) {
      rows.push({ label: `── ${cd.category} ──`, result: '', display: '' });
    }
    rows.push({ label: cd.label, result, display });
  }

  return (
    <div className={styles.detailWrap}>
      <table className={styles.detailTable}>
        <thead>
          <tr>
            <th>項目</th>
            <th>實際計算</th>
            <th>結果</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className={String(r.label).startsWith('──') ? styles.categoryRow : ''}>
              <td className={String(r.label).startsWith('──') ? styles.categoryLabel : ''}>{r.label}</td>
              <td style={{ fontFamily: 'var(--font-data, monospace)', fontSize: '0.75rem', color: 'var(--text-muted)' }}>{r.display}</td>
              <td className={String(r.result).includes('✓') ? styles.passText : String(r.result).includes('✗') ? styles.failText : ''}>{r.result}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FilterBar({
  minScore, setMinScore,
  passFilter, setPassFilter,
}: {
  minScore: number;
  setMinScore: (v: number) => void;
  passFilter: boolean | undefined;
  setPassFilter: (v: boolean | undefined) => void;
}) {
  return (
    <div className={styles.filterBar}>
      <label>
        最低 F-Score：
        <select value={minScore} onChange={(e) => setMinScore(Number(e.target.value))}>
          <option value={0}>全部</option>
          <option value={5}>≥ 5</option>
          <option value={7}>≥ 7</option>
          <option value={9}>9</option>
        </select>
      </label>
      <label>
        篩選：
        <select
          value={passFilter === undefined ? 'all' : passFilter ? 'pass' : 'fail'}
          onChange={(e) => {
            const v = e.target.value;
            setPassFilter(v === 'all' ? undefined : v === 'pass');
          }}
        >
          <option value="all">全部（顯示所有）</option>
          <option value="pass">通過（F-Score ≥ 7）</option>
          <option value="fail">{'未通過（F-Score < 7）'}</option>
        </select>
      </label>
    </div>
  );
}

export default function GuruScores() {
  const [minScore, setMinScore] = useState(0);
  const [passFilter, setPassFilter] = useState<boolean | undefined>(undefined);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggleExpand = (sid: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(sid)) next.delete(sid);
      else next.add(sid);
      return next;
    });
  };

  const { data, isLoading, error } = useQuery({
    queryKey: ['guru-scores', minScore, passFilter],
    queryFn: () => fetchGuruScores({ guru: 'piotroski', min_score: minScore || undefined, pass_filter: passFilter, limit: 200 }),
    refetchInterval: 60_000,
  });

  const csvData = useMemo(() => {
    if (!data) return '';
    const header = ['stock_id', 'stock_name', 'score', 'pass_filter', ...CRITERIA_ORDER];
    const rows = data.map((item) => {
      const detail = item.criteria_detail ?? {};
      return [item.stock_id, item.name ?? '', String(item.score ?? ''), String(item.pass_filter), ...CRITERIA_ORDER.map((k) => String(detail[k] ?? ''))];
    });
    return [header.join(','), ...rows.map((r) => r.join(','))].join('\n');
  }, [data]);

  const [showHelp, setShowHelp] = useState(false);

  const handleExportCSV = () => {
    const blob = new Blob([csvData], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'guru-scores.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <h1>Guru Score — Piotroski F-Score</h1>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className={styles.exportBtn} onClick={() => setShowHelp((v) => !v)}>
            {showHelp ? '隱藏說明' : '說明'}
          </button>
          <button className={styles.exportBtn} onClick={handleExportCSV} disabled={!data?.length}>
            匯出 CSV
          </button>
        </div>
      </div>

      {showHelp && (
        <div className={styles.helpBox}>
          <p><strong>Piotroski F-Score</strong> 由 9 個評估指標組成，每符合一項條件即得 1 分，不符合得 0 分。</p>

          <h4>📊 獲利能力（Profitability）— 最高 4 分</h4>
          <ol>
            <li><strong>ROA &gt; 0：</strong>當年度總資產報酬率大於 0（得 1 分）。</li>
            <li><strong>營業現金流 (CFO) &gt; 0：</strong>當年度營業活動現金流量大於 0（得 1 分）。</li>
            <li><strong>ΔROA &gt; 0：</strong>當年度 ROA 高於前一年度 ROA（得 1 分）。</li>
            <li><strong>盈餘品質：</strong>當年度營業現金流 (CFO) 大於淨利 (Net Income)（得 1 分）。</li>
          </ol>

          <h4>🏦 槓桿與流動性（Funding）— 最高 3 分</h4>
          <ol start={5}>
            <li><strong>Δ槓桿 &lt; 0：</strong>當年度負債佔比低於前一年度（得 1 分）。</li>
            <li><strong>Δ流動比率 &gt; 0：</strong>當年度流動比率高於前一年度（得 1 分）。</li>
            <li><strong>未發行新股：</strong>當年度沒有發行新股（得 1 分）。</li>
          </ol>

          <h4>⚙️ 營運效率（Operating Efficiency）— 最高 2 分</h4>
          <ol start={8}>
            <li><strong>Δ毛利率 &gt; 0：</strong>當年度毛利率高於前一年度（得 1 分）。</li>
            <li><strong>Δ資產週轉率 &gt; 0：</strong>當年度資產週轉率高於前一年度（得 1 分）。</li>
          </ol>

          <h4>📈 得分分析與投資應用</h4>
          <table className={styles.helpTable}>
            <thead><tr><th>得分</th><th>評定</th><th>建議</th></tr></thead>
            <tbody>
              <tr><td className={styles.helpScore}>8 - 9</td><td>極佳 (Strong)</td><td>基本面強勁，營運全面好轉。通常是優質的價值股。</td></tr>
              <tr><td className={styles.helpScore}>3 - 7</td><td>穩定 (Stable)</td><td>財務狀況平庸，缺乏明顯營運亮點，需搭配其他指標觀察。</td></tr>
              <tr><td className={styles.helpScore}>0 - 2</td><td>脆弱 (Weak)</td><td>財務狀況危急，破產或違約風險高。應嚴格避開。</td></tr>
            </tbody>
          </table>
        </div>
      )}

      <FilterBar
        minScore={minScore}
        setMinScore={setMinScore}
        passFilter={passFilter}
        setPassFilter={setPassFilter}
      />

      {isLoading && <SkeletonLoader variant="table" rows={10} />}
      {error && <div className={styles.error}>載入失敗：{(error as Error).message}</div>}

      {data && (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.expandCol}></th>
                <th>股票</th>
                <th>名稱</th>
                <th>F-Score</th>
                <th>通過</th>
                <th>細項評分 (9 項)</th>
              </tr>
            </thead>
            <tbody>
              {data.map((item) => (
                <>
                  <tr key={item.stock_id} className={expanded.has(item.stock_id) ? styles.rowExpanded : ''}>
                    <td>
                      <button className={styles.expandBtn} onClick={() => toggleExpand(item.stock_id)}>
                        {expanded.has(item.stock_id) ? '−' : '+'}
                      </button>
                    </td>
                    <td className={styles.stockId}>{item.stock_id}</td>
                    <td>{item.name ?? '—'}</td>
                    <td><ScoreBadge score={item.score} /></td>
                    <td>
                      {item.pass_filter
                        ? <span className={styles.filterPass}>通過</span>
                        : <span className={styles.filterFail}>未過</span>}
                    </td>
                    <td><CriteriaRow criteria={item.criteria_detail} /></td>
                  </tr>
                  {expanded.has(item.stock_id) && (
                    <tr key={`${item.stock_id}-detail`}>
                      <td colSpan={6} className={styles.detailCell}>
                        <DetailRow criteria={item.criteria_detail} />
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
