/* eslint-disable react-hooks/set-state-in-effect */
import { useState, useEffect, useCallback, type ReactNode } from 'react';
import { DesktopOnly, MobileMessage } from '../utils/responsive';
import SkeletonScreen from '../components/SkeletonScreen';
import Tooltip from '../components/Tooltip';
import AlertRulesPanel from '../components/AlertRulesPanel';
import { useToast } from '../components/Toast';
import styles from './Settings.module.css';

const API = '';
async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, init);
  if (!res.ok) throw new Error(`API ${res.status}`);
  const body = await res.json();
  if (body.error) throw new Error(body.error.message || 'API error');
  return (body.data ?? body) as T;
}

interface SettingItem {
  key: string;
  value: string | null;
  is_env_set: boolean;
  is_sensitive: boolean;
  has_value: boolean;  // T100: whether the sensitive field actually has a real value
}

export default function Settings() {
  const [activeTab, setActiveTab] = useState<'alerts' | 'rules'>('alerts');
  const [settings, setSettings] = useState<SettingItem[]>([]);
  const [dbPath, setDbPath] = useState<{
    backend?: string;
    host?: string;
    port?: number;
    database?: string;
    user?: string;
    is_env_set: boolean;
  }>({ is_env_set: false });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const { addToast } = useToast();

  const loadSettings = useCallback(async () => {
    if (activeTab !== 'alerts') return;
    setLoading(true);
    try {
      const [alertData, pathData] = await Promise.all([
        apiFetch<SettingItem[]>('/api/v1/settings/alerts'),
        apiFetch<{ path: string; is_env_set: boolean }>('/api/v1/settings/db-path'),
      ]);
      setSettings(alertData);
      setDbPath(pathData);
    } catch (e: unknown) {
      addToast(`載入失敗: ${e instanceof Error ? e.message : String(e)}`, 'high');
    } finally {
      setLoading(false);
    }
  }, [activeTab, addToast]);

  useEffect(() => { loadSettings(); }, [loadSettings]);

  const handleUpdate = (key: string, value: string) => {
    setSettings(prev => prev.map(s => s.key === key ? { ...s, value } : s));
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const toSave: Record<string, string> = {};
      settings.forEach(s => {
        // T100: skip sensitive fields that haven't been edited (value === "***")
        if (s.is_env_set || s.value === null) return;
        if (s.is_sensitive && s.value === '***') return;
        toSave[s.key] = s.value;
      });
      
      const promises = [
        apiFetch('/api/v1/settings/alerts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(toSave),
        })
      ];

      await Promise.all(promises);
      addToast('設定已儲存', 'low');
    } catch (e: unknown) {
      addToast(`儲存失敗: ${e instanceof Error ? e.message : String(e)}`, 'high');
    } finally {
      setSaving(false);
    }
  };

  const handleTestAlert = async () => {
    setTesting(true);
    try {
      await apiFetch('/api/v1/settings/test-alert', { method: 'POST' });
      addToast('測試告警已發送，請檢查您的 Telegram 或 Email', 'low');
    } catch (e: unknown) {
      addToast(`發送失敗: ${e instanceof Error ? e.message : String(e)}`, 'high');
    } finally {
      setTesting(false);
    }
  };

  const getS = (key: string) => settings.find(s => s.key === key);

  return (
    <div className={styles.page}>
      <DesktopOnly>
        <div className={styles.tabs}>
          <button
            className={`${styles.tab} ${activeTab === 'alerts' ? styles.tabActive : ''}`}
            onClick={() => setActiveTab('alerts')}
          >
            告警設定
          </button>
          <button
            className={`${styles.tab} ${activeTab === 'rules' ? styles.tabActive : ''}`}
            onClick={() => setActiveTab('rules')}
          >
            警示規則
          </button>
        </div>

        {activeTab === 'alerts' ? (
          <SkeletonScreen loading={loading} variant="card" rows={4} width="100%" height={600}>
            <div className={styles.header}>
              <h1 className={styles.title}>系統設定 Settings</h1>
              <div className={styles.actions}>
                <button className={`${styles.testBtn}${testing ? ' btn-loading' : ''}`} onClick={handleTestAlert} disabled={testing}>
                  {testing ? '發送中...' : '🔔 測試告警'}
                </button>
                <button className={`${styles.saveBtn}${saving ? ' btn-loading' : ''}`} onClick={handleSave} disabled={saving}>
                  {saving ? '儲存中...' : '💾 儲存設定'}
                </button>
              </div>
            </div>

            <div className={styles.grid}>
              {/* Database Section */}
              <div className={styles.card}>
                <h3>資料庫管理 (PostgreSQL)</h3>
                <p className={styles.hint}>PostgreSQL 連線由環境變數設定，運行期間不可變更</p>
                <div className={styles.dbInfoGrid}>
                  <div className={styles.dbInfoRow}>
                    <span className={styles.dbInfoLabel}>主機</span>
                    <span className={dbPath.is_env_set ? styles.dbInfoValueLocked : styles.dbInfoValue}>{dbPath.host ?? '—'}</span>
                  </div>
                  <div className={styles.dbInfoRow}>
                    <span className={styles.dbInfoLabel}>埠號</span>
                    <span className={dbPath.is_env_set ? styles.dbInfoValueLocked : styles.dbInfoValue}>{dbPath.port ?? '—'}</span>
                  </div>
                  <div className={styles.dbInfoRow}>
                    <span className={styles.dbInfoLabel}>資料庫</span>
                    <span className={dbPath.is_env_set ? styles.dbInfoValueLocked : styles.dbInfoValue}>{dbPath.database ?? '—'}</span>
                  </div>
                  <div className={styles.dbInfoRow}>
                    <span className={styles.dbInfoLabel}>使用者</span>
                    <span className={dbPath.is_env_set ? styles.dbInfoValueLocked : styles.dbInfoValue}>{dbPath.user ?? '—'}</span>
                  </div>
                  {dbPath.is_env_set && <span className={styles.envTag}>ENV LOCKED</span>}
                </div>
              </div>

              {/* Thresholds Section */}
              <div className={styles.card}>
                <h3>損益監控門檻</h3>
                <p className={styles.hint}>當投組達到以下條件時觸發通知</p>
                <div className={styles.row}>
                  <SettingInput label={
                    <Tooltip content="投資組合中單一持股的累計損益達到此絕對金額時觸發通知。設為 0 或留空則不監控。">絕對損益門檻 (TWD)</Tooltip>
                  } item={getS('PL_THRESHOLD')} onChange={handleUpdate} type="number" />
                </div>
                <div className={styles.row}>
                  <SettingInput label={
                    <Tooltip content="投資組合中單一持股的累計損益百分比達到此值（相對於成本）時觸發通知。設為 0 或留空則不監控。">損益百分比門檻 (%)</Tooltip>
                  } item={getS('PL_PERCENT_THRESHOLD')} onChange={handleUpdate} type="number" />
                </div>
              </div>

              {/* Telegram Section */}
              <div className={styles.card}>
                <h3>Telegram 告警 (TELEGRAM)</h3>
                <div className={styles.row}>
                  <SettingInput label="Bot Token" item={getS('TELEGRAM_BOT_TOKEN')} onChange={handleUpdate} placeholder="123456:ABC..." />
                </div>
                <div className={styles.row}>
                  <SettingInput label="Chat ID" item={getS('TELEGRAM_CHAT_ID')} onChange={handleUpdate} placeholder="例如: 12345678" />
                </div>
              </div>

              {/* SMTP Section */}
              <div className={styles.card}>
                <h3>Email (SMTP) 伺服器</h3>
                <div className={styles.row}>
                  <SettingInput label="SMTP 伺服器" item={getS('SMTP_SERVER')} onChange={handleUpdate} placeholder="smtp.gmail.com" />
                </div>
                <div className={styles.row}>
                  <SettingInput label="連接埠 (Port)" item={getS('SMTP_PORT')} onChange={handleUpdate} type="number" placeholder="587" />
                </div>
                <div className={styles.row}>
                  <SettingInput label="使用者帳號" item={getS('SMTP_USER')} onChange={handleUpdate} />
                </div>
                <div className={styles.row}>
                  <SettingInput label="發信密碼" item={getS('SMTP_PASSWORD')} onChange={handleUpdate} />
                </div>
              </div>

              {/* Recipients Section */}
              <div className={styles.card}>
                <h3>通知收件者</h3>
                <div className={styles.row}>
                  <SettingInput label="寄件者名稱/地址" item={getS('EMAIL_SENDER')} onChange={handleUpdate} />
                </div>
                <div className={styles.row}>
                  <SettingInput label="收件者地址" item={getS('EMAIL_RECIPIENT')} onChange={handleUpdate} />
                </div>
              </div>
            </div>
          </SkeletonScreen>
        ) : (
          <>
            <div className={styles.header}>
              <h1 className={styles.title}>警示規則設定 Alert Rules</h1>
            </div>
            <AlertRulesPanel />
          </>
        )}
      </DesktopOnly>
      <MobileMessage message="請在桌面環境進行系統設定" />
    </div>
  );
}

function SettingInput({ label, item, onChange, type = "text", placeholder }: {
  label: ReactNode;
  item?: SettingItem;
  onChange: (key: string, val: string) => void;
  type?: string;
  placeholder?: string;
}) {
  if (!item) return null;
  const isLocked = item.is_env_set;

  // T100: sensitive fields show status badge instead of masked value
  if (item.is_sensitive) {
    return (
      <div className={styles.inputField}>
        <label className={styles.fieldLabel}>
          {label}
          {isLocked && <span className={styles.envTag}>ENV LOCKED</span>}
        </label>
        <div className={styles.sensitiveField}>
          <span className={item.has_value ? styles.statusSet : styles.statusUnset}>
            {item.has_value ? '✓ 已設定' : '✗ 未設定'}
          </span>
          <input
            type="password"
            value={item.has_value ? '' : (item.value || '')}
            onChange={(e) => onChange(item.key, e.target.value)}
            disabled={isLocked}
            className={isLocked ? styles.lockedInput : styles.input}
            placeholder={item.has_value ? '輸入新值以變更...' : placeholder}
          />
        </div>
      </div>
    );
  }

  return (
    <div className={styles.inputField}>
      <label className={styles.fieldLabel}>
        {label}
        {isLocked && <span className={styles.envTag}>ENV LOCKED</span>}
      </label>
      <input
        type={item.is_sensitive ? "password" : type}
        value={item.value || ''}
        onChange={(e) => onChange(item.key, e.target.value)}
        disabled={isLocked}
        className={isLocked ? styles.lockedInput : styles.input}
        placeholder={placeholder}
      />
    </div>
  );
}
