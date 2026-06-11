# TODO（将来対応・本作業では未実装）

## 認証のメール＋パスワード化

- **現状の課題**：ユーザー識別を姓名ベースのURLパラメータ（`?user=<姓名>`）で行っているため、
  姓名が分かれば他人の受験画面・進捗を閲覧できてしまう脆弱性がある。
- **将来の方針**：メールアドレス（ID）＋パスワードによるログイン認証へ移行する。
  - 移行先DBは**マネージドPostgres想定**（Render Postgres / Neon / Supabase。認証built-inの点でSupabase有力）。
    自動バックアップ・時点復旧があり、Render Freeのディスク揮発問題が消える。
  - **移行作業の本体は username（氏名文字列）→ user_id（usersテーブルのFK）へのマッピング**。
    attempts / unit_progress は現在 username 文字列をキーにしているため、users テーブル新設後に
    両テーブルへ user_id 列を追加し、氏名で突合して埋める。同姓同名は手動解決。
  - SQL を db.py に集約する現行規律は移行コストを直接下げるので維持すること。
  - 受験者向けマイページ／管理者向け全ユーザー総括ページは、この認証・DB移行の後段で実装する。
- **本作業での扱い**：テスト中につき据え置き（認証を入れるとテストが煩雑になるため）。
  コードには手を加えていない。本ファイルは将来の備忘として記録するもの。

## その他（既出の将来TODO）

- レベル別の出題範囲制限（例：初級では永住権を出さない 等）。現状は全レベル一律でビザ種別単元のみ。
- prompt caching の本番投入（実装済み・未テスト）。
- 除外中の単元（永住権・ビザの基本など）の出題対象への復帰可否の判断。
  データ・観点・プロンプトは保持済み。`config.VISA_TYPE_UNITS` の調整で復帰できる。

## 撤去予定機能の標識と手順（運用移行時）

コード中の `TEST MODE（撤去予定）` / `DEV ONLY（撤去予定）` コメントが標識。grep で全箇所を列挙できる。

### テストモードの撤去手順
撤去順序は **テストモード → 認証化** とする（氏名「テストモード」起動が氏名識別に依存しているため。
先に認証化すると「テストモード」名で登録したユーザーの出題が2問になる地雷が残る）。

1. `backend/routes_quiz.py`：start の is_test 判定ブロックと submit の test フラグ記録ブロックを削除
   （`is_test` 参照箇所ごと。details の test キーも書かなくなる）
2. `backend/models.py`：StartRequest の `test` フィールドを削除
3. `backend/rag_generator.py`：`test_mode` 引数（2関数）とテストモード分岐・ダミープロンプトを削除
4. `backend/config.py`：`RAG_TEST_QUESTIONS` を削除
5. `frontend/assets/quiz.js`：testMode 判定と start ボディの `test` を削除
6. `frontend/result.html`：テストモード表示ラベルのブロックを削除
7. `_smoke_backend.py`：テストモード関連ケースを削除・更新
8. 既存データ：details.meta.test / seeded が true の attempts と、対応する unit_progress の扱いを決めて掃除

### デモデータ生成ボタンの撤去手順
`backend/routes_dev.py` 冒頭のコメントに記載（ファイル削除＋main.py の include＋index.html のブロック＋config のフラグ）。
