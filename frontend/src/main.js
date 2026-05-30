import maplibregl from "maplibre-gl";

const API = "/api";

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: {
      osm: {
        type: "raster",
        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        tileSize: 256,
        attribution: "© OpenStreetMap",
      },
    },
    layers: [{ id: "osm", type: "raster", source: "osm" }],
  },
  center: [15.5, 49.8],
  zoom: 7,
});

map.addControl(new maplibregl.NavigationControl(), "top-right");

let startLngLat = null;
let dragging = false;
let bbox = null;

const els = {
  w: document.getElementById("bbox-w"),
  s: document.getElementById("bbox-s"),
  e: document.getElementById("bbox-e"),
  n: document.getElementById("bbox-n"),
  scale: document.getElementById("scale"),
  experimental: document.getElementById("opt-experimental"),
  zabaged: document.getElementById("opt-zabaged"),
  advanced: document.getElementById("opt-advanced"),
  submit: document.getElementById("submit"),
  status: document.getElementById("status"),
  statusMsg: document.getElementById("status-msg"),
  statusBar: document.getElementById("status-bar"),
  downloads: document.getElementById("downloads"),
};

function setBboxInputs(b) {
  bbox = b;
  els.w.value = b[0].toFixed(5);
  els.s.value = b[1].toFixed(5);
  els.e.value = b[2].toFixed(5);
  els.n.value = b[3].toFixed(5);
}

function drawBbox(b) {
  const coords = [
    [b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]],
  ];
  const data = { type: "Feature", geometry: { type: "Polygon", coordinates: [coords] } };
  if (map.getSource("bbox")) {
    map.getSource("bbox").setData(data);
  } else {
    map.addSource("bbox", { type: "geojson", data });
    map.addLayer({
      id: "bbox-fill", type: "fill", source: "bbox",
      paint: { "fill-color": "#2563eb", "fill-opacity": 0.15 },
    });
    map.addLayer({
      id: "bbox-line", type: "line", source: "bbox",
      paint: { "line-color": "#2563eb", "line-width": 2 },
    });
  }
}

map.on("mousedown", (e) => {
  if (!e.originalEvent.shiftKey) return;
  e.preventDefault();
  map.getCanvas().style.cursor = "crosshair";
  map.dragPan.disable();
  startLngLat = e.lngLat;
  dragging = true;
});
map.on("mousemove", (e) => {
  if (!dragging) return;
  const b = [
    Math.min(startLngLat.lng, e.lngLat.lng),
    Math.min(startLngLat.lat, e.lngLat.lat),
    Math.max(startLngLat.lng, e.lngLat.lng),
    Math.max(startLngLat.lat, e.lngLat.lat),
  ];
  setBboxInputs(b);
  drawBbox(b);
});
map.on("mouseup", () => {
  if (!dragging) return;
  dragging = false;
  map.dragPan.enable();
  map.getCanvas().style.cursor = "";
});

for (const k of ["w", "s", "e", "n"]) {
  els[k].addEventListener("change", () => {
    const b = [parseFloat(els.w.value), parseFloat(els.s.value), parseFloat(els.e.value), parseFloat(els.n.value)];
    if (b.every(Number.isFinite)) { setBboxInputs(b); drawBbox(b); }
  });
}

async function submit() {
  if (!bbox) { alert("Vyber bbox (Shift + tažení)."); return; }
  els.submit.disabled = true;
  els.downloads.innerHTML = "";
  els.status.hidden = false;
  els.statusMsg.textContent = "Odesílám…";
  els.statusBar.style.width = "0%";

  const body = {
    bbox,
    scale: parseInt(els.scale.value, 10),
    options: {
      experimental_points: els.experimental.checked,
      use_zabaged: els.zabaged.checked,
      advanced: els.advanced.checked,
    },
  };

  let res;
  try {
    res = await fetch(`${API}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    fail(`Chyba spojení: ${err.message}`);
    return;
  }
  if (!res.ok) { fail(`HTTP ${res.status}: ${await res.text()}`); return; }
  const job = await res.json();
  poll(job.id);
}

async function poll(id) {
  while (true) {
    await new Promise((r) => setTimeout(r, 800));
    const res = await fetch(`${API}/jobs/${id}`);
    if (!res.ok) { fail(`HTTP ${res.status}`); return; }
    const j = await res.json();
    els.statusMsg.textContent = j.message || j.status;
    els.statusBar.style.width = `${Math.round(j.progress * 100)}%`;
    if (j.status === "done") {
      els.statusMsg.textContent = "✓ Hotovo";
      els.downloads.innerHTML = `
        <a href="${API}/jobs/${id}/pdf" download>📄 Stáhnout PDF</a>
        <a href="${API}/jobs/${id}/omap" download>🗺 Stáhnout .omap</a>
        <a href="${API}/jobs/${id}/preview.png" target="_blank">🖼 Náhled (nové okno)</a>
        <img src="${API}/jobs/${id}/preview.png?t=${Date.now()}"
             style="margin-top:10px;max-width:100%;border:1px solid #ccc"
             alt="náhled mapy" />
      `;
      els.submit.disabled = false;
      return;
    }
    if (j.status === "error") { fail(j.error || "neznámá chyba"); return; }
  }
}

function fail(msg) {
  els.statusMsg.textContent = `Chyba: ${msg}`;
  els.submit.disabled = false;
}

els.submit.addEventListener("click", submit);
