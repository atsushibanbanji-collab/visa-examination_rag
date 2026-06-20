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

// 難易度レベル（順序つき）と日本語表示名の単一の定義元。
const LEVELS = ["beginner", "intermediate", "advanced"];
const LEVEL_NAMES = { beginner: "初級", intermediate: "中級", advanced: "上級" };

// レベルIDを日本語表示名へ
function levelLabel(id) {
  return LEVEL_NAMES[id] || id;
}

// ISO日時 → "YYYY-MM-DD HH:MM"（不正な値はそのまま返す）
function fmtDate(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
         `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ISO日時 → "YYYY/MM/DD"（時刻なし。不正・空は null）
function fmtDateShort(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d)) return null;
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())}`;
}

// ログイン必須ページの共通ガード。未ログインならトップ（ログイン画面）へ送り null を返す。
// 成功時はログイン中ユーザー（id/email/display_name）を返す。
async function requireLogin() {
  try {
    const res = await fetch("/api/auth/me");
    if (!res.ok) { location.href = "/"; return null; }
    return await res.json();
  } catch (e) {
    location.href = "/";
    return null;
  }
}

// ログアウトしてログイン画面へ戻す共通処理。
async function logoutAndRedirect() {
  try { await fetch("/api/auth/logout", { method: "POST" }); } catch (e) {}
  location.href = "/";
}

// 正答率(%) → スコアピルのCSSクラス
function pillClass(pct) {
  if (pct >= 80) return "high";
  if (pct >= 60) return "mid";
  return "low";
}

// チャレンジのステータス内部コード → 表示ラベル（管理画面用）
const CHALLENGE_STATUS_LABEL = {
  open: "未処理",
  accepted: "処理済",
  closed: "クローズ",
  rejected: "却下",
};
