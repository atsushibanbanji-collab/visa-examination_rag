# Claude Code 用ガイド — ビザ検定（出題方式 比較版）

固定プール方式と RAG方式を同一UI・同一採点で並走させ比較する別システム。
ベースは visa-examination（固定プール版）。詳細は README.md。

## 触ってはいけない設計判断

1. **新規DBにつき source 列を持たせている。** `attempts` / `unit_progress` に `source`
   ('pool'|'rag') 列があり、両方式の記録・進捗を分離する。元リポジトリの「attempts無改修」
   制約は本番デプロイ保護用で、別DBの本リポジトリには適用しない。
2. **進捗は (username, level, unit_id, source) で一意。** pool と rag のストリークは独立。
   `update_unit_progress(..., source=)` で使い分ける。
3. **RAGの正答・解説はフロントへ返さない。** 出題時は `quiz_sessions` に伏せて保持し、
   `/api/quiz/check`・`/api/quiz/submit` がサーバ側で照合する。固定プールと同じ思想。
4. **RAG問題IDは `sess_<uuid>#<n>` 形式。** 固定プールの永続ID（b001等）と衝突させない。
   採点はセッションの正答辞書を引く（`get_question_by_id` ではない）。
5. **原本は 2-up レイアウト。** 論理ページ→物理ページ = `論理 // SOURCE_PAGES_PER_SHEET`(=2)。
   `rag_source.text_for_pages()` がこの変換を担う。
6. **原本PDF・APIキー・DBはコミットしない**（`.gitignore` 済み・著作権/秘密情報）。
   原本未配置時は観点summaryを根拠にフォールバック（`grounding=summary`）。

## アーキテクチャ要点

- 固定プール: `questions_store`（questions.json）→ `routes_quiz` の pool 経路。既存挙動のまま。
- RAG: `rag_perspectives`（観点ロード/サンプリング）→ `rag_source`（原本テキスト）→
  `rag_generator`（プロンプト構築・LLM呼び出し・JSON検証・リトライ）→ `rag_session_store`
  （セッション保存）→ `routes_quiz` の rag 経路。
- `routes_quiz` の `/api/quiz/check`・`/api/quiz/submit` は `session_id`/`mode` で分岐。
- LLM呼び出しは `rag_generator.generate(..., llm_call=)` で差し替え可能（テストはモック注入）。

## コード規約（元リポジトリ踏襲）

- コメント・docstring は日本語。エラーは `HTTPException` で 400/403/404/502/503。
- DB操作は `db.py` 経由。問題マスタ書き込みは `questions_store.atomic_write` 経由。
- フロントのDOM注入は `escapeHtml()` を通す。
- 無音の劣化を避ける（pool/観点/原本が無ければ適切なエラーを返す）。

## 動作確認

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r backend/requirements.txt
.venv/Scripts/python _smoke_backend.py   # RAGはモックLLMで全経路検証（APIキー不要）
```

- RAGの実LLM疎通だけは `ANTHROPIC_API_KEY` 設定後に実ブラウザで確認（トークン課金あり）。
- 固定プール方式のスモーク観点は元リポジトリの CLAUDE.md と同じ。

## 既知の制限

- RAG方式に卒業試験は未実装（比較の主眼は単元出題のため）。
- ハルシネーション対策の2パス検証（`RAG_VERIFY_PASS`）は枠のみ・既定off。
- Render Free はディスク揮発 → 再デプロイで履歴・RAGセッションが消える（Persistent Disk推奨）。
