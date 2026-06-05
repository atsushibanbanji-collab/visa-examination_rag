# Claude Code 用ガイド — ビザ検定（RAG出題）

原本＋観点メタからLLMが毎回出題するRAG専用アプリ。固定プール版は別リポジトリ
（visa-examination）。詳細は README.md。

## 触ってはいけない設計判断

1. **RAGの正答・解説はフロントへ返さない。** 出題時は `quiz_sessions` に伏せて保持し、
   `/api/quiz/check`・`/api/quiz/submit` がサーバ側で照合する。
2. **RAG問題IDは `sess_<uuid>#<n>` 形式。** 採点はセッションの正答辞書を引く。
3. **原本は 2-up レイアウト。** 論理ページ→物理ページ = `論理 // SOURCE_PAGES_PER_SHEET`(=2)。
   `rag_source.text_for_pages()` がこの変換を担う。
4. **原本PDF・APIキー・DBはコミットしない**（`.gitignore` 済み・著作権/秘密情報）。
   原本未配置時は観点summaryを根拠にフォールバック（`grounding=summary`）。
5. **進捗は (username, level, unit_id, source) で一意。** `source` 列は残してあるが本リポジトリ
   では常に `'rag'`。10問満点を連続3回で単元クリア。

## アーキテクチャ要点

- `rag_perspectives`（観点ロード/サンプリング）→ `rag_source`（原本テキスト）→
  `rag_generator`（プロンプト構築・LLM呼び出し・JSON検証・リトライ・プロンプトキャッシュ）→
  `rag_session_store`（セッション保存）→ `routes_quiz`。
- LLM呼び出しは `rag_generator.generate(..., llm_call=)` で差し替え可能（テストはモック注入）。
- 出題は `/api/rag/quiz/start`。採点・即時判定は `/api/quiz/check`・`/api/quiz/submit`
  （いずれも `session_id` 必須）。

## コード規約

- コメント・docstring は日本語。エラーは `HTTPException` で 400/403/404/502/503。
- DB操作は `db.py` 経由。フロントのDOM注入は `escapeHtml()` を通す。
- 無音の劣化を避ける（観点/原本が無ければ適切なエラーを返す）。

## 動作確認

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r backend/requirements.txt
.venv/Scripts/python _smoke_backend.py   # RAGはモックLLMで全経路検証（APIキー不要）
```

実LLM疎通は `ANTHROPIC_API_KEY` 設定後にブラウザで確認（トークン課金あり）。

## 既知の制限

- RAG方式に卒業試験は無い（単元出題のみ）。
- ハルシネーション対策の2パス検証（`RAG_VERIFY_PASS`）は枠のみ・既定off。
- Render Free はディスク揮発 → 再デプロイで履歴・RAGセッションが消える（Persistent Disk推奨）。

## 成果物の命名規約

- 配布物（ZIP等）は `YYYYMMDD_<project>_<通し番号2桁>.zip` で命名する。
  - 内容を表す語は入れず、**通し番号**で版を管理する（番号が大きいほど新しい＝最新版が一目で分かる）。
  - 例：`20260604_visa-examination_rag_01.zip`
