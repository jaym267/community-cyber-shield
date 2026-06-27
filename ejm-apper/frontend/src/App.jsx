import { useState } from "react";
import axios from "axios";
import Map, { Marker } from "react-map-gl";
import "mapbox-gl/dist/mapbox-gl.css";

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN;

export default function App() {
  const [zip, setZip] = useState("");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const search = async () => {
    if (!zip || zip.length !== 5) return;
    setLoading(true);
    setError(null);
    try {
      const res = await axios.get(`http://127.0.0.1:8000/api/neighborhood/${zip}`);
      setData(res.data);
    } catch (e) {
      setError("Could not load data for that zip code. Try again.");
    }
    setLoading(false);
  };

  return (
    <div style={{ fontFamily: "sans-serif", maxWidth: 800, margin: "0 auto", padding: 24 }}>
      <h1 style={{ fontSize: 28, marginBottom: 4 }}>EJMapper</h1>
      <p style={{ color: "#666", marginBottom: 24 }}>
        Environmental justice by zip code
      </p>

      <div style={{ display: "flex", gap: 8, marginBottom: 24 }}>
        <input
          value={zip}
          onChange={e => setZip(e.target.value)}
          placeholder="Enter zip code"
          maxLength={5}
          style={{ padding: "10px 14px", fontSize: 16, border: "1px solid #ddd", borderRadius: 8, width: 200 }}
        />
        <button
          onClick={search}
          disabled={loading}
          style={{ padding: "10px 20px", fontSize: 16, background: "#2563eb", color: "white", border: "none", borderRadius: 8, cursor: "pointer" }}
        >
          {loading ? "Loading..." : "Search"}
        </button>
      </div>

      {error && <p style={{ color: "red" }}>{error}</p>}

      {data && (
        <div>
          <div style={{ background: "#f0fdf4", border: "1px solid #86efac", borderRadius: 12, padding: 20, marginBottom: 24 }}>
            <h2 style={{ marginBottom: 8 }}>Zip code {data.zip_code}</h2>
            <p style={{ fontSize: 32, fontWeight: "bold", margin: "8px 0" }}>
              Score: {data.report_card?.score ?? "—"}/100
            </p>
            <p style={{ fontSize: 20, marginBottom: 12 }}>
              Grade: {data.report_card?.grade ?? "—"}
            </p>
            <p style={{ color: "#444", lineHeight: 1.6 }}>{data.report_card?.summary}</p>
          </div>

          <div style={{ marginBottom: 24 }}>
            <h3 style={{ marginBottom: 12 }}>Key findings</h3>
            {data.report_card?.key_findings?.map((f, i) => (
              <div key={i} style={{ padding: "10px 14px", background: "#fafafa", border: "1px solid #eee", borderRadius: 8, marginBottom: 8 }}>
                {f}
              </div>
            ))}
          </div>

          <div style={{ marginBottom: 24 }}>
            <h3 style={{ marginBottom: 12 }}>What you can do</h3>
            {data.report_card?.action_items?.map((a, i) => (
              <div key={i} style={{ padding: "10px 14px", background: "#eff6ff", border: "1px solid #bfdbfe", borderRadius: 8, marginBottom: 8 }}>
                {a}
              </div>
            ))}
          </div>

          <Map
            initialViewState={{ longitude: data.location.lon, latitude: data.location.lat, zoom: 12 }}
            style={{ width: "100%", height: 400, borderRadius: 12 }}
            mapStyle="mapbox://styles/mapbox/streets-v12"
            mapboxAccessToken={MAPBOX_TOKEN}
          >
            <Marker longitude={data.location.lon} latitude={data.location.lat} color="red" />
          </Map>
        </div>
      )}
    </div>
  );
}