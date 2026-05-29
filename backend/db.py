"""SQLite永続化。最小構成。
本番でデータを失いたくないなら、RenderのPersistent Diskをマウントし、
環境変数 DATABASE_PATH に /var/data/quiz.db のようなパスを設定する。

このリポジトリは固定プール方式とRAG方式の比較用に新規DBを切ったため、
最初から source 列（'pool' | 'rag'）を持たせて両方式の記録を分離できるようにしている。
（既存リポジトリの「attempts無改修」制約は本番デプロイ保護のためのもので、
  本リポジトリは新規DBにつき適用しない）
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

DB_PATH = os.environ.get(
    "DATABASE_PATH",
    str(Path(__file__).parent / "quiz.db"),
)

# 卒業試験を unit_progress 上で表現するための特殊 unit_id。
# 通常 unit との衝突を避けるためアンダースコア囲みにする。
GRADUATION_UNIT_ID = "__graduation__"

# 出題方式（source）。比較のため両方式の記録を1つのDBで分離して持つ。
SOURCE_POOL = "pool"
SOURCE_RAG = "rag"
ALLOWED_SOURCES = (SOURCE_POOL, SOURCE_RAG)


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        # 受験履歴。source 列で固定プール / RAG を分離。
        # 生成メタ（レイテンシ・トークン・観点id等）は details JSON に格納する。
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS attempts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                username    TEXT    NOT NULL,
                level       TEXT    NOT NULL,
                source      TEXT    NOT NULL DEFAULT 'pool',
                score       INTEGER NOT NULL,
                total       INTEGER NOT NULL,
                taken_at    TEXT    NOT NULL,
                details     TEXT
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_username ON attempts(username)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_taken_at ON attempts(taken_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_source ON attempts(source)")

        # 単元進捗。username × level × unit_id × source で一意。
        # source を一意キーに含めることで、固定プールとRAGの連続記録が
        # 互いを汚染しないようにしている（比較の独立性確保）。
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS unit_progress (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT    NOT NULL,
                level         TEXT    NOT NULL,
                unit_id       TEXT    NOT NULL,
                source        TEXT    NOT NULL DEFAULT 'pool',
                streak_count  INTEGER NOT NULL DEFAULT 0,
                best_streak   INTEGER NOT NULL DEFAULT 0,
                last_taken_at TEXT,
                graduated_at  TEXT,
                UNIQUE (username, level, unit_id, source)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_unit_progress_user_level "
            "ON unit_progress(username, level, source)"
        )

        # RAGの一時問題プール。生成した問題（正答・解説込み）を session 単位で保持。
        # フロントへは正答・解説を返さず、採点時にこのテーブルから引く。
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS quiz_sessions (
                session_id  TEXT PRIMARY KEY,
                username    TEXT    NOT NULL,
                level       TEXT    NOT NULL,
                unit_id     TEXT    NOT NULL,
                created_at  TEXT    NOT NULL,
                expires_at  TEXT    NOT NULL,
                questions   TEXT    NOT NULL,
                meta        TEXT
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_quiz_sessions_expires "
            "ON quiz_sessions(expires_at)"
        )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


# ----------------------------------------------------------------------
# 受験履歴
# ----------------------------------------------------------------------
def save_attempt(
    username: str,
    level: str,
    score: int,
    total: int,
    details: str,
    source: str = SOURCE_POOL,
) -> int:
    """受験1回分を保存する。

    details は呼び出し側で組み立てた JSON 文字列。単元情報（unit / is_graduation）と
    RAG生成メタ（レイテンシ・トークン等）はこの details JSON の中に格納する。
    source は 'pool'（固定プール） / 'rag'（RAG）。
    """
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO attempts (username, level, source, score, total, taken_at, details)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (username, level, source, score, total, _now_iso(), details),
        )
        return cur.lastrowid


def _extract_attempt_meta(details_raw: Optional[str]) -> dict:
    """details JSON から履歴表示用のメタ情報を取り出す。

    details 構造（本リポジトリ）:
      {"meta": {"unit": "...", "is_graduation": bool, "metrics": {...}}, "answers": [...]}
    壊れた JSON では unit=None / is_graduation=False / metrics=None を返す。
    """
    meta = {"unit": None, "is_graduation": False, "metrics": None}
    if not details_raw:
        return meta
    try:
        parsed = json.loads(details_raw)
    except (ValueError, TypeError):
        return meta
    if isinstance(parsed, dict):
        m = parsed.get("meta") or {}
        meta["unit"] = m.get("unit")
        meta["is_graduation"] = bool(m.get("is_graduation", False))
        meta["metrics"] = m.get("metrics")
    return meta


def get_history_for_user(username: str, limit: int = 50):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, level, source, score, total, taken_at, details
            FROM attempts
            WHERE username = ?
            ORDER BY taken_at DESC, id DESC
            LIMIT ?
            """,
            (username, limit),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            meta = _extract_attempt_meta(d.pop("details", None))
            d.update(meta)
            out.append(d)
        return out


def get_all_attempts(limit: int = 1000):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, username, level, source, score, total, taken_at, details
            FROM attempts
            ORDER BY taken_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            meta = _extract_attempt_meta(d.pop("details", None))
            d.update(meta)
            out.append(d)
        return out


def get_user_summary():
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                username,
                COUNT(*) AS attempts_count,
                MAX(CAST(score AS REAL) * 100 / total)  AS best_pct,
                AVG(CAST(score AS REAL) * 100 / total)  AS avg_pct,
                MAX(taken_at)                           AS last_taken_at
            FROM attempts
            GROUP BY username
            ORDER BY last_taken_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


# ----------------------------------------------------------------------
# 単元進捗
# ----------------------------------------------------------------------
def get_unit_progress(
    username: str, level: str, unit_id: str, source: str = SOURCE_POOL
) -> Optional[dict]:
    """単一の単元進捗を取得。未受験ならNone。"""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT streak_count, best_streak, last_taken_at, graduated_at
            FROM unit_progress
            WHERE username = ? AND level = ? AND unit_id = ? AND source = ?
            """,
            (username, level, unit_id, source),
        ).fetchone()
        return dict(row) if row else None


def get_progress_map_for_user(
    username: str, level: str, source: str = SOURCE_POOL
) -> dict:
    """username × level × source の単元別進捗を {unit_id: progress_dict} で返す。"""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT unit_id, streak_count, best_streak, last_taken_at, graduated_at
            FROM unit_progress
            WHERE username = ? AND level = ? AND source = ?
            """,
            (username, level, source),
        ).fetchall()
        return {r["unit_id"]: dict(r) for r in rows}


def update_unit_progress(
    username: str,
    level: str,
    unit_id: str,
    perfect: bool,
    clear_streak_required: int = 3,
    source: str = SOURCE_POOL,
) -> dict:
    """満点なら streak+1、非満点なら 0 にリセット。

    Args:
        clear_streak_required: クリア（graduated_at 初記録）に必要な連続成功回数。
            通常単元は 3、卒業試験は 1。
        source: 'pool' / 'rag'。方式ごとに進捗を独立管理する。

    Returns:
        更新後の進捗 dict（streak_count, best_streak, last_taken_at,
        graduated_at, newly_cleared）。
        newly_cleared は今回のサブミットで初めてクリア条件に達したかどうか。
    """
    now = _now_iso()

    with get_conn() as conn:
        # まず行を確保（無ければ作る）。INSERT OR IGNORE + 後続 UPDATE で UPSERT。
        conn.execute(
            """
            INSERT OR IGNORE INTO unit_progress
              (username, level, unit_id, source, streak_count, best_streak, last_taken_at)
            VALUES (?, ?, ?, ?, 0, 0, NULL)
            """,
            (username, level, unit_id, source),
        )

        row = conn.execute(
            """
            SELECT streak_count, best_streak, graduated_at
            FROM unit_progress
            WHERE username = ? AND level = ? AND unit_id = ? AND source = ?
            """,
            (username, level, unit_id, source),
        ).fetchone()

        prev_streak = row["streak_count"]
        best_streak = row["best_streak"]
        graduated_at = row["graduated_at"]

        new_streak = prev_streak + 1 if perfect else 0
        if new_streak > best_streak:
            best_streak = new_streak

        # 初クリア判定: 今回 streak が閾値に達し、過去に卒業記録が無い
        newly_cleared = False
        if perfect and new_streak >= clear_streak_required and graduated_at is None:
            graduated_at = now
            newly_cleared = True

        conn.execute(
            """
            UPDATE unit_progress
            SET streak_count  = ?,
                best_streak   = ?,
                last_taken_at = ?,
                graduated_at  = ?
            WHERE username = ? AND level = ? AND unit_id = ? AND source = ?
            """,
            (new_streak, best_streak, now, graduated_at, username, level, unit_id, source),
        )

        return {
            "streak_count": new_streak,
            "best_streak": best_streak,
            "last_taken_at": now,
            "graduated_at": graduated_at,
            "newly_cleared": newly_cleared,
        }


# ----------------------------------------------------------------------
# RAG セッション問題プール（一時保持）
# ----------------------------------------------------------------------
def save_quiz_session(
    session_id: str,
    username: str,
    level: str,
    unit_id: str,
    questions: List[dict],
    meta: dict,
    ttl_sec: int,
) -> None:
    """生成済みの問題（正答・解説込み）を session 単位で保存する。"""
    now = datetime.utcnow()
    expires = now + timedelta(seconds=ttl_sec)
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO quiz_sessions
              (session_id, username, level, unit_id, created_at, expires_at, questions, meta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                username,
                level,
                unit_id,
                now.isoformat(timespec="seconds") + "Z",
                expires.isoformat(timespec="seconds") + "Z",
                json.dumps(questions, ensure_ascii=False),
                json.dumps(meta, ensure_ascii=False),
            ),
        )


def get_quiz_session(session_id: str) -> Optional[dict]:
    """session を取得。期限切れ・不存在なら None。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM quiz_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        # 期限切れ判定
        if d["expires_at"] <= _now_iso():
            conn.execute("DELETE FROM quiz_sessions WHERE session_id = ?", (session_id,))
            return None
        d["questions"] = json.loads(d["questions"])
        d["meta"] = json.loads(d["meta"]) if d["meta"] else {}
        return d


def cleanup_expired_sessions() -> int:
    """期限切れ session を掃除する。削除件数を返す。"""
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM quiz_sessions WHERE expires_at <= ?", (_now_iso(),)
        )
        return cur.rowcount
