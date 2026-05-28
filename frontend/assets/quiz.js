// ===========================
// ビザ検定 - 受験画面ロジック（固定プール / RAG 両対応）
// ===========================

(function () {
  const params = new URLSearchParams(location.search);
  const username = (params.get("user") || "").trim();
  const level = params.get("level") || "beginner";
  const unit = params.get("unit") || ""; // "" / "<unit_id>" / "__graduation__"
  const mode = params.get("mode") === "rag" ? "rag" : "pool";
  const isRag = mode === "rag";
  const isGraduation = unit === "__graduation__";

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

  const levelName = { beginner: "初級", intermediate: "中級", advanced: "上級" }[level] || level;
  const modeName = isRag ? "RAG方式" : "固定プール方式";
  userLabel.textContent = `受験者：${username}（${levelName}・${modeName}）`;

  // 単元一覧へ戻れるようにエラー画面のリンクを差し替え（mode を保持）
  const unitsParams = new URLSearchParams({ user: username, level, mode });
  errorBack.href = `/units.html?${unitsParams.toString()}`;
  const unitsUrl = `/units.html?${unitsParams.toString()}`;

  let questions = [];
  let answers = []; // 各問の選択。未回答は -1
  let checked = []; // 各問の判定結果 {correct_choice, is_correct, explanation} / 未判定は null
  let currentIdx = 0;
  let unitMeta = null;     // 当該単元の情報（戻り先表示用）
  let sessionId = null;    // RAG セッションID（採点・判定に必須）
  let genMetrics = null;   // RAG 生成メトリクス（結果画面で表示）

  function showError(msg) {
    loadingEl.style.display = "none";
    quizArea.style.display = "none";
    errorArea.style.display = "block";
    errorMsg.textContent = msg;
  }

  async function loadQuestions() {
    try {
      if (isRag) {
        loadingEl.textContent = "AIが問題を生成中…（数秒かかります）";
      }

      // ヘッダ用の単元名取得（方式に応じたエンドポイント）
      if (unit && !isGraduation) {
        try {
          const p = new URLSearchParams({ level, user: username });
          const endpoint = isRag ? "/api/rag/units" : "/api/units";
          const ures = await fetch(`${endpoint}?${p.toString()}`);
          if (ures.ok) {
            const udata = await ures.json();
            const found = (udata.units || []).find((x) => x.id === unit);
            if (found) unitMeta = found;
            renderModeLabel();
          }
        } catch (_) {}
      } else if (isGraduation) {
        unitMeta = { name: `${levelName} 卒業試験`, isGraduation: true };
        renderModeLabel();
      }

      let data;
      if (isRag) {
        // RAG: 観点サンプリング → LLM生成。セッションが返る。
        const res = await fetch("/api/rag/quiz/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, level, unit }),
        });
        if (!res.ok) {
          let detail = "";
          try { detail = (await res.json()).detail || ""; } catch (_) {}
          throw new Error(detail || `RAG出題の生成に失敗 (HTTP ${res.status})`);
        }
        data = await res.json();
        sessionId = data.session_id;
        genMetrics = data.gen_metrics || null;
      } else {
        // 固定プール: GET 出題
        let url;
        if (isGraduation) {
          const p = new URLSearchParams({ level, user: username });
          url = `/api/quiz/graduation/start?${p.toString()}`;
        } else if (unit) {
          const p = new URLSearchParams({ level, unit });
          url = `/api/quiz/start?${p.toString()}`;
        } else {
          url = `/api/quiz/start?level=${encodeURIComponent(level)}`;
        }
        const res = await fetch(url);
        if (!res.ok) {
          let detail = "";
          try { detail = (await res.json()).detail || ""; } catch (_) {}
          if (res.status === 403) throw new Error(detail || "この試験はロックされています。");
          throw new Error(detail || `出題エンドポイントから応答がない (HTTP ${res.status})`);
        }
        data = await res.json();
      }

      questions = data.questions || [];
      if (questions.length === 0) {
        throw new Error("問題が0件でした。");
      }
      answers = new Array(questions.length).fill(-1);
      checked = new Array(questions.length).fill(null);
      loadingEl.style.display = "none";
      quizArea.style.display = "block";
      render();
    } catch (e) {
      showError(e.message || "問題の取得に失敗しました");
    }
  }

  function renderModeLabel() {
    if (isGraduation) {
      modeLabelEl.textContent = "FINAL EXAM";
      modeLabelEl.classList.add("graduation");
      unitNameEl.textContent = `${levelName} 卒業試験`;
    } else {
      modeLabelEl.textContent = isRag ? "RAG" : "UNIT";
      modeLabelEl.classList.remove("graduation");
      modeLabelEl.classList.toggle("rag", isRag);
      unitNameEl.textContent = unitMeta ? (unitMeta.name || "") : "";
    }
  }

  function render() {
    const q = questions[currentIdx];
    const result = checked[currentIdx];
    const isChecked = result !== null;

    stepLabel.textContent = `${currentIdx + 1} / ${questions.length}`;
    categoryLabel.textContent = q.category ? q.category : "";
    progressBar.style.width = `${((currentIdx + 1) / questions.length) * 100}%`;
    questionText.textContent = q.question;

    choicesEl.innerHTML = "";
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

    if (isChecked) {
      feedbackEl.style.display = "block";
      feedbackEl.className = "feedback " + (result.is_correct ? "is-correct" : "is-wrong");
      feedbackMark.textContent = result.is_correct ? "〇" : "×";
      feedbackLabel.textContent = result.is_correct ? "正解" : "不正解";
      feedbackExplanation.textContent = result.explanation || "（解説はありません）";
    } else {
      feedbackEl.style.display = "none";
    }

    prevBtn.disabled = currentIdx === 0;
    const isLast = currentIdx === questions.length - 1;

    if (isLast) {
      nextBtn.style.display = "none";
      submitBtn.style.display = "inline-block";
      submitBtn.disabled = checked.some((r) => r === null);
    } else {
      nextBtn.style.display = "inline-block";
      submitBtn.style.display = "none";
      nextBtn.disabled = !isChecked;
    }
  }

  // 選択肢タップ → サーバーに1問だけ判定を問い合わせ、結果を表示
  async function selectAndCheck(choiceIdx) {
    if (checked[currentIdx] !== null) return;
    answers[currentIdx] = choiceIdx;
    render();
    choicesEl.className = "choices locked";
    try {
      const body = { id: questions[currentIdx].id, choice: choiceIdx };
      if (isRag) body.session_id = sessionId;
      const res = await fetch("/api/quiz/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
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

  nextBtn.addEventListener("click", () => {
    if (currentIdx < questions.length - 1) { currentIdx++; render(); }
  });

  submitBtn.addEventListener("click", async () => {
    submitBtn.disabled = true;
    submitBtn.textContent = "採点中…";
    try {
      const payload = {
        username,
        level,
        unit: unit || null,
        mode,
        answers: questions.map((q, i) => ({ id: q.id, choice: answers[i] })),
      };
      if (isRag) payload.session_id = sessionId;
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
      // 結果画面用に生成メトリクスも添えて保存
      if (genMetrics) result.gen_metrics = genMetrics;
      sessionStorage.setItem("visa_quiz_last_result", JSON.stringify(result));
      const p = new URLSearchParams({ user: username, level, mode });
      location.href = `/result.html?${p.toString()}`;
    } catch (e) {
      submitBtn.disabled = false;
      submitBtn.textContent = "採点する";
      alert(e.message || "送信エラー");
    }
  });

  loadQuestions();
})();
