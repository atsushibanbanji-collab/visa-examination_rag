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
    RAG_HEAD_COUNT,
    RAG_QUESTIONS_PER_QUIZ,
    RAG_TEST_QUESTIONS,
    UNIT_CLEAR_REQUIRED_STREAK,
    VISA_TYPE_UNITS,
)
from backend.db import SOURCE_RAG
from backend.models import CheckRequest, RagContinueRequest, RagStartRequest, SubmitRequest

router = APIRouter()


# ----------------------------------------------------------------------
# RAG 出題
# ----------------------------------------------------------------------
def _offered_cells():
    """出題対象（ビザ種別）の単元セルだけを返す。

    永住権・ビザの基本など、VISA_TYPE_UNITS に含まれない単元は除外する。
    cells / units / start すべてここを通すことで、絞り込みの真実源を1つにする。
    """
    return [
        c
        for c in rag_perspectives.available_cells()
        if c["unit_id"] in VISA_TYPE_UNITS
    ]


@router.get("/api/rag/cells")
def rag_cells():
    """観点メタが用意されているセル一覧と、原本テキストの利用可否を返す。

    出題対象（ビザ種別）の単元のみを返す。これにより index 側の難易度導出も
    出題対象のある難易度だけが有効になる。
    """
    return {
        "cells": _offered_cells(),
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

    cells = [c for c in _offered_cells() if c["level"] == level]
    if not cells:
        raise HTTPException(404, f"このレベルには出題対象の単元がありません: {level}")

    progress_map = db.get_progress_map_for_user(username, level, source=SOURCE_RAG)
    units_out = []
    for c in cells:
        unit_id = c["unit_id"]
        prog = progress_map.get(unit_id) or {}
        best_streak = prog.get("best_streak", 0)
        perfect_count = prog.get("perfect_count", 0)
        graduated_at = prog.get("graduated_at")
        units_out.append(
            {
                "id": unit_id,
                "name": c["unit_name"],
                "perspective_count": c["perspective_count"],
                "questions_per_quiz": RAG_QUESTIONS_PER_QUIZ,
                "perfect_count": perfect_count,      # 通算満点回数（クリア進捗）
                "streak_count": prog.get("streak_count", 0),
                "best_streak": best_streak,
                "required_streak": UNIT_CLEAR_REQUIRED_STREAK,
                "cleared": graduated_at is not None or perfect_count >= UNIT_CLEAR_REQUIRED_STREAK,
                "graduated_at": graduated_at,
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
    """RAG出題（ヘッド）: 観点サンプリング → 先頭 RAG_HEAD_COUNT 問だけ生成して即返す。

    残り（テイル）は未消化観点としてセッションに保持し、/api/rag/quiz/continue で
    生成・追記する。開始時の体感待ちを縮めるためのヘッド／テイル分割。
    """
    if req.level not in ALLOWED_LEVELS:
        raise HTTPException(400, f"level は {','.join(ALLOWED_LEVELS)} のいずれか。")
    username = req.username.strip()
    if not username:
        raise HTTPException(400, "user が空です。")
    if req.unit not in VISA_TYPE_UNITS:
        # 出題対象外（永住権・ビザの基本など）。URL直打ち等での到達を塞ぐ。
        # データは保持しているが、当面は出題しない。
        raise HTTPException(404, f"出題対象外の単元です: {req.unit}")
    if rag_perspectives.get_meta(req.level, req.unit) is None:
        raise HTTPException(404, f"観点メタがありません: level={req.level}, unit={req.unit}")

    # 観点は最初に全数サンプリング（LLM不要）。ヘッド／テイルに分割する。
    # テストモードでは出題数を絞り、原本非参照のダミーを生成する（経路は本番同一）。
    # 起動: ?test=1 のほか、受験者名が「テストモード」でも発動する（?test 載せ忘れ対策）。
    is_test = req.test or username == "テストモード"
    total_n = RAG_TEST_QUESTIONS if is_test else RAG_QUESTIONS_PER_QUIZ
    perspectives, seed = rag_perspectives.sample_perspectives(
        req.level, req.unit, total_n
    )
    if not perspectives:
        raise HTTPException(502, f"観点が0件です: level={req.level}, unit={req.unit}")
    head = perspectives[:RAG_HEAD_COUNT]
    tail = perspectives[RAG_HEAD_COUNT:]

    try:
        gen = rag_generator.generate_questions(
            req.level, req.unit, head, seed=seed, test_mode=is_test
        )
    except rag_generator.RAGGenerationError as e:
        msg = str(e)
        if "ANTHROPIC_API_KEY" in msg:
            raise HTTPException(503, msg)
        raise HTTPException(502, f"RAG出題の生成に失敗しました: {msg}")

    session = rag_session_store.create_session(
        username=username,
        level=req.level,
        unit_id=req.unit,
        questions=gen["questions"],
        metrics=gen["metrics"],
        pending_perspectives=tail,
    )
    return {
        "level": req.level,
        "unit": req.unit,
        "session_id": session["session_id"],
        "questions": session["questions"],
        "total_questions": len(perspectives),
        "head_count": len(head),
        "pending_count": len(tail),
        "test": is_test,
        "gen_metrics": gen["metrics"],
    }


@router.post("/api/rag/quiz/continue")
def rag_quiz_continue(req: RagContinueRequest):
    """RAG出題（テイル）: セッションの未消化観点から残り問題を生成・追記する。

    ユーザーがヘッドを解いている間に裏で呼ばれる想定。pending が空なら何もしない。
    """
    if not req.session_id:
        raise HTTPException(400, "session_id が必要です。")
    session = rag_session_store.get_session(req.session_id)
    if session is None:
        raise HTTPException(404, "セッションが見つからない、または期限切れです。")

    pending = session.get("pending") or {}
    pend_perspectives = pending.get("perspectives") or []
    if not pend_perspectives:
        # 既に消化済み or テイル無し。冪等に空を返す。
        return {
            "session_id": req.session_id,
            "questions": [],
            "gen_metrics": session.get("meta", {}),
        }

    try:
        gen = rag_generator.generate_questions(
            session["level"], session["unit_id"], pend_perspectives
        )
    except rag_generator.RAGGenerationError as e:
        msg = str(e)
        if "ANTHROPIC_API_KEY" in msg:
            raise HTTPException(503, msg)
        raise HTTPException(502, f"RAG出題（残り）の生成に失敗しました: {msg}")

    merged = rag_generator.merge_metrics(session.get("meta", {}), gen["metrics"])
    public = rag_session_store.append_tail_questions(session, gen["questions"], merged)
    return {
        "session_id": req.session_id,
        "questions": public,
        "gen_metrics": merged,
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
    graded = rag_session_store.grade_answer(
        q, choice=req.choice, text_answers=req.text_answers
    )
    return {
        "id": q["id"],
        "type": graded["type"],
        "correct_choice": graded["correct_choice"],
        "correct_answers": graded["correct_answers"],
        "is_correct": graded["is_correct"],
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
        graded = rag_session_store.grade_answer(
            q, choice=ans.choice, text_answers=ans.text_answers
        )
        if graded["is_correct"]:
            score += 1
        results.append(
            {
                "id": q["id"],
                "category": q.get("perspective_id"),
                "unit": q.get("unit", req.unit or ""),
                "type": graded["type"],
                "question": q["question"],
                "choices": q.get("choices"),
                "user_choice": ans.choice,
                "user_text_answers": ans.text_answers,
                "correct_choice": graded["correct_choice"],
                "correct_answers": graded["correct_answers"],
                "is_correct": graded["is_correct"],
                "explanation": q.get("explanation", ""),
            }
        )

    total = len(results)
    if total == 0:
        raise HTTPException(400, "有効な解答がない")

    # テストモードのセッションは記録しない（履歴・進捗を汚さない）。
    # 構築段階では、テストモードの受験も履歴・進捗に記録する（管理画面の動作確認用）。
    # 後で識別・除外できるよう、details の meta に test フラグを残す。
    is_test = bool(session_meta.get("test"))

    # 履歴を保存。単元情報・生成メタは details JSON 内の meta に格納する。
    details_payload = json.dumps(
        {
            "meta": {
                "unit": req.unit,
                "is_graduation": False,
                "source": SOURCE_RAG,
                "test": is_test,
                "metrics": session_meta,
            },
            "answers": [
                {
                    "id": r["id"],
                    "type": r["type"],
                    "user_choice": r["user_choice"],
                    "user_text_answers": r["user_text_answers"],
                    "is_correct": r["is_correct"],
                }
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
        "test": is_test,
        "results": results,
    }


@router.get("/api/history")
def get_history(username: str):
    username = username.strip()
    if not username:
        raise HTTPException(400, "ユーザー名が必要")
    return {"username": username, "attempts": db.get_history_for_user(username)}
