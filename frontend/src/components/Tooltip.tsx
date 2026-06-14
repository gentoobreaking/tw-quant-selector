import { useState, useRef, useEffect, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import styles from './Tooltip.module.css';

// 全域 Tooltip 計數器（用於 z-index 堆疊管理）
let tooltipCounter = 0;

interface Props {
  content: string;
  children: ReactNode;
}

export default function Tooltip({ content, children }: Props) {
  const [visible, setVisible] = useState(false);
  const [flip, setFlip] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wrapperRef = useRef<HTMLSpanElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const zIndexRef = useRef(0);

  // 計算 tooltip 位置（position: fixed）
  const updatePosition = () => {
    if (!wrapperRef.current) return;
    const rect = wrapperRef.current.getBoundingClientRect();
    setPosition({
      top: rect.top + window.scrollY,
      left: rect.left + window.scrollX + rect.width / 2,
    });
  };

  useEffect(() => {
    if (!visible) return;
    
    // 遞增全域計數器，取得唯一 z-index
    tooltipCounter += 1;
    zIndexRef.current = tooltipCounter;
    
    updatePosition();
    
    // 檢查是否需要翻轉（空間不足時）
    setTimeout(() => {
      if (!tooltipRef.current) return;
      const rect = tooltipRef.current.getBoundingClientRect();
      if (rect.top < 4) setFlip(true);
      else setFlip(false);
    }, 10);
    
    // 監聽滾動和視窗大小變化
    window.addEventListener('scroll', updatePosition, true);
    window.addEventListener('resize', updatePosition);
    
    return () => {
      window.removeEventListener('scroll', updatePosition, true);
      window.removeEventListener('resize', updatePosition);
    };
  }, [visible]);

  const show = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setVisible(true), 300);
  };

  const hide = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setVisible(false), 150);
  };

  // Portal 渲染 Tooltip 到 document.body
  const tooltipPortal = visible
    ? createPortal(
        <div
          ref={tooltipRef}
          className={`${styles.tooltip} ${flip ? styles.flip : ''}`}
          role="tooltip"
          style={{
            position: 'fixed',
            top: flip ? 'auto' : `${position.top}px`,
            bottom: flip ? `${window.innerHeight - position.top}px` : 'auto',
            left: `${position.left}px`,
            transform: 'translateX(-50%)',
            zIndex: `calc(var(--z-tooltip) + ${zIndexRef.current})`,
          }}
        >
          {content}
        </div>,
        document.body
      )
    : null;

  return (
    <span
      ref={wrapperRef}
      className={styles.wrapper}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      {children}
      {tooltipPortal}
    </span>
  );
}
