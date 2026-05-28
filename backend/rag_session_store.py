"""RAGセッション問題プールのライフサイクル管理。

生成した問題（正答・解説込み）を session_id 紐付けで一時保存し、
フロントへは正答・解説を伏せた形だけを返す。採点時はこのセッションから
正答を引く（固定プール方式が questions.json を引くのと対になる経路）。

DBの生SQLは db.py に集約し、本モジュールはドメイン操作（採番・整形・採点）を担う。
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from backend import db
from backend.config import RAG_SESSION_TTL_SEC


def _qid(session_id: str, idx: int) -> str:
    """セッション内の問題ID。固定プールの永続ID（b001等）とは衝突しない形式。"""
    return f"{session_id}#{idx}"


def create_session(
    username: str,
    level: str,
    unit_id: str,
    questions: List[dict],
    metrics: dict,
) -> dict:
    """生成問題からセッションを作り、フロント向けの整形済み問題を返す。

    Returns:
        {"session_id": str, "questions": [フロント向け（正答・解説なし）], "metrics": {...}}
    """
    session_id = "sess_" + uuid.uuid4().hex[:16]
    # 内部保持用（正答・解説込み）に qid を採番
    stored = []
    public = []
    for i, q in enumerate(questions):
        qid = _qid(session_id, i)
        stored.append(
            {
                "id": qid,
                "perspective_id": q.get("perspective_id", ""),
                "question": q["question"],
                "choices": q["choices"],
                "answer": q["answer"],
                "explanation": q.get("explanation", ""),
                "source_pages": q.get("source_pages", []),
            }
        )
        public.append(
            {
                "id": qid,
                "category": q.get("perspective_id", ""),
                "unit": unit_id,
                "question": q["question"],
                "choices": q["choices"],
            }
        )
    db.cleanup_expired_sessions()
    db.save_quiz_session(
        session_id=session_id,
        username=username,
        level=level,
        unit_id=unit_id,
        questions=stored,
        meta=metrics,
        ttl_sec=RAG_SESSION_TTL_SEC,
    )
    return {"session_id": session_id, "questions": public, "metrics": metrics}


def get_session(session_id: str) -> Optional[dict]:
    """セッションを取得（期限切れ・不存在なら None）。"""
    return db.get_quiz_session(session_id)


def question_in_session(session: dict, qid: str) -> Optional[dict]:
    """セッション内の問題を qid で引く（正答・解説込み）。なければ None。"""
    for q in session.get("questions", []):
        if q["id"] == qid:
            return q
    return None
