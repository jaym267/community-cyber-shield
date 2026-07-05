// Legend for the heat / canopy / vulnerability choropleths. Shows the active
// metric's color ramp with its real min/max, plus the honesty note for that
// data source (air temp not LST; static canopy estimate; composite score).
// Ramps must stay in sync with HEAT_RAMPS in App.jsx.

const META = {
  temp_f: {
    title: "Avg daily max air temp",
    unit: "°F",
    note: "NASA POWER air temperature (not surface temp), interpolated from a ~50 km grid.",
    fmt: (v) => `${v.toFixed(1)}°F`,
  },
  canopy_pct: {
    title: "Tree canopy",
    unit: "%",
    note: "NLCD 2021 zonal estimate — reads urban canopy conservatively vs. LiDAR.",
    fmt: (v) => `${v.toFixed(0)}%`,
  },
  vuln_score: {
    title: "Heat vulnerability",
    unit: "/100",
    note: "Composite of temperature, canopy, and Census demographics — scores compare zips within this region, not nationally.",
    fmt: (v) => `${Math.round(v)}`,
  },
};

export default function HeatLegend({ metric, stats, ramp, sources }) {
  const meta = META[metric];
  const s = stats?.[metric];
  if (!meta || !s) return null;
  const gradient = `linear-gradient(90deg, ${ramp.join(", ")})`;
  const mockBits = [];
  if (metric === "temp_f" && sources?.heat !== "live") mockBits.push("estimated data");
  if (metric === "vuln_score" && sources?.acs !== "live")
    mockBits.push("demographics estimated until a Census key is set");
  return (
    <div className="heat-legend">
      <span className="heat-legend-title">{meta.title}</span>
      <div className="heat-legend-bar" style={{ background: gradient }} />
      <div className="heat-legend-ends">
        <span>{meta.fmt(s.min)}</span>
        <span>{meta.fmt(s.max)}</span>
      </div>
      <p className="heat-legend-note">
        {meta.note}
        {mockBits.length > 0 && ` ⚠ ${mockBits.join("; ")}.`}
      </p>
    </div>
  );
}
