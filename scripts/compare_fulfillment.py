#!/usr/bin/env python3
"""自社発送と発送代行、どちらが得かを数字で比べる。

代行は「現金を払って時間を買う」取引。比べるべきは費用だけではなく、
空いた時間を新規出品に回せるかどうか。このスクリプトは両方を出す。

使い方:
    python3 scripts/compare_fulfillment.py --sku BIB-001
    python3 scripts/compare_fulfillment.py --sku BIB-001 --monthly-units 80
    python3 scripts/compare_fulfillment.py --cost 1200 --weight 150 --size 20x15x4
    python3 scripts/compare_fulfillment.py --sku BIB-001 --providers self shopeeking
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shopee import catalog, config, pricing  # noqa: E402

# 料金が1円も入っていない代行プロバイダは、比較しても意味がないので警告する
PLACEHOLDER_HINT = (
    "  ⚠️ {name} の料金が未入力です（全項目0円）。\n"
    "     config/fulfillment.yaml の providers.{code} に、契約の実数を入れてください。\n"
    "     入れるまで、この比較結果は使えません。"
)


def width(text: str) -> int:
    """全角文字を2桁として数えた表示幅。日本語の表がずれないようにするため。"""
    return sum(2 if unicodedata.east_asian_width(c) in "FWA" else 1 for c in text)


def pad(text: str, n: int) -> str:
    """表示幅 n になるよう右に空白を足す。"""
    return text + " " * max(0, n - width(text))


def parse_size(text: str) -> tuple[float, float, float]:
    parts = text.lower().replace("×", "x").split("x")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("サイズは 縦x横x高さ の形で書いてください（例: 20x15x4）")
    return tuple(float(p) for p in parts)  # type: ignore[return-value]


def is_unfilled(ff: config.Fulfillment) -> bool:
    """代行なのに費用が全部0 = まだ料金を入れていない、とみなす。"""
    return ff.looks_unpriced()


def crossover_units(a: config.Fulfillment, b: config.Fulfillment) -> float | None:
    """月間何個から b のほうが a より1個あたり安くなるか。

    1個あたり費用 = 変動費 + 月額固定費 / 出荷数 なので、
    両者が等しくなる出荷数を解く。交差しない場合は None。
    """
    a_var = a.cost_at_volume(0)
    b_var = b.cost_at_volume(0)
    fixed_diff = b.monthly_fixed_jpy - a.monthly_fixed_jpy
    var_diff = a_var - b_var
    if var_diff <= 0:
        return None            # 変動費でも b が高いなら、どれだけ数を出しても逆転しない
    if fixed_diff <= 0:
        return 0.0             # 固定費も変動費も b のほうが安い。最初から b が得
    return fixed_diff / var_diff


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="自社発送と発送代行の比較")
    parser.add_argument("--sku", help="商品マスタに登録済みのSKU")
    parser.add_argument("--name", default="(名称未設定)", help="商品名（その場計算のとき）")
    parser.add_argument("--cost", type=float, help="仕入れ値（円・税込）")
    parser.add_argument("--weight", type=float, help="梱包後の重量（g）")
    parser.add_argument("--size", type=parse_size, default=(0, 0, 0), help="外寸 縦x横x高さ（cm）")
    parser.add_argument("--points", type=float, default=0.0, help="仕入れ時のポイント還元率")
    parser.add_argument("--market", help="市場コード（既定: primary_market）")
    parser.add_argument("--providers", nargs="*", help="比べる発送主体（既定: 全部）")
    parser.add_argument("--monthly-units", type=int,
                        help="月間の出荷個数の想定（既定: 各プロバイダの expected_monthly_units）")
    args = parser.parse_args(argv)

    business = config.load_business()
    market = config.get_market(args.market or business.primary_market)
    active, providers = config.load_fulfillments()

    codes = args.providers or list(providers)
    for code in codes:
        if code not in providers:
            print(f"発送主体 '{code}' は config/fulfillment.yaml にありません。"
                  f" 使えるのは: {', '.join(providers)}", file=sys.stderr)
            return 1
    selected = [providers[c] for c in codes]

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

    print("=" * 72)
    print(f"  発送方法の比較： {product.name_ja}  [{product.sku}]")
    print(f"  販売先: {market.name_ja}({market.code})   "
          f"（いま有効な設定: {providers[active].name_ja}）")
    print("=" * 72)

    # --- 未入力の警告（先に出す。これを見落とすと結論を間違える） ---
    unfilled = [f for f in selected if is_unfilled(f)]
    for ff in unfilled:
        print()
        print(PLACEHOLDER_HINT.format(name=ff.name_ja, code=ff.code))

    # --- 1個あたりの比較 ---
    print("\n── 1個あたり（同じ目標利益率で値付けした場合） ──\n")
    name_w = max(24, max(width(f.name_ja) for f in selected) + 2)
    header = (pad("発送方法", name_w) + pad("発送費", 10) + pad("推奨売価", 14)
              + pad("手元に残る", 13) + pad("出荷まで", 10) + "手間")
    print(header)
    print("-" * width(header))

    rows: list[tuple[config.Fulfillment, pricing.Quote]] = []
    for ff in selected:
        quote = pricing.price_for_margin(
            product, market, business.pricing, business.pricing.target_margin_rate, fulfillment=ff
        )
        rows.append((ff, quote))
        print(pad(ff.name_ja, name_w)
              + pad(f"{ff.per_unit_jpy:,.0f}円", 10)
              + pad(f"{quote.price_local:,.0f} {market.currency}", 14)
              + pad(f"{quote.profit_jpy:,.0f}円", 13)
              + pad(f"{ff.handling_days}日", 10)
              + f"{ff.labor_minutes_per_order:,.0f}分")

    # --- 同じ売価で売った場合（実際の比較はこちらが正しい） ---
    base_price = rows[0][1].price_local
    print(f"\n── 同じ売価 {base_price:,.0f} {market.currency} で売った場合の利益 ──\n")
    for ff, _ in rows:
        q = pricing.quote_at_price(product, market, business.pricing, base_price, fulfillment=ff)
        print(f"  {pad(ff.name_ja, name_w)}{pad(f'{q.profit_jpy:,.0f}円', 12)}"
              f"(利益率 {q.margin_rate:.1%})")

    # --- 自社発送 vs 代行 の損益分岐点 ---
    if "self" in codes and len(codes) >= 2:
        me = providers["self"]
        for ff in selected:
            if ff.code == "self":
                continue
            print(f"\n── 自社発送 と {ff.name_ja} の比較 ──\n")

            volume = args.monthly_units or ff.expected_monthly_units
            self_cost = me.cost_at_volume(volume)
            ff_cost = ff.cost_at_volume(volume)
            diff_per_unit = ff_cost - self_cost
            monthly_diff = diff_per_unit * volume

            print(f"  想定出荷数: 月 {volume} 個")
            print(f"  1個あたり発送費: 自社 {self_cost:,.0f}円  →  {ff.name_ja} {ff_cost:,.0f}円"
                  f"  （差 {diff_per_unit:+,.0f}円）")
            print(f"  月あたりの差額: {monthly_diff:+,.0f}円")

            # 空く時間と、その時間を買う値段
            minutes_freed = (me.labor_minutes_per_order - ff.labor_minutes_per_order) * volume
            hours_freed = minutes_freed / 60.0
            hourly_value = float((business.operations or {}).get("hourly_value_jpy", 0) or 0)

            if hours_freed > 0:
                print(f"  空く時間: 月 {hours_freed:,.1f} 時間"
                      f"（1注文1個として計算）")
                if monthly_diff > 0:
                    implied = monthly_diff / hours_freed
                    print(f"  → その時間を **時給 {implied:,.0f}円 で買う** ことになります")
                    if hourly_value:
                        if implied <= hourly_value:
                            print(f"     あなたの時間の値段 {hourly_value:,.0f}円/時 より安い → ✅ 任せる価値あり")
                        else:
                            print(f"     あなたの時間の値段 {hourly_value:,.0f}円/時 より高い → ⚠️ 出荷数が増えるまで待つ")
                else:
                    print("  → 現金も安く、時間も空きます → ✅ 任せない理由がありません")

            cross = crossover_units(me, ff)
            if cross is None:
                print(f"  損益分岐: 出荷数をいくら増やしても、現金では自社発送のほうが安いままです")
            elif cross <= 0:
                print(f"  損益分岐: 出荷数にかかわらず {ff.name_ja} のほうが現金でも安いです")
            else:
                print(f"  損益分岐: **月 {cross:,.0f} 個** を超えると、現金でも {ff.name_ja} のほうが安くなります")

            if ff.is_stale():
                print(f"  ⚠️ {ff.name_ja} の料金が {ff.updated_at} 以降更新されていません。"
                      "改定されていないか確認してください")

    if unfilled:
        print("\n料金が未入力のプロバイダがあるため、上の結論はまだ信用できません。")
        return 2

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
