import { useState, useEffect, type ReactNode } from 'react';
import SkeletonLoader from './SkeletonLoader';

interface Props {
  loading: boolean;
  variant?: 'text' | 'card' | 'table' | 'chart' | 'circle';
  rows?: number;
  width?: string | number;
  height?: string | number;
  children: ReactNode;
}

export default function SkeletonScreen({ loading, variant, rows, width, height, children }: Props) {
  const [renderSkeleton, setRenderSkeleton] = useState(true);
  const [fadeOut, setFadeOut] = useState(false);

  useEffect(() => {
    if (!loading) {
      setFadeOut(true);
      const id = setTimeout(() => setRenderSkeleton(false), 400);
      return () => clearTimeout(id);
    } else {
      setRenderSkeleton(true);
      setFadeOut(false);
    }
  }, [loading]);

  if (!renderSkeleton) return <>{children}</>;

  return (
    <div style={{ position: 'relative' }}>
      <div
        style={{
          opacity: fadeOut ? 0 : 1,
          transition: 'opacity 0.4s ease-out',
        }}
      >
        <SkeletonLoader variant={variant || 'card'} rows={rows} width={width} height={height} />
      </div>
      <div
        style={{
          position: 'absolute',
          inset: 0,
          opacity: fadeOut ? 1 : 0,
          transition: 'opacity 0.4s ease-out',
        }}
      >
        {children}
      </div>
    </div>
  );
}
