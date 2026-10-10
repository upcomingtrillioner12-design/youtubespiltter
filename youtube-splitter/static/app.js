const $ = (id) => document.getElementById(id);

let videoMeta = null;   // {duration, title, ...}
let selectedSec = 30;
let jobId = null;
let pollTimer = null;

const fmtDur = (s) => {
  s = Math.round(s);
  const m = Math.floor(s / 60), r = s % 60;
  return m ? `${m}m ${r}s` : `${r}s`;
};

/* ---------- Step 1 : fetch info ---------- */
$("analyze-btn").onclick = async () => {
  const url = $("url").value.trim();
  if (!url) return hint("Paste a link first.");
  const btn = $("analyze-btn");
  btn.disabled = true; btn.textContent = "Fetching…";
  try {
    const r = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, seconds: 30 }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "Could not read that video.");
    videoMeta = data;
    $("thumb").src = data.thumbnail || "";
    $("v-title").textContent = data.title;
    $("v-sub").textContent =
      `${data.uploader || "unknown channel"} · ${fmtDur(data.duration)}` +
      (data.view_count ? ` · ${data.view_count.toLocaleString()} views` : "");
    $("video-info").classList.remove("hidden");
    $("step-options").classList.remove("hidden");
    hint("");
    updateEstimate();
    $("step-options").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    hint(e.message, true);
  } finally {
    btn.disabled = false; btn.textContent = "Fetch video";
  }
};

function hint(msg, isErr = false) {
  const h = $("analyze-hint");
  h.textContent = msg || "Works with regular videos, Shorts and youtu.be links.";
  h.classList.toggle("error", isErr);
}

/* ---------- Step 2 : presets ---------- */
$("presets").addEventListener("click", (e) => {
  const btn = e.target.closest(".preset");
  if (!btn) return;
  document.querySelectorAll(".preset").forEach((b) => b.classList.remove("selected"));
  btn.classList.add("selected");
  if (btn.dataset.sec === "custom") {
    $("custom-row").classList.remove("hidden");
    selectedSec = parseInt($("custom-sec").value) || 45;
    $("custom-sec").focus();
  } else {
    $("custom-row").classList.add("hidden");
    selectedSec = parseInt(btn.dataset.sec);
  }
  updateEstimate();
});
$("custom-sec").oninput = () => { selectedSec = parseInt($("custom-sec").value) || 1; updateEstimate(); };
$("accurate").onchange = updateEstimate;

function updateEstimate() {
  if (!videoMeta?.duration) { $("estimate").textContent = ""; return; }
  const n = Math.ceil(videoMeta.duration / selectedSec);
  const over = n * selectedSec - videoMeta.duration;
  $("estimate").textContent =
    `“${videoMeta.title}” is ${fmtDur(videoMeta.duration)} → about ${n} clip${n > 1 ? "s" : ""}` +
    (over > 1 ? ` (last clip ~${fmtDur(selectedSec - over)})` : "") +
    ($("accurate").checked ? " · frame-exact mode" : " · fast keyframe mode");
}

/* ---------- Step 3 : split & poll ---------- */
$("split-btn").onclick = async () => {
  const body = {
    url: $("url").value.trim(),
    seconds: selectedSec,
    accurate: $("accurate").checked,
  };
  const btn = $("split-btn");
  btn.disabled = true; btn.textContent = "Starting…";
  try {
    const r = await fetch("/api/split", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "Failed to start.");
    jobId = data.job_id;
    $("step-progress").classList.remove("hidden");
    $("clips").innerHTML = "";
    $("download-all").classList.add("hidden");
    $("new-job").classList.add("hidden");
    $("step-progress").scrollIntoView({ behavior: "smooth" });
    poll();
  } catch (e) {
    alert(e.message);
  } finally {
    btn.disabled = false; btn.textContent = "✂ Split into clips";
  }
};

async function poll() {
  clearTimeout(pollTimer);
  try {
    const r = await fetch(`/api/job/${jobId}`);
    const job = await r.json();
    $("fill").style.width = `${job.progress || 0}%`;
    $("status-msg").textContent = `${job.message || ""} ${job.progress ? Math.round(job.progress) + "%" : ""}`;

    if (job.status === "done") {
      $("status-msg").textContent = `Done — ${job.clips.length} clips ready.`;
      renderClips(job);
      $("zip-link").href = `/api/download/${jobId}`;
      $("download-all").classList.remove("hidden");
      $("new-job").classList.remove("hidden");
      return;
    }
    if (job.status === "error") {
      $("status-msg").textContent = "⚠ " + job.message;
      $("status-msg").classList.add("error");
      $("new-job").classList.remove("hidden");
      return;
    }
    pollTimer = setTimeout(poll, 1500);
  } catch {
    pollTimer = setTimeout(poll, 2500);
  }
}

function renderClips(job) {
  const box = $("clips");
  box.innerHTML = "";
  job.clips.forEach((c) => {
    const div = document.createElement("div");
    div.className = "clip";
    div.innerHTML = `
      <div class="num">#${String(c.index).padStart(2, "0")}</div>
      <div class="dur">${fmtDur(c.duration)} · ${(c.size / 1048576).toFixed(1)} MB</div>
      <a href="/api/download/${jobId}/${c.index}" download>⬇ Download</a>`;
    box.appendChild(div);
  });
}

$("new-job").onclick = () => {
  clearTimeout(pollTimer);
  if (jobId) fetch(`/api/job/${jobId}`, { method: "DELETE" }).catch(() => {});
  jobId = null; videoMeta = null;
  ["step-options", "step-progress", "video-info"].forEach((id) => $(id).classList.add("hidden"));
  $("url").value = ""; $("fill").style.width = "0";
  $("status-msg").classList.remove("error");
  window.scrollTo({ top: 0, behavior: "smooth" });
  $("url").focus();
};

$("url").addEventListener("keydown", (e) => { if (e.key === "Enter") $("analyze-btn").click(); });
