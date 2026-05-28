"""RAG出題の生成エンジン。

観点サンプリング → プロンプト構築 → LLM呼び出し → JSON検証 → リトライ、までを担う。
原本テキスト（rag_source）を根拠として渡し、観点1つにつき1問を生成させる。
LLM呼び出しは llm_call 引数で差し替え可能（テストではモックを注入する）。
"""
from __future__ import annotations

import json
import time
from typing import Callable, List, Optional, Tuple

from backend import rag_perspectives, rag_source
from backend.config import (
    ANTHROPIC_API_KEY,
    RAG_CHOICES,
    RAG_MAX_TOKENS,
    RAG_MODEL,
)

# LLM呼び出しの戻り値: (本文テキスト, usage: {"input_tokens": int, "output_tokens": int})
LLMCall = Callable[[list, str], Tuple[str, dict]]

_SYSTEM_INSTRUCTIONS = (
    "あなたは米国ビザ実務の検定問題を作成する専門家。"
    "以下の「原本」と「観点」に明記された内容のみに基づき、選択式問題を作成する。"
    "原本・観点にない事実・数値・条文を創作してはならない。"
    "難度は指定レベルに厳密に合わせる。"
    "設問文の語尾は「〜として正しいものはどれか。」等で統一する。"
    "解説は根拠を1〜2文で簡潔に述べる。"
    "出力は指定のJSONのみ。前後の説明文やMarkdownのコードフェンスを一切付けない。"
)


class RAGGenerationError(Exception):
    """RAG生成に失敗したことを表す（API未設定・JSON不正・件数不足など）。"""


def _build_user_prompt(
    level: str,
    unit_name: str,
    level_description: str,
    perspectives: List[dict],
    n_choices: int,
) -> str:
    """LLMへ渡すユーザープロンプトを組み立てる。

    原本テキストはキャッシュ効率のため system 側（キャッシュ対象ブロック）に置き、
    ここには難度・単元・観点・出力形式だけを入れる。
    """
    lines: List[str] = []
    lines.append(f"# 難度\n{level} … {level_description}")
    lines.append("")
    lines.append(f"# 単元\n{unit_name}")
    lines.append("")
    lines.append(f"# 出題する観点（各観点につき1問、計{len(perspectives)}問）")
    for p in perspectives:
        pages = ",".join(str(x) for x in p.get("source_pages", []))
        lines.append(
            f"- {p['id']}: {p['name']} / {p.get('summary','')} / 根拠ページ {pages}"
        )
    lines.append("")
    lines.append(
        "# 出力JSON形式（このスキーマちょうど。questions は上の観点と同数）\n"
        '{ "questions": [ { "perspective_id": "観点id", "question": "設問文", '
        f'"choices": [{"、".join([chr(34)+"選択肢"+str(i+1)+chr(34) for i in range(n_choices)])}], '
        '"answer_index": 0, "explanation": "解説", "source_pages": [21] } ] }\n'
        f"- choices はちょうど {n_choices} 個。\n"
        "- answer_index は0始まり（正答の選択肢の位置）。\n"
        "- 誤答は『ありそうだが原本に照らすと誤り』にする。明らかすぎる誤答は避ける。"
    )
    return "\n".join(lines)


def _real_llm_call(system_blocks: list, user_text: str) -> Tuple[str, dict]:
    """Anthropic Messages API を実呼び出しする。プロンプトキャッシュ利用。"""
    if not ANTHROPIC_API_KEY:
        raise RAGGenerationError(
            "ANTHROPIC_API_KEY が未設定です。RAG出題には API キーが必要です。"
        )
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=RAG_MODEL,
        max_tokens=RAG_MAX_TOKENS,
        system=system_blocks,
        messages=[{"role": "user", "content": user_text}],
    )
    text = "".join(block.text for block in resp.content if block.type == "text")
    usage = {
        "input_tokens": getattr(resp.usage, "input_tokens", None),
        "output_tokens": getattr(resp.usage, "output_tokens", None),
    }
    return text, usage


def _parse_and_validate(raw: str, expected_choices: int) -> List[dict]:
    """LLM応答JSONをパースして検証する。不正なら ValueError。"""
    text = raw.strip()
    # 念のためコードフェンスが付いた場合に剥がす
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    data = json.loads(text)
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("questions 配列が空または不正")
    out = []
    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            raise ValueError(f"questions[{i}] が dict でない")
        question = q.get("question")
        choices = q.get("choices")
        answer_index = q.get("answer_index")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"questions[{i}].question が不正")
        if not isinstance(choices, list) or len(choices) != expected_choices:
            raise ValueError(
                f"questions[{i}].choices は {expected_choices} 個必要（実際 {len(choices) if isinstance(choices, list) else 'N/A'}）"
            )
        if not all(isinstance(c, str) and c.strip() for c in choices):
            raise ValueError(f"questions[{i}].choices に空文字が含まれる")
        if not isinstance(answer_index, int) or not (0 <= answer_index < expected_choices):
            raise ValueError(f"questions[{i}].answer_index が範囲外")
        out.append(
            {
                "perspective_id": q.get("perspective_id", ""),
                "question": question.strip(),
                "choices": [c.strip() for c in choices],
                "answer": answer_index,  # 内部は0始まり（固定プールと同じ）
                "explanation": (q.get("explanation") or "").strip(),
                "source_pages": q.get("source_pages", []),
            }
        )
    return out


def generate(
    level: str,
    unit_id: str,
    n: int,
    seed: Optional[int] = None,
    llm_call: Optional[LLMCall] = None,
    max_retries: int = 2,
) -> dict:
    """観点をサンプリングし、LLMで n 問生成する。

    Returns:
        {"questions": [...], "metrics": {...}}
        questions は answer/explanation を含む内部形式。
    Raises:
        RAGGenerationError: 観点メタ不在・API未設定・検証失敗の最終リトライ超過など。
    """
    meta = rag_perspectives.get_meta(level, unit_id)
    if meta is None:
        raise RAGGenerationError(
            f"観点メタがありません: level={level}, unit={unit_id}"
        )

    perspectives, used_seed = rag_perspectives.sample_perspectives(
        level, unit_id, n, seed=seed
    )
    if not perspectives:
        raise RAGGenerationError(
            f"観点が0件です: level={level}, unit={unit_id}"
        )

    # 根拠テキスト: サンプリングした観点の source_pages を集約
    all_pages: List[int] = []
    for p in perspectives:
        for pg in p.get("source_pages", []):
            if pg not in all_pages:
                all_pages.append(pg)
    source_text = rag_source.text_for_pages(all_pages)
    grounding = "pdf" if source_text else "summary"

    user_prompt = _build_user_prompt(
        level=level,
        unit_name=meta.get("unit_name", unit_id),
        level_description=meta.get("level_description", ""),
        perspectives=perspectives,
        n_choices=RAG_CHOICES,
    )
    # システムブロック: 指示は静的。原本テキストは大きく同一単元の連続生成で
    # 使い回せるため、キャッシュ対象（ephemeral）ブロックとして置く。
    system_blocks = [{"type": "text", "text": _SYSTEM_INSTRUCTIONS}]
    if source_text:
        system_blocks.append(
            {
                "type": "text",
                "text": f"# 原本（この記述の範囲だけを使う）\n{source_text}",
                "cache_control": {"type": "ephemeral"},
            }
        )

    call = llm_call or _real_llm_call

    start = time.monotonic()
    usage = {"input_tokens": None, "output_tokens": None}
    last_err: Optional[Exception] = None
    questions: List[dict] = []
    attempts_used = 0
    for attempt in range(max_retries + 1):
        attempts_used = attempt + 1
        try:
            raw, usage = call(system_blocks, user_prompt)
            questions = _parse_and_validate(raw, RAG_CHOICES)
            break
        except RAGGenerationError:
            raise  # API未設定などは即時に上げる
        except Exception as e:  # JSON不正・検証失敗はリトライ対象
            last_err = e
            questions = []
    if not questions:
        raise RAGGenerationError(
            f"LLM応答の検証に失敗しました（{attempts_used}回試行）: {last_err}"
        )

    latency_ms = round((time.monotonic() - start) * 1000)
    metrics = {
        "model": RAG_MODEL,
        "latency_ms": latency_ms,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "seed": used_seed,
        "perspective_ids": [p["id"] for p in perspectives],
        "grounding": grounding,
        "retries": attempts_used - 1,
        "n_choices": RAG_CHOICES,
    }
    return {"questions": questions, "metrics": metrics}
