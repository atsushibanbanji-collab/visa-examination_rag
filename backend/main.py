"""ビザ検定（RAG比較版） - FastAPI アプリ組み立て。

責務はアプリの組み立てと配線のみ:
  - CORS / 起動時のデータロード・DB初期化
  - 受験系ルーター（routes_quiz）と管理系ルーター（routes_admin）の登録
  - フロントの静的配信

原本PDF＋観点メタ（perspectives）から、出題のたびにLLMが問題を生成する。
個々のエンドポイントの実装は routes_quiz.py / routes_admin.py に、
RAGの観点メタは rag_perspectives.py に分離。
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend import db
from backend import rag_perspectives
from backend.config import FRONTEND_DIR
from backend.routes_admin import router as admin_router
from backend.routes_dev import router as dev_router  # DEV ONLY（撤去予定）
from backend.routes_quiz import router as quiz_router

# --- アプリ ---
app = FastAPI(title="ビザ検定（RAG出題）", description="RAG出題によるビザ知識検定", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 起動時の初期化 ---
rag_perspectives.load()   # 観点メタ（perspectives/*.json）をメモリへ
db.init_db()              # SQLite スキーマ初期化

# --- ルーター登録 ---
app.include_router(quiz_router)
app.include_router(admin_router)
app.include_router(dev_router)  # DEV ONLY（撤去予定）: デモデータ生成

# --- フロントの静的配信（必ず最後にマウント）---
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
