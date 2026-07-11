# apple-macmini-watch

Apple整備済製品ページ（日本）を5分ごとに監視し、条件に合うMac miniが
出品されたらLINEに通知する。

- 条件: Mac mini / M4系チップ / メモリ24GB以上
- 実行: GitHub Actions（`.github/workflows/watch.yml`）
- 通知: LINE Messaging API（認証情報はリポジトリSecrets）
- `state.json`: 通知済み商品の管理（売り切れ→再入荷で再通知）
