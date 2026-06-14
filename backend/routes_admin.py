"""管理者用エンドポイント（URLトークンで難読化のみ）。

  GET  /api/{token}/admin/users             受験者一覧（名前＋単元別進捗・クリア数降順）
  GET  /api/{token}/admin/history?username= 個別の受験履歴（得点は返さず正答率のみ）

RAG出題専用。サマリー・受験回数・最高点・平均点・全件履歴は廃止した
（受験者ごとの単元クリア状況の把握と、個別履歴の正答率確認に絞る）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend import auth, db, rag_perspectives
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
    """アカウント一覧。各アカウントの単元別進捗（満点回数 / クリア状況）と、
    クリア済み単元の総数を返す。クリア数の降順、同数は表示名の昇順で並べる。

    進捗のないアカウント（登録のみ）も一覧に出す。
    user_id の紐づかない旧データ（氏名のみの記録）は表示しない。
    """
    _check_token(token)
    name_map = _unit_name_map()
    rows = db.get_all_unit_progress_by_account(source=SOURCE_RAG)

    by_uid: dict = {}
    for r in rows:
        bucket = by_uid.setdefault(r["user_id"], [])
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
    for account in db.list_users():
        units = by_uid.get(account["id"], [])
        # 表示順: 直近に受験した単元ほど前（クライアント要望）。未受験日時は末尾。
        units.sort(key=lambda u: u.get("last_taken_at") or "", reverse=True)
        cleared_count = sum(1 for u in units if u["cleared"])
        last_taken_at = max((u.get("last_taken_at") or "" for u in units), default="") or None
        users.append(
            {
                "user_id": account["id"],
                "username": account["display_name"],
                "email": account["email"],
                "cleared_count": cleared_count,
                "last_taken_at": last_taken_at,
                "units": units,
            }
        )
    # クリア数の降順、同数は表示名の昇順
    users.sort(key=lambda u: (-u["cleared_count"], u["username"]))
    return {"users": users, "required": UNIT_CLEAR_REQUIRED_STREAK}


@router.get("/api/{token}/admin/history")
def admin_history(token: str, user_id: int):
    """指定アカウントの受験履歴。得点（score/total）は返さず、正答率（%）のみを返す。

    各回ごとに 日時・レベル・単元・正答率 を返す（どの受験か特定できる情報は維持）。
    """
    _check_token(token)
    account = db.get_user_by_id(user_id)
    if account is None:
        raise HTTPException(404, "アカウントが見つかりません。")
    name_map = _unit_name_map()
    # 満点の通し番号付与のため余裕を持って取得（時系列の古い側から数える）
    attempts = db.get_history_by_user_id(user_id, limit=1000)

    # 満点の通し番号: 同一 (level, unit) で時系列昇順に 1, 2, 3... と数える。
    # attempts は新しい順なので、逆順に走査してカウンタを進める。
    perfect_no_by_id: dict = {}
    counters: dict = {}
    for a in reversed(attempts):
        total = a.get("total") or 0
        score = a.get("score") or 0
        if total and score == total:
            key = (a.get("level"), a.get("unit"))
            counters[key] = counters.get(key, 0) + 1
            perfect_no_by_id[a.get("id")] = counters[key]

    out = []
    for a in attempts[:50]:  # 表示件数は従来どおり50件まで
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
                # 満点なら「何回目の満点か」（同一レベル×単元の通算。クリア閾値と並べて表示する用）
                "perfect_no": perfect_no_by_id.get(a.get("id")),
            }
        )
    return {
        "username": account["display_name"],
        "email": account["email"],
        "attempts": out,
        "required": UNIT_CLEAR_REQUIRED_STREAK,
    }


class AdminPasswordResetRequest(BaseModel):
    new_password: str = Field(..., min_length=8, max_length=128)


@router.post("/api/{token}/admin/users/{user_id}/password")
def admin_reset_password(token: str, user_id: int, req: AdminPasswordResetRequest):
    """パスワードを忘れたユーザーのために管理者が再設定する（メール送信基盤は持たない）。

    再設定後、そのユーザーの全ログインセッションは失効する（update_user_password 内）。
    新しいパスワードは管理者が口頭等で本人へ伝える運用。
    """
    _check_token(token)
    if db.get_user_by_id(user_id) is None:
        raise HTTPException(404, "アカウントが見つかりません。")
    db.update_user_password(user_id, auth.hash_password(req.new_password))
    return {"ok": True, "user_id": user_id}
