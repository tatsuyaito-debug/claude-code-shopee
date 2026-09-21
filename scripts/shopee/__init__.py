"""Shopee 越境ベビーグッズ販売の運用ライブラリ。

CSV 運用を前提に動くが、Shopee Open Platform API へ差し替えられるよう
データ取得・反映は backend.py のインターフェース越しに行う。
"""

__all__ = ["config", "pricing", "listing", "orders", "kpi", "backend"]
