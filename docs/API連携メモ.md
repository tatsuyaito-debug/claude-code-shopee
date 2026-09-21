# 将来のAPI連携について（技術メモ）

いまは **CSV運用**（Seller Centre から手でダウンロード／アップロード）で動いています。
審査が不要ですぐ使えて、出品数が数十〜百品程度なら十分実用的です。

在庫の同期や注文処理を自動化したくなったら、Shopee Open Platform API に差し替えます。
**そのときに上位のスクリプトとAI社員を書き換えずに済むよう**、データの出入りは
`scripts/shopee/backend.py` のインターフェースに集約してあります。

## 切り替え方

```python
from shopee.backend import get_backend

backend = get_backend()          # 環境変数 SHOPEE_BACKEND で csv / api を切り替え
orders = backend.fetch_orders()
```

`ApiBackend` を実装すれば、`SHOPEE_BACKEND=api` にするだけで切り替わります。

## 実装に必要なもの

1. **Shopee Open Platform でのアプリ登録** — partner_id と partner_key が発行される
2. **ショップの認可** — OAuth でショップ側が許可し、access_token / refresh_token を取得する
   （access_token は短命なので、refresh のロジックが必要）
3. **リクエスト署名** — HMAC-SHA256。`partner_id + api_path + timestamp + access_token + shop_id`
   を連結して partner_key で署名する
4. **エンドポイント**

| 用途 | API |
|---|---|
| 注文一覧 | `order.get_order_list` |
| 注文詳細 | `order.get_order_detail` |
| 出品追加 | `product.add_item` |
| 出品更新 | `product.update_item` |
| 在庫更新 | `product.update_stock` |
| 価格更新 | `product.update_price` |

## 実装するときの注意

- **認証情報をリポジトリに書かない。** 環境変数か `.env`（`.gitignore` 済み）に置く
- **レート制限がある。** 一括更新はまとめて投げず、間隔を空ける
- **timestamp のずれで署名が失敗する。** サーバー時刻がずれていないか確認する
- **先に読み取り系（注文取得）だけ実装する。** 書き込み系を最初に作ると、
  バグで全商品の価格を壊すような事故が起きうる
- CSV運用は残しておく。API障害時のフォールバックになる

## 優先度について

**出品数が100品を超えるまでは、API連携をやる価値は薄いです。**
それより出品数を増やすことに時間を使ったほうが売上に直結します。
在庫同期の手作業が明らかに負担になってから着手してください。
