# TODO（将来対応・本作業では未実装）

## 認証のメール＋パスワード化 → **実装済み（feature/auth）**

- メール＋パスワードの自由登録・ログイン（HttpOnly Cookieセッション・30日）、マイページ、
  管理画面のアカウント単位管理、管理者によるパスワード再設定まで実装済み。
- 残課題:
  - 旧・氏名運用データ（user_id が NULL の attempts / unit_progress）は管理画面に表示されない。
    本番マージ時に削除するか残置するかを決める（構築段階のテストデータのみのため削除推奨）。
  - Cookie の Secure 属性は未付与（ローカルHTTP開発との両立のため）。本番HTTPS固定にする際は付与を検討。
  - 管理画面自体の認証は引き続きURLトークンのみ。社外公開時はBasic認証・IP制限等を追加すること。

## その他（既出の将来TODO）

- レベル別の出題範囲制限（例：初級では永住権を出さない 等）。現状は全レベル一律でビザ種別単元のみ。
- prompt caching の本番投入（実装済み・未テスト）。
- 除外中の単元（永住権・ビザの基本など）の出題対象への復帰可否の判断。
  データ・観点・プロンプトは保持済み。`config.VISA_TYPE_UNITS` の調整で復帰できる。

## 既知バグ（未修正・修正は要事前承認）

- **テイル生成に test_mode が伝播しない**：`/api/rag/quiz/continue` は
  `rag_generator.generate_questions` へ `test_mode` を渡していない。
  現状の既定値（`RAG_TEST_QUESTIONS=2` ≦ `RAG_HEAD_COUNT=3`）ではテストモードの
  テイルが常に空のため顕在化しないが、`RAG_TEST_QUESTIONS` をヘッド数より大きく
  設定すると、テイル分が本番同等の生成（原本参照・コスト発生）になる。
  修正時はセッション meta の test フラグを continue 側で読み、生成へ引き渡すこと。
  ※テストモード自体が撤去予定のため、撤去が先行するなら修正不要となる。

## 撤去予定機能の標識と手順（運用移行時）

コード中の `TEST MODE（撤去予定）` / `DEV ONLY（撤去予定）` コメントが標識。grep で全箇所を列挙できる。

### テストモードの撤去手順 → **撤去済み（feature/auth で完了）**
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
