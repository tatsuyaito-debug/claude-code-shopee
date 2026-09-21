"""利益計算のテスト。

ここが壊れると、赤字商品を「出品してOK」と言い出すので、
計算の芯の部分（手数料・送料・逆算・判定）は必ずテストで固定しておく。

実行: python3 -m pytest tests/ -q   または   python3 tests/test_pricing.py
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from shopee import config, listing, orders, pricing  # noqa: E402


def make_market(**overrides) -> config.Market:
    base = dict(
        code="TW", name_ja="台湾", currency="TWD", listing_language="zh-Hant", cs_language="zh-Hant",
        fx_jpy_per_unit=5.0, fx_updated_at=_dt.date.today(),
        fees=config.Fees(commission_rate=0.06, transaction_fee_rate=0.02,
                         service_fee_rate=0.0, fixed_fee_local=0.0),
        shipping=config.Shipping(base_jpy=200.0, per_kg_jpy=1000.0, max_weight_kg=20.0),
        psychological_ending=None, round_to=1.0, volumetric_divisor=6000.0,
        weight_step_g=100, fx_max_age_days=14,
    )
    base.update(overrides)
    return config.Market(**base)


def make_policy(**overrides) -> config.PricingPolicy:
    base = dict(
        target_margin_rate=0.25, min_margin_rate=0.10, min_profit_jpy=300.0,
        ads_rate=0.0, fx_buffer_rate=0.0, return_loss_rate=0.0,
        packaging_jpy=0.0, domestic_ship_jpy=0.0,
    )
    base.update(overrides)
    return config.PricingPolicy(**base)


# ---------------------------------------------------------------------------
# 重量と送料
# ---------------------------------------------------------------------------

def test_billable_weight_uses_volumetric_when_box_is_large():
    """軽くて大きい箱は、容積重量で課金される。"""
    market = make_market()
    # 30x20x20cm = 12000cm3 / 6000 = 2.0kg。実重量300gより重い
    product = pricing.Product(sku="X", name_ja="かさばる商品", cost_jpy=0, weight_g=300,
                              length_cm=30, width_cm=20, height_cm=20)
    assert pricing.volumetric_weight_kg(product, market) == 2.0
    assert pricing.billable_weight_kg(product, market) == 2.0


def test_billable_weight_rounds_up_to_step():
    """請求重量は刻み単位で切り上げられる（150g → 200g）。"""
    market = make_market()
    product = pricing.Product(sku="X", name_ja="小物", cost_jpy=0, weight_g=150)
    assert pricing.billable_weight_kg(product, market) == 0.2


def test_international_shipping_is_base_plus_per_kg():
    market = make_market()
    product = pricing.Product(sku="X", name_ja="小物", cost_jpy=0, weight_g=150)
    # 200 + 1000 * 0.2 = 400
    assert pricing.international_ship_jpy(product, market) == 400.0


# ---------------------------------------------------------------------------
# 価格の逆算
# ---------------------------------------------------------------------------

def test_price_for_margin_hits_the_target_margin():
    """逆算した価格で売ると、狙った利益率になる。"""
    market = make_market()
    policy = make_policy()
    product = pricing.Product(sku="X", name_ja="商品", cost_jpy=1000, weight_g=100)

    quote = pricing.price_for_margin(product, market, policy, 0.25, apply_ending=False)
    assert abs(quote.margin_rate - 0.25) < 1e-6


def test_rounding_never_drops_below_target_margin():
    """丸めは必ず切り上げ方向。丸めたせいで目標利益率を割ってはいけない。"""
    market = make_market(psychological_ending=9, round_to=1.0)
    policy = make_policy()
    product = pricing.Product(sku="X", name_ja="商品", cost_jpy=1234, weight_g=137)

    quote = pricing.price_for_margin(product, market, policy, 0.25)
    assert quote.price_local % 10 == 9
    assert quote.margin_rate >= 0.25


def test_breakeven_price_yields_zero_profit():
    market = make_market()
    policy = make_policy()
    product = pricing.Product(sku="X", name_ja="商品", cost_jpy=1000, weight_g=100)

    price = pricing.breakeven_price(product, market, policy)
    quote = pricing.quote_at_price(product, market, policy, price)
    # 売価は小数第2位に丸めるので、利益はちょうど0ではなく数銭ぶんずれる
    assert abs(quote.profit_jpy) < 0.1


def test_selling_below_breakeven_is_a_loss():
    market = make_market()
    policy = make_policy()
    product = pricing.Product(sku="X", name_ja="商品", cost_jpy=1000, weight_g=100)

    price = pricing.breakeven_price(product, market, policy)
    assert pricing.quote_at_price(product, market, policy, price * 0.9).profit_jpy < 0


def test_points_back_lowers_effective_cost():
    """ポイント還元は実質的な値引きとして原価から引く。"""
    product = pricing.Product(sku="X", name_ja="商品", cost_jpy=1000, weight_g=100,
                              points_back_rate=0.10)
    assert product.effective_cost_jpy(count_points=True) == 900.0
    assert product.effective_cost_jpy(count_points=False) == 1000.0


def test_fx_buffer_makes_pricing_conservative():
    """為替バッファを入れると、同じ利益率でも売価は高くなる（保守的になる）。"""
    market = make_market()
    product = pricing.Product(sku="X", name_ja="商品", cost_jpy=1000, weight_g=100)

    without = pricing.price_for_margin(product, market, make_policy(fx_buffer_rate=0.0),
                                       0.25, apply_ending=False).price_local
    with_buffer = pricing.price_for_margin(product, market, make_policy(fx_buffer_rate=0.05),
                                           0.25, apply_ending=False).price_local
    assert with_buffer > without


def test_impossible_margin_raises():
    """手数料＋広告費＋目標利益率が100%を超えたら、黙って変な値を返さずエラーにする。"""
    market = make_market()
    policy = make_policy(ads_rate=0.50)
    product = pricing.Product(sku="X", name_ja="商品", cost_jpy=1000, weight_g=100)
    try:
        pricing.price_for_margin(product, market, policy, 0.60)
    except ValueError as exc:
        assert "100%" in str(exc)
    else:
        raise AssertionError("ValueError が投げられませんでした")


# ---------------------------------------------------------------------------
# 価格の丸め
# ---------------------------------------------------------------------------

def test_apply_price_ending_rounds_up_to_nine():
    assert pricing.apply_price_ending(671.2, 1.0, 9) == 679.0
    assert pricing.apply_price_ending(679.0, 1.0, 9) == 679.0
    assert pricing.apply_price_ending(680.0, 1.0, 9) == 689.0


def test_apply_price_ending_with_decimal_step():
    assert pricing.apply_price_ending(12.34, 0.1, 9) == 12.9
    assert pricing.apply_price_ending(12.95, 0.1, 9) == 13.9


# ---------------------------------------------------------------------------
# 出品可否の判定
# ---------------------------------------------------------------------------

def _business(**pricing_overrides) -> config.Business:
    return config.Business(
        shop_name="テスト", primary_market="TW", secondary_markets=[], fulfillment="SLS",
        monthly_revenue_jpy=0, monthly_profit_jpy=0, daily_work_minutes=60,
        pricing=make_policy(**pricing_overrides),
        selection=config.SelectionPolicy(
            max_weight_g=800, max_longest_side_cm=40, min_sell_price_jpy=1500,
            avoid_fragile=True, avoid_battery=True, avoid_liquid=True,
        ),
        operations={}, sourcing={},
    )


def test_judge_rejects_thin_profit():
    market = make_market()
    business = _business(min_profit_jpy=1000.0)
    product = pricing.Product(sku="X", name_ja="薄利商品", cost_jpy=1000, weight_g=100)
    quote = pricing.price_for_margin(product, market, business.pricing, 0.25)

    verdict = pricing.judge(quote, business, market)
    assert not verdict.ok
    assert any("最低ライン" in r for r in verdict.reasons)


def test_judge_rejects_heavy_product():
    market = make_market()
    business = _business()
    product = pricing.Product(sku="X", name_ja="重い商品", cost_jpy=3000, weight_g=2000)
    quote = pricing.price_for_margin(product, market, business.pricing, 0.25)

    verdict = pricing.judge(quote, business, market)
    assert not verdict.ok
    assert any("重量" in r for r in verdict.reasons)


def test_judge_flags_stale_fx():
    """為替が古いまま値付けの判断をさせない。"""
    market = make_market(fx_updated_at=_dt.date.today() - _dt.timedelta(days=60))
    business = _business()
    product = pricing.Product(sku="X", name_ja="商品", cost_jpy=1000, weight_g=100)
    quote = pricing.price_for_margin(product, market, business.pricing, 0.25)

    assert any("為替" in r for r in pricing.judge(quote, business, market).reasons)


def test_rank_candidates_sorts_by_profit():
    market = make_market()
    business = _business()
    cheap = pricing.Product(sku="A", name_ja="安い", cost_jpy=300, weight_g=100)
    pricey = pricing.Product(sku="B", name_ja="高い", cost_jpy=3000, weight_g=100)

    ranked = pricing.rank_candidates([cheap, pricey], market, business)
    assert [q.product.sku for q in ranked] == ["B", "A"]


# ---------------------------------------------------------------------------
# 出品チェック
# ---------------------------------------------------------------------------

def _draft(**overrides) -> listing.ListingDraft:
    base = dict(
        sku="X", market_code="TW", title="日本直送 " + "純棉紗布圍兜 " * 3,
        description="あ" * 300, price_local=679, stock=10, weight_g=150,
        images=["1.jpg", "2.jpg", "3.jpg"], category_id="100017", language="zh-Hant",
    )
    base.update(overrides)
    return listing.ListingDraft(**base)


COMPLIANCE = {
    "forbidden_terms": {"ja": ["治る", "100%安全"], "zh-Hant": ["治療", "100%安全"]},
    "required_disclaimers": {"zh-Hant": "※ 本商品由日本直送，商品外包裝可能因運送而有輕微壓痕。"},
    "limits": {"title_max_chars": 120, "description_min_chars": 200, "images_min": 3, "images_max": 9},
}


def test_validate_passes_a_clean_draft():
    draft = _draft()
    listing.append_disclaimer(draft, COMPLIANCE)
    assert not listing.has_errors(listing.validate(draft, compliance=COMPLIANCE))


def test_validate_catches_long_title():
    issues = listing.validate(_draft(title="あ" * 200), compliance=COMPLIANCE)
    assert listing.has_errors(issues)
    assert any("上限" in i.message for i in issues)


def test_validate_catches_forbidden_claims():
    issues = listing.validate(_draft(description="本產品可以治療濕疹。" + "あ" * 300),
                              compliance=COMPLIANCE)
    assert any(i.field_name == "claims" for i in issues)


def test_forbidden_term_reported_once_even_if_in_two_languages():
    """同じ語が複数言語のリストに載っていても、指摘は1回にまとめる。"""
    issues = listing.check_forbidden_terms(
        _draft(description="100%安全です" + "あ" * 300), COMPLIANCE
    )
    assert len(issues) == 1


def test_validate_requires_enough_images():
    issues = listing.validate(_draft(images=["1.jpg"]), compliance=COMPLIANCE)
    assert listing.has_errors(issues)
    assert any(i.field_name == "images" for i in issues)


def test_append_disclaimer_is_idempotent():
    draft = _draft()
    listing.append_disclaimer(draft, COMPLIANCE)
    once = draft.description
    listing.append_disclaimer(draft, COMPLIANCE)
    assert draft.description == once


# ---------------------------------------------------------------------------
# 注文CSV
# ---------------------------------------------------------------------------

def test_order_date_normalization():
    assert orders._normalize_date("2026/03/01 10:22") == "2026-03-01"
    assert orders._normalize_date("2026-03-01") == "2026-03-01"
    assert orders._normalize_date("") == ""


def test_order_amount_parsing_strips_currency():
    assert orders._to_float("NT$1,408") == 1408.0
    assert orders._to_float("RM 35.50") == 35.5
    assert orders._to_float("") == 0.0


def test_cancelled_orders_are_excluded():
    cancelled = orders.Order(order_sn="1", order_date="2026-03-01", status="已取消", sku="X",
                             product_name="p", quantity=1, price_local=100,
                             buyer_paid_local=100, shipping_fee_local=0)
    completed = orders.Order(order_sn="2", order_date="2026-03-01", status="Completed", sku="X",
                             product_name="p", quantity=1, price_local=100,
                             buyer_paid_local=100, shipping_fee_local=0)
    assert not orders.is_completed(cancelled)
    assert orders.is_completed(completed)


# ---------------------------------------------------------------------------
# 設定ファイルが壊れていないか
# ---------------------------------------------------------------------------

def test_real_config_files_load():
    markets = config.load_markets()
    business = config.load_business()
    assert business.primary_market in markets
    for code in business.secondary_markets:
        assert code in markets, f"secondary_markets の {code} が markets.yaml にありません"


def test_real_compliance_has_required_sections():
    comp = config.load_compliance()
    for key in ("prohibited", "restricted", "forbidden_terms", "required_disclaimers", "limits"):
        assert key in comp, f"compliance.yaml に {key} がありません"


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                passed += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"❌ {name}: {exc}")
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
