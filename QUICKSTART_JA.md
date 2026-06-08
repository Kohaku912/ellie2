# Ellie Agent - Quick Start Guide (日本語)

## セットアップ（5分）

### 1. 環境構築
```powershell
cd c:\Users\kohak\programs\ellie2

# 仮想環境の作成
python -m venv venv

# 仮想環境の有効化
.\venv\Scripts\activate

# 依存パッケージのインストール
pip install -r requirements.txt
```

### 2. 設定ファイルの準備
```powershell
# .env ファイルを編集（テキストエディタで開く）
notepad .env

# 必要な設定：
# CEREBRAS_API_KEY=<あなたのAPIキーを入力>
```

### 3. 動作確認
```powershell
# セットアップテストを実行
.\venv\Scripts\python test_setup.py

# 期待される出力:
# ✓ PASS: Directory structure
# ✓ PASS: Configuration
# ✓ PASS: Imports
# ✓ PASS: Memory system
# ✓ All tests passed! System is ready to run.
```

## 実行方法

### 方法1: フォアグラウンド実行（ログを見ながら実行）
```powershell
cd c:\Users\kohak\programs\ellie2
.\venv\Scripts\python main.py

# ログが表示される
# [実行を停止するには Ctrl+C を押す]
```

### 方法2: バックグラウンド実行（デーモンモード）
```powershell
# PowerShell ジョブとして実行
Start-Job -FilePath "C:\Users\kohak\programs\ellie2\run_agent_bg.ps1"

# ジョブを確認
Get-Job

# ジョブを停止
Stop-Job -Name "Job1"
Remove-Job -Name "Job1"
```

`run_agent_bg.ps1` を作成:
```powershell
Set-Location C:\Users\kohak\programs\ellie2
.\venv\Scripts\python main.py
```

## 動作確認

### メモリファイルを確認
```powershell
# 本日のメモリ状態を確認
Get-Content agent_data\memory.json | ConvertFrom-Json | Format-Table

# 最新の実行ログを確認
Get-Content agent_data\logs\execution.log -Tail 20

# タスク出力を確認
Get-ChildItem agent_data\task_outputs\
```

### ログをリアルタイムで監視
```powershell
# 実行ログをリアルタイムで見る
Get-Content -Path agent_data\logs\execution.log -Wait
```

## 設定のカスタマイズ

### 実行時間帯の変更
```env
# .env ファイルを編集
AGENT_START_HOUR=8      # 8時開始
AGENT_END_HOUR=22       # 22時終了
```

### エージェントの名前変更
```env
AGENT_NAME=Ellie        # 変更可能
```

### ログレベルの変更
```env
LOG_LEVEL=DEBUG         # より詳細なログを出力
```

## 日次メモリの理解

### メモリ構造
- **date**: メモリが作成された日付（UTC）
- **daily_stats**: その日の統計情報
  - `tasks_generated`: 生成されたタスク数
  - `tasks_executed`: 実行されたタスク数
  - `tasks_completed`: 完了したタスク数
  - `total_execution_time_ms`: 総実行時間
  - `total_api_calls`: API呼び出し回数
- **execution_history**: 実行履歴（毎時間更新）
- **today_insights**: 洞察と学習内容
- **completed_tasks**: 完了したタスク一覧
- **failed_tasks**: 失敗したタスク一覧

### メモリのリセット
- **毎日00:00 UTC**: 前日のメモリが `archive/` に保存され、新しいメモリが作成される
- **30日以上前**: 古いアーカイブが自動削除される

## トラブルシューティング

### エラー: "CEREBRAS_API_KEY environment variable is not set"
**原因**: .env ファイルに API キーが設定されていない  
**解決法**: 
```powershell
notepad .env
# CEREBRAS_API_KEY=<実際のAPIキー> を追加
```

### エラー: "No module named 'anthropic'"
**原因**: 仮想環境が有効化されていない  
**解決法**:
```powershell
.\venv\Scripts\activate
pip install -r requirements.txt
```

### ログが出力されない
**原因**: スケジューラーが実行時間帯外の可能性  
**確認方法**:
```powershell
# 現在のUTC時刻を確認
[DateTime]::UtcNow

# 設定時間を確認
Get-Content .env | findstr AGENT_START_HOUR
Get-Content .env | findstr AGENT_END_HOUR
```

## 実行の流れ（毎時間）

```
[時刻: 9:00 UTC]
  ↓
【Think】- メモリとコンテキストを分析
  - 実行済みタスク
  - 今までの成果
  - ユーザーニーズの推定
  ↓
【Plan】- タスク案を生成（1-3個）
  - ファイル操作
  - データ分析
  - 提案生成
  - 調査タスク
  ↓
【Act】- 最適なタスクを選択して実行
  - API 呼び出し
  - ファイル生成
  - 分析実行
  ↓
【Reflect】- 結果をメモリに記録
  - 実行時間
  - 成功/失敗
  - 学んだこと
  ↓
[メモリ更新完了]
[次の時刻まで待機...]
```

## ファイル構成の理解

```
ellie2/
├── main.py                    # 実行開始ファイル
├── config.py                  # 設定管理
├── requirements.txt           # 依存パッケージ
├── .env                       # APIキーなど（秘密）
├── README.md                  # 詳細ドキュメント
├── test_setup.py              # セットアップテスト
│
├── agent/                     # 推論エンジン
│   ├── cerebras_agent.py      # ReAct実装
│   └── memory.py              # メモリ管理
│
├── scheduler/                 # スケジューリング
│   └── scheduler.py           # APScheduler設定
│
├── tasks/                     # タスク実行
│   ├── task_executor.py       # 実行エンジン
│   └── tools.py               # 利用可能ツール
│
└── agent_data/                # 永続データ
    ├── memory.json            # 本日のメモリ
    ├── task_log.json          # タスクログ
    ├── archive/               # 過去のメモリ
    ├── logs/                  # ログファイル
    └── task_outputs/          # 生成ファイル
```

## パフォーマンス目安

- **API呼び出し**: 2-5秒
- **タスク実行**: 1-3秒
- **メモリ使用量**: 50-100 MB
- **毎時実行時間**: 約 5-10秒
- **1日の実行時間**: 12時間（9-21時）

## 高度な設定

### ログレベルの詳細設定
```env
LOG_LEVEL=DEBUG    # 開発時（詳細ログ）
LOG_LEVEL=INFO     # 本番運用（標準）
LOG_LEVEL=WARNING  # 本番運用（警告以上のみ）
```

### タイムゾーンの変更
```env
AGENT_TIMEZONE=Asia/Tokyo    # 日本時間（設定例）
```

注: 内部的にはすべて UTC で処理されています

## よくある質問（FAQ）

**Q: 夜間も実行したいのですが？**
A: `.env` で `AGENT_END_HOUR=23` などに変更してください

**Q: メモリを手動でリセットできますか？**
A: `agent_data/memory.json` を削除すれば、次の実行時に新しいメモリが作成されます

**Q: 複数台のマシンで実行できますか？**
A: はい。各マシンで独立した `.env` と `agent_data/` を管理してください

**Q: API キーを安全に管理するには？**
A: Windows Credential Manager か Azure Key Vault の使用をお勧めします

## 次のステップ

1. ✅ セットアップ完了
2. ✅ テスト実行完了
3. → **`.\venv\Scripts\python main.py` で実行開始**
4. → ログを監視: `Get-Content agent_data\logs\execution.log -Wait`
5. → メモリを確認: `Get-Content agent_data\memory.json | ConvertFrom-Json`

---

**お疲れ様でした！Ellie エージェントの準備が整いました。**  
毎時間、自律的にタスクを生成・実行します。ぜひ試してみてください！
