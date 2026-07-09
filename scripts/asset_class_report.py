"""
T142 - 表格輸出與圖表繪製 + 主入口腳本

讀取 T141 的指標結果，產出 Markdown 表格與 matplotlib 淨值走勢圖。
可作為主入口，自動依序觸發 T140 → T141 → T142。

用法:
    python scripts/asset_class_report.py [--skip-download] [--skip-analysis]
"""
import argparse
import json
import os
import pickle
import subprocess
import sys
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

OUTPUT_DIR = Path(__file__).parent.parent / "output" / "asset_comparison_2021_2026"
HOT_STOCKS = ["2330", "2317", "2454", "2412", "2308", "2881", "2882", "2002"]
HOT_NAMES = {
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2412": "中華電",
    "2308": "台達電", "2881": "富邦金", "2882": "國泰金", "2002": "中鋼",
}
YEAR_FIELDS = ["return_2021", "return_2022", "return_2023", "return_2024", "return_2025", "return_2026"]
TABLE_COLS = 2 + 5 + len(YEAR_FIELDS) + 6
SEP_LINE = "|" + "|".join([":---:"] * TABLE_COLS) + "|"

# Try to set Chinese font — probe all available sans-serif fonts for CJK
_font_candidates = [
    "Heiti TC", "Heiti SC", "PingFang TC", "PingFang SC",
    "STHeiti", "SimSong", "Songti SC", "Songti TC",
    "Noto Sans CJK TC", "Noto Sans CJK SC",
]
_font_set = False
for fname in matplotlib.font_manager.fontManager.ttflist:
    if fname.name in _font_candidates:
        matplotlib.rc("font", family=fname.name)
        _font_set = True
        break
if not _font_set:
    for _family in ["Heiti TC", "PingFang TC", "STHeiti", "SimSong"]:
        try:
            matplotlib.rc("font", family=_family)
            plt.figure()
            plt.text(0.5, 0.5, "測試", fontsize=12)
            plt.close()
            _font_set = True
            break
        except Exception:
            continue
if not _font_set:
    print("WARNING: no CJK font found, chart labels may use fallback glyphs")
matplotlib.rcParams["axes.unicode_minus"] = False


def _pct(v) -> str:
    if v is None:
        return "-"
    return f"{v * 100:.2f}%"


def _float(v) -> str:
    if v is None:
        return "-"
    return f"{v:.4f}"


YEAR_LABELS = {"return_2021": "2021", "return_2022": "2022", "return_2023": "2023", "return_2024": "2024", "return_2025": "2025", "return_2026": "2026"}

def build_table(assets: list[dict], title: str) -> str:
    year_headers = "".join([f" {YEAR_LABELS.get(yf, yf)} |" for yf in YEAR_FIELDS])
    header = f"| 代碼 | 名稱 | 總報酬率 | CAGR | 年化波動度 | Sharpe | 最大回撤 |{year_headers} 每週均漲跌 | 每月均漲跌 | 每季均漲跌 | 每週波動度 | 每月波動度 | 每季波動度 |"
    lines = [f"## {title}\n", "", header, SEP_LINE]
    for a in assets:
        year_cells = "".join([f" {_pct(a.get(yf))} |" for yf in YEAR_FIELDS])
        lines.append(
            f"| {a['stock_id']} | {a['name']} "
            f"| {_pct(a.get('total_return'))} | {_pct(a.get('cagr'))} "
            f"| {_pct(a.get('ann_volatility'))} | {_float(a.get('sharpe'))} "
            f"| {_pct(a.get('max_drawdown'))} |"
            f"{year_cells}"
            f" {_pct(a.get('weekly_avg_return'))} | {_pct(a.get('monthly_avg_return'))} "
            f"| {_pct(a.get('quarterly_avg_return'))} | {_pct(a.get('weekly_volatility'))} "
            f"| {_pct(a.get('monthly_volatility'))} | {_pct(a.get('quarterly_volatility'))} |"
        )
    return "\n".join(lines) + "\n"


def build_summary(metrics: dict) -> str:
    lines = ["## 三類資產彙整對照表\n", "", "| 指標 | 台股(前50權值股) | 市場型ETF | 配息型ETF |", "|---|---|---|---|"]
    cats = ["台股", "市场型ETF", "配息型ETF"]
    cat_names = {"台股": "台股(前50權值股)", "市场型ETF": "市場型ETF", "配息型ETF": "配息型ETF"}
    fields = [
        ("平均總報酬率", "total_return", _pct),
        ("平均CAGR", "cagr", _pct),
        ("平均年化波動度", "ann_volatility", _pct),
        ("平均Sharpe", "sharpe", _float),
        ("平均最大回撤", "max_drawdown", _pct),
        ("平均每週漲跌率", "weekly_avg_return", _pct),
        ("平均每月漲跌率", "monthly_avg_return", _pct),
        ("平均每季漲跌率", "quarterly_avg_return", _pct),
        ("平均每週波動度", "weekly_volatility", _pct),
        ("平均每月波動度", "monthly_volatility", _pct),
        ("平均每季波動度", "quarterly_volatility", _pct),
        ("資產數量", "count", lambda v: str(int(v))),
    ]
    avgs = {}
    for cat_name in cats:
        assets = metrics.get(cat_name, [])
        if not assets:
            avgs[cat_name] = {}
            continue
        avg = {}
        for label, field, fmt in fields:
            if field == "count":
                continue
            vals = [a[field] for a in assets if field in a]
            if vals:
                avg[field] = float(np.mean(vals))
        avg["count"] = len(assets)
        avgs[cat_name] = avg

    for label, field, fmt in fields:
        row = f"| {label} "
        for cat_name in cats:
            avg = avgs.get(cat_name, {})
            v = avg.get(field, "-")
            row += f"| {fmt(v) if v != '-' else '-'} "
        row += "|"
        lines.append(row)

    return "\n".join(lines) + "\n"


def draw_chart(metrics: dict, equity_curves: dict):
    fig, ax = plt.subplots(figsize=(16, 8))

    colors = {
        "台股": ("#2196F3", "#BBDEFB"),
        "市场型ETF": ("#4CAF50", "#C8E6C9"),
        "配息型ETF": ("#FF9800", "#FFE0B2"),
    }

    first_date = None
    last_date = None

    for cat_name in ["台股", "市场型ETF", "配息型ETF"]:
        if cat_name not in equity_curves:
            continue
        eq_df = equity_curves[cat_name]
        if eq_df.empty:
            continue

        color_main, color_fill = colors.get(cat_name, ("#666", "#CCC"))

        median = eq_df.median(axis=1)
        upper = eq_df.quantile(0.75, axis=1)
        lower = eq_df.quantile(0.25, axis=1)

        x = median.index
        ax.plot(x, median.values, color=color_main, linewidth=2, label=f"{cat_name} (中位數)")
        ax.fill_between(x, lower.values, upper.values, color=color_fill, alpha=0.3)

        if first_date is None:
            first_date = x[0]
            last_date = x[-1]

    for sid in HOT_STOCKS:
        found = False
        for cat_name in ["台股", "市场型ETF", "配息型ETF"]:
            if cat_name not in equity_curves:
                continue
            eq_df = equity_curves[cat_name]
            if sid in eq_df.columns:
                ax.plot(
                    eq_df.index, eq_df[sid].values,
                    color="#999999", linewidth=1, linestyle="--", alpha=0.7,
                    label=f"{sid} {HOT_NAMES.get(sid, '')}"
                )
                found = True
                break
        if not found:
            print(f"  WARNING: {sid} {HOT_NAMES.get(sid, '')} not found in any equity curves")

    ax.axhline(y=10000, color="#cccccc", linewidth=0.5, linestyle=":")

    ax.set_title("台股資產類別 5 年淨值增長走勢圖（含息）\n2021.01 ~ 2026.07", fontsize=16, pad=20)
    ax.set_xlabel("日期", fontsize=12)
    ax.set_ylabel("淨值 (NT$，初始 $10,000)", fontsize=12)
    ax.legend(loc="upper left", fontsize=9, ncol=2)
    ax.grid(True, alpha=0.3)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))

    fig.tight_layout()
    chart_path = OUTPUT_DIR / "nav_growth_chart_5y.png"
    fig.savefig(chart_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  圖表已儲存: {chart_path}")
    return chart_path


def print_summary(metrics: dict):
    cats = ["台股", "市场型ETF", "配息型ETF"]
    print("\n" + "=" * 60)
    print("台股資產類別比較分析 — 結果摘要")
    print("=" * 60)
    for cat in cats:
        assets = metrics.get(cat, [])
        if not assets:
            continue
        avg_ret = np.mean([a["total_return"] for a in assets]) * 100
        avg_sharpe = np.mean([a["sharpe"] for a in assets])
        avg_mdd = np.mean([a["max_drawdown"] for a in assets]) * 100
        print(f"\n  {cat} ({len(assets)} 檔)")
        print(f"    平均總報酬率: {avg_ret:.2f}%")
        print(f"    平均 Sharpe:  {avg_sharpe:.3f}")
        print(f"    平均最大回撤: {avg_mdd:.2f}%")
        top = assets[0]
        print(f"    最佳: {top['stock_id']} {top['name']} ({top['total_return']*100:.2f}%)")
        bottom = assets[-1]
        print(f"    最差: {bottom['stock_id']} {bottom['name']} ({bottom['total_return']*100:.2f}%)")


def main():
    parser = argparse.ArgumentParser(description="台股資產類別比較分析 - 表格與圖表產出")
    parser.add_argument("--skip-download", action="store_true", help="跳過 T140 下載")
    parser.add_argument("--skip-analysis", action="store_true", help="跳過 T141 分析")
    args = parser.parse_args()

    if not args.skip_download:
        print(">>> 執行 T140: 資產分類與價格下載...")
        subprocess.run([sys.executable, str(Path(__file__).parent / "asset_class_prefetch.py")], check=True)

    if not args.skip_analysis:
        print("\n>>> 執行 T141: 指標計算...")
        subprocess.run([sys.executable, str(Path(__file__).parent / "asset_class_analysis.py")], check=True)

    print("\n=== T142: 輸出表格與圖表 ===")

    metrics_path = OUTPUT_DIR / "metrics_all.json"
    if not metrics_path.exists():
        print("ERROR: metrics_all.json not found. Run analysis first.")
        sys.exit(1)

    with open(metrics_path, "r", encoding="utf-8") as f:
        metrics = json.load(f)

    cats = ["台股", "市场型ETF", "配息型ETF"]
    tables_dir = OUTPUT_DIR
    for cat_name, cat_label in zip(cats, ["台股", "市场型ETF", "配息型ETF"]):
        assets = metrics.get(cat_name, [])
        if not assets:
            print(f"  SKIP {cat_name}: no data")
            continue
        table = build_table(assets, f"{cat_name} — {len(assets)} 檔資產績效比較")
        table_path = tables_dir / f"comparison_table_{cat_name}.md"
        with open(table_path, "w", encoding="utf-8") as f:
            f.write(table)
        print(f"  -> {table_path} ({len(assets)} 檔)")

    summary = build_summary(metrics)
    summary_path = tables_dir / "summary_comparison.md"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary)
    print(f"  -> {summary_path}")

    equity_path = OUTPUT_DIR / "equity_curves.pkl"
    if equity_path.exists():
        print("\n繪製淨值走勢圖...")
        with open(equity_path, "rb") as f:
            equity_curves = pickle.load(f)
        draw_chart(metrics, equity_curves)
    else:
        print("  SKIP chart: equity_curves.pkl not found")

    print_summary(metrics)
    print(f"\n所有輸出檔案位於: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
