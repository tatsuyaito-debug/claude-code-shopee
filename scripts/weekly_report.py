#!/usr/bin/env python3
"""注文CSVから週次レポート(Markdown)を作る。

使い方:
    python3 scripts/weekly_report.py                        # 直近7日
    python3 scripts/weekly_report.py --days 30
    python3 scripts/weekly_report.py --start 2026-03-01 --end 2026-03-31
    python3 scripts/weekly_report.py --stdout                # ファイルに書かず画面に出す
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shopee import catalog, config, kpi, orders as orders_mod  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="週次レポートの生成")
    parser.add_argument("--market", help="市場コード（既定: business.yaml の primary_market）")
    parser.add_argument("--days", type=int, default=7, help="直近何日ぶんを集計するか（既定: 7）")
    parser.add_argument("--start", help="集計開始日 YYYY-MM-DD（--days より優先）")
    parser.add_argument("--end", help="集計終了日 YYYY-MM-DD")
    parser.add_argument("--orders-dir", type=Path, help="注文CSVの置き場（既定: data/orders）")
    parser.add_argument("--stdout", action="store_true", help="ファイルに書かず標準出力へ")
    args = parser.parse_args(argv)

    business = config.load_business()
    market = config.get_market(args.market or business.primary_market)

    today = _dt.date.today()
    end = args.end or today.isoformat()
    start = args.start or (today - _dt.timedelta(days=args.days - 1)).isoformat()

    orders_dir = args.orders_dir or orders_mod.DEFAULT_ORDERS_DIR
    all_orders = orders_mod.load_all_orders(orders_dir)
    if not all_orders:
        print(f"注文CSVが {orders_dir} にありません。\n"
              "Seller Centre > 注文管理 から注文をエクスポートして、このフォルダに置いてください。",
              file=sys.stderr)
        return 1

    scoped = [o for o in all_orders if not o.market_code or o.market_code == market.code]
    period_orders = orders_mod.filter_period(scoped, start, end)

    try:
        products = catalog.load_products()
    except catalog.CatalogError as exc:
        print(f"⚠️ {exc}\n   原価が分からないので、利益は0として集計します。\n", file=sys.stderr)
        products = []

    summary = kpi.summarize(period_orders, products, market, business, start, end)
    text = kpi.render_markdown(summary, catalog.stock_map(), today)

    if args.stdout:
        print(text)
        return 0

    out = config.REPO_ROOT / "data" / "reports" / f"{today.isoformat()}_{market.code}_weekly.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"📄 レポートを書き出しました: {out}")
    print(f"   売上 {summary.revenue_jpy:,.0f}円 / 粗利 {summary.profit_jpy:,.0f}円 "
          f"/ 注文 {summary.orders_count}件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
