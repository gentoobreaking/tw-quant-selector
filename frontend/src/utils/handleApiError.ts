type Severity = 'critical' | 'high' | 'medium' | 'low';

type AddToastFn = (msg: string, severity?: Severity) => void;

const DEFAULT_MESSAGES: Record<number, string> = {
  400: '請求參數錯誤',
  404: '查無資料',
  422: '輸入格式錯誤',
  429: '請求過於頻繁，請稍後再試',
  500: '伺服器內部錯誤',
  503: '伺服器暫時無法處理請求',
};

export function handleApiError(
  e: unknown,
  addToast: AddToastFn,
  context?: string,
  severity: Severity = 'high',
): void {
  const prefix = context ? `${context}：` : '';
  if (e instanceof Error) {
    const status = parseInt(e.message.replace('API ', ''), 10);
    const defaultMsg = DEFAULT_MESSAGES[status] || '';
    const msg = defaultMsg ? `${prefix}${defaultMsg} (${status})` : `${prefix}${e.message}`;
    addToast(msg, severity);
  } else {
    addToast(`${prefix}未知錯誤`, severity);
  }
}