import { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import styles from './Dropdown.module.css';

interface Option {
  value: string;
  label: string;
}

interface Props {
  options: Option[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  label?: string;
  className?: string;  // 加入 className 屬性
}

export default function Dropdown({ 
  options, 
  value, 
  onChange, 
  placeholder = '請選擇...',
  label,
  className  // 解構 className
}: Props) {
  const [isOpen, setIsOpen] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0, width: 0 });
  const [highlightedIndex, setHighlightedIndex] = useState(-1);
  
  const wrapperRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLUListElement>(null);
  
  // 找到當前選中的選項
  const selectedOption = options.find(opt => opt.value === value);
  
  // 計算選單位置（position: fixed）
  const updatePosition = () => {
    if (!wrapperRef.current) return;
    const rect = wrapperRef.current.getBoundingClientRect();
    setPosition({
      top: rect.bottom + window.scrollY,
      left: rect.left + window.scrollX,
      width: rect.width,
    });
  };
  
  // 開關選單
  const toggleMenu = () => {
    if (!isOpen) {
      updatePosition();
      setIsOpen(true);
      setHighlightedIndex(-1);
    } else {
      setIsOpen(false);
    }
  };
  
  // 選擇選項
  const handleSelect = (optionValue: string) => {
    onChange(optionValue);
    setIsOpen(false);
  };
  
  // 鍵盤導航
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!isOpen) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        toggleMenu();
      }
      return;
    }
    
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setHighlightedIndex(prev => 
          prev < options.length - 1 ? prev + 1 : 0
        );
        break;
      case 'ArrowUp':
        e.preventDefault();
        setHighlightedIndex(prev => 
          prev > 0 ? prev - 1 : options.length - 1
        );
        break;
      case 'Enter':
        e.preventDefault();
        if (highlightedIndex >= 0) {
          handleSelect(options[highlightedIndex].value);
        }
        break;
      case 'Escape':
        e.preventDefault();
        setIsOpen(false);
        break;
    }
  };
  
  // 點擊外部關閉選單
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        wrapperRef.current && 
        !wrapperRef.current.contains(e.target as Node) &&
        menuRef.current &&
        !menuRef.current.contains(e.target as Node)
      ) {
        setIsOpen(false);
      }
    };
    
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      window.addEventListener('scroll', updatePosition, true);
      window.addEventListener('resize', updatePosition);
    }
    
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      window.removeEventListener('scroll', updatePosition, true);
      window.removeEventListener('resize', updatePosition);
    };
  }, [isOpen]);
  
  // 高亮選項滾動到可視區域
  useEffect(() => {
    if (highlightedIndex >= 0 && menuRef.current) {
      const items = menuRef.current.querySelectorAll(`.${styles.option}`);
      if (items[highlightedIndex]) {
        items[highlightedIndex].scrollIntoView({ block: 'nearest' });
      }
    }
  }, [highlightedIndex]);
  
  // Portal 渲染選單
  const menuPortal = isOpen
    ? createPortal(
        <ul
          ref={menuRef}
          className={styles.menu}
          style={{
            position: 'fixed',
            top: `${position.top}px`,
            left: `${position.left}px`,
            width: `${position.width}px`,
            zIndex: 'calc(var(--z-dropdown) + 1000)',
          }}
          role="listbox"
        >
          {options.map((option, index) => (
            <li
              key={option.value}
              className={`${styles.option} ${option.value === value ? styles.selected : ''} ${index === highlightedIndex ? styles.highlighted : ''}`}
              onClick={() => handleSelect(option.value)}
              onMouseEnter={() => setHighlightedIndex(index)}
              role="option"
              aria-selected={option.value === value}
            >
              {option.label}
            </li>
          ))}
        </ul>,
        document.body
      )
    : null;
  
  return (
    <div className={`${styles.wrapper} ${className || ''}`} ref={wrapperRef}>
      {label && <label className={styles.label}>{label}</label>}
      <button
        className={styles.trigger}
        onClick={toggleMenu}
        onKeyDown={handleKeyDown}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        type="button"
      >
        <span className={styles.triggerText}>
          {selectedOption ? selectedOption.label : placeholder}
        </span>
        <span className={`${styles.arrow} ${isOpen ? styles.arrowUp : ''}`}>
          ▼
        </span>
      </button>
      {menuPortal}
    </div>
  );
}
