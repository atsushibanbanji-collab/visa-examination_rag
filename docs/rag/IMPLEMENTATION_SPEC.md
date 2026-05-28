# ビザ検定 RAG出題方式 実装指示書（Claude Code 向け）

## このパッケージの目的
ビザ検定アプリに**RAG出題方式**を追加し、既存の**固定プール方式**と比較できるようにする。
両方式を切り替え可能にし、同一UI・同一採点ロジックで並走させ、出題品質・難度安定性・
コスト・レイテンシを比較するのが最終ゴール。

このパッケージはRAG側の実装一式。Claude Code は本書に従って実装し、Gitにコミットすること。

---

## 前提・既存コードベース
- バックエンド: FastAPI（main.py が config/questions_store/services/routes_quiz/routes_admin に分割済み）
- フロント: Vanilla JS
- DB: attempts（受験履歴）+ unit_progress（単元進捗）。卒業試験は unit_id=`__graduation__`
- 既存の固定プール: questions.json（_units にセル定義、各levelに問題配列）
- 単元構成（10セル対象）:
  - beginner: basics(40) / b_visa(20) / e_visa(20) / l_visa(20) / h1b_visa(20) / f_visa(20) / j_visa(20) / green_card(20)
  - intermediate: basics(20)
  - advanced: basics(20)

## このパッケージに含まれるもの
- `perspectives/*.json` … 観点メタ10ファイル（計188観点）。出題の論点リスト。
- `perspectives/PERSPECTIVES_SPEC.md` … 観点メタの作成仕様（拡張時に参照）
- 原本PDF「米国ビザ申請の手引き Ver.22.1」… **このZIPには含まない**。別途配置すること（下記）。

---

## アーキテクチャ（方式A：都度生成RAG）

```
原本PDF（不変・システムプロンプトに埋め込み or 添付）
  +  観点メタ（perspectives/{level}_{unit}.json）
  +  難度（level）
  +  単元（unit_id）
        ↓  /api/quiz/start のたびにLLMが生成
   10問（4択 + 正答 + 解説 + 使用した観点id）
        ↓
   セッション問題プール（一時保持テーブル）
        ↓
   採点（/api/quiz/check）・進捗（既存 attempts / unit_progress）
```

### 出題フロー
1. `/api/quiz/start?level=&unit=` を受ける。
2. 対象セルの観点メタを読み込み、観点を**10個サンプリング**（重複なし）。
3. LLMに「原本＋サンプリングした観点＋難度＋単元」を渡し、観点1つにつき1問、計10問を生成させる。
   - 各問は該当観点の `source_pages` の記述に基づいて作る（原本外への逸脱を禁止）。
   - 出力は厳格なJSON（前後の説明文・Markdownフェンス禁止）。
4. 生成10問をセッション問題プールに保存し、問題IDを採番してフロントへ返す（正答・解説は返さない）。
5. `/api/quiz/check` で回答を受け、セッションプールの正答辞書で採点、解説を返す。
6. 結果を attempts に記録、クリア条件を満たせば unit_progress を更新。

---

## 設計上の決定事項（実装前に必ず確認）

### D1. 生成問題のID寿命 ★要オーナー承認
既存 attempts は b001 等の**永続ID**を参照する設計。RANはその場生成のため永続IDがない。
- 推奨案: `quiz_sessions` テーブルを新設し、生成10問を session_id 紐付けで一時保存。
  問題IDは `sess_{uuid}_{n}` 形式。attempts には「session_id + 生成問題スナップショット(JSON)」を保存し、
  永続問題IDへの外部キーは張らない（固定プール方式と混在させるため、attempts に `source`= 'pool' | 'rag' 列を追加）。
- セッションプールの保持期間: 受験完了 or TTL（例 2時間）で破棄。
- **この案でよいか、attempts スキーマ変更を許可するか、オーナーに確認すること。**

### D2. 採点経路
- 固定プール: 既存どおり questions.json の正答辞書を引く。
- RAG: セッションプールの一時正答辞書を引く。
- 採点関数は `source` で分岐。共通インターフェース `get_answer_key(session_or_pool, qid)` に寄せる。

### D3. クリア条件「10/10連続3回」の再定義 ★要オーナー承認
固定プールは「同じ難度の問題を3回満点」が暗黙前提だった。RAGは毎回問題が違う。
- 観点メタで難度ブレを抑える前提で、「10/10連続3回」をそのまま流用する案を推奨。
- ただし RAG は観点サンプリングのシードを記録し、再現・監査できるようにする（attempts に seed/観点idリストを保存）。
- **この解釈でよいか確認すること。**

### D4. ハルシネーション対策（段階導入）
- フェーズ1（まず動かす）: 生成時に「観点の source_pages の記述に基づき、原本にない事実を作らない」と強く指示。
- フェーズ2（任意・コスト2倍）: 生成→検証の2パス。別呼びで「各問が原本に照らして正しいか/難度が妥当か」を採点し、外れを差し替え。
  - フェーズ2は**フラグで切替**にする（`RAG_VERIFY_PASS=true/false`）。既定 false。

### D5. 比較計測
両方式で以下をログ:
- 生成/取得レイテンシ、トークン消費（RAGのみ）、難度自己採点スコア（フェーズ2時）、ユーザー正答率、観点カバレッジ。
- 比較用エンドポイント or 管理画面に最低限の集計を出す（任意、優先度低）。

---

## API 仕様（RAG側・既存と整合させる）

### POST /api/quiz/start
req: `{ "mode": "rag", "level": "beginner", "unit": "b_visa" }`
res: `{ "session_id": "...", "questions": [ { "qid": "...", "question": "...", "choices": ["..."] }, ... ] }`
※ 正答・解説は返さない。

### POST /api/quiz/check
req: `{ "session_id": "...", "answers": [ { "qid": "...", "choice_index": 1 }, ... ] }`
res: `{ "score": 8, "total": 10, "results": [ { "qid": "...", "correct": true, "answer_index": 1, "explanation": "...", "perspective_id": "bv03", "source_pages": [21] }, ... ] }`

### 既存 /api/examples（サポートレター側ツール）とは無関係。混同しないこと。

---

## LLM 生成プロンプト雛形（実装で使う）
システム:
```
あなたは米国ビザ実務の検定問題を作成する専門家。
以下の「原本」に明記された内容のみに基づき、4択問題を作成する。
原本にない事実・数値・条文を創作してはならない。
難度は指定レベルに厳密に合わせる。
出力は指定JSONのみ。前後の説明やMarkdownフェンスを一切付けない。
```
ユーザー:
```
# 原本
{原本PDFの該当範囲テキスト}

# 難度
{level} … {level_description}

# 単元
{unit_name}

# 出題する観点（各観点につき1問、計{N}問）
- {perspective.id}: {perspective.name} / {perspective.summary} / 根拠ページ {source_pages}
  ...

# 出力JSON形式
{ "questions": [ { "perspective_id": "...", "question": "...",
  "choices": ["...","...","..."], "answer_index": 0, "explanation": "...",
  "source_pages": [..] }, ... ] }
```
- choices は3〜4択（既存プールは3択が混在。フロントに合わせ既定3択、設定で4択可）。
- answer_index は0始まり。
- 生成後にJSONパースを検証し、失敗時はリトライ（最大2回）。

---

## モデル / 設定
- 既定モデルは Claude Haiku 系（プロンプトキャッシュ前提でコスト最適）。原本テキストはキャッシュ対象に置く。
- 環境変数: `ANTHROPIC_API_KEY`, `RAG_MODEL`, `RAG_VERIFY_PASS`, `RAG_CHOICES`(=3|4), `RAG_SESSION_TTL_SEC`。
- 原本PDF配置: `backend/source/visa_guide_v22_1.pdf`（ZIP非同梱。手動配置）。
  - テキスト抽出済みを `backend/source/visa_guide_v22_1.txt` に置く運用でも可。実装はどちらかに対応。

---

## ディレクトリ構成（RAG追加分の想定）
```
backend/
  source/                 # 原本（gitignore対象。実体はコミットしない）
  perspectives/           # 本パッケージの観点メタをここへ配置
  services/
    rag_generator.py      # 観点サンプリング + LLM生成 + JSON検証
    rag_session_store.py  # セッション問題プール（D1）
  routes_quiz.py          # mode=rag 分岐を追加
config/
  settings.py             # 上記環境変数
```

---

## 実装タスク（順序）
1. `perspectives/` を backend に配置、ローダ実装（観点メタ読込 + サンプリング）。
2. D1〜D3 をオーナーに確認 → 確定後に quiz_sessions / attempts スキーマ変更。
3. `rag_generator.py`: サンプリング→プロンプト構築→LLM呼び出し→JSON検証→リトライ。
4. `rag_session_store.py`: セッション保存・TTL破棄。
5. `routes_quiz.py`: `mode` で固定プール/RAGを分岐。`/start` `/check` を両対応に。
6. 採点・進捗を既存ロジックに接続（`source` 列で分岐）。
7. フェーズ2検証パスを **フラグ off** で枠だけ用意。
8. 比較計測ログ（D5）を最小限。
9. README に切替方法と比較手順を記載。

## 禁止事項 / 注意（CLAUDE.md準拠）
- **事前承認のない仕様変更・スキーマ変更を行わない**（D1/D3は特に要承認）。
- 対症療法的な握りつぶし（例外を握って握って黙って空配列を返す等）を禁止。原因を特定して直す。
- 既存の固定プール方式・サポートレター作成支援ツール・/api/examples を壊さない。
- 原本PDFはGitにコミットしない（.gitignore に追加）。著作権に配慮。

---

## 比較実験の運び方（参考）
- 同一セル（例: beginner/b_visa）で、固定プール方式とRAG方式をそれぞれN回受験。
- 指標: ユーザー正答率分布、難度の体感ブレ、1出題あたりトークン/コスト、レイテンシ、観点カバレッジ。
- RAGは seed と観点idリストを attempts に残し、後から再現・検証できるようにする。
