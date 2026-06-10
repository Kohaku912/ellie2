# Ellie の自己改善リクエスト
AI が「今すぐ実装したほうがよい」と判断した改善依頼を、短い自然文で残すためのメモです。

## 保留中の自己改善リクエスト
- Playwright MCP で X/Twitter を開いたら、ログイン確認、必要なクリック、入力、遷移、読取まで続けて、途中で止まらないようにする。
- `twitter_followers_check` の案内が必要なときは、`request_user_approval` で overlay を実際に出して、ユーザーにログインを促せるようにする。
- 重タスクの案内は「使えるツールが 4 つだけ」という古い前提に戻さず、`overlay_show`、`request_user_approval`、`twitter_followers_check` も含めて扱う。
- 指示文の例示は増やしすぎず、同じ方向に偏りにくい簡潔な指示に整える。
- Playwright MCP が ReferenceError: URL is not defined で落ちている。PC クライアント側の Playwright MCP ツール内部の問題の可能性。修正を検討する。
- twitter_followers_check のプロフィール検出が失敗している。原因は Playwright 実行コンテキストで `new URL()` が ReferenceError になる可能性。改善案: (1) `URL` の代わりに文字列結合でプロフィールURLを構築する、(2) サイドバーのナビゲーションリンクからプロフィールを探す（'a[data-testid="AppTabBar_Profile_Link"]'）、(3) ホーム画面から `/settings/profile` など既知パスを使わず、サイドメニューのプロフィールアイコンをクリックして遷移する方法に変更する。
