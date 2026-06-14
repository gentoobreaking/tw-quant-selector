import React, { useMemo } from 'react';
import { PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer, type PieLabelRenderProps } from 'recharts';
import styles from './PortfolioPieCharts.module.css';

interface Holding {
  stock_id: string;
  totalShares: number;
  avgCost: number;
  current_price: number | null;
  name?: string;
}

interface PortfolioPieChartsProps {
  holdings: Holding[];
  prices: Record<string, { name: string; close: number | null }>;
  onSliceClick?: (stockId: string) => void;
}

// 預設顏色調色盤
const COLORS = [
  '#0088FE', '#00C49F', '#FFBB28', '#FF8042', '#8884D8',
  '#82CA9D', '#FF6B6B', '#6A5ACD', '#20B2AA', '#FFA07A'
];

const PortfolioPieCharts: React.FC<PortfolioPieChartsProps> = ({ 
  holdings, 
  prices, 
  onSliceClick 
}) => {
  // 計算每個持倉的當前價格和市值
  const dataWithValue = useMemo(() => {
    return holdings.map(h => {
      const currentPrice = h.current_price ?? prices[h.stock_id]?.close ?? h.avgCost;
      const marketValue = currentPrice * h.totalShares;
      const name = prices[h.stock_id]?.name ?? h.stock_id;
      return {
        ...h,
        currentPrice,
        marketValue,
        name
      };
    }).filter(h => h.marketValue > 0);
  }, [holdings, prices]);

  // 計算總市值
  const totalValue = useMemo(() => {
    return dataWithValue.reduce((sum, h) => sum + h.marketValue, 0);
  }, [dataWithValue]);

  // 準備圓餅圖數據 - 權重分佈
  const weightData = useMemo(() => {
    return dataWithValue.map(h => ({
      name: h.name,
      value: totalValue > 0 ? (h.marketValue / totalValue) * 100 : 0,
      stock_id: h.stock_id
    }));
  }, [dataWithValue, totalValue]);

  // 準備圓餅圖數據 - 總金額分佈
  const valueData = useMemo(() => {
    return dataWithValue.map(h => ({
      name: h.name,
      value: h.marketValue,
      stock_id: h.stock_id
    }));
  }, [dataWithValue]);

  // 如果沒有持倉，顯示空狀態
  if (holdings.length === 0 || totalValue === 0) {
    return (
      <div className={styles.emptyState}>
        <p>暫無持倉數據，無法顯示圓餅圖</p>
      </div>
    );
  }

  // 自定義標籤渲染 - 權重分佈（顯示百分比）
  const renderWeightLabel = ({ cx = 0, cy = 0, midAngle = 0, innerRadius = 0, outerRadius = 0, percent = 0 }: PieLabelRenderProps) => {
    const RADIAN = Math.PI / 180;
    const radius = innerRadius + (outerRadius - innerRadius) * 0.5;
    const x = cx + radius * Math.cos(-midAngle * RADIAN);
    const y = cy + radius * Math.sin(-midAngle * RADIAN);
    
    if (percent < 0.05) return null; // 小於 5% 不顯示標籤
    
    return (
      <text x={x} y={y} fill="white" textAnchor="middle" dominantBaseline="central" fontSize={12}>
        {`${(percent * 100).toFixed(1)}%`}
      </text>
    );
  };

  // 自定義標籤渲染 - 總金額分佈（顯示 NT$ 金額）
  const renderValueLabel = ({ cx = 0, cy = 0, midAngle = 0, innerRadius = 0, outerRadius = 0, value = 0 }: PieLabelRenderProps) => {
    const RADIAN = Math.PI / 180;
    const radius = innerRadius + (outerRadius - innerRadius) * 0.5;
    const x = cx + radius * Math.cos(-midAngle * RADIAN);
    const y = cy + radius * Math.sin(-midAngle * RADIAN);
    
    // 金額小於 10000 不顯示標籤（避免擁擠）
    if (value < 10000) return null;
    
    // 格式化金額：NT$ 100,000
    const formattedValue = `NT$ ${Number(value).toLocaleString('zh-TW', { maximumFractionDigits: 0 })}`;
    
    return (
      <text x={x} y={y} fill="white" textAnchor="middle" dominantBaseline="central" fontSize={11}>
        {formattedValue}
      </text>
    );
  };

  // 處理點擊事件
  const handlePieClick = (_: unknown, index: number) => {
    if (onSliceClick && dataWithValue[index]) {
      onSliceClick(dataWithValue[index].stock_id);
    }
  };

  return (
    <div className={styles.container}>
      {/* 權重分佈圓餅圖 */}
      <div className={styles.chartWrapper}>
        <h3 className={styles.chartTitle}>持倉權重分佈</h3>
        <ResponsiveContainer width="100%" height={350}>
          <PieChart>
            <Pie
              data={weightData}
              cx="50%"
              cy="50%"
              labelLine={false}
              label={renderWeightLabel}
              outerRadius={120}
              fill="#8884d8"
              dataKey="value"
              onClick={handlePieClick}
            >
              {weightData.map((_, index) => (
                <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
              ))}
            </Pie>
            <Tooltip 
              formatter={(value: any, name: any) => [
                `NT$ ${Number(value).toLocaleString()}`, 
                name
              ]}
            />
            <Legend 
              formatter={(value) => (
                <span style={{ color: 'var(--text-primary)', fontSize: '12px' }}>
                  {value}
                </span>
              )}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>

      {/* 總金額分佈圓餅圖 */}
      <div className={styles.chartWrapper}>
        <h3 className={styles.chartTitle}>每股總金額分佈</h3>
        <ResponsiveContainer width="100%" height={350}>
          <PieChart>
            <Pie
              data={valueData}
              cx="50%"
              cy="50%"
              labelLine={false}
              label={renderValueLabel}
              outerRadius={120}
              fill="#8884d8"
              dataKey="value"
              onClick={handlePieClick}
            >
              {valueData.map((_, index) => (
                <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
              ))}
            </Pie>
            <Tooltip 
              formatter={(value: any, name: any) => [
                `NT$ ${Number(value).toLocaleString()}`, 
                name  // 顯示股票名稱
              ]}
            />
            <Legend 
              formatter={(value) => (
                <span style={{ color: 'var(--text-primary)', fontSize: '12px' }}>
                  {value}
                </span>
              )}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};

export default PortfolioPieCharts;
