"""管理者用エンドポイント（URLトークンで難読化のみ）。

  GET  /api/{token}/admin/users             受験者一覧（名前＋単元別進捗・クリア数降順）
  GET  /api/{token}/admin/history?username= 個別の受験履歴（得点は返さず正答率のみ）

RAG出題専用。サマリー・受験回数・最高点・平均点・全件履歴は廃止した
（受験者ごとの単元クリア状況の把握と、個別履歴の正答率確認に絞る）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend import db, rag_perspectives
from backend.config import ADMIN_TOKEN, UNIT_CLEAR_REQUIRED_STREAK
from backend.db import SOURCE_RAG

router = APIRouter()


def _check_token(token: str) -> None:
    if token != ADMIN_TOKEN:
        raise HTTPException(404)


def _unit_name_map() -> dict:
    """unit_id → 表示名。除外単元（永住権等）の履歴も名前を引けるよう全観点から作る。"""
    m = {}
    for c in rag_perspectives.available_cells():
        m[c["unit_id"]] = c.get("unit_name", c["unit_id"])
    return m


@router.get("/api/{token}/admin/users")
def admin_users(token: str):
    """受験者一覧。各受験者の単元別進捗（通算満点 / クリア状況）と、
    クリア済み単元の総数を返す。クリア数の降順、同数は受験者名の昇順で並べる。
    """
    _check_token(token)
    name_map = _unit_name_map()
    rows = db.get_all_unit_progress(source=SOURCE_RAG)

    by_user: dict = {}
    for r in rows:
        bucket = by_user.setdefault(r["username"], [])
        cleared = r.get("graduated_at") is not None or \
            r.get("perfect_count", 0) >= UNIT_CLEAR_REQUIRED_STREAK
        bucket.append(
            {
                "level": r["level"],
                "unit_id": r["unit_id"],
                "unit_name": name_map.get(r["unit_id"], r["unit_id"]),
                "perfect_count": r.get("perfect_count", 0),
                "required": UNIT_CLEAR_REQUIRED_STREAK,
                "cleared": cleared,
                "last_taken_at": r.get("last_taken_at"),
            }
        )

    users = []
    for username, units in by_user.items():
        # 表示順: クリア済みを先に、その中は単元名、未クリアは通算満点の多い順
        units.sort(key=lambda u: (not u["cleared"], u["unit_name"]))
        cleared_count = sum(1 for u in units if u["cleared"])
        users.append(
            {
                "username": username,
                "cleared_count": cleared_count,
                "units": units,
            }
        )
    # クリア数の降順、同数は名前の昇順
    users.sort(key=lambda u: (-u["cleared_count"], u["username"]))
    return {"users": users, "required": UNIT_CLEAR_REQUIRED_STREAK}


@router.get("/api/{token}/admin/history")
def admin_history(token: str, username: str):
    """指定受験者の受験履歴。得点（score/total）は返さず、正答率（%）のみを返す。

    各回ごとに 日時・レベル・単元・正答率 を返す（どの受験か特定できる情報は維持）。
    """
    _check_token(token)
    name_map = _unit_name_map()
    out = []
    for a in db.get_history_for_user(username):
        total = a.get("total") or 0
        score = a.get("score") or 0
        pct = round(score * 100 / total) if total else 0
        unit_id = a.get("unit")
        out.append(
            {
                "taken_at": a.get("taken_at"),
                "level": a.get("level"),
                "unit_id": unit_id,
                "unit_name": name_map.get(unit_id) if unit_id else None,
                "pct": pct,  # 正答率のみ。得点は意図的に返さない。
            }
        )
    return {"username": username, "attempts": out}
