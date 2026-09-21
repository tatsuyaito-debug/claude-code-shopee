"""注文CSVの読み込み。

Shopee の注文CSVは国・時期で列名が変わるので、
config/csv_templates/order_export.yaml の別名リストを引いて吸収する。
"""

from __future__ import annotations

import csv
import datetime as _dt
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import CONFIG_DIR, REPO_ROOT

DEFAULT_ORDERS_DIR = REPO_ROOT / "data" / "orders"


class OrderParseError(Exception):
    pass


@dataclass(frozen=True)
class Order:
    order_sn: str
    order_date: str
    status: str
    sku: str
    product_name: str
    quantity: int
    price_local: float
    buyer_paid_local: float
    shipping_fee_local: float
    variation: str = ""
    buyer: str = ""
    market_code: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def gross_local(self) -> float:
        """商品代金の合計（送料を除く）。"""
        return self.price_local * self.quantity


def _mapping() -> dict[str, Any]:
    path = CONFIG_DIR / "csv_templates" / "order_export.yaml"
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _pick(row: dict[str, str], aliases: list[str]) -> str:
    for name in aliases:
        if name in row and (row[name] or "").strip():
            return row[name].strip()
    # 大文字小文字・前後空白の揺れを吸収して再挑戦
    normalized = {(k or "").strip().lower(): v for k, v in row.items()}
    for name in aliases:
        v = normalized.get(name.strip().lower())
        if v and str(v).strip():
            return str(v).strip()
    return ""


def _to_float(value: str) -> float:
    if not value:
        return 0.0
    cleaned = value.replace(",", "").replace("NT$", "").replace("$", "").replace("RM", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _to_int(value: str) -> int:
    return int(_to_float(value))


def _normalize_date(value: str) -> str:
    """'2026-03-01 12:34' や '2026/03/01' を 'YYYY-MM-DD' に揃える。"""
    if not value:
        return ""
    head = value.replace("/", "-").split(" ")[0].split("T")[0]
    try:
        return _dt.date.fromisoformat(head).isoformat()
    except ValueError:
        return head


def load_orders(path: Path, market_code: str = "") -> list[Order]:
    """注文CSV 1ファイルを読む。"""
    if not path.exists():
        raise OrderParseError(f"注文CSVが見つかりません: {path}")

    mapping = _mapping()
    aliases = mapping["aliases"]
    orders: list[Order] = []

    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise OrderParseError(f"注文CSVにヘッダー行がありません: {path}")
        for row in reader:
            order_sn = _pick(row, aliases["order_sn"])
            if not order_sn:
                continue
            orders.append(Order(
                order_sn=order_sn,
                order_date=_normalize_date(_pick(row, aliases["order_date"])),
                status=_pick(row, aliases["status"]),
                sku=_pick(row, aliases["sku"]),
                product_name=_pick(row, aliases["product_name"]),
                variation=_pick(row, aliases.get("variation", [])),
                quantity=_to_int(_pick(row, aliases["quantity"])) or 1,
                price_local=_to_float(_pick(row, aliases["price_local"])),
                buyer_paid_local=_to_float(_pick(row, aliases["buyer_paid_local"])),
                shipping_fee_local=_to_float(_pick(row, aliases.get("shipping_fee_local", []))),
                buyer=_pick(row, aliases.get("buyer", [])),
                market_code=market_code or _market_from_filename(path),
            ))

    if not orders:
        raise OrderParseError(
            f"{path} から注文を1件も読めませんでした。\n"
            "列名が config/csv_templates/order_export.yaml の aliases と合っているか確認してください。\n"
            f"このCSVの列名: {', '.join(reader.fieldnames or [])}"
        )
    return orders


def _market_from_filename(path: Path) -> str:
    """'orders_TW_2026-03.csv' のようなファイル名から市場コードを推測する。"""
    for token in path.stem.replace("-", "_").split("_"):
        if len(token) == 2 and token.isalpha():
            return token.upper()
    return ""


def load_all_orders(orders_dir: Path | None = None) -> list[Order]:
    orders_dir = orders_dir or DEFAULT_ORDERS_DIR
    rows: list[Order] = []
    for path in sorted(orders_dir.glob("*.csv")):
        rows.extend(load_orders(path))
    return rows


def is_completed(order: Order) -> bool:
    m = _mapping()
    if order.status in set(m.get("cancelled_statuses") or []):
        return False
    completed = set(m.get("completed_statuses") or [])
    # ステータス欄が空のCSVもあるので、その場合は「売れた」とみなす
    return not order.status or order.status in completed


def filter_period(orders: list[Order], start: str | None = None, end: str | None = None) -> list[Order]:
    out = orders
    if start:
        out = [o for o in out if o.order_date >= start]
    if end:
        out = [o for o in out if o.order_date <= end]
    return out
