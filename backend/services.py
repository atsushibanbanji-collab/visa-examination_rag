"""ドメインロジック（純粋関数寄り）。

エンドポイントから切り出した、HTTPに依存しない処理を集める。
挙動は従来の main.py と同一。
"""
from __future__ import annotations

from typing import List

from backend.config import UNIT_CLEAR_REQUIRED_STREAK


def sanitize_questions(qs: List[dict]) -> List[dict]:
    """フロントへ返す形式に削ぎ落とす。answer/explanation を含めない。"""
    return [
        {
            "id": q["id"],
            "category": q.get("category"),
            "unit": q.get("unit", ""),
            "question": q["question"],
            "choices": q["choices"],
        }
        for q in qs
    ]


def is_graduation_unlocked(progress_map: dict, units_meta: List[dict]) -> bool:
    """progress_map（unit_id → progress dict）から卒業試験ロック解除判定。

    units_meta が空のレベルは「卒業試験そのものが存在しない」とみなして False。
    """
    if not units_meta:
        return False
    return all(
        progress_map.get(u["id"], {}).get("best_streak", 0) >= UNIT_CLEAR_REQUIRED_STREAK
        for u in units_meta
    )


def count_cleared_units(progress_map: dict, units_meta: List[dict]) -> int:
    """クリア済み単元数を数える。"""
    return sum(
        1
        for u in units_meta
        if progress_map.get(u["id"], {}).get("best_streak", 0) >= UNIT_CLEAR_REQUIRED_STREAK
    )
