"""売上・利益の集計と週次レポートの本文生成。

「売れた」だけでは意味がなく、売れて手元にいくら残ったかを見る。
注文CSVの実売価に対して、商品マスタの原価と市場の手数料を当てて
1件ずつ利益を計算し直している（セール値引きで赤字になった注文を見つけるため）。
"""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from .config import Business, Fulfillment, Market
from .orders import Order, is_completed
from .pricing import Product, quote_at_price


@dataclass
class SkuStat:
    sku: str
    name: str
    units: int = 0
    revenue_local: float = 0.0
    revenue_jpy: float = 0.0
    profit_jpy: float = 0.0
    orders: int = 0
    costed: bool = True          # 商品マスタに原価があったか

    @property
    def margin_rate(self) -> float:
        return self.profit_jpy / self.revenue_jpy if self.revenue_jpy else 0.0

    @property
    def profit_per_unit_jpy(self) -> float:
        return self.profit_jpy / self.units if self.units else 0.0


@dataclass
class Summary:
    market: Market
    business: Business
    start: str
    end: str
    orders_count: int = 0
    cancelled_count: int = 0
    units: int = 0
    revenue_local: float = 0.0
    revenue_jpy: float = 0.0
    profit_jpy: float = 0.0
    by_sku: dict[str, SkuStat] = field(default_factory=dict)
    unknown_skus: list[str] = field(default_factory=list)

    @property
    def margin_rate(self) -> float:
        return self.profit_jpy / self.revenue_jpy if self.revenue_jpy else 0.0

    @property
    def avg_order_value_jpy(self) -> float:
        return self.revenue_jpy / self.orders_count if self.orders_count else 0.0

    @property
    def cancel_rate(self) -> float:
        total = self.orders_count + self.cancelled_count
        return self.cancelled_count / total if total else 0.0

    def top(self, n: int = 5, key: str = "profit_jpy") -> list[SkuStat]:
        return sorted(self.by_sku.values(), key=lambda s: getattr(s, key), reverse=True)[:n]

    def losers(self) -> list[SkuStat]:
        """赤字、または最低利益ラインを割っているSKU。"""
        floor = self.business.pricing.min_profit_jpy
        return sorted(
            (s for s in self.by_sku.values() if s.costed and s.profit_per_unit_jpy < floor),
            key=lambda s: s.profit_per_unit_jpy,
        )


def summarize(orders: Iterable[Order], products: Iterable[Product], market: Market,
              business: Business, start: str = "", end: str = "",
              fulfillment: Fulfillment | None = None) -> Summary:
    by_sku_product = {p.sku: p for p in products}
    summary = Summary(market=market, business=business, start=start, end=end)
    unknown: set[str] = set()

    order_sns: set[str] = set()
    for order in orders:
        if not is_completed(order):
            summary.cancelled_count += 1
            continue

        order_sns.add(order.order_sn)
        summary.units += order.quantity
        summary.revenue_local += order.gross_local

        stat = summary.by_sku.get(order.sku)
        if stat is None:
            stat = SkuStat(sku=order.sku, name=order.product_name)
            summary.by_sku[order.sku] = stat
        stat.units += order.quantity
        stat.revenue_local += order.gross_local
        stat.orders += 1

        product = by_sku_product.get(order.sku)
        if product is None:
            # 原価が分からないSKUは売上だけ積み、利益は0として扱う（水増ししない）
            unknown.add(order.sku or "(SKU未設定)")
            stat.costed = False
            revenue_jpy = order.gross_local * market.fx_jpy_per_unit
            stat.revenue_jpy += revenue_jpy
            summary.revenue_jpy += revenue_jpy
            continue

        quote = quote_at_price(product, market, business.pricing, order.price_local,
                               fulfillment=fulfillment)
        stat.revenue_jpy += quote.revenue_jpy * order.quantity
        stat.profit_jpy += quote.profit_jpy * order.quantity
        summary.revenue_jpy += quote.revenue_jpy * order.quantity
        summary.profit_jpy += quote.profit_jpy * order.quantity

    summary.orders_count = len(order_sns)
    summary.unknown_skus = sorted(unknown)
    return summary


def month_progress(today: _dt.date | None = None) -> float:
    """当月がどれだけ進んだか（0〜1）。目標に対する進捗の物差しに使う。"""
    today = today or _dt.date.today()
    if today.month == 12:
        next_month = _dt.date(today.year + 1, 1, 1)
    else:
        next_month = _dt.date(today.year, today.month + 1, 1)
    days_in_month = (next_month - _dt.date(today.year, today.month, 1)).days
    return today.day / days_in_month


def render_markdown(summary: Summary, stock: dict[str, int] | None = None,
                    today: _dt.date | None = None) -> str:
    """奥さんが2分で読める週報にする。数字→意味→次の一手 の順で書く。"""
    today = today or _dt.date.today()
    b = summary.business
    m = summary.market
    stock = stock or {}
    cur = m.currency

    period = f"{summary.start or '開始日未指定'} 〜 {summary.end or today.isoformat()}"
    lines: list[str] = [
        f"# {m.name_ja}ショップ 週次レポート",
        "",
        f"- 集計期間: **{period}**",
        f"- 作成日: {today.isoformat()}",
        "",
        "## 1. 今週の数字",
        "",
        "| 項目 | 実績 |",
        "|---|---|",
        f"| 売上 | {summary.revenue_local:,.0f} {cur}（約 {summary.revenue_jpy:,.0f} 円） |",
        f"| 粗利 | **{summary.profit_jpy:,.0f} 円**（利益率 {summary.margin_rate:.1%}） |",
        f"| 注文数 | {summary.orders_count} 件 / 販売個数 {summary.units} 個 |",
        f"| 平均注文単価 | {summary.avg_order_value_jpy:,.0f} 円 |",
        f"| キャンセル・返品 | {summary.cancelled_count} 件（{summary.cancel_rate:.1%}） |",
        "",
    ]

    # 月間目標への進捗
    if b.monthly_profit_jpy:
        progress = summary.profit_jpy / b.monthly_profit_jpy
        pace = month_progress(today)
        verdict = "🟢 ペース通り" if progress >= pace else "🔴 ペース不足"
        lines += [
            "## 2. 月間目標に対して",
            "",
            f"- 目標利益: {b.monthly_profit_jpy:,.0f} 円 / 月",
            f"- ここまでの利益: {summary.profit_jpy:,.0f} 円（達成率 {progress:.0%}）",
            f"- 月の経過: {pace:.0%} → **{verdict}**",
            "",
        ]

    lines += ["## 3. 売れ筋トップ5（利益額順）", "", "| SKU | 商品 | 個数 | 売上 | 粗利 | 利益率 |", "|---|---|---|---|---|---|"]
    top = summary.top(5)
    if top:
        for s in top:
            name = (s.name or "")[:24]
            profit = f"{s.profit_jpy:,.0f} 円" if s.costed else "原価未登録"
            rate = f"{s.margin_rate:.1%}" if s.costed else "—"
            lines.append(
                f"| {s.sku} | {name} | {s.units} | {s.revenue_local:,.0f} {cur} | {profit} | {rate} |"
            )
    else:
        lines.append("| — | 売上なし | — | — | — | — |")
    lines.append("")

    losers = summary.losers()
    lines += ["## 4. 手を入れるべき商品", ""]
    if losers:
        lines.append(f"1個あたり利益が最低ライン（{b.pricing.min_profit_jpy:,.0f}円）を下回っています。")
        lines.append("")
        lines.append("| SKU | 商品 | 1個あたり利益 | 打ち手の候補 |")
        lines.append("|---|---|---|---|")
        for s in losers[:8]:
            if s.profit_per_unit_jpy < 0:
                action = "**即値上げ or 出品停止**（売るほど赤字）"
            else:
                action = "値上げ / 仕入れ値の交渉 / 軽量な梱包に変更"
            lines.append(f"| {s.sku} | {(s.name or '')[:24]} | {s.profit_per_unit_jpy:,.0f} 円 | {action} |")
    else:
        lines.append("利益ラインを割っている商品はありません。良い状態です。")
    lines.append("")

    # 在庫アラート
    threshold = int((b.operations or {}).get("restock_threshold", 3))
    low = [(sku, qty) for sku, qty in stock.items() if qty <= threshold and sku in summary.by_sku]
    lines += ["## 5. 在庫アラート", ""]
    if low:
        for sku, qty in sorted(low, key=lambda x: x[1]):
            name = summary.by_sku[sku].name or ""
            lines.append(f"- 🔻 `{sku}` {name[:24]} … 残り **{qty}個**（{threshold}個以下）→ 補充する")
    else:
        lines.append("- 補充が必要な商品はありません。")
    lines.append("")

    if summary.unknown_skus:
        lines += [
            "## 6. データの穴",
            "",
            "次のSKUが商品マスタ（`data/products/products.csv`）に無いため、**利益が計算できていません**。",
            "売上だけ計上し、利益は0として扱っています。原価を登録してください。",
            "",
        ]
        lines += [f"- `{sku}`" for sku in summary.unknown_skus]
        lines.append("")

    if m.fx_is_stale():
        lines += [
            "> ⚠️ **為替レートが古いです**"
            f"（{m.fx_age_days()}日前）。`config/markets.yaml` の `fx.jpy_per_unit` と "
            "`updated_at` を更新してから、この数字を信じてください。",
            "",
        ]

    return "\n".join(lines)
