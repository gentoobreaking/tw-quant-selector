import { describe, it, expect } from 'vitest';
import { calc_industry_avg_pb } from '../utils/industryAverages';
import type { LatestValuation } from '../api/client';

describe('calc_industry_avg_pb', () => {
  const mockStocks: LatestValuation[] = [
    { stock_id: '2330', pb: 8.5, industry: '半導體業' },
    { stock_id: '2303', pb: 1.2, industry: '半導體業' },
    { stock_id: '2454', pb: 4.0, industry: '半導體業' },
    { stock_id: '2317', pb: 1.5, industry: '其他電子業' },
    { stock_id: '2357', pb: 2.5, industry: '其他電子業' },
    { stock_id: '0050', pb: null, industry: null },
  ];

  it('should calculate median PB for the target industry', () => {
    // 半導體業: [1.2, 4.0, 8.5] -> median is 4.0
    const avg = calc_industry_avg_pb('2330', mockStocks);
    expect(avg).toBe(4.0);
  });

  it('should handle even number of stocks in industry', () => {
    // 其他電子業: [1.5, 2.5] -> median is (1.5 + 2.5) / 2 = 2.0
    const avg = calc_industry_avg_pb('2317', mockStocks);
    expect(avg).toBe(2.0);
  });

  it('should return null if stock_id not found', () => {
    const avg = calc_industry_avg_pb('9999', mockStocks);
    expect(avg).toBeNull();
  });

  it('should return null if stock has no industry', () => {
    const avg = calc_industry_avg_pb('0050', mockStocks);
    expect(avg).toBeNull();
  });

  it('should exclude null or non-positive PB values', () => {
    const stocksWithInvalid: LatestValuation[] = [
      { stock_id: '1', pb: 1.0, industry: 'A' },
      { stock_id: '2', pb: null, industry: 'A' },
      { stock_id: '3', pb: 0, industry: 'A' },
      { stock_id: '4', pb: -1, industry: 'A' },
      { stock_id: '5', pb: 2.0, industry: 'A' },
    ];
    // Industry A: [1.0, 2.0] -> median 1.5
    const avg = calc_industry_avg_pb('1', stocksWithInvalid);
    expect(avg).toBe(1.5);
  });
});
