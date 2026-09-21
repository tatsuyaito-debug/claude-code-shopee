"""商品マスタ（data/products/products.csv）の読み書き。

仕入れ値・重量・サイズという「利益計算に必要な事実」を1箇所に置く。
ここが正しくないと値付けも週報も全部ずれるので、読み込み時に型と必須項目を確かめる。
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable

from .config import REPO_ROOT
from .pricing import Product

DEFAULT_PATH = REPO_ROOT / "data" / "products" / "products.csv"

HEADERS = [
    "sku", "name_ja", "cost_jpy", "weight_g", "length_cm", "width_cm", "height_cm",
    "points_back_rate", "stock", "market", "price_local", "url", "note",
]


class CatalogError(Exception):
    pass


def _num(row: dict[str, str], key: str, default: float = 0.0) -> float:
    raw = (row.get(key) or "").strip().replace(",", "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise CatalogError(
            f"商品マスタの {row.get('sku', '?')} 行、{key} の値 '{raw}' が数値として読めません"
        ) from exc


def load_products(path: Path | None = None) -> list[Product]:
    """商品マスタを Product のリストとして読む。"""
    path = path or DEFAULT_PATH
    if not path.exists():
        raise CatalogError(
            f"商品マスタが見つかりません: {path}\n"
            "scripts/init_catalog.py を実行するか、data/products/products.csv を作ってください。"
        )
    products: list[Product] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            sku = (row.get("sku") or "").strip()
            if not sku or sku.startswith("#"):
                continue
            products.append(Product(
                sku=sku,
                name_ja=(row.get("name_ja") or "").strip(),
                cost_jpy=_num(row, "cost_jpy"),
                weight_g=_num(row, "weight_g"),
                length_cm=_num(row, "length_cm"),
                width_cm=_num(row, "width_cm"),
                height_cm=_num(row, "height_cm"),
                points_back_rate=_num(row, "points_back_rate"),
                url=(row.get("url") or "").strip(),
                note=(row.get("note") or "").strip(),
            ))
    if not products:
        raise CatalogError(f"商品マスタに有効な行がありません: {path}")
    return products


def load_rows(path: Path | None = None) -> list[dict[str, str]]:
    """在庫数や現在価格など、Product に入れていない列も含めて生で読む。"""
    path = path or DEFAULT_PATH
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [r for r in csv.DictReader(fh) if (r.get("sku") or "").strip()]


def stock_map(path: Path | None = None) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in load_rows(path):
        raw = (row.get("stock") or "").strip()
        if raw:
            try:
                out[row["sku"].strip()] = int(float(raw))
            except ValueError:
                continue
    return out


def write_products(rows: Iterable[dict[str, Any]], path: Path | None = None) -> Path:
    path = path or DEFAULT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=HEADERS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path
