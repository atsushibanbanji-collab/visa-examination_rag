"""受験系エンドポイント（RAG出題専用）。

  GET  /api/rag/cells              観点メタのあるセル一覧 + 原本利用可否
  GET  /api/rag/units              単元一覧 + 進捗
  POST /api/rag/quiz/start         観点サンプリング→LLM生成→セッション保存→出題
  POST /api/quiz/check             1問即時判定（セッションの正答を照合）
  POST /api/quiz/submit            採点・保存・進捗更新
  GET  /api/history                個人履歴

出題は常にRAG方式。問題はセッション（quiz_sessions）に伏せて保持し、
正答・解説はサーバ側でのみ照合する（フロントへは返さない）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query

from backend import db
from backend import rag_generator, rag_perspectives, rag_session_store, rag_source
from backend.config import (
    ALLOWED_LEVELS,
    RAG_QUESTIONS_PER_QUIZ,
    UNIT_CLEAR_REQUIRED_STREAK,
)
from backend.db import SOURCE_RAG
from backend.models import CheckRequest, RagStartRequest, SubmitRequest

router = APIRouter()


# ----------------------------------------------------------------------
# RAG 出題
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
    """単元一覧。プールサイズの代わりに観点数を表示し、進捗を返す。"""
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
        "session_id": session["session_id"],
        "questions": session["questions"],
        "gen_metrics": gen["metrics"],
    }


# ----------------------------------------------------------------------
# 採点・即時判定・履歴
# ----------------------------------------------------------------------
@router.post("/api/quiz/check")
def check_answer(req: CheckRequest):
    """1問だけの即時正誤判定。セッションの正答を照合する。

    採点結果は履歴にも進捗にも一切記録しない（記録は /api/quiz/submit が担う）。
    """
    if not req.session_id:
        raise HTTPException(400, "session_id が必要です。")
    session = rag_session_store.get_session(req.session_id)
    if session is None:
        raise HTTPException(404, "セッションが見つからない、または期限切れです。")
    q = rag_session_store.question_in_session(session, req.id)
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
    if not req.session_id:
        raise HTTPException(400, "session_id が必要です。")

    session = rag_session_store.get_session(req.session_id)
    if session is None:
        raise HTTPException(404, "セッションが見つからない、または期限切れです。")
    qlookup = {q["id"]: q for q in session.get("questions", [])}
    session_meta = session.get("meta", {})

    # 採点
    results = []
    score = 0
    for ans in req.answers:
        q = qlookup.get(ans.id)
        if q is None:
            continue
        is_correct = ans.choice == q["answer"]
        if is_correct:
            score += 1
        results.append(
            {
                "id": q["id"],
                "category": q.get("perspective_id"),
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

    # 履歴を保存。単元情報・生成メタは details JSON 内の meta に格納する。
    details_payload = json.dumps(
        {
            "meta": {
                "unit": req.unit,
                "is_graduation": False,
                "source": SOURCE_RAG,
                "metrics": session_meta,
            },
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
        source=SOURCE_RAG,
    )

    # 単元進捗を更新（満点で連続+1、非満点で0リセット）
    unit_progress = None
    if req.unit:
        perfect = score == total
        unit_progress = db.update_unit_progress(
            username, req.level, req.unit, perfect, source=SOURCE_RAG,
        )

    return {
        "attempt_id": attempt_id,
        "username": username,
        "level": req.level,
        "unit": req.unit,
        "score": score,
        "total": total,
        "passed": score == total,
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
