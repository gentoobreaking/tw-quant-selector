import type { LatestValuation } from '../api/client';

/**
 * 計算指定股票所屬產業的平均 PB (中位數，排除極端值)
 * 
 * @param stockId 目標股票代號
 * @param allStocks 所有股票的最新估值資料
 * @returns 產業平均 PB 或 null
 */
export function calc_industry_avg_pb(
  stockId: string,
  allStocks: LatestValuation[]
): number | null {
  if (!allStocks || allStocks.length === 0) return null;

  const targetStock = allStocks.find(s => s.stock_id === stockId);
  if (!targetStock || !targetStock.industry) return null;

  const targetIndustry = targetStock.industry;
  
  // 篩選同產業且 PB 有效的股票
  const industryPbs = allStocks
    .filter(s => s.industry === targetIndustry && s.pb !== null && s.pb > 0)
    .map(s => s.pb as number);

  if (industryPbs.length === 0) return null;

  // 排序以計算中位數 (排除極端值影響)
  industryPbs.sort((a, b) => a - b);
  
  const mid = Math.floor(industryPbs.length / 2);
  if (industryPbs.length % 2 === 0) {
    return (industryPbs[mid - 1] + industryPbs[mid]) / 2;
  } else {
    return industryPbs[mid];
  }
}
