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
    // 単元別進捗チップ: クリア済み or 通算満点 N/3
    if (u.cleared) {
      return `<span class="prog-chip prog-chip--cleared">${escapeHtml(u.unit_name)}（${levelLabel(u.level)}）クリア済み</span>`;
    }
    return `<span class="prog-chip">${escapeHtml(u.unit_name)}（${levelLabel(u.level)}）通算 ${u.perfect_count}/${u.required}</span>`;
  }

  function renderUsers(users) {
    if (users.length === 0) {
      usersArea.innerHTML = '<div class="empty">受験データはまだありません</div>';
      return;
    }
    const rows = users.map((u) => {
      const chips = (u.units || []).map(progressChip).join(" ");
      return `<tr>
        <td><button type="button" class="user-link" data-user="${escapeHtml(u.username)}">${escapeHtml(u.username)}</button></td>
        <td class="cleared-num">${u.cleared_count}</td>
        <td class="prog-cell">${chips || '<span class="muted">進捗なし</span>'}</td>
      </tr>`;
    }).join("");
    usersArea.innerHTML = `
      <table class="data">
        <thead><tr><th>受験者名</th><th>クリア単元数</th><th>単元別進捗</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    `;
    usersArea.querySelectorAll(".user-link").forEach((btn) => {
      btn.addEventListener("click", () => loadHistory(btn.dataset.user));
    });
  }

  async function loadHistory(username) {
    historyCard.style.display = "block";
    historyTitle.textContent = `受験履歴：${username}`;
    historyArea.innerHTML = '<div class="loading">読み込み中…</div>';
    historyCard.scrollIntoView({ behavior: "smooth", block: "start" });
    try {
      const data = await fetchJson(
        `/api/${ADMIN_TOKEN}/admin/history?username=${encodeURIComponent(username)}`
      );
      renderHistory(data.attempts || []);
    } catch (e) {
      historyArea.innerHTML = `<div class="empty">履歴の取得に失敗しました: ${escapeHtml(e.message)}</div>`;
    }
  }

  function renderHistory(attempts) {
    if (attempts.length === 0) {
      historyArea.innerHTML = '<div class="empty">この受験者の履歴はありません</div>';
      return;
    }
    // 正答率の数値は表示せず、記録1行全体を正答率バンドで色付けする
    // （満点=緑 / 61〜99%=黄 / 60%以下=赤）。
    const rows = attempts.map((a) => {
      const kind = a.unit_name
        ? escapeHtml(a.unit_name)
        : `<span class="hist-kind legacy">${levelLabel(a.level)}</span>`;
      return `<tr class="hist-row hist-row--${rateClass(a.pct)}">
        <td>${fmtDate(a.taken_at)}</td>
        <td>${kind}</td>
        <td>${levelLabel(a.level)}</td>
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
