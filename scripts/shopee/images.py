"""商品画像の取り回し。

Shopeeの一括アップロードCSVに入れられるのは「画像のURL」までで、
画像ファイルそのものはCSVに乗らない。手元に写真しかない状態では、

  1. CSVで商品の骨格（タイトル・説明・価格・在庫）を作ってアップロード
  2. Seller Centre の商品編集画面で、写真を1商品ずつアップロードする

という2段構えになる。このモジュールは、その2段目を間違えずに回すために
「どのSKUに、どの写真を、どの順番で入れるか」の手順を作る。

画像のサイズ確認は Pillow を使わず、PNG / JPEG のヘッダーを直接読む。
奥さんの環境に追加インストールを増やしたくないため。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import REPO_ROOT

IMAGES_ROOT = REPO_ROOT / "data" / "products" / "images"

# カバー画像から順に、この役割で並べるとうまくいく（出品担当AIもこの順で指示を出す）
IMAGE_ROLES = [
    "カバー：白背景で商品だけ。一覧で並んだときに勝つ絵にする",
    "使用シーン：赤ちゃんが使っている様子",
    "サイズ比較：手や定規と並べて大きさが分かるように",
    "素材・ディテールの寄り",
    "セット内容の全体像",
]

SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png"}


def is_url(ref: str) -> bool:
    return ref.strip().lower().startswith(("http://", "https://"))


def resolve(ref: str, sku: str, root: Path | None = None) -> Path:
    """出品案に書かれた画像の指定を、実ファイルのパスに直す。

    'BIB-001/01-cover.jpg' のようなSKU込みの相対指定でも、
    '01-cover.jpg' のようなファイル名だけでも受け取れるようにする。
    """
    root = root or IMAGES_ROOT
    ref = ref.strip()
    candidate = root / ref
    if candidate.exists():
        return candidate
    return root / sku / ref


def list_sku_images(sku: str, root: Path | None = None) -> list[Path]:
    """data/products/images/<SKU>/ の画像をファイル名順に返す。

    ファイル名の先頭に 01, 02 と番号を振っておけば、その順が出品の並び順になる。
    """
    folder = (root or IMAGES_ROOT) / sku
    if not folder.is_dir():
        return []
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )


# ---------------------------------------------------------------------------
# 画像サイズの読み取り（依存を増やさないためヘッダーを直接読む）
# ---------------------------------------------------------------------------

def read_dimensions(path: Path) -> tuple[int, int] | None:
    """PNG / JPEG の (幅, 高さ) を返す。読めなければ None。"""
    try:
        with path.open("rb") as fh:
            head = fh.read(26)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                width, height = struct.unpack(">II", head[16:24])
                return int(width), int(height)
            if head[:2] == b"\xff\xd8":
                return _jpeg_dimensions(fh)
    except OSError:
        return None
    return None


def _jpeg_dimensions(fh) -> tuple[int, int] | None:
    """JPEGのSOFマーカーを探して寸法を取り出す。"""
    fh.seek(2)
    while True:
        marker = fh.read(2)
        if len(marker) < 2 or marker[0] != 0xFF:
            return None
        code = marker[1]
        length_bytes = fh.read(2)
        if len(length_bytes) < 2:
            return None
        length = struct.unpack(">H", length_bytes)[0]
        # SOF0〜SOF15。ただし DHT(C4) / JPG(C8) / DAC(CC) は寸法を持たない
        if 0xC0 <= code <= 0xCF and code not in (0xC4, 0xC8, 0xCC):
            payload = fh.read(5)
            if len(payload) < 5:
                return None
            height, width = struct.unpack(">HH", payload[1:5])
            return int(width), int(height)
        fh.seek(length - 2, 1)


@dataclass(frozen=True)
class ImageCheck:
    path: Path
    exists: bool
    width: int = 0
    height: int = 0
    bytes_size: int = 0
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.exists and not self.problems

    @property
    def is_square(self) -> bool:
        return bool(self.width) and self.width == self.height


def check_image(path: Path, limits: dict[str, Any] | None = None) -> ImageCheck:
    """1枚の画像が出品に耐えるかを見る。"""
    limits = limits or {}
    min_side = int(limits.get("image_min_px", 500))
    recommended = int(limits.get("image_recommended_px", 1000))
    max_mb = float(limits.get("image_max_mb", 2.0))

    if not path.exists():
        return ImageCheck(path=path, exists=False, problems=("ファイルが見つかりません",))

    problems: list[str] = []
    size = path.stat().st_size
    dims = read_dimensions(path)

    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        problems.append(f"対応していない形式です（{path.suffix}）。jpg か png にしてください")

    if size > max_mb * 1024 * 1024:
        problems.append(f"ファイルが大きすぎます（{size / 1024 / 1024:.1f}MB / 上限 {max_mb:.0f}MB）")

    width = height = 0
    if dims is None:
        problems.append("画像サイズを読めませんでした（壊れているか、対応外の形式）")
    else:
        width, height = dims
        if min(width, height) < min_side:
            problems.append(f"小さすぎます（{width}×{height} / 最低 {min_side}px）")
        elif min(width, height) < recommended:
            problems.append(f"もう少し大きいほうが綺麗です（{width}×{height} / 推奨 {recommended}px以上）")
        if width != height:
            problems.append(f"正方形ではありません（{width}×{height}）。一覧で切れることがあります")

    return ImageCheck(
        path=path, exists=True, width=width, height=height,
        bytes_size=size, problems=tuple(problems),
    )


# ---------------------------------------------------------------------------
# アップロード手順
# ---------------------------------------------------------------------------

@dataclass
class UploadPlan:
    """1商品ぶんの、Seller Centre での画像アップロード手順。"""

    sku: str
    name_ja: str
    checks: list[ImageCheck]

    @property
    def has_problems(self) -> bool:
        return any(not c.ok for c in self.checks)

    def lines(self) -> list[str]:
        out = [f"□ {self.sku}  {self.name_ja}"]
        for i, check in enumerate(self.checks, start=1):
            role = IMAGE_ROLES[i - 1] if i <= len(IMAGE_ROLES) else "追加カット"
            mark = "  " if check.ok else "⚠️"
            dims = f"{check.width}×{check.height}" if check.width else "サイズ不明"
            out.append(f"   {mark} {i}枚目  {check.path.name}  ({dims})")
            out.append(f"        {role}")
            for problem in check.problems:
                out.append(f"        → {problem}")
        return out


def build_upload_plan(sku: str, name_ja: str, refs: list[str],
                      limits: dict[str, Any] | None = None,
                      root: Path | None = None) -> UploadPlan:
    paths = [resolve(ref, sku, root) for ref in refs if not is_url(ref)]
    return UploadPlan(sku=sku, name_ja=name_ja,
                      checks=[check_image(p, limits) for p in paths])


def split_refs(refs: list[str]) -> tuple[list[str], list[str]]:
    """画像の指定を (URL, ローカルファイル) に分ける。"""
    urls = [r for r in refs if is_url(r)]
    local = [r for r in refs if not is_url(r)]
    return urls, local
