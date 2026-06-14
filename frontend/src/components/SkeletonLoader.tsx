import styles from './SkeletonLoader.module.css';

interface Props {
  variant?: 'text' | 'card' | 'table' | 'chart' | 'circle';
  rows?: number;
  width?: string | number;
  height?: string | number;
  children?: React.ReactNode;
}

export default function SkeletonLoader({ variant = 'text', rows = 4, width, height }: Props) {
  const style = width ? { width: typeof width === 'number' ? `${width}px` : width } : undefined;
  const hStyle = height ? { height: typeof height === 'number' ? `${height}px` : height } : undefined;

  if (variant === 'card') {
    return (
      <div className={styles.card} style={{ ...style, ...hStyle }}>
        <div className={styles.titleBar} />
        <div className={styles.bodyBar} />
        <div className={styles.bodyBar} style={{ width: '60%' }} />
      </div>
    );
  }

  if (variant === 'table') {
    return (
      <div className={`${styles.table} ${styles.stagger}`} style={style}>
        <div className={styles.tableHeader} />
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className={styles.tableRow}>
            <div className={styles.cell} style={{ width: '15%' }} />
            <div className={styles.cell} style={{ width: '25%' }} />
            <div className={styles.cell} style={{ width: '12%' }} />
            <div className={styles.cell} style={{ width: '12%' }} />
            <div className={styles.cell} style={{ width: '12%' }} />
            <div className={styles.cell} style={{ width: '12%' }} />
          </div>
        ))}
      </div>
    );
  }

  if (variant === 'chart') {
    return (
      <div className={styles.chart} style={{ ...style, ...hStyle }}>
        <div className={styles.chartLine} />
        <div className={styles.chartAxis} />
      </div>
    );
  }

  if (variant === 'circle') {
    return (
      <div className={styles.circle} style={{ width: width || '32px', height: height || width || '32px', ...style }} />
    );
  }

  return (
    <div className={`${styles.textBlock} ${styles.stagger}`} style={style}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className={styles.textLine} style={{ width: `${70 + (i * 10) % 30}%` }} />
      ))}
    </div>
  );
}
