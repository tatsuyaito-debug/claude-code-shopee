#!/usr/bin/env python3
"""仕入れ候補をまとめて判定して、利益額の大きい順に並べる。

リサーチは1商品ずつ会話で聞くと時間が溶ける。候補をCSVに10〜20行書いて
これを流せば、規制・越境適性・利益を一度に判定して順位を付ける。

    python3 scripts/screen_candidates.py
    python3 scripts/screen_candidates.py --top 5
    python3 scripts/screen_candidates.py data/research/candidates.csv -o data/research/結果.md

判定は3段階:
    ⛔ 仕入れない  … 禁止カテゴリ、または利益が出ない
    ⚠️ 要確認     … 規制の確認が要る、または条件がぎりぎり
    ✅ 仕入れてよい

※ 規制の判定はキーワード照合なので、取りこぼしも誤検出もある。
   ⛔ と ⚠️ は必ず人間が config/compliance.yaml を読んで確認すること。
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shopee import config, pricing  # noqa: E402

DEFAULT_SHEET = config.REPO_ROOT / "data" / "research" / "candidates.csv"

VERDICT_NG = "ng"
VERDICT_CHECK = "check"
VERDICT_OK = "ok"

MARKS = {VERDICT_NG: "⛔", VERDICT_CHECK: "⚠️", VERDICT_OK: "✅"}
LABELS = {VERDICT_NG: "仕入れない", VERDICT_CHECK: "要確認", VERDICT_OK: "仕入れてよい"}


@dataclass
class Candidate:
    name_ja: str
    product: pricing.Product
    supplier: str = ""
    images_provided: bool = False
    note: str = ""
    verdict: str = VERDICT_OK
    reasons: list[str] = field(default_factory=list)
    quote: pricing.Quote | None = None
    worst: pricing.Quote | None = None

    @property
    def profit(self) -> float:
        return self.quote.profit_jpy if self.quote else float("-inf")


def _num(row: dict[str, str], key: str, default: float = 0.0) -> float:
    raw = (row.get(key) or "").strip().replace(",", "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def load_candidates(path: Path) -> list[Candidate]:
    if not path.exists():
        raise SystemExit(
            f"候補シートがありません: {path}\n"
            "data/research/candidates.csv に候補を書いてから実行してください。"
        )
    out: list[Candidate] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=1):
            name = (row.get("name_ja") or "").strip()
            if not name or name.startswith("#"):
                continue
            out.append(Candidate(
                name_ja=name,
                product=pricing.Product(
                    sku=f"CAND-{i:03d}",
                    name_ja=name,
                    cost_jpy=_num(row, "cost_jpy"),
                    weight_g=_num(row, "weight_g"),
                    length_cm=_num(row, "length_cm"),
                    width_cm=_num(row, "width_cm"),
                    height_cm=_num(row, "height_cm"),
                    points_back_rate=_num(row, "points_back_rate"),
                    url=(row.get("url") or "").strip(),
                ),
                supplier=(row.get("supplier") or "").strip(),
                images_provided=(row.get("images_provided") or "").strip().lower()
                in ("yes", "y", "true", "1", "あり"),
                note=(row.get("note") or "").strip(),
            ))
    if not out:
        raise SystemExit(f"{path} に候補が1行もありません。")
    return out


def check_compliance(name: str, compliance: dict[str, Any]) -> tuple[str, list[str]]:
    """商品名を禁止・要確認カテゴリのキーワードと照合する。

    キーワード照合なので万能ではない。引っかかったものを人間に回すための一次ふるい。
    """
    verdict = VERDICT_OK
    reasons: list[str] = []
    haystack = name.lower()

    for entry in compliance.get("prohibited") or []:
        for kw in entry.get("keywords") or []:
            if str(kw).lower() in haystack:
                verdict = VERDICT_NG
                reasons.append(f"禁止カテゴリの疑い「{entry['label']}」… {entry['reason']}")
                break

    if verdict == VERDICT_NG:
        return verdict, reasons

    for entry in compliance.get("restricted") or []:
        for kw in entry.get("keywords") or []:
            if str(kw).lower() in haystack:
                verdict = VERDICT_CHECK
                reasons.append(
                    f"要確認カテゴリ「{entry['label']}」… 確認すること: {entry.get('check', entry['reason'])}"
                )
                break

    return verdict, reasons


def screen(candidates: list[Candidate], market, business, fulfillment,
           compliance: dict[str, Any]) -> list[Candidate]:
    for cand in candidates:
        verdict, reasons = check_compliance(cand.name_ja, compliance)
        cand.verdict, cand.reasons = verdict, list(reasons)

        if cand.product.cost_jpy <= 0 or cand.product.weight_g <= 0:
            cand.verdict = VERDICT_CHECK
            cand.reasons.append("仕入れ値か重量が未入力。埋めないと利益が計算できない")
            continue

        try:
            quote = pricing.price_for_margin(
                cand.product, market, business.pricing,
                business.pricing.target_margin_rate, fulfillment=fulfillment,
            )
        except ValueError as exc:
            cand.verdict = VERDICT_NG
            cand.reasons.append(str(exc))
            continue

        cand.quote = quote
        judged = pricing.judge(quote, business, market)
        if not judged.ok:
            cand.verdict = VERDICT_NG
            cand.reasons.extend(judged.reasons)

        # 暫定値が悪い方にぶれた場合も見る
        if not (market.fees.verified and market.shipping.verified):
            cand.worst = pricing.quote_at_price(
                cand.product, pricing.shock_market(market, 0.03, 1.40),
                business.pricing, quote.price_local, fulfillment=fulfillment,
            )
            if cand.verdict == VERDICT_OK and cand.worst.profit_jpy < business.pricing.min_profit_jpy:
                cand.verdict = VERDICT_CHECK
                cand.reasons.append(
                    f"暫定値が悪い方にぶれると利益 {cand.worst.profit_jpy:,.0f}円 まで落ちる"
                )

        if not cand.images_provided and cand.verdict == VERDICT_OK:
            cand.reasons.append("仕入れ先の画像提供なし → 自分で撮影が必要（カバー1枚だけでも可）")

    order = {VERDICT_OK: 0, VERDICT_CHECK: 1, VERDICT_NG: 2}
    return sorted(candidates, key=lambda c: (order[c.verdict], -c.profit))


def render(results: list[Candidate], market, business, top: int | None) -> str:
    cur = market.currency
    lines = [
        "# 仕入れ候補の判定結果",
        "",
        f"- 販売先: {market.name_ja}（{market.code}）",
        f"- 目標利益率: {business.pricing.target_margin_rate:.0%} / "
        f"1個あたり最低利益: {business.pricing.min_profit_jpy:,.0f}円",
        f"- 候補 {len(results)}件",
        "",
        "## 結果（仕入れてよいもの順・利益額の大きい順）",
        "",
        "| | 商品 | 仕入れ | 推奨売価 | 1個利益 | 利益率 | 重量 |",
        "|---|---|---|---|---|---|---|",
    ]
    for cand in results:
        q = cand.quote
        if q:
            row = (f"| {MARKS[cand.verdict]} | {cand.name_ja[:22]} | {cand.product.cost_jpy:,.0f}円 "
                   f"| {q.price_local:,.0f} {cur} | {q.profit_jpy:,.0f}円 "
                   f"| {q.margin_rate:.1%} | {cand.product.weight_g:,.0f}g |")
        else:
            row = (f"| {MARKS[cand.verdict]} | {cand.name_ja[:22]} | {cand.product.cost_jpy:,.0f}円 "
                   f"| — | — | — | {cand.product.weight_g:,.0f}g |")
        lines.append(row)
    lines.append("")

    ok = [c for c in results if c.verdict == VERDICT_OK]
    if top and ok:
        lines += [f"## まずこの{min(top, len(ok))}つから始める", ""]
        for i, cand in enumerate(ok[:top], start=1):
            q = cand.quote
            lines.append(f"{i}. **{cand.name_ja}**（{cand.supplier or '仕入れ先未記入'}）")
            if q:
                lines.append(f"   - 推奨売価 {q.price_local:,.0f} {cur} / 1個あたり利益 {q.profit_jpy:,.0f}円")
                if cand.worst:
                    lines.append(f"   - 暫定値が悪い方にぶれても {cand.worst.profit_jpy:,.0f}円 残る")
            lines.append(f"   - まず5個のテストロットで仕入れる（{cand.product.cost_jpy * 5:,.0f}円）")
            for reason in cand.reasons:
                lines.append(f"   - {reason}")
            lines.append("")

    flagged = [c for c in results if c.verdict != VERDICT_OK]
    if flagged:
        lines += ["## 見送り・要確認", ""]
        for cand in flagged:
            lines.append(f"### {MARKS[cand.verdict]} {cand.name_ja} — {LABELS[cand.verdict]}")
            for reason in cand.reasons:
                lines.append(f"- {reason}")
            lines.append("")

    lines += [
        "---",
        "",
        "> ⚠️ 規制の判定は商品名のキーワード照合です。取りこぼしも誤検出もあります。",
        "> ⛔ と ⚠️ は `config/compliance.yaml` を自分で読んで確認してください。",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="仕入れ候補のまとめ判定")
    parser.add_argument("sheet", nargs="?", type=Path, default=DEFAULT_SHEET,
                        help="候補CSV（既定: data/research/candidates.csv）")
    parser.add_argument("--market", help="市場コード（既定: primary_market）")
    parser.add_argument("--top", type=int, default=3, help="まず始める件数（既定: 3）")
    parser.add_argument("-o", "--out", type=Path, help="結果の書き出し先（Markdown）")
    args = parser.parse_args(argv)

    business = config.load_business()
    market = config.get_market(args.market or business.primary_market)
    fulfillment = config.get_fulfillment()
    compliance = config.load_compliance()

    results = screen(load_candidates(args.sheet), market, business, fulfillment, compliance)
    text = render(results, market, business, args.top)
    print(text)

    out = args.out or (args.sheet.parent / "判定結果.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"\n📄 書き出しました: {out}")

    ok = sum(1 for c in results if c.verdict == VERDICT_OK)
    print(f"   仕入れてよい {ok}件 / 要確認 {sum(1 for c in results if c.verdict == VERDICT_CHECK)}件 "
          f"/ 見送り {sum(1 for c in results if c.verdict == VERDICT_NG)}件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
