// ===========================
// ビザ検定 - 受験画面ロジック（RAG出題）
// ===========================

(function () {
  const params = new URLSearchParams(location.search);
  const username = (params.get("user") || "").trim();
  const level = params.get("level") || "beginner";
  const unit = params.get("unit") || ""; // "<unit_id>"
  // --- TEST MODE（撤去予定）: 起動判定。?test=1 のほか受験者名「テストモード」でも発動 ---
  const testMode = params.get("test") === "1" || username === "テストモード";
  // --- /TEST MODE ---

  const loadingEl = document.getElementById("loading");
  const quizArea = document.getElementById("quiz-area");
  const errorArea = document.getElementById("error-area");
  const errorMsg = document.getElementById("error-message");
  const errorBack = document.getElementById("error-back");
  const userLabel = document.getElementById("user-label");
  const stepLabel = document.getElementById("step-label");
  const categoryLabel = document.getElementById("category-label");
  const progressBar = document.getElementById("progress-bar");
  const questionText = document.getElementById("question-text");
  const choicesEl = document.getElementById("choices");
  const prevBtn = document.getElementById("prev-btn");
  const nextBtn = document.getElementById("next-btn");
  const submitBtn = document.getElementById("submit-btn");
  const modeLabelEl = document.getElementById("quiz-mode-label");
  const unitNameEl = document.getElementById("quiz-unit-name");
  const abortBtn = document.getElementById("abort-btn");
  const feedbackEl = document.getElementById("feedback");
  const feedbackMark = document.getElementById("feedback-mark");
  const feedbackLabel = document.getElementById("feedback-label");
  const feedbackExplanation = document.getElementById("feedback-explanation-text");

  if (!username) {
    showError("受験者名が指定されていません。トップから開始してください。");
    return;
  }
  if (!unit) {
    showError("単元が指定されていません。単元一覧から選んでください。");
    return;
  }

  const levelName = { beginner: "初級", intermediate: "中級", advanced: "上級" }[level] || level;
  userLabel.textContent = `受験者：${username}（${levelName}）`;

  const unitsParams = new URLSearchParams({ user: username, level });
  errorBack.href = `/units.html?${unitsParams.toString()}`;
  const unitsUrl = `/units.html?${unitsParams.toString()}`;

  let questions = [];
  let answers = []; // 各問の選択。未回答は -1
  let checked = []; // 各問の判定結果 / 未判定は null
  let currentIdx = 0;
  let unitMeta = null;
  let sessionId = null;    // RAG セッションID（採点・判定に必須）
  let genMetrics = null;   // RAG 生成メトリクス（結果画面で表示）

  // --- ヘッド／テイル分割 ---
  let totalExpected = 0;   // 最終的な総問数（開始時に確定）
  let tailLoaded = true;   // テイル（残り問題）取得済みか。pending無しなら最初から true
  let tailPromise = null;  // テイル取得の進行中プロミス（多重起動防止）
  let tailError = null;    // テイル取得失敗時のメッセージ

  function showError(msg) {
    loadingEl.style.display = "none";
    quizArea.style.display = "none";
    errorArea.style.display = "block";
    errorMsg.textContent = msg;
  }

  async function loadQuestions() {
    try {
      loadingEl.textContent = "AIが問題を生成中…（数秒かかります）";

      // ヘッダ用の単元名取得（失敗は致命的でない）
      try {
        const p = new URLSearchParams({ level, user: username });
        const ures = await fetch(`/api/rag/units?${p.toString()}`);
        if (ures.ok) {
          const udata = await ures.json();
          const found = (udata.units || []).find((x) => x.id === unit);
          if (found) unitMeta = found;
          renderModeLabel();
        }
      } catch (_) {}

      const res = await fetch("/api/rag/quiz/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, level, unit, test: testMode }),
      });
      if (!res.ok) {
        let detail = "";
        try { detail = (await res.json()).detail || ""; } catch (_) {}
        throw new Error(detail || `RAG出題の生成に失敗 (HTTP ${res.status})`);
      }
      const data = await res.json();
      sessionId = data.session_id;
      genMetrics = data.gen_metrics || null;

      questions = data.questions || [];   // ヘッド（先頭ぶん）
      if (questions.length === 0) {
        throw new Error("問題が0件でした。");
      }
      // 総問数はサーバが返す total_questions を正とする（未指定ならヘッド数）
      totalExpected = data.total_questions || questions.length;
      const pendingCount = data.pending_count || 0;
      tailLoaded = pendingCount === 0;

      // 回答・判定の配列は総問数ぶん確保し、インデックスを最後まで揃える
      answers = new Array(totalExpected).fill(-1);
      checked = new Array(totalExpected).fill(null);

      loadingEl.style.display = "none";
      quizArea.style.display = "block";
      render();

      // テイルはユーザーが解いている間に裏で先読みしておく（fire-and-forget）
      if (!tailLoaded) ensureTail();
    } catch (e) {
      showError(e.message || "問題の取得に失敗しました");
    }
  }

  // テイル（残り問題）を取得してセッションへ追記。多重起動はプロミスで抑止する。
  function ensureTail() {
    if (tailLoaded) return Promise.resolve(true);
    if (!tailPromise) tailPromise = loadTail();
    return tailPromise;
  }

  async function loadTail() {
    try {
      const res = await fetch("/api/rag/quiz/continue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      });
      if (!res.ok) {
        let detail = "";
        try { detail = (await res.json()).detail || ""; } catch (_) {}
        throw new Error(detail || `残りの問題の生成に失敗 (HTTP ${res.status})`);
      }
      const data = await res.json();
      const tail = data.questions || [];
      questions = questions.concat(tail);
      if (data.gen_metrics) genMetrics = data.gen_metrics;
      tailLoaded = true;
      tailError = null;
      render();
      return true;
    } catch (e) {
      // 失敗は黙って短い検定にせず、再試行できるようにする（無音の劣化を避ける）
      tailError = e.message || "残りの問題の生成に失敗しました。";
      tailPromise = null;   // 次回 ensureTail で再試行できるようリセット
      render();
      return false;
    }
  }

  function renderModeLabel() {
    modeLabelEl.textContent = testMode ? "TEST" : "UNIT";
    unitNameEl.textContent = unitMeta ? (unitMeta.name || "") : "";
  }

  function render() {
    const q = questions[currentIdx];
    if (!q) return;  // 未ロードのインデックスは描画しない（通常は到達しない）
    const result = checked[currentIdx];
    const isChecked = result !== null;

    stepLabel.textContent = `${currentIdx + 1} / ${totalExpected}`;
    categoryLabel.textContent = q.category ? q.category : "";
    progressBar.style.width = `${((currentIdx + 1) / totalExpected) * 100}%`;
    questionText.textContent = q.question;

    choicesEl.innerHTML = "";
    if (q.type === "fill_in") {
      renderFillIn(q, isChecked);
    } else {
      renderChoices(q, result, isChecked);
    }

    if (isChecked) {
      feedbackEl.style.display = "block";
      feedbackEl.className = "feedback " + (result.is_correct ? "is-correct" : "is-wrong");
      feedbackMark.textContent = result.is_correct ? "〇" : "×";
      feedbackLabel.textContent = result.is_correct ? "正解" : "不正解";
      let exp = result.explanation || "（解説はありません）";
      // 穴埋めは選択肢で正解を示せないため、解説の先頭に正解例を添える
      if (q.type === "fill_in" && Array.isArray(result.correct_answers) && result.correct_answers.length) {
        exp = `正解例: ${result.correct_answers.join(" / ")}\n` + exp;
      }
      feedbackExplanation.textContent = exp;
    } else {
      feedbackEl.style.display = "none";
    }

    prevBtn.disabled = currentIdx === 0;
    const isLast = currentIdx === totalExpected - 1;

    if (isLast) {
      nextBtn.style.display = "none";
      submitBtn.style.display = "inline-block";
      // 全問判定済み かつ テイルも揃っている場合のみ採点可能
      submitBtn.disabled = !tailLoaded || checked.some((r) => r === null);
    } else {
      nextBtn.style.display = "inline-block";
      submitBtn.style.display = "none";
      nextBtn.disabled = !isChecked;
    }
  }

  // 選択式（初級Yes/No・中級）の選択肢を描画する
  function renderChoices(q, result, isChecked) {
    choicesEl.className = "choices" + (isChecked ? " locked" : "");
    q.choices.forEach((c, i) => {
      const div = document.createElement("div");
      let cls = "choice";
      if (isChecked) {
        if (i === result.correct_choice) cls += " correct";
        else if (i === answers[currentIdx]) cls += " wrong";
      } else if (answers[currentIdx] === i) {
        cls += " selected";
      }
      div.className = cls;
      const marker = String.fromCharCode(0x2160 + i); // Ⅰ Ⅱ Ⅲ Ⅳ
      div.innerHTML = `
        <div class="marker">${marker}</div>
        <div class="text">${escapeHtml(c)}</div>
      `;
      if (!isChecked) {
        div.addEventListener("click", () => selectAndCheck(i));
      }
      choicesEl.appendChild(div);
    });
  }

  // 穴埋め（上級）の入力欄＋「回答する」ボタンを描画する
  function renderFillIn(q, isChecked) {
    choicesEl.className = "fill-in" + (isChecked ? " locked" : "");
    const n = q.blank_count || 1;
    const saved = Array.isArray(answers[currentIdx]) ? answers[currentIdx] : [];
    const inputs = [];
    for (let i = 0; i < n; i++) {
      const wrap = document.createElement("div");
      wrap.className = "fill-in-blank";
      const label = document.createElement("label");
      label.textContent = n > 1 ? `空欄 ${i + 1}` : "解答";
      const input = document.createElement("input");
      input.type = "text";
      input.autocomplete = "off";
      input.value = saved[i] || "";
      input.disabled = isChecked;
      wrap.appendChild(label);
      wrap.appendChild(input);
      choicesEl.appendChild(wrap);
      inputs.push(input);
    }
    if (isChecked) return;

    const btn = document.createElement("button");
    btn.className = "btn fill-in-submit";
    btn.type = "button";
    btn.textContent = "回答する";
    const refresh = () => { btn.disabled = inputs.some((el) => !el.value.trim()); };
    inputs.forEach((el) => {
      el.addEventListener("input", refresh);
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !btn.disabled) btn.click();
      });
    });
    refresh();
    btn.addEventListener("click", () => checkFillIn(inputs.map((el) => el.value.trim())));
    choicesEl.appendChild(btn);
  }

  // 穴埋め回答 → サーバーに1問だけ判定を問い合わせる
  async function checkFillIn(texts) {
    if (checked[currentIdx] !== null) return;
    answers[currentIdx] = texts;
    try {
      const res = await fetch("/api/quiz/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: questions[currentIdx].id, text_answers: texts, session_id: sessionId }),
      });
      if (!res.ok) {
        let detail = "";
        try { detail = (await res.json()).detail || ""; } catch (_) {}
        throw new Error(detail || `判定に失敗しました (HTTP ${res.status})`);
      }
      checked[currentIdx] = await res.json();
      render();
    } catch (e) {
      answers[currentIdx] = -1;
      checked[currentIdx] = null;
      render();
      alert(e.message || "判定エラー。もう一度入力してください。");
    }
  }

  // 選択肢タップ → サーバーに1問だけ判定を問い合わせ、結果を表示
  async function selectAndCheck(choiceIdx) {
    if (checked[currentIdx] !== null) return;
    answers[currentIdx] = choiceIdx;
    render();
    choicesEl.className = "choices locked";
    try {
      const res = await fetch("/api/quiz/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: questions[currentIdx].id, choice: choiceIdx, session_id: sessionId }),
      });
      if (!res.ok) {
        let detail = "";
        try { detail = (await res.json()).detail || ""; } catch (_) {}
        throw new Error(detail || `判定に失敗しました (HTTP ${res.status})`);
      }
      checked[currentIdx] = await res.json();
      render();
    } catch (e) {
      answers[currentIdx] = -1;
      checked[currentIdx] = null;
      render();
      alert(e.message || "判定エラー。もう一度選んでください。");
    }
  }

  // escapeHtml は common.js に共通化

  abortBtn.addEventListener("click", () => {
    const ok = confirm(
      "検定を中止して単元一覧に戻りますか？\nここまでの回答は記録されません。"
    );
    if (ok) location.href = unitsUrl;
  });

  prevBtn.addEventListener("click", () => {
    if (currentIdx > 0) { currentIdx--; render(); }
  });

  nextBtn.addEventListener("click", async () => {
    const target = currentIdx + 1;
    if (target >= totalExpected) return;

    // 次問がまだ生成できていない（テイル未着）なら、ここで待つ
    if (target >= questions.length) {
      const prevLabel = nextBtn.textContent;
      nextBtn.disabled = true;
      nextBtn.textContent = "問題を準備中…";
      const ok = await ensureTail();
      nextBtn.textContent = prevLabel;
      if (!ok || target >= questions.length) {
        nextBtn.disabled = false;
        alert(tailError || "残りの問題をまだ準備できていません。少し待って再度お試しください。");
        return;
      }
    }
    currentIdx = target;
    render();
  });

  submitBtn.addEventListener("click", async () => {
    submitBtn.disabled = true;
    submitBtn.textContent = "採点中…";
    try {
      const payload = {
        username,
        level,
        unit,
        session_id: sessionId,
        answers: questions.map((q, i) =>
          q.type === "fill_in"
            ? { id: q.id, text_answers: Array.isArray(answers[i]) ? answers[i] : [] }
            : { id: q.id, choice: answers[i] }
        ),
      };
      const res = await fetch("/api/quiz/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        let detail = "";
        try { detail = (await res.json()).detail || ""; } catch (_) {}
        throw new Error(detail || `採点リクエストが失敗しました (HTTP ${res.status})`);
      }
      const result = await res.json();
      if (genMetrics) result.gen_metrics = genMetrics;
      sessionStorage.setItem("visa_quiz_last_result", JSON.stringify(result));
      const p = new URLSearchParams({ user: username, level });
      location.href = `/result.html?${p.toString()}`;
    } catch (e) {
      submitBtn.disabled = false;
      submitBtn.textContent = "採点する";
      alert(e.message || "送信エラー");
    }
  });

  loadQuestions();
})();
