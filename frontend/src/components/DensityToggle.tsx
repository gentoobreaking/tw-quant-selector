import { useState, useEffect, useCallback } from 'react';
import styles from './DensityToggle.module.css';

type DensityMode = 'comfortable' | 'compact' | 'dense';

interface DensityToggleProps {
  value?: DensityMode;
  onChange?: (mode: DensityMode) => void;
}

const STORAGE_KEY = 'tw-quant-density';

export function DensityToggle({ value, onChange }: DensityToggleProps) {
  const [mode, setMode] = useState<DensityMode>(() => {
    // 從 localStorage 讀取，或使用預設值
    const stored = localStorage.getItem(STORAGE_KEY) as DensityMode | null;
    return stored || 'comfortable';
  });

  // 同步外部控制
  useEffect(() => {
    if (value && value !== mode) {
      setMode(value);
    }
  }, [value]);

  // 更新 DOM 和 localStorage
  useEffect(() => {
    document.body.setAttribute('data-density', mode);
    localStorage.setItem(STORAGE_KEY, mode);
  }, [mode]);

  const cycleMode = useCallback(() => {
    const modes: DensityMode[] = ['comfortable', 'compact', 'dense'];
    const currentIndex = modes.indexOf(mode);
    const nextMode = modes[(currentIndex + 1) % modes.length];
    setMode(nextMode);
    onChange?.(nextMode);
  }, [mode, onChange]);

  const getIcon = () => {
    switch (mode) {
      case 'comfortable':
        return (
          <svg width="16" height="14" viewBox="0 0 16 14" fill="currentColor">
            <rect x="0" y="0" width="16" height="2" rx="1" />
            <rect x="0" y="6" width="16" height="2" rx="1" />
            <rect x="0" y="12" width="16" height="2" rx="1" />
          </svg>
        );
      case 'compact':
        return (
          <svg width="16" height="14" viewBox="0 0 16 14" fill="currentColor">
            <rect x="0" y="1" width="16" height="1.5" rx="0.75" />
            <rect x="0" y="4.5" width="16" height="1.5" rx="0.75" />
            <rect x="0" y="8" width="16" height="1.5" rx="0.75" />
            <rect x="0" y="11.5" width="16" height="1.5" rx="0.75" />
          </svg>
        );
      case 'dense':
        return (
          <svg width="16" height="14" viewBox="0 0 16 14" fill="currentColor">
            <rect x="0" y="0.5" width="16" height="1" rx="0.5" />
            <rect x="0" y="2.5" width="16" height="1" rx="0.5" />
            <rect x="0" y="4.5" width="16" height="1" rx="0.5" />
            <rect x="0" y="6.5" width="16" height="1" rx="0.5" />
            <rect x="0" y="8.5" width="16" height="1" rx="0.5" />
            <rect x="0" y="10.5" width="16" height="1" rx="0.5" />
            <rect x="0" y="12.5" width="16" height="1" rx="0.5" />
          </svg>
        );
    }
  };

  return (
    <button
      className={styles.toggle}
      onClick={cycleMode}
      title={`密度模式: ${mode} (點擊切換)`}
      aria-label={`資料密度: ${mode}`}
    >
      {getIcon()}
      <span className={styles.label}>{mode === 'comfortable' ? '舒適' : mode === 'compact' ? '緊湊' : '密集'}</span>
    </button>
  );
}
