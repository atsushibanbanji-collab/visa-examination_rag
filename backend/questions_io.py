"""問題データのCSV入出力。

外向きのCSV仕様:
  列: id, level, unit, category, question, choice_1, choice_2, choice_3,
      answer, explanation
  改行: \r\n
  文字コード: 出力は BOM 付き UTF-8。入力は BOM 付き/無し UTF-8 と CP932(Shift-JIS)
              を自動判定。
  answer: 1〜3（1始まり、choice_N の N に対応）。内部表現の 0-indexed とは
          ここで変換する。エディタは Excel での編集を想定。
  unit:   単元ID。空でも取り込み可能（警告扱い）。unit 列ごと存在しない旧CSV
          も受け付ける（既存CSVの後方互換性）。
"""
from __future__ import annotations

import csv
import io
from typing import Dict, List, Tuple

# CSV列定義（順序固定）。新フォーマット。
CSV_COLUMNS = [
    "id",
    "level",
    "unit",
    "category",
    "question",
    "choice_1",
    "choice_2",
    "choice_3",
    "answer",
    "explanation",
]

# 旧フォーマット（unit 列なし）。読み込み時のみ受け付ける。
LEGACY_CSV_COLUMNS = [
    "id",
    "level",
    "category",
    "question",
    "choice_1",
    "choice_2",
    "choice_3",
    "answer",
    "explanation",
]

# 受け入れるレベル
ALLOWED_LEVELS = ("beginner", "intermediate", "advanced")

# 上限値（防御的に大きめに取る）
MAX_ID_LEN = 100
MAX_UNIT_LEN = 50
MAX_TEXT_LEN = 4000  # question/explanation/choice 共通の上限


# ----------------------------------------------------------------------
# エクスポート
# ----------------------------------------------------------------------
def export_questions_to_csv(
    questions_data: Dict[str, List[dict]],
    levels: List[str],
) -> bytes:
    """指定レベルの問題を CSV バイト列で返す（BOM 付き UTF-8）。

    questions_data の構造:
        {"beginner": [{"id":..., "unit":..., "category":..., "question":...,
                       "choices":[...], "answer":int, "explanation":...}, ...],
         "intermediate": [...],
         "advanced": [...]}

    answer は 0-indexed なので、出力時に +1 する。
    """
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)

    for level in levels:
        if level not in ALLOWED_LEVELS:
            continue
        for q in questions_data.get(level, []):
            choices = q.get("choices", [])
            # 3択固定だが念のため不足分は空文字で埋める
            c = list(choices) + [""] * (3 - len(choices))
            writer.writerow(
                [
                    q.get("id", ""),
                    level,
                    q.get("unit", ""),
                    q.get("category", ""),
                    q.get("question", ""),
                    c[0],
                    c[1],
                    c[2],
                    int(q.get("answer", 0)) + 1,  # 0-idx → 1-idx
                    q.get("explanation", ""),
                ]
            )

    # BOM 付き UTF-8 で出力（Excel 互換）
    return buf.getvalue().encode("utf-8-sig")


# ----------------------------------------------------------------------
# インポート
# ----------------------------------------------------------------------
def _decode_csv_bytes(data: bytes) -> str:
    """BOM 付き/無し UTF-8 と CP932(Shift-JIS) を試して文字列に変換する。"""
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(
        "文字コードを判別できません。UTF-8 または Shift-JIS で保存してください。"
    )


def parse_questions_csv(
    data: bytes,
) -> Tuple[Dict[str, List[dict]], List[dict], List[dict]]:
    """CSV バイト列をパースしてバリデーション。

    Returns:
        (parsed_by_level, errors, warnings)

        parsed_by_level: {"beginner": [...], "intermediate": [...]} のような辞書。
                         CSV に出現したレベルだけがキーに入る。
        errors: [{"row": int, "message": str}, ...]  取り込みを止める致命的問題。
        warnings: [{"row": int, "message": str}, ...]  取り込みは通すが伝える事項。
                  unit 列欠落、unit 値が空、などはここに入る。

        errors が空でなければ parsed_by_level は採用してはならない。
    """
    errors: List[dict] = []
    warnings: List[dict] = []

    # まずデコード
    try:
        text = _decode_csv_bytes(data)
    except ValueError as e:
        return {}, [{"row": 0, "message": str(e)}], []

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return {}, [{"row": 0, "message": "CSV が空です。"}], []

    # ヘッダ検証。新旧フォーマットを受け付ける。
    header = [h.strip().lstrip("\ufeff") for h in rows[0]]
    if header == CSV_COLUMNS:
        legacy_mode = False
    elif header == LEGACY_CSV_COLUMNS:
        legacy_mode = True
        warnings.append(
            {
                "row": 1,
                "message": (
                    "旧フォーマット（unit 列なし）のCSVを取り込みました。"
                    "全行 unit が空として扱われます。エクスポートでは "
                    "新フォーマットで書き出されます。"
                ),
            }
        )
    else:
        return (
            {},
            [
                {
                    "row": 1,
                    "message": (
                        f"ヘッダが想定と異なります。期待: {','.join(CSV_COLUMNS)} "
                        f"または旧仕様 {','.join(LEGACY_CSV_COLUMNS)} / "
                        f"実際: {','.join(header)}"
                    ),
                }
            ],
            [],
        )

    expected_cols = LEGACY_CSV_COLUMNS if legacy_mode else CSV_COLUMNS

    # データ行の処理
    parsed_by_level: Dict[str, List[dict]] = {}
    seen_ids: Dict[str, int] = {}  # id → 出現行（重複検出用）

    for idx, raw_row in enumerate(rows[1:], start=2):  # Excel 行番号に合わせて 2 始まり
        # 完全な空行はスキップ（末尾の空行対策）
        if not any(cell.strip() for cell in raw_row):
            continue

        # 列数チェック
        if len(raw_row) != len(expected_cols):
            errors.append(
                {
                    "row": idx,
                    "message": f"列数が {len(expected_cols)} ではありません（実際: {len(raw_row)}）",
                }
            )
            continue

        row = dict(zip(expected_cols, [c.strip() for c in raw_row]))
        # 旧フォーマットなら unit を空で補完
        if legacy_mode:
            row["unit"] = ""

        # id
        qid = row["id"]
        if not qid:
            errors.append({"row": idx, "message": "id が空です。"})
            continue
        if len(qid) > MAX_ID_LEN:
            errors.append(
                {"row": idx, "message": f"id が長すぎます（{MAX_ID_LEN}文字以内）。"}
            )
            continue
        if qid in seen_ids:
            errors.append(
                {
                    "row": idx,
                    "message": f"id '{qid}' が重複しています（先出: {seen_ids[qid]} 行目）。",
                }
            )
            continue

        # level
        level = row["level"]
        if level not in ALLOWED_LEVELS:
            errors.append(
                {
                    "row": idx,
                    "message": f"level は {','.join(ALLOWED_LEVELS)} のいずれか（実際: '{level}'）。",
                }
            )
            continue

        # unit（空は警告、長さチェックのみ）
        unit = row.get("unit", "")
        if not unit:
            warnings.append(
                {
                    "row": idx,
                    "message": f"unit が空です（id={qid}）。この問題は単元クイズに出題されません。",
                }
            )
        elif len(unit) > MAX_UNIT_LEN:
            errors.append(
                {"row": idx, "message": f"unit が長すぎます（{MAX_UNIT_LEN}文字以内）。"}
            )
            continue

        # question
        question = row["question"]
        if not question:
            errors.append({"row": idx, "message": "question が空です。"})
            continue
        if len(question) > MAX_TEXT_LEN:
            errors.append(
                {"row": idx, "message": f"question が長すぎます（{MAX_TEXT_LEN}文字以内）。"}
            )
            continue

        # choices
        choices = [row["choice_1"], row["choice_2"], row["choice_3"]]
        if not all(choices):
            errors.append(
                {
                    "row": idx,
                    "message": "choice_1〜choice_3 はすべて必須です（空の選択肢があります）。",
                }
            )
            continue
        if any(len(c) > MAX_TEXT_LEN for c in choices):
            errors.append(
                {"row": idx, "message": f"choice の長さが {MAX_TEXT_LEN} 文字を超えています。"}
            )
            continue

        # answer
        ans_raw = row["answer"]
        try:
            ans = int(ans_raw)
        except ValueError:
            errors.append(
                {
                    "row": idx,
                    "message": f"answer は整数で指定してください（実際: '{ans_raw}'）。",
                }
            )
            continue
        if ans not in (1, 2, 3):
            errors.append(
                {
                    "row": idx,
                    "message": f"answer は 1〜3 の整数で指定してください（実際: {ans}）。",
                }
            )
            continue

        # explanation（空は許容）
        explanation = row["explanation"]
        if len(explanation) > MAX_TEXT_LEN:
            errors.append(
                {
                    "row": idx,
                    "message": f"explanation が長すぎます（{MAX_TEXT_LEN}文字以内）。",
                }
            )
            continue

        seen_ids[qid] = idx
        parsed_by_level.setdefault(level, []).append(
            {
                "id": qid,
                "unit": unit,
                "category": row["category"],  # 空も許容
                "question": question,
                "choices": choices,
                "answer": ans - 1,  # 1-idx → 0-idx
                "explanation": explanation,
            }
        )

    # データ行が一件も無いケースもエラー
    if not parsed_by_level and not errors:
        errors.append({"row": 0, "message": "有効なデータ行がありません。"})

    return parsed_by_level, errors, warnings
