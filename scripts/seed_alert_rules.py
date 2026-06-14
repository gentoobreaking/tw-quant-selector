"""
Seed default alert rules into alert_rules table.
T128: alert_rules table initialization with 26 default rules.

Usage:
    python scripts/seed_alert_rules.py           # UPSERT (no overwrite)
    python scripts/seed_alert_rules.py --force   # DELETE-ALL + REINSERT
"""
import argparse
import sys

from sqlalchemy import text

from tw_quant_selector.data.database import get_db

# ── Default Rules (26 rules, 5 categories) ────────────────────────────
DEFAULT_RULES: list[dict] = [
    # Category A: Data Freshness
    {"rule_name": "DATA_PRICE_DELAY",         "threshold": 19.0,  "cooldown_seconds": 3600,   "severity": "HIGH",     "description": "股價資料延遲超過閾值",            "message_template": "股價資料延遲 {hours} 小時（閾值 {threshold} 小時）"},
    {"rule_name": "DATA_INSTITUTIONAL_DELAY", "threshold": 18.5,  "cooldown_seconds": 3600,   "severity": "HIGH",     "description": "法人資料延遲超過閾值",            "message_template": "法人資料延遲 {hours} 小時（閾值 {threshold} 小時）"},
    {"rule_name": "DATA_PRICE_MISSING",       "threshold": 3.0,   "cooldown_seconds": 86400,  "severity": "CRITICAL", "description": "股價資料缺失超過天數",            "message_template": "股價缺失 {days} 天（閾值 {threshold} 天）"},

    # Category B: System Health
    {"rule_name": "SYS_DB_UNREACHABLE",       "threshold": None,  "cooldown_seconds": 300,    "severity": "CRITICAL", "description": "資料庫不可達",               "message_template": "資料庫不可達"},
    {"rule_name": "SYS_SCHEDULER_STOPPED",    "threshold": 25.0,  "cooldown_seconds": 3600,   "severity": "CRITICAL", "description": "排程停止超過小時",               "message_template": "排程器停止 {hours} 小時"},
    {"rule_name": "SYS_DISK_SPACE",           "threshold": 10.0,  "cooldown_seconds": 21600,  "severity": "HIGH",     "description": "磁碟空間低於百分比",               "message_template": "磁碟空間 {free:.1f}%（閾值 {threshold}%）"},

    # Category C: Institutional
    {"rule_name": "INST_HEAVY_BUY",           "threshold": 0.005, "cooldown_seconds": 86400,  "severity": "LOW",      "description": "法人大量買入",               "message_template": "{stock_name}（{stock_id}）法人大量買入 {amount} 張"},
    {"rule_name": "INST_HEAVY_SELL",          "threshold": 5.0,   "cooldown_seconds": 86400,  "severity": "MEDIUM",   "description": "法人大量賣出",               "message_template": "{stock_name}（{stock_id}）法人大量賣出 {amount} 張"},
    {"rule_name": "INST_DIVERGENCE",          "threshold": 3.0,   "cooldown_seconds": 86400,  "severity": "MEDIUM",   "description": "法人買賣分歧",               "message_template": "{stock_name}（{stock_id}）法人分歧：外資 {foreign} 投信 {sity}"},
    {"rule_name": "INST_CONSEC_BUY",          "threshold": 10.0,  "cooldown_seconds": 259200, "severity": "LOW",      "description": "法人連續買入",               "message_template": "{stock_name}（{stock_id}）法人連買 {days} 天"},
    {"rule_name": "INST_QUARTER_END",         "threshold": 5.0,   "cooldown_seconds": 604800, "severity": "LOW",      "description": "季末法人調整",               "message_template": "{stock_name}（{stock_id}）季末法人調整 {net} 張"},

    # Category D: Real-time Price
    {"rule_name": "PRICE_LIMIT_UP",           "threshold": 10.0,  "cooldown_seconds": 86400,  "severity": "LOW",      "description": "漲停",                   "message_template": "{stock_name}（{stock_id}）漲停 {change_pct:+.2f}%"},
    {"rule_name": "PRICE_LIMIT_DOWN",         "threshold": -10.0, "cooldown_seconds": 86400,  "severity": "HIGH",     "description": "跌停",                   "message_template": "{stock_name}（{stock_id}）跌停 {change_pct:+.2f}%"},
    {"rule_name": "PRICE_UNUSUAL_VOLUME",     "threshold": 3.0,   "cooldown_seconds": 86400,  "severity": "LOW",      "description": "異常成交量",               "message_template": "{stock_name}（{stock_id}）異常量 {volume}x 均量"},
    {"rule_name": "PRICE_PE_EXTREME",         "threshold": 95.0,  "cooldown_seconds": 259200, "severity": "MEDIUM",   "description": "本益比極端",               "message_template": "{stock_name}（{stock_id}）本益比 {pe}（百分位 {percentile}）"},
    {"rule_name": "PRICE_STOP_LOSS",          "threshold": -8.0,  "cooldown_seconds": 86400,  "severity": "HIGH",     "description": "停損觸發",               "message_template": "{stock_name}（{stock_id}）停損 {change_pct:+.2f}%（閾值 {threshold}%）"},
    {"rule_name": "PRICE_MIS_UNAVAILABLE",    "threshold": 10.0,  "cooldown_seconds": 1800,   "severity": "HIGH",     "description": "MIS報價不可用",               "message_template": "MIS報價中斷 {minutes} 分鐘"},

    # Category E: Smart Alerts
    {"rule_name": "VOLUME_SPIKE",             "threshold": 10.0,  "cooldown_seconds": 3600,   "severity": "MEDIUM",   "description": "成交量暴增",               "message_template": "{stock_name}（{stock_id}）成交量暴增 {ratio:.1f}x 中位數"},
    {"rule_name": "HIGH_VOL_NO_MOVE",         "threshold": 50.0,  "cooldown_seconds": 3600,   "severity": "LOW",      "description": "高成交量無價格變動",               "message_template": "{stock_name}（{stock_id}）大量無漲跌 成交量第 {rank}"},
    {"rule_name": "TURNOVER_MONSTER",         "threshold": 0.02,  "cooldown_seconds": 3600,   "severity": "MEDIUM",   "description": "週轉率異常",               "message_template": "{stock_name}（{stock_id}）週轉率 {turnover:.2%}"},
    {"rule_name": "INTRADAY_VOLATILITY",      "threshold": 8.0,   "cooldown_seconds": 3600,   "severity": "MEDIUM",   "description": "日內波動率",               "message_template": "{stock_name}（{stock_id}）日內波動 {volatility:.1f}%"},
    {"rule_name": "INDUSTRY_MOMENTUM",        "threshold": 0.30,  "cooldown_seconds": 86400,  "severity": "LOW",      "description": "產業動量",               "message_template": "產業動量 {industry} 平均 {momentum:.2%}"},
    {"rule_name": "AGAINST_TREND",            "threshold": 2.0,   "cooldown_seconds": 86400,  "severity": "MEDIUM",   "description": "逆勢股",                   "message_template": "{stock_name}（{stock_id}）逆勢 {change_pct:+.2f}%（大盤跌）"},
    {"rule_name": "LOW_PRICE_JUNK_RALLY",     "threshold": 0.60,  "cooldown_seconds": 86400,  "severity": "HIGH",     "description": "低價股投機",               "message_template": "{stock_name}（{stock_id}）低價股投機反彈"},
    {"rule_name": "ETF_PREMIUM_DISCOUNT",     "threshold": 0.005, "cooldown_seconds": 3600,   "severity": "LOW",      "description": "ETF折溢價",               "message_template": "{stock_name}（{stock_id}）折溢價 {diff:.2%}"},
    {"rule_name": "WHALE_MOVE",               "threshold": 3.0,   "cooldown_seconds": 3600,   "severity": "CRITICAL", "description": "大戶動向",               "message_template": "{stock_name}（{stock_id}）大戶移動 {volume_ratio:.1f}x"},
    {"rule_name": "ACTIVE_ETF_HYPE",          "threshold": 0.90,  "cooldown_seconds": 86400,  "severity": "LOW",      "description": "主動型ETF熱度",               "message_template": "{stock_name}（{stock_id}）ETF 熱度分位 {percentile:.2%}"},

    # Category F: Technical Analysis (T132)
    {"rule_name": "TECH_MA_CROSS",            "threshold": 0.0,   "cooldown_seconds": 3600,   "severity": "MEDIUM",   "description": "價格站上/跌破移動平均線（60分MA）",  "config_json": "{\"period\": 60, \"direction\": \"above\"}",   "message_template": "{stock_name}（{stock_id}）{direction}穿 {period}MA 收 {close:.2f} MA {sma:.2f}"},
    {"rule_name": "TECH_KD_CROSS",            "threshold": 50.0,  "cooldown_seconds": 3600,   "severity": "MEDIUM",   "description": "KD指標K值穿越門檻（轉強/轉弱）",   "config_json": "{\"kd_n\": 60, \"kd_k1\": 3, \"kd_d1\": 3}",   "message_template": "{stock_name}（{stock_id}）K值 {k:.1f}（門檻 {threshold}）"},

    # Category G: TAIEX Index (T133)
    {"rule_name": "TECH_INDEX_MA",            "threshold": 0.0,   "cooldown_seconds": 3600,   "severity": "HIGH",     "description": "加權指數站上/跌破移動平均線",        "config_json": "{\"period\": 20, \"direction\": \"above\"}",      "message_template": "大盤 {direction}穿 {period}MA 收 {close:.0f} MA {sma:.0f}"},
    {"rule_name": "TECH_INDEX_KD",            "threshold": 80.0,  "cooldown_seconds": 3600,   "severity": "HIGH",     "description": "加權指數 KD 超買/超賣",               "config_json": "{\"kd_n\": 9, \"kd_k1\": 3, \"kd_d1\": 3, \"zone\": \"overbought\"}",    "message_template": "大盤 KD {k:.1f}（{zone}）K值 {k:.1f} D值 {d:.1f}"},
]


def get_default_rules() -> list[dict]:
    """Return the 26 default alert rule definitions.

    Returns:
        list of dicts with keys: rule_name, threshold, cooldown_seconds, severity, description
    """
    return [dict(r) for r in DEFAULT_RULES]


def upsert_alert_rules(rules: list[dict]) -> int:
    """INSERT … ON CONFLICT (rule_name) DO UPDATE non-threshold fields.

    New rules are inserted; existing rules get updated cooldown/severity/description
    but threshold is preserved (not overwritten) so operator tweaks are not lost.

    Args:
        rules: list of dicts with rule_name, threshold, cooldown_seconds, severity, description

    Returns:
        number of rules written
    """
    db = get_db()
    with db.connection() as session:
        stmt = text("""
            INSERT INTO alert_rules (rule_name, enabled, threshold, cooldown_seconds, severity, description, config_json, message_template)
            VALUES (:rule_name, TRUE, :threshold, :cooldown_seconds, :severity, :description, :config_json, :message_template)
            ON CONFLICT (rule_name) DO UPDATE SET
                cooldown_seconds = EXCLUDED.cooldown_seconds,
                severity         = EXCLUDED.severity,
                description      = EXCLUDED.description,
                config_json      = EXCLUDED.config_json,
                message_template = EXCLUDED.message_template,
                updated_at       = CURRENT_TIMESTAMP
        """)
        count = 0
        for r in rules:
            session.execute(stmt, {
                "rule_name": r["rule_name"],
                "threshold": r.get("threshold"),
                "cooldown_seconds": r["cooldown_seconds"],
                "severity": r["severity"],
                "description": r.get("description", ""),
                "config_json": r.get("config_json", "{}"),
                "message_template": r.get("message_template"),
            })
            count += 1
        return count


def run_seed(force: bool = False) -> None:
    """Seed default alert rules into alert_rules.

    Args:
        force: if True, DELETE then re-insert all rules (full overwrite including thresholds)
    """
    db = get_db()
    if force:
        with db.connection() as session:
            session.execute(text("DELETE FROM alert_rules"))
        print("🗑  已清除現有規則")

    rules = get_default_rules()
    if force:
        stmt = text("""
            INSERT INTO alert_rules (rule_name, enabled, threshold, cooldown_seconds, severity, description, config_json, message_template)
            VALUES (:rule_name, TRUE, :threshold, :cooldown_seconds, :severity, :description, :config_json, :message_template)
        """)
        count = 0
        with db.connection() as session:
            for r in rules:
                session.execute(stmt, {
                    "rule_name": r["rule_name"],
                    "threshold": r.get("threshold"),
                    "cooldown_seconds": r["cooldown_seconds"],
                    "severity": r["severity"],
                    "description": r.get("description", ""),
                    "config_json": r.get("config_json", "{}"),
                    "message_template": r.get("message_template"),
                })
                count += 1
    else:
        count = upsert_alert_rules(rules)

    print(f"✅ 已寫入 {count} 條規則")


# ── Backward-compatible entry point ───────────────────────────────────
def seed_alert_rules() -> None:
    """Legacy entry point — delegates to run_seed()."""
    run_seed(force=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed alert_rules table")
    parser.add_argument("--force", action="store_true", help="DELETE all then re-insert (full overwrite)")
    args = parser.parse_args()
    run_seed(force=args.force)
