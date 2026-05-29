# ビザ検定（RAG出題）

社内向けビザ知識検定アプリの **RAG出題版**。原本「米国ビザ申請の手引き Ver.22.1」と
観点メタ（188観点）をもとに、出題のたびに **LLMが問題を生成** する。

固定プール方式（事前作成問題のランダム出題）は別リポジトリ
[visa-examination](https://github.com/atsushibanbanji-collab/visa-examination) が担う。
本リポジトリは **RAG方式専用**。

## 出題の流れ

```
原本PDF（該当ページ） + 観点メタ（10個サンプリング） + 難度 + 単元
  → /api/rag/quiz/start のたびに LLM が10問生成（4択+正答+解説+観点id）
  → セッション問題プール（quiz_sessions）に正答を伏せて保持
  → /api/quiz/check（即時判定）・/api/quiz/submit（採点）でサーバ側照合
  → attempts / unit_progress に記録（10問満点を連続3回で単元クリア）
```

受験者名・難易度・単元を選んで受験する。UI・採点・連続記録の仕組みは固定プール版と共通。

## ディレクトリ構成

```
backend/
  main.py              アプリ組み立て（観点メタ + DBを起動時ロード）
  config.py            定数・環境変数
  db.py                SQLite。attempts / unit_progress / quiz_sessions
  models.py            Pydantic スキーマ
  routes_quiz.py       RAG出題・即時判定・採点・履歴
  routes_admin.py      管理系（履歴・ユーザー集計）
  rag_perspectives.py  観点メタのロード＆サンプリング
  rag_source.py        原本PDFのページテキスト供給（2-upレイアウト対応）
  rag_generator.py     観点サンプリング→プロンプト→LLM生成→JSON検証→リトライ
  rag_session_store.py RAGセッション問題プールのライフサイクル
  perspectives/        観点メタ10ファイル（計188観点）
  source/              原本PDF/txt（gitignore。実体は手動配置）
frontend/
  index.html           名前＋難易度の選択
  units.html           単元選択
  quiz.html            受験画面
  result.html          結果＋RAG生成メトリクス＋履歴
  admin-Kp7vQm2xRt.html 管理画面（ファイル名＝ADMIN_TOKEN）
  assets/              style.css, quiz.js, admin.js, common.js
docs/rag/              実装指示書（IMPLEMENTATION_SPEC.md 等）
```

## ローカル起動

Python 3.12 を使うこと（3.13/3.14 では依存ビルド不可）。

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows
pip install -r backend/requirements.txt

export ANTHROPIC_API_KEY=sk-ant-...   # RAG生成に必須

uvicorn backend.main:app --reload --port 8000
```

- 受験画面: `http://localhost:8000/`
- 管理画面: `http://localhost:8000/admin-Kp7vQm2xRt.html`（ファイル名が ADMIN_TOKEN と一致している必要がある）

`ANTHROPIC_API_KEY` 未設定だと出題開始時に 503 を返す。

### 原本PDFの配置

著作権の都合で原本PDFはリポジトリに含めない（`.gitignore` 済み）。
`backend/source/visa_guide_v22_1.pdf` に手動配置する。原本は 2-up レイアウト
（1物理ページに論理2ページ）なので、観点メタの `source_pages`（論理ページ）→
物理ページ = `論理ページ // 2` で対応付けている。

PDF未配置でも、観点メタの `summary`（原本に基づく事実要約）を根拠に生成は動作する
（`grounding=summary`）。結果画面のメトリクスで根拠（PDF/要約）を確認できる。

## 主な設定（環境変数）

| 変数 | 既定 | 用途 |
|---|---|---|
| `ANTHROPIC_API_KEY` | （空） | RAG生成に必須。未設定だと503 |
| `RAG_MODEL` | claude-haiku-4-5-20251001 | 生成モデル |
| `RAG_CHOICES` | 3 | 選択肢数（3 or 4） |
| `RAG_QUESTIONS_PER_QUIZ` | 10 | 1回の出題数 |
| `RAG_SESSION_TTL_SEC` | 7200 | セッション保持秒 |
| `ADMIN_TOKEN` | Kp7vQm2xRt | 管理画面トークン（`admin-<token>.html` と一致必須） |
| `DATABASE_PATH` | backend/quiz.db | DBパス |

## API

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/api/rag/cells` | 観点メタのあるセル＋原本利用可否 |
| GET | `/api/rag/units?level=&user=` | 単元一覧＋進捗 |
| POST | `/api/rag/quiz/start` | RAG出題（生成→セッション保存） |
| POST | `/api/quiz/check` | 1問即時判定（session_id でセッション照合） |
| POST | `/api/quiz/submit` | 採点・保存・進捗更新（session_id 必須） |
| GET | `/api/history?username=` | 個人履歴 |
| GET | `/api/{TOKEN}/admin/attempts` | 全履歴 |
| GET | `/api/{TOKEN}/admin/users` | ユーザー集計 |

## 設計メモ

- **RAGの正答・解説はフロントへ返さない。** 出題時はセッションに伏せて保持し、
  `check`・`submit` がサーバ側で照合する。
- **問題IDは `sess_<uuid>#<n>`。** 採点はセッションの正答辞書を引く。
- `attempts` / `unit_progress` に `source` 列があり、本リポジトリでは常に `'rag'`。
- ハルシネーション対策の2パス検証（`RAG_VERIFY_PASS`）は枠のみ。既定 off。

## 注意

- 生成問題は本番運用前に専門家レビューを推奨。
- 認証はURL難読化のみ。社外公開時はBasic認証・IP制限・SSO等を追加すること。
- 原本PDF・APIキー・DBはコミットしない（`.gitignore` 済み）。
