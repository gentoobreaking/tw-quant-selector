import { useState, useEffect, useRef, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { LineChart, Line, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { createChart, ColorType, LineSeries, AreaSeries, type IChartApi, type ISeriesApi, type Time } from 'lightweight-charts';
import { fetchBacktestEquity, runSensitivityAnalysis as apiRunSensitivity, type EquityPoint } from '../api/client';
import { formatNumber, colorize } from '../utils/format';
import { handleApiError } from '../utils/handleApiError';
import { useToast } from '../components/Toast';
import { DesktopOnly } from '../utils/responsive';
import ErrorBoundary from '../components/ErrorBoundary';
import type { BacktestRun } from '../types';
import styles from './Backtest.module.css';


const API = '';
async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, init);
  if (!res.ok) {
    const detail = await res.json().then(b => b.detail || b.message || '').catch(() => '');
    throw new Error(`API ${res.status}${detail ? ': ' + detail : ''}`);
  }
  const body = await res.json();
  if (body.error) throw new Error(body.error.message || 'API error');
  return (body.data ?? body) as T;
}

import SkeletonLoader from '../components/SkeletonLoader';

export default function Backtest() {
  const [searchParams, setSearchParams] = useSearchParams();
  const today = new Date();
  const defaultStart = new Date(today);
  defaultStart.setDate(defaultStart.getDate() - 365);
  const [startDate, setStartDate] = useState(defaultStart.toISOString().slice(0, 10));
  const [endDate, setEndDate] = useState(today.toISOString().slice(0, 10));
  const [benchmark, setBenchmark] = useState('0050');
  const [customUniverse, setCustomUniverse] = useState('');
  const [weights, setWeights] = useState({ momentum: 20, value: 15, quality: 15, growth: 10, institutional: 25 });
  const [topN, setTopN] = useState(20);
  const [initialCapital, setInitialCapital] = useState(1_000_000);
  const [running, setRunning] = useState(false);
  const [history, setHistory] = useState<BacktestRun[]>([]);
  const [result, setResult] = useState<BacktestRun | null>(null);
  const [equityData, setEquityData] = useState<EquityPoint[]>([]);
  const [chartLoading, setChartLoading] = useState(false);
  
  // T052: 比較模式狀態
  const [compareIds, setCompareIds] = useState<string[]>(() => {
    const compareParam = searchParams.get('compare');
    return compareParam ? [compareParam] : [];
  });
  const [compareDataA, setCompareDataA] = useState<EquityPoint[]>([]);
  const [compareDataB, setCompareDataB] = useState<EquityPoint[]>([]);
  
  const isCompareMode = compareIds.length === 2;

  const { addToast } = useToast();

  // TBD: 刪除模式狀態
  const [deleteMode, setDeleteMode] = useState(false);
  const [selectedDeleteIds, setSelectedDeleteIds] = useState<string[]>([]);

  useEffect(() => {
    apiFetch<BacktestRun[]>('/api/v1/backtest/history')
      .then(setHistory)
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (result?.run_id) {
      setChartLoading(true);
      fetchBacktestEquity(result.run_id)
        .then(data => { setEquityData(data); setChartLoading(false); })
        .catch(() => { setEquityData([]); setChartLoading(false); });
    } else {
      setEquityData([]);
    }
  }, [result?.run_id]);

  const updateWeight = (key: keyof typeof weights, val: number) => {
    setWeights((w) => ({ ...w, [key]: val }));
  };

  const totalWeight = Object.values(weights).reduce((s, v) => s + v, 0);

  const runBacktest = async () => {
    setRunning(true);
    try {
      const w: Record<string, number> = {};
      for (const [k, v] of Object.entries(weights)) w[k] = v / 100;
      
      const customList = customUniverse
        .split(',')
        .map(s => s.trim())
        .filter(s => s.length > 0);

      const data = await apiFetch<{ run_id: string; status: string }>('/api/v1/backtest/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          start_date: startDate,
          end_date: endDate || null,
          strategy_weights: w,
          benchmark,
          custom_universe: customList.length > 0 ? customList : null,
        }),
      });
      const detail = await apiFetch<{ metrics: Record<string, number | null>; run_id: string; benchmark?: string }>(`/api/v1/backtest/${data.run_id}`);
      setResult({ run_id: data.run_id, start_date: startDate, end_date: endDate, benchmark: detail.benchmark, ...detail.metrics } as BacktestRun);
      const h = await apiFetch<BacktestRun[]>('/api/v1/backtest/history');
      setHistory(h);
    } catch (e: unknown) {
      handleApiError(e, addToast, '回測執行失敗');
    } finally {
      setRunning(false);
    }
  };

  // T052: 勾選處理
  const toggleCompare = (runId: string) => {
    if (compareIds.includes(runId)) {
      setCompareIds(compareIds.filter(id => id !== runId));
    } else if (compareIds.length < 2) {
      setCompareIds([...compareIds, runId]);
    }
  };

  const deleteRun = async (runId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm('確定刪除此回測記錄？')) return;
    try {
      await apiFetch(`/api/v1/backtest/${runId}`, { method: 'DELETE' });
      setHistory(prev => prev.filter(r => r.run_id !== runId));
      if (result?.run_id === runId) setResult(null);
    } catch (err: unknown) {
      handleApiError(err, addToast, '刪除失敗');
    }
  };

  // TBD: 批量刪除函數
  const deleteSelectedRuns = async () => {
    if (selectedDeleteIds.length === 0) return;
    if (!confirm(`確定刪除所選 ${selectedDeleteIds.length} 筆回測記錄？`)) return;
    
    try {
      const apiResult = await apiFetch<{ deleted: string[]; total_deleted: number }>('/api/v1/backtest/batch', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_ids: selectedDeleteIds }),
      });
      
      // 從 history 中移除已刪除的項目
      setHistory(prev => prev.filter(r => !selectedDeleteIds.includes(r.run_id)));
      
      // 如果當前結果在被刪除列表中，清除結果
      if (result?.run_id && selectedDeleteIds.includes(result.run_id)) {
        setResult(null);
      }
      
      // 清除選取狀態
      setSelectedDeleteIds([]);
      
      alert(`成功刪除 ${apiResult.total_deleted} 筆記錄`);
    } catch (err: unknown) {
      alert('批量刪除失敗：' + (err instanceof Error ? err.message : '未知錯誤'));
    }
  };

  // Sensitivity analysis state
  const [sensitivityParam, setSensitivityParam] = useState<'momentum' | 'value' | 'quality' | 'growth' | 'institutional' | 'top_n'>('momentum');
  const [sensitivityValues, setSensitivityValues] = useState('0,10,20,30,40,50');
  const [sensitivityRunning, setSensitivityRunning] = useState(false);
  const [sensitivityResults, setSensitivityResults] = useState<{ param_value: number; sharpe: number | null; cagr: number | null; total_return: number | null; max_drawdown: number | null }[]>([]);
  const [sensitivityMetric, setSensitivityMetric] = useState<'cagr'>('cagr');
  const [showSensitivity, setShowSensitivity] = useState(false);

  const runSensitivity = async () => {
    setSensitivityRunning(true);
    try {
      const vals = sensitivityValues.split(',').map(s => Number(s.trim())).filter(n => !isNaN(n));
      if (vals.length < 2) { addToast('請輸入至少 2 個數值（逗號分隔）', 'high'); return; }
      const data = await apiRunSensitivity({
        start_date: startDate,
        end_date: endDate || undefined,
        parameter: sensitivityParam === 'top_n' ? 'top_n' : sensitivityParam,
        values: vals,
      });
      setSensitivityResults(data);
      setShowSensitivity(true);
    } catch (e: unknown) {
      handleApiError(e, addToast, '敏感性分析失敗');
    } finally {
      setSensitivityRunning(false);
    }
  };

  // TBD: 切換刪除模式
  const toggleDeleteMode = () => {
    setDeleteMode(prev => !prev);
    setSelectedDeleteIds([]);  // 退出模式時清除選取
  };

  // TBD: 切換單筆選取
  const toggleDeleteSelection = (runId: string) => {
    setSelectedDeleteIds(prev => 
      prev.includes(runId) ? prev.filter(id => id !== runId) : [...prev, runId]
    );
  };

  // TBD: 全選/取消全選
  const toggleSelectAll = () => {
    if (selectedDeleteIds.length === history.length) {
      setSelectedDeleteIds([]);  // 取消全選
    } else {
      setSelectedDeleteIds(history.map(r => r.run_id));  // 全選
    }
  };

  // T052: 載入比較資料
  useEffect(() => {
    if (compareIds.length === 2) {
      fetchBacktestEquity(compareIds[0]).then(setCompareDataA).catch(() => setCompareDataA([]));
      fetchBacktestEquity(compareIds[1]).then(setCompareDataB).catch(() => setCompareDataB([]));
      // T052: 同步到 URL
      const newParams = new URLSearchParams(searchParams);
      newParams.set('compare', compareIds[1]);
      setSearchParams(newParams, { replace: true });
    } else {
      setCompareDataA([]);
      setCompareDataB([]);
      // T052: 清除 URL 參數
      const newParams = new URLSearchParams(searchParams);
      newParams.delete('compare');
      setSearchParams(newParams, { replace: true });
    }
  }, [compareIds]);

  return (
    <div className={styles.page}>
      <DesktopOnly>
      <h1 className={styles.title}>回測分析 Backtest</h1>
      <div className={styles.split}>
        {/* Left panel */}
        <div className={styles.leftPanel}>
          <div className={styles.section}>
            <h3>回測設定</h3>
            <div className={styles.field}>
              <label>開始日期</label>
              <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
            </div>
            <div className={styles.field}>
              <label>結束日期</label>
              <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
            </div>
            <div className={styles.field}>
              <label>選股數量</label>
              <input type="number" value={topN} min={1} max={100} onChange={(e) => setTopN(Number(e.target.value))} />
            </div>
            <div className={styles.field}>
              <label>初始資金</label>
              <input type="number" value={initialCapital} onChange={(e) => setInitialCapital(Number(e.target.value))} />
            </div>
            <div className={styles.field}>
              <label>基準 (Benchmark)</label>
              <input type="text" value={benchmark} onChange={(e) => setBenchmark(e.target.value.toUpperCase())} placeholder="例: 0050, 006208" />
            </div>
            <div className={styles.field}>
              <label>自訂名單 (Universe)</label>
              <input type="text" value={customUniverse} onChange={(e) => setCustomUniverse(e.target.value)} placeholder="逗點分隔，如: 2330, 2317" />
            </div>
          </div>

          <div className={styles.section}>
            <h3>策略權重</h3>
            {(['momentum', 'value', 'quality', 'growth', 'institutional'] as const).map((k) => (
              <div key={k} className={styles.sliderRow}>
                <label style={{ color: `var(--color-${k})` }}>
                  {k === 'momentum' ? '動能' : k === 'value' ? '價值' : k === 'quality' ? '品質' : k === 'growth' ? '成長' : '法人'}
                </label>
                <input type="range" min={0} max={100} step={5} value={weights[k]}
                  onChange={(e) => updateWeight(k, Number(e.target.value))} />
                <span className="font-data">{weights[k]}%</span>
              </div>
            ))}
            <div className={styles.totalWeight}>
              總計 <span className={`font-data ${totalWeight === 100 ? styles.ok : styles.warn}`}>{totalWeight}%</span>
              {totalWeight === 100 && ' ✓'}
            </div>
          </div>

          <button className={styles.runBtn} onClick={runBacktest} disabled={running}>
            {running ? '執行中...' : '▶ 執行回測'}
          </button>

          <div className={styles.section}>
            <div className={styles.sectionHeader}>
              <h3>歷史執行記錄</h3>
              <button 
                className={`${styles.modeToggleBtn} ${deleteMode ? styles.active : ''}`}
                onClick={toggleDeleteMode}
              >
                {deleteMode ? '退出刪除模式' : '刪除模式'}
              </button>
            </div>
            {history.length === 0 ? (
              <p className={styles.muted}>尚無回測紀錄</p>
            ) : (
              <div className={styles.historyList}>
                {/* 刪除模式：全選 + 批量刪除按鈕 */}
                {deleteMode && (
                  <div className={styles.deleteModeHeader}>
                    <label className={styles.selectAllLabel}>
                      <input
                        type="checkbox"
                        checked={selectedDeleteIds.length === history.length}
                        onChange={toggleSelectAll}
                        className={styles.selectAllCheckbox}
                      />
                      全選
                    </label>
                    <button
                      className={styles.batchDeleteBtn}
                      disabled={selectedDeleteIds.length === 0}
                      onClick={deleteSelectedRuns}
                    >
                      刪除所選 ({selectedDeleteIds.length})
                    </button>
                  </div>
                )}
                
                {history.map((r) => (
                  <div 
                    key={r.run_id} 
                    className={`${styles.historyItem} ${compareIds.includes(r.run_id) ? styles.selected : ''} ${selectedDeleteIds.includes(r.run_id) ? styles.markedForDelete : ''}`}
                    onClick={() => !deleteMode && toggleCompare(r.run_id)}
                  >
                    {/* 比較模式 checkbox */}
                    <input
                      type="checkbox"
                      checked={compareIds.includes(r.run_id)}
                      onChange={() => {}}
                      onClick={(e) => e.stopPropagation()}
                      className={styles.historyCheckbox}
                      disabled={deleteMode}  // 刪除模式下禁用比較選取
                    />
                    
                    {/* 刪除模式 checkbox */}
                    {deleteMode && (
                      <input
                        type="checkbox"
                        checked={selectedDeleteIds.includes(r.run_id)}
                        onChange={() => toggleDeleteSelection(r.run_id)}
                        onClick={(e) => e.stopPropagation()}
                        className={styles.deleteCheckbox}
                      />
                    )}
                    
                    <span className={styles.historyDate}>{r.start_date || ''}</span>
                    <span className={`font-data ${colorize(r.cagr, 'percent').className}`}>
                      {formatNumber(r.cagr, { type: 'percent' })}
                    </span>
                    <span className="font-data" style={{ color: 'var(--text-muted)' }}>
                      Sharpe {formatNumber(r.sharpe, { type: 'score' })}
                    </span>
                    
                    {/* 非刪除模式：顯示單筆刪除按鈕 */}
                    {!deleteMode && (
                      <button
                        className={styles.deleteBtn}
                        onClick={(e) => deleteRun(r.run_id, e)}
                        title="刪除此記錄"
                      >✕</button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right panel */}
        <div className={styles.rightPanel}>
          {/* T052: 比較模式 */}
          {isCompareMode ? (
            <CompareView 
              runA={history.find(r => r.run_id === compareIds[0])!}
              runB={history.find(r => r.run_id === compareIds[1])!}
              dataA={compareDataA}
              dataB={compareDataB}
              onExit={() => setCompareIds([])}
            />
          ) : !result ? (
            <div className={styles.placeholder}>
              <p>設定參數後點擊「執行回測」</p>
            </div>
          ) : (
            <>
              {/* Equity curve chart */}
              <div className={styles.chartCard}>
                <h3 id="equity-label">累積淨值 <span className={styles.legend}><span style={{color:'var(--color-bull)', borderBottom: '2px solid var(--color-bull)'}}>— 策略（實線）</span> <span style={{color:'var(--text-muted)', borderBottom: '1px dashed var(--text-muted)'}}>--- {result.benchmark || benchmark}（虛線）</span></span></h3>
                <div style={{ position: 'relative' }}>
                  {chartLoading ? <SkeletonLoader variant="chart" height={250} children={<></>} /> : (
                    equityData.length === 0 ? <div style={{ height: 250, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: 'var(--font-size-sm)' }}>尚無淨值資料</div> : <ErrorBoundary level="component" name="淨值曲線"><BacktestChart data={equityData} height={250} searchParams={searchParams} setSearchParams={setSearchParams} /></ErrorBoundary>
                  )}
                </div>
              </div>

              {/* Drawdown chart */}
              <div className={styles.chartCard}>
                <h3 id="dd-label">回撤 Drawdown</h3>
                {chartLoading ? <SkeletonLoader variant="chart" height={80} children={<></>} /> : (
                  equityData.length === 0 ? <div style={{ height: 80, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: 'var(--font-size-sm)' }}>尚無回撤資料</div> : <DrawdownChart data={equityData} height={80} />
                )}
              </div>

              {/* Metric grid */}
              <div className={styles.metricGrid}>
                {[
                  { label: '年化報酬率', value: result.cagr, fmt: 'pct' as const, bull: (result.cagr ?? 0) > 0 },
                  { label: 'Sharpe Ratio', value: result.sharpe, fmt: 'dec' as const },
                  { label: '最大回撤', value: result.max_drawdown, fmt: 'pct' as const, bear: true },
                  { label: 'Calmar Ratio', value: (result.cagr != null && result.max_drawdown) ? result.cagr / Math.abs(result.max_drawdown) : null, fmt: 'dec' as const },
                  { label: '總報酬率', value: result.total_return, fmt: 'pct' as const, bull: (result.total_return ?? 0) > 0 },
                  { label: '年化換手率', value: 3.12, fmt: 'pct' as const },
                ].map((m) => (
                  <MetricCell key={m.label} {...m} />
                ))}
              </div>
            </>
          )}

          {/* Sensitivity analysis section (Always visible) */}
          <div className={styles.section}>
            <h3>敏感性分析
              <button className={styles.sensitivityToggle} onClick={() => setShowSensitivity(!showSensitivity)}>
                {showSensitivity ? '收起' : '展開'}
              </button>
            </h3>
            {showSensitivity && (
              <div className={styles.sensitivityPanel}>
                <div className={styles.field} style={{ display: 'flex', gap: '1rem' }}>
                  <div style={{ flex: 1 }}>
                    <label>參數</label>
                    <select style={{ width: '100%' }} value={sensitivityParam} onChange={(e) => setSensitivityParam(e.target.value as typeof sensitivityParam)}>
                      <option value="momentum">動能權重</option>
                      <option value="value">價值權重</option>
                      <option value="quality">品質權重</option>
                      <option value="growth">成長權重</option>
                      <option value="institutional">法人權重</option>
                      <option value="top_n">選股數量</option>
                    </select>
                  </div>
                  <div style={{ flex: 1 }}>
                    <label>顯示指標</label>
                    <select style={{ width: '100%' }} value={sensitivityMetric} onChange={(e) => setSensitivityMetric(e.target.value as typeof sensitivityMetric)}>
                      <option value="cagr">年化報酬</option>
                      <option value="sharpe">Sharpe</option>
                      <option value="total_return">總報酬</option>
                      <option value="max_drawdown">最大回撤</option>
                    </select>
                  </div>
                </div>
                <div className={styles.field}>
                  <label>測試數值（逗號分隔）</label>
                  <input type="text" value={sensitivityValues} onChange={(e) => setSensitivityValues(e.target.value)} placeholder="例: 0,10,20,30,40,50" />
                </div>
                <button className={styles.runBtn} onClick={runSensitivity} disabled={sensitivityRunning} style={{ marginTop: 8, width: '100%' }}>
                  {sensitivityRunning ? '分析中...' : '▶ 執行分析'}
                </button>

                {sensitivityResults.length > 0 && (
                  <div className={styles.sensitivityChart}>
                    <ResponsiveContainer width="100%" height={200}>
                      <LineChart data={sensitivityResults}>
                        <XAxis dataKey="param_value" tick={{ fontSize: 11 }} />
                        <YAxis tick={{ fontSize: 11 }} />
                        <Tooltip />
                        <Legend />
                        <Line type="monotone" dataKey={sensitivityMetric} stroke="#38bdf8" strokeWidth={2} dot={{ r: 3 }} name={sensitivityMetric === 'cagr' ? '年化報酬' : sensitivityMetric === 'sharpe' ? 'Sharpe' : sensitivityMetric === 'total_return' ? '總報酬' : '最大回撤'} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </DesktopOnly>
    </div>
  );
}

function BacktestChart({ 
  data, 
  height, 
  onBrushSelect,
  searchParams,
  setSearchParams
}: { 
  data: EquityPoint[]; 
  height: number; 
  onBrushSelect?: (start: string, end: string) => void;
  searchParams: URLSearchParams;
  setSearchParams: (params: URLSearchParams) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const strategySeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const benchmarkSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  
  // T051: Brush 選取狀態
  const [brushSelection, setBrushSelection] = useState<{ start: number; end: number } | null>(null);
  const [brushPopover, setBrushPopover] = useState<{ start: string; end: string; startValue: number; endValue: number; return: number } | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState<number | null>(null);

  // T051: 重設縮放函式
  const resetZoom = useCallback(() => {
    if (chartRef.current) {
      chartRef.current.timeScale().fitContent();
    }
  }, []);

  // T051: 移動時間範圍
  const panTimeRange = useCallback((direction: 'left' | 'right', large: boolean = false) => {
    if (!chartRef.current) return;
    const range = chartRef.current.timeScale().getVisibleRange();
    if (!range) return;
    
    const fromMs = typeof range.from === 'string' 
      ? new Date(range.from + 'T00:00:00Z').getTime() 
      : (range.from as number) * 1000;
    const toMs = typeof range.to === 'string' 
      ? new Date(range.to + 'T00:00:00Z').getTime() 
      : (range.to as number) * 1000;
    const rangeMs = toMs - fromMs;
    const shiftMs = large ? rangeMs * 0.5 : rangeMs * 0.2;
    const directionFactor = direction === 'left' ? -1 : 1;
    
    const newFromMs = fromMs + shiftMs * directionFactor;
    const newToMs = toMs + shiftMs * directionFactor;
    
    const msToTime = (ms: number): Time => {
      const d = new Date(ms);
      return d.toISOString().slice(0, 10);
    };
    
    chartRef.current.timeScale().setVisibleRange({
      from: msToTime(newFromMs),
      to: msToTime(newToMs),
    });
  }, []);

  // T051: 鍵盤快捷鍵
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!chartRef.current) return;
      
      // R: 重設縮放
      if (e.key === 'r' || e.key === 'R') {
        e.preventDefault();
        resetZoom();
        return;
      }
      
      // 0: 重設縮放（回到初始狀態）
      if (e.key === '0') {
        e.preventDefault();
        resetZoom();
        return;
      }
      
      // Home: 回到最左側（最早日期）
      if (e.key === 'Home') {
        e.preventDefault();
        if (chartRef.current && data.length > 0) {
          const chart = chartRef.current;
          const firstDate = data[0].date; // 假設格式為 'YYYY-MM-DD'
          const secondDate = data[Math.min(29, data.length - 1)].date;
          chart.timeScale().setVisibleRange({
            from: firstDate as Time,
            to: secondDate as Time,
          });
        }
        return;
      }
      
      // End: 跳到最右側（最新日期）
      if (e.key === 'End') {
        e.preventDefault();
        if (chartRef.current && data.length > 0) {
          const chart = chartRef.current;
          const lastIndex = data.length - 1;
          const lastDate = data[lastIndex].date;
          const prevDate = data[Math.max(0, lastIndex - 29)].date;
          chart.timeScale().setVisibleRange({
            from: prevDate as Time,
            to: lastDate as Time,
          });
        }
        return;
      }
      
      // 左右方向鍵
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        panTimeRange('left', e.shiftKey);
        return;
      }
      
      if (e.key === 'ArrowRight') {
        e.preventDefault();
        panTimeRange('right', e.shiftKey);
        return;
      }
    };
    
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [resetZoom, panTimeRange]);

  useEffect(() => {
    if (!containerRef.current || data.length === 0) return;

    // 建立 chart
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: height,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#94a3b8',
      },
      grid: {
        vertLines: { color: '#1e293b' },
        horzLines: { color: '#1e293b' },
      },
      crosshair: {
        mode: 1, // Normal
        vertLine: {
          color: '#64748b',
          width: 1,
          style: 2, // Dashed
          labelBackgroundColor: '#1e293b',
        },
        horzLine: {
          color: '#64748b',
          width: 1,
          style: 2, // Dashed
          labelBackgroundColor: '#1e293b',
        },
      },
      rightPriceScale: {
        borderColor: '#334155',
      },
      timeScale: {
        borderColor: '#334155',
        timeVisible: false,
      },
    });

    chartRef.current = chart;

    // 策略線（實線）
    const strategySeries = chart.addSeries(LineSeries, {
      color: '#38bdf8',
      lineWidth: 2,
      lineStyle: 0, // Solid
      lastValueVisible: true,
      priceLineVisible: false,
    });
    strategySeriesRef.current = strategySeries;

    // 基準線（虛線）
    const benchmarkSeries = chart.addSeries(LineSeries, {
      color: '#64748b',
      lineWidth: 1,
      lineStyle: 2, // Dashed
      lastValueVisible: true,
      priceLineVisible: false,
    });
    benchmarkSeriesRef.current = benchmarkSeries;

    // 設定資料
    strategySeries.setData(data.map(p => ({ time: p.date, value: p.value })));
    benchmarkSeries.setData(data.filter(p => p.benchmark !== null).map(p => ({ time: p.date, value: p.benchmark! })));

    // 建立 tooltip
    const tooltip = document.createElement('div');
    tooltip.className = styles.chartTooltip;
    tooltip.style.display = 'none';
    containerRef.current.appendChild(tooltip);
    tooltipRef.current = tooltip;

    // Crosshair move 事件 - 自製 tooltip
    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !tooltipRef.current || !containerRef.current) {
        if (tooltipRef.current) tooltipRef.current.style.display = 'none';
        return;
      }

      // 取得 snap 的資料點
      const strategyData = param.seriesData.get(strategySeries);
      const benchmarkData = param.seriesData.get(benchmarkSeries);

      if (!strategyData) {
        tooltipRef.current.style.display = 'none';
        return;
      }

      const tv = strategyData as { time: string; value: number };
      const bv = benchmarkData as { time: string; value: number } | undefined;

      // 計算超額報酬
      const excessReturn = bv ? ((tv.value - bv.value) / bv.value) * 100 : null;

      // 更新 tooltip 內容
      tooltipRef.current.innerHTML = `
        <div class="${styles.tooltipDate}">${tv.time}</div>
        <div class="${styles.tooltipRow}">
          <span class="${styles.tooltipLabel}">策略</span>
          <span class="${styles.tooltipValue} font-data" style="color: var(--color-bull-text)">${formatNumber(tv.value, { type: 'price' })}</span>
        </div>
        <div class="${styles.tooltipRow}">
          <span class="${styles.tooltipLabel}">基準 (0050)</span>
          <span class="${styles.tooltipValue} font-data" style="color: var(--text-muted)">${bv ? formatNumber(bv.value, { type: 'price' }) : '-'}</span>
        </div>
        <div class="${styles.tooltipRow}">
          <span class="${styles.tooltipLabel}">超額報酬</span>
          <span class="${styles.tooltipValue} font-data" style="color: ${excessReturn && excessReturn > 0 ? 'var(--color-bull-text)' : 'var(--color-bear-text)'}">
            ${excessReturn !== null ? formatNumber(excessReturn / 100, { type: 'percent' }) : '-'}
          </span>
        </div>
      `;

      // 定位 tooltip(智慧定位:右側優先 → 左側)
      const containerRect = containerRef.current.getBoundingClientRect();
      const x = param.point?.x ?? 0;
      const y = param.point?.y ?? 0;
      const tooltipWidth = tooltipRef.current.offsetWidth;
      const tooltipHeight = tooltipRef.current.offsetHeight;

      let left = x + 20;
      let top = y - tooltipHeight / 2;

      // 右側空間不足 → 左側顯示
      if (left + tooltipWidth > containerRect.width) {
        left = x - tooltipWidth - 20;
      }

      // 超出上方 → 調整
      if (top < 0) top = 10;
      // 超出下方 → 調整
      if (top + tooltipHeight > containerRect.height) top = containerRect.height - tooltipHeight - 10;

      tooltipRef.current.style.left = `${left}px`;
      tooltipRef.current.style.top = `${top}px`;
      tooltipRef.current.style.display = 'block';
    });

    // Zoom: 監聽可視範圍變化，強制最小 22 交易日
    const MIN_DAYS = 22;
    
    // 將 Time 轉為毫秒數
    const timeToMs = (t: Time): number => {
      if (typeof t === 'string') {
        return new Date(t + 'T00:00:00Z').getTime();
      }
      return (t as number) * 1000; // UTC timestamp (seconds) -> milliseconds
    };
    
    // 將毫秒數轉回 Time (假設是 business day string)
    const msToTime = (ms: number): Time => {
      const d = new Date(ms);
      return d.toISOString().slice(0, 10); // 'YYYY-MM-DD'
    };
    
    const enforceMinRange = () => {
      const range = chart.timeScale().getVisibleRange();
      if (!range) return;
      
      const fromMs = timeToMs(range.from);
      const toMs = timeToMs(range.to);
      const daysDiff = Math.round((toMs - fromMs) / (1000 * 60 * 60 * 24));
      
      if (daysDiff < MIN_DAYS) {
        const newFromMs = toMs - (MIN_DAYS * 24 * 60 * 60 * 1000);
        chart.timeScale().setVisibleRange({
          from: msToTime(newFromMs),
          to: range.to,
        });
      }
    };
    
    chart.timeScale().subscribeVisibleTimeRangeChange(enforceMinRange);

    // Zoom: 滑鼠滾輪縮放（以滑鼠位置為中心）
    const handleWheel = (e: WheelEvent) => {
      e.preventDefault();
      
      const rect = containerRef.current!.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const range = chart.timeScale().getVisibleRange();
      if (!range) return;
      
      const fromMs = timeToMs(range.from);
      const toMs = timeToMs(range.to);
      const rangeMs = toMs - fromMs;
      const zoomFactor = e.deltaY > 0 ? 1.1 : 0.9;
      
      // 以滑鼠位置為中心計算新的範圍
      const mouseTimeMs = fromMs + (toMs - fromMs) * (mouseX / rect.width);
      const newRangeMs = rangeMs * zoomFactor;
      
      const newFromMs = mouseTimeMs - (mouseX / rect.width) * newRangeMs;
      const newToMs = mouseTimeMs + ((rect.width - mouseX) / rect.width) * newRangeMs;
      
      chart.timeScale().setVisibleRange({ from: msToTime(newFromMs), to: msToTime(newToMs) });
    };
    
    containerRef.current!.addEventListener('wheel', handleWheel, { passive: false });

    // 初始 fit
    chart.timeScale().fitContent();

    // T051: URL 狀態同步 - 讀取 URL 參數並套用
    const urlFrom = searchParams.get('from');
    const urlTo = searchParams.get('to');
    if (urlFrom && urlTo) {
      chart.timeScale().setVisibleRange({ from: urlFrom as Time, to: urlTo as Time });
    }

    // T051: URL 狀態同步 - 監聽縮放變化並更新 URL
    const syncToUrl = () => {
      const range = chart.timeScale().getVisibleRange();
      if (range) {
        const newParams = new URLSearchParams(searchParams);
        const fromStr = typeof range.from === 'string' ? range.from : new Date((range.from as number) * 1000).toISOString().slice(0, 10);
        const toStr = typeof range.to === 'string' ? range.to : new Date((range.to as number) * 1000).toISOString().slice(0, 10);
        newParams.set('from', fromStr);
        newParams.set('to', toStr);
        setSearchParams(newParams);
      }
    };

    chart.timeScale().subscribeVisibleTimeRangeChange(syncToUrl);

    const handleResize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    };

    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.timeScale().unsubscribeVisibleTimeRangeChange(enforceMinRange);
      chart.timeScale().unsubscribeVisibleTimeRangeChange(syncToUrl);
      if (containerRef.current) {
        containerRef.current.removeEventListener('wheel', handleWheel);
      }
      if (tooltipRef.current && containerRef.current) {
        containerRef.current.removeChild(tooltipRef.current);
      }
      chart.remove();
    };
  }, [data, height, searchParams, setSearchParams]);

  // T051: Brush 選取處理
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    setIsDragging(true);
    setDragStart(x);
    setBrushSelection(null);
    setBrushPopover(null);
  }, []);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!isDragging || !containerRef.current || dragStart === null) return;
    const rect = containerRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const start = Math.min(dragStart, x);
    const end = Math.max(dragStart, x);
    setBrushSelection({ start, end });
  }, [isDragging, dragStart]);

  const handleMouseUp = useCallback(() => {
    if (!isDragging || !brushSelection || !containerRef.current || !chartRef.current) {
      setIsDragging(false);
      setDragStart(null);
      return;
    }

    const range = chartRef.current.timeScale().getVisibleRange();
    if (!range) {
      setIsDragging(false);
      setDragStart(null);
      return;
    }

    const rect = containerRef.current.getBoundingClientRect();
    const fromMs = typeof range.from === 'string'
      ? new Date(range.from + 'T00:00:00Z').getTime()
      : (range.from as number) * 1000;
    const toMs = typeof range.to === 'string'
      ? new Date(range.to + 'T00:00:00Z').getTime()
      : (range.to as number) * 1000;

    // 計算選取的時間範圍
    const startTimeMs = fromMs + (brushSelection.start / rect.width) * (toMs - fromMs);
    const endTimeMs = fromMs + (brushSelection.end / rect.width) * (toMs - fromMs);

    const msToTime = (ms: number): string => {
      const d = new Date(ms);
      return d.toISOString().slice(0, 10);
    };

    const startDate = msToTime(startTimeMs);
    const endDate = msToTime(endTimeMs);

    // 找到對應的資料點
    const startPoint = data.find(p => p.date === startDate);
    const endPoint = data.find(p => p.date === endDate);

    if (startPoint && endPoint) {
      const returnVal = ((endPoint.value - startPoint.value) / startPoint.value) * 100;
      setBrushPopover({
        start: startDate,
        end: endDate,
        startValue: startPoint.value,
        endValue: endPoint.value,
        return: returnVal,
      });
    }

    setIsDragging(false);
    setDragStart(null);
  }, [isDragging, brushSelection, data]);

  return (
    <div 
      style={{ position: 'relative' }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
    >
      <div ref={containerRef} />
      {/* T051: 重設縮放按鈕 */}
      <button
        className={styles.resetZoomBtn}
        onClick={resetZoom}
        title="重設縮放 (R)"
        aria-label="重設縮放"
      >
        ⟲
      </button>
      {/* T051: Brush 選取框 */}
      {brushSelection && (
        <div
          className={styles.brushSelection}
          style={{
            left: brushSelection.start,
            width: brushSelection.end - brushSelection.start,
          }}
        />
      )}
      {/* T051: Brush Popover */}
      {brushPopover && (
        <div className={styles.brushPopover}>
          <div className={styles.brushPopoverHeader}>
            選取期間
            <button 
              className={styles.brushPopoverClose}
              onClick={() => setBrushPopover(null)}
            >✕</button>
          </div>
          <div className={styles.brushPopoverContent}>
            <div className={styles.brushPopoverRow}>
              <span>期間</span>
              <span>{brushPopover.start} ~ {brushPopover.end}</span>
            </div>
            <div className={styles.brushPopoverRow}>
              <span>期間報酬</span>
              <span style={{ color: brushPopover.return > 0 ? 'var(--color-bull-text)' : 'var(--color-bear-text)' }}>
                {formatNumber(brushPopover.return / 100, { type: 'percent' })}
              </span>
            </div>
            {onBrushSelect && (
              <button
                className={styles.brushApplyBtn}
                onClick={() => {
                  onBrushSelect(brushPopover.start, brushPopover.end);
                  setBrushPopover(null);
                  setBrushSelection(null);
                }}
              >
                設為回測期間
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function DrawdownChart({ data, height }: { data: EquityPoint[]; height: number }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || data.length === 0) return;

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: height,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#94a3b8',
      },
    });

    const ddSeries = chart.addSeries(AreaSeries, {
      lineColor: '#ef4444',
      topColor: 'rgba(239, 68, 68, 0.4)',
      bottomColor: 'rgba(239, 68, 68, 0.05)',
      lineWidth: 1,
    });

    ddSeries.setData(data.filter(p => p.drawdown !== null).map(p => ({ time: p.date, value: p.drawdown! * 100 })));
    chart.timeScale().fitContent();

    return () => { chart.remove(); };
  }, [data, height]);

  return <div ref={containerRef} />;
}

function MetricCell({ label, value, fmt, bull, bear, benchmark }: {
  label: string; value: number | null; fmt: 'pct' | 'dec'; bull?: boolean; bear?: boolean; benchmark?: boolean;
}) {
  const formatted = value == null ? '-' : fmt === 'pct' ? formatNumber(value, { type: 'percent' }) : formatNumber(value, { type: 'score' });
  let color = 'var(--text-primary)';
  if (bull) color = 'var(--color-bull-text)';
  else if (bear) color = 'var(--color-bear-text)';

  return (
    <div className={styles.metricCell}>
      <div className={styles.metricLabel}>{label}</div>
      <div className={`font-data ${styles.metricValue}`} style={{ color }}>
        {formatted}
        {benchmark && <span className={styles.pctIcon}>▮</span>}
      </div>
    </div>
  );
}

// T052: 比較模式組件
function CompareView({ 
  runA, 
  runB, 
  dataA, 
  dataB,
  onExit 
}: { 
  runA: BacktestRun; 
  runB: BacktestRun; 
  dataA: EquityPoint[]; 
  dataB: EquityPoint[];
  onExit: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  // 計算差異百分比
  const calcDiff = (a: number | null, b: number | null): { value: number; isPositive: boolean; isSmall: boolean } | null => {
    if (a === null || b === null) return null;
    const diff = ((b - a) / Math.abs(a)) * 100;
    return {
      value: diff,
      isPositive: diff > 0,
      isSmall: Math.abs(diff) < 5,
    };
  };

  const metrics = [
    { key: 'total_return', label: '總報酬' },
    { key: 'cagr', label: 'CAGR' },
    { key: 'sharpe', label: 'Sharpe' },
    { key: 'max_drawdown', label: '最大回撤' },
  ];

  // 建立疊加圖表
  useEffect(() => {
    if (!containerRef.current || dataA.length === 0 || dataB.length === 0) return;

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 300,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#94a3b8',
      },
      grid: {
        vertLines: { color: '#1e293b' },
        horzLines: { color: '#1e293b' },
      },
      rightPriceScale: { borderColor: '#334155' },
      timeScale: { borderColor: '#334155', timeVisible: false },
    });

    chartRef.current = chart;

    // A 策略線（藍色）
    const seriesA = chart.addSeries(LineSeries, {
      color: '#38bdf8',
      lineWidth: 2,
      lastValueVisible: true,
      priceLineVisible: false,
    });
    seriesA.setData(dataA.map(p => ({ time: p.date, value: p.value })));

    // B 策略線（綠色）
    const seriesB = chart.addSeries(LineSeries, {
      color: '#22c55e',
      lineWidth: 2,
      lastValueVisible: true,
      priceLineVisible: false,
    });
    seriesB.setData(dataB.map(p => ({ time: p.date, value: p.value })));

    chart.timeScale().fitContent();

    const handleResize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    window.addEventListener('resize', handleResize);
    
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, [dataA, dataB]);

  return (
    <div className={styles.compareContainer}>
      <div className={styles.compareHeader}>
        <h3>回測比較</h3>
        <button className={styles.exitCompareBtn} onClick={onExit}>✕ 退出比較</button>
      </div>
      
      {/* 並排標頭 */}
      <div className={styles.compareHeaders}>
        <div className={styles.compareHeaderA}>
          <div className={styles.compareLabel}>A 策略</div>
          <div className={styles.compareDate}>{runA.start_date}</div>
        </div>
        <div className={styles.compareHeaderB}>
          <div className={styles.compareLabel}>B 策略</div>
          <div className={styles.compareDate}>{runB.start_date}</div>
        </div>
      </div>

      {/* 差異指標格 */}
      <div className={styles.compareMetrics}>
        {metrics.map(({ key, label }) => {
          const valA = runA[key as keyof BacktestRun] as number | null;
          const valB = runB[key as keyof BacktestRun] as number | null;
          const diff = calcDiff(valA, valB);
          
          return (
            <div key={key} className={styles.compareRow}>
              <div className={styles.compareMetricLabel}>{label}</div>
              <div className={styles.compareValueA}>
                {valA !== null ? formatNumber(key === 'sharpe' ? valA : valA / 100, { type: key === 'sharpe' ? 'score' : 'percent' }) : '—'}
              </div>
              <div className={styles.compareValueB}>
                {valB !== null ? formatNumber(key === 'sharpe' ? valB : valB / 100, { type: key === 'sharpe' ? 'score' : 'percent' }) : '—'}
              </div>
              <div 
                className={styles.compareDiff}
                style={{ 
                  color: diff?.isSmall ? 'var(--text-muted)' : diff?.isPositive ? 'var(--color-bull-text)' : 'var(--color-bear-text)' 
                }}
                title={diff ? `差異 ${diff.value.toFixed(2)}%` : ''}
              >
                {diff ? (
                  diff.isSmall ? '─' : `${diff.isPositive ? '▲' : '▼'} ${Math.abs(diff.value).toFixed(1)}%`
                ) : '—'}
              </div>
            </div>
          );
        })}
      </div>

      {/* 疊加淨值曲線圖 */}
      <div className={styles.chartCard}>
        <h3>疊加淨值曲線</h3>
        <div className={styles.compareLegend}>
          <span style={{ color: '#38bdf8' }}>● A 策略</span>
          <span style={{ color: '#22c55e' }}>● B 策略</span>
        </div>
        <div ref={containerRef} style={{ minHeight: 300 }} />
      </div>
    </div>
  );
}
