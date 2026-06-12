"""APIスキーマ定義"""
from typing import List, Optional
from pydantic import BaseModel, Field


class Answer(BaseModel):
    id: str
    choice: Optional[int] = None  # 選択式（初級Yes/No・中級）の0始まり選択
    text_answers: Optional[List[str]] = None  # 穴埋め（上級）の各空欄の入力


class SubmitRequest(BaseModel):
    level: str
    unit: Optional[str] = None  # 単元ID
    answers: List[Answer]
    # 採点はこのセッションの正答辞書を引く（RAG出題専用）。
    session_id: str


class CheckRequest(BaseModel):
    """1問だけの即時正誤判定リクエスト（履歴・進捗には一切影響しない）。"""
    id: str
    choice: Optional[int] = None        # 選択式（初級Yes/No・中級）の0始まり選択
    text_answers: Optional[List[str]] = None  # 穴埋め（上級）の各空欄の入力
    # 正答はこのセッションから引く（RAG出題専用）。
    session_id: str


class RagStartRequest(BaseModel):
    """RAG出題の開始リクエスト。観点をサンプリングしてLLMで生成する。"""
    level: str
    unit: str


class RagContinueRequest(BaseModel):
    """RAG出題のテイル（残り問題）生成リクエスト。

    開始時に発行された session_id を渡し、未消化のテイル観点から残り問題を
    生成・追記する（ヘッド／テイル分割）。
    """
    session_id: str
