"""ビザ検定（RAG比較版） - FastAPI アプリ組み立て。

責務はアプリの組み立てと配線のみ:
  - CORS / 起動時のデータロード・DB初期化
  - 受験系ルーター（routes_quiz）と管理系ルーター（routes_admin）の登録
  - フロントの静的配信

固定プール方式（questions.json）と RAG方式（perspectives + 原本）を
同一UI・同一採点で並走させ、出題品質・難度安定性・コスト・レイテンシを比較する。
個々のエンドポイントの実装は routes_quiz.py / routes_admin.py に、
固定プールの状態管理は questions_store.py に、RAGの観点メタは rag_perspectives.py に分離。
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend import db
from backend import questions_store as store
from backend import rag_perspectives
from backend.config import FRONTEND_DIR
from backend.routes_admin import router as admin_router
from backend.routes_quiz import router as quiz_router

# --- アプリ ---
app = FastAPI(title="ビザ検定（RAG比較版）", description="固定プール vs RAG 比較", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 起動時の初期化 ---
store.load()              # questions.json をメモリへ
rag_perspectives.load()   # 観点メタ（perspectives/*.json）をメモリへ
db.init_db()              # SQLite スキーマ初期化

# --- ルーター登録 ---
app.include_router(quiz_router)
app.include_router(admin_router)

# --- フロントの静的配信（必ず最後にマウント）---
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
