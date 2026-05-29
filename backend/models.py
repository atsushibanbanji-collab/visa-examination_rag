"""APIスキーマ定義"""
from typing import List, Optional
from pydantic import BaseModel, Field


class Answer(BaseModel):
    id: str
    choice: int  # 0-indexed


class SubmitRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    level: str
    unit: Optional[str] = None  # 単元ID
    answers: List[Answer]
    # 採点はこのセッションの正答辞書を引く（RAG出題専用）。
    session_id: str


class CheckRequest(BaseModel):
    """1問だけの即時正誤判定リクエスト（履歴・進捗には一切影響しない）。"""
    id: str
    choice: int  # 0-indexed
    # 正答はこのセッションから引く（RAG出題専用）。
    session_id: str


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
