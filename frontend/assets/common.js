// ===========================================================
// common.js — フロント共通ユーティリティ
//
// result.html / units.html / admin.js / quiz.js に重複定義されていた
// 小さなヘルパをここへ一本化する。各HTMLで <script src="/assets/common.js">
// を他のスクリプトより前に読み込むことで、グローバル関数として使える。
//
// 挙動は従来の各実装と同一（ロジックは変えていない）。
// ===========================================================

// HTMLエスケープ（XSS対策）
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// レベルIDを日本語表示名へ
function levelLabel(id) {
  return { beginner: "初級", intermediate: "中級", advanced: "上級" }[id] || id;
}

// ISO日時 → "YYYY-MM-DD HH:MM"（不正な値はそのまま返す）
function fmtDate(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
         `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// 正答率(%) → スコアピルのCSSクラス
function pillClass(pct) {
  if (pct >= 80) return "high";
  if (pct >= 60) return "mid";
  return "low";
}
