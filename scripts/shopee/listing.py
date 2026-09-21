"""出品データの組み立て・検証・CSV書き出し。

文章そのもの（繁体字のタイトルや説明文）を書くのは AI社員の仕事で、
このモジュールが受け持つのは「形式を守らせること」と「危ない表現を止めること」。
人間もAIも、勢いで書くと文字数超過や禁止表現をやらかすので機械で止める。
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

from .config import CONFIG_DIR, REPO_ROOT, Market, load_compliance

SEVERITY_ERROR = "error"      # 直すまで出品しない
SEVERITY_WARN = "warn"        # 出してもいいが、見てから決める


@dataclass
class ListingDraft:
    """1商品ぶんの出品案。"""

    sku: str
    market_code: str
    title: str
    description: str
    price_local: float
    stock: int
    weight_g: float
    length_cm: float = 0.0
    width_cm: float = 0.0
    height_cm: float = 0.0
    category_id: str = ""
    brand: str = "No Brand"
    images: list[str] = field(default_factory=list)
    variation_name: str = ""
    variation_option: str = ""
    language: str = "zh-Hant"
    name_ja: str = ""          # 自分用のメモ（CSVには出さない）

    def to_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "sku": self.sku,
            "category_id": self.category_id,
            "title": self.title,
            "description": self.description,
            "brand": self.brand,
            "variation_name": self.variation_name,
            "variation_option": self.variation_option,
            "price_local": f"{self.price_local:g}",
            "stock": str(self.stock),
            "weight_kg": f"{self.weight_g / 1000.0:g}",
            "length_cm": f"{self.length_cm:g}",
            "width_cm": f"{self.width_cm:g}",
            "height_cm": f"{self.height_cm:g}",
        }
        for i in range(1, 10):
            row[f"image_{i}"] = self.images[i - 1] if len(self.images) >= i else ""
        return row


@dataclass(frozen=True)
class Issue:
    severity: str
    field_name: str
    message: str

    def __str__(self) -> str:
        mark = "🛑" if self.severity == SEVERITY_ERROR else "⚠️"
        return f"{mark} [{self.field_name}] {self.message}"


# ---------------------------------------------------------------------------
# 検証
# ---------------------------------------------------------------------------

def validate(draft: ListingDraft, market: Market | None = None,
             compliance: dict[str, Any] | None = None) -> list[Issue]:
    """出品前チェック。error が1つでもあれば出品しない。"""
    comp = compliance if compliance is not None else load_compliance()
    limits = comp.get("limits") or {}
    title_max = int(limits.get("title_max_chars", 120))
    desc_min = int(limits.get("description_min_chars", 200))
    img_min = int(limits.get("images_min", 3))
    img_max = int(limits.get("images_max", 9))

    issues: list[Issue] = []

    if not draft.title.strip():
        issues.append(Issue(SEVERITY_ERROR, "title", "タイトルが空です"))
    elif len(draft.title) > title_max:
        issues.append(Issue(
            SEVERITY_ERROR, "title",
            f"タイトルが{len(draft.title)}文字で上限{title_max}文字を超えています"
        ))
    elif len(draft.title) < 25:
        issues.append(Issue(
            SEVERITY_WARN, "title",
            f"タイトルが{len(draft.title)}文字と短いです。検索で拾われにくいので、"
            "用途・対象月齢・素材などのキーワードを足してください"
        ))

    if len(draft.description) < desc_min:
        issues.append(Issue(
            SEVERITY_WARN, "description",
            f"説明文が{len(draft.description)}文字です。{desc_min}文字以上あると転換率が上がります"
        ))

    if len(draft.images) < img_min:
        issues.append(Issue(
            SEVERITY_ERROR, "images",
            f"画像が{len(draft.images)}枚しかありません（最低{img_min}枚）"
        ))
    elif len(draft.images) > img_max:
        issues.append(Issue(SEVERITY_WARN, "images", f"画像が{img_max}枚を超えています"))

    if draft.price_local <= 0:
        issues.append(Issue(SEVERITY_ERROR, "price_local", "売価が0以下です"))
    if draft.stock < 0:
        issues.append(Issue(SEVERITY_ERROR, "stock", "在庫がマイナスです"))
    if draft.weight_g <= 0:
        issues.append(Issue(SEVERITY_ERROR, "weight_g", "重量が未入力です（送料が計算できません）"))
    if not draft.category_id:
        issues.append(Issue(SEVERITY_WARN, "category_id", "カテゴリ未設定です（検索に出にくくなります）"))

    issues.extend(check_forbidden_terms(draft, comp))
    issues.extend(check_disclaimer(draft, comp))

    if market is not None and market.code != draft.market_code:
        issues.append(Issue(
            SEVERITY_ERROR, "market_code",
            f"市場コードが一致しません（draft={draft.market_code}, market={market.code}）"
        ))

    return issues


def check_forbidden_terms(draft: ListingDraft, compliance: dict[str, Any]) -> list[Issue]:
    """薬機法的な言い切り・絶対表現を検出する。ベビー用品で一番危ないところ。"""
    terms_by_lang = compliance.get("forbidden_terms") or {}
    haystack = f"{draft.title}\n{draft.description}"
    issues: list[Issue] = []
    seen: set[str] = set()
    # 出品言語と日本語（元原稿の直訳が混ざりがち）の両方を見る。
    # 同じ語が複数の言語リストに載っていても、報告は1回にまとめる。
    for lang in {draft.language, "ja"}:
        for term in terms_by_lang.get(lang, []) or []:
            term = str(term)
            if not term or term in seen:
                continue
            if re.search(re.escape(term), haystack, re.IGNORECASE):
                seen.add(term)
                issues.append(Issue(
                    SEVERITY_ERROR, "claims",
                    f"禁止表現「{term}」が入っています。効能をうたう・断定する表現は削除してください"
                ))
    return issues


def check_disclaimer(draft: ListingDraft, compliance: dict[str, Any]) -> list[Issue]:
    """必須の注意書きが説明文末尾に入っているか。"""
    required = (compliance.get("required_disclaimers") or {}).get(draft.language)
    if not required:
        return []
    # 全文一致は求めず、特徴的な冒頭部分で判定する（翻訳の揺れを許容）
    probe = str(required).strip()[:12]
    if probe and probe not in draft.description:
        return [Issue(
            SEVERITY_WARN, "description",
            "必須の注意書き（越境発送・大人の付き添い）が見当たりません。"
            "config/compliance.yaml の required_disclaimers を末尾に付けてください"
        )]
    return []


def has_errors(issues: Iterable[Issue]) -> bool:
    return any(i.severity == SEVERITY_ERROR for i in issues)


def append_disclaimer(draft: ListingDraft, compliance: dict[str, Any] | None = None) -> ListingDraft:
    """必須注意書きを説明文の末尾に足した複製を返す。"""
    comp = compliance if compliance is not None else load_compliance()
    text = (comp.get("required_disclaimers") or {}).get(draft.language)
    if not text or str(text).strip()[:12] in draft.description:
        return draft
    draft.description = draft.description.rstrip() + "\n\n" + str(text).strip()
    return draft


# ---------------------------------------------------------------------------
# CSV 出力
# ---------------------------------------------------------------------------

def load_template(name: str = "default_mass_upload") -> dict[str, Any]:
    path = CONFIG_DIR / "csv_templates" / f"{name}.yaml"
    if not path.exists():
        available = ", ".join(p.stem for p in (CONFIG_DIR / "csv_templates").glob("*.yaml"))
        raise FileNotFoundError(f"CSVテンプレート '{name}' がありません。使えるのは: {available}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def write_listings_csv(drafts: Sequence[ListingDraft], dest: Path,
                       template_name: str = "default_mass_upload") -> Path:
    """一括アップロード用CSVを書き出す。"""
    template = load_template(template_name)
    columns = template["columns"]
    encoding = template.get("encoding", "utf-8-sig")

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding=encoding, newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([c["header"] for c in columns])
        for draft in drafts:
            row = draft.to_row()
            writer.writerow([row.get(c["field"], "") for c in columns])
    return dest


def write_rows(rows: Sequence[dict[str, Any]], dest: Path) -> Path:
    """任意の辞書リストをそのままCSVにする（在庫更新CSVなど汎用）。"""
    if not rows:
        raise ValueError("書き出す行がありません")
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = list(rows[0].keys())
    with dest.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    return dest


def default_output_dir() -> Path:
    return REPO_ROOT / "data" / "products"
