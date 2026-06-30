import { useState, useEffect } from "react";
import axios from "axios";
import Map, { Marker, Source, Layer, Popup } from "react-map-gl";
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

// Legal text shown in the footer modal. Plain-English, app-specific boilerplate —
// not a substitute for review by a lawyer before serious public launch.
const LEGAL_UPDATED = "June 2026";
const LEGAL = {
  disclaimer: {
    title: "Disclaimer",
    body: [
      "EJMapper is provided for general informational and educational purposes only. It is not professional advice of any kind — legal, medical, environmental, financial, or real-estate — and must not be relied upon for any decision about where to live, buy, rent, or invest, or about your health or safety.",
      "Environmental indicators come from the U.S. EPA's EJScreen dataset and other public sources. When those services are unavailable, the app may display clearly-labeled estimated (\"mock\") data so the interface keeps working. Map place data comes from OpenStreetMap and may be incomplete or out of date.",
      "The \"report card,\" score, grade, and summaries are generated by an AI model (Claude, by Anthropic) interpreting that data and may contain errors, omissions, or misstatements. Always verify anything important against the official primary sources before acting on it.",
      "The service is provided \"as is\" and \"as available,\" without warranties of any kind, express or implied. To the fullest extent permitted by law, the operator is not liable for any loss or damage arising from use of, or reliance on, this site or its data.",
    ],
  },
  privacy: {
    title: "Privacy Policy",
    body: [
      "EJMapper does not require an account and does not ask for your name, email, or any personal information.",
      "When you search a ZIP code, that ZIP code is sent to our backend and to third-party data providers (OpenStreetMap's Nominatim geocoder and the U.S. EPA) to look up results. Map tiles are loaded from Mapbox, which may receive your IP address and basic usage data under Mapbox's own privacy policy.",
      "We cache results by ZIP code to reduce cost and load. This cache stores environmental data only — it contains no personal information and is not linked to you.",
      "We do not use cookies, advertising, or third-party analytics/tracking. Our hosting providers (e.g. Vercel and Render) may automatically log standard technical request metadata such as IP address and timestamp for security and reliability, as described in their own privacy policies.",
      "Because no personal data is collected or stored, there is nothing for us to sell, share, or delete on request.",
    ],
  },
  terms: {
    title: "Terms of Use",
    body: [
      "By using EJMapper you agree to these terms. If you do not agree, do not use the site.",
      "The site and its data are provided \"as is\" and \"as available,\" with no warranties of any kind. We do not guarantee that the data is accurate, complete, current, or fit for any particular purpose.",
      "To the fullest extent permitted by law, the operator will not be liable for any direct, indirect, incidental, or consequential damages arising from your use of, or inability to use, the site or its data.",
      "You agree to use the site lawfully and not to abuse it — including not scraping, overloading, or attempting to disrupt or gain unauthorized access to the service or its providers.",
      "Data is provided by the U.S. EPA (EJScreen), © OpenStreetMap contributors (ODbL), and © Mapbox. Their respective terms also apply to that data.",
    ],
  },
};

export default function App() {
  const [zip, setZip] = useState("");
  const [legalDoc, setLegalDoc] = useState(null);
  const [data, setData] = useState(null);
  const [layers, setLayers] = useState(null);
  const [visible, setVisible] = useState({ air: true, facilities: true, parks: true });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [hasSearched, setHasSearched] = useState(false);
  const [profile, setProfile] = useState("general");
  const [nearbyZips, setNearbyZips] = useState(null);
  const [nearbyLoading, setNearbyLoading] = useState(false);
  const [facilityPopup, setFacilityPopup] = useState(null);

  const toggle = (key) => setVisible((v) => ({ ...v, [key]: !v[key] }));

  const reset = () => {
    setZip("");
    setData(null);
    setLayers(null);
    setError(null);
    setHasSearched(false);
    setNearbyZips(null);
    setFacilityPopup(null);
    window.history.pushState({}, "", "/");
  };

  const search = async (zipArg, { pushUrl = true, profileOverride } = {}) => {
    const z = (zipArg ?? zip).toString();
    if (!z || z.length !== 5) {
      setError("Please enter a 5-digit zip code.");
      return;
    }
    const activeProfile = profileOverride ?? profile;
    setZip(z);
    setLoading(true);
    setError(null);
    setData(null);
    setLayers(null);
    setNearbyZips(null);
    setFacilityPopup(null);
    setHasSearched(true);
    // Reflect the searched zip in the URL so the link is shareable / bookmarkable.
    if (pushUrl && window.location.pathname !== `/${z}`) {
      window.history.pushState({ zip: z }, "", `/${z}`);
    }
    try {
      const res = await axios.get(`${API_BASE}/api/neighborhood/${z}`, { params: { profile: activeProfile } });
      setData(res.data);
      // Fetch map overlays + nearby zips in background — don't block the report card.
      const intensity = airIntensity(res.data.percentiles);
      axios
        .get(`${API_BASE}/api/map-layers/${z}`, { params: { intensity } })
        .then((r) => setLayers(r.data))
        .catch(() => setLayers(null));
      setNearbyLoading(true);
      axios
        .get(`${API_BASE}/api/nearby-zips/${z}`)
        .then((r) => setNearbyZips(r.data))
        .catch(() => setNearbyZips(null))
        .finally(() => setNearbyLoading(false));
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

  // Close the legal modal on Escape.
  useEffect(() => {
    if (!legalDoc) return;
    const onEsc = (e) => e.key === "Escape" && setLegalDoc(null);
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [legalDoc]);

  const onKey = (e) => e.key === "Enter" && search();

  const rc = data?.report_card;

  const childScore = data ? Math.round(
    (data.percentiles?.lead_paint_pctile_national ?? 50) * 0.4 +
    (data.percentiles?.traffic_pctile_national ?? 50) * 0.35 +
    (data.percentiles?.cancer_risk_pctile_national ?? 50) * 0.25
  ) : null;
  const childSev = severity(childScore);
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
        <h1 className="logo" onClick={reset} title="Start a new search">EJMapper</h1>
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

      {/* Profile selector — always visible */}
      <div className="profile-bar">
        <span className="profile-label">Viewing as:</span>
        {[
          { key: "general",     label: "General" },
          { key: "children",    label: "Parent / Child" },
          { key: "elderly",     label: "Elderly" },
          { key: "respiratory", label: "Respiratory" },
        ].map((p) => (
          <button
            key={p.key}
            className={`profile-btn ${profile === p.key ? "active" : ""}`}
            onClick={() => {
              setProfile(p.key);
              if (data && zip) search(zip, { pushUrl: false, profileOverride: p.key });
            }}
          >
            {p.label}
          </button>
        ))}
      </div>

      {!hasSearched && (
        <div className="landing">
          <p className="landing-lead">
            Find out what environmental hazards exist in your neighborhood.
          </p>
          <p className="landing-body">
            EJMapper pulls data directly from the EPA's EJScreen database and uses AI to turn
            it into a plain-language report card — no scientific background required. Enter any
            US zip code to see air quality, pollution levels, proximity to industrial sites,
            and how your area compares to the rest of the country.
          </p>
          <div className="landing-facts">
            <div className="landing-fact">
              <span className="fact-num">12</span>
              <span className="fact-label">Environmental indicators tracked</span>
            </div>
            <div className="landing-fact">
              <span className="fact-num">A–F</span>
              <span className="fact-label">Plain-language grade for every zip</span>
            </div>
            <div className="landing-fact">
              <span className="fact-num">EPA</span>
              <span className="fact-label">Data sourced from EJScreen</span>
            </div>
          </div>
        </div>
      )}

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

          {/* Children's Health Index */}
          <div className="child-index" style={{ "--child-color": childSev.color, "--child-bg": childSev.bg }}>
            <div className="child-index-score">
              <span className="child-num">{childScore ?? "—"}</span>
              <span className="child-out">/ 100</span>
            </div>
            <div className="child-index-body">
              <div className="child-index-title">Children's Health Index</div>
              <p className="child-index-desc">
                Weighted score combining lead paint exposure (40%), traffic pollution (35%), and air toxics cancer risk (25%) — the three indicators most linked to childhood health outcomes. Higher = more risk.
              </p>
            </div>
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
                  interactiveLayerIds={layers ? ["facilities-circle"] : []}
                  onClick={(e) => {
                    const feature = e.features?.[0];
                    if (feature?.layer?.id === "facilities-circle") {
                      setFacilityPopup({
                        lon: feature.geometry.coordinates[0],
                        lat: feature.geometry.coordinates[1],
                        name: feature.properties.name,
                        type: feature.properties.type,
                      });
                    } else {
                      setFacilityPopup(null);
                    }
                  }}
                  cursor={facilityPopup ? "pointer" : ""}
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

                  {/* Facility popup */}
                  {facilityPopup && (
                    <Popup
                      longitude={facilityPopup.lon}
                      latitude={facilityPopup.lat}
                      onClose={() => setFacilityPopup(null)}
                      closeOnClick={false}
                      anchor="bottom"
                    >
                      <div className="facility-popup">
                        <strong className="facility-popup-name">{facilityPopup.name}</strong>
                        <span className={`facility-popup-status ${facilityPopup.type === "Recent violation" ? "violation" : ""}`}>
                          {facilityPopup.type}
                        </span>
                        <a
                          className="facility-popup-link"
                          href={`https://echo.epa.gov/facilities/facility-search/results?p_fn=${encodeURIComponent(facilityPopup.name)}`}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          View on EPA ECHO
                        </a>
                      </div>
                    </Popup>
                  )}
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
          {/* Nearby zip comparison */}
          <div className="section">
            <h3>How does this area compare nearby?</h3>
            {nearbyLoading && (
              <div className="nearby-loading">
                <div className="skel skel-line" style={{ width: "60%", marginBottom: 10 }} />
                <div className="skel skel-line" style={{ width: "80%", marginBottom: 10 }} />
                <div className="skel skel-line" style={{ width: "50%" }} />
              </div>
            )}
            {nearbyZips && nearbyZips.zips.length > 0 && (
              <div className="nearby-grid">
                {nearbyZips.zips.map((nz) => {
                  const s = severity(nz.score);
                  const isCurrent = nz.zip === data.zip_code;
                  return (
                    <button
                      key={nz.zip}
                      className={`nearby-card ${isCurrent ? "current" : ""}`}
                      style={{ "--nz-color": s.color, "--nz-bg": s.bg }}
                      onClick={() => !isCurrent && search(nz.zip)}
                    >
                      <span className="nearby-zip">{nz.zip}</span>
                      <span className="nearby-grade">{nz.grade}</span>
                      <span className="nearby-score">{nz.score}</span>
                      {isCurrent && <span className="nearby-current-tag">current</span>}
                    </button>
                  );
                })}
              </div>
            )}
            {nearbyZips && nearbyZips.zips.length === 0 && (
              <p className="map-note">No nearby zip codes found for comparison.</p>
            )}
          </div>
        </div>
      )}

      <footer className="site-footer">
        <p className="footer-attrib">
          Environmental data: U.S. EPA EJScreen. Maps &amp; place data:{" "}
          <a href="https://www.mapbox.com/about/maps/" target="_blank" rel="noopener noreferrer">© Mapbox</a>,{" "}
          <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">© OpenStreetMap contributors</a>.
          Geocoding by OpenStreetMap Nominatim.
        </p>
        <p className="footer-legal">
          <button type="button" onClick={() => setLegalDoc("disclaimer")}>Disclaimer</button>
          <span aria-hidden="true">·</span>
          <button type="button" onClick={() => setLegalDoc("privacy")}>Privacy</button>
          <span aria-hidden="true">·</span>
          <button type="button" onClick={() => setLegalDoc("terms")}>Terms</button>
        </p>
        <p className="footer-note">
          For informational use only — not professional advice. Verify critical information with official sources.
        </p>
      </footer>

      {legalDoc && (
        <div
          className="legal-overlay"
          role="dialog"
          aria-modal="true"
          aria-label={LEGAL[legalDoc].title}
          onClick={() => setLegalDoc(null)}
        >
          <div className="legal-modal" onClick={(e) => e.stopPropagation()}>
            <div className="legal-modal-head">
              <h2>{LEGAL[legalDoc].title}</h2>
              <button
                type="button"
                className="legal-close"
                aria-label="Close"
                onClick={() => setLegalDoc(null)}
              >
                ×
              </button>
            </div>
            {LEGAL[legalDoc].body.map((para, i) => (
              <p key={i}>{para}</p>
            ))}
            <p className="legal-updated">Last updated: {LEGAL_UPDATED}</p>
          </div>
        </div>
      )}
    </div>
  );
}
