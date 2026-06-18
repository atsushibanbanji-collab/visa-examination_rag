// ===========================
// ビザ検定 - 管理画面ロジック（RAG出題）
// 受験者一覧（名前＋単元別進捗・クリア数降順）と、名前クリックでの個別履歴（正答率のみ）。
// ===========================

(function () {
  // URLのファイル名からトークンを推定（admin-Kp7vQm2xRt.html → Kp7vQm2xRt）
  function detectAdminToken() {
    const m = location.pathname.match(/admin-([a-zA-Z0-9_-]+)\.html$/);
    return m ? m[1] : "";
  }

  const ADMIN_TOKEN = detectAdminToken();

  const loadingEl = document.getElementById("loading");
  const contentEl = document.getElementById("content");
  const errorArea = document.getElementById("error-area");
  const errorMsg = document.getElementById("error-message");
  const usersArea = document.getElementById("users-area");
  const historyCard = document.getElementById("history-card");
  const historyTitle = document.getElementById("history-title");
  const historyArea = document.getElementById("history-area");
  const challengesArea = document.getElementById("challenges-area");
  const challengeStatusFilter = document.getElementById("challenge-status-filter");

  // escapeHtml / fmtDate / levelLabel は common.js に共通化

  // 正答率(%) → 色クラス（管理画面の閾値: 満点=緑 / 61〜99=黄 / 60以下=赤）
  function rateClass(pct) {
    if (pct >= 100) return "high"; // 緑
    if (pct >= 61) return "mid";   // 黄
    return "low";                  // 赤
  }

  function showError(msg) {
    loadingEl.style.display = "none";
    contentEl.style.display = "none";
    errorArea.style.display = "block";
    errorMsg.textContent = msg;
  }

  async function fetchJson(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  }

  async function load() {
    if (!ADMIN_TOKEN) {
      showError("管理トークンを検出できませんでした。URLパスを確認してください。");
      return;
    }
    try {
      const data = await fetchJson(`/api/${ADMIN_TOKEN}/admin/users`);
      renderUsers(data.users || []);
      loadingEl.style.display = "none";
      contentEl.style.display = "block";
      loadChallenges();
    } catch (e) {
      showError(`データの取得に失敗しました: ${e.message}`);
    }
  }

  function progressChip(u) {
    // 単元別進捗チップ。クリア済みは緑のチップ色で示すため文言は付けない（クライアント要望）。
    // 未クリアは満点回数を N/3 表記のみで示す（「通算」の語は付けない）。
    if (u.cleared) {
      return `<span class="prog-chip prog-chip--cleared">${escapeHtml(u.unit_name)}（${levelLabel(u.level)}）</span>`;
    }
    return `<span class="prog-chip">${escapeHtml(u.unit_name)}（${levelLabel(u.level)}）${u.perfect_count}/${u.required}</span>`;
  }

  function renderUsers(users) {
    if (users.length === 0) {
      usersArea.innerHTML = '<div class="empty">受験データはまだありません</div>';
      return;
    }
    const rows = users.map((u) => {
      const chips = (u.units || []).map(progressChip).join(" ");
      return `<tr>
        <td><button type="button" class="user-link" data-user-id="${u.user_id}" data-user="${escapeHtml(u.username)}">${escapeHtml(u.username)}</button>
            <div class="muted" style="font-size: 11px;">${escapeHtml(u.email)}</div></td>
        <td class="cleared-num">${u.cleared_count}</td>
        <td class="last-taken">${u.last_taken_at ? fmtDate(u.last_taken_at) : '<span class="muted">−</span>'}</td>
        <td class="prog-cell">${chips || '<span class="muted">進捗なし</span>'}</td>
        <td><button type="button" class="btn btn-secondary pw-reset" data-user-id="${u.user_id}" data-user="${escapeHtml(u.username)}"
              style="padding: 4px 8px; font-size: 12px; white-space: nowrap;">PW再設定</button></td>
      </tr>`;
    }).join("");
    usersArea.innerHTML = `
      <table class="data">
        <thead><tr><th>受験者</th><th>クリア単元数</th><th>直近の受験</th><th>単元別進捗</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    `;
    usersArea.querySelectorAll(".user-link").forEach((btn) => {
      btn.addEventListener("click", () => loadHistory(btn.dataset.userId, btn.dataset.user));
    });
    // パスワード再設定（メール送信基盤なし＝管理者が新パスワードを決めて本人へ伝える運用）
    usersArea.querySelectorAll(".pw-reset").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const newPw = prompt(`${btn.dataset.user} さんの新しいパスワード（8文字以上）を入力してください：`);
        if (newPw === null) return;
        if (newPw.length < 8) { alert("8文字以上にしてください。"); return; }
        try {
          const res = await fetch(`/api/${ADMIN_TOKEN}/admin/users/${btn.dataset.userId}/password`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ new_password: newPw }),
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.detail || "再設定に失敗しました");
          alert(`再設定しました。新しいパスワードを ${btn.dataset.user} さんへ伝えてください。\n（本人の既存ログインは全て無効になります）`);
        } catch (e) {
          alert("失敗: " + e.message);
        }
      });
    });
  }

  async function loadHistory(userId, displayName) {
    historyCard.style.display = "block";
    historyTitle.textContent = `受験履歴：${displayName}`;
    historyArea.innerHTML = '<div class="loading">読み込み中…</div>';
    historyCard.scrollIntoView({ behavior: "smooth", block: "start" });
    try {
      const data = await fetchJson(
        `/api/${ADMIN_TOKEN}/admin/history?user_id=${encodeURIComponent(userId)}`
      );
      renderHistory(data.attempts || [], data.required || 3);
    } catch (e) {
      historyArea.innerHTML = `<div class="empty">履歴の取得に失敗しました: ${escapeHtml(e.message)}</div>`;
    }
  }

  function renderHistory(attempts, requiredCount) {
    if (attempts.length === 0) {
      historyArea.innerHTML = '<div class="empty">この受験者の履歴はありません</div>';
      return;
    }
    // 正答率の数値は表示せず、記録1行全体を正答率バンドで色付けする
    // （満点=緑 / 61〜99%=黄 / 60%以下=赤）。
    // 満点の行はレベルの右に（N/3）を付け、何回目の満点かを示す（クライアント要望）。
    const rows = attempts.map((a) => {
      const kind = a.unit_name
        ? escapeHtml(a.unit_name)
        : escapeHtml(levelLabel(a.level));
      const perfectNo = a.perfect_no
        ? `（${a.perfect_no}/${requiredCount}）`
        : "";
      return `<tr class="hist-row hist-row--${rateClass(a.pct)}">
        <td>${fmtDate(a.taken_at)}</td>
        <td>${kind}</td>
        <td>${levelLabel(a.level)}${perfectNo}</td>
      </tr>`;
    }).join("");
    historyArea.innerHTML = `
      <table class="data hist-table">
        <thead><tr><th>受験日時</th><th>単元</th><th>レベル</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="muted hist-legend">行の色＝正答率（<span class="lg lg-high">緑：満点</span>／<span class="lg lg-mid">黄：61〜99%</span>／<span class="lg lg-low">赤：60%以下</span>）</p>
    `;
  }

  // ===== 異議申し立て（チャレンジ） =====
  async function loadChallenges() {
    const status = challengeStatusFilter ? challengeStatusFilter.value : "";
    challengesArea.innerHTML = '<div class="loading">読み込み中…</div>';
    try {
      const qs = status ? `?status=${encodeURIComponent(status)}` : "";
      const data = await fetchJson(`/api/${ADMIN_TOKEN}/admin/challenges${qs}`);
      renderChallenges(data.challenges || []);
    } catch (e) {
      challengesArea.innerHTML = `<div class="empty">取得に失敗しました: ${escapeHtml(e.message)}</div>`;
    }
  }

  // スナップショット（設問・選択肢・正答・受験者の解答・解説）を整形する
  function renderSnapshot(s) {
    if (!s || !s.question) return "";
    let body = `<div class="ch-question">${escapeHtml(s.question)}</div>`;
    if (s.type === "fill_in") {
      const correct = Array.isArray(s.correct_answers) ? s.correct_answers.join(" / ") : "";
      const ua = Array.isArray(s.user_text_answers) ? s.user_text_answers.join(" / ") : "";
      body += `<div class="ch-meta">正解例：${escapeHtml(correct)}</div>`;
      body += `<div class="ch-meta">受験者の解答：${escapeHtml(ua) || "（未記入）"}（${s.is_correct ? "正解" : "不正解"}）</div>`;
    } else {
      const choices = Array.isArray(s.choices) ? s.choices : [];
      const opts = choices.map((c, i) => {
        const marks = [];
        if (i === s.correct_choice) marks.push("正答");
        if (i === s.user_choice) marks.push("受験者");
        const tag = marks.length ? `（${marks.join("・")}）` : "";
        return `<li class="${i === s.correct_choice ? "ch-correct" : ""}">${escapeHtml(c)}${tag}</li>`;
      }).join("");
      body += `<ul class="ch-choices">${opts}</ul>`;
      body += `<div class="ch-meta">判定：${s.is_correct ? "正解" : "不正解"}</div>`;
    }
    if (s.explanation) body += `<div class="ch-meta">解説：${escapeHtml(s.explanation)}</div>`;
    return body;
  }

  function renderChallenges(items) {
    if (items.length === 0) {
      challengesArea.innerHTML = '<div class="empty">該当する異議申し立てはありません</div>';
      return;
    }
    const cards = items.map((ch) => {
      const kind = CHALLENGE_KIND_LABEL[ch.kind] || "−";
      const statusLabel = ch.status_label || CHALLENGE_STATUS_LABEL[ch.status] || ch.status;
      // 操作ボタン: 未処理→認容/却下、未修正→クローズ、終端→なし
      let actions = "";
      if (ch.status === "open") {
        actions = `
          <button type="button" class="btn ch-accept" data-id="${ch.id}">認容（正解扱いに訂正）</button>
          <button type="button" class="btn btn-secondary ch-reject" data-id="${ch.id}">却下</button>`;
      } else if (ch.status === "accepted") {
        actions = `<button type="button" class="btn btn-secondary ch-close" data-id="${ch.id}">クローズ（是正完了）</button>`;
      }
      const adminMsg = ch.admin_message
        ? `<div class="ch-meta">受験者へのメッセージ：${escapeHtml(ch.admin_message)}</div>` : "";
      const adminNote = ch.admin_note
        ? `<div class="ch-meta">対応メモ：${escapeHtml(ch.admin_note)}</div>` : "";
      const noAttempt = ch.attempt_id ? "" :
        '<div class="ch-meta" style="color:var(--danger);">※受験未確定（中断）。認容しても採点反映はありません。</div>';
      return `<div class="ch-card ch-card--${ch.status}">
        <div class="ch-head">
          <span class="ch-status ch-status--${ch.status}">${escapeHtml(statusLabel)}</span>
          <span class="muted">${escapeHtml(ch.username)} ／ ${escapeHtml(ch.unit_name)}（${levelLabel(ch.level)}）／ ${fmtDate(ch.created_at)}</span>
        </div>
        <div class="ch-reason"><strong>申し立て（${escapeHtml(kind)}）：</strong>${escapeHtml(ch.reason || "")}</div>
        ${renderSnapshot(ch.snapshot)}
        ${noAttempt}${adminMsg}${adminNote}
        <div class="ch-actions">${actions}</div>
      </div>`;
    }).join("");
    challengesArea.innerHTML = cards;

    challengesArea.querySelectorAll(".ch-accept").forEach((b) =>
      b.addEventListener("click", () => resolveChallenge(b.dataset.id, "accept")));
    challengesArea.querySelectorAll(".ch-reject").forEach((b) =>
      b.addEventListener("click", () => resolveChallenge(b.dataset.id, "reject")));
    challengesArea.querySelectorAll(".ch-close").forEach((b) =>
      b.addEventListener("click", () => closeChallenge(b.dataset.id)));
  }

  async function resolveChallenge(id, action) {
    const verb = action === "accept" ? "認容" : "却下";
    if (!confirm(`このチャレンジを${verb}します。よろしいですか？` +
        (action === "accept" ? "\n（当該設問が正解扱いに訂正され、満点化すれば通算満点に加算されます）" : ""))) return;
    const adminMessage = prompt("受験者へのメッセージ（任意・本人に表示されます）", "") || null;
    const adminNote = prompt("対応メモ（任意・内部用）", "") || null;
    try {
      const res = await fetch(`/api/${ADMIN_TOKEN}/admin/challenges/${id}/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ admin_message: adminMessage, admin_note: adminNote }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "処理に失敗しました");
      if (action === "accept" && data.scoring && data.scoring.became_perfect) {
        alert("認容しました。採点を訂正し、満点として通算満点に加算しました。");
      } else {
        alert(`${verb}しました。`);
      }
      loadChallenges();
    } catch (e) {
      alert("失敗: " + e.message);
    }
  }

  async function closeChallenge(id) {
    if (!confirm("このチャレンジをクローズします。\n観点・プロンプトの是正をGitに反映済み、または是正不要と判断した場合に実施してください。")) return;
    const adminNote = prompt("対応メモ（是正内容、または「是正不要：理由」など）", "") || null;
    try {
      const res = await fetch(`/api/${ADMIN_TOKEN}/admin/challenges/${id}/close`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ admin_note: adminNote }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "処理に失敗しました");
      alert("クローズしました。");
      loadChallenges();
    } catch (e) {
      alert("失敗: " + e.message);
    }
  }

  if (challengeStatusFilter) {
    challengeStatusFilter.addEventListener("change", loadChallenges);
  }

  load();
})();
