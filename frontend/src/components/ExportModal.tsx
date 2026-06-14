import { useState } from 'react';
import Dropdown from './Dropdown';
import styles from './ExportModal.module.css';

interface ColumnOption {
  key: string;
  label: string;
  visible: boolean;
}

interface Props {
  defaultColumns: ColumnOption[];
  onExport: (format: 'csv' | 'json', columns: string[]) => void;
  onClose: () => void;
}

export default function ExportModal({ defaultColumns, onExport, onClose }: Props) {
  const [format, setFormat] = useState<'csv' | 'json'>('csv');
  const [selected, setSelected] = useState<Set<string>>(() => new Set(defaultColumns.filter((c) => c.visible).map((c) => c.key)));

  const toggle = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const handleExport = () => {
    if (selected.size === 0) return;
    onExport(format, Array.from(selected));
  };

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <h3>匯出設定 Export</h3>

        <div className={styles.field}>
          <label>格式</label>
          <Dropdown
            options={[
              { value: 'csv', label: 'CSV' },
              { value: 'json', label: 'JSON' },
            ]}
            value={format}
            onChange={(value) => setFormat(value as 'csv' | 'json')}
          />
        </div>

        <div className={styles.field}>
          <label>欄位</label>
          <div className={styles.columnList}>
            {defaultColumns.map((col) => (
              <label key={col.key} className={styles.columnItem}>
                <input type="checkbox" checked={selected.has(col.key)} onChange={() => toggle(col.key)} />
                {col.label}
              </label>
            ))}
          </div>
        </div>

        <div className={styles.actions}>
          <button className={styles.cancelBtn} onClick={onClose}>取消</button>
          <button className={styles.exportBtn} onClick={handleExport} disabled={selected.size === 0}>
            匯出
          </button>
        </div>
      </div>
    </div>
  );
}
