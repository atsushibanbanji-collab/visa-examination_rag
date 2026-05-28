"""APIスキーマ定義"""
from typing import List, Optional
from pydantic import BaseModel, Field


class Answer(BaseModel):
    id: str
    choice: int  # 0-indexed


class SubmitRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    level: str
    unit: Optional[str] = None  # 単元クイズの場合は unit_id、卒業試験は "__graduation__"
    answers: List[Answer]
    # 出題方式。'pool'（固定プール）/ 'rag'（RAG）。省略時は pool。
    mode: str = "pool"
    # RAG の場合のセッションID（採点はこのセッションの正答辞書を引く）。
    session_id: Optional[str] = None


class CheckRequest(BaseModel):
    """1問だけの即時正誤判定リクエスト（履歴・進捗には一切影響しない）。"""
    id: str
    choice: int  # 0-indexed
    # RAG の場合はセッションIDを添える（正答はセッションから引く）。
    session_id: Optional[str] = None


class RagStartRequest(BaseModel):
    """RAG出題の開始リクエスト。観点をサンプリングしてLLMで生成する。"""
    username: str = Field(..., min_length=1, max_length=50)
    level: str
    unit: str


class QuestionPublic(BaseModel):
    """フロントへ返す形（answer/explanationは含めない）"""
    id: str
    category: Optional[str] = None
    question: str
    choices: List[str]


class QuestionResult(BaseModel):
    id: str
    question: str
    choices: List[str]
    user_choice: int
    correct_choice: int
    is_correct: bool
    explanation: str = ""


class SubmitResponse(BaseModel):
    score: int
    total: int
    results: List[QuestionResult]
