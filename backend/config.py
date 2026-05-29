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
UNIT_CLEAR_REQUIRED_STREAK = 3      # 単元クリアに必要な連続満点回数

# --- RAG出題方式の設定 ---
# ANTHROPIC_API_KEY 未設定なら RAG 出題は 503 を返す。
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
RAG_MODEL = os.environ.get("RAG_MODEL", "claude-haiku-4-5-20251001")
RAG_CHOICES = int(os.environ.get("RAG_CHOICES", "3"))            # 1問あたりの選択肢数（3 or 4）
RAG_QUESTIONS_PER_QUIZ = int(os.environ.get("RAG_QUESTIONS_PER_QUIZ", "10"))
RAG_SESSION_TTL_SEC = int(os.environ.get("RAG_SESSION_TTL_SEC", "7200"))  # セッション保持（既定2時間）
RAG_VERIFY_PASS = os.environ.get("RAG_VERIFY_PASS", "false").lower() == "true"  # 生成→検証2パス（既定off）
RAG_MAX_TOKENS = int(os.environ.get("RAG_MAX_TOKENS", "4000"))
# 原本PDFは2-up（1物理ページに論理2ページ）。論理ページ→物理ページの除数。
SOURCE_PDF_PATH = SOURCE_DIR / "visa_guide_v22_1.pdf"
SOURCE_TXT_PATH = SOURCE_DIR / "visa_guide_v22_1.txt"
SOURCE_PAGES_PER_SHEET = int(os.environ.get("SOURCE_PAGES_PER_SHEET", "2"))
