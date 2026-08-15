"""Intraday technical alert checks (TECH_MA_CROSS, TECH_KD_CROSS,
TECH_INDEX_MA, TECH_INDEX_KD).

Module: technical_checker.py
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from tw_quant_selector.monitoring.base_checker import MARKET_CLOSE, MARKET_OPEN
from tw_quant_selector.monitoring.indicators import compute_kd, compute_sma
from tw_quant_selector.monitoring.notifiers import format_alert


class TechnicalCheckerMixin:
    def check_technical_alerts(self, now: Optional[datetime] = None) -> list[dict]:
        """Check intraday K-line indicators (MA, KD) and trigger alerts.

        Reads from intraday_kline table (built by build_intraday_kline),
        computes SMA(60) on 60-min close prices, and KD(60,3,3), then
        evaluates conditions defined in alert_rules.
        """
        now = now or datetime.now()
        t = now.time()
        if not (MARKET_OPEN <= t <= MARKET_CLOSE):
            return []
        if now.weekday() >= 5:
            return []

        triggered: list[dict] = []

        # Load rules from DB (include config_json and message_template)
        rules = self.db.execute(
            "SELECT rule_name, enabled, threshold, cooldown_seconds, severity, config_json, message_template FROM alert_rules WHERE enabled = TRUE"
        ).fetchall()
        rule_map = {}
        for r in rules:
            cj = {}
            if r[5]:
                try:
                    cj = json.loads(r[5])
                except (json.JSONDecodeError, TypeError):
                    cj = {}
            rule_map[r[0]] = {"threshold": r[2], "cooldown_seconds": r[3] or 3600, "severity": r[4], "config": cj, "message_template": r[6]}

        if not rule_map:
            return triggered

        # Get stocks with recent intraday kline data
        today = now.date()
        stock_rows = self.db.execute("""
            SELECT DISTINCT stock_id FROM intraday_kline
            WHERE k_time::date = ?
        """, [today]).fetchall()

        kline_stocks = [r[0] for r in stock_rows]
        if not kline_stocks:
            return triggered

        for stock_id in kline_stocks:
            is_index = stock_id == '^TWII'
            kline_rows = self.db.execute("""
                SELECT k_time, open, high, low, close, volume
                FROM intraday_kline
                WHERE stock_id = ? AND k_time::date = ?
                ORDER BY k_time ASC
            """, [stock_id, today]).fetchall()

            if len(kline_rows) < 2:
                continue

            closes = [float(r[3]) for r in kline_rows]
            highs = [float(r[2]) for r in kline_rows]
            lows = [float(r[1]) for r in kline_rows]

            stock_name = '加權指數' if is_index else ''

            # ── Stock-level rules (TECH_MA_CROSS, TECH_KD_CROSS) ──
            if not is_index:
                ma_config = rule_map.get('TECH_MA_CROSS', {}).get('config', {})
                ma_period = int(ma_config.get('period', 60))
                ma_direction = ma_config.get('direction', 'above')

                sma_values = compute_sma(closes, min(ma_period, len(closes)))
                latest_close = closes[-1]
                latest_sma = sma_values[-1] if sma_values and sma_values[-1] is not None else None

                kd_config = rule_map.get('TECH_KD_CROSS', {}).get('config', {})
                kd_n = int(kd_config.get('kd_n', 60))
                kd_k1 = int(kd_config.get('kd_k1', 3))
                kd_d1 = int(kd_config.get('kd_d1', 3))
                kd_n = min(kd_n, len(closes))

                _, k_vals, d_vals = compute_kd(highs, lows, closes, n=kd_n, k1=kd_k1, d1=kd_d1)
                latest_k = k_vals[-1] if k_vals and k_vals[-1] is not None else None

                # Check TECH_MA_CROSS
                ma_rule = rule_map.get('TECH_MA_CROSS')
                if ma_rule and latest_sma is not None and latest_sma > 0:
                    threshold = ma_rule['threshold'] or 0
                    crossed = (
                        (ma_direction == 'above' and latest_close >= latest_sma * (1 + threshold / 100)) or
                        (ma_direction == 'below' and latest_close <= latest_sma * (1 - threshold / 100))
                    )
                    if crossed:
                        dir_label = '上' if ma_direction == 'above' else '下'
                        default_msg = f"{dir_label}穿{ma_period}MA: {stock_id} 收盤 {latest_close:.2f} MA {latest_sma:.2f}"
                        msg = self._format_alert_message(
                            ma_rule.get('message_template'), default_msg,
                            stock_id=stock_id, stock_name=stock_name, close=latest_close, sma=latest_sma,
                            period=ma_period, direction=dir_label, threshold=threshold,
                        )
                        self._log_history('TECH_MA_CROSS', ma_rule['severity'], msg, {
                            'stock_id': stock_id, 'close': latest_close, 'sma': latest_sma,
                        })
                        triggered.append({
                            'alert_type': 'TECH_MA_CROSS',
                            'severity': ma_rule['severity'],
                            'stock_id': stock_id,
                            'stock_name': stock_name,
                            'message': msg,
                            'details': {'close': latest_close, 'sma': latest_sma},
                        })
                        if self._check_cooldown('TECH_MA_CROSS', ma_rule['cooldown_seconds']):
                            self.manager.send_notification(
                                f'[tw-quant-selector] TECH_MA_CROSS',
                                format_alert(ma_rule['severity'], 'TECH_MA_CROSS', msg),
                            )

                # Check TECH_KD_CROSS
                kd_rule = rule_map.get('TECH_KD_CROSS')
                if kd_rule and latest_k is not None:
                    threshold = kd_rule['threshold'] or 50
                    if latest_k >= threshold:
                        default_msg = f"K值站上{threshold:.0f}: {stock_id} K {latest_k:.1f}"
                        msg = self._format_alert_message(
                            kd_rule.get('message_template'), default_msg,
                            stock_id=stock_id, stock_name=stock_name, k=latest_k, threshold=threshold,
                        )
                        self._log_history('TECH_KD_CROSS', kd_rule['severity'], msg, {
                            'stock_id': stock_id, 'k': latest_k,
                        })
                        triggered.append({
                            'alert_type': 'TECH_KD_CROSS',
                            'severity': kd_rule['severity'],
                            'stock_id': stock_id,
                            'stock_name': stock_name,
                            'message': msg,
                            'details': {'k': latest_k},
                        })
                        if self._check_cooldown('TECH_KD_CROSS', kd_rule['cooldown_seconds']):
                            self.manager.send_notification(
                                f'[tw-quant-selector] TECH_KD_CROSS',
                                format_alert(kd_rule['severity'], 'TECH_KD_CROSS', msg),
                            )

            # ── Index-level rules (TECH_INDEX_MA, TECH_INDEX_KD) ──
            if is_index:
                # TECH_INDEX_MA
                idx_ma_rule = rule_map.get('TECH_INDEX_MA')
                if idx_ma_rule:
                    ma_cfg = idx_ma_rule.get('config', {})
                    idx_ma_period = int(ma_cfg.get('period', 20))
                    idx_ma_dir = ma_cfg.get('direction', 'above')
                    idx_sma_vals = compute_sma(closes, min(idx_ma_period, len(closes)))
                    idx_close = closes[-1]
                    idx_sma = idx_sma_vals[-1] if idx_sma_vals and idx_sma_vals[-1] is not None else None
                    if idx_sma is not None and idx_sma > 0:
                        idx_threshold = idx_ma_rule['threshold'] or 0
                        crossed = (
                            (idx_ma_dir == 'above' and idx_close >= idx_sma * (1 + idx_threshold / 100)) or
                            (idx_ma_dir == 'below' and idx_close <= idx_sma * (1 - idx_threshold / 100))
                        )
                        if crossed:
                            dir_label = '上' if idx_ma_dir == 'above' else '下'
                            default_msg = f"大盤{dir_label}穿{idx_ma_period}MA: {idx_close:.0f} MA {idx_sma:.0f}"
                            msg = self._format_alert_message(
                                idx_ma_rule.get('message_template'), default_msg,
                                stock_id=stock_id, stock_name=stock_name, close=idx_close, sma=idx_sma,
                                period=idx_ma_period, direction=dir_label, threshold=idx_threshold,
                            )
                            self._log_history('TECH_INDEX_MA', idx_ma_rule['severity'], msg, {
                                'stock_id': stock_id, 'close': idx_close, 'sma': idx_sma,
                            })
                            triggered.append({
                                'alert_type': 'TECH_INDEX_MA',
                                'severity': idx_ma_rule['severity'],
                                'stock_id': stock_id,
                                'stock_name': stock_name,
                                'message': msg,
                                'details': {'close': idx_close, 'sma': idx_sma},
                            })
                            if self._check_cooldown('TECH_INDEX_MA', idx_ma_rule['cooldown_seconds']):
                                self.manager.send_notification(
                                    f'[tw-quant-selector] TECH_INDEX_MA',
                                    format_alert(idx_ma_rule['severity'], 'TECH_INDEX_MA', msg),
                                )

                # TECH_INDEX_KD
                idx_kd_rule = rule_map.get('TECH_INDEX_KD')
                if idx_kd_rule:
                    kd_cfg = idx_kd_rule.get('config', {})
                    idx_kd_n = int(kd_cfg.get('kd_n', 9))
                    idx_kd_k1 = int(kd_cfg.get('kd_k1', 3))
                    idx_kd_d1 = int(kd_cfg.get('kd_d1', 3))
                    idx_kd_n = min(idx_kd_n, len(closes))
                    _, idx_k_vals, idx_d_vals = compute_kd(highs, lows, closes, n=idx_kd_n, k1=idx_kd_k1, d1=idx_kd_d1)
                    idx_k = idx_k_vals[-1] if idx_k_vals and idx_k_vals[-1] is not None else None
                    idx_d = idx_d_vals[-1] if idx_d_vals and idx_d_vals[-1] is not None else None
                    if idx_k is not None:
                        zone = kd_cfg.get('zone', 'overbought')
                        idx_threshold = idx_kd_rule['threshold'] or 80
                        triggered_flag = (
                            (zone == 'overbought' and idx_k >= idx_threshold) or
                            (zone == 'oversold' and idx_k <= (100 - idx_threshold))
                        )
                        if triggered_flag:
                            zone_label = '超買' if zone == 'overbought' else '超賣'
                            default_msg = f"大盤KD{zone_label}: K {idx_k:.1f}"
                            msg = self._format_alert_message(
                                idx_kd_rule.get('message_template'), default_msg,
                                stock_id=stock_id, stock_name=stock_name, k=idx_k, d=idx_d,
                                zone=zone_label, threshold=idx_threshold,
                            )
                            self._log_history('TECH_INDEX_KD', idx_kd_rule['severity'], msg, {
                                'stock_id': stock_id, 'k': idx_k, 'd': idx_d,
                            })
                            triggered.append({
                                'alert_type': 'TECH_INDEX_KD',
                                'severity': idx_kd_rule['severity'],
                                'stock_id': stock_id,
                                'stock_name': stock_name,
                                'message': msg,
                                'details': {'k': idx_k, 'd': idx_d},
                            })
                            if self._check_cooldown('TECH_INDEX_KD', idx_kd_rule['cooldown_seconds']):
                                self.manager.send_notification(
                                    f'[tw-quant-selector] TECH_INDEX_KD',
                                    format_alert(idx_kd_rule['severity'], 'TECH_INDEX_KD', msg),
                                )

        return triggered