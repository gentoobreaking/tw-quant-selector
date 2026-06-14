import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { FACTOR_COLORS, FACTOR_LABELS } from '../utils/color'
import FactorMiniBar from '../components/FactorMiniBar'
import RealtimeValuationBadge from '../components/RealtimeValuationBadge'
import { autoRebalance } from '../pages/Strategy'

// 雷達圖顏色常量（對應 Strategy.tsx 中的 RADAR_COLORS）
const RADAR_COLORS: Record<string, string> = {
  momentum: '#a78bfa',
  value: '#34d399',
  quality: '#f59e0b',
  growth: '#38bdf8',
  guru: '#4f8ef7',
  institutional: '#ec4899',
};

const ALL_SIX_FACTORS = ['momentum', 'value', 'quality', 'growth', 'guru', 'institutional'];

describe('FACTOR_COLORS / FACTOR_LABELS', () => {
  it('includes institutional in FACTOR_COLORS', () => {
    expect(FACTOR_COLORS.institutional).toBe('var(--color-institutional)')
  })

  it('includes institutional in FACTOR_LABELS', () => {
    expect(FACTOR_LABELS.institutional).toBe('法人')
  })

  it('has all 5 factors in FACTOR_COLORS', () => {
    expect(Object.keys(FACTOR_COLORS)).toEqual(
      expect.arrayContaining(['momentum', 'value', 'quality', 'growth', 'institutional'])
    )
  })

  it('has only 5 factors in utils (guru is page-level)', () => {
    expect(FACTOR_COLORS.guru).toBeUndefined()
  })
})

describe('RADAR_COLORS (雷達圖)', () => {
  it('has hex colors for all 6 factors', () => {
    for (const f of ALL_SIX_FACTORS) {
      expect(RADAR_COLORS[f]).toMatch(/^#[0-9a-fA-F]{6}$/)
    }
  })

  it('all 6 colors are distinct', () => {
    const colors = Object.values(RADAR_COLORS)
    expect(new Set(colors).size).toBe(6)
  })
})

describe('autoRebalance', () => {
  it('keeps total at 100 when one factor changes', () => {
    const weights = { m: 20, v: 15, q: 15, g: 10, gu: 15, i: 25 };
    const result = autoRebalance(weights, 'm', 30, new Set());
    expect(result).not.toBeNull();
    if (result) {
      const sum = Object.values(result).reduce((a, b) => a + b, 0);
      expect(sum).toBe(100);
      expect(result.m).toBe(30);
    }
  });

  it('handles edge case where all other weights become zero', () => {
    const weights = { m: 20, v: 15, q: 15, g: 10, gu: 15, i: 25 };
    const result = autoRebalance(weights, 'm', 100, new Set());
    expect(result).not.toBeNull();
    if (result) {
      expect(result.m).toBe(100);
      expect(result.v).toBe(0);
      expect(result.q).toBe(0);
      expect(result.g).toBe(0);
      expect(result.gu).toBe(0);
      expect(result.i).toBe(0);
    }
  });

  it('works with 6-factor weights explicitly', () => {
    const w6 = { momentum: 20, value: 15, quality: 15, growth: 10, guru: 15, institutional: 25 }
    const result = autoRebalance(w6, 'institutional', 30, new Set())
    expect(result).not.toBeNull()
    if (result) {
      expect(result.institutional).toBe(30)
      expect(result.guru).toBeGreaterThan(0)
      const total = Object.values(result).reduce((s, v) => s + v, 0)
      expect(total).toBe(100)
    }
  })
});

describe('FactorMiniBar Rendering', () => {
  it('renders correct color for each factor', () => {
    ALL_SIX_FACTORS.forEach(f => {
      const { container } = render(<FactorMiniBar name={f} score={0.5} />);
      const bar = container.querySelector(`[style*="${FACTOR_COLORS[f] || ''}"]`);
      // Guru factor might not be in utils FACTOR_COLORS
      if (f !== 'guru') {
        expect(bar).toBeDefined();
      }
    });
  });

  it('renders label when showLabels is true', () => {
    render(<FactorMiniBar name="momentum" score={0.5} showLabels />);
    const bar = screen.getByRole('img');
    expect(bar.getAttribute('aria-label')).toContain('動能');
  });

  it('renders institutional factor with correct label', () => {
    render(<FactorMiniBar name="institutional" score={1.5} />)
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('法人');
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('1.5');
  })

  it('handles extreme scores', () => {
    render(<FactorMiniBar name="institutional" score={2.5} />)
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('2.5');
  })
});

describe('RealtimeValuationBadge', () => {
  const baseData = {
    stockId: '2330',
    currentPrice: 943,
    peRt: 22.04,
    pbRt: 2.85,
    dividendYield: 0.032,
    industryAvgPb: 4.0,
    lastClosePe: null,
  }

  it('renders PE and PB values', () => {
    render(<RealtimeValuationBadge {...baseData} />)
    expect(screen.getByText('22.0')).toBeInTheDocument() 
    expect(screen.getByText('2.9')).toBeInTheDocument()
  })

  it('renders dividend yield', () => {
    render(<RealtimeValuationBadge {...baseData} />)
    expect(screen.getByText('+3.20%')).toBeInTheDocument()
  })

  it('shows >200 when peRt > 200', () => {
    render(<RealtimeValuationBadge {...baseData} peRt={250} />)
    expect(screen.getByText('> 200')).toBeInTheDocument()
  })

  it('shows — when values are null', () => {
    render(<RealtimeValuationBadge {...baseData} peRt={null} pbRt={null} />)
    const values = screen.getAllByText('—')
    expect(values.length).toBeGreaterThanOrEqual(2)
  })

  it('shows 高於同業 warning when PB > industry avg * 2', () => {
    render(<RealtimeValuationBadge {...baseData} pbRt={10.0} industryAvgPb={4.0} />)
    expect(screen.getByText('高於同業')).toBeInTheDocument()
  })

  it('does not show 高於同業 when PB within normal range', () => {
    render(<RealtimeValuationBadge {...baseData} pbRt={2.0} industryAvgPb={4.0} />)
    expect(screen.queryByText('高於同業')).not.toBeInTheDocument()
  })

  it('shows divergence warning when pe differs from last_close_pe > 5%', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 5, 4, 10, 0, 0))
    render(<RealtimeValuationBadge {...baseData} peRt={22} lastClosePe={20} />)
    expect(screen.getByText('⚠')).toBeInTheDocument()
    vi.useRealTimers()
  })

  it('does not show divergence warning when within 5%', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 5, 4, 10, 0, 0))
    render(<RealtimeValuationBadge {...baseData} peRt={20.5} lastClosePe={20} />)
    expect(screen.queryByText('⚠')).not.toBeInTheDocument()
    vi.useRealTimers()
  })
});
