import { useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';

interface ShortcutConfig {
  key: string;
  ctrl?: boolean;
  meta?: boolean;
  shift?: boolean;
  alt?: boolean;
  handler: (e: KeyboardEvent) => void;
  description: string;
  page?: string; // 如果指定，只在特定頁面觸發
}

/**
 * 全域鍵盤快捷鍵管理器
 * 參考 spec §12.1–§12.5
 */
export function useKeyboardShortcuts(shortcuts: ShortcutConfig[]) {
  const shortcutsRef = useRef(shortcuts);
  shortcutsRef.current = shortcuts;

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    // 忽略輸入框、下拉選單等可輸入元素
    const target = e.target as HTMLElement;
    if (
      target.tagName === 'INPUT' ||
      target.tagName === 'TEXTAREA' ||
      target.tagName === 'SELECT' ||
      target.isContentEditable
    ) {
      return;
    }

    const currentShortcuts = shortcutsRef.current;

    for (const shortcut of currentShortcuts) {
      const keyMatch = shortcut.key.toLowerCase() === e.key.toLowerCase();
      const ctrlMatch = (shortcut.ctrl ?? false) === e.ctrlKey;
      const metaMatch = (shortcut.meta ?? false) === e.metaKey;
      const shiftMatch = (shortcut.shift ?? false) === e.shiftKey;
      const altMatch = (shortcut.alt ?? false) === e.altKey;

      if (keyMatch && ctrlMatch && metaMatch && shiftMatch && altMatch) {
        e.preventDefault();
        shortcut.handler(e);
        return;
      }
    }
  }, []);

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);
}

/**
 * 全域預設快捷鍵
 * 參考 spec §12.1
 */
export function useGlobalShortcuts() {
  const navigate = useNavigate();

  const shortcuts: ShortcutConfig[] = [
    // Cmd+K: 聚焦搜尋框
    {
      key: 'k',
      meta: true,
      handler: () => {
        const searchInput = document.querySelector('input[type="search"]') as HTMLInputElement;
        if (searchInput) {
          searchInput.focus();
        }
      },
      description: '聚焦搜尋框',
    },
    // Cmd+/: 顯示快捷鍵說明 Modal
    {
      key: '/',
      meta: true,
      handler: () => {
        // 發送自訂事件，由 App 層監聽
        window.dispatchEvent(new CustomEvent('shortcut:show-help'));
      },
      description: '顯示快捷鍵說明',
    },
    // G → D: 導航到 Dashboard
    {
      key: 'g',
      handler: () => {
        // 等待下一個按鍵
        const handleNextKey = (e: KeyboardEvent) => {
          if (e.key === 'd' || e.key === 'D') {
            navigate('/');
          } else if (e.key === 's' || e.key === 'S') {
            navigate('/signals');
          } else if (e.key === 'b' || e.key === 'B') {
            navigate('/backtest');
          } else if (e.key === 't' || e.key === 'T') {
            navigate('/strategy');
          } else if (e.key === 'm' || e.key === 'M') {
            navigate('/monitor');
          } else if (e.key === 'p' || e.key === 'P') {
            navigate('/portfolio');
          }
          window.removeEventListener('keydown', handleNextKey);
        };
        window.addEventListener('keydown', handleNextKey);
        // 2 秒後自動移除監聽器
        setTimeout(() => window.removeEventListener('keydown', handleNextKey), 2000);
      },
      description: '導航前置鍵（G→D/S/B/T/M/P）',
    },
  ];

  useKeyboardShortcuts(shortcuts);
}
