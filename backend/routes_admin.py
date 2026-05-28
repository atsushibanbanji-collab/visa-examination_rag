"""管理者用エンドポイント（URLトークンで難読化のみ）。

  GET  /api/{token}/admin/attempts          全受験履歴
  GET  /api/{token}/admin/users             ユーザー集計
  GET  /api/{token}/admin/meta              プールサイズ等メタ
  GET  /api/{token}/admin/questions/export  CSVエクスポート
  POST /api/{token}/admin/questions/import  CSV取り込み（レベル単位の置換）

挙動は従来の main.py と同一。グローバル QUESTIONS_DATA への直接参照を
questions_store 経由に置き換えただけ。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response

from backend import db
from backend import questions_store as store
from backend.config import ADMIN_TOKEN
from backend.questions_io import (
    ALLOWED_LEVELS,
    export_questions_to_csv,
    parse_questions_csv,
)

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


@router.get("/api/{token}/admin/comparison")
def admin_comparison(token: str):
    """固定プール方式 vs RAG方式の比較集計。

    source 別の受験回数・平均正答率、RAGはレイテンシ・トークン平均を返す。
    """
    _check_token(token)
    return {"comparison": db.get_comparison_summary()}


@router.get("/api/{token}/admin/meta")
def admin_meta(token: str):
    _check_token(token)
    qdata = store.get_questions_data()
    pool_sizes = {level: len(qdata.get(level, [])) for level in ALLOWED_LEVELS}
    unit_pool_sizes = {}
    for level in ALLOWED_LEVELS:
        units = store.units_meta(level)
        if not units:
            continue
        unit_pool_sizes[level] = [
            {
                "id": u["id"],
                "name": u["name"],
                "pool_size": len(store.pool_for_unit(level, u["id"])),
                "target_pool": u.get("target_pool", 0),
            }
            for u in units
        ]
    return {
        "pool_sizes": pool_sizes,
        "unit_pool_sizes": unit_pool_sizes,
        "admin_token": ADMIN_TOKEN,
    }


@router.get("/api/{token}/admin/questions/export")
def admin_export_questions(
    token: str,
    level: str = Query("all", description="beginner / intermediate / advanced / all"),
):
    """問題マスタを CSV で吐き出す。BOM 付き UTF-8。"""
    _check_token(token)

    if level == "all":
        target_levels = list(ALLOWED_LEVELS)
    elif level in ALLOWED_LEVELS:
        target_levels = [level]
    else:
        raise HTTPException(
            400, f"level は {','.join(ALLOWED_LEVELS)} または 'all' のいずれかで指定してください。"
        )

    with store.get_lock():
        csv_bytes = export_questions_to_csv(store.get_questions_data(), target_levels)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"questions-{level}-{timestamp}.csv"

    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/api/{token}/admin/questions/import")
async def admin_import_questions(token: str, file: UploadFile = File(...)):
    """CSV を取り込んで questions.json を書き換える（レベル単位の置換）。

    挙動:
      - CSV に含まれるレベルのみ置換、含まれないレベルは温存。
      - _units メタは常に温存（CSVには載せていない）。
      - 全行バリデーション通過後にのみファイルへ書き出す。
      - 書き換え前のファイルは `.bak.YYYYMMDD-HHMMSS` で残す。

    レスポンス（常に HTTP 200、ok フラグで分岐）。
    """
    _check_token(token)

    data = await file.read()
    if not data:
        return {"ok": False, "errors": [{"row": 0, "message": "ファイルが空です。"}]}

    parsed_by_level, errors, warnings = parse_questions_csv(data)
    if errors:
        return {"ok": False, "errors": errors}

    with store.get_lock():
        current = store.get_questions_data()
        # CSV に含まれないレベルは現状を温存。_units メタも温存。
        new_data = {"_units": current.get("_units", {})}
        for level in ALLOWED_LEVELS:
            new_data[level] = list(current.get(level, []))
        for level, qs in parsed_by_level.items():
            new_data[level] = qs

        # 全レベル横断 id 重複検出（未編集レベルとの衝突）
        seen = {}
        cross_errors = []
        for level in ALLOWED_LEVELS:
            for q in new_data[level]:
                if q["id"] in seen:
                    cross_errors.append(
                        {
                            "row": 0,
                            "message": (
                                f"id '{q['id']}' が複数レベルに存在します"
                                f"（{seen[q['id']]} / {level}）。"
                                "未編集レベルとの衝突をチェックしてください。"
                            ),
                        }
                    )
                else:
                    seen[q["id"]] = level
        if cross_errors:
            return {"ok": False, "errors": cross_errors}

        # _units で定義された unit_id への参照整合性チェック（警告のみ）
        for level in parsed_by_level:
            known_units = {u["id"] for u in store._units_meta_from(new_data, level)}
            for q in new_data[level]:
                unit = q.get("unit", "")
                if unit and known_units and unit not in known_units:
                    warnings.append(
                        {
                            "row": 0,
                            "message": (
                                f"id={q['id']}: 未定義の unit '{unit}' が指定されています"
                                f"（{level}の_units にありません）。単元クイズには出題されません。"
                            ),
                        }
                    )

        # ここまで来たら確定
        backup_path = store.atomic_write(new_data)
        store.replace_in_memory(new_data)

        applied = {level: len(parsed_by_level[level]) for level in parsed_by_level}
        untouched_levels = [lv for lv in ALLOWED_LEVELS if lv not in parsed_by_level]
        untouched = {lv: len(new_data[lv]) for lv in untouched_levels}

    return {
        "ok": True,
        "applied": applied,
        "untouched": untouched,
        "warnings": warnings,
        "backup": backup_path.name,
    }
