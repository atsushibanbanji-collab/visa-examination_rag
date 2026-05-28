// ===========================
// ビザ検定 - 管理画面ロジック
// ===========================

(function () {
  // URLのファイル名からトークンを推定（admin-x7k2a9.html → x7k2a9）
  function detectAdminToken() {
    const path = location.pathname; // e.g. /admin-x7k2a9.html
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
  const poolInfo = document.getElementById("pool-info");
  const csvFileInput = document.getElementById("csv-file");
  const uploadBtn = document.getElementById("upload-btn");
  const uploadResult = document.getElementById("upload-result");

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
      const [users, attempts, meta, comparison] = await Promise.all([
        fetchJson(`/api/${ADMIN_TOKEN}/admin/users`),
        fetchJson(`/api/${ADMIN_TOKEN}/admin/attempts`),
        fetchJson(`/api/${ADMIN_TOKEN}/admin/meta`),
        fetchJson(`/api/${ADMIN_TOKEN}/admin/comparison`),
      ]);
      render(users.users, attempts.attempts, meta);
      renderComparison(comparison.comparison || {});
    } catch (e) {
      showError(`データの取得に失敗しました: ${e.message}`);
    }
  }

  function renderComparison(comp) {
    const area = document.getElementById("comparison-area");
    if (!area) return;
    const pool = comp.pool;
    const rag = comp.rag;
    if (!pool && !rag) {
      area.innerHTML = '<div class="empty">受験データはまだありません</div>';
      return;
    }
    function cell(v, suffix) {
      return v == null ? "—" : `${v}${suffix || ""}`;
    }
    area.innerHTML = `
      <table class="data">
        <thead><tr>
          <th>方式</th><th>受験回数</th><th>平均正答率</th>
          <th>平均生成時間</th><th>平均入力Tok</th><th>平均出力Tok</th>
        </tr></thead>
        <tbody>
          <tr>
            <td><span class="src-badge src-pool">POOL</span> 固定プール</td>
            <td>${cell(pool && pool.attempts_count)}</td>
            <td>${cell(pool && pool.avg_pct, "%")}</td>
            <td>—</td><td>—</td><td>—</td>
          </tr>
          <tr>
            <td><span class="src-badge src-rag">RAG</span> RAG生成</td>
            <td>${cell(rag && rag.attempts_count)}</td>
            <td>${cell(rag && rag.avg_pct, "%")}</td>
            <td>${cell(rag && rag.avg_latency_ms, " ms")}</td>
            <td>${cell(rag && rag.avg_input_tokens)}</td>
            <td>${cell(rag && rag.avg_output_tokens)}</td>
          </tr>
        </tbody>
      </table>
    `;
  }

  function render(users, attempts, meta) {
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

    // プールサイズ表示
    renderPoolInfo(meta);

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
      // meta.unit_pool_sizes から unit_id→name マップを構築（全レベル分）
      const unitNameMap = {};
      const upbl = (meta && meta.unit_pool_sizes) || {};
      for (const lv of Object.keys(upbl)) {
        (upbl[lv] || []).forEach(u => { unitNameMap[u.id] = u.name; });
      }

      const rows = attempts.map(a => {
        const pct = Math.round((a.score / a.total) * 100);
        let kind;
        if (a.is_graduation) {
          kind = `<span class="hist-kind grad">${levelLabel(a.level)} 卒業試験</span>`;
        } else if (a.unit) {
          kind = escapeHtml(unitNameMap[a.unit] || a.unit);
        } else {
          kind = `<span class="hist-kind legacy">${levelLabel(a.level)}</span>`;
        }
        const srcBadge = a.source === "rag"
          ? `<span class="src-badge src-rag">RAG</span>`
          : `<span class="src-badge src-pool">POOL</span>`;
        return `<tr>
          <td>${fmtDate(a.taken_at)}</td>
          <td>${escapeHtml(a.username)}</td>
          <td>${srcBadge}</td>
          <td>${kind}</td>
          <td>${a.score} / ${a.total}</td>
          <td><span class="score-pill ${pillClass(pct)}">${pct}%</span></td>
        </tr>`;
      }).join("");
      attemptsArea.innerHTML = `
        <table class="data">
          <thead><tr><th>受験日時</th><th>受験者名</th><th>方式</th><th>種別</th><th>得点</th><th>正答率</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    }

    loadingEl.style.display = "none";
    contentEl.style.display = "block";
  }

  function renderPoolInfo(meta) {
    if (!meta || !meta.pool_sizes) {
      poolInfo.textContent = "プールサイズ: —";
      return;
    }
    const p = meta.pool_sizes;
    const lines = [
      `現在のプール — 初級: ${p.beginner || 0} 件 / 中級: ${p.intermediate || 0} 件 / 上級: ${p.advanced || 0} 件`,
    ];

    // 単元別のブレイクダウン
    const upbl = meta.unit_pool_sizes || {};
    for (const level of ["beginner", "intermediate", "advanced"]) {
      const units = upbl[level];
      if (!units || units.length === 0) continue;
      const parts = units.map(u => {
        const target = u.target_pool ? ` / 目標 ${u.target_pool}` : "";
        return `${u.name}: ${u.pool_size}${target}`;
      });
      lines.push(`  ${levelLabel(level)} 単元別 — ${parts.join(" / ")}`);
    }

    // 各行を escape して <br> で連結
    poolInfo.innerHTML = lines.map(escapeHtml).join("<br>");
  }

  // ===========================
  // 問題管理（エクスポート/インポート）
  // ===========================

  // エクスポート: data-export 属性のついたボタンを一括ハンドル
  document.querySelectorAll("[data-export]").forEach(btn => {
    btn.addEventListener("click", () => {
      const level = btn.getAttribute("data-export");
      const url = `/api/${ADMIN_TOKEN}/admin/questions/export?level=${encodeURIComponent(level)}`;
      // 単純にリダイレクトすればブラウザがダウンロードしてくれる
      // （Content-Disposition: attachment が付いているため）
      window.location.href = url;
    });
  });

  // インポート
  csvFileInput.addEventListener("change", () => {
    uploadBtn.disabled = !csvFileInput.files || csvFileInput.files.length === 0;
    // 前回の結果は消す
    uploadResult.style.display = "none";
    uploadResult.innerHTML = "";
  });

  uploadBtn.addEventListener("click", async () => {
    const file = csvFileInput.files && csvFileInput.files[0];
    if (!file) return;

    uploadBtn.disabled = true;
    uploadBtn.textContent = "アップロード中…";
    uploadResult.style.display = "block";
    uploadResult.className = "upload-result";
    uploadResult.innerHTML = '<div class="muted">送信中…</div>';

    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`/api/${ADMIN_TOKEN}/admin/questions/import`, {
        method: "POST",
        body: form,
      });

      // 200 でも ok:false で返ることがある（バリデーションエラー）
      let data;
      try {
        data = await res.json();
      } catch (_) {
        throw new Error(`HTTP ${res.status} - レスポンスをJSONとして解釈できませんでした`);
      }

      if (!res.ok && !data) {
        throw new Error(`HTTP ${res.status} ${res.statusText}`);
      }

      if (data.ok) {
        renderImportSuccess(data);
        // プールサイズ表示と全体集計を更新するため、データ再取得
        try {
          const meta = await fetchJson(`/api/${ADMIN_TOKEN}/admin/meta`);
          renderPoolInfo(meta);
        } catch (_) { /* メタ更新失敗は致命的ではない */ }
      } else {
        renderImportErrors(data.errors || []);
      }
    } catch (e) {
      uploadResult.className = "upload-result error";
      uploadResult.innerHTML = `<strong>送信エラー:</strong> ${escapeHtml(e.message)}`;
    } finally {
      uploadBtn.disabled = false;
      uploadBtn.textContent = "アップロード";
    }
  });

  function renderImportSuccess(data) {
    uploadResult.className = "upload-result success";
    const appliedRows = Object.entries(data.applied || {})
      .map(([lv, n]) => `<li>${levelLabel(lv)}を <strong>${n}</strong> 件で置き換え</li>`)
      .join("");
    const untouchedRows = Object.entries(data.untouched || {})
      .map(([lv, n]) => `<li>${levelLabel(lv)}: ${n} 件（温存）</li>`)
      .join("");

    const warnings = data.warnings || [];
    let warningsBlock = "";
    if (warnings.length > 0) {
      const rows = warnings.slice(0, 50).map(w => {
        const where = w.row ? `${w.row} 行目` : "全体";
        return `<tr><td>${escapeHtml(where)}</td><td>${escapeHtml(w.message || "")}</td></tr>`;
      }).join("");
      const more = warnings.length > 50
        ? `<p class="muted">…他 ${warnings.length - 50} 件の警告は省略</p>`
        : "";
      warningsBlock = `
        <div class="result-section">
          <div class="result-label">警告（${warnings.length} 件・取り込みは成功）</div>
          <table class="data">
            <thead><tr><th style="width:8em;">場所</th><th>内容</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
          ${more}
        </div>
      `;
    }

    uploadResult.innerHTML = `
      <h4>取り込み完了</h4>
      <div class="result-section">
        <div class="result-label">適用</div>
        <ul>${appliedRows || "<li>（適用された行はありません）</li>"}</ul>
      </div>
      ${untouchedRows ? `
      <div class="result-section">
        <div class="result-label">温存</div>
        <ul>${untouchedRows}</ul>
      </div>` : ""}
      ${warningsBlock}
      <p class="muted">バックアップ: <code>${escapeHtml(data.backup || "")}</code></p>
    `;
  }

  function renderImportErrors(errors) {
    uploadResult.className = "upload-result error";
    const rows = errors.map(e => {
      const where = e.row ? `${e.row} 行目` : "全体";
      return `<tr><td>${escapeHtml(where)}</td><td>${escapeHtml(e.message || "")}</td></tr>`;
    }).join("");
    uploadResult.innerHTML = `
      <h4>取り込み失敗（${errors.length} 件のエラー）</h4>
      <p class="muted">questions.json は書き換えられていません。CSV を直して再度アップロードしてください。</p>
      <table class="data">
        <thead><tr><th style="width:8em;">場所</th><th>内容</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  }

  load();
})();
