import { createContext, useContext } from 'react';
import type { AlertMessage } from '../types';

export interface AlertContextValue {
  alerts: AlertMessage['data'][];
  unread: number;
  markAllRead: () => void;
  getAlertsForStock: (stockId: string) => AlertMessage['data'][];
}

export const AlertContext = createContext<AlertContextValue>({
  alerts: [],
  unread: 0,
  markAllRead: () => {},
  getAlertsForStock: () => [],
});

export function useAlertContext() {
  return useContext(AlertContext);
}
