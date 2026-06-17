# チャレンジ（異議申し立て）機能 要件書ドラフト v1

原本＋観点メタからLLMが毎回出題するRAG方式（本リポジトリ）に、**出題・採点への異議申し立て
（チャレンジ）**機構を足すための要件書。実装は `feature/challenge` ブランチで行う想定。
本書は要件の確定版（v1）であり、実装着手は別途承認を得てから行う。

> 本書は CLAUDE.md の「触ってはいけない設計判断」を前提に書いている。特に
> ①RAG正答をフロントに返さない（出題時）、⑤進捗の一意性と user_id 紐付け、
> ⑥受験系・履歴・マイページはログイン必須、に整合させること。

---

## 1. 目的

1. 受験者が、出題された問題そのものや採点結果に**異議**を申し立てられるようにする。
   - ① 採点（正誤判定）への異議（「この判定はおかしい」）
   - ② 出題内容への異議（「設問が事実と違う／不適切」）
   - 両方を1機能で扱う。認容時は一律「正解扱い」に訂正する。
2. 管理者がサイト内で**認容／却下**を裁定し、認容時は当該受験者の採点を遡及訂正する。
3. 認容された異議は、**不適切な問題が再生成される余地**を示すため、根本原因（観点メタ／
   システムプロンプト）の是正につなげる。是正自体はサイト外（Git push）で行い、痕跡を残す。

## 2. スコープ（v1）

- 含む：起票（受験中の解説パネル）／管理画面での裁定・採点遡及訂正／マイページでの結果表示
  と管理者メッセージ／観点・プロンプト是正の追跡（手動クローズ）。
- 含まない：観点JSON・システムプロンプトのサイト上編集（Git push 運用のため）。メール通知
  基盤（auth と同様に持たない）。RAG以外の出題方式（固定プール版は別リポジトリ）。

## 3. 用語・ステータスモデル

ステータスは内部コード（英語）と表示ラベル（日本語）を対応させる。

| 内部コード | 表示ラベル | 意味 | 終端 |
|---|---|---|---|
| `open` | 未処理 | 起票直後・未裁定 | × |
| `accepted` | 未修正 | 認容済み。**採点訂正は実行済み**、根本原因の是正待ち | × |
| `closed` | クローズ | 認容案件の終端。管理者が**手動で**立てる（是正を Git 反映後、または是正不要と判断後） | ○ |
| `rejected` | 却下 | 却下・終端 | ○ |

### 遷移（すべて管理画面の操作。採点訂正の発火点に注意）

```
            ┌──────────── 認容(accept) ───────────┐
            │  ※この瞬間に採点を遡及訂正          ▼
  [未処理 open] ───── 却下(reject) ─────▶ [却下 rejected]（終端）
            │                                  [未修正 accepted]
            │                                       │
            │                          手動クローズ(close)
            │                                       ▼
            └──────────────────────────────▶ [クローズ closed]（終端）
```

- **採点の遡及訂正は `open → accepted`（認容）の瞬間に一度だけ**実行する。`accepted → closed`
  は是正作業の完了を示す手動フラグで、採点には影響しない。
- 認容案件は必ず `closed` で閉じる（「是正不要」も `closed`＋対応メモに理由を記録）。
- `closed` / `rejected` は終端。再オープンはサイト上では行わない。

## 4. ルール

- **起点は受験中の解説パネルのみ。** `/api/quiz/check` 後に表示される正誤＋解説パネルに
  「異議を申し立てる」ボタンを置く（結果画面・履歴からは起票しない）。
- **件数制限なし。** 1人がその受験の10問すべてに起票してもよい。
- **1設問×1ユーザー＝1回のみ。** 同一設問への再起票は不可。**却下後も再起票できない**
  （DBの `UNIQUE(user_id, question_id)` で担保）。
- **認容の効果（採点訂正）：** 当該設問を「正解扱い」に訂正 → その受験（attempt）の score を
  再計算 → **満点化した場合のみ `perfect_count` を +1**（単元クリア＝通算3回満点に反映）。
  `streak_count` は触らない（情報用でクリア判定に不使用）。冪等に行う。
- **受験者への通知あり。** マイページに自分のチャレンジ一覧（ステータス・裁定結果）を表示し、
  **管理者メッセージ**を添えられる。
- **認証：** 起票・マイページはログイン必須（`auth.get_current_user`、user_id はセッションから。
  リクエストボディで username を受けない）。管理APIは既存どおりURLトークン。
- **source は常に `'rag'`。**

## 5. データモデル

### 5.1 新テーブル `challenges`

`db.py` 経由で SQLite / PostgreSQL 両対応（`?` プレースホルダ、AUTOINCREMENT/BIGSERIAL は
既存テーブルと同じ方式で分岐）。既存を壊さず `CREATE TABLE IF NOT EXISTS` で足す。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | PK | 連番 |
| `user_id` | int | 起票者（本人紐付け。design rule #5/#6） |
| `username` | text | メールアドレス（互換のため保持） |
| `session_id` | text | 起票時のクイズセッション |
| `question_id` | text | `sess_<uuid>#<n>` 形式 |
| `attempt_id` | int NULL | 確定受験への紐付け。submit 時に backfill（中断時は NULL のまま） |
| `level` | text | 出題レベル |
| `unit_id` | text | 単元 |
| `source` | text | 既定 `'rag'` |
| `kind` | text | `grading`／`content`／`both`（①②の分類。任意） |
| `reason` | text | 申し立て理由（受験者入力・必須） |
| `snapshot` | text(JSON) | **設問スナップショット**（下記）。起票時にサーバ側で生成 |
| `status` | text | `open`／`accepted`／`closed`／`rejected`（既定 `open`） |
| `scoring_applied` | int | 採点訂正の適用済みフラグ（冪等担保。既定 0） |
| `admin_message` | text NULL | 受験者向けメッセージ |
| `admin_note` | text NULL | 対応メモ（内部・痕跡。是正内容や「是正不要：理由」） |
| `created_at` | text | 起票時刻 |
| `resolved_at` | text NULL | 認容/却下の時刻 |
| `closed_at` | text NULL | クローズ時刻 |

制約：`UNIQUE(user_id, question_id)`（1設問×1ユーザー＝1件・再起票不可）。
インデックス：`status`、`user_id`、`attempt_id`。

### 5.2 スナップショット（snapshot JSON）

設問本文・正答・解説は ephemeral な `quiz_sessions`（期限切れで消滅）にしか無く、attempt の
`details` には各設問の `is_correct` までしか残らない。よって**起票時に必ずスナップショットを
取る**（取らないと後で審査も採点訂正もできない）。

```json
{
  "question": "設問本文",
  "choices": ["..."],
  "type": "single|multi|fill_in",
  "correct_choice": 2,
  "correct_answers": ["..."],
  "explanation": "解説",
  "perspective_id": "観点ID",
  "user_choice": 1,
  "user_text_answers": ["..."]
}
```

スナップショットの正答・解説は**管理画面のみ**で表示する。受験者は起票時点で `/api/quiz/check`
の応答により既に正答・解説を見ているため、マイページでの自分のチャレンジ表示に解説を再掲する
のは整合的（出題時にRAG正答を返さないという設計判断は維持される）。

### 5.3 attempt との紐付け

起票は受験中（submit 前）に起きるため、その時点では attempt は存在しない。
`challenges.session_id` を保持し、**submit が attempt を保存した直後に、同一 session_id の
未紐付けチャレンジへ `attempt_id` を backfill** する（`link_challenges_to_attempt`）。
中断（submit 前に離脱）した場合は `attempt_id` が NULL のまま残り、認容しても採点反映は無い
（スナップショットに基づく原因是正のみ可能）。

## 6. API 設計

### 6.1 受験者向け（ログイン必須・`auth.get_current_user`）

- **POST `/api/quiz/challenge`** — 起票
  - body：`{ session_id, question_id, reason, kind? }`
  - 検証：セッション存在／当該設問がセッションに属する／同一 `(user_id, question_id)` の既存が
    無い（あれば 409）。
  - 処理：セッションから snapshot を生成し `status=open` で保存。正答等は返さない
    （応答は受理可否と challenge id のみ）。
- **GET `/api/mypage/challenges`** — 自分のチャレンジ一覧
  - 返す：設問要約・ステータス（表示ラベル）・裁定結果・`admin_message`・作成/裁定時刻。
  - マイページ（mypage.html）に表示。

### 6.2 管理者向け（既存 routes_admin の規約＝URLトークン）

- **GET `/admin/.../challenges?status=`** — 一覧（ステータスでフィルタ）
- **GET `/admin/.../challenges/{id}`** — 詳細（snapshot 全体を表示）
- **POST `/admin/.../challenges/{id}/accept`** — 認容　body：`{ admin_message?, admin_note? }`
  - `open → accepted`、`resolved_at` を記録、**採点遡及訂正を実行**（§7）。
- **POST `/admin/.../challenges/{id}/reject`** — 却下　body：`{ admin_message?, admin_note? }`
  - `open → rejected`、`resolved_at` を記録（採点は変えない）。
- **POST `/admin/.../challenges/{id}/close`** — クローズ　body：`{ admin_note? }`
  - `accepted → closed`、`closed_at` を記録（是正を Git 反映後に管理者が手動で実施）。

（正確なパス接頭辞は既存 routes_admin に合わせる。エラーは HTTPException で 400/403/404/409。）

## 7. 採点の遡及訂正ロジック（認容時・冪等）

```
accept(challenge, admin_message?, admin_note?):
  if challenge.status != "open": 400  # open のみ認容可
  challenge.status = "accepted"; challenge.resolved_at = now
  challenge.admin_message = admin_message; challenge.admin_note = admin_note
  if challenge.attempt_id is not None and challenge.scoring_applied == 0:
      attempt  = get_attempt(challenge.attempt_id)
      details  = json.loads(attempt.details)
      ans      = find a in details.answers where a.id == challenge.question_id
      if ans is not None and ans.is_correct == false:
          ans.is_correct = true
          new_score   = count(a.is_correct == true for a in details.answers)
          was_perfect = (attempt.score == attempt.total)
          if new_score > attempt.score:
              update_attempt(attempt_id, score=new_score, details=json(details))
              if new_score == attempt.total and not was_perfect:
                  increment_perfect_count(user_id, level, unit_id, source)  # +1・クリア再評価
      challenge.scoring_applied = 1
  save(challenge)
```

- **冪等性**：`status==open` でしか認容できず、`scoring_applied` で二重加算を防ぐ。
- **同一受験に複数の認容**：各チャレンジが自分の設問のみ反転。`perfect_count` は score が
  「非満点→満点」に**跨いだ時だけ +1**（既に満点なら増えない）ため二重計上しない。
- **②内容への異議で、元々正解だった設問**：`is_correct` は既に true なので採点変化なし。
  認容は原因是正の追跡のために成立する。
- **`increment_perfect_count`** は専用関数として新設（`update_unit_progress` は submit 用の
  streak リセット等を含むため流用しない）。`perfect_count += 1` と単元クリア（通算3満点）の
  再評価のみ行い、`streak_count` は触らない。クリア閾値の真実源は config に一本化。

## 8. フロント

- **quiz.html / assets/quiz.js**：解説パネル（`#feedback`）に「異議を申し立てる」ボタンを追加。
  押下でモーダル（種別 ①/② のラジオ＋理由テキスト）→ `/api/quiz/challenge` を POST。
  起票済みの設問はボタンを無効化（重複防止のUI）。DOM注入は `escapeHtml()` を通す。
- **mypage.html**：自分のチャレンジ一覧（設問要約・ステータスラベル・裁定結果・管理者メッセージ）。
- **admin-*.html / assets/admin.js**：異議一覧（ステータスフィルタ）・詳細（snapshot 表示）・
  認容/却下（メッセージ入力可）・未修正→クローズの手動切替・対応メモ編集。

## 9. CLAUDE.md 設計判断との整合チェック

| 設計判断 | 対応 |
|---|---|
| ①RAG正答をフロントに返さない（出題時） | 維持。snapshot は管理画面のみ。起票応答に正答を載せない |
| ②問題IDは `sess_<uuid>#<n>` | `challenges.question_id` にそのまま保持 |
| ⑤進捗は (username, level, unit_id, source) で一意・user_id 紐付け | username=メール、user_id を併記。`increment_perfect_count` も同方式 |
| ⑥受験系・履歴・マイページはログイン必須 | 起票・マイページは `get_current_user`。username はボディで受けない |
| 無音の劣化を避ける | セッション期限切れ・重複起票は適切なHTTPエラー（404/409）を返す |

## 10. エッジケース・非機能

- **中断起票**（submit 前離脱）：`attempt_id` NULL。認容しても採点反映なしだが原因是正は可能。仕様。
- **セッション期限切れ後の起票**：404。受験中の操作のため通常は起きない。
- **是正はサイト外（Git）**：観点JSON／システムプロンプトの修正は開発者がファイル編集→push。
  クローズは管理者の手動操作。`admin_note` に痕跡を残す。
- **通知の実体**：メール基盤は無い。マイページ表示＋管理者メッセージで完結。

## 11. 実装タスク分解（feature/challenge・着手は承認後）

1. `db.py`：`challenges` DDL（両方言）＋ CRUD（create/get/list/list_by_user/
   link_challenges_to_attempt/accept/reject/close/update_attempt_score_details/
   increment_perfect_count）。
2. `models.py`：起票・裁定のリクエスト/レスポンス型。
3. `routes_quiz.py`：`POST /api/quiz/challenge`。submit に `link_challenges_to_attempt` を追加。
4. マイページAPI：`GET /api/mypage/challenges`（routes_auth もしくは新 routes_mypage）。
5. `routes_admin.py`：一覧・詳細・accept/reject/close・メッセージ/メモ。
6. `frontend/quiz.html` / `assets/quiz.js`：起票ボタン＋モーダル。
7. `frontend/mypage.html`：自分のチャレンジ一覧。
8. `frontend/admin-*.html` / `assets/admin.js`：裁定UI。
9. `_smoke_backend.py`：ライフサイクル試験（起票→submit紐付け→認容で採点訂正＆満点で
   `perfect_count`+1→クローズ／却下→`UNIQUE` で再起票不可／中断起票は採点反映なし）。
10. ドキュメント更新（README・本書）。

## 12. 確定事項（要件決定の記録）

- 起点：受験中の解説パネルのみ。
- 対象：①採点 ②内容、両方。認容は一律「正解扱い」。
- ステータス：未処理 / 未修正 / クローズ / 却下（「修正済み」→「クローズ」に改名）。是正は
  サイト外、クローズは手動。認容は必ずクローズで閉じる。
- 重複：件数制限なし。1設問1ユーザー1回・却下後も再起票不可。
- 採点：認容で満点化したら通算満点（`perfect_count`）に+1（単元クリアに反映）。streak は不変。
- 通知：あり。管理者メッセージを添付可。
