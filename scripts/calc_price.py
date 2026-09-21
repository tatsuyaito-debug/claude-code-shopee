#!/usr/bin/env python3
"""この商品はいくらで売ればいいのか？を出す。

使い方:
    # 商品マスタに登録済みのSKUから
    python3 scripts/calc_price.py --sku BIB-001

    # まだ登録していない仕入れ候補をその場で計算
    python3 scripts/calc_price.py --cost 1200 --weight 150 --size 20x15x4 --name "ガーゼスタイ3枚組"

    # 競合が399TWDで売っている。合わせたら儲かるか？
    python3 scripts/calc_price.py --sku BIB-001 --competitor 399
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shopee import catalog, config, pricing  # noqa: E402


def parse_size(text: str) -> tuple[float, float, float]:
    """'20x15x4' を (20, 15, 4) にする。"""
    parts = text.lower().replace("×", "x").split("x")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("サイズは 縦x横x高さ の形で書いてください（例: 20x15x4）")
    return tuple(float(p) for p in parts)  # type: ignore[return-value]


def show_quote(label: str, quote: pricing.Quote, market) -> None:
    c = quote.costs
    print(f"\n── {label} ──")
    print(f"  売価           : {quote.price_local:,.2f} {market.currency}"
          f"  (円換算 約 {quote.revenue_jpy:,.0f}円 ※為替バッファ込み)")
    print(f"  ├ 仕入れ(実質) : -{c.effective_cost_jpy:,.0f}円")
    print(f"  ├ 国内送料     : -{c.domestic_ship_jpy:,.0f}円")
    print(f"  ├ 梱包資材     : -{c.packaging_jpy:,.0f}円")
    print(f"  ├ 国際送料     : -{c.international_ship_jpy:,.0f}円"
          f"  (請求重量 {c.billable_weight_kg:.2f}kg)")
    print(f"  ├ Shopee手数料 : -{quote.fee_jpy:,.0f}円")
    print(f"  ├ 広告費(想定) : -{quote.ads_jpy:,.0f}円")
    print(f"  └ 返品ロス想定 : -{quote.return_loss_jpy:,.0f}円")
    print(f"  ▶ 手元に残る   : {quote.profit_jpy:,.0f}円   (利益率 {quote.margin_rate:.1%}"
          f" / 原価の {quote.markup_rate:.2f}倍で販売)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Shopee越境販売の利益計算・推奨売価")
    parser.add_argument("--sku", help="商品マスタに登録済みのSKU")
    parser.add_argument("--name", default="(名称未設定)", help="商品名（その場計算のとき）")
    parser.add_argument("--cost", type=float, help="仕入れ値（円・税込）")
    parser.add_argument("--weight", type=float, help="梱包後の重量（g）")
    parser.add_argument("--size", type=parse_size, default=(0, 0, 0), help="外寸 縦x横x高さ（cm）")
    parser.add_argument("--points", type=float, default=0.0, help="仕入れ時のポイント還元率（例: 0.1）")
    parser.add_argument("--market", help="市場コード（既定: business.yaml の primary_market）")
    parser.add_argument("--competitor", type=float, help="競合の売価（現地通貨）")
    args = parser.parse_args(argv)

    business = config.load_business()
    market = config.get_market(args.market or business.primary_market)

    if args.sku:
        products = {p.sku: p for p in catalog.load_products()}
        if args.sku not in products:
            print(f"SKU '{args.sku}' は商品マスタにありません。"
                  f" 登録済み: {', '.join(sorted(products))}", file=sys.stderr)
            return 1
        product = products[args.sku]
    else:
        if args.cost is None or args.weight is None:
            parser.error("--sku を使わない場合は --cost と --weight が必要です")
        product = pricing.Product(
            sku="TEMP", name_ja=args.name, cost_jpy=args.cost, weight_g=args.weight,
            length_cm=args.size[0], width_cm=args.size[1], height_cm=args.size[2],
            points_back_rate=args.points,
        )

    result = pricing.recommend(product, market, business, args.competitor)

    print("=" * 68)
    print(f"  {product.name_ja}  [{product.sku}]")
    print(f"  販売先: {market.name_ja}({market.code})   "
          f"為替: 1{market.currency} = {market.fx_jpy_per_unit}円"
          f"（{market.fx_age_days()}日前の値）")
    print("=" * 68)

    target: pricing.Quote = result["target"]        # type: ignore[assignment]
    floor: pricing.Quote = result["floor"]          # type: ignore[assignment]
    show_quote(f"推奨売価（目標利益率 {business.pricing.target_margin_rate:.0%}）", target, market)
    print(f"\n  値下げ下限 : {floor.price_local:,.2f} {market.currency}"
          f"（利益 {floor.profit_jpy:,.0f}円 / これ以上は下げない）")
    print(f"  損益分岐点 : {result['breakeven_price_local']:,.2f} {market.currency}"
          f"（これ以下は赤字）")

    if "competitor_match" in result:
        show_quote(f"競合の {args.competitor:,.2f} {market.currency} に合わせた場合",
                   result["competitor_match"], market)  # type: ignore[arg-type]

    print("\n── 判定 ──")
    verdict: pricing.Verdict = result["verdict"]    # type: ignore[assignment]
    if verdict.ok:
        print("  ✅ 出品してよい条件です。")
    else:
        print("  ⛔ このままでは出品を勧めません:")
        for reason in verdict.reasons:
            print(f"     - {reason}")

    comp_verdict = result.get("competitor_verdict")
    if comp_verdict is not None and not comp_verdict.ok:  # type: ignore[union-attr]
        print("  ⛔ 競合価格に合わせるのは不利です:")
        for reason in comp_verdict.reasons:  # type: ignore[union-attr]
            print(f"     - {reason}")

    print()
    return 0 if verdict.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
