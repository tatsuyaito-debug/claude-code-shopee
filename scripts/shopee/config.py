"""config/*.yaml の読み込みと検証。

設定は「奥さんが直接編集する唯一の場所」という位置づけなので、
読み込み時点でおかしな値を見つけて日本語で知らせることを重視している。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


class ConfigError(Exception):
    """設定ファイルの内容がおかしいときに投げる。"""


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"設定ファイルが見つかりません: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError(f"設定ファイルの形式が不正です（辞書ではありません）: {path}")
    return data


@dataclass(frozen=True)
class Fees:
    commission_rate: float
    transaction_fee_rate: float
    service_fee_rate: float
    fixed_fee_local: float

    @property
    def total_rate(self) -> float:
        """販売額に対して率でかかる手数料の合計。"""
        return self.commission_rate + self.transaction_fee_rate + self.service_fee_rate


@dataclass(frozen=True)
class Shipping:
    base_jpy: float
    per_kg_jpy: float
    max_weight_kg: float


@dataclass(frozen=True)
class Market:
    code: str
    name_ja: str
    currency: str
    listing_language: str
    cs_language: str
    fx_jpy_per_unit: float
    fx_updated_at: _dt.date
    fees: Fees
    shipping: Shipping
    psychological_ending: int | None
    round_to: float
    notes: str = ""
    volumetric_divisor: float = 6000.0
    weight_step_g: int = 100
    fx_max_age_days: int = 14

    def fx_age_days(self, today: _dt.date | None = None) -> int:
        today = today or _dt.date.today()
        return (today - self.fx_updated_at).days

    def fx_is_stale(self, today: _dt.date | None = None) -> bool:
        return self.fx_age_days(today) > self.fx_max_age_days


@dataclass(frozen=True)
class PricingPolicy:
    target_margin_rate: float
    min_margin_rate: float
    min_profit_jpy: float
    ads_rate: float
    fx_buffer_rate: float
    return_loss_rate: float
    packaging_jpy: float
    domestic_ship_jpy: float


@dataclass(frozen=True)
class SelectionPolicy:
    max_weight_g: float
    max_longest_side_cm: float
    min_sell_price_jpy: float
    avoid_fragile: bool
    avoid_battery: bool
    avoid_liquid: bool
    prefer_keywords_ja: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Business:
    shop_name: str
    primary_market: str
    secondary_markets: list[str]
    fulfillment: str
    monthly_revenue_jpy: float
    monthly_profit_jpy: float
    daily_work_minutes: int
    pricing: PricingPolicy
    selection: SelectionPolicy
    operations: dict[str, Any]
    sourcing: dict[str, Any]


def _require(d: dict[str, Any], key: str, where: str) -> Any:
    if key not in d:
        raise ConfigError(f"{where} に '{key}' がありません。config/ の該当ファイルを確認してください。")
    return d[key]


def _as_date(value: Any, where: str) -> _dt.date:
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        try:
            return _dt.date.fromisoformat(value)
        except ValueError as exc:  # pragma: no cover - メッセージ整形のみ
            raise ConfigError(f"{where} の日付が読めません: {value!r} (YYYY-MM-DD で書いてください)") from exc
    raise ConfigError(f"{where} の日付が読めません: {value!r}")


def load_markets(config_dir: Path | None = None) -> dict[str, Market]:
    path = (config_dir or CONFIG_DIR) / "markets.yaml"
    raw = _load_yaml(path)
    defaults = raw.get("defaults") or {}
    markets_raw = _require(raw, "markets", str(path))

    markets: dict[str, Market] = {}
    for code, m in markets_raw.items():
        where = f"{path}:markets.{code}"
        fees_raw = _require(m, "fees", where)
        ship_raw = _require(m, "shipping", where)
        fx_raw = _require(m, "fx", where)
        pricing_raw = m.get("pricing") or {}

        fx_rate = float(_require(fx_raw, "jpy_per_unit", f"{where}.fx"))
        if fx_rate <= 0:
            raise ConfigError(f"{where}.fx.jpy_per_unit は正の数にしてください（今: {fx_rate}）")

        markets[code] = Market(
            code=code,
            name_ja=m.get("name_ja", code),
            currency=_require(m, "currency", where),
            listing_language=m.get("listing_language", "en"),
            cs_language=m.get("cs_language", m.get("listing_language", "en")),
            fx_jpy_per_unit=fx_rate,
            fx_updated_at=_as_date(_require(fx_raw, "updated_at", f"{where}.fx"), f"{where}.fx.updated_at"),
            fees=Fees(
                commission_rate=float(fees_raw.get("commission_rate", 0.0)),
                transaction_fee_rate=float(fees_raw.get("transaction_fee_rate", 0.0)),
                service_fee_rate=float(fees_raw.get("service_fee_rate", 0.0)),
                fixed_fee_local=float(fees_raw.get("fixed_fee_local", 0.0)),
            ),
            shipping=Shipping(
                base_jpy=float(ship_raw.get("base_jpy", 0.0)),
                per_kg_jpy=float(_require(ship_raw, "per_kg_jpy", f"{where}.shipping")),
                max_weight_kg=float(ship_raw.get("max_weight_kg", 20.0)),
            ),
            psychological_ending=pricing_raw.get("psychological_ending"),
            round_to=float(pricing_raw.get("round_to", 1)),
            notes=m.get("notes", ""),
            volumetric_divisor=float(defaults.get("volumetric_divisor", 6000)),
            weight_step_g=int(defaults.get("weight_step_g", 100)),
            fx_max_age_days=int(defaults.get("fx_max_age_days", 14)),
        )

    if not markets:
        raise ConfigError(f"{path} に market が1つも定義されていません。")
    return markets


def load_business(config_dir: Path | None = None) -> Business:
    path = (config_dir or CONFIG_DIR) / "business.yaml"
    raw = _load_yaml(path)
    shop = _require(raw, "shop", str(path))
    goals = _require(raw, "goals", str(path))
    pricing = _require(raw, "pricing", str(path))
    selection = _require(raw, "selection", str(path))

    policy = PricingPolicy(
        target_margin_rate=float(pricing.get("target_margin_rate", 0.25)),
        min_margin_rate=float(pricing.get("min_margin_rate", 0.10)),
        min_profit_jpy=float(pricing.get("min_profit_jpy", 0.0)),
        ads_rate=float(pricing.get("ads_rate", 0.0)),
        fx_buffer_rate=float(pricing.get("fx_buffer_rate", 0.0)),
        return_loss_rate=float(pricing.get("return_loss_rate", 0.0)),
        packaging_jpy=float(pricing.get("packaging_jpy", 0.0)),
        domestic_ship_jpy=float(pricing.get("domestic_ship_jpy", 0.0)),
    )
    if not 0 < policy.target_margin_rate < 1:
        raise ConfigError("business.yaml の target_margin_rate は 0〜1 の小数で書いてください（例: 0.25）")
    if policy.min_margin_rate > policy.target_margin_rate:
        raise ConfigError("business.yaml の min_margin_rate が target_margin_rate より大きくなっています。")

    return Business(
        shop_name=shop.get("name", ""),
        primary_market=_require(shop, "primary_market", f"{path}:shop"),
        secondary_markets=list(shop.get("secondary_markets") or []),
        fulfillment=shop.get("fulfillment", "SLS"),
        monthly_revenue_jpy=float(goals.get("monthly_revenue_jpy", 0)),
        monthly_profit_jpy=float(goals.get("monthly_profit_jpy", 0)),
        daily_work_minutes=int(goals.get("daily_work_minutes", 60)),
        pricing=policy,
        selection=SelectionPolicy(
            max_weight_g=float(selection.get("max_weight_g", 1000)),
            max_longest_side_cm=float(selection.get("max_longest_side_cm", 50)),
            min_sell_price_jpy=float(selection.get("min_sell_price_jpy", 1000)),
            avoid_fragile=bool(selection.get("avoid_fragile", True)),
            avoid_battery=bool(selection.get("avoid_battery", True)),
            avoid_liquid=bool(selection.get("avoid_liquid", True)),
            prefer_keywords_ja=list(selection.get("prefer_keywords_ja") or []),
        ),
        operations=raw.get("operations") or {},
        sourcing=raw.get("sourcing") or {},
    )


def load_compliance(config_dir: Path | None = None) -> dict[str, Any]:
    return _load_yaml((config_dir or CONFIG_DIR) / "compliance.yaml")


def get_market(code: str, config_dir: Path | None = None) -> Market:
    markets = load_markets(config_dir)
    if code not in markets:
        raise ConfigError(
            f"市場コード '{code}' は config/markets.yaml にありません。"
            f" 使えるのは: {', '.join(sorted(markets))}"
        )
    return markets[code]
