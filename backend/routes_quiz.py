"""受験系エンドポイント（固定プール方式 + RAG方式の比較対応）。

  GET  /api/levels                 難易度メタ
  GET  /api/units                  単元一覧＋進捗＋卒業ロック状態（固定プール）
  GET  /api/quiz/start             単元クイズ出題（固定プール）
  GET  /api/quiz/graduation/start  卒業試験出題（固定プール）
  POST /api/quiz/check             1問即時判定（固定プール / RAG 両対応）
  POST /api/quiz/submit            採点・保存・進捗更新（固定プール / RAG 両対応）
  GET  /api/history                個人履歴

  RAG方式（比較用）:
  GET  /api/rag/cells              観点メタのあるセル一覧 + 原本利用可否
  GET  /api/rag/units              単元一覧 + RAG進捗（source='rag'）
  POST /api/rag/quiz/start         観点サンプリング→LLM生成→セッション保存→出題

固定プール方式のエンドポイントは既存挙動のまま。RAGは別経路として追加している。
"""
from __future__ import annotations

import json
import random
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from backend import db
from backend import questions_store as store
from backend import rag_generator, rag_perspectives, rag_session_store, rag_source
from backend.config import (
    GRADUATION_PASS_SCORE,
    GRADUATION_QUESTIONS,
    QUIZ_QUESTIONS_PER_UNIT,
    RAG_QUESTIONS_PER_QUIZ,
    UNIT_CLEAR_REQUIRED_STREAK,
)
from backend.db import GRADUATION_UNIT_ID, SOURCE_POOL, SOURCE_RAG
from backend.models import CheckRequest, RagStartRequest, SubmitRequest
from backend.questions_io import ALLOWED_LEVELS
from backend.services import (
    count_cleared_units,
    is_graduation_unlocked,
    sanitize_questions,
)

router = APIRouter()


@router.get("/api/levels")
def get_levels():
    """難易度一覧。フロントの初期画面で利用。"""
    qdata = store.get_questions_data()
    out = []
    for level_id, name in (
        ("beginner", "初級"),
        ("intermediate", "中級"),
        ("advanced", "上級"),
    ):
        pool = qdata.get(level_id, [])
        units = store.units_meta(level_id)
        out.append(
            {
                "id": level_id,
                "name": name,
                "available": len(pool) > 0,
                "pool_size": len(pool),
                "unit_count": len(units),
            }
        )
    return {"levels": out}


@router.get("/api/units")
def get_units(
    level: str = Query(..., description="beginner / intermediate / advanced"),
    user: str = Query(..., min_length=1, max_length=50, description="受験者名"),
):
    """単元一覧と各単元の進捗（streak/3 表示用）、卒業試験ロック状態を返す（固定プール）。"""
    if level not in ALLOWED_LEVELS:
        raise HTTPException(400, f"level は {','.join(ALLOWED_LEVELS)} のいずれか。")
    username = user.strip()
    if not username:
        raise HTTPException(400, "user が空です。")

    units_list = store.units_meta(level)
    progress_map = db.get_progress_map_for_user(username, level, source=SOURCE_POOL)

    units_out = []
    for u in units_list:
        unit_id = u["id"]
        pool_size = len(store.pool_for_unit(level, unit_id))
        prog = progress_map.get(unit_id) or {}
        best_streak = prog.get("best_streak", 0)
        streak_count = prog.get("streak_count", 0)
        cleared = best_streak >= UNIT_CLEAR_REQUIRED_STREAK
        questions_per_quiz = min(QUIZ_QUESTIONS_PER_UNIT, pool_size) if pool_size > 0 else 0

        units_out.append(
            {
                "id": unit_id,
                "name": u["name"],
                "target_pool": u.get("target_pool", 0),
                "pool_size": pool_size,
                "questions_per_quiz": questions_per_quiz,
                "streak_count": streak_count,
                "best_streak": best_streak,
                "required_streak": UNIT_CLEAR_REQUIRED_STREAK,
                "cleared": cleared,
                "graduated_at": prog.get("graduated_at"),
                "last_taken_at": prog.get("last_taken_at"),
                "playable": questions_per_quiz > 0,
            }
        )

    grad_prog = progress_map.get(GRADUATION_UNIT_ID) or {}
    graduation = {
        "unlocked": is_graduation_unlocked(progress_map, units_list),
        "total_units": len(units_list),
        "cleared_units": sum(1 for u in units_out if u["cleared"]),
        "passed": grad_prog.get("graduated_at") is not None,
        "graduated_at": grad_prog.get("graduated_at"),
        "last_taken_at": grad_prog.get("last_taken_at"),
        "questions_count": min(GRADUATION_QUESTIONS, len(store.level_pool(level))),
        "pass_score": GRADUATION_PASS_SCORE,
    }

    return {"level": level, "username": username, "units": units_out, "graduation": graduation}


@router.get("/api/quiz/start")
def start_quiz(
    level: str = "beginner",
    unit: Optional[str] = None,
    count: int = QUIZ_QUESTIONS_PER_UNIT,
):
    """ランダム出題（固定プール）。

    - unit 指定時: 当該単元プールから min(count, pool_size) 問。
    - unit 未指定時: レベル全体プールから min(count, pool_size) 問（旧API互換）。
    """
    if level not in ALLOWED_LEVELS:
        raise HTTPException(400, f"level は {','.join(ALLOWED_LEVELS)} のいずれか。")

    if unit is None:
        pool = store.level_pool(level)
        if not pool:
            raise HTTPException(404, f"問題プールが空: level={level}")
    else:
        pool = store.pool_for_unit(level, unit)
        if not pool:
            raise HTTPException(404, f"問題プールが空: level={level}, unit={unit}")

    n = min(count, len(pool))
    selected = random.sample(pool, n)
    return {
        "level": level,
        "unit": unit,
        "mode": SOURCE_POOL,
        "questions": sanitize_questions(selected),
    }


@router.get("/api/quiz/graduation/start")
def start_graduation(
    level: str = Query(..., description="beginner / intermediate / advanced"),
    user: str = Query(..., min_length=1, max_length=50),
):
    """卒業試験を出題する（固定プール）。全単元クリア前は 403。"""
    if level not in ALLOWED_LEVELS:
        raise HTTPException(400, f"level は {','.join(ALLOWED_LEVELS)} のいずれか。")

    username = user.strip()
    if not username:
        raise HTTPException(400, "user が空です。")

    units_list = store.units_meta(level)
    if not units_list:
        raise HTTPException(404, f"このレベルには単元が定義されていません: {level}")

    progress_map = db.get_progress_map_for_user(username, level, source=SOURCE_POOL)
    if not is_graduation_unlocked(progress_map, units_list):
        cleared = count_cleared_units(progress_map, units_list)
        raise HTTPException(
            403,
            f"卒業試験はロックされています（クリア済み単元: {cleared}/{len(units_list)}）。"
            "全単元を 10/10 連続3回でクリアしてください。",
        )

    pool = store.level_pool(level)
    if not pool:
        raise HTTPException(404, f"問題プールが空: level={level}")

    n = min(GRADUATION_QUESTIONS, len(pool))
    selected = random.sample(pool, n)
    return {
        "level": level,
        "unit": GRADUATION_UNIT_ID,
        "graduation": True,
        "mode": SOURCE_POOL,
        "pass_score": GRADUATION_PASS_SCORE,
        "questions": sanitize_questions(selected),
    }


@router.post("/api/quiz/check")
def check_answer(req: CheckRequest):
    """1問だけの即時正誤判定（固定プール / RAG 両対応）。

    session_id があれば RAG セッションから、なければ固定プールから正答を引く。
    採点結果は履歴にも進捗にも一切記録しない（記録は /api/quiz/submit が担う）。
    """
    if req.session_id:
        session = rag_session_store.get_session(req.session_id)
        if session is None:
            raise HTTPException(404, "セッションが見つからない、または期限切れです。")
        q = rag_session_store.question_in_session(session, req.id)
    else:
        q = store.get_question_by_id(req.id)
    if q is None:
        raise HTTPException(404, f"問題が見つからない: id={req.id}")
    is_correct = req.choice == q["answer"]
    return {
        "id": q["id"],
        "correct_choice": q["answer"],
        "is_correct": is_correct,
        "explanation": q.get("explanation", ""),
    }


@router.post("/api/quiz/submit")
def submit_quiz(req: SubmitRequest):
    username = req.username.strip()
    if not username:
        raise HTTPException(400, "ユーザー名が必要")
    if req.level not in ALLOWED_LEVELS:
        raise HTTPException(400, f"level は {','.join(ALLOWED_LEVELS)} のいずれか。")
    if req.mode not in (SOURCE_POOL, SOURCE_RAG):
        raise HTTPException(400, "mode は pool / rag のいずれか。")

    # 採点に使う正答辞書を方式に応じて用意する
    session_meta = {}
    qlookup = None
    if req.mode == SOURCE_RAG:
        if not req.session_id:
            raise HTTPException(400, "RAG採点には session_id が必要です。")
        session = rag_session_store.get_session(req.session_id)
        if session is None:
            raise HTTPException(404, "セッションが見つからない、または期限切れです。")
        qlookup = {q["id"]: q for q in session.get("questions", [])}
        session_meta = session.get("meta", {})

    # 採点
    results = []
    score = 0
    for ans in req.answers:
        if req.mode == SOURCE_RAG:
            q = qlookup.get(ans.id)
        else:
            q = store.get_question_by_id(ans.id)
        if q is None:
            continue
        is_correct = ans.choice == q["answer"]
        if is_correct:
            score += 1
        results.append(
            {
                "id": q["id"],
                "category": q.get("category") or q.get("perspective_id"),
                "unit": q.get("unit", req.unit or ""),
                "question": q["question"],
                "choices": q["choices"],
                "user_choice": ans.choice,
                "correct_choice": q["answer"],
                "is_correct": is_correct,
                "explanation": q.get("explanation", ""),
            }
        )

    total = len(results)
    if total == 0:
        raise HTTPException(400, "有効な解答がない")

    # 履歴を保存。単元情報・方式・生成メタは details JSON 内の meta に格納する。
    is_graduation = req.unit == GRADUATION_UNIT_ID
    meta_payload = {
        "unit": None if is_graduation else req.unit,
        "is_graduation": is_graduation,
        "source": req.mode,
    }
    if req.mode == SOURCE_RAG:
        meta_payload["metrics"] = session_meta
    details_payload = json.dumps(
        {
            "meta": meta_payload,
            "answers": [
                {"id": r["id"], "user_choice": r["user_choice"], "is_correct": r["is_correct"]}
                for r in results
            ],
        },
        ensure_ascii=False,
    )
    attempt_id = db.save_attempt(
        username=username,
        level=req.level,
        score=score,
        total=total,
        details=details_payload,
        source=req.mode,
    )

    # 単元進捗 / 卒業試験進捗を更新（source ごとに独立管理）
    unit_progress = None
    if req.unit:
        if is_graduation:
            qualified = score >= GRADUATION_PASS_SCORE
            unit_progress = db.update_unit_progress(
                username, req.level, GRADUATION_UNIT_ID, qualified,
                clear_streak_required=1, source=req.mode,
            )
        else:
            perfect = score == total
            unit_progress = db.update_unit_progress(
                username, req.level, req.unit, perfect, source=req.mode,
            )

    return {
        "attempt_id": attempt_id,
        "username": username,
        "level": req.level,
        "unit": req.unit,
        "mode": req.mode,
        "is_graduation": is_graduation,
        "score": score,
        "total": total,
        "pass_score": GRADUATION_PASS_SCORE if is_graduation else None,
        "passed": (score >= GRADUATION_PASS_SCORE) if is_graduation else (score == total),
        "required_streak": UNIT_CLEAR_REQUIRED_STREAK,
        "unit_progress": unit_progress,
        "results": results,
    }


@router.get("/api/history")
def get_history(username: str):
    username = username.strip()
    if not username:
        raise HTTPException(400, "ユーザー名が必要")
    return {"username": username, "attempts": db.get_history_for_user(username)}


# ----------------------------------------------------------------------
# RAG方式（比較用）
# ----------------------------------------------------------------------
@router.get("/api/rag/cells")
def rag_cells():
    """観点メタが用意されているセル一覧と、原本テキストの利用可否を返す。"""
    return {
        "cells": rag_perspectives.available_cells(),
        "source_available": rag_source.is_available(),
        "source_error": rag_source.load_error(),
        "questions_per_quiz": RAG_QUESTIONS_PER_QUIZ,
    }


@router.get("/api/rag/units")
def rag_units(
    level: str = Query(..., description="beginner / intermediate / advanced"),
    user: str = Query(..., min_length=1, max_length=50),
):
    """RAG用の単元一覧。プールサイズの代わりに観点数を表示し、進捗は source='rag'。"""
    if level not in ALLOWED_LEVELS:
        raise HTTPException(400, f"level は {','.join(ALLOWED_LEVELS)} のいずれか。")
    username = user.strip()
    if not username:
        raise HTTPException(400, "user が空です。")

    cells = [c for c in rag_perspectives.available_cells() if c["level"] == level]
    if not cells:
        raise HTTPException(404, f"このレベルには観点メタがありません: {level}")

    progress_map = db.get_progress_map_for_user(username, level, source=SOURCE_RAG)
    units_out = []
    for c in cells:
        unit_id = c["unit_id"]
        prog = progress_map.get(unit_id) or {}
        best_streak = prog.get("best_streak", 0)
        units_out.append(
            {
                "id": unit_id,
                "name": c["unit_name"],
                "perspective_count": c["perspective_count"],
                "questions_per_quiz": RAG_QUESTIONS_PER_QUIZ,
                "streak_count": prog.get("streak_count", 0),
                "best_streak": best_streak,
                "required_streak": UNIT_CLEAR_REQUIRED_STREAK,
                "cleared": best_streak >= UNIT_CLEAR_REQUIRED_STREAK,
                "graduated_at": prog.get("graduated_at"),
                "last_taken_at": prog.get("last_taken_at"),
                "playable": c["perspective_count"] > 0,
            }
        )
    return {
        "level": level,
        "username": username,
        "units": units_out,
        "source_available": rag_source.is_available(),
    }


@router.post("/api/rag/quiz/start")
def rag_quiz_start(req: RagStartRequest):
    """RAG出題: 観点サンプリング → LLM生成 → セッション保存 → 出題（正答は伏せる）。"""
    if req.level not in ALLOWED_LEVELS:
        raise HTTPException(400, f"level は {','.join(ALLOWED_LEVELS)} のいずれか。")
    username = req.username.strip()
    if not username:
        raise HTTPException(400, "user が空です。")
    if rag_perspectives.get_meta(req.level, req.unit) is None:
        raise HTTPException(404, f"観点メタがありません: level={req.level}, unit={req.unit}")

    try:
        gen = rag_generator.generate(req.level, req.unit, RAG_QUESTIONS_PER_QUIZ)
    except rag_generator.RAGGenerationError as e:
        msg = str(e)
        # API未設定はサービス未構成として 503、それ以外は生成失敗として 502
        if "ANTHROPIC_API_KEY" in msg:
            raise HTTPException(503, msg)
        raise HTTPException(502, f"RAG出題の生成に失敗しました: {msg}")

    session = rag_session_store.create_session(
        username=username,
        level=req.level,
        unit_id=req.unit,
        questions=gen["questions"],
        metrics=gen["metrics"],
    )
    return {
        "level": req.level,
        "unit": req.unit,
        "mode": SOURCE_RAG,
        "session_id": session["session_id"],
        "questions": session["questions"],
        "gen_metrics": gen["metrics"],
    }
