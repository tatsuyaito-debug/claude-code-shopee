# このリポジトリについて（AI向けの取扱説明）

Shopee（東南アジア・台湾のECモール）で **ベビーグッズを日本から越境販売する**ための
運営システム。オーナーはプログラマーではないので、**Claude Code に話しかけて使う**ことを前提にしている。

## いちばん大事な前提

- **オーナーの作業時間は1日1時間程度。** 提案するときは必ず所要時間を見積もり、やることを増やすなら何かを削る
- **売上ではなく利益で判断する。** 越境販売は送料・手数料・為替で利益が溶ける
- **数字は必ずスクリプトで計算する。** 推測で「だいたい◯円」と答えない
- **出力は日本語。** ただし出品文・顧客対応文は販売先の言語で書き、日本語訳を添える

## 担当AI（`.claude/skills/`）

| スキル | 担当 | 呼ばれる場面 |
|---|---|---|
| `shopee-manager` | 店長 | 今日やること、週次ふりかえり、振り分け |
| `shopee-research` | リサーチ | 何を仕入れるか、出品していい商品か |
| `shopee-pricing` | 値付け | いくらで売るか、値下げしていいか |
| `shopee-listing` | 出品 | タイトル・説明文・出品CSV |
| `shopee-cs` | 顧客対応 | チャット返信、クレーム、レビュー返信 |
| `shopee-order-ops` | 受注・発送 | 発送作業、仕入れリスト、在庫 |
| `shopee-analyst` | 数字 | 売上・利益の集計、レポート |

迷ったら `shopee-manager` から入る。

## ディレクトリ

```
config/           事業の設定。数字の判断はすべてここが根拠
  business.yaml     目標・利益方針・商品選定基準
  markets.yaml      市場別の為替・手数料・送料   ★数字は実データに要更新
  fulfillment.yaml  誰が梱包・発送するか（自社発送 / ShopeeKing発送代行）★料金は要更新
  compliance.yaml   禁止・要確認カテゴリ、禁止表現
  csv_templates/    Shopee CSV の列マッピング     ★実テンプレに要差し替え
scripts/          道具。AIはこれを実行して答えを出す
  calc_price.py           利益計算・推奨売価
  compare_fulfillment.py  自社発送と発送代行の損得比較
  build_listing_csv.py    出品案の検証とCSV書き出し
  weekly_report.py        週次レポート生成
  shopee/                 ライブラリ本体
data/
  products/products.csv   商品マスタ（原価・重量・在庫）
  products/drafts/        出品案のYAML
  orders/                 Seller Centre から落とした注文CSV
  reports/                生成されたレポート
templates/cs/     顧客対応の定型文（繁体字・英語）
docs/             人間向けマニュアル
tests/            計算ロジックのテスト
```

## 作業するときの決まり

- 値付けの答えを出す前に `python3 scripts/calc_price.py` を実行する
- **発送や在庫の話をする前に `config/fulfillment.yaml` の `active` を確認する。**
  自社発送か発送代行かで、作業フロー・リードタイム・原価が変わる
- 発送方法の切り替えを聞かれたら `python3 scripts/compare_fulfillment.py` を実行する
- 出品CSVを作る前に `--check-only` で検証を通す
- 売上・利益を語る前に `python3 scripts/weekly_report.py` を実行する
- `config/markets.yaml` の為替が古い警告が出たら、**数字を語る前に更新を依頼する**
- 計算ロジックを変えたら `python3 tests/test_pricing.py` を実行する（pytest があれば `python3 -m pytest tests/ -q`）

## 触ってはいけないこと

- `config/compliance.yaml` の `prohibited` に例外を作らない
- 損益分岐点を下回る価格を提案しない
- 顧客への返金・返品の約束を、オーナーの確認なしに確定しない
- 発送代行の料金が未入力（全項目0円）のまま「代行のほうが得」と結論づけない
- 認証情報（Shopeeのキー等）をリポジトリに書かない。環境変数か `.env` に置く

## 設定の数字について

`config/markets.yaml` の手数料率・送料・為替、および `config/fulfillment.yaml` の
発送代行の料金は **プレースホルダ**。
Seller Centre・SLS料金表・代行業者の契約の実数に置き換えるまで、計算結果は目安でしかない。
数字を使って重要な判断をするときは、まず更新されているか確認すること。
