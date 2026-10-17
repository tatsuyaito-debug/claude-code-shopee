#!/usr/bin/env python3
"""セットアップ健診：いま何が埋まっていなくて、それをいつ埋めるべきかを出す。

設定が仮の数字のままだと、利益計算も商品選定も「目安」にしかならない。
ただし全部を今すぐ埋める必要はなく、意味を持つタイミングが項目ごとに違う。
このスクリプトは、実績データを見たうえで

    いま埋める / 条件が来たら埋める / もう埋まっている

の3つに仕分けて出す。作業を増やさないために、「まだやらなくていい」を
はっきり言うことをこのスクリプトの仕事の半分だと考えている。

使い方:
    python3 scripts/check_setup.py
"""

from __future__ import annotations

import datetime as _dt
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shopee import catalog, config, orders as orders_mod  # noqa: E402

NOW = "now"        # いま埋める
LATER = "later"    # 条件が来たら埋める
DONE = "done"      # 埋まっている

# 発送代行が割に合い始めるおおよその出荷数。これ未満なら月額費の按分が重すぎる。
AGENCY_THRESHOLD_UNITS = 20


def agency_status(units_last_30d: int, verified: bool, unpriced: bool) -> str:
    """発送代行の料金を「いま調べるべきか」を決める。

    出荷が少ないうちに調べても使い道がない（月額費の按分が重すぎて、
    どう計算しても自社発送に負ける）ので、出荷数で素直に仕分ける。
    """
    if verified and not unpriced:
        return DONE
    return NOW if units_last_30d >= AGENCY_THRESHOLD_UNITS else LATER


@dataclass
class Item:
    key: str
    title: str
    status: str
    minutes: int = 0
    now_value: str = ""          # いまの設定値
    why: str = ""                # なぜ必要か / なぜまだ不要か
    how: list[str] = field(default_factory=list)   # どこで調べるか
    trigger: str = ""            # いつやるか（status=LATER のとき）


def monthly_units_shipped(orders_dir: Path | None = None,
                          today: _dt.date | None = None) -> tuple[int, str]:
    """直近30日の出荷個数。サンプルCSVは実績に数えない。"""
    orders_dir = orders_dir or orders_mod.DEFAULT_ORDERS_DIR
    real_files = [p for p in sorted(orders_dir.glob("*.csv")) if "sample" not in p.stem.lower()]
    if not real_files:
        return 0, "実績データなし（data/orders にあるのはサンプルだけ）"

    rows: list[orders_mod.Order] = []
    for path in real_files:
        try:
            rows.extend(orders_mod.load_orders(path))
        except orders_mod.OrderParseError:
            continue
    if not rows:
        return 0, "注文CSVを読めませんでした"

    cutoff = ((today or _dt.date.today()) - _dt.timedelta(days=30)).isoformat()
    recent = [o for o in rows if o.order_date >= cutoff and orders_mod.is_completed(o)]
    units = sum(o.quantity for o in recent)
    return units, f"直近30日の出荷 {units}個"


def catalog_state(path: Path | None = None) -> tuple[int, int, int]:
    """商品マスタの (実商品数, サンプル行数, 重量未入力数) を返す。"""
    rows = catalog.load_rows(path)
    sample = sum(1 for r in rows if "サンプル" in (r.get("note") or ""))
    real = len(rows) - sample
    no_weight = sum(
        1 for r in rows
        if "サンプル" not in (r.get("note") or "") and not (r.get("weight_g") or "").strip()
    )
    return real, sample, no_weight


def build_items() -> list[Item]:
    business = config.load_business()
    market = config.get_market(business.primary_market)
    _, providers = config.load_fulfillments()
    agency = [f for code, f in providers.items() if code != "self"]
    real_products, sample_products, missing_weight = catalog_state()
    units, units_note = monthly_units_shipped()

    items: list[Item] = []

    # ---------------- 為替 ----------------
    age = market.fx_age_days()
    items.append(Item(
        key="fx",
        title=f"{market.name_ja}の為替レート",
        status=DONE if not market.fx_is_stale() else NOW,
        minutes=5,
        now_value=f"1 {market.currency} = {market.fx_jpy_per_unit}円（{age}日前に更新）",
        why="売価はすべてこのレートで円に換算される。古いと利益がまるごとずれる",
        how=[
            "「台湾ドル 円」で検索して中値を見る",
            "config/markets.yaml の fx.jpy_per_unit と updated_at を直す",
        ],
        trigger="毎月1回",
    ))

    # ---------------- Shopee手数料 ----------------
    items.append(Item(
        key="fees",
        title="Shopeeの手数料率",
        status=DONE if market.fees.verified else NOW,
        minutes=20,
        now_value=(f"販売{market.fees.commission_rate:.1%} + 決済{market.fees.transaction_fee_rate:.1%}"
                   f" = 合計{market.fees.total_rate:.1%}（仮の数字）"),
        why="売上から率で引かれる。1%違うと推奨売価と損益分岐点が両方ずれる",
        how=[
            "Seller Centre にログインして、手数料／マイ収益の案内ページを開く",
            "※ メニュー名は時期で変わるので、見つからなければサポートに聞くのが早い",
            "自分のアカウントに適用されている率をメモする（無料配送プログラム等に",
            "  入っていると service_fee_rate が乗る）",
            "config/markets.yaml の fees に入れて、verified: true にする",
        ],
    ))

    # ---------------- SLS送料 ----------------
    items.append(Item(
        key="shipping",
        title="SLSの国際送料",
        status=DONE if market.shipping.verified else NOW,
        minutes=20,
        now_value=(f"基本 {market.shipping.base_jpy:,.0f}円 + "
                   f"{market.shipping.per_kg_jpy:,.0f}円/kg（仮の数字）"),
        why="越境で利益を一番食う費目。ここが違うと「儲かるはずが赤字」になる",
        how=[
            "Shopee Japan から案内されるSLSの料金表を開く",
            "台湾向けの、重量帯ごとの料金を見る",
            "基本料と1kgあたりの単価に直して config/markets.yaml の shipping に入れる",
            "重量帯が階段状なら、よく使う重量帯（100〜300g）に合う直線で近似すればよい",
            "入れたら verified: true にする",
        ],
    ))

    # ---------------- 商品マスタの重量 ----------------
    if real_products == 0:
        items.append(Item(
            key="weight",
            title="商品マスタの重量・サイズ",
            status=LATER,
            minutes=30,
            now_value=f"実商品 0件（サンプル行が{sample_products}件あるだけ）",
            why="実測した梱包後の重量がないと、国際送料＝利益が計算できない",
            trigger="最初の商品を仕入れて、手元に届いたとき",
            how=[
                "キッチンスケールで「梱包した状態」を量る（商品＋OPP袋＋封筒）",
                "メジャーで外寸3辺を測る（大きいと容積重量で送料が上がる）",
                "data/products/products.csv に1行ずつ入れる。サンプル行は消す",
                "入れ終わったら python3 scripts/calc_price.py --sku <SKU> で検算する",
            ],
        ))
    elif missing_weight:
        items.append(Item(
            key="weight",
            title="商品マスタの重量・サイズ",
            status=NOW,
            minutes=5 * missing_weight,
            now_value=f"実商品{real_products}件のうち、重量未入力が{missing_weight}件",
            why="重量が空の商品は送料が計算できず、利益が出せない",
            how=["キッチンスケールで梱包後を実測して products.csv に入れる"],
        ))
    else:
        items.append(Item(
            key="weight",
            title="商品マスタの重量・サイズ",
            status=DONE,
            now_value=f"実商品{real_products}件すべてに重量が入っています",
        ))

    # ---------------- 発送代行の料金 ----------------
    for ff in agency:
        if agency_status(units, ff.verified, ff.looks_unpriced()) == DONE:
            items.append(Item(
                key=f"ff-{ff.code}",
                title=f"{ff.name_ja}の料金",
                status=DONE,
                now_value=f"1個あたり {ff.per_unit_jpy:,.0f}円（月{ff.expected_monthly_units}個想定）",
            ))
            continue

        status = agency_status(units, ff.verified, ff.looks_unpriced())
        enough = status == NOW
        items.append(Item(
            key=f"ff-{ff.code}",
            title=f"{ff.name_ja}の料金6項目",
            status=status,
            minutes=30,
            now_value=f"未入力（全項目0円）／ {units_note}",
            why=(
                "出荷数が増えてきたので、代行に任せたほうが得か計算できる"
                if enough else
                f"出荷が月{AGENCY_THRESHOLD_UNITS}個に届くまでは、月額費の1個あたり負担が重く"
                "て代行は割に合わない。いま調べても使う場面がない"
            ),
            trigger=f"出荷が月{AGENCY_THRESHOLD_UNITS}個を超えたら（いま {units}個）",
            how=[
                "ShopeeKing に次の6つを聞いて config/fulfillment.yaml に入れる:",
                "  1. 入庫料（1個あたり）",
                "  2. 梱包・ラベル貼付の代行料（1個あたり）",
                "  3. 資材費（1個あたり）",
                "  4. 代行倉庫からSLS倉庫までの国内送料（1個あたり）",
                "  5. 月額料金",
                "  6. こちらから代行倉庫へ送るときの送料の目安（1回あたり）",
                "入れたら verified: true にして、",
                "  python3 scripts/compare_fulfillment.py --sku <SKU> で損得を確認する",
            ],
        ))

    # ---------------- 店名 ----------------
    items.append(Item(
        key="shop_name",
        title="ショップ名",
        status=DONE if business.shop_name else NOW,
        minutes=1,
        now_value=business.shop_name or "未設定",
        why="レポートや出品文で使う。Shopeeの店名を決めたら入れる",
        how=["config/business.yaml の shop.name に入れる"],
    ))

    return items


def render(items: list[Item]) -> int:
    today = _dt.date.today().isoformat()
    print("=" * 66)
    print(f"  セットアップ健診  {today}")
    print("=" * 66)

    groups = [
        (NOW, "■ いま埋める"),
        (LATER, "■ 条件が来たら埋める（いまはやらなくていい）"),
        (DONE, "■ 埋まっている"),
    ]

    now_items = [i for i in items if i.status == NOW]
    total_minutes = sum(i.minutes for i in now_items)

    for status, heading in groups:
        group = [i for i in items if i.status == status]
        if not group:
            continue
        suffix = ""
        if status == NOW:
            suffix = f"（{len(group)}件・合計 約{total_minutes}分）"
        print(f"\n{heading}{suffix}")
        print("-" * 66)

        for item in group:
            if status == DONE:
                print(f"  ✅ {item.title}")
                if item.now_value:
                    print(f"       {item.now_value}")
                continue

            mark = "□" if status == NOW else "⏸"
            head = f"  {mark} {item.title}"
            if item.minutes:
                head += f"    約{item.minutes}分"
            print(head)
            if item.now_value:
                print(f"       いま: {item.now_value}")
            if item.why:
                print(f"       理由: {item.why}")
            if status == LATER and item.trigger:
                print(f"       やる時期: {item.trigger}")
            if item.how:
                print("       手順:")
                for line in item.how:
                    print(f"         {line}")
            print()

    if now_items:
        print("\n次の一手")
        print("-" * 66)
        print(f"  上から順に、今日は「{now_items[0].title}」だけでも前に進みます。")
        print("  埋めたら、もう一度このスクリプトを流して確認してください。")
    else:
        print("\n  いま埋めるべき項目はありません。出品と発送に集中してください。")
    print()
    return 0


def main() -> int:
    return render(build_items())


if __name__ == "__main__":
    raise SystemExit(main())
