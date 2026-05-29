"""管理者用エンドポイント（URLトークンで難読化のみ）。

  GET  /api/{token}/admin/attempts          全受験履歴
  GET  /api/{token}/admin/users             ユーザー集計

RAG出題専用のため、固定プールのCSV管理・プールメタ・方式比較は持たない。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend import db
from backend.config import ADMIN_TOKEN

router = APIRouter()


def _check_token(token: str) -> None:
    if token != ADMIN_TOKEN:
        raise HTTPException(404)


@router.get("/api/{token}/admin/attempts")
def admin_attempts(token: str):
    _check_token(token)
    return {"attempts": db.get_all_attempts()}


@router.get("/api/{token}/admin/users")
def admin_users(token: str):
    _check_token(token)
    return {"users": db.get_user_summary()}
