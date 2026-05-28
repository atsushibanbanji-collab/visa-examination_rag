"""バックエンドのスモークテスト（固定プール + RAG）。本番DBは汚さない。

RAGのLLM呼び出しはモックに差し替えて、APIキー無し・トークン消費なしで
セッション保存→出題→即時判定→採点→進捗 までの一連を検証する。
"""
import json
import os
import pathlib
import sys
import tempfile

tmpdb = pathlib.Path(tempfile.gettempdir()) / "rag_smoke.db"
os.environ["DATABASE_PATH"] = str(tmpdb)
os.environ["ADMIN_TOKEN"] = "checktok"
os.environ["RAG_CHOICES"] = "3"
tmpdb.unlink(missing_ok=True)

from fastapi.testclient import TestClient

from backend import rag_generator
from backend.main import app

# --- RAGのLLM呼び出しをモック（10問・3択の合法JSONを返す）---
def _fake_llm(system_blocks, user_text):
    qs = []
    for i in range(10):
        qs.append(
            {
                "perspective_id": f"bv{i+1:02d}",
                "question": f"テスト設問{i+1}として正しいものはどれか。",
                "choices": ["正しい選択肢", "誤りの選択肢A", "誤りの選択肢B"],
                "answer_index": 0,
                "explanation": f"これはテスト解説{i+1}である。",
                "source_pages": [21],
            }
        )
    usage = {"input_tokens": 1234, "output_tokens": 567}
    return json.dumps({"questions": qs}, ensure_ascii=False), usage


rag_generator._real_llm_call = _fake_llm

c = TestClient(app)
T = "checktok"
ng = []


def chk(cond, label):
    print(("OK " if cond else "NG ") + label)
    if not cond:
        ng.append(label)


# ============ 固定プール方式（既存挙動が壊れていないか）============
levels = c.get("/api/levels").json()["levels"]
chk(next(x for x in levels if x["id"] == "beginner")["unit_count"] == 8, "[pool] levels unit_count=8")
u = c.get("/api/units?level=beginner&user=u").json()
chk(len(u["units"]) == 8 and u["graduation"]["unlocked"] is False, "[pool] units 8件・卒業ロック")
qs = c.get("/api/quiz/start?level=beginner&unit=b_visa").json()["questions"]
chk(len(qs) == 10, "[pool] b_visa 10問（160問プール）")


def pool_perfect(unit):
    qs = c.get(f"/api/quiz/start?level=beginner&unit={unit}").json()["questions"]
    pr = c.post("/api/quiz/submit", json={"username": "_p_", "level": "beginner", "unit": unit,
                "answers": [{"id": q["id"], "choice": 0} for q in qs]}).json()
    cor = {r["id"]: r["correct_choice"] for r in pr["results"]}
    return [{"id": q["id"], "choice": cor[q["id"]]} for q in qs]


r = c.post("/api/quiz/submit", json={"username": "m", "level": "beginner", "unit": "b_visa",
           "answers": pool_perfect("b_visa")}).json()
chk(r["unit_progress"]["streak_count"] == 1 and r["passed"] is True, "[pool] 満点で streak=1")
chk(r["mode"] == "pool", "[pool] submit mode=pool")

# ============ RAG方式 ============
cells = c.get("/api/rag/cells").json()
chk(len(cells["cells"]) == 10, "[rag] cells 10件（観点メタ10ファイル）")
chk(cells["source_available"] is True, "[rag] 原本PDF利用可能")

ru = c.get("/api/rag/units?level=beginner&user=raguser").json()
chk(len(ru["units"]) == 8, "[rag] beginner units 8件")

start = c.post("/api/rag/quiz/start", json={"username": "raguser", "level": "beginner", "unit": "b_visa"}).json()
sid = start.get("session_id")
chk(sid and len(start["questions"]) == 10, "[rag] start: session_id発行・10問")
chk("answer" not in start["questions"][0] and "explanation" not in start["questions"][0],
    "[rag] start: フロントに正答・解説を返さない")
chk(start["gen_metrics"]["grounding"] == "pdf", "[rag] grounding=pdf")
chk(start["gen_metrics"]["input_tokens"] == 1234, "[rag] メトリクス記録")

# RAG: 1問即時判定（session_id 経由）
q0 = start["questions"][0]
ck = c.post("/api/quiz/check", json={"id": q0["id"], "choice": 0, "session_id": sid}).json()
chk(ck["correct_choice"] == 0 and ck["is_correct"] is True, "[rag] check: session経由で正答判定")
chk(ck["explanation"].startswith("これはテスト解説"), "[rag] check: 解説が返る")

# RAG: 全問正解で submit → streak=1（source='rag'）
rag_answers = [{"id": q["id"], "choice": 0} for q in start["questions"]]
rs = c.post("/api/quiz/submit", json={"username": "raguser", "level": "beginner", "unit": "b_visa",
            "mode": "rag", "session_id": sid, "answers": rag_answers}).json()
chk(rs["mode"] == "rag" and rs["score"] == 10, "[rag] submit: 満点・mode=rag")
chk(rs["unit_progress"]["streak_count"] == 1, "[rag] submit: streak=1")

# RAG進捗が pool 進捗を汚染していないこと（独立性）
pool_prog = c.get("/api/units?level=beginner&user=raguser").json()
bvisa_pool = next(x for x in pool_prog["units"] if x["id"] == "b_visa")
chk(bvisa_pool["streak_count"] == 0, "[rag] pool進捗とRAG進捗が独立")

# RAG: 期限切れ/不正 session は404
bad = c.post("/api/quiz/submit", json={"username": "x", "level": "beginner", "unit": "b_visa",
             "mode": "rag", "session_id": "sess_nope", "answers": [{"id": "sess_nope#0", "choice": 0}]})
chk(bad.status_code == 404, "[rag] 不正sessionは404")

# 比較集計
comp = c.get(f"/api/{T}/admin/comparison").json()["comparison"]
chk("pool" in comp and "rag" in comp, "[admin] 比較集計に pool/rag 両方")
chk(comp["rag"]["avg_latency_ms"] is not None, "[admin] RAGレイテンシ集計")

# 履歴に source が乗る
hist = c.get("/api/history?username=raguser").json()["attempts"]
chk(any(a["source"] == "rag" for a in hist), "[history] source=rag が記録される")

print("\n" + ("=== 全通過 ===" if not ng else f"=== 失敗 {ng} ==="))
sys.exit(1 if ng else 0)
