# Ellie Agent - 実行ガイド (日本語)

## 概要
Ellie AI Agent は、以下の3つのモードで実行できます：

## 🎯 実行方法

### 1️⃣ デモモード (推奨 - API キー不要)
```powershell
cd c:\Users\kohak\programs\ellie2
.\venv\Scripts\python run_manual_task_demo.py
```

✅ **実行結果:**
- 2つのタスクを生成・実行
- ファイルを生成: `agent_data/task_outputs/suggestions_*.md`
- 実行時間: ~1秒
- **API キー不要**

### 2️⃣ API 接続テスト (API キー設定確認用)
```powershell
.\venv\Scripts\python test_api_connection.py
```

✅ **実行結果:**
- Cerebras API への接続確認
- API キーが有効か確認
- エラーメッセージで解決方法を提示

### 3️⃣ 手動実行 (API キー必須)
```powershell
.\venv\Scripts\python run_manual_task.py
```

✅ **実行結果:**
- Cerebras API を使用して実際のタスク生成
- ReAct サイクル完全実行
- メモリシステムに記録

### 4️⃣ バックグラウンド デーモン (継続実行)
```powershell
.\venv\Scripts\python main.py
```

✅ **実行結果:**
- 毎時間自動実行（9-21 UTC）
- 日次リセット（00:00 UTC）
- 継続実行（Ctrl+C で終了）

## 📊 テスト結果

| テスト | 結果 | 詳細 |
|------|------|------|
| デモモード実行 | ✅ PASS | 2つのタスク生成・実行成功 |
| スケジューラ起動 | ✅ PASS | 3つのジョブ登録成功 |
| メモリシステム | ✅ PASS | JSON 記憶正常動作 |
| ファイル生成 | ✅ PASS | suggestions_*.md 生成成功 |

## 📁 生成ファイル位置

```
agent_data/
├── memory.json                          # 本日のメモリ
├── archive/memory_YYYY-MM-DD.json      # 前日のスナップショット
├── logs/execution.log                  # 実行ログ
└── task_outputs/
    ├── suggestions_*.md                # 提案ファイル
    ├── daily_report_*.md               # 日報（本番モード）
    └── health_check_*.md               # ヘルスチェック（本番モード）
```

## 🔧 トラブルシューティング

**Q: モジュールが見つからないエラー**
```powershell
cd c:\Users\kohak\programs\ellie2
.\venv\Scripts\pip install -r requirements.txt
```

**Q: API 接続エラー (404)**
- ✅ デモモードで動作確認: `run_manual_task_demo.py`
- `.env` の `CEREBRAS_API_KEY` を確認
- API ステータス確認: https://www.cerebras.ai/

**Q: ログをリアルタイムで見たい**
```powershell
Get-Content agent_data\logs\execution.log -Tail 20 -Wait
```

## 💡 推奨される使い方

### 初回セットアップ
1. `test_setup.py` で依存関係確認
2. `run_manual_task_demo.py` でシステム動作確認
3. `test_api_connection.py` で API キー確認

### 日常使用
1. `python main.py` でバックグラウンド起動
2. `agent_data/logs/execution.log` でログ監視
3. `agent_data/task_outputs/` で出力ファイル確認

### 問題診断
1. `test_daemon_startup.py` でスケジューラ確認
2. `run_manual_task_demo.py` で基本機能確認
3. `test_api_connection.py` で API 接続確認

## ✅ 実装完了項目

- ✅ ReAct パターン（Think→Plan→Act→Reflect）
- ✅ 外部メモリシステム（JSON ベース、日次リセット）
- ✅ APScheduler による 3 つのジョブ登録
- ✅ タスク実行エンジン（5 種類のタスク型）
- ✅ Windows 対応（signal.pause() 修正）
- ✅ Cerebras API 統合（base_url 設定済み）
- ✅ 手動実行機能（デモ + 本番）
- ✅ 完全なテストスイート

---

**準備完了！** デモモードで今すぐ試してください: `python run_manual_task_demo.py`
