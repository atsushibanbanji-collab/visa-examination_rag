"""アプリ全体の設定値・定数。

環境変数依存の設定とマジックナンバーをここへ集約する。RAG出題専用。
"""
import os
from pathlib import Path

# --- パス ---
BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
PERSPECTIVES_DIR = Path(__file__).parent / "perspectives"
SOURCE_DIR = Path(__file__).parent / "source"

# --- 難易度レベル ---
ALLOWED_LEVELS = ("beginner", "intermediate", "advanced")

# --- 管理者トークン（URL難読化のみ。環境変数で差し替え可能）---
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "Kp7vQm2xRt")

# --- 出題・採点のルール ---
UNIT_CLEAR_REQUIRED_STREAK = 3      # 単元クリアに必要な通算満点回数（累計方式。外しても減らない）

# 出題対象の単元（当面、ビザ種別の単元のみに限定する）。
# 永住権(green_card)・ビザの基本(basics)など、ビザ種別でない単元は単元選択画面から除外する。
# データ・観点・プロンプトは保持しており、ここに unit_id を足せば即座に復帰できる（単一の真実源）。
# 環境変数 VISA_TYPE_UNITS にカンマ区切りで指定すると上書き可能。
VISA_TYPE_UNITS = frozenset(
    u.strip()
    for u in os.environ.get(
        "VISA_TYPE_UNITS",
        "b_visa,e_visa,f_visa,h1b_visa,j_visa,l_visa",
    ).split(",")
    if u.strip()
)

# --- RAG出題方式の設定 ---
# ANTHROPIC_API_KEY 未設定なら RAG 出題は 503 を返す。
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
RAG_MODEL = os.environ.get("RAG_MODEL", "claude-haiku-4-5-20251001")
RAG_CHOICES = int(os.environ.get("RAG_CHOICES", "3"))            # 1問あたりの選択肢数（3 or 4）

# レベル別の出題形式（項目5）。
#   yesno   … Yes/No 2択（はい/いいえ固定）。選択式採点を流用する。
#   choice  … 選択式（RAG_CHOICES 択）。選択式採点を流用する。
#   fill_in … 穴埋め（自由記述）。正解候補配列＋正規化で完全一致判定する（新採点系統）。
QUESTION_FORMAT_BY_LEVEL = {
    "beginner": "yesno",
    "intermediate": "choice",
    "advanced": "fill_in",
}
# Yes/No 出題で用いる固定の2択（はい=index0 / いいえ=index1）。
YESNO_CHOICES = ("はい", "いいえ")
RAG_QUESTIONS_PER_QUIZ = int(os.environ.get("RAG_QUESTIONS_PER_QUIZ", "10"))
# 出題開始の体感待ちを縮めるためのヘッド／テイル分割。
# 開始時はまず先頭 RAG_HEAD_COUNT 問だけを生成して即返し（=最初の描画を速く）、
# 残りはユーザーが解いている間に /api/rag/quiz/continue で生成・追記する。
RAG_HEAD_COUNT = int(os.environ.get("RAG_HEAD_COUNT", "3"))
# --- TEST MODE（撤去予定）: 動作確認用テストモードの出題数 ---------------------
# 本番フローを汚さず最小コストで配線を確認する。撤去手順は TODO.md を参照。
RAG_TEST_QUESTIONS = int(os.environ.get("RAG_TEST_QUESTIONS", "2"))
# ------------------------------------------------------------------------------
RAG_SESSION_TTL_SEC = int(os.environ.get("RAG_SESSION_TTL_SEC", "7200"))  # セッション保持（既定2時間）

# --- DEV ONLY（撤去予定）: 管理画面確認用デモデータ生成ボタン -----------------
# ホーム画面右上の「ダミーデータ生成」ボタンと /api/dev/seed-demo を有効化する。
# 構築段階専用。運用移行時は false にするか、routes_dev.py ごと撤去する。
# 撤去箇所: この定数 / backend/routes_dev.py / main.py の include / index.html のボタン
DEMO_SEED_ENABLED = os.environ.get("DEMO_SEED_ENABLED", "true").lower() == "true"
# ------------------------------------------------------------------------------
RAG_VERIFY_PASS = os.environ.get("RAG_VERIFY_PASS", "false").lower() == "true"  # 生成→検証2パス（既定off）
RAG_MAX_TOKENS = int(os.environ.get("RAG_MAX_TOKENS", "4000"))
# 原本PDFは2-up（1物理ページに論理2ページ）。論理ページ→物理ページの除数。
SOURCE_PDF_PATH = SOURCE_DIR / "visa_guide_v22_1.pdf"
SOURCE_TXT_PATH = SOURCE_DIR / "visa_guide_v22_1.txt"
SOURCE_PAGES_PER_SHEET = int(os.environ.get("SOURCE_PAGES_PER_SHEET", "2"))
