// ===========================
// ビザ検定 - 管理画面ロジック（RAG出題）
// ===========================

(function () {
  // URLのファイル名からトークンを推定（admin-Kp7vQm2xRt.html → Kp7vQm2xRt）
  function detectAdminToken() {
    const path = location.pathname; // e.g. /admin-Kp7vQm2xRt.html
    const m = path.match(/admin-([a-zA-Z0-9_-]+)\.html$/);
    return m ? m[1] : "";
  }

  const ADMIN_TOKEN = detectAdminToken();

  const loadingEl = document.getElementById("loading");
  const contentEl = document.getElementById("content");
  const errorArea = document.getElementById("error-area");
  const errorMsg = document.getElementById("error-message");
  const statGrid = document.getElementById("stat-grid");
  const usersArea = document.getElementById("users-area");
  const attemptsArea = document.getElementById("attempts-area");

  // escapeHtml / pillClass / fmtDate / levelLabel は common.js に共通化

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
      const [users, attempts, cells] = await Promise.all([
        fetchJson(`/api/${ADMIN_TOKEN}/admin/users`),
        fetchJson(`/api/${ADMIN_TOKEN}/admin/attempts`),
        fetchJson(`/api/rag/cells`),
      ]);
      // 単元ID→表示名マップ（履歴表示用）
      const unitNameMap = {};
      (cells.cells || []).forEach((c) => { unitNameMap[c.unit_id] = c.unit_name; });
      render(users.users, attempts.attempts, unitNameMap);
    } catch (e) {
      showError(`データの取得に失敗しました: ${e.message}`);
    }
  }

  function render(users, attempts, unitNameMap) {
    // サマリー
    const totalAttempts = attempts.length;
    const uniqueUsers = users.length;
    const avgScore = attempts.length
      ? Math.round(attempts.reduce((s, a) => s + (a.score / a.total) * 100, 0) / attempts.length)
      : 0;
    const latest = attempts.length ? fmtDate(attempts[0].taken_at) : "—";

    statGrid.innerHTML = `
      <div class="stat"><div class="num">${totalAttempts}</div><div class="lbl">総受験回数</div></div>
      <div class="stat"><div class="num">${uniqueUsers}</div><div class="lbl">受験者数</div></div>
      <div class="stat"><div class="num">${avgScore}%</div><div class="lbl">平均正答率</div></div>
      <div class="stat"><div class="num" style="font-size:14px;line-height:1.8;">${latest}</div><div class="lbl">最新受験</div></div>
    `;

    // ユーザー別
    if (users.length === 0) {
      usersArea.innerHTML = '<div class="empty">受験データはまだありません</div>';
    } else {
      const rows = users.map(u => {
        const best = u.best_pct == null ? 0 : Math.round(u.best_pct);
        const avg = u.avg_pct == null ? 0 : Math.round(u.avg_pct);
        return `<tr>
          <td>${escapeHtml(u.username)}</td>
          <td>${u.attempts_count}</td>
          <td><span class="score-pill ${pillClass(best)}">${best}%</span></td>
          <td><span class="score-pill ${pillClass(avg)}">${avg}%</span></td>
          <td>${fmtDate(u.last_taken_at)}</td>
        </tr>`;
      }).join("");
      usersArea.innerHTML = `
        <table class="data">
          <thead><tr><th>受験者名</th><th>受験回数</th><th>最高</th><th>平均</th><th>最終受験</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    }

    // 受験履歴
    if (attempts.length === 0) {
      attemptsArea.innerHTML = '<div class="empty">受験データはまだありません</div>';
    } else {
      const rows = attempts.map(a => {
        const pct = Math.round((a.score / a.total) * 100);
        const kind = a.unit
          ? escapeHtml(unitNameMap[a.unit] || a.unit)
          : `<span class="hist-kind legacy">${levelLabel(a.level)}</span>`;
        return `<tr>
          <td>${fmtDate(a.taken_at)}</td>
          <td>${escapeHtml(a.username)}</td>
          <td>${kind}</td>
          <td>${a.score} / ${a.total}</td>
          <td><span class="score-pill ${pillClass(pct)}">${pct}%</span></td>
        </tr>`;
      }).join("");
      attemptsArea.innerHTML = `
        <table class="data">
          <thead><tr><th>受験日時</th><th>受験者名</th><th>単元</th><th>得点</th><th>正答率</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    }

    loadingEl.style.display = "none";
    contentEl.style.display = "block";
  }

  load();
})();
