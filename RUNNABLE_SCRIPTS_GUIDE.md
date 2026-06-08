# Ellie Agent - 実行スクリプト完全ガイド

## 実行スクリプト一覧

### 🎬 デモ・テスト用スクリプト

#### 1. `run_manual_task_demo.py` - デモモード（最初に試すべき）
```powershell
.\venv\Scripts\python run_manual_task_demo.py
```

**用途:** API キーなしでシステム全体をテスト  
**実行時間:** ~1 秒  
**生成ファイル:** `agent_data/task_outputs/suggestions_*.md`

**期待される出力:**
```
✓ Modules loaded successfully
✓ Memory manager initialized
✓ Task executor initialized
✓ Manual task execution completed successfully!
```

---

#### 2. `test_api_connection.py` - API 接続テスト
```powershell
.\venv\Scripts\python test_api_connection.py
```

**用途:** Cerebras API キーと接続を検証  
**実行時間:** ~3 秒  
**結果:**
- ✅ PASS: API 接続成功
- ❌ FAIL: API キーまたは接続エラー（トラブルシューティング情報表示）

---

#### 3. `test_daemon_startup.py` - デーモン起動テスト
```powershell
.\venv\Scripts\python test_daemon_startup.py
```

**用途:** スケジューラの起動と 3 つのジョブ登録を確認  
**実行時間:** ~5 秒  
**確認項目:**
- Hourly autonomous task generation (毎時)
- Daily memory reset (毎日 00:00 UTC)
- Daily summary generation (毎日 21:59 UTC)

---

#### 4. `test_setup.py` - セットアップテスト
```powershell
.\venv\Scripts\python test_setup.py
```

**用途:** 全依存関係とディレクトリ構造を検証  
**実行時間:** ~1 秒  
**テスト内容:**
- ✓ Directory structure
- ✓ Configuration loading
- ✓ Module imports
- ✓ Memory system

---

### 🚀 実行用スクリプト

#### 5. `run_manual_task.py` - 手動実行（API キー必須）
```powershell
.\venv\Scripts\python run_manual_task.py
```

**用途:** 手動で 1 回の AI タスク生成・実行サイクルを実行  
**実行時間:** ~5 秒  
**前提条件:**
- `.env` に有効な `CEREBRAS_API_KEY` が設定されていること
- インターネット接続が利用可能なこと

**機能:**
- Cerebras API を使用して実際のタスクを生成
- ReAct サイクル全体実行
- メモリシステムに実行結果を記録
- タスク実行エンジンで生成されたタスクを実行

---

#### 6. `main.py` - バックグラウンド デーモン
```powershell
.\venv\Scripts\python main.py
```

**用途:** 継続的にバックグラウンドで実行（スケジュール式）  
**実行時間:** 無期限（Ctrl+C で終了）  
**前提条件:**
- `.env` に有効な `CEREBRAS_API_KEY` が設定されていること
- バックグラウンドで実行し続ける環境

**動作:**
- **毎時実行:** 9:00-21:00 UTC 内の各時間に AI タスク生成
- **日次リセット:** 毎日 00:00 UTC に前日メモリをアーカイブ
- **日次サマリー:** 毎日 21:59 UTC に 1 日のサマリー生成
- **グレースフルシャットダウン:** Ctrl+C で安全に終了

**監視ログ:**
```powershell
Get-Content agent_data\logs\execution.log -Tail 20 -Wait
```

---

## 実行フロー

### 初回セットアップ時

```
1. test_setup.py          ← 環境確認
   ↓
2. run_manual_task_demo.py ← デモでシステム確認
   ↓
3. test_api_connection.py  ← API キー確認
   ↓
4. main.py                 ← バックグラウンド起動
```

### 日常使用時

```
main.py (継続実行)
  ├─ 毎時: 自動的に AI タスク生成・実行
  ├─ 00:00: 前日メモリをアーカイブ
  └─ 21:59: 1 日のサマリー生成
```

### トラブルシューティング時

```
test_setup.py              ← 基本確認
  ↓
run_manual_task_demo.py    ← デモで動作確認
  ↓
test_api_connection.py     ← API 接続確認
  ↓
test_daemon_startup.py     ← スケジューラ確認
```

---

## スクリプト仕様表

| スクリプト | 用途 | API 必須 | 実行時間 | ファイル生成 |
|----------|------|---------|---------|-----------|
| run_manual_task_demo.py | デモ実行 | ❌ | ~1s | ✅ suggestions_*.md |
| test_api_connection.py | API テスト | ✅ | ~3s | ❌ |
| test_daemon_startup.py | スケジューラテスト | ❌ | ~5s | ❌ |
| test_setup.py | セットアップテスト | ❌ | ~1s | ❌ |
| run_manual_task.py | 手動実行 | ✅ | ~5s | ✅ task_*.md |
| main.py | デーモン | ✅ | ∞ | ✅ daily_*.md |

---

## ファイル生成ツリー

```
実行後、以下のファイルが生成されます：

agent_data/
├── memory.json                               # 本日のメモリ（JSON）
│
├── archive/
│   ├── memory_2026-06-07.json               # 前日のスナップショット
│   ├── memory_2026-06-06.json               # 2 日前
│   └── ...                                  # 30 日間保持
│
├── logs/
│   └── execution.log                        # 全実行ログ
│
└── task_outputs/
    ├── suggestions_20260608_014424.md       # 提案ファイル
    ├── daily_report_20260608_014424.md      # 日報（本番のみ）
    ├── health_check_20260608_014424.md      # ヘルスチェック（本番のみ）
    └── ...
```

---

## クイックスタート

### 1 分で動かす

```powershell
cd c:\Users\kohak\programs\ellie2
.\venv\Scripts\python run_manual_task_demo.py
```

✅ これで完全に動作することが確認できます。

---

## トラブルシューティング フローチャート

```
エラー発生？
  │
  ├─ "ModuleNotFoundError" → pip install -r requirements.txt
  │
  ├─ "API connection failed" → test_api_connection.py で確認
  │
  ├─ "Scheduler not working" → test_daemon_startup.py で確認
  │
  └─ その他 → run_manual_task_demo.py でデモ確認
```

---

## 詳細ドキュメント

- [MANUAL_EXECUTION_GUIDE.md](MANUAL_EXECUTION_GUIDE.md) - 英文詳細ガイド
- [README_JA.md](README_JA.md) - 日本語概要
- [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md) - 実装詳細
- [config.py](config.py) - 設定ファイル

---

**推奨:** まずは `run_manual_task_demo.py` で試してください！🚀
