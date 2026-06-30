import { useState, useEffect } from "react";
import axios from "axios";
import Map, { Marker, Source, Layer } from "react-map-gl";
import "mapbox-gl/dist/mapbox-gl.css";
import "./App.css";

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN;
// API base: set VITE_API_BASE to the deployed backend URL in production;
// falls back to the local FastAPI dev server otherwise.
const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

// The 12 EJScreen indicators, in display order. Each pairs the raw value key
// (from `environmental`) with its national-percentile key (from `percentiles`).
// `pct: true` means the raw value is a 0–1 fraction shown as a percentage.
const INDICATORS = [
  { env: "pm25_avg_ugm3",             pctl: "pm25_pctile_national",        label: "Fine particles (PM2.5)",   unit: "µg/m³" },
  { env: "ozone_ppb",                 pctl: "ozone_pctile_national",       label: "Ozone",                    unit: "ppb" },
  { env: "diesel_pm_ugm3",            pctl: "diesel_pm_pctile_national",   label: "Diesel exhaust",           unit: "µg/m³" },
  { env: "air_toxics_cancer_risk",    pctl: "cancer_risk_pctile_national", label: "Air toxics cancer risk",   unit: "per million" },
  { env: "air_toxics_resp_hazard",    pctl: "resp_hazard_pctile_national", label: "Respiratory hazard",       unit: "index" },
  { env: "traffic_proximity",         pctl: "traffic_pctile_national",     label: "Traffic proximity",        unit: "vehicles/m" },
  { env: "lead_paint_pct",            pctl: "lead_paint_pctile_national",  label: "Lead paint (pre-1960 homes)", unit: "", pct: true },
  { env: "superfund_proximity",       pctl: "superfund_pctile_national",   label: "Superfund sites",          unit: "per km²" },
  { env: "rmp_facility_proximity",    pctl: "rmp_pctile_national",         label: "Risk-management facilities", unit: "per km²" },
  { env: "hazwaste_proximity",        pctl: "hazwaste_pctile_national",    label: "Hazardous waste sites",    unit: "per km²" },
  { env: "underground_storage_tanks", pctl: "ust_pctile_national",         label: "Underground storage tanks", unit: "per km²" },
  { env: "wastewater_discharge",      pctl: "wastewater_pctile_national",  label: "Wastewater discharge",     unit: "index" },
];

// Map a 0–100 burden value (higher = worse) to a severity color + background.
function severity(value) {
  if (value == null) return { color: "#9a8f7a", bg: "#efe9da" };
  if (value < 50)  return { color: "var(--good)",     bg: "var(--good-bg)" };
  if (value < 75)  return { color: "var(--moderate)", bg: "var(--moderate-bg)" };
  if (value < 90)  return { color: "var(--elevated)", bg: "var(--elevated-bg)" };
  return { color: "var(--severe)", bg: "var(--severe-bg)" };
}

function fmt(value, isPct) {
  if (value == null) return "—";
  if (isPct) return `${Math.round(value * 100)}%`;
  return Number.isInteger(value) ? value.toString() : value.toFixed(2);
}

// Grade key — what each letter means, worst-to-best is A→F, rendered as a
// single gradient scale bar with the current grade marked on it.
const GRADE_KEY = [
  { letter: "A", color: "#3f6b34", label: "Clean", desc: "Minimal environmental burden" },
  { letter: "B", color: "#6b8f3f", label: "Low",   desc: "Below-average burden" },
  { letter: "C", color: "#b08a1f", label: "Moderate", desc: "Around the national average" },
  { letter: "D", color: "#c1672f", label: "High",  desc: "Above-average burden" },
  { letter: "F", color: "#8c2f23", label: "Severe", desc: "Among the most burdened areas" },
];

// ── Mapbox layer style definitions ──────────────────────────────────────────
const heatmapLayer = {
  id: "air-quality-heat",
  type: "heatmap",
  paint: {
    "heatmap-weight": ["get", "weight"],
    "heatmap-intensity": 1.1,
    "heatmap-radius": 34,
    "heatmap-opacity": 0.75,
    "heatmap-color": [
      "interpolate", ["linear"], ["heatmap-density"],
      0, "rgba(0,0,0,0)",
      0.2, "#d9c9a0",
      0.4, "#cc9a3e",
      0.6, "#c1672f",
      0.8, "#9c3a26",
      1, "#5e1a12",
    ],
  },
};

const facilitiesLayer = {
  id: "facilities-circle",
  type: "circle",
  paint: {
    "circle-radius": 7,
    "circle-color": [
      "match", ["get", "type"],
      "Recent violation", "#8c2f23",
      "#3a3530",
    ],
    "circle-stroke-width": 2,
    "circle-stroke-color": "#fffdf8",
    "circle-opacity": 0.9,
  },
};

const greenFillLayer = {
  id: "green-fill",
  type: "fill",
  paint: { "fill-color": "#5c7a45", "fill-opacity": 0.3 },
};

const greenLineLayer = {
  id: "green-line",
  type: "line",
  paint: { "line-color": "#3f5b2e", "line-width": 1.5 },
};

// Average the three air-pollution percentiles to drive heatmap intensity.
function airIntensity(percentiles) {
  const vals = [
    percentiles?.pm25_pctile_national,
    percentiles?.ozone_pctile_national,
    percentiles?.diesel_pm_pctile_national,
  ].filter((v) => v != null);
  if (!vals.length) return 60;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

export default function App() {
  const [zip, setZip] = useState("");
  const [data, setData] = useState(null);
  const [layers, setLayers] = useState(null);
  const [visible, setVisible] = useState({ air: true, facilities: true, parks: true });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const toggle = (key) => setVisible((v) => ({ ...v, [key]: !v[key] }));

  const search = async (zipArg, { pushUrl = true } = {}) => {
    const z = (zipArg ?? zip).toString();
    if (!z || z.length !== 5) {
      setError("Please enter a 5-digit zip code.");
      return;
    }
    setZip(z);
    setLoading(true);
    setError(null);
    setData(null);
    setLayers(null);
    // Reflect the searched zip in the URL so the link is shareable / bookmarkable.
    if (pushUrl && window.location.pathname !== `/${z}`) {
      window.history.pushState({ zip: z }, "", `/${z}`);
    }
    try {
      const res = await axios.get(`${API_BASE}/api/neighborhood/${z}`);
      setData(res.data);
      // Fetch map overlays in the background — don't block the report card on them.
      const intensity = airIntensity(res.data.percentiles);
      axios
        .get(`${API_BASE}/api/map-layers/${z}`, { params: { intensity } })
        .then((r) => setLayers(r.data))
        .catch(() => setLayers(null));
    } catch (e) {
      setError(
        e.response?.data?.detail ||
          "Could not load data for that zip code. Please try again."
      );
    }
    setLoading(false);
  };

  // On first load (and on browser back/forward), read a zip from the URL path
  // like /78207 and auto-run that search so shared links open straight to the report.
  useEffect(() => {
    const loadFromUrl = () => {
      const m = window.location.pathname.match(/^\/(\d{5})$/);
      if (m) search(m[1], { pushUrl: false });
    };
    loadFromUrl();
    window.addEventListener("popstate", loadFromUrl);
    return () => window.removeEventListener("popstate", loadFromUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onKey = (e) => e.key === "Enter" && search();

  const rc = data?.report_card;
  // Color the score dial by the report's letter grade (falling back to the
  // score-based severity scale if no grade was returned), so the dial, the
  // grade pill, and the grade scale all share one consistent color.
  const gradeIndex = GRADE_KEY.findIndex((g) => g.letter === rc?.grade);
  const gradeMeta = gradeIndex >= 0 ? GRADE_KEY[gradeIndex] : null;
  const dialColor = gradeMeta?.color || severity(rc?.score).color;
  const dialBg = `color-mix(in srgb, ${dialColor} 12%, var(--surface))`;
  const gradeGradient = `linear-gradient(90deg, ${GRADE_KEY.map((g) => g.color).join(", ")})`;

  return (
    <div className="app">
      <header className="header">
        <h1>EJMapper</h1>
        <p className="tagline">Environmental justice by zip code</p>
        <p className="disclaimer">
          Report cards are generated by Claude, an AI model by Anthropic, using data from the EPA EJScreen database. AI-generated summaries may contain errors — always verify critical information with official sources.
        </p>
      </header>

      <div className="search">
        <input
          value={zip}
          onChange={(e) => setZip(e.target.value.replace(/\D/g, "").slice(0, 5))}
          onKeyDown={onKey}
          placeholder="Enter a 5-digit zip code"
          inputMode="numeric"
          maxLength={5}
        />
        <button onClick={() => search()} disabled={loading}>
          {loading ? "Loading…" : "Search"}
        </button>
      </div>

      {error && <div className="banner error">{error}</div>}

      {loading && (
        <div className="skeleton-report">
          <div className="score-card skel-card">
            <div className="skel skel-dial" />
            <div className="score-body">
              <div className="skel skel-line" style={{ width: "55%", marginBottom: 12 }} />
              <div className="skel skel-line" style={{ width: "90%", marginBottom: 6 }} />
              <div className="skel skel-line" style={{ width: "70%" }} />
            </div>
          </div>
          <div className="grade-key skel-card">
            <div className="skel skel-line" style={{ width: "30%", marginBottom: 16 }} />
            <div className="skel" style={{ height: 8, borderRadius: 999, marginBottom: 12 }} />
            <div className="skel skel-line" style={{ width: "80%" }} />
          </div>
          <div className="section">
            <div className="skel skel-heading" />
            <div className="indicator-grid">
              {Array.from({ length: 12 }).map((_, i) => (
                <div className="indicator skel-card" key={i} style={{ opacity: 1, animation: "none" }}>
                  <div className="skel skel-line" style={{ width: "70%", marginBottom: 14 }} />
                  <div className="skel skel-line" style={{ width: "40%", height: 22, marginBottom: 14 }} />
                  <div className="skel" style={{ height: 5, borderRadius: 999, marginBottom: 10 }} />
                  <div className="skel skel-line" style={{ width: "60%" }} />
                </div>
              ))}
            </div>
          </div>
          <div className="section">
            <div className="skel skel-heading" />
            {[80, 90, 65].map((w, i) => (
              <div className="list-item" key={i} style={{ marginBottom: 9 }}>
                <div className="skel skel-line" style={{ width: `${w}%` }} />
              </div>
            ))}
          </div>
          <div className="section">
            <div className="skel skel-heading" />
            {[75, 88].map((w, i) => (
              <div className="list-item action" key={i} style={{ marginBottom: 9 }}>
                <div className="skel skel-line" style={{ width: `${w}%` }} />
              </div>
            ))}
          </div>
        </div>
      )}

      {data && !loading && (
        <div className="report" key={data.zip_code}>
          {data.data_source === "mock" && (
            <div className="banner mock">
              Showing estimated data — the EPA EJScreen service is temporarily offline.
            </div>
          )}

          {/* Score hero */}
          <div
            className="score-card"
            style={{ "--sev-color": dialColor, "--sev-bg": dialBg }}
          >
            <div className="score-dial">
              <span className="num">{rc?.score ?? "—"}</span>
              <span className="out-of">out of 100</span>
            </div>
            <div className="score-body">
              <div className="zip-line">
                <h2>Zip code {data.zip_code}</h2>
                {rc?.grade && <span className="grade-pill">Grade {rc.grade}</span>}
              </div>
              <p className="summary">{rc?.summary}</p>
            </div>
          </div>

          {/* Grade key — a single gradient scale, A (clean) to F (severe),
              with the current grade marked on it. */}
          <div className="grade-key">
            <span className="grade-key-title">What the grade means</span>
            <div className="grade-scale">
              <div
                className="grade-scale-track"
                style={{ background: gradeGradient }}
              >
                {gradeIndex >= 0 && (
                  <span
                    className="grade-scale-marker"
                    style={{ left: `${(gradeIndex / (GRADE_KEY.length - 1)) * 100}%` }}
                  >
                    {rc.grade}
                  </span>
                )}
              </div>
              <div className="grade-scale-ticks">
                {GRADE_KEY.map((g) => (
                  <span
                    key={g.letter}
                    className={`grade-scale-tick ${rc?.grade === g.letter ? "active" : ""}`}
                  >
                    {g.letter}
                  </span>
                ))}
              </div>
            </div>
            <p className="grade-key-legend">
              {GRADE_KEY.map((g, i) => (
                <span key={g.letter} className={rc?.grade === g.letter ? "active" : ""}>
                  <b>{g.letter}</b> {g.label}
                  {i < GRADE_KEY.length - 1 && " · "}
                </span>
              ))}
            </p>
            {gradeMeta && <p className="grade-key-desc">{gradeMeta.desc}.</p>}
          </div>

          {/* Indicator cards */}
          <div className="section">
            <h3>Environmental indicators</h3>
            <div className="indicator-grid">
              {INDICATORS.map((ind, i) => {
                const raw = data.environmental?.[ind.env];
                const pctl = data.percentiles?.[ind.pctl];
                const s = severity(pctl);
                return (
                  <div className="indicator" key={ind.env} style={{ "--i": i }}>
                    <div className="label">{ind.label}</div>
                    <div className="value">
                      {fmt(raw, ind.pct)}
                      {ind.unit && <span className="unit">{ind.unit}</span>}
                    </div>
                    <div className="bar">
                      <span
                        style={{
                          width: `${pctl ?? 0}%`,
                          background: s.color,
                        }}
                      />
                    </div>
                    <div className="pctile">
                      {pctl != null ? (
                        <>Worse than <b>{Math.round(pctl)}%</b> of the US</>
                      ) : (
                        "No data"
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Key findings */}
          {rc?.key_findings?.length > 0 && (
            <div className="section">
              <h3>Key findings</h3>
              {rc.key_findings.map((f, i) => (
                <div className="list-item" key={i}>{f}</div>
              ))}
            </div>
          )}

          {/* Action items */}
          {rc?.action_items?.length > 0 && (
            <div className="section">
              <h3>What you can do</h3>
              {rc.action_items.map((a, i) => (
                <div className="list-item action" key={i}>{a}</div>
              ))}
            </div>
          )}

          {/* Map with toggleable layers */}
          {data.location && (
            <div className="section">
              <h3>Map &amp; layers</h3>

              <div className="layer-toggles">
                <button
                  className={`chip air ${visible.air ? "on" : ""}`}
                  onClick={() => toggle("air")}
                >
                  <span className="swatch" /> Air quality heatmap
                </button>
                <button
                  className={`chip fac ${visible.facilities ? "on" : ""}`}
                  onClick={() => toggle("facilities")}
                >
                  <span className="swatch" /> Industrial facilities
                  {layers && ` (${layers.facilities.features.length})`}
                </button>
                <button
                  className={`chip park ${visible.parks ? "on" : ""}`}
                  onClick={() => toggle("parks")}
                >
                  <span className="swatch" /> Green spaces
                  {layers && ` (${layers.green_spaces.features.length})`}
                </button>
              </div>

              <div className="map-wrap">
                <Map
                  initialViewState={{
                    longitude: data.location.lon,
                    latitude: data.location.lat,
                    zoom: 12.5,
                  }}
                  style={{ width: "100%", height: 440 }}
                  mapStyle="mapbox://styles/mapbox/light-v11"
                  mapboxAccessToken={MAPBOX_TOKEN}
                >
                  {/* Green spaces (drawn first, underneath) */}
                  {layers && visible.parks && (
                    <Source id="green" type="geojson" data={layers.green_spaces}>
                      <Layer {...greenFillLayer} />
                      <Layer {...greenLineLayer} />
                    </Source>
                  )}

                  {/* Air-quality heatmap */}
                  {layers && visible.air && (
                    <Source id="air" type="geojson" data={layers.air_quality}>
                      <Layer {...heatmapLayer} />
                    </Source>
                  )}

                  {/* Industrial facility markers */}
                  {layers && visible.facilities && (
                    <Source id="facilities" type="geojson" data={layers.facilities}>
                      <Layer {...facilitiesLayer} />
                    </Source>
                  )}

                  {/* Searched location pin */}
                  <Marker
                    longitude={data.location.lon}
                    latitude={data.location.lat}
                    color={dialColor}
                  />
                </Map>
              </div>

              {layers &&
                (layers.facilities.source === "mock" ||
                  layers.green_spaces.source === "mock") && (
                  <p className="map-note">
                    Some map layers show estimated locations while the EPA and
                    OpenStreetMap services are unavailable.
                  </p>
                )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
