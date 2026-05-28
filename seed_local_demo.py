"""ローカル確認用のサンプル受験データ投入スクリプト。

起動中のサーバ（既定 http://127.0.0.1:8000）に対して受験リクエストを送り、
進捗・履歴・卒業導線を目視確認できる状態を作る。

使い方:
    # 別ターミナルでサーバを起動しておく
    #   uvicorn backend.main:app --reload --port 8000
    python3 seed_local_demo.py
    # ポートを変える場合:
    python3 seed_local_demo.py --base http://127.0.0.1:8001
    # 投入するユーザ名を変える場合:
    python3 seed_local_demo.py --user 山田太郎

投入後、ブラウザで次を開くと改善点が見える:
    /units.html?user=<USER>&level=beginner
    /result.html?user=<USER>&level=beginner   ※直近の結果はsessionStorage依存のため
                                                 実際の遷移で見るのが確実

このスクリプトはサーバ越しのHTTPしか使わないので、本番DBには触れない（サーバが
向いているDBに書き込む）。ローカルの一時DBに向けて確認するため、サーバ起動時に
DATABASE_PATH=/tmp/visa-demo.db を指定することを推奨する。
"""
import argparse
import sys
import urllib.request
import urllib.parse
import json


def http_get(base, path):
    with urllib.request.urlopen(f"{base}{path}", timeout=10) as r:
        return json.loads(r.read().decode())


def http_post(base, path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def perfect_answers(base, level, unit):
    """指定単元を満点にする解答配列を作る（probeで正解を引いてから組む）。"""
    qs = http_get(base, f"/api/quiz/start?level={level}&unit={unit}")["questions"]
    probe = http_post(base, "/api/quiz/submit", {
        "username": "_seed_probe_", "level": level, "unit": unit,
        "answers": [{"id": q["id"], "choice": 0} for q in qs],
    })
    correct = {r["id"]: r["correct_choice"] for r in probe["results"]}
    return [{"id": q["id"], "choice": correct[q["id"]]} for q in qs]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--user", default="デモ太郎")
    ap.add_argument("--level", default="beginner")
    args = ap.parse_args()
    base, user, level = args.base.rstrip("/"), args.user, args.level
    user_q = urllib.parse.quote(user)

    try:
        units = http_get(base, f"/api/units?level={level}&user={user_q}")["units"]
    except Exception as e:
        print(f"サーバに接続できません（{base}）。先に uvicorn を起動してください。\n{e}")
        sys.exit(1)

    print(f"対象ユーザ: {user} / レベル: {level} / 単元数: {len(units)}")
    print("--- 投入シナリオ ---")
    print("  単元1: 連続3回満点 → クリア")
    print("  単元2: 1回だけ満点 → 進捗 1/3")
    print("  単元3: わざと1問外す → 進捗 0/3（未挑戦表示が消えるだけ）")
    print("  残り単元: 手つかず（未挑戦のまま）")
    print()

    if len(units) < 3:
        print("単元が3つ未満です。questions.json の中身を確認してください。")
        sys.exit(1)

    # 単元1: 連続3回満点 → クリア
    u1 = units[0]["id"]
    for i in range(3):
        ans = perfect_answers(base, level, u1)
        res = http_post(base, "/api/quiz/submit", {
            "username": user, "level": level, "unit": u1, "answers": ans,
        })
        print(f"  [{units[0]['name']}] {i+1}回目 満点 → streak {res['unit_progress']['streak_count']}")

    # 単元2: 1回だけ満点
    u2 = units[1]["id"]
    ans = perfect_answers(base, level, u2)
    res = http_post(base, "/api/quiz/submit", {
        "username": user, "level": level, "unit": u2, "answers": ans,
    })
    print(f"  [{units[1]['name']}] 1回 満点 → streak {res['unit_progress']['streak_count']}")

    # 単元3: わざと外す
    u3 = units[2]["id"]
    ans = perfect_answers(base, level, u3)
    if ans:
        ans[0]["choice"] = (ans[0]["choice"] + 1) % 3
    res = http_post(base, "/api/quiz/submit", {
        "username": user, "level": level, "unit": u3, "answers": ans,
    })
    print(f"  [{units[2]['name']}] 1問ミス → score {res['score']}/{res['total']}, streak {res['unit_progress']['streak_count']}")

    # 結果サマリー
    after = http_get(base, f"/api/units?level={level}&user={user_q}")
    g = after["graduation"]
    print()
    print(f"投入完了。クリア済 {g['cleared_units']}/{g['total_units']} 単元、卒業解放: {g['unlocked']}")
    print()
    print("ブラウザで以下を開いて確認:")
    print(f"  {base}/units.html?user={user}&level={level}")
    print("    → 進捗サマリーバー / 各単元カードの最終受験日 / 卒業ロックの残り単元名")
    print(f"  {base}/admin-<TOKEN>.html")
    print("    → 全履歴の『種別』列に単元名が出ているか")


if __name__ == "__main__":
    main()
