# ビザ検定（出題方式 比較版）

社内向けビザ知識検定アプリの **出題方式 比較システム**。
**固定プール方式**（事前作成の問題集からランダム出題）と **RAG方式**（原本＋観点から
LLMが毎回生成）を、**同一UI・同一採点ロジック**で並走させ、出題品質・難度安定性・
コスト・レイテンシを比較することが目的。

ベースは [visa-examination](https://github.com/atsushibanbanji-collab/visa-examination)
（固定プール版）。本リポジトリはそこに RAG 方式を追加した別システム。

## 2つの出題方式

| | 固定プール方式 | RAG方式 |
|---|---|---|
| 出題元 | `questions.json`（初級160問） | 原本PDF＋観点メタ（188観点）からLLM生成 |
| 速度 | 即時 | 生成に数秒（LLM呼び出し） |
| 多様性 | プール内で固定 | 毎回異なる |
| コスト | ゼロ | トークン課金 |
| 進捗管理 | `source='pool'` | `source='rag'`（独立管理） |

トップ画面で受験者名・難易度に加えて **出題方式** を選ぶ。以降の受験画面・採点・解説は
両方式で共通（`quiz.html` を共用）。進捗（連続記録）は方式ごとに別々に記録されるので、
片方の受験がもう片方のストリークを汚さない。

## ディレクトリ構成

```
backend/
  main.py              アプリ組み立て（固定プール + 観点メタ + DBを起動時ロード）
  config.py            定数・環境変数（RAG設定を含む）
  db.py                SQLite。attempts/unit_progress に source 列、quiz_sessions 新設
  questions_store.py   固定プールの状態管理（questions.json）
  questions_io.py      CSV入出力
  services.py          採点ロジック（出題整形・卒業判定）
  models.py            Pydantic スキーマ（mode/session_id を追加）
  routes_quiz.py       受験系。pool/rag を mode で分岐
  routes_admin.py      管理系。比較集計エンドポイントを追加
  rag_perspectives.py  観点メタのロード＆サンプリング
  rag_source.py        原本PDFのページテキスト供給（2-upレイアウト対応）
  rag_generator.py     観点サンプリング→プロンプト→LLM生成→JSON検証→リトライ
  rag_session_store.py RAGセッション問題プールのライフサイクル
  perspectives/        観点メタ10ファイル（計188観点）
  source/              原本PDF/txt（gitignore。実体は手動配置）
frontend/
  index.html           名前＋方式＋難易度の選択
  units.html           単元選択（pool: 卒業試験あり / rag: 観点数表示）
  quiz.html            受験画面（両方式共通）
  result.html          結果＋RAG生成メトリクス＋履歴（方式バッジ付き）
  admin-x7k2a9.html    管理画面（比較集計を追加）
  assets/              style.css, quiz.js, admin.js, common.js
docs/rag/              実装指示書（IMPLEMENTATION_SPEC.md 等）
```

## ローカル起動

Python 3.12 を使うこと（3.13/3.14 では依存ビルド不可）。

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows
pip install -r backend/requirements.txt

# RAGを動かすなら API キーを設定（未設定でも固定プール方式は動く）
export ANTHROPIC_API_KEY=sk-ant-...

uvicorn backend.main:app --reload --port 8000
```

- 受験画面: `http://localhost:8000/`
- 管理画面: `http://localhost:8000/admin-x7k2a9.html`

### 原本PDFの配置（RAGをPDF根拠で動かす場合）

著作権の都合で原本PDFはリポジトリに含めない（`.gitignore` 済み）。
`backend/source/visa_guide_v22_1.pdf` に手動配置する。
原本は 2-up レイアウト（1物理ページに論理2ページ）なので、観点メタの
`source_pages`（論理ページ）→ 物理ページ = `論理ページ // 2` で対応付けている。

PDF未配置でも、観点メタの `summary`（原本に基づく事実要約）を根拠に生成は動作する
（`grounding=summary`）。結果画面・メトリクスで根拠（PDF/要約）を確認できる。

## 比較の進め方

1. 同じ単元（例: 初級・Bビザ商用）を、固定プール方式とRAG方式でそれぞれ複数回受験する。
2. 各回の結果画面で、RAGは **生成レイテンシ・入出力トークン・使用観点・根拠** を確認。
3. 管理画面の「出題方式の比較」で、source別の **受験回数・平均正答率・平均生成時間・
   平均トークン** を一覧。
4. RAGは `seed` と `観点idリスト` を履歴（attempts.details）に残すので、後から再現・監査可能。

## 主な設定（環境変数）

| 変数 | 既定 | 用途 |
|---|---|---|
| `ANTHROPIC_API_KEY` | （空） | RAG生成に必須。未設定だとRAGは503 |
| `RAG_MODEL` | claude-haiku-4-5-20251001 | 生成モデル |
| `RAG_CHOICES` | 3 | 選択肢数（3 or 4） |
| `RAG_QUESTIONS_PER_QUIZ` | 10 | 1回の出題数 |
| `RAG_SESSION_TTL_SEC` | 7200 | セッション保持秒 |
| `ADMIN_TOKEN` | x7k2a9 | 管理画面トークン |
| `DATABASE_PATH` | backend/quiz.db | DBパス |

## API（抜粋）

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/api/units?level=&user=` | 単元一覧＋進捗（固定プール） |
| GET | `/api/quiz/start?level=&unit=` | 出題（固定プール） |
| GET | `/api/rag/cells` | 観点メタのあるセル＋原本利用可否 |
| GET | `/api/rag/units?level=&user=` | 単元一覧＋RAG進捗 |
| POST | `/api/rag/quiz/start` | RAG出題（生成→セッション保存） |
| POST | `/api/quiz/check` | 1問即時判定（session_id でRAG分岐） |
| POST | `/api/quiz/submit` | 採点・保存・進捗更新（mode でpool/rag分岐） |
| GET | `/api/{TOKEN}/admin/comparison` | 方式別の比較集計 |

## 設計メモ

- **新規DBにつき `attempts`/`unit_progress` に `source` 列を最初から持たせた。** 元リポジトリの
  「attempts無改修」制約は本番デプロイ保護のためのもので、別DBの本リポジトリには適用しない。
- **RAGの正答・解説はフロントへ返さない。** 出題時はセッションに伏せて保持し、
  `/api/quiz/check`・`/api/quiz/submit` がサーバ側で照合する（固定プールと同じ思想）。
- **RAG方式に卒業試験は未実装**（比較の主眼は単元出題のため）。必要なら別途。
- ハルシネーション対策の2パス検証（`RAG_VERIFY_PASS`）は枠のみ。既定 off。

## 注意

- 同梱の固定プール問題・RAG生成問題ともに、本番運用前に専門家レビューを推奨。
- 認証はURL難読化のみ。社外公開時はBasic認証・IP制限・SSO等を追加すること。
- 原本PDF・APIキー・DBはコミットしない（`.gitignore` 済み）。
