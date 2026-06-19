"""バックエンドのスモークテスト（RAG出題専用）。本番DBは汚さない。

RAGのLLM呼び出しはモックに差し替えて、APIキー無し・トークン消費なしで
セッション保存→出題→即時判定→採点→進捗 までの一連を検証する。
固定プール方式のエンドポイントが消えている（404）ことも確認する。
"""
import json
import os
import pathlib
import re
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
    # プロンプトの「計N問」を読み、要求された数ちょうど生成する。
    m = re.search(r"計(\d+)問", user_text)
    n = int(m.group(1)) if m else 10
    # 出力スキーマのヒントから出題形式を判別する。
    if '"blanks"' in user_text:
        fmt = "fill_in"
    elif '"answer": "yes"' in user_text:
        fmt = "yesno"
    else:
        fmt = "choice"

    qs = []
    for i in range(n):
        base = {"perspective_id": f"bv{i+1:02d}", "explanation": f"テスト解説{i+1}",
                "source_pages": [21]}
        if fmt == "yesno":
            base.update({"question": f"テスト断定文{i+1}は正しい。", "answer": "yes"})
        elif fmt == "fill_in":
            base.update({"question": f"テスト穴埋め{i+1}は ____ に基づく。",
                         "blanks": [{"variants": ["日米条約", "条約", "Treaty"]}]})
        else:
            base.update({"question": f"テスト設問{i+1}として正しいものはどれか。",
                         "choices": ["正しい選択肢", "誤りA", "誤りB"], "answer_index": 0})
        qs.append(base)
    return json.dumps({"questions": qs}, ensure_ascii=False), {"input_tokens": 1234, "output_tokens": 567}


rag_generator._real_llm_call = _fake_llm

c = TestClient(app)
T = "checktok"
ng = []


def chk(cond, label):
    print(("OK " if cond else "NG ") + label)
    if not cond:
        ng.append(label)


# ============ 認証（メール＋パスワード） ============
# 未ログインは401
_pre = c.get("/api/rag/units?level=beginner")
_me0 = c.get("/api/auth/me")

reg = c.post("/api/auth/register",
             json={"email": "RagUser@Example.com", "password": "pass1234", "display_name": "ラグ太郎"})
me = c.get("/api/auth/me").json() if reg.status_code == 200 else {}

# ============ RAG出題 ============
cells = c.get("/api/rag/cells").json()
chk(len(cells["cells"]) == 18, "[rag] cells 18件（ビザ種別6単元×初中上3レベル）")
chk(all(x["unit_id"] in {"b_visa","e_visa","f_visa","h1b_visa","j_visa","l_visa"} for x in cells["cells"]),
    "[rag] cells: ビザ種別単元のみ（永住権・基本は除外）")
chk(cells["source_available"] is True, "[rag] 原本PDF利用可能")

chk(_pre.status_code == 401 and _me0.status_code == 401, "[auth] 未ログインは401")
chk(reg.status_code == 200 and me.get("display_name") == "ラグ太郎"
    and me.get("email") == "raguser@example.com",
    "[auth] 登録→Cookieセッション発行→me取得（メールは小文字正規化）")
chk(c.post("/api/auth/register",
           json={"email": "raguser@example.com", "password": "pass1234",
                 "display_name": "二重"}).status_code == 409,
    "[auth] メール重複登録は409")
chk(c.post("/api/auth/login",
           json={"email": "raguser@example.com", "password": "WRONG"}).status_code == 401,
    "[auth] パスワード誤りは401")

ru = c.get("/api/rag/units?level=beginner").json()
chk(len(ru["units"]) == 6, "[rag] beginner units 6件（ビザ種別のみ）")
chk(ru.get("username") == "ラグ太郎", "[auth] units応答はログイン中の表示名")
chk(all(u["id"] != "green_card" for u in ru["units"]), "[rag] units: 永住権が一覧に出ない")
# 中級・上級にもビザ種別6単元が入った（answer形式で難度を制御）
chk(len(c.get("/api/rag/units?level=intermediate").json()["units"]) == 6,
    "[rag] intermediate units 6件")
chk(len(c.get("/api/rag/units?level=advanced").json()["units"]) == 6,
    "[rag] advanced units 6件")
# 除外単元への start 直打ちは 404（バックエンドの関所）
chk(c.post("/api/rag/quiz/start",
           json={"level": "beginner", "unit": "green_card"}).status_code == 404,
    "[rag] start: 除外単元(green_card)は404")

start = c.post("/api/rag/quiz/start", json={"level": "beginner", "unit": "b_visa"}).json()
sid = start.get("session_id")
chk(sid is not None, "[rag] start: session_id発行")
chk(len(start["questions"]) == 3, "[rag] start: ヘッド3問のみ返す")
chk(start.get("total_questions") == 10, "[rag] start: total_questions=10")
chk(start.get("head_count") == 3 and start.get("pending_count") == 7,
    "[rag] start: head=3 / pending=7")
chk("answer" not in start["questions"][0] and "explanation" not in start["questions"][0],
    "[rag] start: フロントに正答・解説を返さない")
chk(start["gen_metrics"]["grounding"] == "pdf", "[rag] grounding=pdf")
chk("mode" not in start, "[rag] start応答に mode キー無し")

# 即時判定（ヘッド問題で）
q0 = start["questions"][0]
ck = c.post("/api/quiz/check", json={"id": q0["id"], "choice": 0, "session_id": sid}).json()
chk(ck["correct_choice"] == 0 and ck["is_correct"] is True, "[rag] check: 正答判定")
# session_id 無しは 422
chk(c.post("/api/quiz/check", json={"id": q0["id"], "choice": 0}).status_code == 422,
    "[rag] check: session_id必須(422)")

# テイル（残り7問）を生成・追記
cont = c.post("/api/rag/quiz/continue", json={"session_id": sid}).json()
chk(len(cont["questions"]) == 7, "[rag] continue: テイル7問を返す")
chk("answer" not in cont["questions"][0] and "explanation" not in cont["questions"][0],
    "[rag] continue: フロントに正答・解説を返さない")
# 追記後のメトリクスは観点idがヘッド+テイルで10件に合算される
chk(len(cont["gen_metrics"].get("perspective_ids", [])) == 10,
    "[rag] continue: メトリクスの観点idが10件に合算")
# continue は冪等（再呼び出しは空を返す）
cont2 = c.post("/api/rag/quiz/continue", json={"session_id": sid}).json()
chk(cont2["questions"] == [], "[rag] continue: 2回目は空（冪等）")
# 不正 session の continue は 404
chk(c.post("/api/rag/quiz/continue", json={"session_id": "sess_nope"}).status_code == 404,
    "[rag] continue: 不正sessionは404")

# ヘッド+テイルの全10問が check で引けること（採点前の整合確認）
all_q = start["questions"] + cont["questions"]
chk(len(all_q) == 10, "[rag] ヘッド+テイルで計10問")
ck_tail = c.post("/api/quiz/check",
                 json={"id": all_q[-1]["id"], "choice": 0, "session_id": sid}).json()
chk(ck_tail.get("is_correct") is True, "[rag] check: テイル末尾問題も判定できる")

# 採点 → 通算満点 +1（全10問）
rag_answers = [{"id": q["id"], "choice": 0} for q in all_q]
rs = c.post("/api/quiz/submit", json={"level": "beginner", "unit": "b_visa",
            "session_id": sid, "answers": rag_answers}).json()
chk(rs["score"] == 10 and rs["passed"] is True, "[rag] submit: 満点")
chk(rs["unit_progress"]["perfect_count"] == 1 and rs["unit_progress"]["streak_count"] == 1,
    "[rag] submit: 通算満点=1")
chk("mode" not in rs, "[rag] submit応答に mode キー無し")

# 累計クリア方式の検証（満点を通算3回で graduated。途中で外しても減らない）
from backend import db as _db
P = ("累計太郎", "beginner", "b_visa")
r1 = _db.update_unit_progress(*P, perfect=True, source=_db.SOURCE_RAG)
chk(r1["perfect_count"] == 1 and r1["graduated_at"] is None, "[cum] 満点1回目: 通算1・未クリア")
r2 = _db.update_unit_progress(*P, perfect=False, source=_db.SOURCE_RAG)
chk(r2["perfect_count"] == 1 and r2["streak_count"] == 0,
    "[cum] 非満点: 通算は減らない（連続は0）")
r3 = _db.update_unit_progress(*P, perfect=True, source=_db.SOURCE_RAG)
r4 = _db.update_unit_progress(*P, perfect=True, source=_db.SOURCE_RAG)
chk(r4["perfect_count"] == 3 and r4["newly_cleared"] is True and r4["graduated_at"] is not None,
    "[cum] 通算3回目（非連続）で初クリア")
r5 = _db.update_unit_progress(*P, perfect=True, source=_db.SOURCE_RAG)
chk(r5["perfect_count"] == 4 and r5["newly_cleared"] is False,
    "[cum] クリア後は newly_cleared が立たない")

# session_id 無しの submit は 422（必須フィールド）
bad = c.post("/api/quiz/submit", json={"level": "beginner", "unit": "b_visa",
             "answers": [{"id": "a", "choice": 0}]})
chk(bad.status_code == 422, "[rag] submit: session_id必須(422)")

# 不正 session は 404
bad2 = c.post("/api/quiz/submit", json={"level": "beginner", "unit": "b_visa",
              "session_id": "sess_nope", "answers": [{"id": "sess_nope#0", "choice": 0}]})
chk(bad2.status_code == 404, "[rag] 不正sessionは404")

# 履歴に source=rag（Cookieベース＝ログイン中ユーザー自身の履歴）
hist = c.get("/api/history").json()["attempts"]
chk(any(a["source"] == "rag" for a in hist), "[history] source=rag が記録される")

# ============ チャレンジ（異議申し立て） ============
# （_db は累計クリア検証で import 済み）
# 新規に beginner e_visa を1回受験。先頭1問だけ誤答して 9/10 にする。
ev_start = c.post("/api/rag/quiz/start", json={"level": "beginner", "unit": "e_visa"}).json()
ev_sid = ev_start["session_id"]
ev_cont = c.post("/api/rag/quiz/continue", json={"session_id": ev_sid}).json()
ev_all = ev_start["questions"] + ev_cont["questions"]
chk(len(ev_all) == 10, "[challenge] 準備: e_visa 10問")
wrong_q = ev_all[0]   # choice=1（誤り）で回答する設問

# 起票（受験中の解説パネル相当）: 誤答した設問の採点に異議
chal = c.post("/api/quiz/challenge", json={
    "session_id": ev_sid, "question_id": wrong_q["id"],
    "reason": "この判定はおかしい", "kind": "grading", "choice": 1})
chk(chal.status_code == 200 and chal.json().get("challenge_id"), "[challenge] 起票できる")
chk("correct_choice" not in chal.json() and "snapshot" not in chal.json(),
    "[challenge] 起票応答に正答・スナップショットを返さない")
cid = chal.json()["challenge_id"]
# 同一設問への再起票は 409（1設問×1ユーザー＝1件）
chk(c.post("/api/quiz/challenge", json={
    "session_id": ev_sid, "question_id": wrong_q["id"],
    "reason": "再", "choice": 1}).status_code == 409, "[challenge] 同一設問の再起票は409")
# 別の正解設問にも内容異議を起票（後でクローズ系の検証に使う）
chal2 = c.post("/api/quiz/challenge", json={
    "session_id": ev_sid, "question_id": ev_all[1]["id"],
    "reason": "設問が事実と違う", "kind": "content", "choice": 0})
cid2 = chal2.json()["challenge_id"]
# さらに別の設問（却下用）
chal3 = c.post("/api/quiz/challenge", json={
    "session_id": ev_sid, "question_id": ev_all[2]["id"],
    "reason": "却下されるやつ", "kind": "content", "choice": 0})
cid3 = chal3.json()["challenge_id"]

# 採点（9/10・未満点）。submit でチャレンジが attempt へ紐付く。
ev_answers = [{"id": q["id"], "choice": (1 if i == 0 else 0)} for i, q in enumerate(ev_all)]
ev_rs = c.post("/api/quiz/submit", json={"level": "beginner", "unit": "e_visa",
               "session_id": ev_sid, "answers": ev_answers}).json()
chk(ev_rs["score"] == 9 and ev_rs["passed"] is False, "[challenge] 採点9/10（未満点）")
chk(ev_rs["unit_progress"]["perfect_count"] == 0, "[challenge] 認容前は通算満点0")
ev_attempt_id = ev_rs["attempt_id"]

# 受験者本人のチャレンジ一覧（マイページ）に未処理で出る
mine = c.get("/api/my/challenges").json()["challenges"]
chk(any(x["id"] == cid and x["status"] == "open" and x["status_label"] == "未処理" for x in mine),
    "[challenge] マイページに未処理で表示")

# 管理一覧（スナップショット込み）
adm = c.get(f"/api/{T}/admin/challenges").json()["challenges"]
ac = next((x for x in adm if x["id"] == cid), None)
chk(ac is not None and ac["status"] == "open" and ac["snapshot"].get("question"),
    "[challenge] 管理一覧にスナップショット込みで表示")
# status フィルタ
chk(all(x["status"] == "open" for x in
        c.get(f"/api/{T}/admin/challenges?status=open").json()["challenges"]),
    "[challenge] 管理一覧: statusフィルタ")

# 認容 → 採点遡及訂正（9→10）・通算満点 +1
acc = c.post(f"/api/{T}/admin/challenges/{cid}/accept",
             json={"admin_message": "ご指摘どおり訂正しました"})
chk(acc.status_code == 200 and acc.json()["scoring"]["became_perfect"] is True
    and acc.json()["scoring"]["new_score"] == 10, "[challenge] 認容で9→10満点化")
chk(_db.get_attempt_by_id(ev_attempt_id)["score"] == 10, "[challenge] 受験記録が10点に訂正")
ev_prog = _db.get_progress_map_by_user_id(me["id"], "beginner", source=_db.SOURCE_RAG)
chk(ev_prog.get("e_visa", {}).get("perfect_count") == 1, "[challenge] 認容で通算満点が1に")
# 再認容は 409（冪等・二重加算しない）
chk(c.post(f"/api/{T}/admin/challenges/{cid}/accept", json={}).status_code == 409,
    "[challenge] 認容済みの再認容は409")
# マイページに管理者メッセージ付きで反映
mine2 = c.get("/api/my/challenges").json()["challenges"]
mc = next((x for x in mine2 if x["id"] == cid), None)
chk(mc and mc["status"] == "accepted" and mc["status_label"] == "処理済"
    and mc["admin_message"] == "ご指摘どおり訂正しました",
    "[challenge] マイページに認容と管理者メッセージが反映")

# 認容（処理済）→ クローズ（手動・根本是正の完了印）
clo = c.post(f"/api/{T}/admin/challenges/{cid}/close",
             json={"admin_note": "観点summaryを修正済み"})
chk(clo.status_code == 200, "[challenge] 処理済→クローズできる")
chk(c.post(f"/api/{T}/admin/challenges/{cid}/close", json={}).status_code == 409,
    "[challenge] クローズ済みの再クローズは409")

# 内容異議（元々正解）を認容 → 採点は変わらない（became_perfect=False）
acc2 = c.post(f"/api/{T}/admin/challenges/{cid2}/accept", json={})
chk(acc2.status_code == 200 and acc2.json()["scoring"]["became_perfect"] is False
    and acc2.json()["scoring"]["new_score"] is None,
    "[challenge] 元々正解への内容異議は採点不変で認容")

# 却下 → 終端。クローズはできない（accepted のみ）。
rej = c.post(f"/api/{T}/admin/challenges/{cid3}/reject",
             json={"admin_message": "原本どおりで誤りなし"})
chk(rej.status_code == 200, "[challenge] 却下できる")
chk(c.post(f"/api/{T}/admin/challenges/{cid3}/close", json={}).status_code == 409,
    "[challenge] 却下後のクローズは409（accepted のみ）")
chk(c.post(f"/api/{T}/admin/challenges/{cid3}/accept", json={}).status_code == 409,
    "[challenge] 却下後の認容は409（終端）")
# 却下後も同一設問へ再起票できない（UNIQUE）
chk(c.post("/api/quiz/challenge", json={
    "session_id": ev_sid, "question_id": ev_all[2]["id"],
    "reason": "やっぱり納得いかない", "choice": 0}).status_code == 409,
    "[challenge] 却下後も同一設問は再起票不可")
# 認証必須（未ログインの起票は401）
anon = TestClient(app)
chk(anon.post("/api/quiz/challenge", json={
    "session_id": ev_sid, "question_id": ev_all[0]["id"], "reason": "x"}).status_code == 401,
    "[challenge] 未ログインの起票は401")

# ============ 項目5: レベル別出題形式 ============
from backend import rag_perspectives, rag_generator, rag_session_store
rag_perspectives.load()

# 初級は Yes/No（はい/いいえ の2択）
chk(start["questions"][0].get("type") == "choice", "[fmt] 初級: type=choice")
chk(start["questions"][0]["choices"] == ["はい", "いいえ"], "[fmt] 初級: 選択肢ははい/いいえ")
chk(start["gen_metrics"].get("format") == "yesno", "[fmt] 初級: format=yesno")

# 中級は選択式（RAG_CHOICES択）。単元はbasicsのみ＝start経路は対象外のため関数で検証
ips, _ = rag_perspectives.sample_perspectives("intermediate", "basics", 2)
igen = rag_generator.generate_questions("intermediate", "basics", ips, llm_call=_fake_llm)
chk(all(q["type"] == "choice" and len(q["choices"]) == 3 for q in igen["questions"]),
    "[fmt] 中級: choice型・3択で生成")
chk(igen["metrics"]["format"] == "choice", "[fmt] 中級: format=choice")

# 上級は穴埋め（fill_in）
aps, _ = rag_perspectives.sample_perspectives("advanced", "basics", 2)
agen = rag_generator.generate_questions("advanced", "basics", aps, llm_call=_fake_llm)
chk(all(q["type"] == "fill_in" and q.get("blanks") for q in agen["questions"]),
    "[fmt] 上級: fill_in型・blanks付きで生成")
chk("choices" not in agen["questions"][0] and "answer" not in agen["questions"][0],
    "[fmt] 上級: choices/answer を持たない")

# 上級採点を実APIで検証（セッションを手動生成して check/submit を通す）
adv_session = rag_session_store.create_session(
    username="advuser", level="advanced", unit_id="basics",
    questions=agen["questions"], metrics=agen["metrics"],
)
asid = adv_session["session_id"]
aq0 = adv_session["questions"][0]
chk(aq0.get("type") == "fill_in" and aq0.get("blank_count") == 1 and "choices" not in aq0,
    "[fmt] 上級: 公開問題は choices無し・blank_count付き")
# 正規化: variants の "Treaty" を全角小文字 "ｔｒｅａｔｙ" で答えても正解
ck_ok = c.post("/api/quiz/check",
    json={"id": aq0["id"], "text_answers": ["ｔｒｅａｔｙ"], "session_id": asid}).json()
chk(ck_ok["is_correct"] is True and ck_ok["type"] == "fill_in",
    "[fmt] 上級check: 全角/大小文字を正規化して正解")
chk(ck_ok["correct_answers"] == ["日米条約"], "[fmt] 上級check: 代表正解を返す")
ck_ng = c.post("/api/quiz/check",
    json={"id": aq0["id"], "text_answers": ["まったく違う"], "session_id": asid}).json()
chk(ck_ng["is_correct"] is False, "[fmt] 上級check: 候補外は不正解")
# submit（全問 variants で回答 → 満点）
adv_answers = [{"id": q["id"], "text_answers": ["日米条約"]} for q in adv_session["questions"]]
ars = c.post("/api/quiz/submit", json={"level": "advanced",
             "unit": "basics", "session_id": asid, "answers": adv_answers}).json()
chk(ars["score"] == len(adv_session["questions"]) and ars["score"] > 0,
    "[fmt] 上級submit: 穴埋めで満点採点")

# 複数空欄は全問正解のみ正解（部分点なし）
q_multi = {"type": "fill_in", "blanks": [{"variants": ["甲"]}, {"variants": ["乙"]}]}
chk(rag_session_store.grade_answer(q_multi, text_answers=["甲", "乙"])["is_correct"] is True,
    "[fmt] 複数空欄: 全部正解で正解")
chk(rag_session_store.grade_answer(q_multi, text_answers=["甲", "X"])["is_correct"] is False,
    "[fmt] 複数空欄: 一部誤りは不正解（部分点なし）")
chk(rag_session_store.grade_answer(q_multi, text_answers=["甲"])["is_correct"] is False,
    "[fmt] 複数空欄: 数不一致は不正解")

# 中級・上級も実API（start）経路で形式を確認（単元データを追加したため到達可能になった）
istart = c.post("/api/rag/quiz/start",
                json={"level": "intermediate", "unit": "e_visa"}).json()
chk(istart["questions"][0].get("type") == "choice" and len(istart["questions"][0]["choices"]) == 3,
    "[fmt] 中級start: choice型・3択")
chk(istart["gen_metrics"].get("format") == "choice", "[fmt] 中級start: format=choice")

astart = c.post("/api/rag/quiz/start",
                json={"level": "advanced", "unit": "e_visa"}).json()
chk(astart["questions"][0].get("type") == "fill_in" and astart["questions"][0].get("blank_count", 0) >= 1,
    "[fmt] 上級start: fill_in型・blank_count付き")
chk("choices" not in astart["questions"][0], "[fmt] 上級start: 公開問題にchoices無し")
chk(astart["gen_metrics"].get("format") == "fill_in", "[fmt] 上級start: format=fill_in")

# ============ 項目6: 認証ライフサイクル（テストモードは撤去済み） ============
# startリクエストの test フィールドは受け付けない（撤去確認: 余剰フィールドは無視され通常10問）
xt = c.post("/api/rag/quiz/start", json={"level": "beginner", "unit": "b_visa", "test": True}).json()
chk(xt.get("total_questions") == 10 and "test" not in xt,
    "[auth] テストモード撤去: test指定は無効・常に通常出題")

# パスワード自己変更 → 全セッション失効 → 旧パスワード不可・新パスワード可
pw = c.post("/api/auth/password",
            json={"current_password": "pass1234", "new_password": "newpass5678"}).json()
chk(pw.get("relogin_required") is True and c.get("/api/auth/me").status_code == 401,
    "[auth] パスワード変更で全セッション失効")
chk(c.post("/api/auth/login",
           json={"email": "raguser@example.com", "password": "pass1234"}).status_code == 401,
    "[auth] 旧パスワードは無効")
chk(c.post("/api/auth/login",
           json={"email": "raguser@example.com", "password": "newpass5678"}).status_code == 200,
    "[auth] 新パスワードでログイン可")

# ログアウト → 401
c.post("/api/auth/logout")
chk(c.get("/api/auth/me").status_code == 401, "[auth] ログアウトで401")
c.post("/api/auth/login", json={"email": "raguser@example.com", "password": "newpass5678"})

# ============ 固定プール方式の経路が消えたこと ============
chk(c.get("/api/levels").status_code == 404, "[gone] /api/levels が404")
chk(c.get("/api/units?level=beginner&user=u").status_code == 404, "[gone] /api/units が404")
chk(c.get("/api/quiz/start?level=beginner&unit=b_visa").status_code == 404, "[gone] /api/quiz/start が404")
chk(c.get("/api/quiz/graduation/start?level=beginner&user=u").status_code == 404, "[gone] /api/quiz/graduation/start が404")
chk(c.get(f"/api/{T}/admin/comparison").status_code == 404, "[gone] /admin/comparison が404")
chk(c.get(f"/api/{T}/admin/meta").status_code == 404, "[gone] /admin/meta が404")
chk(c.get(f"/api/{T}/admin/questions/export?level=all").status_code == 404, "[gone] CSV export が404")

# ============ 管理画面（刷新後） ============
# 旧・全件履歴エンドポイントは廃止
chk(c.get(f"/api/{T}/admin/attempts").status_code == 404, "[admin] 旧 attempts 一覧は廃止(404)")

# users: アカウント（表示名＋メール）＋単元別進捗＋クリア数。クリア数降順・同数は名前昇順
au = c.get(f"/api/{T}/admin/users").json()
chk("users" in au and isinstance(au["users"], list), "[admin] users: 一覧を返す")
rag_u = next((u for u in au["users"] if u["email"] == "raguser@example.com"), None)
chk(rag_u is not None and "cleared_count" in rag_u and "units" in rag_u
    and "user_id" in rag_u and rag_u["username"] == "ラグ太郎",
    "[admin] users: アカウント単位（user_id・表示名・メール）で一覧化")
chk(all("perfect_count" in x and "cleared" in x and "unit_name" in x for x in rag_u["units"]),
    "[admin] users: 単元別進捗（満点回数・クリア状況・単元名）")
# ソート: cleared_count 降順、同数は名前昇順
ccs = [u["cleared_count"] for u in au["users"]]
chk(ccs == sorted(ccs, reverse=True), "[admin] users: クリア数の降順で並ぶ")

# history: user_id指定。得点を返さず正答率(pct)のみ。日時・単元・レベルは残す
ah = c.get(f"/api/{T}/admin/history?user_id={rag_u['user_id']}").json()
chk(ah["attempts"] and all("pct" in x for x in ah["attempts"]), "[admin] history: 正答率(pct)を返す")
chk(all("score" not in x and "total" not in x for x in ah["attempts"]),
    "[admin] history: 得点(score/total)は返さない")
chk(all(("taken_at" in x and "level" in x) for x in ah["attempts"]),
    "[admin] history: 日時・レベルは残す")
# 管理者によるパスワード再設定 → 本人セッション失効・新パスワードでログイン可
pr = c.post(f"/api/{T}/admin/users/{rag_u['user_id']}/password",
            json={"new_password": "admin-reset-1"})
from fastapi.testclient import TestClient as _TC
c_re = _TC(app)
chk(pr.status_code == 200
    and c_re.post("/api/auth/login",
                  json={"email": "raguser@example.com", "password": "admin-reset-1"}).status_code == 200,
    "[admin] パスワード再設定: 新パスワードでログイン可")
# トークン不一致は404
chk(c.get("/api/wrongtoken/admin/users").status_code == 404, "[admin] 不正トークンは404")

print("\n" + ("=== 全通過 ===" if not ng else f"=== 失敗 {ng} ==="))
sys.exit(1 if ng else 0)
