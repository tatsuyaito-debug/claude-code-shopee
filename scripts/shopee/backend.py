"""データの入口と出口を1枚かぶせるレイヤ。

いまは CSV 運用（Seller Centre から落とした注文CSV、一括アップロード用CSV）だけを
実装している。将来 Shopee Open Platform API のキーが取れたら ApiBackend を
実装して差し替えるだけで、上位のスクリプトとスキルは変更不要にしてある。

    backend = get_backend()            # 環境変数 SHOPEE_BACKEND で切り替え
    orders  = backend.fetch_orders(...)
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import REPO_ROOT


class Backend(ABC):
    """注文取得・出品反映の共通インターフェース。"""

    name: str = "base"

    @abstractmethod
    def fetch_orders(self, since: str | None = None) -> list[dict[str, Any]]:
        """注文データを辞書のリストで返す。"""

    @abstractmethod
    def push_listings(self, rows: Sequence[dict[str, Any]], dest: Path) -> Path:
        """出品データを反映する。CSV運用では「ファイルを書き出す」が反映にあたる。"""

    @abstractmethod
    def update_stock(self, updates: Sequence[dict[str, Any]], dest: Path) -> Path:
        """在庫を更新する。CSV運用では在庫更新用CSVを書き出す。"""


class CsvBackend(Backend):
    """Seller Centre の CSV を手で上げ下ろしする運用。API審査なしで今日から動く。"""

    name = "csv"

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or (REPO_ROOT / "data")

    def fetch_orders(self, since: str | None = None) -> list[dict[str, Any]]:
        from .orders import load_orders  # 循環importを避けるため遅延import

        orders_dir = self.data_dir / "orders"
        rows: list[dict[str, Any]] = []
        for path in sorted(orders_dir.glob("*.csv")):
            rows.extend(o.as_dict() for o in load_orders(path))
        if since:
            rows = [r for r in rows if str(r.get("order_date", "")) >= since]
        return rows

    def push_listings(self, rows: Sequence[dict[str, Any]], dest: Path) -> Path:
        from .listing import write_rows

        return write_rows(rows, dest)

    def update_stock(self, updates: Sequence[dict[str, Any]], dest: Path) -> Path:
        from .listing import write_rows

        return write_rows(updates, dest)


class ApiBackend(Backend):
    """Shopee Open Platform API 版（未実装）。

    実装するときに必要なもの:
      - パートナーID / パートナーKey（Shopee Open Platform でアプリ登録）
      - ショップの認可（OAuth でショップ側が許可 → access_token / refresh_token）
      - リクエスト署名（HMAC-SHA256: partner_id + path + timestamp + token + shop_id）
      - 呼ぶ主なエンドポイント:
          order.get_order_list / order.get_order_detail   注文取得
          product.add_item / product.update_item          出品・更新
          product.update_stock                            在庫更新
      - 認証情報は必ず環境変数か .env に置き、リポジトリにコミットしない。
    """

    name = "api"

    def __init__(self) -> None:
        raise NotImplementedError(
            "API連携はまだ未実装です。Shopee Open Platform でアプリ登録とショップ認可が済んだら、"
            "このクラスを実装してください。上位のスクリプトは変更不要です。"
        )

    def fetch_orders(self, since: str | None = None) -> list[dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def push_listings(self, rows: Sequence[dict[str, Any]], dest: Path) -> Path:  # pragma: no cover
        raise NotImplementedError

    def update_stock(self, updates: Sequence[dict[str, Any]], dest: Path) -> Path:  # pragma: no cover
        raise NotImplementedError


def get_backend(name: str | None = None) -> Backend:
    """SHOPEE_BACKEND 環境変数（既定: csv）でバックエンドを選ぶ。"""
    name = (name or os.environ.get("SHOPEE_BACKEND") or "csv").lower()
    if name == "csv":
        return CsvBackend()
    if name == "api":
        return ApiBackend()
    raise ValueError(f"未知のバックエンドです: {name!r}（使えるのは 'csv' か 'api'）")


def available_backends() -> Iterable[str]:
    return ("csv", "api")
