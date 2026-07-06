import { useEffect, useRef, useState } from "react";
import axios from "axios";
import Map, { Marker, Source, Layer, Popup } from "react-map-gl";
import "mapbox-gl/dist/mapbox-gl.css";
import "./App.css";
import HeatLegend from "./HeatLegend.jsx";

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN;
// API base: set VITE_API_BASE to the deployed backend URL in production;
// falls back to the local FastAPI dev server otherwise.
const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

// The 13 EJScreen v2.32 indicators, in display order. Each pairs the raw value
// key (from `environmental`) with its national-percentile key (from
// `percentiles`). `pct: true` means the raw value is a 0–1 fraction shown as a
// percentage. `desc` is the plain-language explainer shown when a card is
// expanded. Keys must match backend/ejscreen_api.py's field maps.
const INDICATORS = [
  { env: "pm25_avg_ugm3", pctl: "pm25_pctile_national", label: "Fine particles (PM2.5)", unit: "µg/m³",
    desc: "Microscopic particles from vehicles, industry, and smoke that lodge deep in the lungs. Long-term exposure is linked to heart and lung disease." },
  { env: "ozone_ppb", pctl: "ozone_pctile_national", label: "Ozone", unit: "ppb",
    desc: "A gas formed when sunlight reacts with vehicle and industrial emissions. High levels trigger asthma attacks and irritate airways." },
  { env: "no2_ppb", pctl: "no2_pctile_national", label: "Nitrogen dioxide (NO₂)", unit: "ppb",
    desc: "A traffic-related gas concentrated near busy roads. Strongly linked to childhood asthma and reduced lung function." },
  { env: "diesel_pm_ugm3", pctl: "diesel_pm_pctile_national", label: "Diesel exhaust", unit: "µg/m³",
    desc: "Exhaust particles from trucks, buses, and heavy equipment — a known carcinogen, concentrated near highways and freight routes." },
  { env: "toxic_releases_air", pctl: "toxic_releases_pctile_national", label: "Industrial air toxics", unit: "index",
    desc: "Toxicity-weighted industrial chemical releases to the air (EPA RSEI). Higher values mean more — and more dangerous — reported releases nearby." },
  { env: "traffic_proximity", pctl: "traffic_pctile_national", label: "Traffic proximity", unit: "index",
    desc: "A distance-weighted count of vehicles on nearby roads — how much high-volume traffic passes close to homes. A strong proxy for exhaust exposure and noise." },
  { env: "lead_paint_pct", pctl: "lead_paint_pctile_national", label: "Lead paint (pre-1960 homes)", unit: "", pct: true,
    desc: "Share of housing built before 1960, when lead paint was common. Lead exposure permanently affects children's development." },
  { env: "superfund_proximity", pctl: "superfund_pctile_national", label: "Superfund sites", unit: "per km²",
    desc: "Nearness to federally designated hazardous-waste cleanup sites on the National Priorities List." },
  { env: "rmp_facility_proximity", pctl: "rmp_pctile_national", label: "Risk-management facilities", unit: "per km²",
    desc: "Nearness to facilities that handle chemicals dangerous enough to require a federal risk-management plan." },
  { env: "hazwaste_proximity", pctl: "hazwaste_pctile_national", label: "Hazardous waste sites", unit: "per km²",
    desc: "Nearness to hazardous-waste treatment, storage, and disposal facilities." },
  { env: "underground_storage_tanks", pctl: "ust_pctile_national", label: "Underground storage tanks", unit: "per km²",
    desc: "Density of underground fuel and chemical tanks, which can leak into soil and groundwater over time." },
  { env: "wastewater_discharge", pctl: "wastewater_pctile_national", label: "Wastewater discharge", unit: "index",
    desc: "Toxicity-weighted industrial wastewater released into nearby streams and rivers." },
  { env: "drinking_water_noncompliance", pctl: "drinking_water_pctile_national", label: "Drinking water violations", unit: "index",
    desc: "How often local drinking water systems have violated federal safety standards. Higher values mean more frequent or serious non-compliance." },
];

const PROFILES = [
  { key: "general", label: "General" },
  { key: "children", label: "Parent / Child" },
  { key: "elderly", label: "Elderly" },
  { key: "respiratory", label: "Respiratory" },
];

const SAMPLE_ZIPS = [
  { zip: "78207", place: "San Antonio TX" },
  { zip: "90011", place: "Los Angeles CA" },
  { zip: "60623", place: "Chicago IL" },
  { zip: "11212", place: "Brooklyn NY" },
];

// Grade key — worst-to-best is A→F, rendered as a gradient scale bar with the
// current grade marked on it. `max` is the exclusive upper score bound for each
// letter and MUST match _grade_from_score in backend/main.py — the backend is
// the single source of truth for grades; these bands only pick display colors.
// Colors: watch-green → slate teal → brass alert → brick → true alarm-red —
// a beacon escalating from calm vigilance to crimson danger. No orange/purple/white.
const GRADE_KEY = [
  { letter: "A", max: 30,  color: "#2f8f52", bg: "#dcf1e1", label: "Clean", desc: "Minimal environmental burden" },
  { letter: "B", max: 50,  color: "#1d6b66", bg: "#dcece9", label: "Low", desc: "Below-average burden" },
  { letter: "C", max: 65,  color: "#a9791c", bg: "#f2e6c8", label: "Moderate", desc: "Around the national average" },
  { letter: "D", max: 80,  color: "#95401f", bg: "#ecdad2", label: "High", desc: "Above-average burden" },
  { letter: "F", max: 101, color: "#8a1620", bg: "#f1d9d5", label: "Severe", desc: "Among the most burdened areas" },
];

// Map a 0–100 burden value (higher = worse) to a severity color + background.
// Derived from GRADE_KEY's bands so a score's color can never contradict the
// letter grade shown beside it.
function severity(value) {
  if (value == null) return { color: "#807d6f", bg: "#e9e6da" };
  const band = GRADE_KEY.find((g) => value < g.max) ?? GRADE_KEY[GRADE_KEY.length - 1];
  return { color: band.color, bg: band.bg };
}

function fmt(value, isPct) {
  if (value == null) return "—";
  if (isPct) return `${Math.round(value * 100)}%`;
  return Number.isInteger(value) ? value.toString() : value.toFixed(2);
}

// US EPA Air Quality Index categories with plain-language guidance.
// Breakpoints per EPA's official AQI scale.
const AQI_BANDS = [
  { max: 51,  label: "Good", color: "#2f8f52",
    advice: "Air is clean right now — a good time to be outside." },
  { max: 101, label: "Moderate", color: "#1d6b66",
    advice: "Fine for most people. Unusually sensitive individuals should watch for symptoms." },
  { max: 151, label: "Unhealthy for sensitive groups", color: "#a9791c",
    advice: "Children, older adults, and people with asthma or heart disease should limit long outdoor exertion." },
  { max: 201, label: "Unhealthy", color: "#95401f",
    advice: "Everyone should reduce prolonged outdoor exertion; sensitive groups should stay indoors." },
  { max: 301, label: "Very unhealthy", color: "#8a1620",
    advice: "Avoid outdoor activity. Keep windows closed and run filtration if available." },
  { max: Infinity, label: "Hazardous", color: "#5c0e16",
    advice: "Health emergency conditions — everyone should remain indoors." },
];
function aqiBand(aqi) {
  if (aqi == null) return null;
  return AQI_BANDS.find((b) => aqi < b.max) ?? AQI_BANDS[AQI_BANDS.length - 1];
}

// ── Mapbox layer style definitions ──────────────────────────────────────────
const heatmapLayer = {
  id: "air-quality-heat",
  type: "heatmap",
  paint: {
    "heatmap-weight": ["get", "weight"],
    "heatmap-intensity": 1.1,
    "heatmap-radius": 34,
    "heatmap-opacity": 0.75,
    // Brass-beacon data-layer gradient — pale gold glow escalating to alarm-red.
    "heatmap-color": [
      "interpolate", ["linear"], ["heatmap-density"],
      0, "rgba(0,0,0,0)",
      0.2, "#e8d69a",
      0.4, "#cba33c",
      0.6, "#a9791c",
      0.8, "#95401f",
      1, "#5c0e16",
    ],
  },
};

const facilitiesLayer = {
  id: "facilities-circle",
  type: "circle",
  paint: {
    "circle-radius": 7,
    // Slate teal = regulated facility (calm, monitored); alarm-red = flagged
    // for a recent compliance violation.
    "circle-color": [
      "match", ["get", "type"],
      "Recent violation", "#8a1620",
      "#1d6b66",
    ],
    "circle-stroke-width": 2,
    "circle-stroke-color": "#f3efe4",
    "circle-opacity": 0.9,
  },
};

const greenFillLayer = {
  id: "green-fill",
  type: "fill",
  paint: { "fill-color": "#2f8f52", "fill-opacity": 0.25 },
};

const greenLineLayer = {
  id: "green-line",
  type: "line",
  paint: { "line-color": "#1d6b66", "line-width": 1.5 },
};

// ── Heat / canopy / vulnerability choropleths (San Antonio region) ──────────
// Monotonic-lightness ramps in the Watchtower palette (colorblind-friendly;
// no purple/white/orange). Must stay in sync with HeatLegend.jsx.
const HEAT_RAMPS = {
  temp_f: ["#e8d69a", "#cba33c", "#a9791c", "#95401f", "#8a1620"],
  canopy_pct: ["#e9e6da", "#a8d3b2", "#5fae77", "#2f8f52", "#1c5f37"],
  vuln_score: ["#e3ded0", "#c9a884", "#95401f", "#8a1620", "#5c0e16"],
};

// Fill paint for one metric, with stops built from the response's real
// min/max. Nulls (zips missing that metric) render neutral stone = "no data".
function heatFillPaint(metric, stats) {
  const s = stats?.[metric] ?? { min: 0, max: 1 };
  const span = s.max - s.min || 1;
  const colors = HEAT_RAMPS[metric];
  const stops = colors.flatMap((c, i) => [s.min + (span * i) / (colors.length - 1), c]);
  return {
    "fill-opacity": 0.6,
    "fill-color": [
      "case",
      ["==", ["coalesce", ["get", metric], -9999], -9999],
      "#e9e6da",
      ["interpolate", ["linear"], ["get", metric], ...stops],
    ],
  };
}

const heatOutlinePaint = {
  "line-color": "#1d4a48",
  "line-width": 0.8,
  "line-opacity": 0.45,
};

// The three choropleth toggles are mutually exclusive — stacking translucent
// choropleths reads as mud, so turning one on turns the others off.
const HEAT_TOGGLE_KEYS = ["heat", "canopy", "vuln"];
const METRIC_FOR_TOGGLE = { heat: "temp_f", canopy: "canopy_pct", vuln: "vuln_score" };

// Cooling-center markers (City of San Antonio program — SA-area only).
const coolingLayer = {
  id: "cooling-circle",
  type: "circle",
  paint: {
    "circle-radius": 6,
    "circle-color": "#133f3d",
    "circle-stroke-width": 2,
    "circle-stroke-color": "#f3efe4",
    "circle-opacity": 0.95,
  },
};

// Dashed alarm outline for high-vulnerability zips > 2 mi from any cooling
// center (drawn over the vulnerability choropleth when both layers are on).
const farZipLineLayer = (farZips) => ({
  id: "far-cooling-line",
  type: "line",
  filter: ["in", ["get", "zip"], ["literal", farZips]],
  paint: {
    "line-color": "#8a1620",
    "line-width": 2.2,
    "line-dasharray": [2, 1.6],
  },
});

function haversineMi(lat1, lon1, lat2, lon2) {
  const r = (d) => (d * Math.PI) / 180;
  const a =
    Math.sin(r(lat2 - lat1) / 2) ** 2 +
    Math.cos(r(lat1)) * Math.cos(r(lat2)) * Math.sin(r(lon2 - lon1) / 2) ** 2;
  return 3958.8 * 2 * Math.asin(Math.sqrt(a));
}

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

const prefersReducedMotion = () =>
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

// Animate a number from 0 to `target`. Instant under reduced motion or in a
// hidden tab (browsers suspend requestAnimationFrame there); a settle timer
// guarantees the final value lands even if animation frames never fire.
function useCountUp(target, duration = 700) {
  const [val, setVal] = useState(target);
  useEffect(() => {
    if (target == null) { setVal(null); return; }
    if (prefersReducedMotion() || document.hidden) { setVal(target); return; }
    let raf;
    const start = performance.now();
    const tick = (now) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setVal(t < 1 ? Math.round(target * eased) : target);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    const settle = setTimeout(() => setVal(target), duration + 200);
    return () => { cancelAnimationFrame(raf); clearTimeout(settle); };
  }, [target, duration]);
  return val;
}

// Recent searches live only in the visitor's own browser (localStorage).
const RECENTS_KEY = "ejmapper_recent_zips";
function loadRecents() {
  try {
    const raw = JSON.parse(localStorage.getItem(RECENTS_KEY) || "[]");
    return Array.isArray(raw) ? raw.filter((z) => /^\d{5}$/.test(z)).slice(0, 5) : [];
  } catch {
    return [];
  }
}
function saveRecent(zip) {
  try {
    const next = [zip, ...loadRecents().filter((z) => z !== zip)].slice(0, 5);
    localStorage.setItem(RECENTS_KEY, JSON.stringify(next));
    return next;
  } catch {
    return [zip];
  }
}

// Decorative topography for the landing hero: two nested-contour "hills"
// (concentric wobbly blobs, like elevation lines on a survey map) plus a few
// spot-elevation markers with typewriter labels.
function ContourBackground() {
  const blob =
    "M 0 -150 C 105 -160 175 -95 185 -10 C 195 80 130 150 25 162 C -80 174 -170 115 -180 15 C -190 -85 -105 -140 0 -150 Z";
  const hill = (cx, cy, baseScale, rings, keyPrefix) =>
    Array.from({ length: rings }).map((_, i) => (
      <path
        key={`${keyPrefix}-${i}`}
        d={blob}
        fill="none"
        stroke="var(--brand)"
        strokeWidth="1.1"
        opacity={0.055 + i * 0.012}
        transform={`translate(${cx} ${cy}) rotate(${i * 7}) scale(${baseScale * (1 - i * 0.14)})`}
      />
    ));
  return (
    <svg className="contours" viewBox="0 0 1200 800" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      {hill(150, 190, 1.5, 6, "a")}
      {hill(1060, 640, 2.1, 7, "b")}
      {hill(1010, 90, 0.8, 4, "c")}
      {[
        { x: 320, y: 560, label: "▲ 412" },
        { x: 880, y: 250, label: "▲ 1,208" },
        { x: 120, y: 700, label: "▲ 96" },
      ].map((m) => (
        <text
          key={m.label}
          x={m.x}
          y={m.y}
          fill="var(--brand)"
          opacity="0.22"
          fontSize="12"
          fontFamily="'Special Elite', monospace"
          letterSpacing="2"
        >
          {m.label}
        </text>
      ))}
    </svg>
  );
}

// Legal text shown in the footer modal. Plain-English, app-specific boilerplate —
// not a substitute for review by a lawyer before serious public launch.
const LEGAL_UPDATED = "June 2026";
const LEGAL = {
  disclaimer: {
    title: "Disclaimer",
    body: [
      "Sentinal is provided for general informational and educational purposes only. It is not professional advice of any kind — legal, medical, environmental, financial, or real-estate — and must not be relied upon for any decision about where to live, buy, rent, or invest, or about your health or safety.",
      "Environmental survey indicators come from the U.S. EPA's EJScreen dataset (v2.32), which EPA discontinued in February 2025 and which is now preserved and served by the Public Environmental Data Partners; it is a historical snapshot and is no longer updated by EPA. Live air quality and weather come from Open-Meteo, hazard alerts from the National Weather Service, and seismic data from USGS. When any service is unavailable, the app may display clearly-labeled estimated (\"mock\") data so the interface keeps working. Map place data comes from OpenStreetMap and may be incomplete or out of date.",
      "The \"report card,\" score, grade, and summaries are generated by an AI model (Claude, by Anthropic) interpreting that data and may contain errors, omissions, or misstatements. Always verify anything important against the official primary sources before acting on it.",
      "The service is provided \"as is\" and \"as available,\" without warranties of any kind, express or implied. To the fullest extent permitted by law, the operator is not liable for any loss or damage arising from use of, or reliance on, this site or its data.",
    ],
  },
  privacy: {
    title: "Privacy Policy",
    body: [
      "Sentinal does not require an account and does not ask for your name, email, or any personal information.",
      "When you search a ZIP code, that ZIP code is sent to our backend and to third-party data providers (OpenStreetMap's Nominatim geocoder and the U.S. EPA) to look up results. Map tiles are loaded from Mapbox, which may receive your IP address and basic usage data under Mapbox's own privacy policy.",
      "Your recent searches are saved only in your own browser's local storage so they can be offered as shortcuts; they are never transmitted to us and you can clear them at any time by clearing your browser data.",
      "We cache results by ZIP code to reduce cost and load. This cache stores environmental data only — it contains no personal information and is not linked to you.",
      "We do not use cookies, advertising, or third-party analytics/tracking. Our hosting providers (e.g. Vercel and Render) may automatically log standard technical request metadata such as IP address and timestamp for security and reliability, as described in their own privacy policies.",
      "Because no personal data is collected or stored by us, there is nothing for us to sell, share, or delete on request.",
    ],
  },
  terms: {
    title: "Terms of Use",
    body: [
      "By using Sentinal you agree to these terms. If you do not agree, do not use the site.",
      "The site and its data are provided \"as is\" and \"as available,\" with no warranties of any kind. We do not guarantee that the data is accurate, complete, current, or fit for any particular purpose.",
      "To the fullest extent permitted by law, the operator will not be liable for any direct, indirect, incidental, or consequential damages arising from your use of, or inability to use, the site or its data.",
      "You agree to use the site lawfully and not to abuse it — including not scraping, overloading, or attempting to disrupt or gain unauthorized access to the service or its providers.",
      "Data is provided by the U.S. EPA (EJScreen), © OpenStreetMap contributors (ODbL), and © Mapbox. Their respective terms also apply to that data.",
    ],
  },
};

export default function App() {
  const [zip, setZip] = useState("");
  const [data, setData] = useState(null);
  const [layers, setLayers] = useState(null);
  const [visible, setVisible] = useState({
    air: true, facilities: true, parks: true,
    heat: false, canopy: false, vuln: false,   // choropleths default off
    cooling: false,                            // SA cooling centers
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [hasSearched, setHasSearched] = useState(false);
  const [profile, setProfile] = useState("general");
  const [nearbyZips, setNearbyZips] = useState(null);
  const [nearbyLoading, setNearbyLoading] = useState(false);
  const [liveCond, setLiveCond] = useState(null);
  const [actionMsg, setActionMsg] = useState(null);
  const [heatLayers, setHeatLayers] = useState(null); // regional choropleth data
  const [zipPopup, setZipPopup] = useState(null);     // clicked choropleth zip
  const [coolingCenters, setCoolingCenters] = useState(null); // SA cooling sites
  const [coolPopup, setCoolPopup] = useState(null);   // clicked cooling center
  const [assistance, setAssistance] = useState(null); // FEMA history + help directory
  const [facilityPopup, setFacilityPopup] = useState(null);
  const [legalDoc, setLegalDoc] = useState(null);
  // New-feature state
  const [recents, setRecents] = useState(loadRecents);
  const [shareMsg, setShareMsg] = useState(null);
  const [pinned, setPinned] = useState(null);       // {zip, score, grade, percentiles}
  const [openInd, setOpenInd] = useState(null);     // env key of the expanded indicator
  const [mapCenter, setMapCenter] = useState(null); // survives loads so the map persists

  const mapRef = useRef(null);

  const toggle = (key) =>
    setVisible((v) => {
      const next = { ...v, [key]: !v[key] };
      if (HEAT_TOGGLE_KEYS.includes(key) && next[key]) {
        for (const k of HEAT_TOGGLE_KEYS) if (k !== key) next[k] = false;
      }
      return next;
    });

  const reset = () => {
    setZip("");
    setData(null);
    setLayers(null);
    setError(null);
    setHasSearched(false);
    setNearbyZips(null);
    setLiveCond(null);
    setFacilityPopup(null);
    setZipPopup(null);
    setCoolPopup(null);
    setAssistance(null);
    setPinned(null);
    setOpenInd(null);
    setMapCenter(null);
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
    setLiveCond(null);
    setFacilityPopup(null);
    setOpenInd(null);
    setHasSearched(true);
    // Reflect the searched zip in the URL so the link is shareable / bookmarkable.
    if (pushUrl && window.location.pathname !== `/${z}`) {
      window.history.pushState({ zip: z }, "", `/${z}`);
    }
    try {
      const res = await axios.get(`${API_BASE}/api/neighborhood/${z}`, {
        params: { profile: activeProfile },
      });
      setData(res.data);
      setMapCenter({ lat: res.data.location.lat, lon: res.data.location.lon });
      setRecents(saveRecent(z));
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
      // Live measured conditions (AQI, alerts, heat) — independent of EJScreen.
      axios
        .get(`${API_BASE}/api/live-conditions/${z}`)
        .then((r) => setLiveCond(r.data))
        .catch(() => setLiveCond(null));
      // Heat/canopy/vulnerability choropleths — built on demand for the
      // region (~12 mi) around any searched US zip. Can be slow on the first
      // search in a new region (runtime canopy computation); independent
      // .catch so failure only hides these layers.
      setZipPopup(null);
      setHeatLayers(null);
      axios
        .get(`${API_BASE}/api/heat-layers/${z}`)
        .then((r) => setHeatLayers(r.data))
        .catch(() => setHeatLayers(null));
      // Cooling centers (City of San Antonio program) — the chip only shows
      // when the searched location is actually near the centers.
      setCoolPopup(null);
      axios
        .get(`${API_BASE}/api/cooling-centers`)
        .then((r) => setCoolingCenters(r.data))
        .catch(() => setCoolingCenters(null));
      // County hazard history (FEMA declarations) + assistance directory.
      setAssistance(null);
      axios
        .get(`${API_BASE}/api/assistance/${z}`)
        .then((r) => setAssistance(r.data))
        .catch(() => setAssistance(null));
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

  // Glide the persistent map to each newly searched location.
  useEffect(() => {
    if (!data?.location || !mapRef.current) return;
    const center = [data.location.lon, data.location.lat];
    if (prefersReducedMotion()) {
      mapRef.current.jumpTo({ center, zoom: 12.5 });
    } else {
      mapRef.current.flyTo({ center, zoom: 12.5, duration: 1800, essential: true });
    }
  }, [data?.location?.lat, data?.location?.lon]);

  const share = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setShareMsg("Link copied");
    } catch {
      setShareMsg("Copy blocked — use the address bar");
    }
    setTimeout(() => setShareMsg(null), 2000);
  };

  const togglePin = () => {
    if (!data || !rc) return;
    if (pinned?.zip === data.zip_code) {
      setPinned(null);
    } else {
      setPinned({
        zip: data.zip_code,
        score: rc.score,
        grade: rc.grade,
        percentiles: data.percentiles,
      });
    }
  };

  // Build a ready-to-send letter to local officials from this report's actual
  // numbers (worst two indicators) and copy it to the clipboard.
  const copyLetter = async () => {
    if (!data) return;
    const worst = INDICATORS
      .map((ind) => ({ label: ind.label, pctl: data.percentiles?.[ind.pctl] }))
      .filter((x) => x.pctl != null)
      .sort((a, b) => b.pctl - a.pctl)
      .slice(0, 2);
    const bullets = worst
      .map((w) => `  • ${w.label}: worse than ${Math.round(w.pctl)}% of the United States`)
      .join("\n");
    const letter =
`Dear [Council member / Representative],

I am a resident of zip code ${data.zip_code}. According to EPA EJScreen data (v2.32), our neighborhood carries an environmental burden score of ${rc?.score ?? "—"} out of 100 (grade ${rc?.grade ?? "—"}), including:

${bullets}

I am asking you to: (1) tell residents what monitoring and enforcement is currently happening here, (2) support increased air and water monitoring in our area, and (3) prioritize our neighborhood for environmental remediation funding.

I would welcome a response describing concrete steps.

Respectfully,
[Your name]
[Your street address]`;
    try {
      await navigator.clipboard.writeText(letter);
      setActionMsg("Letter copied — paste it into an email");
    } catch {
      setActionMsg("Copy blocked by the browser");
    }
    setTimeout(() => setActionMsg(null), 3000);
  };

  const onKey = (e) => e.key === "Enter" && search();

  const rc = data?.report_card;
  const displayScore = useCountUp(rc?.score ?? null);
  const comparing = pinned && data && pinned.zip !== data.zip_code;

  // Heat choropleths are built per region around the searched zip — gate the
  // UI on the response actually covering this zip with drawable polygons.
  const inHeatRegion = !!(
    data &&
    heatLayers?.features?.length > 0 &&
    heatLayers?.region_zips?.includes(data.zip_code)
  );
  const activeHeatToggle = HEAT_TOGGLE_KEYS.find((k) => visible[k]) ?? null;
  const activeHeatMetric =
    inHeatRegion && activeHeatToggle ? METRIC_FOR_TOGGLE[activeHeatToggle] : null;

  // Cooling centers are a City of San Antonio program: show that UI only
  // when the searched location is within ~30 mi of at least one center.
  const coolFeatures = coolingCenters?.centers?.features ?? [];
  const nearCooling = !!(
    data?.location &&
    coolFeatures.some((f) =>
      haversineMi(
        data.location.lat, data.location.lon,
        f.geometry.coordinates[1], f.geometry.coordinates[0],
      ) < 30
    )
  );
  const farZipList = (coolingCenters?.far_zips ?? []).map((f) => f.zip);

  // Children's Health Index: computed ONLY when all three inputs exist — a
  // missing percentile must show as "no data", never silently default to 50
  // and masquerade as a real score.
  const childInputs = [
    data?.percentiles?.lead_paint_pctile_national,
    data?.percentiles?.traffic_pctile_national,
    data?.percentiles?.no2_pctile_national,
  ];
  const childScore =
    data && childInputs.every((v) => v != null)
      ? Math.round(childInputs[0] * 0.4 + childInputs[1] * 0.35 + childInputs[2] * 0.25)
      : null;
  const childSev = severity(childScore);
  // Color the score dial by the report's letter grade (falling back to the
  // score-based severity scale), so dial, pill, marker, and scale stay consistent.
  const gradeIndex = GRADE_KEY.findIndex((g) => g.letter === rc?.grade);
  const gradeMeta = gradeIndex >= 0 ? GRADE_KEY[gradeIndex] : null;
  const dialColor = gradeMeta?.color || severity(rc?.score).color;
  const dialBg = `color-mix(in srgb, ${dialColor} 12%, var(--surface))`;
  const gradeGradient = `linear-gradient(90deg, ${GRADE_KEY.map((g) => g.color).join(", ")})`;

  const searchBox = (big) => (
    <div className={big ? "search search-big" : "search search-compact"}>
      <input
        value={zip}
        onChange={(e) => setZip(e.target.value.replace(/\D/g, "").slice(0, 5))}
        onKeyDown={onKey}
        placeholder="Enter a 5-digit zip code"
        aria-label="Zip code"
        inputMode="numeric"
        maxLength={5}
      />
      <button onClick={() => search()} disabled={loading}>
        {loading ? "Loading…" : "Search"}
      </button>
    </div>
  );

  const footer = (
    <footer className="site-footer">
      <p className="footer-attrib">
        Environmental survey data: EPA EJScreen v2.32, preserved by the{" "}
        <a href="https://screening-tools.com/epa-ejscreen" target="_blank" rel="noopener noreferrer">Public Environmental Data Partners</a>{" "}
        (EPA discontinued EJScreen in 2025). Live air quality &amp; weather by{" "}
        <a href="https://open-meteo.com/" target="_blank" rel="noopener noreferrer">Open-Meteo.com</a>.
        Hazard alerts: National Weather Service. Seismic data: USGS. Heat layer: NASA POWER.
        Tree canopy: NLCD 2021 (USFS/MRLC). Demographics: US Census Bureau ACS.
        Cooling centers: City of San Antonio. Disaster history: FEMA OpenFEMA.
        County lookup: FCC Area API. Maps &amp; place data:{" "}
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
  );

  return (
    <div className={`app ${hasSearched ? "atlas-mode" : "landing-mode"}`}>
      {/* ── Landing ─────────────────────────────────────────────────────── */}
      {!hasSearched && (
        <div className="landing-hero">
          <ContourBackground />
          <div className="hero-inner">
            <h1 className="wordmark">Sentinal</h1>
            <p className="tagline">Environmental justice by zip code</p>
            <p className="hero-lead">
              Every neighborhood has an environmental story. Enter a zip code to
              see air quality, pollution burden, industrial sites, and green
              space — turned into a plain-language report card.
            </p>

            {searchBox(true)}
            {error && <div className="banner error hero-error">{error}</div>}

            <div className="zip-chips">
              <span className="zip-chips-label">Try</span>
              {SAMPLE_ZIPS.map((s) => (
                <button key={s.zip} className="zip-chip" onClick={() => search(s.zip)}>
                  <b>{s.zip}</b> {s.place}
                </button>
              ))}
            </div>
            {recents.length > 0 && (
              <div className="zip-chips recents">
                <span className="zip-chips-label">Recent</span>
                {recents.map((r) => (
                  <button key={r} className="zip-chip" onClick={() => search(r)}>
                    <b>{r}</b>
                  </button>
                ))}
              </div>
            )}

            <div className="landing-facts">
              <div className="landing-fact">
                <span className="fact-num">13</span>
                <span className="fact-label">Environmental indicators tracked</span>
              </div>
              <div className="landing-fact">
                <span className="fact-num">A–F</span>
                <span className="fact-label">Plain-language grade for every zip</span>
              </div>
              <div className="landing-fact">
                <span className="fact-num">LIVE</span>
                <span className="fact-label">Real-time air quality &amp; official hazard alerts</span>
              </div>
            </div>

            <p className="hero-disclaimer">
              Report cards are generated by Claude, an AI model by Anthropic, using
              data from the EPA EJScreen database. AI-generated summaries may contain
              errors — always verify critical information with official sources.
            </p>
          </div>
          {footer}
        </div>
      )}

      {/* ── Atlas: topbar + report pane + persistent map pane ───────────── */}
      {hasSearched && (
        <>
          <header className="topbar">
            <button type="button" className="topbar-logo" onClick={reset} title="Start over">
              Sentinal
            </button>
            {searchBox(false)}
            <div className="topbar-profiles" role="group" aria-label="Audience profile">
              {PROFILES.map((p) => (
                <button
                  key={p.key}
                  type="button"
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
          </header>

          <div className="atlas">
            {/* ── Left: scrolling report ─────────────────────────────────── */}
            <div className="report-pane">
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
                      {Array.from({ length: 6 }).map((_, i) => (
                        <div className="skel-ind" key={i}>
                          <div className="skel skel-line" style={{ width: "70%", marginBottom: 14 }} />
                          <div className="skel skel-line" style={{ width: "40%", height: 22, marginBottom: 14 }} />
                          <div className="skel" style={{ height: 5, borderRadius: 999, marginBottom: 10 }} />
                          <div className="skel skel-line" style={{ width: "60%" }} />
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {data && !loading && (
                <div className="report" key={`${data.zip_code}-${profile}`}>
                  {data.data_source === "mock" && (
                    <div className="banner mock">
                      Showing estimated data — the EJScreen data service is unreachable
                      right now. These numbers are plausible placeholders, not measurements.
                    </div>
                  )}

                  {/* Score hero */}
                  <div
                    className="score-card"
                    style={{ "--sev-color": dialColor, "--sev-bg": dialBg }}
                  >
                    <div className="score-dial">
                      <span className="num">{displayScore ?? "—"}</span>
                      <span className="out-of">out of 100</span>
                    </div>
                    <div className="score-body">
                      <div className="zip-line">
                        <h2>Zip code {data.zip_code}</h2>
                        {rc?.grade && <span className="grade-pill">Grade {rc.grade}</span>}
                      </div>
                      <p className="summary">{rc?.summary}</p>
                      <div className="score-actions">
                        <button type="button" className="mini-btn" onClick={share}>
                          {shareMsg ?? "Share this report"}
                        </button>
                        <button
                          type="button"
                          className={`mini-btn ${pinned?.zip === data.zip_code ? "on" : ""}`}
                          onClick={togglePin}
                        >
                          {pinned?.zip === data.zip_code ? "Unpin comparison" : "Pin to compare"}
                        </button>
                      </div>
                    </div>
                  </div>

                  {comparing && (
                    <div className="compare-strip">
                      <span>
                        Comparing with <b>{pinned.zip}</b> — score {pinned.score ?? "—"}, grade{" "}
                        {pinned.grade ?? "—"}. Thin bars below show {pinned.zip}.
                      </span>
                      <button type="button" onClick={() => setPinned(null)}>Unpin</button>
                    </div>
                  )}

                  {/* ── Live conditions — measured data, independent of EJScreen ── */}
                  {liveCond && (liveCond.air || liveCond.weather || liveCond.alerts) && (
                    <div className="live-panel">
                      <div className="live-head">
                        <span className="live-dot" aria-hidden="true" />
                        <span className="live-title">Right now in {data.zip_code}</span>
                        <span className="live-sub">live measured conditions</span>
                      </div>

                      {liveCond.alerts?.length > 0 && (
                        <div className="alerts">
                          {liveCond.alerts.map((a, i) => (
                            <div
                              key={i}
                              className={`alert sev-${(a.severity || "unknown").toLowerCase()}`}
                            >
                              <span className="alert-event">⚠ {a.event}</span>
                              {a.headline && <span className="alert-headline">{a.headline}</span>}
                              {a.instruction && <span className="alert-instr">{a.instruction}</span>}
                            </div>
                          ))}
                        </div>
                      )}

                      <div className="live-grid">
                        {liveCond.air?.us_aqi != null && (() => {
                          const band = aqiBand(liveCond.air.us_aqi);
                          return (
                            <div className="live-cell live-aqi" style={{ "--aqi-color": band.color }}>
                              <div className="live-aqi-top">
                                <span className="live-num">{Math.round(liveCond.air.us_aqi)}</span>
                                <div className="live-aqi-meta">
                                  <span className="live-cell-label">Air quality index</span>
                                  <span className="live-aqi-cat">{band.label}</span>
                                </div>
                              </div>
                              <p className="live-advice">{band.advice}</p>
                              <div className="live-pollutants">
                                {liveCond.air.pm2_5 != null && <span>PM2.5 {liveCond.air.pm2_5} µg/m³</span>}
                                {liveCond.air.ozone != null && <span>O₃ {liveCond.air.ozone} µg/m³</span>}
                                {liveCond.air.nitrogen_dioxide != null && <span>NO₂ {liveCond.air.nitrogen_dioxide} µg/m³</span>}
                              </div>
                            </div>
                          );
                        })()}
                        {liveCond.weather?.days?.length > 0 && (
                          <div className="live-cell">
                            <span className="live-cell-label">3-day outlook (NWS)</span>
                            <div className="live-days">
                              {liveCond.weather.days.map((d) => (
                                <div className="live-day" key={d.date}>
                                  <span className="live-day-date">
                                    {new Date(d.date + "T12:00:00").toLocaleDateString(undefined, { weekday: "short" })}
                                  </span>
                                  <span className="live-day-temp">
                                    {d.high_f != null ? `${Math.round(d.high_f)}°F` : "—"}
                                  </span>
                                  <span className="live-day-uv">{d.short || ""}</span>
                                </div>
                              ))}
                            </div>
                            {liveCond.weather.days.some((d) => d.high_f >= 95) && (
                              <p className="live-advice">High heat expected — check on elderly neighbors and limit midday exertion.</p>
                            )}
                          </div>
                        )}
                      </div>

                      {liveCond.alerts && liveCond.alerts.length === 0 && (
                        <p className="live-noalerts">
                          ✓ No active weather hazard alerts for this area right now.
                        </p>
                      )}
                      {liveCond.quakes?.count_30d > 0 && liveCond.quakes.strongest && (
                        <p className="live-quakes">
                          {liveCond.quakes.count_30d} earthquake{liveCond.quakes.count_30d > 1 ? "s" : ""} (M2.5+)
                          within 200 km in the past 30 days — strongest M{liveCond.quakes.strongest.mag}{" "}
                          {liveCond.quakes.strongest.place}.
                        </p>
                      )}
                      <p className="live-attrib">
                        Air &amp; weather by Open-Meteo.com · Alerts: National Weather Service · Seismic: USGS
                      </p>
                    </div>
                  )}

                  {/* Grade key — gradient scale with the current grade marked */}
                  <div className="grade-key">
                    <span className="grade-key-title">What the grade means</span>
                    <div className="grade-scale">
                      <div className="grade-scale-track" style={{ background: gradeGradient }}>
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
                  <div
                    className="child-index"
                    style={{ "--child-color": childSev.color, "--child-bg": childSev.bg }}
                  >
                    <div className="child-index-score">
                      <span className="child-num">{childScore ?? "—"}</span>
                      <span className="child-out">/ 100</span>
                    </div>
                    <div className="child-index-body">
                      <div className="child-index-title">Children's Health Index</div>
                      <p className="child-index-desc">
                        {childScore != null ? (
                          <>
                            This app's weighted score combining lead paint exposure (40%),
                            traffic pollution (35%), and nitrogen dioxide (25%) — three
                            indicators strongly associated with children's environmental
                            health. Higher = more risk.
                          </>
                        ) : (
                          <>Not enough data to compute this index for this area.</>
                        )}
                      </p>
                    </div>
                  </div>

                  {/* Indicator cards — click to expand a plain-language explainer */}
                  <div className="section">
                    <h3>Environmental indicators</h3>
                    <p className="section-hint">Select any measure to learn what it means.</p>
                    <div className="indicator-grid">
                      {INDICATORS.map((ind, i) => {
                        const raw = data.environmental?.[ind.env];
                        const pctl = data.percentiles?.[ind.pctl];
                        const pinPctl = comparing ? pinned.percentiles?.[ind.pctl] : null;
                        const s = severity(pctl);
                        const open = openInd === ind.env;
                        return (
                          <button
                            type="button"
                            className={`indicator ${open ? "open" : ""}`}
                            key={ind.env}
                            style={{ "--i": i }}
                            aria-expanded={open}
                            onClick={() => setOpenInd(open ? null : ind.env)}
                          >
                            <div className="label">{ind.label}</div>
                            <div className="value">
                              {fmt(raw, ind.pct)}
                              {ind.unit && <span className="unit">{ind.unit}</span>}
                            </div>
                            <div className="bar">
                              <span style={{ width: `${pctl ?? 0}%`, background: s.color }} />
                            </div>
                            {comparing && (
                              <div className="bar compare">
                                <span style={{ width: `${pinPctl ?? 0}%` }} />
                              </div>
                            )}
                            <div className="pctile">
                              {pctl != null ? (
                                <>Worse than <b>{Math.round(pctl)}%</b> of the US</>
                              ) : (
                                "No data"
                              )}
                              <span className="ind-more">{open ? "Less" : "More"}</span>
                            </div>
                            {open && <p className="ind-desc">{ind.desc}</p>}
                          </button>
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

                  {/* Take-action toolkit — turn the report into pressure */}
                  <div className="section">
                    <h3>Take action</h3>
                    <p className="section-hint">Turn this report into something officials have to answer.</p>
                    <div className="action-tools">
                      <button type="button" className="tool-btn primary" onClick={copyLetter}>
                        ✉ {actionMsg ?? "Copy a letter to your officials"}
                      </button>
                      <a
                        className="tool-btn"
                        href="https://echo.epa.gov/report-environmental-violations"
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        ⚑ Report a violation to EPA
                      </a>
                      <a
                        className="tool-btn"
                        href="https://www.usa.gov/elected-officials"
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        ☖ Find your elected officials
                      </a>
                      <button type="button" className="tool-btn" onClick={() => window.print()}>
                        ⎙ Print this report
                      </button>
                    </div>
                    <p className="action-note">
                      The letter is pre-filled with this report's actual numbers — paste it
                      into an email, add your name, and send. Print gives you a clean copy
                      to bring to a council or neighborhood meeting.
                    </p>
                  </div>

                  {/* Hazard history + assistance directory */}
                  {assistance && (
                    <div className="section assist-section">
                      <h3>
                        Hazard history &amp; getting help
                        {assistance.county?.county_name && (
                          <span className="assist-county"> — {assistance.county.county_name}</span>
                        )}
                      </h3>

                      {assistance.disasters === null ? (
                        <p className="map-note">
                          Federal disaster records are unavailable right now.
                        </p>
                      ) : assistance.disasters.length === 0 ? (
                        <p className="assist-none">
                          ✓ No federal disaster declarations on record for this county.
                        </p>
                      ) : (
                        <>
                          <p className="section-hint">
                            Federally declared disasters for this county (FEMA record) —
                            useful evidence when asking for local preparedness investment.
                          </p>
                          <div className="disaster-list">
                            {assistance.disasters.map((d) => (
                              <div className="disaster-row" key={d.fema_id}>
                                <span className="disaster-date">{d.declared}</span>
                                <span className="disaster-title">
                                  {d.title} <em>({d.type})</em>
                                </span>
                                <span className="disaster-badges">
                                  {d.recent && <span className="dbadge recent">recent</span>}
                                  {d.individual_assistance && (
                                    <span className="dbadge ia" title="FEMA opened aid applications for residents for this disaster">
                                      resident aid was available
                                    </span>
                                  )}
                                </span>
                              </div>
                            ))}
                          </div>
                        </>
                      )}

                      <h4 className="assist-subhead">Where to get help</h4>
                      <div className="resource-grid">
                        {assistance.resources.map((r) => (
                          <a
                            className="resource-card"
                            key={r.name}
                            href={r.url}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            <span className="resource-name">{r.name}</span>
                            <span className="resource-desc">{r.desc}</span>
                            {r.contact && <span className="resource-contact">{r.contact}</span>}
                          </a>
                        ))}
                      </div>
                      <p className="action-note">
                        Official programs only — every link goes to a government or
                        established nonprofit site. Disaster history: FEMA OpenFEMA.
                      </p>
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
                    {nearbyZips && nearbyZips.data_source && nearbyZips.data_source !== "live" && (
                      <p className="map-note">
                        ⚠ Nearby grades currently use estimated data — the EJScreen data
                        service is unreachable. They are directional, not measured.
                      </p>
                    )}
                  </div>

                  <p className="ai-note">
                    Summary and findings are AI-generated from EPA data and may contain
                    errors. Verify important details with official sources.
                  </p>
                </div>
              )}

              {footer}
            </div>

            {/* ── Right: persistent full-height map ──────────────────────── */}
            <div className="map-pane">
              {mapCenter ? (
                <>
                  <Map
                    ref={mapRef}
                    initialViewState={{
                      longitude: mapCenter.lon,
                      latitude: mapCenter.lat,
                      zoom: 12.5,
                    }}
                    style={{ width: "100%", height: "100%" }}
                    mapStyle="mapbox://styles/mapbox/light-v11"
                    mapboxAccessToken={MAPBOX_TOKEN}
                    interactiveLayerIds={[
                      ...(layers ? ["facilities-circle"] : []),
                      ...(heatLayers && activeHeatMetric ? ["heat-fill"] : []),
                      ...(nearCooling && visible.cooling ? ["cooling-circle"] : []),
                    ]}
                    onClick={(e) => {
                      const feature = e.features?.[0];
                      setFacilityPopup(null);
                      setZipPopup(null);
                      setCoolPopup(null);
                      if (feature?.layer?.id === "facilities-circle") {
                        setFacilityPopup({
                          lon: feature.geometry.coordinates[0],
                          lat: feature.geometry.coordinates[1],
                          name: feature.properties.name,
                          type: feature.properties.type,
                          source: feature.properties.source,
                        });
                      } else if (feature?.layer?.id === "cooling-circle") {
                        setCoolPopup({
                          lon: feature.geometry.coordinates[0],
                          lat: feature.geometry.coordinates[1],
                          ...feature.properties,
                        });
                      } else if (feature?.layer?.id === "heat-fill") {
                        setZipPopup({
                          lon: e.lngLat.lng,
                          lat: e.lngLat.lat,
                          ...feature.properties,
                        });
                      }
                    }}
                    cursor={facilityPopup || zipPopup || coolPopup ? "pointer" : ""}
                  >
                    {/* Choropleth first so it renders under markers/circles */}
                    {heatLayers && activeHeatMetric && (
                      <Source id="heat-zips" type="geojson" data={heatLayers}>
                        <Layer
                          id="heat-fill"
                          type="fill"
                          paint={heatFillPaint(activeHeatMetric, heatLayers.stats)}
                        />
                        <Layer id="heat-outline" type="line" paint={heatOutlinePaint} />
                        {nearCooling && visible.vuln && visible.cooling && farZipList.length > 0 && (
                          <Layer {...farZipLineLayer(farZipList)} />
                        )}
                      </Source>
                    )}
                    {nearCooling && visible.cooling && coolFeatures.length > 0 && (
                      <Source id="cooling" type="geojson" data={coolingCenters.centers}>
                        <Layer {...coolingLayer} />
                      </Source>
                    )}
                    {layers && visible.parks && (
                      <Source id="green" type="geojson" data={layers.green_spaces}>
                        <Layer {...greenFillLayer} />
                        <Layer {...greenLineLayer} />
                      </Source>
                    )}
                    {layers && visible.air && (
                      <Source id="air" type="geojson" data={layers.air_quality}>
                        <Layer {...heatmapLayer} />
                      </Source>
                    )}
                    {layers && visible.facilities && (
                      <Source id="facilities" type="geojson" data={layers.facilities}>
                        <Layer {...facilitiesLayer} />
                      </Source>
                    )}
                    <Marker longitude={mapCenter.lon} latitude={mapCenter.lat} color={dialColor} />
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
                          {facilityPopup.source === "mock" ? (
                            // Estimated markers are not real facilities — never link
                            // them to an EPA lookup that would come back empty.
                            <span className="facility-popup-note">
                              Estimated location shown while the EPA facility service is
                              unreachable — not a specific real facility.
                            </span>
                          ) : (
                            <a
                              className="facility-popup-link"
                              href={`https://echo.epa.gov/facilities/facility-search/results?p_fn=${encodeURIComponent(facilityPopup.name)}`}
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              View on EPA ECHO
                            </a>
                          )}
                        </div>
                      </Popup>
                    )}
                    {zipPopup && (
                      <Popup
                        longitude={zipPopup.lon}
                        latitude={zipPopup.lat}
                        onClose={() => setZipPopup(null)}
                        closeOnClick={false}
                        anchor="bottom"
                      >
                        <div className="facility-popup">
                          <strong className="facility-popup-name">Zip {zipPopup.zip}</strong>
                          <span className="zip-popup-row">
                            Avg daily high: <b>{zipPopup.temp_f != null ? `${zipPopup.temp_f}°F` : "—"}</b>
                          </span>
                          <span className="zip-popup-row">
                            Tree canopy: <b>{zipPopup.canopy_pct != null ? `${zipPopup.canopy_pct}%` : "—"}</b>
                          </span>
                          <span className="zip-popup-row">
                            Heat vulnerability: <b>{zipPopup.vuln_score ?? "—"}</b>/100
                          </span>
                          {zipPopup.zip !== data.zip_code && (
                            <button
                              type="button"
                              className="facility-popup-link zip-popup-btn"
                              onClick={() => { setZipPopup(null); search(zipPopup.zip); }}
                            >
                              Open this zip's report
                            </button>
                          )}
                        </div>
                      </Popup>
                    )}
                    {coolPopup && (
                      <Popup
                        longitude={coolPopup.lon}
                        latitude={coolPopup.lat}
                        onClose={() => setCoolPopup(null)}
                        closeOnClick={false}
                        anchor="bottom"
                      >
                        <div className="facility-popup">
                          <strong className="facility-popup-name">❄ {coolPopup.name}</strong>
                          <span className="facility-popup-status">{coolPopup.type}</span>
                          {coolPopup.address && (
                            <span className="zip-popup-row">{coolPopup.address}</span>
                          )}
                          {coolPopup.phone && (
                            <span className="zip-popup-row">{coolPopup.phone}</span>
                          )}
                          <span className="facility-popup-note">
                            Free public cooling site (City of San Antonio).
                          </span>
                        </div>
                      </Popup>
                    )}
                  </Map>

                  {/* Overlays */}
                  {data && rc?.grade && (
                    <div className="map-badge" style={{ "--badge-color": dialColor }}>
                      <span className="map-badge-zip">{data.zip_code}</span>
                      <span className="map-badge-grade">{rc.grade}</span>
                    </div>
                  )}
                  <div className="map-chips">
                    <button
                      className={`chip air ${visible.air ? "on" : ""}`}
                      onClick={() => toggle("air")}
                      title="A modeled gradient from the area-wide EPA percentile — not station measurements"
                    >
                      <span className="swatch" /> Air burden (modeled)
                    </button>
                    <button
                      className={`chip fac ${visible.facilities ? "on" : ""}`}
                      onClick={() => toggle("facilities")}
                    >
                      <span className="swatch" /> Facilities
                      {layers && ` (${layers.facilities.features.length})`}
                    </button>
                    <button
                      className={`chip park ${visible.parks ? "on" : ""}`}
                      onClick={() => toggle("parks")}
                    >
                      <span className="swatch" /> Green spaces
                      {layers && ` (${layers.green_spaces.features.length})`}
                    </button>
                    {inHeatRegion && (
                      <>
                        <button
                          className={`chip heat ${visible.heat ? "on" : ""}`}
                          onClick={() => toggle("heat")}
                          title="30-day avg daily max air temperature (NASA POWER, interpolated ~50km grid)"
                        >
                          <span className="swatch" /> Heat (air temp)
                        </button>
                        <button
                          className={`chip canopy ${visible.canopy ? "on" : ""}`}
                          onClick={() => toggle("canopy")}
                          title="Mean NLCD 2021 tree canopy per zip — static estimate"
                        >
                          <span className="swatch" /> Tree canopy (est.)
                        </button>
                        <button
                          className={`chip vuln ${visible.vuln ? "on" : ""}`}
                          onClick={() => toggle("vuln")}
                          title="Composite of temperature, canopy, and Census demographics"
                        >
                          <span className="swatch" /> Heat vulnerability
                        </button>
                      </>
                    )}
                    {nearCooling && (
                      <button
                        className={`chip cool ${visible.cooling ? "on" : ""}`}
                        onClick={() => toggle("cooling")}
                        title="Free public cooling sites (City of San Antonio). Turn on with Heat vulnerability to outline high-risk zips far from any center."
                      >
                        <span className="swatch" /> Cooling centers
                        {coolFeatures.length > 0 && ` (${coolFeatures.length})`}
                      </button>
                    )}
                  </div>
                  {nearCooling && visible.vuln && visible.cooling && farZipList.length > 0 && (
                    <p className="far-cooling-note">
                      ⚠ Dashed outline = high-vulnerability zip whose center is
                      more than {coolingCenters.threshold_mi} mi from any cooling
                      center (approximate, centroid-based).
                    </p>
                  )}
                  {activeHeatMetric && heatLayers && (
                    <HeatLegend
                      metric={activeHeatMetric}
                      stats={heatLayers.stats}
                      ramp={HEAT_RAMPS[activeHeatMetric]}
                      sources={heatLayers.sources}
                    />
                  )}
                  {layers &&
                    (layers.facilities.source === "mock" ||
                      layers.green_spaces.source === "mock") && (
                      <p className="map-mock-note">
                        Some layers show estimated locations while EPA / OpenStreetMap
                        services are unavailable.
                      </p>
                    )}
                </>
              ) : (
                <div className="map-skeleton">
                  <div className="skel" style={{ width: "100%", height: "100%", borderRadius: 0 }} />
                </div>
              )}
            </div>
          </div>
        </>
      )}

      {/* ── Legal modal ─────────────────────────────────────────────────── */}
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
