import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import RealtimeValuationBadge from '../components/RealtimeValuationBadge';
import '@testing-library/jest-dom';

describe('RealtimeValuationBadge', () => {
  const defaultProps = {
    stockId: '2330',
    currentPrice: 600,
    peRt: 22.1,
    pbRt: 8.3,
    industryAvgPb: 4.0,
  };

  it('renders correctly with default props', () => {
    render(<RealtimeValuationBadge {...defaultProps} />);
    expect(screen.getByText('RT-PE:')).toBeInTheDocument();
    expect(screen.getByText('22.1')).toBeInTheDocument();
    expect(screen.getByText('RT-PB:')).toBeInTheDocument();
    expect(screen.getByText('8.3')).toBeInTheDocument();
  });

  it('shows green color when PB < 1.0', () => {
    render(<RealtimeValuationBadge {...defaultProps} pbRt={0.8} />);
    const pbValue = screen.getByText('0.8');
    expect(pbValue).toHaveStyle('color: var(--color-positive)');
  });

  it('shows yellow-orange color and warning when PB > industryAvgPb * 2', () => {
    render(<RealtimeValuationBadge {...defaultProps} pbRt={10.0} industryAvgPb={4.0} />);
    const pbValue = screen.getByText('10.0');
    expect(pbValue).toHaveStyle('color: var(--color-neutral)');
    expect(screen.getByText('高於同業')).toBeInTheDocument();
  });

  it('shows "> 200" when PE > 200', () => {
    render(<RealtimeValuationBadge {...defaultProps} peRt={250} />);
    expect(screen.getByText('> 200')).toBeInTheDocument();
  });

  it('shows "—" when values are null', () => {
    render(<RealtimeValuationBadge {...defaultProps} peRt={null} pbRt={null} />);
    const values = screen.getAllByText('—');
    expect(values.length).toBeGreaterThanOrEqual(2);
  });
});
