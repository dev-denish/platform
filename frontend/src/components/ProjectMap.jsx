import { useMemo } from "react";
import { MapContainer, TileLayer, Rectangle, Tooltip } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { DATASET_TYPE_COLORS } from "../lib/colors.js";

export default function ProjectMap({ layers }) {
  const bounds = useMemo(() => {
    const all = layers.flatMap((l) => l.bounds);
    if (all.length === 0) return null;
    const lats = all.map((p) => p[0]);
    const lngs = all.map((p) => p[1]);
    return [
      [Math.min(...lats), Math.min(...lngs)],
      [Math.max(...lats), Math.max(...lngs)],
    ];
  }, [layers]);

  if (!bounds) return null;

  return (
    <div className="map-frame">
      <MapContainer bounds={bounds} boundsOptions={{ padding: [24, 24] }} scrollWheelZoom={false} className="map-root">
        <TileLayer
          attribution='&copy; <a href="https://carto.com/attributions">CARTO</a> &copy; OpenStreetMap contributors'
          url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
        />
        {layers.map((l) => (
          <Rectangle
            key={l.layer_id}
            bounds={l.bounds}
            pathOptions={{
              color: DATASET_TYPE_COLORS[l.type] ?? "#0B6B46",
              weight: 2,
              fillOpacity: 0.18,
            }}
          >
            <Tooltip sticky>
              {l.type} · {l.date_processed ?? "undated"}
            </Tooltip>
          </Rectangle>
        ))}
      </MapContainer>
    </div>
  );
}
