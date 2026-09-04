import * as pdfjsLib from "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/pdf.min.mjs";
pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/pdf.worker.min.mjs";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};

const state = { accounts: [], account: null, detail: null, pdf: null, pdfUrl: null, page: 1, boxes: [], scale: 1 };

// ---------------------------------------------------------------- accounts
async function loadAccounts() {
  state.accounts = await api("/api/accounts");
  const sel = $("account");
  sel.innerHTML = state.accounts.map((a) =>
    `<option value="${a.account_id}">${a.account_id} · ${esc(a.named_insured)} · ${esc(a.location.split(",").slice(1).join(",").trim())} · ${a.endorsements.length} endorsement${a.endorsements.length === 1 ? "" : "s"}</option>`).join("");
  const preferred = state.accounts.find((a) => a.endorsements.length >= 2) || state.accounts[0];
  sel.value = preferred.account_id;
  await selectAccount(preferred.account_id);
}

async function selectAccount(id) {
  state.account = state.accounts.find((a) => a.account_id === id);
  state.detail = await api(`/api/accounts/${id}`);
  const a = state.account;
  const ends = a.endorsements.map((e) => `${e.form_id} (${e.effective_date})`).join(", ") || "none";
  $("acct-meta").innerHTML = `Policy <span class="mono">${a.policy_number}</span> · ${esc(a.location)} · zone ${a.flood_zone} · term ${a.term_start} → ${a.term_end}<br>Endorsements: ${esc(ends)}${a.mid_term_changes ? ` · ${a.mid_term_changes} mid-term change` : ""}`;
  $("examples").innerHTML = state.detail.examples.map((q) => `<button class="chip">${esc(q)}</button>`).join("");
  $("examples").querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => { $("question").value = b.textContent; ask(); }));
  renderChanges();
  $("answer").classList.add("hidden");
  await openPdf(`/api/forms/NFIP-DWELLING@2021-10/pdf`, "NFIP-DWELLING@2021-10", 3, []);
}

// ---------------------------------------------------------------- ask
async function ask() {
  const question = $("question").value.trim();
  if (!question) return;
  const btn = $("askbtn");
  btn.disabled = true; btn.textContent = "Asking…";
  try {
    const body = { account_id: state.account.account_id, question, loop: $("loop").checked, generator: $("generator").value || null };
    if ($("asof").value) body.as_of = $("asof").value;
    const res = await api("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    renderAnswer(res);
  } catch (e) {
    $("answer").classList.remove("hidden");
    $("status").className = "status withheld"; $("status").textContent = "error";
    $("answer-text").textContent = ""; $("reason").textContent = e.message; $("citations").innerHTML = ""; $("meta").textContent = "";
  } finally { btn.disabled = false; btn.textContent = "Ask"; }
}

function renderAnswer(res) {
  const a = res.answer;
  $("answer").classList.remove("hidden");
  $("status").className = `status ${a.status}`; $("status").textContent = a.status;
  $("route").textContent = `route: ${a.route}${a.loop_used ? " · retry loop fired" : ""}${a.rewritten_query ? ` → "${a.rewritten_query}"` : ""}`;
  $("answer-text").textContent = a.status === "answered" ? a.answer : "";
  $("reason").textContent = a.status === "answered" ? "" : a.reason;
  $("citations").innerHTML = res.citations.map((c, i) => {
    const where = c.paths.length ? c.paths.join(", ") : c.source === "declarations" ? "declarations page" : c.source;
    const lin = c.lineage.length ? `<span class="lin">as amended by ${esc(c.lineage.join(", "))}</span>` : "";
    const del = c.active === false ? `<span class="lin">deleted</span>` : "";
    return `<button class="cite" data-i="${i}"><span class="where">${esc(where)}</span>${lin}${del}<div class="q">${esc(c.quote)}</div></button>`;
  }).join("") || `<div class="reason">no citations</div>`;
  $("citations").querySelectorAll(".cite").forEach((b) => b.addEventListener("click", () => showCitation(res.citations[+b.dataset.i], b)));
  const checks = Object.entries(a.checks).map(([k, v]) => `${k}=${v ? "ok" : "FAIL"}`).join(" ");
  $("meta").textContent = `generator ${a.generator} · ${checks} · ${Math.round(a.latency_ms)} ms · retrieval query: “${res.retrieval_query}”`;
  const first = res.citations.find((c) => c.bboxes && c.bboxes.length) || res.citations[0];
  if (first) showCitation(first, $("citations").querySelector(".cite"));
  $("answer").scrollIntoView({ block: "nearest", behavior: "smooth" });
}

async function showCitation(c, btn) {
  $("citations").querySelectorAll(".cite").forEach((x) => x.classList.remove("active"));
  if (btn) btn.classList.add("active");
  activateTab("pdf");
  if (c.pdf_url) await openPdf(c.pdf_url, c.form_key, c.page || 1, c.bboxes || []);
  const panel = $("clause-panel");
  if (c.paths.length || c.text_as_endorsed) {
    panel.classList.remove("hidden");
    $("clause-path").textContent = `${c.form_key || ""} › ${c.paths.join(", ")}${c.lineage.length ? `  ·  modified by ${c.lineage.join(", ")}` : ""}`;
    const cur = c.text_as_endorsed ?? c.original_text ?? c.quote;
    const changed = c.text_as_endorsed && c.original_text && c.text_as_endorsed !== c.original_text;
    $("clause-versions").innerHTML = changed || c.active === false
      ? `<div class="ver"><h4>Printed form</h4>${esc(c.original_text)}</div><div class="ver current ${c.active === false ? "deleted" : ""}"><h4>${c.active === false ? "Deleted by endorsement" : "As endorsed"}</h4>${esc(cur)}</div>`
      : `<div class="ver" style="grid-column: 1 / -1"><h4>Clause text</h4>${esc(cur)}</div>`;
  } else panel.classList.add("hidden");
}

// ---------------------------------------------------------------- pdf
async function openPdf(url, title, page, boxes) {
  if (state.pdfUrl !== url) { state.pdf = await pdfjsLib.getDocument(url).promise; state.pdfUrl = url; }
  state.page = Math.min(Math.max(1, page), state.pdf.numPages); state.boxes = boxes;
  $("pdf-title").textContent = title;
  await renderPage();
}

let renderTask = null, renderSeq = 0;
async function renderPage() {
  const seq = ++renderSeq;
  if (renderTask) { try { renderTask.cancel(); } catch (_) { /* already done */ } renderTask = null; }
  const pg = await state.pdf.getPage(state.page);
  if (seq !== renderSeq) return;
  const wrap = $("pane-pdf");
  const avail = Math.max(320, wrap.clientWidth - 40);
  const base = pg.getViewport({ scale: 1 });
  const scale = Math.min(1.6, avail / base.width);
  const vp = pg.getViewport({ scale });
  const canvas = $("pdf-canvas");
  canvas.width = vp.width; canvas.height = vp.height;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, canvas.width, canvas.height);
  renderTask = pg.render({ canvasContext: ctx, viewport: vp });
  try { await renderTask.promise; } catch (e) { if (e?.name === "RenderingCancelledException") return; throw e; }
  if (seq !== renderSeq) return;
  $("pageinfo").textContent = `${state.page} / ${state.pdf.numPages}`;
  const ov = $("overlay"); ov.innerHTML = "";
  for (const b of state.boxes.filter((b) => b.page === state.page)) {
    const d = document.createElement("div"); d.className = "hl";
    d.style.left = `${(b.x0 - 2) * scale}px`; d.style.top = `${(b.y0 - 2) * scale}px`;
    d.style.width = `${(b.x1 - b.x0 + 4) * scale}px`; d.style.height = `${(b.y1 - b.y0 + 4) * scale}px`;
    ov.appendChild(d);
  }
  const first = ov.querySelector(".hl");
  if (first) first.scrollIntoView({ block: "center", behavior: "smooth" });
}
$("prev").addEventListener("click", () => { if (state.pdf && state.page > 1) { state.page--; renderPage(); } });
$("next").addEventListener("click", () => { if (state.pdf && state.page < state.pdf.numPages) { state.page++; renderPage(); } });
window.addEventListener("resize", () => state.pdf && renderPage());

// ---------------------------------------------------------------- tabs, changes, review, eval
function activateTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("hidden", p.id !== `pane-${name}`));
  if (name === "pdf" && state.pdf) renderPage();
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => activateTab(t.dataset.tab)));

function renderChanges() {
  const rows = state.detail.changed;
  if (!rows.length) { $("changes").innerHTML = `<div class="pane-intro">No endorsements change this policy's clauses.</div>`; return; }
  $("changes").innerHTML = `<table class="changes"><thead><tr><th>Clause</th><th>Change</th><th>Printed form</th><th>As endorsed</th></tr></thead><tbody>` +
    rows.map((r) => {
      const ops = r.lineage.map((l) => `<span class="op ${l.op}">${l.op}</span> <span class="mono">${esc(l.endorsement)}</span><br><span class="mono" style="color:var(--muted)">${l.effective_date || ""}</span>`).join("<br>");
      const cur = r.active ? esc(r.text_as_endorsed) : `<del>${esc(r.text_as_endorsed)}</del>`;
      const flags = r.flags.length ? `<div class="mono" style="color:var(--amber)">${esc(r.flags.join("; "))}</div>` : "";
      return `<tr><td class="mono">${esc(r.path)}<br><span style="color:var(--muted)">${esc(r.heading || "")}</span></td><td>${ops}</td><td>${esc(r.original_text || "—")}</td><td>${cur}${flags}</td></tr>`;
    }).join("") + `</tbody></table>`;
}

async function loadReview() {
  const ops = await api("/api/review");
  $("review").innerHTML = `<table class="review"><thead><tr><th>Endorsement</th><th>Op</th><th>Status</th><th>Target as written</th><th>Why</th></tr></thead><tbody>` +
    ops.map((o) => `<tr><td class="mono">${esc(o.endorsement_key)}</td><td><span class="op ${o.op}">${o.op}</span></td><td><span class="pill ${o.status}">${o.status}</span></td><td>${esc(o.target_ref || "–")}</td><td>${esc(o.notes.join("; "))}${o.scanned ? " (scanned PDF)" : ""}</td></tr>`).join("") +
    `</tbody></table>`;
}

function mdToHtml(md) {
  const lines = md.split("\n"); let html = "", table = [];
  const flush = () => { if (table.length) { const [h, , ...b] = table; const cells = (r) => r.split("|").slice(1, -1).map((c) => c.trim()); html += `<table><thead><tr>${cells(h).map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${b.map((r) => `<tr>${cells(r).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`; table = []; } };
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`(.+?)`/g, "<code>$1</code>");
  for (const l of lines) { if (l.startsWith("|")) table.push(l); else { flush(); if (l.startsWith("#### ")) html += `<h3>${inline(l.slice(5))}</h3>`; else if (l.trim()) html += `<p>${inline(l)}</p>`; } }
  flush(); return html;
}
async function loadEval() {
  const e = await api("/api/eval");
  $("eval").innerHTML = `<h3>Retrieval ladder</h3>${e.retrieval ? mdToHtml(e.retrieval) : "<p>not run</p>"}<h3>Generation</h3>${e.generation ? mdToHtml(e.generation) : "<p>not run</p>"}` +
    (e.generation_claude ? `<h3>Generation, Claude</h3>${mdToHtml(e.generation_claude)}` : "");
}

// ---------------------------------------------------------------- boot
(async () => {
  const h = await api("/api/health");
  $("health").textContent = `${h.accounts} accounts · ${h.chunks.toLocaleString()} chunks · ${h.embedder} · rerank ${h.reranker}`;
  $("generator").innerHTML = h.generators.map((g) => `<option value="${g}">${g}</option>`).join("");
  if (h.generators.includes("claude")) $("generator").value = "claude";
  $("account").addEventListener("change", (e) => selectAccount(e.target.value));
  $("askbtn").addEventListener("click", ask);
  $("question").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } });
  await loadAccounts();
  loadReview(); loadEval();
  // Deep link: /?account=SYN-00001&q=How+does+the+policy+define+basement
  const params = new URLSearchParams(location.search);
  if (params.get("account") && state.accounts.some((a) => a.account_id === params.get("account"))) {
    $("account").value = params.get("account");
    await selectAccount(params.get("account"));
  }
  if (params.get("q")) { $("question").value = params.get("q"); await ask(); }
})().catch((e) => { $("health").textContent = `error: ${e.message}`; });
