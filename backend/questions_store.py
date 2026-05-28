"""問題マスタ（questions.json）の状態管理を一手に引き受けるモジュール。

main.py に散在していた次の責務をここへ集約する:
  - questions.json のロードとメモリ保持（QUESTIONS_DATA）
  - id → 問題 の索引（QUESTION_BY_ID）
  - 書き換え時のロック（QUESTIONS_LOCK）
  - 単元メタ（_units）の取得
  - 単元プールの取得
  - アトミックな書き換え（バックアップ付き）

重要な設計上の注意:
  QUESTIONS_DATA は CSV インポートで丸ごと差し替わる。各ルーターが
  `from questions_store import QUESTIONS_DATA` のように「値」を import すると、
  差し替え後に古い参照を掴んだままになる。これを避けるため、外部からは必ず
  関数（get_questions_data() / pool_for_unit() など）経由でアクセスすること。
  モジュール属性を直接束縛しないのがこのモジュールの約束事。

挙動は従来の main.py と同一。ロジックは一切変えていない。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from backend.config import QUESTIONS_PATH
from backend.questions_io import ALLOWED_LEVELS

# --- モジュール内状態（外部から直接触らない）---
_LOCK = threading.Lock()
_QUESTIONS_DATA: dict = {}
_QUESTION_BY_ID: dict = {}


def _rebuild_index() -> None:
    """_QUESTION_BY_ID を _QUESTIONS_DATA から再生成する。

    レベルを跨いで id が重複していたケースは後勝ち（後のレベルが優先）。
    """
    global _QUESTION_BY_ID
    new_index = {}
    for level in ALLOWED_LEVELS:
        for q in _QUESTIONS_DATA.get(level, []):
            new_index[q["id"]] = q
    _QUESTION_BY_ID = new_index


def load() -> None:
    """questions.json をディスクから読み込み、索引を再構築する。

    アプリ起動時に一度呼ぶ。テストで差し替えたい場合も再度呼べる。
    """
    global _QUESTIONS_DATA
    with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
        _QUESTIONS_DATA = json.load(f)
    _rebuild_index()


# --- 読み取りアクセサ ---
def get_questions_data() -> dict:
    """現在の問題データ全体を返す（参照を返すので破壊的変更をしないこと）。"""
    return _QUESTIONS_DATA


def get_question_by_id(qid: str) -> Optional[dict]:
    """id から問題1件を引く。なければ None。"""
    return _QUESTION_BY_ID.get(qid)


def get_lock() -> threading.Lock:
    """エクスポート/インポートの一貫性確保に使うロックを返す。"""
    return _LOCK


def level_pool(level: str) -> List[dict]:
    """指定レベルの全問題プールを返す。"""
    return _QUESTIONS_DATA.get(level, [])


def pool_for_unit(level: str, unit_id: str) -> List[dict]:
    """指定レベル×単元の問題プールを返す。"""
    return [q for q in _QUESTIONS_DATA.get(level, []) if q.get("unit") == unit_id]


def units_meta(level: str) -> List[dict]:
    """指定レベルの _units メタ情報を order 順で返す。未定義なら空リスト。"""
    return _units_meta_from(_QUESTIONS_DATA, level)


def _units_meta_from(data: dict, level: str) -> List[dict]:
    """任意の data 辞書から _units を引く（差し替え前データの検証用）。"""
    return sorted(
        data.get("_units", {}).get(level, []),
        key=lambda u: u.get("order", 99),
    )


# --- 書き換え ---
def atomic_write(new_data: dict) -> Path:
    """questions.json をアトミックに書き換え、バックアップパスを返す。

    手順: 既存をバックアップ → 一時ファイルに書く → fsync → os.replace。
    途中失敗で questions.json が壊れることはない。
    呼び出し側は get_lock() のロック下で呼ぶこと。
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = QUESTIONS_PATH.with_name(f"{QUESTIONS_PATH.name}.bak.{timestamp}")
    if QUESTIONS_PATH.exists():
        shutil.copy2(QUESTIONS_PATH, backup_path)

    fd, tmp_path = tempfile.mkstemp(
        prefix=".questions-", suffix=".json.tmp", dir=str(QUESTIONS_PATH.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, QUESTIONS_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return backup_path


def replace_in_memory(new_data: dict) -> None:
    """メモリ上の問題データを差し替え、索引を再構築する。

    atomic_write でディスクへ書いた後に呼び、メモリとディスクを一致させる。
    """
    global _QUESTIONS_DATA
    _QUESTIONS_DATA = new_data
    _rebuild_index()
