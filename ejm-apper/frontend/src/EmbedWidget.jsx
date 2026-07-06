// Sentinal embeddable badge — a compact, self-contained score card a city
// or partner can drop onto their own page via an <iframe>:
//
//   <iframe src="https://sentinal.app/embed/78207"
//           width="340" height="240" style="border:0" title="Sentinal"></iframe>
//
// Path: /embed/{zip}?lang=es (optional). It reuses the same /api/neighborhood
// endpoint, renders only the score + grade + top concerns + a deep link back
// to the full report, and posts its height to the parent so the iframe can
// auto-size. No map, no controls — read-only and dependency-light.

import { useEffect, useState } from "react";
import axios from "axios";
import { makeT } from "./i18n.js";
import "./embed.css";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

const GRADE_COLORS = {
  A: "#2f8f52", B: "#1d6b66", C: "#a9791c", D: "#95401f", F: "#8a1620",
};

export default function EmbedWidget({ zip, lang = "en" }) {
  const t = makeT(lang);
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!/^\d{5}$/.test(zip)) { setError(true); return; }
    axios
      .get(`${API_BASE}/api/neighborhood/${zip}`, { params: { lang } })
      .then((r) => setData(r.data))
      .catch(() => setError(true));
  }, [zip, lang]);

  // This route renders inside a host iframe: strip the default body margin and
  // let the host background show through. Done imperatively (not in embed.css)
  // so the rule can't leak into the full app, which shares the same bundle.
  useEffect(() => {
    const prev = { margin: document.body.style.margin, background: document.body.style.background };
    document.body.style.margin = "0";
    document.body.style.background = "transparent";
    return () => { document.body.style.margin = prev.margin; document.body.style.background = prev.background; };
  }, []);

  // Report our rendered height to the embedding page for auto-resize.
  useEffect(() => {
    const post = () =>
      window.parent?.postMessage(
        { type: "sentinal-embed-height", height: document.body.scrollHeight },
        "*",
      );
    post();
    const ro = new ResizeObserver(post);
    ro.observe(document.body);
    return () => ro.disconnect();
  }, [data, error]);

  const fullUrl = `${window.location.origin}/${zip}${lang === "es" ? "?lang=es" : ""}`;

  if (error) {
    return (
      <div className="embed-card embed-error">
        <span>Sentinal — {t("invalidZip")}</span>
      </div>
    );
  }
  if (!data) {
    return <div className="embed-card embed-loading">Sentinal…</div>;
  }

  const rc = data.report_card || {};
  const grade = rc.grade;
  const color = GRADE_COLORS[grade] || "#1d6b66";

  return (
    <a
      className="embed-card"
      href={fullUrl}
      target="_blank"
      rel="noopener noreferrer"
      style={{ "--grade-color": color }}
    >
      <div className="embed-head">
        <span className="embed-brand">SENTINAL</span>
        <span className="embed-zip">{t("zipCode")} {data.zip_code}</span>
      </div>
      <div className="embed-body">
        <div className="embed-score">
          <span className="embed-grade">{grade || "—"}</span>
          <span className="embed-num">{rc.score ?? "—"}<i>/100</i></span>
        </div>
        <div className="embed-detail">
          <p className="embed-summary">{rc.summary}</p>
        </div>
      </div>
      <div className="embed-foot">
        {lang === "es" ? "Ver reporte completo" : "View full report"} →
      </div>
    </a>
  );
}
