#!/usr/bin/env python3
"""出品案(YAML) → 検証 → Shopee一括アップロード用CSV。

出品担当AIが data/products/drafts/*.yaml に出品案を書き、
このスクリプトが「文字数・禁止表現・必須項目」を機械チェックしてからCSVにする。

使い方:
    python3 scripts/build_listing_csv.py data/products/drafts/sample_tw.yaml
    python3 scripts/build_listing_csv.py data/products/drafts/sample_tw.yaml --check-only
    python3 scripts/build_listing_csv.py data/products/drafts/sample_tw.yaml -o data/products/upload.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shopee import config, listing  # noqa: E402


def load_drafts(path: Path) -> tuple[str, str, list[listing.ListingDraft]]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not data or "listings" not in data:
        raise SystemExit(f"{path} に 'listings' がありません。docs/運用マニュアル.md の書式を見てください。")

    market_code = data.get("market") or config.load_business().primary_market
    template = data.get("template", "default_mass_upload")
    market = config.get_market(market_code)

    drafts: list[listing.ListingDraft] = []
    for i, item in enumerate(data["listings"], start=1):
        missing = [k for k in ("sku", "title", "description", "price_local") if k not in item]
        if missing:
            raise SystemExit(f"{path} の {i}件目に必須項目がありません: {', '.join(missing)}")
        drafts.append(listing.ListingDraft(
            sku=str(item["sku"]),
            market_code=market_code,
            title=str(item["title"]),
            description=str(item["description"]),
            price_local=float(item["price_local"]),
            stock=int(item.get("stock", 0)),
            weight_g=float(item.get("weight_g", 0)),
            length_cm=float(item.get("length_cm", 0)),
            width_cm=float(item.get("width_cm", 0)),
            height_cm=float(item.get("height_cm", 0)),
            category_id=str(item.get("category_id", "")),
            brand=str(item.get("brand", "No Brand")),
            images=[str(u) for u in (item.get("images") or [])],
            variation_name=str(item.get("variation_name", "")),
            variation_option=str(item.get("variation_option", "")),
            language=str(item.get("language", market.listing_language)),
            name_ja=str(item.get("name_ja", "")),
        ))
    return market_code, template, drafts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="出品案の検証とCSV書き出し")
    parser.add_argument("draft_file", type=Path, help="出品案のYAMLファイル")
    parser.add_argument("-o", "--out", type=Path, help="出力先CSV（既定: data/products/upload_<市場>.csv）")
    parser.add_argument("--check-only", action="store_true", help="検証だけしてCSVは作らない")
    parser.add_argument("--force", action="store_true", help="エラーがあってもCSVを作る（非推奨）")
    parser.add_argument("--add-disclaimer", action="store_true",
                        help="必須の注意書きを説明文の末尾に自動で足す")
    args = parser.parse_args(argv)

    market_code, template, drafts = load_drafts(args.draft_file)
    market = config.get_market(market_code)
    compliance = config.load_compliance()

    total_errors = 0
    print(f"出品案: {args.draft_file}  /  市場: {market.name_ja}({market_code})  /  {len(drafts)}件\n")

    for draft in drafts:
        if args.add_disclaimer:
            listing.append_disclaimer(draft, compliance)
        issues = listing.validate(draft, market, compliance)
        errors = [i for i in issues if i.severity == listing.SEVERITY_ERROR]
        total_errors += len(errors)

        mark = "🛑" if errors else ("⚠️" if issues else "✅")
        print(f"{mark} {draft.sku}  {draft.title[:40]}")
        print(f"    タイトル {len(draft.title)}字 / 説明 {len(draft.description)}字 / "
              f"画像 {len(draft.images)}枚 / 価格 {draft.price_local:g} {market.currency}")
        for issue in issues:
            print(f"    {issue}")
        print()

    if total_errors:
        print(f"エラー {total_errors}件。直してから出品してください。")
        if not args.force:
            return 1
        print("--force が指定されたので、エラーのままCSVを書き出します。")

    if args.check_only:
        print("検証のみで終了しました（--check-only）。")
        return 0

    out = args.out or (listing.default_output_dir() / f"upload_{market_code}.csv")
    path = listing.write_listings_csv(drafts, out, template)
    print(f"📄 CSVを書き出しました: {path}")
    print("   Seller Centre > 商品管理 > 一括ツール からアップロードしてください。")
    print("   ※ 列名が合わずに弾かれたら config/csv_templates/ のテンプレを実物に合わせて直すこと。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
