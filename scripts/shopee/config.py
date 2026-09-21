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
class Fulfillment:
    """誰が梱包して発送するか。自社発送か、発送代行か。

    1個あたりの費用を「入庫・梱包・資材・SLSまでの国内送料・納品送料の按分・
    月額固定費の按分」に分解して持つ。代行業者ごとに何が有料かが違うので、
    合計だけでなく内訳で持っておかないと比較ができない。
    """

    code: str
    name_ja: str
    inbound_jpy: float                  # 入庫料／個
    pick_pack_jpy: float                # 梱包・ピッキング代行料／個
    material_jpy: float                 # 梱包資材費／個
    domestic_to_sls_jpy: float          # SLS倉庫までの国内送料／個
    inbound_shipping_jpy_per_lot: float # 代行倉庫への納品送料／1回
    lot_size: int                       # 1回の納品個数（納品送料を按分する分母）
    monthly_fixed_jpy: float            # 月額固定費
    expected_monthly_units: int         # 月額固定費を按分する想定出荷数
    handling_days: int
    labor_minutes_per_order: float      # 1注文あたり自分が使う時間（分）
    updated_at: _dt.date | None = None
    note: str = ""

    @property
    def inbound_shipping_per_unit_jpy(self) -> float:
        """代行倉庫への納品送料を1個あたりに按分した額。"""
        if self.lot_size <= 0:
            return 0.0
        return self.inbound_shipping_jpy_per_lot / self.lot_size

    @property
    def monthly_fixed_per_unit_jpy(self) -> float:
        """月額固定費を1個あたりに按分した額。出荷数が少ないほど重くなる。"""
        if self.expected_monthly_units <= 0:
            return 0.0
        return self.monthly_fixed_jpy / self.expected_monthly_units

    def items(self) -> list[tuple[str, float]]:
        """内訳。0円の項目も残す（何が無料なのかが分かるほうが比較しやすい）。"""
        return [
            ("入庫料", self.inbound_jpy),
            ("梱包代行料", self.pick_pack_jpy),
            ("梱包資材", self.material_jpy),
            ("国内送料(SLSまで)", self.domestic_to_sls_jpy),
            ("倉庫への納品送料", self.inbound_shipping_per_unit_jpy),
            ("月額費の按分", self.monthly_fixed_per_unit_jpy),
        ]

    @property
    def per_unit_jpy(self) -> float:
        """1個あたりの合計費用。"""
        return sum(amount for _, amount in self.items())

    def cost_at_volume(self, monthly_units: int) -> float:
        """出荷数を仮定したときの1個あたり費用。

        月額固定費は出荷数で割るので、想定と実績がずれると1個あたりの重さが変わる。
        自社発送と代行の損益分岐点を出すのに使う。
        """
        variable = (
            self.inbound_jpy + self.pick_pack_jpy + self.material_jpy
            + self.domestic_to_sls_jpy + self.inbound_shipping_per_unit_jpy
        )
        if monthly_units <= 0:
            return variable
        return variable + self.monthly_fixed_jpy / monthly_units

    def looks_unpriced(self) -> bool:
        """代行なのに費用が1円も入っていない = まだ料金を入れていない。

        これを見落とすと「代行はタダなので最強」という誤った結論が出るので、
        値付けにも比較にも進ませない。
        """
        return (
            self.code not in ("self", "none")
            and self.per_unit_jpy == 0.0
            and self.monthly_fixed_jpy == 0.0
        )

    def is_stale(self, today: _dt.date | None = None, max_age_days: int = 180) -> bool:
        """料金表が古くないか。代行の料金改定を見落とすと計算が狂う。"""
        if self.updated_at is None:
            return True
        return ((today or _dt.date.today()) - self.updated_at).days > max_age_days

    @staticmethod
    def none() -> "Fulfillment":
        """費用ゼロの発送主体。テストや、発送費を別途見るときに使う。"""
        return Fulfillment(
            code="none", name_ja="(発送費なし)", inbound_jpy=0.0, pick_pack_jpy=0.0,
            material_jpy=0.0, domestic_to_sls_jpy=0.0, inbound_shipping_jpy_per_lot=0.0,
            lot_size=1, monthly_fixed_jpy=0.0, expected_monthly_units=1,
            handling_days=0, labor_minutes_per_order=0.0,
        )


def load_fulfillments(config_dir: Path | None = None) -> tuple[str, dict[str, Fulfillment]]:
    """発送主体の設定を読む。戻り値は (いま有効なコード, 全プロバイダ)。"""
    path = (config_dir or CONFIG_DIR) / "fulfillment.yaml"
    raw = _load_yaml(path)
    providers_raw = _require(raw, "providers", str(path))

    providers: dict[str, Fulfillment] = {}
    for code, p in providers_raw.items():
        where = f"{path}:providers.{code}"
        per_unit = p.get("per_unit") or {}
        updated = p.get("updated_at")
        providers[code] = Fulfillment(
            code=code,
            name_ja=p.get("name_ja", code),
            inbound_jpy=float(per_unit.get("inbound_jpy", 0.0)),
            pick_pack_jpy=float(per_unit.get("pick_pack_jpy", 0.0)),
            material_jpy=float(per_unit.get("material_jpy", 0.0)),
            domestic_to_sls_jpy=float(per_unit.get("domestic_to_sls_jpy", 0.0)),
            inbound_shipping_jpy_per_lot=float(p.get("inbound_shipping_jpy_per_lot", 0.0)),
            lot_size=int(p.get("lot_size", 1)),
            monthly_fixed_jpy=float(p.get("monthly_fixed_jpy", 0.0)),
            expected_monthly_units=int(p.get("expected_monthly_units", 1)),
            handling_days=int(p.get("handling_days", 2)),
            labor_minutes_per_order=float(p.get("labor_minutes_per_order", 0.0)),
            updated_at=_as_date(updated, f"{where}.updated_at") if updated else None,
            note=p.get("note", ""),
        )

    active = raw.get("active") or "self"
    if active not in providers:
        raise ConfigError(
            f"fulfillment.yaml の active '{active}' が providers にありません。"
            f" 使えるのは: {', '.join(sorted(providers))}"
        )
    return active, providers


def get_fulfillment(code: str | None = None, config_dir: Path | None = None) -> Fulfillment:
    """発送主体を1つ取り出す。code 省略時は active のもの。"""
    active, providers = load_fulfillments(config_dir)
    code = code or active
    if code not in providers:
        raise ConfigError(
            f"発送主体 '{code}' は config/fulfillment.yaml にありません。"
            f" 使えるのは: {', '.join(sorted(providers))}"
        )
    return providers[code]


@dataclass(frozen=True)
class Business:
    shop_name: str
    primary_market: str
    secondary_markets: list[str]
    logistics: str          # 越境の物流チャネル（SLS など）
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
    )
    if not 0 < policy.target_margin_rate < 1:
        raise ConfigError("business.yaml の target_margin_rate は 0〜1 の小数で書いてください（例: 0.25）")
    if policy.min_margin_rate > policy.target_margin_rate:
        raise ConfigError("business.yaml の min_margin_rate が target_margin_rate より大きくなっています。")

    return Business(
        shop_name=shop.get("name", ""),
        primary_market=_require(shop, "primary_market", f"{path}:shop"),
        secondary_markets=list(shop.get("secondary_markets") or []),
        logistics=shop.get("logistics", shop.get("fulfillment", "SLS")),
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
