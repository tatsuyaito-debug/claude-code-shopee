"""利益計算と推奨売価の算出。

このモジュールが店の生命線。「いくらで売れば手元にいくら残るか」を
一箇所で定義し、値付け・商品選定・週次レポートがすべてこれを使う。

考え方（1個あたり、すべて日本円換算で考える）:

    売上(JPY)  = 現地価格 × 為替 × (1 - 為替バッファ)
    率でかかる費用 = 売上 × (Shopee手数料率 + 広告費率 + 返品ロス率)
    固定でかかる費用 = Shopee固定手数料
    原価      = 仕入れ値(ポイント還元差引後) + 国内送料 + 梱包費 + 国際送料
    利益      = 売上 - 率費用 - 固定費用 - 原価

目標利益率 m を満たす売上は、上の式を解くと

    売上 = (原価 + 固定費用) / (1 - 率合計 - m)

で一発で求まる。ここから逆算して現地通貨の売価を出している。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Iterable

from .config import Business, Fulfillment, Market, PricingPolicy


@dataclass(frozen=True)
class Product:
    """仕入れ候補 / 出品中の商品1点。"""

    sku: str
    name_ja: str
    cost_jpy: float                 # 仕入れ値（税込）
    weight_g: float                 # 梱包後の実重量
    length_cm: float = 0.0
    width_cm: float = 0.0
    height_cm: float = 0.0
    points_back_rate: float = 0.0   # 仕入れ時のポイント還元率（0.10 = 10%）
    url: str = ""                   # 仕入れ元URL（メモ用）
    note: str = ""

    @property
    def longest_side_cm(self) -> float:
        return max(self.length_cm, self.width_cm, self.height_cm)

    def effective_cost_jpy(self, count_points: bool = True) -> float:
        """ポイント還元を差し引いた実質仕入れ値。"""
        if count_points and self.points_back_rate:
            return self.cost_jpy * (1.0 - self.points_back_rate)
        return self.cost_jpy


@dataclass(frozen=True)
class CostBreakdown:
    """1個売るのにかかる、売上に比例しない費用。"""

    effective_cost_jpy: float
    fulfillment_jpy: float                    # 梱包・発送にかかる費用（自社でも代行でも）
    international_ship_jpy: float
    billable_weight_kg: float
    volumetric_weight_kg: float
    fulfillment_name: str = ""
    fulfillment_items: tuple[tuple[str, float], ...] = ()   # 表示用の内訳

    @property
    def landed_cost_jpy(self) -> float:
        return (
            self.effective_cost_jpy
            + self.fulfillment_jpy
            + self.international_ship_jpy
        )


@dataclass(frozen=True)
class Quote:
    """ある売価で売ったときの損益。"""

    product: Product
    market: Market
    price_local: float
    revenue_jpy: float
    costs: CostBreakdown
    fee_jpy: float          # Shopee手数料（率＋固定）
    ads_jpy: float
    return_loss_jpy: float
    profit_jpy: float

    @property
    def margin_rate(self) -> float:
        if self.revenue_jpy <= 0:
            return 0.0
        return self.profit_jpy / self.revenue_jpy

    @property
    def markup_rate(self) -> float:
        """原価に対して何倍で売っているか（仕入れ判断の感覚用）。"""
        if self.costs.landed_cost_jpy <= 0:
            return 0.0
        return self.revenue_jpy / self.costs.landed_cost_jpy


@dataclass(frozen=True)
class Verdict:
    """出品してよいかの判定結果。"""

    ok: bool
    reasons: list[str]

    def __bool__(self) -> bool:  # if verdict: と書けるように
        return self.ok


# ---------------------------------------------------------------------------
# 重量・送料
# ---------------------------------------------------------------------------

def volumetric_weight_kg(product: Product, market: Market) -> float:
    """容積重量(kg)。箱が大きいと実重量より重い扱いで課金される。"""
    vol = product.length_cm * product.width_cm * product.height_cm
    if vol <= 0:
        return 0.0
    return vol / market.volumetric_divisor


def billable_weight_kg(product: Product, market: Market) -> float:
    """請求重量(kg)。実重量と容積重量の大きい方を、刻み単位で切り上げる。"""
    actual_kg = product.weight_g / 1000.0
    charged = max(actual_kg, volumetric_weight_kg(product, market))
    step_kg = market.weight_step_g / 1000.0
    if step_kg <= 0:
        return charged
    return math.ceil(charged / step_kg) * step_kg


def international_ship_jpy(product: Product, market: Market) -> float:
    return market.shipping.base_jpy + market.shipping.per_kg_jpy * billable_weight_kg(product, market)


def cost_breakdown(product: Product, market: Market, policy: PricingPolicy,
                   count_points: bool = True,
                   fulfillment: Fulfillment | None = None) -> CostBreakdown:
    ff = fulfillment or Fulfillment.none()
    return CostBreakdown(
        effective_cost_jpy=product.effective_cost_jpy(count_points),
        fulfillment_jpy=ff.per_unit_jpy,
        international_ship_jpy=international_ship_jpy(product, market),
        billable_weight_kg=billable_weight_kg(product, market),
        volumetric_weight_kg=volumetric_weight_kg(product, market),
        fulfillment_name=ff.name_ja,
        fulfillment_items=tuple(ff.items()),
    )


# ---------------------------------------------------------------------------
# 価格の丸め
# ---------------------------------------------------------------------------

def apply_price_ending(price: float, round_to: float, ending: int | None) -> float:
    """売価を「切り上げ」方向で見栄えのする数字に整える。

    切り上げにしているのは、丸めで利益率が目標を下回らないようにするため。
    """
    if round_to <= 0:
        round_to = 1.0
    stepped = math.ceil(price / round_to - 1e-9) * round_to

    if ending is None:
        return round(stepped, 2)

    if round_to >= 1:
        n = int(math.ceil(stepped - 1e-9))
        while n % 10 != ending % 10:
            n += 1
        return float(n)

    # 0.1 刻みなど小数価格の場合は、小数第1位を ending に寄せる
    whole = math.floor(stepped)
    target = whole + ending / 10.0
    if target < stepped - 1e-9:
        target = whole + 1 + ending / 10.0
    return round(target, 2)


# ---------------------------------------------------------------------------
# 価格算出
# ---------------------------------------------------------------------------

def effective_fx(market: Market, policy: PricingPolicy) -> float:
    """為替バッファを織り込んだ、1現地通貨あたりの円。

    為替が不利に振れても赤字にならないよう、売上を保守的に見積もる。
    """
    return market.fx_jpy_per_unit * (1.0 - policy.fx_buffer_rate)


def variable_rate(market: Market, policy: PricingPolicy) -> float:
    """売上に比例して消える率の合計。"""
    return market.fees.total_rate + policy.ads_rate + policy.return_loss_rate


def quote_at_price(product: Product, market: Market, policy: PricingPolicy,
                   price_local: float, count_points: bool = True,
                   fulfillment: Fulfillment | None = None) -> Quote:
    """現地価格を決め打ちしたときの損益を出す（競合価格に合わせる時に使う）。"""
    fx = effective_fx(market, policy)
    revenue_jpy = price_local * fx
    costs = cost_breakdown(product, market, policy, count_points, fulfillment)

    fee_jpy = revenue_jpy * market.fees.total_rate + market.fees.fixed_fee_local * fx
    ads_jpy = revenue_jpy * policy.ads_rate
    return_loss_jpy = revenue_jpy * policy.return_loss_rate
    profit_jpy = revenue_jpy - fee_jpy - ads_jpy - return_loss_jpy - costs.landed_cost_jpy

    return Quote(
        product=product,
        market=market,
        price_local=price_local,
        revenue_jpy=revenue_jpy,
        costs=costs,
        fee_jpy=fee_jpy,
        ads_jpy=ads_jpy,
        return_loss_jpy=return_loss_jpy,
        profit_jpy=profit_jpy,
    )


def price_for_margin(product: Product, market: Market, policy: PricingPolicy,
                     margin_rate: float, count_points: bool = True,
                     apply_ending: bool = True,
                     fulfillment: Fulfillment | None = None) -> Quote:
    """目標利益率を満たす現地売価を逆算する。"""
    rate = variable_rate(market, policy)
    denom = 1.0 - rate - margin_rate
    if denom <= 0:
        raise ValueError(
            "手数料率・広告費率・目標利益率の合計が100%を超えています。"
            f"（率合計={rate:.1%}, 目標利益率={margin_rate:.1%}）"
            " config/business.yaml の target_margin_rate か ads_rate を下げてください。"
        )

    fx = effective_fx(market, policy)
    costs = cost_breakdown(product, market, policy, count_points, fulfillment)
    required_revenue_jpy = (costs.landed_cost_jpy + market.fees.fixed_fee_local * fx) / denom
    raw_price = required_revenue_jpy / fx

    price = apply_price_ending(raw_price, market.round_to, market.psychological_ending) \
        if apply_ending else round(raw_price, 2)
    return quote_at_price(product, market, policy, price, count_points, fulfillment)


def breakeven_price(product: Product, market: Market, policy: PricingPolicy,
                    count_points: bool = True,
                    fulfillment: Fulfillment | None = None) -> float:
    """利益ゼロになる現地価格。これ以下は必ず赤字。"""
    return price_for_margin(
        product, market, policy, margin_rate=0.0, count_points=count_points,
        apply_ending=False, fulfillment=fulfillment
    ).price_local


def recommend(product: Product, market: Market, business: Business,
              competitor_price_local: float | None = None,
              fulfillment: Fulfillment | None = None) -> dict[str, object]:
    """AI社員が値付けするときの標準フロー。

    目標価格・損益分岐・（あれば）競合価格に合わせた場合の損益をまとめて返す。
    """
    policy = business.pricing
    count_points = bool((business.sourcing or {}).get("count_points_as_discount", True))

    target = price_for_margin(product, market, policy, policy.target_margin_rate,
                              count_points, fulfillment=fulfillment)
    floor = price_for_margin(product, market, policy, policy.min_margin_rate,
                             count_points, fulfillment=fulfillment)
    result: dict[str, object] = {
        "target": target,
        "floor": floor,                                   # これ以上は下げない下限価格
        "breakeven_price_local": breakeven_price(product, market, policy, count_points, fulfillment),
        "verdict": judge(target, business, market),
    }
    if competitor_price_local is not None:
        matched = quote_at_price(product, market, policy, competitor_price_local,
                                 count_points, fulfillment)
        result["competitor_match"] = matched
        result["competitor_verdict"] = judge(matched, business, market)
    return result


def judge(quote: Quote, business: Business, market: Market) -> Verdict:
    """この条件で出品してよいかを、事業方針に照らして判定する。"""
    reasons: list[str] = []
    policy = business.pricing
    sel = business.selection
    p = quote.product

    if quote.profit_jpy < policy.min_profit_jpy:
        reasons.append(
            f"1個あたり利益 {quote.profit_jpy:,.0f}円 が最低ライン {policy.min_profit_jpy:,.0f}円 未満"
        )
    if quote.margin_rate < policy.min_margin_rate:
        reasons.append(
            f"利益率 {quote.margin_rate:.1%} が最低ライン {policy.min_margin_rate:.1%} 未満"
        )
    if p.weight_g > sel.max_weight_g:
        reasons.append(f"重量 {p.weight_g:,.0f}g が上限 {sel.max_weight_g:,.0f}g 超（送料負けしやすい）")
    if p.longest_side_cm > sel.max_longest_side_cm:
        reasons.append(
            f"最長辺 {p.longest_side_cm:,.1f}cm が上限 {sel.max_longest_side_cm:,.1f}cm 超（容積重量で不利）"
        )
    if quote.costs.billable_weight_kg > market.shipping.max_weight_kg:
        reasons.append(
            f"請求重量 {quote.costs.billable_weight_kg:.2f}kg が {market.name_ja} の上限 "
            f"{market.shipping.max_weight_kg:.1f}kg 超（SLSで送れない）"
        )
    if quote.revenue_jpy < sel.min_sell_price_jpy:
        reasons.append(
            f"売価が円換算 {quote.revenue_jpy:,.0f}円 で、下限 {sel.min_sell_price_jpy:,.0f}円 未満（低単価は割に合わない）"
        )
    if market.fx_is_stale():
        reasons.append(
            f"為替レートが {market.fx_age_days()}日前のまま。config/markets.yaml の fx を更新してから判断すること"
        )

    return Verdict(ok=not reasons, reasons=reasons)


def rank_candidates(products: Iterable[Product], market: Market, business: Business,
                    fulfillment: Fulfillment | None = None) -> list[Quote]:
    """候補商品を「1個あたり利益が大きい順」に並べる。

    利益率ではなく利益額で並べるのは、作業時間が有限で
    1件の発送にかかる手間がほぼ一定だから（率が高くても数十円では意味がない）。
    """
    quotes = [
        price_for_margin(p, market, business.pricing, business.pricing.target_margin_rate,
                         fulfillment=fulfillment)
        for p in products
    ]
    return sorted(quotes, key=lambda q: q.profit_jpy, reverse=True)


def with_cost(product: Product, cost_jpy: float) -> Product:
    """仕入れ値だけ差し替えた複製（値引き交渉やセール価格の検討用）。"""
    return replace(product, cost_jpy=cost_jpy)
