"""永続化層。SQLite / PostgreSQL 両対応。

接続先は環境変数で決まる：
  - DATABASE_URL（postgresql://...）があれば PostgreSQL（本番想定。Render Postgres 等）
  - なければ SQLite（DATABASE_PATH、既定 backend/quiz.db。ローカル開発・スモークテスト用）

SQL本文は共通（プレースホルダは ? で記述）とし、方言差は _Conn ラッパと
init_db / 一部UPSERT文の分岐だけで吸収する。データの中身・関数の入出力は両方言で同一。

このリポジトリは固定プール方式とRAG方式の比較用に新規DBを切ったため、
最初から source 列（'pool' | 'rag'）を持たせて両方式の記録を分離できるようにしている。
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))

if IS_POSTGRES:
    import psycopg
    from psycopg.rows import dict_row

DB_PATH = os.environ.get(
    "DATABASE_PATH",
    str(Path(__file__).parent / "quiz.db"),
)

# 出題方式（source）。比較のため両方式の記録を1つのDBで分離して持つ。
SOURCE_POOL = "pool"
SOURCE_RAG = "rag"


class _Conn:
    """sqlite3 / psycopg の差を吸収する薄いラッパ。

    - SQL は ? プレースホルダで書き、PostgreSQL では %s へ変換して実行する
      （本モジュールのSQLに文字 '?' のリテラルは存在しない前提。追加時は注意）。
    - 行アクセスは両方言とも名前で可能（sqlite3.Row / psycopg dict_row）。
    - execute の戻り値はカーソル（fetchone/fetchall/rowcount が共通で使える）。
    """

    def __init__(self, raw, is_pg: bool):
        self.raw = raw
        self.is_pg = is_pg

    def execute(self, sql: str, params=()):
        if self.is_pg:
            sql = sql.replace("?", "%s")
        return self.raw.execute(sql, params)


@contextmanager
def get_conn():
    if IS_POSTGRES:
        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        try:
            yield _Conn(conn, True)
            conn.commit()
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield _Conn(conn, False)
            conn.commit()
        finally:
            conn.close()


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


# ----------------------------------------------------------------------
# スキーマ初期化（方言別DDL。テーブル・列・制約の意味は両方言で同一）
# ----------------------------------------------------------------------
def init_db() -> None:
    if IS_POSTGRES:
        _init_db_postgres()
    else:
        _init_db_sqlite()


def _init_db_postgres() -> None:
    conn = psycopg.connect(DATABASE_URL)
    try:
        cur = conn.cursor()
        # 受験履歴。source 列で固定プール / RAG を分離。
        # 生成メタ（レイテンシ・トークン・観点id等）は details JSON に格納する。
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS attempts (
                id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
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
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS unit_progress (
                id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                username      TEXT    NOT NULL,
                level         TEXT    NOT NULL,
                unit_id       TEXT    NOT NULL,
                source        TEXT    NOT NULL DEFAULT 'pool',
                streak_count  INTEGER NOT NULL DEFAULT 0,
                best_streak   INTEGER NOT NULL DEFAULT 0,
                perfect_count INTEGER NOT NULL DEFAULT 0,
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
        # 旧スキーマからの移行保険（PostgreSQL は IF NOT EXISTS が使える）
        cur.execute(
            "ALTER TABLE unit_progress ADD COLUMN IF NOT EXISTS perfect_count INTEGER NOT NULL DEFAULT 0"
        )

        # RAGの一時問題プール。正答・解説込みで session 単位に保持（フロントへは返さない）。
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
                meta        TEXT,
                pending     TEXT
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_quiz_sessions_expires "
            "ON quiz_sessions(expires_at)"
        )
        cur.execute("ALTER TABLE quiz_sessions ADD COLUMN IF NOT EXISTS pending TEXT")

        # 認証: ユーザーアカウント（メール＋パスワード）。
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                email         TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name  TEXT NOT NULL,
                created_at    TEXT NOT NULL
            )
            """
        )
        # 認証: ログインセッション（トークンはSHA-256のみ保存）。
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id    BIGINT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires ON auth_sessions(expires_at)"
        )
        # アカウント化: 既存テーブルへ user_id を追加（usernameは互換のため残す＝壊さず足す）。
        cur.execute("ALTER TABLE attempts ADD COLUMN IF NOT EXISTS user_id BIGINT")
        cur.execute("ALTER TABLE unit_progress ADD COLUMN IF NOT EXISTS user_id BIGINT")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_attempts_user_id ON attempts(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_unit_progress_user_id ON unit_progress(user_id)")

        # チャレンジ（異議申し立て）。出題・採点への異議を受け付け、管理画面で裁定する。
        # 設問本文・正答・解説は ephemeral な quiz_sessions にしか無いため snapshot に保存する。
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS challenges (
                id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                user_id         BIGINT  NOT NULL,
                username        TEXT    NOT NULL,
                session_id      TEXT    NOT NULL,
                question_id     TEXT    NOT NULL,
                attempt_id      BIGINT,
                level           TEXT    NOT NULL,
                unit_id         TEXT    NOT NULL,
                source          TEXT    NOT NULL DEFAULT 'rag',
                kind            TEXT,
                reason          TEXT    NOT NULL,
                snapshot        TEXT    NOT NULL,
                status          TEXT    NOT NULL DEFAULT 'open',
                scoring_applied INTEGER NOT NULL DEFAULT 0,
                admin_message   TEXT,
                admin_note      TEXT,
                created_at      TEXT    NOT NULL,
                resolved_at     TEXT,
                closed_at       TEXT,
                UNIQUE (user_id, question_id)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_challenges_status ON challenges(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_challenges_user_id ON challenges(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_challenges_attempt_id ON challenges(attempt_id)")
        conn.commit()
    finally:
        conn.close()


def _init_db_sqlite() -> None:
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
        # 累計クリア方式用: 通算満点回数。既存DBにも安全に列を足す。
        up_cols = {
            r[1] for r in cur.execute("PRAGMA table_info(unit_progress)").fetchall()
        }
        if "perfect_count" not in up_cols:
            cur.execute(
                "ALTER TABLE unit_progress ADD COLUMN perfect_count INTEGER NOT NULL DEFAULT 0"
            )

        # RAGの一時問題プール。正答・解説込みで session 単位に保持（フロントへは返さない）。
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
        # ヘッド／テイル分割用: テイルの未消化観点を保持する列。既存DBにも安全に足す。
        existing_cols = {
            r[1] for r in cur.execute("PRAGMA table_info(quiz_sessions)").fetchall()
        }
        if "pending" not in existing_cols:
            cur.execute("ALTER TABLE quiz_sessions ADD COLUMN pending TEXT")

        # 認証: ユーザーアカウント（メール＋パスワード）。
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name  TEXT NOT NULL,
                created_at    TEXT NOT NULL
            )
            """
        )
        # 認証: ログインセッション（トークンはSHA-256のみ保存）。
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires ON auth_sessions(expires_at)"
        )
        # アカウント化: 既存テーブルへ user_id を追加（usernameは互換のため残す＝壊さず足す）。
        at_cols = {r[1] for r in cur.execute("PRAGMA table_info(attempts)").fetchall()}
        if "user_id" not in at_cols:
            cur.execute("ALTER TABLE attempts ADD COLUMN user_id INTEGER")
        up_cols2 = {r[1] for r in cur.execute("PRAGMA table_info(unit_progress)").fetchall()}
        if "user_id" not in up_cols2:
            cur.execute("ALTER TABLE unit_progress ADD COLUMN user_id INTEGER")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_attempts_user_id ON attempts(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_unit_progress_user_id ON unit_progress(user_id)")

        # チャレンジ（異議申し立て）。出題・採点への異議を受け付け、管理画面で裁定する。
        # 設問本文・正答・解説は ephemeral な quiz_sessions にしか無いため snapshot に保存する。
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS challenges (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                username        TEXT    NOT NULL,
                session_id      TEXT    NOT NULL,
                question_id     TEXT    NOT NULL,
                attempt_id      INTEGER,
                level           TEXT    NOT NULL,
                unit_id         TEXT    NOT NULL,
                source          TEXT    NOT NULL DEFAULT 'rag',
                kind            TEXT,
                reason          TEXT    NOT NULL,
                snapshot        TEXT    NOT NULL,
                status          TEXT    NOT NULL DEFAULT 'open',
                scoring_applied INTEGER NOT NULL DEFAULT 0,
                admin_message   TEXT,
                admin_note      TEXT,
                created_at      TEXT    NOT NULL,
                resolved_at     TEXT,
                closed_at       TEXT,
                UNIQUE (user_id, question_id)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_challenges_status ON challenges(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_challenges_user_id ON challenges(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_challenges_attempt_id ON challenges(attempt_id)")
        conn.commit()
    finally:
        conn.close()


def save_attempt(
    username: str,
    level: str,
    score: int,
    total: int,
    details: str,
    source: str = SOURCE_POOL,
    taken_at: Optional[str] = None,
    user_id: Optional[int] = None,
) -> int:
    """受験1回分を保存する。

    details は呼び出し側で組み立てた JSON 文字列。単元情報（unit）と
    RAG生成メタ（レイテンシ・トークン等）はこの details JSON の中に格納する。
    source は 'pool'（固定プール） / 'rag'（RAG）。
    taken_at は通常未指定（現在時刻）。デモデータ生成（routes_dev）が過去日時を
    ばらまく用途でのみ指定する。
    """
    with get_conn() as conn:
        base_sql = """
            INSERT INTO attempts (username, level, source, score, total, taken_at, details, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """
        params = (username, level, source, score, total, taken_at or _now_iso(), details, user_id)
        if IS_POSTGRES:
            # PostgreSQL に lastrowid は無いため RETURNING で採番値を受け取る
            cur = conn.execute(base_sql.rstrip() + " RETURNING id", params)
            return cur.fetchone()["id"]
        cur = conn.execute(base_sql, params)
        return cur.lastrowid


def _extract_attempt_meta(details_raw: Optional[str]) -> dict:
    """details JSON から履歴表示用のメタ情報を取り出す。

    details 構造（本リポジトリ）:
      {"meta": {"unit": "...", "metrics": {...}}, "answers": [...]}
    壊れた JSON では unit=None / metrics=None を返す。
    """
    meta = {"unit": None, "metrics": None}
    if not details_raw:
        return meta
    try:
        parsed = json.loads(details_raw)
    except (ValueError, TypeError):
        return meta
    if isinstance(parsed, dict):
        m = parsed.get("meta") or {}
        meta["unit"] = m.get("unit")
        meta["metrics"] = m.get("metrics")
    return meta


# ----------------------------------------------------------------------
# 単元進捗
# ----------------------------------------------------------------------
def update_unit_progress(
    username: str,
    level: str,
    unit_id: str,
    perfect: bool,
    clear_streak_required: Optional[int] = None,
    source: str = SOURCE_POOL,
    user_id: Optional[int] = None,
) -> dict:
    """累計方式の進捗更新。満点なら通算満点回数 perfect_count を +1（非満点でも減らさない）。

    通算満点回数が clear_streak_required に達した時点で、その単元をそつぎょうとみなす
    （graduated_at を初記録）。streak_count は「現在の連続満点数」を情報として保持する
    （満点で+1、非満点で0リセット）が、そつぎょう判定には用いない。

    Args:
        clear_streak_required: そつぎょうに必要な通算満点回数。
            未指定（None）なら config.UNIT_CLEAR_REQUIRED_STREAK を用いる
            （閾値の真実源を config に一本化し、ここでの数値の二重定義を避ける）。
        source: 'pool' / 'rag'。方式ごとに進捗を独立管理する。

    Returns:
        更新後の進捗 dict（perfect_count, streak_count, best_streak, last_taken_at,
        graduated_at, newly_cleared）。
        newly_cleared は今回のサブミットで初めてそつぎょう条件に達したかどうか。
    """
    now = _now_iso()
    if clear_streak_required is None:
        # 真実源は config.UNIT_CLEAR_REQUIRED_STREAK（遅延importで循環を避ける）
        from backend.config import UNIT_CLEAR_REQUIRED_STREAK
        clear_streak_required = UNIT_CLEAR_REQUIRED_STREAK

    with get_conn() as conn:
        # まず行を確保（無ければ作る）。挿入の衝突無視は方言差があるため分岐する
        # （SQLite: INSERT OR IGNORE / PostgreSQL: ON CONFLICT DO NOTHING）。
        ensure_cols = """
              (username, level, unit_id, source, perfect_count, streak_count, best_streak, last_taken_at, user_id)
            VALUES (?, ?, ?, ?, 0, 0, 0, NULL, ?)
            """
        if IS_POSTGRES:
            conn.execute(
                "INSERT INTO unit_progress" + ensure_cols
                + " ON CONFLICT (username, level, unit_id, source) DO NOTHING",
                (username, level, unit_id, source, user_id),
            )
        else:
            conn.execute(
                "INSERT OR IGNORE INTO unit_progress" + ensure_cols,
                (username, level, unit_id, source, user_id),
            )

        row = conn.execute(
            """
            SELECT perfect_count, streak_count, best_streak, graduated_at
            FROM unit_progress
            WHERE username = ? AND level = ? AND unit_id = ? AND source = ?
            """,
            (username, level, unit_id, source),
        ).fetchone()

        prev_perfect = row["perfect_count"]
        prev_streak = row["streak_count"]
        best_streak = row["best_streak"]
        graduated_at = row["graduated_at"]

        # 累計: 満点で +1、外しても据え置き（減らさない）
        new_perfect = prev_perfect + 1 if perfect else prev_perfect
        # 連続: 情報用に保持（満点で+1、非満点で0）
        new_streak = prev_streak + 1 if perfect else 0
        if new_streak > best_streak:
            best_streak = new_streak

        # 初そつぎょう判定: 通算満点が閾値に達し、過去にそつぎょう記録が無い
        newly_cleared = False
        if new_perfect >= clear_streak_required and graduated_at is None:
            graduated_at = now
            newly_cleared = True

        conn.execute(
            """
            UPDATE unit_progress
            SET perfect_count = ?,
                streak_count  = ?,
                best_streak   = ?,
                last_taken_at = ?,
                graduated_at  = ?
            WHERE username = ? AND level = ? AND unit_id = ? AND source = ?
            """,
            (new_perfect, new_streak, best_streak, now, graduated_at, username, level, unit_id, source),
        )

        return {
            "perfect_count": new_perfect,
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
    pending: Optional[dict] = None,
) -> None:
    """生成済みの問題（正答・解説込み）を session 単位で保存する。

    pending: テイル（残り問題）の未消化観点など。ヘッド／テイル分割時に持たせ、
             テイル生成後は None でクリアする。一括生成時は None。
    """
    now = datetime.utcnow()
    expires = now + timedelta(seconds=ttl_sec)
    cols_values = """
          (session_id, username, level, unit_id, created_at, expires_at, questions, meta, pending)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
    params = (
        session_id,
        username,
        level,
        unit_id,
        now.isoformat(timespec="seconds") + "Z",
        expires.isoformat(timespec="seconds") + "Z",
        json.dumps(questions, ensure_ascii=False),
        json.dumps(meta, ensure_ascii=False),
        json.dumps(pending, ensure_ascii=False) if pending is not None else None,
    )
    with get_conn() as conn:
        if IS_POSTGRES:
            # 同一 session_id への再保存は全列を上書き（SQLite の INSERT OR REPLACE と同義）
            conn.execute(
                "INSERT INTO quiz_sessions" + cols_values
                + """ ON CONFLICT (session_id) DO UPDATE SET
                       username   = EXCLUDED.username,
                       level      = EXCLUDED.level,
                       unit_id    = EXCLUDED.unit_id,
                       created_at = EXCLUDED.created_at,
                       expires_at = EXCLUDED.expires_at,
                       questions  = EXCLUDED.questions,
                       meta       = EXCLUDED.meta,
                       pending    = EXCLUDED.pending""",
                params,
            )
        else:
            conn.execute("INSERT OR REPLACE INTO quiz_sessions" + cols_values, params)


def update_quiz_session_questions(
    session_id: str,
    questions: List[dict],
    meta: dict,
    pending: Optional[dict] = None,
) -> None:
    """既存セッションに問題を追記する（テイル生成後）。

    created_at / expires_at は保持し、questions・meta・pending のみ差し替える。
    """
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE quiz_sessions
               SET questions = ?, meta = ?, pending = ?
             WHERE session_id = ?
            """,
            (
                json.dumps(questions, ensure_ascii=False),
                json.dumps(meta, ensure_ascii=False),
                json.dumps(pending, ensure_ascii=False) if pending is not None else None,
                session_id,
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
        # pending は dict 化したものに加え、CAS（claim_quiz_session_pending）の
        # 比較対象として DB 上の生JSON文字列も保持する。
        d["pending_raw"] = d.get("pending") or None
        d["pending"] = json.loads(d["pending"]) if d.get("pending") else None
        return d


def claim_quiz_session_pending(session_id: str, expected_pending_raw: str) -> bool:
    """テイル生成権を原子的に取得する（compare-and-swap）。

    DB上の pending が expected_pending_raw（取得時の生JSON）と一致する場合のみ
    NULL にクリアして True を返す。一致しなければ（他リクエストが先に取得済み）
    何もせず False を返す。同時リクエストによるテイルの二重生成・二重追記を防ぐ。
    """
    with get_conn() as conn:
        cur = conn.execute(
            """
            UPDATE quiz_sessions
               SET pending = NULL
             WHERE session_id = ? AND pending = ?
            """,
            (session_id, expected_pending_raw),
        )
        return cur.rowcount == 1


def restore_quiz_session_pending(session_id: str, pending_raw: str) -> None:
    """クレーム後にテイル生成が失敗した場合、pending を復元してリトライ可能に戻す。

    pending が NULL（=自分がクレームしたまま）の行にのみ書き戻す。
    """
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE quiz_sessions
               SET pending = ?
             WHERE session_id = ? AND pending IS NULL
            """,
            (pending_raw, session_id),
        )


def cleanup_expired_sessions() -> int:
    """期限切れ session を掃除する。削除件数を返す。"""
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM quiz_sessions WHERE expires_at <= ?", (_now_iso(),)
        )
        return cur.rowcount


# ----------------------------------------------------------------------
# 認証: ユーザー・ログインセッション
# ----------------------------------------------------------------------
def create_user(email: str, password_hash: str, display_name: str) -> Optional[int]:
    """ユーザーを作成して id を返す。メール重複時は None。"""
    email = email.strip().lower()
    with get_conn() as conn:
        dup = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if dup is not None:
            return None
        base_sql = """
            INSERT INTO users (email, password_hash, display_name, created_at)
            VALUES (?, ?, ?, ?)
            """
        params = (email, password_hash, display_name.strip(), _now_iso())
        if IS_POSTGRES:
            cur = conn.execute(base_sql.rstrip() + " RETURNING id", params)
            return cur.fetchone()["id"]
        cur = conn.execute(base_sql, params)
        return cur.lastrowid


def get_user_by_email(email: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, email, password_hash, display_name, created_at FROM users WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, email, display_name, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def list_users() -> List[dict]:
    """全アカウント（管理画面用。password_hash は返さない）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, email, display_name, created_at FROM users ORDER BY id ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def update_user_password(user_id: int, password_hash: str) -> bool:
    """パスワードを更新し、そのユーザーの全ログインセッションを失効させる。"""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id)
        )
        if cur.rowcount != 1:
            return False
        conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
        return True


def create_auth_session(token_hash: str, user_id: int, ttl_sec: int) -> None:
    now = datetime.utcnow()
    expires = now + timedelta(seconds=ttl_sec)
    with get_conn() as conn:
        # ついでに期限切れセッションを掃除する
        conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (_now_iso(),))
        conn.execute(
            """
            INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                token_hash,
                user_id,
                now.isoformat(timespec="seconds") + "Z",
                expires.isoformat(timespec="seconds") + "Z",
            ),
        )


def get_user_by_session(token_hash: str) -> Optional[dict]:
    """有効なセッションからユーザーを引く（期限切れ・不存在は None）。"""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT u.id, u.email, u.display_name, s.expires_at
            FROM auth_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        if d.pop("expires_at") <= _now_iso():
            conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
            return None
        return d


def delete_auth_session(token_hash: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))


# ----------------------------------------------------------------------
# 認証: user_id ベースのデータ取得（アカウント化後の本流）
# ----------------------------------------------------------------------
def get_progress_map_by_user_id(user_id: int, level: str, source: str = SOURCE_RAG) -> dict:
    """user_id × level × source の単元別進捗を {unit_id: progress_dict} で返す。"""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT unit_id, perfect_count, streak_count, best_streak, last_taken_at, graduated_at
            FROM unit_progress
            WHERE user_id = ? AND level = ? AND source = ?
            """,
            (user_id, level, source),
        ).fetchall()
        return {r["unit_id"]: dict(r) for r in rows}


def get_history_by_user_id(user_id: int, limit: int = 50):
    """user_id の受験履歴（新しい順）。"""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, level, source, score, total, taken_at, details
            FROM attempts
            WHERE user_id = ?
            ORDER BY taken_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            meta = _extract_attempt_meta(d.pop("details", None))
            d.update(meta)
            out.append(d)
        return out


def get_all_unit_progress_by_account(source: str = SOURCE_RAG) -> List[dict]:
    """user_id が紐づく進捗行のみ返す（管理画面のアカウント別一覧用）。"""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT user_id, level, unit_id, perfect_count, streak_count,
                   best_streak, last_taken_at, graduated_at
            FROM unit_progress
            WHERE source = ? AND user_id IS NOT NULL
            ORDER BY user_id ASC
            """,
            (source,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_user_account_records(user_id: int) -> None:
    """指定アカウントの受験記録・進捗・ログインセッション・アカウント本体を削除する。

    デモデータの再生成（routes_dev）専用。通常運用の経路からは呼ばない。
    """
    with get_conn() as conn:
        conn.execute("DELETE FROM attempts WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM unit_progress WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM challenges WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


# ----------------------------------------------------------------------
# チャレンジ（異議申し立て）
# ----------------------------------------------------------------------
def get_attempt_by_id(attempt_id: int) -> Optional[dict]:
    """受験1回分を id で取得（details は生JSON文字列のまま返す）。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, user_id, level, source, score, total, taken_at, details "
            "FROM attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()
        return dict(row) if row else None


def create_challenge(
    user_id: int,
    username: str,
    session_id: str,
    question_id: str,
    level: str,
    unit_id: str,
    source: str,
    kind: Optional[str],
    reason: str,
    snapshot_json: str,
) -> Optional[int]:
    """チャレンジを起票する。同一 (user_id, question_id) が既にあれば None を返す
    （1設問×1ユーザー＝1件。却下後も再起票不可）。"""
    now = _now_iso()
    with get_conn() as conn:
        exists = conn.execute(
            "SELECT id FROM challenges WHERE user_id = ? AND question_id = ?",
            (user_id, question_id),
        ).fetchone()
        if exists:
            return None
        sql = """
            INSERT INTO challenges
              (user_id, username, session_id, question_id, level, unit_id,
               source, kind, reason, snapshot, status, scoring_applied, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', 0, ?)
            """
        params = (
            user_id, username, session_id, question_id, level, unit_id,
            source, kind, reason, snapshot_json, now,
        )
        if IS_POSTGRES:
            cur = conn.execute(sql.rstrip() + " RETURNING id", params)
            return cur.fetchone()["id"]
        cur = conn.execute(sql, params)
        return cur.lastrowid


def link_challenges_to_attempt(session_id: str, attempt_id: int, user_id: int) -> None:
    """submit で受験が確定した直後、同一セッションの未紐付けチャレンジへ attempt_id を結ぶ。"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE challenges SET attempt_id = ? "
            "WHERE session_id = ? AND user_id = ? AND attempt_id IS NULL",
            (attempt_id, session_id, user_id),
        )


def get_challenge(challenge_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM challenges WHERE id = ?", (challenge_id,)
        ).fetchone()
        return dict(row) if row else None


def list_challenges(status: Optional[str] = None) -> List[dict]:
    """管理画面用のチャレンジ一覧（新しい順）。status 指定でフィルタ。"""
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM challenges WHERE status = ? ORDER BY created_at DESC, id DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM challenges ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def list_challenges_by_user(user_id: int) -> List[dict]:
    """受験者本人のチャレンジ一覧（マイページ用。新しい順）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, question_id, level, unit_id, reason, kind, snapshot, status, "
            "admin_message, created_at, resolved_at, closed_at "
            "FROM challenges WHERE user_id = ? ORDER BY created_at DESC, id DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def _increment_perfect_count_conn(conn, username, level, unit_id, source, user_id, now) -> None:
    """既存接続内で perfect_count を +1 し、単元クリア（通算満点）を再評価する。

    採点の遡及訂正専用。streak_count・last_taken_at は触らない
    （連続満点は情報用でクリア判定に使わないため、遡及で動かさない）。
    """
    from backend.config import UNIT_CLEAR_REQUIRED_STREAK
    ensure_cols = """
          (username, level, unit_id, source, perfect_count, streak_count, best_streak, last_taken_at, user_id)
        VALUES (?, ?, ?, ?, 0, 0, 0, NULL, ?)
        """
    if IS_POSTGRES:
        conn.execute(
            "INSERT INTO unit_progress" + ensure_cols
            + " ON CONFLICT (username, level, unit_id, source) DO NOTHING",
            (username, level, unit_id, source, user_id),
        )
    else:
        conn.execute(
            "INSERT OR IGNORE INTO unit_progress" + ensure_cols,
            (username, level, unit_id, source, user_id),
        )
    row = conn.execute(
        "SELECT perfect_count, graduated_at FROM unit_progress "
        "WHERE username = ? AND level = ? AND unit_id = ? AND source = ?",
        (username, level, unit_id, source),
    ).fetchone()
    new_perfect = row["perfect_count"] + 1
    graduated_at = row["graduated_at"]
    if new_perfect >= UNIT_CLEAR_REQUIRED_STREAK and graduated_at is None:
        graduated_at = now
    conn.execute(
        "UPDATE unit_progress SET perfect_count = ?, graduated_at = ? "
        "WHERE username = ? AND level = ? AND unit_id = ? AND source = ?",
        (new_perfect, graduated_at, username, level, unit_id, source),
    )


def accept_challenge(
    challenge_id: int,
    admin_message: Optional[str] = None,
    admin_note: Optional[str] = None,
) -> dict:
    """open のチャレンジを認容し、採点を遡及訂正する（単一トランザクション・冪等）。

    紐付く受験（attempt）があり、当該設問が不正解だった場合のみ「正解扱い」に反転して
    score を再計算する。満点へ跨いだ場合のみ perfect_count を +1（単元クリアに反映）。
    attempt が無い（中断起票）場合は採点反映なしでステータスのみ accepted にする。

    戻り値: {"ok": bool, "error"?: str, "scoring": {applied, new_score, became_perfect}}
    """
    now = _now_iso()
    with get_conn() as conn:
        ch = conn.execute(
            "SELECT * FROM challenges WHERE id = ?", (challenge_id,)
        ).fetchone()
        if ch is None:
            return {"ok": False, "error": "not_found"}
        if ch["status"] != "open":
            return {"ok": False, "error": "not_open"}

        scoring = {"applied": False, "new_score": None, "became_perfect": False}
        attempt_id = ch["attempt_id"]
        if attempt_id is not None and not ch["scoring_applied"]:
            att = conn.execute(
                "SELECT id, user_id, level, source, score, total, details "
                "FROM attempts WHERE id = ?",
                (attempt_id,),
            ).fetchone()
            details = None
            if att is not None and att["details"]:
                try:
                    details = json.loads(att["details"])
                except (ValueError, TypeError):
                    details = None
            if isinstance(details, dict):
                answers = details.get("answers", [])
                target = next(
                    (a for a in answers if a.get("id") == ch["question_id"]), None
                )
                # 不正解だった設問のみ正解へ反転（②内容異議で元々正解なら採点変化なし）
                if target is not None and not target.get("is_correct", False):
                    target["is_correct"] = True
                    new_score = sum(1 for a in answers if a.get("is_correct"))
                    was_perfect = att["score"] == att["total"]
                    if new_score > att["score"]:
                        conn.execute(
                            "UPDATE attempts SET score = ?, details = ? WHERE id = ?",
                            (new_score, json.dumps(details, ensure_ascii=False), att["id"]),
                        )
                        scoring["new_score"] = new_score
                        # 非満点→満点へ跨いだ時だけ通算満点を +1（二重計上しない）
                        if new_score == att["total"] and not was_perfect:
                            _increment_perfect_count_conn(
                                conn, ch["username"], ch["level"], ch["unit_id"],
                                ch["source"], att["user_id"], now,
                            )
                            scoring["became_perfect"] = True
                    scoring["applied"] = True

        conn.execute(
            "UPDATE challenges SET status = 'accepted', scoring_applied = 1, "
            "admin_message = ?, admin_note = ?, resolved_at = ? WHERE id = ?",
            (admin_message, admin_note, now, challenge_id),
        )
        return {"ok": True, "scoring": scoring}


def reject_challenge(
    challenge_id: int,
    admin_message: Optional[str] = None,
    admin_note: Optional[str] = None,
) -> dict:
    """open のチャレンジを却下（終端）にする。採点は変えない。"""
    now = _now_iso()
    with get_conn() as conn:
        ch = conn.execute(
            "SELECT status FROM challenges WHERE id = ?", (challenge_id,)
        ).fetchone()
        if ch is None:
            return {"ok": False, "error": "not_found"}
        if ch["status"] != "open":
            return {"ok": False, "error": "not_open"}
        conn.execute(
            "UPDATE challenges SET status = 'rejected', admin_message = ?, "
            "admin_note = ?, resolved_at = ? WHERE id = ?",
            (admin_message, admin_note, now, challenge_id),
        )
        return {"ok": True}


def close_challenge(challenge_id: int, admin_note: Optional[str] = None) -> dict:
    """accepted（処理済）のチャレンジを手動でクローズ（終端）にする。根本是正の完了印。"""
    now = _now_iso()
    with get_conn() as conn:
        ch = conn.execute(
            "SELECT status, admin_note FROM challenges WHERE id = ?", (challenge_id,)
        ).fetchone()
        if ch is None:
            return {"ok": False, "error": "not_found"}
        if ch["status"] != "accepted":
            return {"ok": False, "error": "not_accepted"}
        note = admin_note if admin_note is not None else ch["admin_note"]
        conn.execute(
            "UPDATE challenges SET status = 'closed', admin_note = ?, closed_at = ? WHERE id = ?",
            (note, now, challenge_id),
        )
        return {"ok": True}
