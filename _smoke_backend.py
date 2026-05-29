"""バックエンドのスモークテスト（RAG出題専用）。本番DBは汚さない。

RAGのLLM呼び出しはモックに差し替えて、APIキー無し・トークン消費なしで
セッション保存→出題→即時判定→採点→進捗 までの一連を検証する。
固定プール方式のエンドポイントが消えている（404）ことも確認する。
"""
import json
import os
import pathlib
import sys
import tempfile

tmpdb = pathlib.Path(tempfile.gettempdir()) / "rag_only_smoke.db"
os.environ["DATABASE_PATH"] = str(tmpdb)
os.environ["ADMIN_TOKEN"] = "checktok"
os.environ["RAG_CHOICES"] = "3"
tmpdb.unlink(missing_ok=True)

from fastapi.testclient import TestClient

from backend import rag_generator
from backend.main import app


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
    return json.dumps({"questions": qs}, ensure_ascii=False), {"input_tokens": 1234, "output_tokens": 567}


rag_generator._real_llm_call = _fake_llm

c = TestClient(app)
T = "checktok"
ng = []


def chk(cond, label):
    print(("OK " if cond else "NG ") + label)
    if not cond:
        ng.append(label)


# ============ RAG出題 ============
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
chk("mode" not in start, "[rag] start応答に mode キー無し")

# 即時判定（session_id 経由）
q0 = start["questions"][0]
ck = c.post("/api/quiz/check", json={"id": q0["id"], "choice": 0, "session_id": sid}).json()
chk(ck["correct_choice"] == 0 and ck["is_correct"] is True, "[rag] check: 正答判定")
# session_id 無しは 400
chk(c.post("/api/quiz/check", json={"id": q0["id"], "choice": 0}).status_code == 422,
    "[rag] check: session_id必須(422)")

# 採点 → streak=1
rag_answers = [{"id": q["id"], "choice": 0} for q in start["questions"]]
rs = c.post("/api/quiz/submit", json={"username": "raguser", "level": "beginner", "unit": "b_visa",
            "session_id": sid, "answers": rag_answers}).json()
chk(rs["score"] == 10 and rs["passed"] is True, "[rag] submit: 満点")
chk(rs["unit_progress"]["streak_count"] == 1, "[rag] submit: streak=1")
chk("mode" not in rs, "[rag] submit応答に mode キー無し")

# session_id 無しの submit は 422（必須フィールド）
bad = c.post("/api/quiz/submit", json={"username": "x", "level": "beginner", "unit": "b_visa",
             "answers": [{"id": "a", "choice": 0}]})
chk(bad.status_code == 422, "[rag] submit: session_id必須(422)")

# 不正 session は 404
bad2 = c.post("/api/quiz/submit", json={"username": "x", "level": "beginner", "unit": "b_visa",
              "session_id": "sess_nope", "answers": [{"id": "sess_nope#0", "choice": 0}]})
chk(bad2.status_code == 404, "[rag] 不正sessionは404")

# 履歴に source=rag
hist = c.get("/api/history?username=raguser").json()["attempts"]
chk(any(a["source"] == "rag" for a in hist), "[history] source=rag が記録される")

# ============ 固定プール方式の経路が消えたこと ============
chk(c.get("/api/levels").status_code == 404, "[gone] /api/levels が404")
chk(c.get("/api/units?level=beginner&user=u").status_code == 404, "[gone] /api/units が404")
chk(c.get("/api/quiz/start?level=beginner&unit=b_visa").status_code == 404, "[gone] /api/quiz/start が404")
chk(c.get("/api/quiz/graduation/start?level=beginner&user=u").status_code == 404, "[gone] /api/quiz/graduation/start が404")
chk(c.get(f"/api/{T}/admin/comparison").status_code == 404, "[gone] /admin/comparison が404")
chk(c.get(f"/api/{T}/admin/meta").status_code == 404, "[gone] /admin/meta が404")
chk(c.get(f"/api/{T}/admin/questions/export?level=all").status_code == 404, "[gone] CSV export が404")

# ============ 残すべき admin ============
chk(c.get(f"/api/{T}/admin/attempts").status_code == 200, "[admin] attempts 200")
chk(c.get(f"/api/{T}/admin/users").status_code == 200, "[admin] users 200")

print("\n" + ("=== 全通過 ===" if not ng else f"=== 失敗 {ng} ==="))
sys.exit(1 if ng else 0)
