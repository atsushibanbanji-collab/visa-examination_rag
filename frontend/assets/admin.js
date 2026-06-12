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
        : `<span class="hist-kind legacy">${levelLabel(a.level)}</span>`;
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

  load();
})();
