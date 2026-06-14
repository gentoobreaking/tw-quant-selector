import { useEffect } from 'react';
import styles from './ShortcutHelp.module.css';

interface ShortcutItem {
  keys: string[];
  description: string;
  page?: string; // 如果指定，只顯示在特定頁面
}

interface Props {
  isOpen: boolean;
  onClose: () => void;
  globalShortcuts: ShortcutItem[];
  pageShortcuts: ShortcutItem[];
}

export default function ShortcutHelp({ isOpen, onClose, globalShortcuts, pageShortcuts }: Props) {
  // ESC 關閉 Modal
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <h2>⌨️ 快捷鍵說明</h2>
          <button className={styles.closeBtn} onClick={onClose}>✕</button>
        </div>

        <div className={styles.content}>
          {/* 左欄：全域快捷鍵 */}
          <div className={styles.column}>
            <h3>🌐 全域快捷鍵</h3>
            <div className={styles.shortcutList}>
              {globalShortcuts.map((shortcut, index) => (
                <div key={index} className={styles.shortcutItem}>
                  <div className={styles.keyPills}>
                    {shortcut.keys.map((key, i) => (
                      <span key={i} className={styles.keyPill}>{key}</span>
                    ))}
                  </div>
                  <span className={styles.description}>{shortcut.description}</span>
                </div>
              ))}
            </div>
          </div>

          {/* 右欄：頁面情境快捷鍵 */}
          <div className={styles.column}>
            <h3>📄 頁面情境快捷鍵</h3>
            <div className={styles.shortcutList}>
              {pageShortcuts.map((shortcut, index) => (
                <div key={index} className={styles.shortcutItem}>
                  <div className={styles.keyPills}>
                    {shortcut.keys.map((key, i) => (
                      <span key={i} className={styles.keyPill}>{key}</span>
                    ))}
                  </div>
                  <span className={styles.description}>{shortcut.description}</span>
                  {shortcut.page && (
                    <span className={styles.pageTag}>{shortcut.page}</span>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className={styles.footer}>
          <p>💡 提示：快捷鍵說明會在首次進入時顯示（3 秒後自動消失）</p>
          <button className={styles.dismissBtn} onClick={() => {
            localStorage.setItem('shortcut-help-dismissed', 'true');
            onClose();
          }}>
            不再顯示
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * 快捷鍵資料（全域）
 * 參考 spec §12.1
 */
export const GLOBAL_SHORTCUTS: ShortcutItem[] = [
  { keys: ['⌘', 'K'], description: '聚焦搜尋框' },
  { keys: ['⌘', '/'], description: '顯示快捷鍵說明' },
  { keys: ['G', 'D'], description: '導航到 Dashboard' },
  { keys: ['G', 'S'], description: '導航到 Signals' },
  { keys: ['G', 'B'], description: '導航到 Backtest' },
  { keys: ['G', 'T'], description: '導航到 Strategy' },
  { keys: ['G', 'M'], description: '導航到 Monitor' },
  { keys: ['G', 'P'], description: '導航到 Portfolio' },
];

/**
 * 快捷鍵資料（表格頁面）
 * 參考 spec §12.2
 */
export const TABLE_SHORTCUTS: ShortcutItem[] = [
  { keys: ['↑'], description: '上一列' },
  { keys: ['↓'], description: '下一列' },
  { keys: ['Enter'], description: '展開/收合' },
  { keys: ['Space'], description: '勾選' },
  { keys: ['Home'], description: '回到第一列' },
  { keys: ['End'], description: '跳到最後一列' },
  { keys: ['Shift', '↑'], description: '多選：上一列' },
  { keys: ['Shift', '↓'], description: '多選：下一列' },
  { keys: ['Escape'], description: '取消選取' },
];

/**
 * 快捷鍵資料（圖表頁面）
 * 參考 spec §12.3
 */
export const CHART_SHORTCUTS: ShortcutItem[] = [
  { keys: ['R'], description: '重設縮放' },
  { keys: ['←'], description: '平移：向左' },
  { keys: ['→'], description: '平移：向右' },
  { keys: ['Shift', '←'], description: '快速平移：向左' },
  { keys: ['Shift', '→'], description: '快速平移：向右' },
  { keys: ['0'], description: '重設到初始狀態' },
  { keys: ['Home'], description: '回到最左側（最早日期）' },
  { keys: ['End'], description: '跳到最右側（最新日期）' },
];
