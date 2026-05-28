# visa-rag-package

ビザ検定アプリの **RAG出題方式** 実装パッケージ。
既存の **固定プール方式** と並走・比較できるようにすることが目的。

## 中身
```
IMPLEMENTATION_SPEC.md     ← Claude Code への実装指示書（まずこれを読む）
GIT_WORKFLOW.md            ← Git へのプッシュ手順
perspectives/
  PERSPECTIVES_SPEC.md     ← 観点メタの作成仕様（拡張時に参照）
  beginner_basics.json     ← 観点メタ 10ファイル / 計188観点
  beginner_b_visa.json
  beginner_e_visa.json
  beginner_l_visa.json
  beginner_h1b_visa.json
  beginner_f_visa.json
  beginner_j_visa.json
  beginner_green_card.json
  intermediate_basics.json
  advanced_basics.json
source/
  PUT_ORIGINAL_PDF_HERE.txt ← 原本PDFの配置場所の案内（PDF自体は非同梱）
.gitignore
```

## クイックスタート（Claude Code）
1. `IMPLEMENTATION_SPEC.md` を読む。
2. 設計上の決定事項 D1〜D3（ID寿命・採点経路・クリア条件）をオーナーに確認する。
3. 原本PDFを `backend/source/` に配置（このZIPには含まれない）。
4. 指示書「実装タスク」の順に実装し、各ステップでコミットする。

## 比較のゴール
固定プール方式 vs RAG方式を、同一UI・同一採点で並走させ、
出題品質・難度安定性・コスト・レイテンシを比較する。
