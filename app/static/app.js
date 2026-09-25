/* 徒步轨迹分享前端：无底图 Canvas 预览、遮蔽圆编辑、调用后端裁切与导出 */
"use strict";

const METERS_PER_DEG_LAT = 111319.9;

const state = {
  trackId: null,
  name: null,
  segments: [],   // 原始轨迹 [[{lat,lon,ele,time},...],...]
  circles: [],    // [{lat, lon, radius_m}]
  masked: null,   // /api/mask 返回结果
  transform: null,
};

const canvas = document.getElementById("map");
const ctx = canvas.getContext("2d");

/* ---------- 坐标变换：经纬度 <-> 画布像素 ---------- */

function computeTransform() {
  if (!state.segments.length) {
    state.transform = null;
    return;
  }
  let minLat = 90, maxLat = -90, minLon = 180, maxLon = -180;
  for (const seg of state.segments) {
    for (const p of seg) {
      if (p.lat < minLat) minLat = p.lat;
      if (p.lat > maxLat) maxLat = p.lat;
      if (p.lon < minLon) minLon = p.lon;
      if (p.lon > maxLon) maxLon = p.lon;
    }
  }
  const dpr = window.devicePixelRatio || 1;
  const pad = 36 * dpr;
  const W = canvas.width, H = canvas.height;
  const meanLat = (minLat + maxLat) / 2;
  const cosLat = Math.cos((meanLat * Math.PI) / 180);
  const dx = Math.max((maxLon - minLon) * cosLat, 1e-9);
  const dy = Math.max(maxLat - minLat, 1e-9);
  const scale = Math.min((W - 2 * pad) / dx, (H - 2 * pad) / dy);
  state.transform = {
    minLat, minLon, cosLat, scale, pad,
    offX: (W - 2 * pad - dx * scale) / 2,
    offY: (H - 2 * pad - dy * scale) / 2,
  };
}

function toPx(lat, lon) {
  const t = state.transform;
  const x = t.pad + t.offX + (lon - t.minLon) * t.cosLat * t.scale;
  const y = canvas.height - (t.pad + t.offY + (lat - t.minLat) * t.scale);
  return [x, y];
}

function toLatLon(x, y) {
  const t = state.transform;
  const lat = t.minLat + (canvas.height - t.pad - t.offY - y) / t.scale;
  const lon = t.minLon + (x - t.pad - t.offX) / (t.cosLat * t.scale);
  return [lat, lon];
}

/* ---------- 绘制 ---------- */

function drawPolyline(seg, style, width, dash) {
  if (!seg || seg.length < 2) return;
  ctx.strokeStyle = style;
  ctx.lineWidth = width;
  ctx.setLineDash(dash || []);
  ctx.beginPath();
  seg.forEach((p, i) => {
    const [x, y] = toPx(p.lat, p.lon);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.setLineDash([]);
}

function draw() {
  const dpr = window.devicePixelRatio || 1;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#f7f7f5";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  if (!state.transform) {
    ctx.fillStyle = "#9e9e9e";
    ctx.font = `${15 * dpr}px sans-serif`;
    ctx.textAlign = "center";
    ctx.fillText("上传 GPX 后在此预览轨迹（无底图）", canvas.width / 2, canvas.height / 2);
    return;
  }

  // 原始轨迹（灰）
  for (const seg of state.segments) drawPolyline(seg, "#c4c4c4", 2 * dpr);

  // 裁切结果：隐藏段（红虚线）+ 可见段（蓝）
  if (state.masked) {
    for (const seg of state.masked.hidden_segments) {
      drawPolyline(seg, "#e53935", 2.5 * dpr, [7 * dpr, 6 * dpr]);
    }
    for (const seg of state.masked.visible_segments) {
      drawPolyline(seg, "#1e88e5", 2.5 * dpr);
    }
  }

  // 遮蔽圆
  const t = state.transform;
  for (const c of state.circles) {
    const [x, y] = toPx(c.lat, c.lon);
    const ry = (c.radius_m / METERS_PER_DEG_LAT) * t.scale;
    const rx =
      (c.radius_m / (METERS_PER_DEG_LAT * Math.cos((c.lat * Math.PI) / 180))) *
      t.cosLat * t.scale;
    ctx.beginPath();
    ctx.ellipse(x, y, Math.max(rx, 2), Math.max(ry, 2), 0, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(229, 57, 53, 0.12)";
    ctx.fill();
    ctx.strokeStyle = "#e53935";
    ctx.lineWidth = 1.5 * dpr;
    ctx.setLineDash([5 * dpr, 4 * dpr]);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // 起点 / 终点标记
  const first = state.segments[0] && state.segments[0][0];
  const lastSeg = state.segments[state.segments.length - 1];
  const last = lastSeg && lastSeg[lastSeg.length - 1];
  if (first) drawMarker(first, "#2e7d32", "起", dpr);
  if (last) drawMarker(last, "#c62828", "终", dpr);
}

function drawMarker(p, color, label, dpr) {
  const [x, y] = toPx(p.lat, p.lon);
  ctx.beginPath();
  ctx.arc(x, y, 7 * dpr, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.fillStyle = "#fff";
  ctx.font = `${8 * dpr}px sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(label, x, y);
  ctx.textBaseline = "alphabetic";
}

/* ---------- 后端交互 ---------- */

let maskTimer = null;
function scheduleMask() {
  clearTimeout(maskTimer);
  maskTimer = setTimeout(refreshMask, 250);
}

async function refreshMask() {
  if (!state.trackId) return;
  try {
    const res = await fetch("/api/mask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_id: state.trackId, circles: state.circles }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(errDetail(data, "裁切计算失败"));
    state.masked = data;
    renderStats(data);
    draw();
  } catch (err) {
    showMessage(err.message);
  }
}

let splitsTimer = null;
function scheduleSplits() {
  clearTimeout(splitsTimer);
  splitsTimer = setTimeout(refreshSplits, 250);
}

async function refreshSplits() {
  if (!state.trackId) return;
  const input = document.getElementById("split-km");
  let km = Number(input.value);
  if (!Number.isFinite(km) || km <= 0) km = 1;
  km = Math.min(10, Math.max(0.1, km));
  try {
    const res = await fetch("/api/splits", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_id: state.trackId, split_km: km }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(errDetail(data, "配速计算失败"));
    renderSplits(data);
  } catch (err) {
    showMessage(err.message);
  }
}

async function uploadFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/upload", { method: "POST", body: fd });
  const data = await res.json();
  if (!res.ok) throw new Error(errDetail(data, "上传失败"));
  state.trackId = data.track_id;
  state.name = data.name;
  state.segments = data.segments;
  state.circles = [];
  state.masked = null;
  computeTransform();
  renderTrackInfo(data.stats);
  renderCircleList();
  await refreshMask();
  await refreshSplits();
}

async function downloadGpx() {
  if (!state.trackId) return;
  try {
    const res = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_id: state.trackId, circles: state.circles }),
    });
    if (!res.ok) {
      const data = await res.json();
      throw new Error(errDetail(data, "导出失败"));
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${state.name || "track"}-masked.gpx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    showMessage(err.message);
  }
}

/* ---------- 界面渲染 ---------- */

function fmtKm(m) {
  return (m / 1000).toFixed(2);
}

function renderTrackInfo(s) {
  const el = document.getElementById("track-info");
  el.classList.remove("muted");
  const t0 = s.start_time ? new Date(s.start_time).toLocaleString() : "-";
  const t1 = s.end_time ? new Date(s.end_time).toLocaleString() : "-";
  el.innerHTML =
    `<div>轨迹名称：<b>${escapeHtml(state.name)}</b></div>` +
    `<div>轨迹点：<b>${s.points}</b> 个，原始分段：<b>${s.segments}</b> 个</div>` +
    `<div>总距离：<b>${fmtKm(s.distance_m)} km</b></div>` +
    `<div>时间：${t0}<br>至　${t1}</div>`;
}

function renderStats(d) {
  const el = document.getElementById("stats");
  el.classList.remove("muted");
  const hiddenKm = fmtKm(d.distance_before_m - d.distance_after_m);
  el.innerHTML =
    `<div>隐藏段数：<b>${d.hidden_intervals}</b></div>` +
    `<div>裁切前距离：<b>${fmtKm(d.distance_before_m)} km</b></div>` +
    `<div>裁切后距离：<b>${fmtKm(d.distance_after_m)} km</b>（隐藏 ${hiddenKm} km）</div>` +
    `<div>可见分段：<b>${d.visible_segments.length}</b> 个 trkseg（导出时互不连接）</div>`;
  document.getElementById("download").disabled = false;
}

function fmtDuration(totalSec) {
  const s = Math.max(0, Math.round(totalSec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return (h ? `${h}:` : "") + `${mm}:${String(sec).padStart(2, "0")}`;
}

function fmtPace(secPerKm) {
  if (secPerKm == null || !Number.isFinite(secPerKm)) return "—"; // 零里程不显示无穷配速
  const m = Math.floor(secPerKm / 60);
  const s = Math.round(secPerKm % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function fmtClock(iso) {
  if (!iso) return "-";
  return new Date(iso).toLocaleTimeString("zh-CN", { hour12: false });
}

function renderSplits(data) {
  const el = document.getElementById("splits");
  el.classList.remove("muted");
  let html = "";
  for (const seg of data.segments) {
    html += `<div class="split-seg">原始分段 #${seg.segment_index}` +
      ` · 累计 ${fmtKm(seg.total_distance_m)} km</div>`;
    if (!seg.ok) {
      html += `<div class="split-warn">${escapeHtml(seg.message || "无法计算配速")}</div>`;
      continue;
    }
    if (!seg.splits.length) {
      html += `<div class="split-warn muted">轨迹点不足，无分段</div>`;
      continue;
    }
    html += `<table class="split-table"><thead><tr>` +
      `<th>段</th><th>距离(km)</th><th>耗时</th><th>配速(/km)</th><th>起止时间</th>` +
      `</tr></thead><tbody>`;
    for (const sp of seg.splits) {
      html += `<tr>` +
        `<td>${sp.index}</td>` +
        `<td>${fmtKm(sp.distance_m)}</td>` +
        `<td>${fmtDuration(sp.duration_s)}</td>` +
        `<td>${fmtPace(sp.pace_s_per_km)}</td>` +
        `<td>${fmtClock(sp.start_time)}–${fmtClock(sp.end_time)}</td>` +
        `</tr>`;
    }
    html += `</tbody></table>`;
  }
  el.innerHTML = html;
}

function renderCircleList() {
  const ul = document.getElementById("circle-list");
  ul.innerHTML = "";
  state.circles.forEach((c, i) => {
    const li = document.createElement("li");
    const coord = document.createElement("span");
    coord.className = "coord";
    coord.textContent = `#${i + 1} (${c.lat.toFixed(5)}, ${c.lon.toFixed(5)})`;
    const input = document.createElement("input");
    input.type = "number";
    input.min = "1";
    input.max = "100000";
    input.value = Math.round(c.radius_m);
    input.title = "半径（米）";
    input.addEventListener("change", () => {
      const v = Number(input.value);
      c.radius_m = Number.isFinite(v) && v > 0 ? Math.min(v, 100000) : c.radius_m;
      input.value = Math.round(c.radius_m);
      scheduleMask();
    });
    const del = document.createElement("button");
    del.textContent = "删除";
    del.addEventListener("click", () => {
      state.circles.splice(i, 1);
      renderCircleList();
      scheduleMask();
    });
    li.appendChild(coord);
    li.appendChild(input);
    li.appendChild(del);
    ul.appendChild(li);
  });
  draw();
}

let messageTimer = null;
function showMessage(text) {
  const el = document.getElementById("message");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(messageTimer);
  messageTimer = setTimeout(() => { el.hidden = true; }, 4000);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function errDetail(data, fallback) {
  // FastAPI 参数校验失败时 detail 为数组，统一转成可读字符串
  if (!data || data.detail == null) return fallback;
  if (typeof data.detail === "string") return data.detail;
  try {
    return data.detail.map((d) => d.msg).join("；") || fallback;
  } catch {
    return fallback;
  }
}

/* ---------- 事件绑定 ---------- */

function resizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.round(rect.width * dpr));
  canvas.height = Math.max(1, Math.round(rect.height * dpr));
  computeTransform();
  draw();
}

document.getElementById("file").addEventListener("change", (e) => {
  const file = e.target.files && e.target.files[0];
  if (file) uploadFile(file).catch((err) => showMessage(err.message));
});

canvas.addEventListener("click", (e) => {
  if (!state.transform) return;
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const x = (e.clientX - rect.left) * dpr;
  const y = (e.clientY - rect.top) * dpr;
  const [lat, lon] = toLatLon(x, y);
  const radiusInput = Number(document.getElementById("radius").value);
  const radius = Number.isFinite(radiusInput) && radiusInput > 0 ? radiusInput : 200;
  state.circles.push({ lat, lon, radius_m: radius });
  renderCircleList();
  scheduleMask();
});

document.getElementById("clear-circles").addEventListener("click", () => {
  if (!state.circles.length) return;
  state.circles = [];
  renderCircleList();
  scheduleMask();
});

document.getElementById("download").addEventListener("click", downloadGpx);

document.getElementById("split-km").addEventListener("input", scheduleSplits);

window.addEventListener("resize", resizeCanvas);
resizeCanvas();
