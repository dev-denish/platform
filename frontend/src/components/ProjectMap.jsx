import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  MapContainer,
  TileLayer,
  ImageOverlay,
  Rectangle,
  Polyline,
  Polygon,
  CircleMarker,
  Popup,
  Tooltip,
  useMap,
  useMapEvents,
} from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { apiFetch } from "../config.js";
import { DATASET_TYPE_COLORS } from "../lib/colors.js";
import { BASEMAP_URL, BASEMAP_ATTRIBUTION, CARTO_BASEMAP_URL, CARTO_BASEMAP_ATTRIBUTION } from "../lib/basemap.js";
import { initSymbologyState, buildTileUrl, legendEntryLabel, legendEntryColor } from "../lib/symbology.js";
import { lineDistanceMeters, polygonAreaHectares } from "../lib/measure.js";
import { formatNumber, formatHectares } from "../lib/format.js";
import LayersPanel from "./LayersPanel.jsx";
import MeasureTools from "./MeasureTools.jsx";
import BasemapToggle from "./BasemapToggle.jsx";
import FullscreenToggle from "./FullscreenToggle.jsx";
import ErrorBanner from "./ErrorBanner.jsx";

/**
 * scrollWheelZoom starts disabled on the MapContainer (see below) so scrolling
 * PAST this map on the page doesn't get hijacked into a zoom - this component
 * enables it only while the map actually has the user's attention (clicked or
 * keyboard-focused) and disables it again the moment that attention leaves
 * (mouse-out or blur), so the hijack risk never comes back. A small hint
 * fades out permanently after the first real interaction; dragging and the
 * +/- zoom control are untouched by any of this - only wheel-zoom needed the
 * click-to-activate gate.
 *
 * Bugfix: the first version wired click/mouseleave via raw
 * `container.addEventListener(...)`, bypassing Leaflet's own event dispatch
 * entirely - a plain DOM listener glued on independently of how Leaflet
 * itself interprets mouse activity on that same container. It never visibly
 * worked. Every OTHER interactive behavior on this map (e.g. the Rectangle
 * `Tooltip`s below, which position themselves via Leaflet's own
 * mouseover/mousemove events) goes through Leaflet's event system and does
 * work - so click/hover here now goes through the same system, via
 * react-leaflet's `useMapEvents` (-> `map.on(...)`), instead of a parallel,
 * unproven path. Focus/blur have no Leaflet-level equivalent, so those stay
 * as plain DOM listeners on `map.getContainer()`.
 */
function ScrollZoomOnActivate() {
  const map = useMap();
  const [interacted, setInteracted] = useState(false);

  const activate = useCallback(() => {
    map.scrollWheelZoom.enable();
    setInteracted(true);
  }, [map]);
  const deactivate = useCallback(() => {
    map.scrollWheelZoom.disable();
  }, [map]);

  const mapEventHandlers = useMemo(
    () => ({ click: activate, mouseover: activate, mouseout: deactivate }),
    [activate, deactivate]
  );
  useMapEvents(mapEventHandlers);

  useEffect(() => {
    map.scrollWheelZoom.disable();
    const container = map.getContainer();
    container.addEventListener("focus", activate);
    container.addEventListener("blur", deactivate);
    return () => {
      container.removeEventListener("focus", activate);
      container.removeEventListener("blur", deactivate);
    };
  }, [map, activate, deactivate]);

  return (
    <div className={`map-scroll-hint${interacted ? " map-scroll-hint-faded" : ""}`}>
      Click the map to enable scroll-to-zoom
    </div>
  );
}

/**
 * Live lat/lon readout in the map's corner (Phase 3 Wave D). EPSG:4326 always
 * - that's the storage/display CRS everywhere else in this app (see
 * LayerOut.crs) - a metric reprojection only ever happens internally, inside
 * the measure-tool math (lib/measure.js), never for what's shown here.
 */
function CoordinateReadout() {
  const [pos, setPos] = useState(null);
  useMapEvents({
    mousemove(e) {
      setPos(e.latlng);
    },
    mouseout() {
      setPos(null);
    },
  });
  if (!pos) return null;
  return (
    <div className="coord-readout">
      {pos.lat.toFixed(5)}, {pos.lng.toFixed(5)} <span className="coord-readout-crs">EPSG:4326</span>
    </div>
  );
}

/**
 * Routes a plain map click to exactly one behavior depending on the current
 * tool (see MeasureTools.jsx) - pixel inspection by default, or adding a
 * vertex to whichever shape is being measured. A separate component (rather
 * than folding this into ScrollZoomOnActivate's own click handler) since
 * react-leaflet's useMapEvents just registers another independent listener
 * on the same Leaflet map - no conflict with that component's own click
 * handling, which still runs too.
 */
function MapClickRouter({ mode, onInspect, onMeasurePoint }) {
  useMapEvents({
    click(e) {
      if (mode === "inspect") onInspect(e.latlng);
      else onMeasurePoint(e.latlng);
    },
  });
  return null;
}

const MEASURE_COLOR = "#e0692f";

/** The in-progress polyline/polygon + vertex markers for whichever measure
 * tool is active - purely visual, the actual distance/area math lives in
 * lib/measure.js and is computed by the parent from the same `points`. */
function MeasureDrawing({ mode, points }) {
  if (mode === "inspect" || points.length === 0) return null;
  return (
    <>
      {mode === "area" && points.length >= 3 ? (
        <Polygon positions={points} pathOptions={{ color: MEASURE_COLOR, weight: 2, fillOpacity: 0.15 }} />
      ) : (
        <Polyline positions={points} pathOptions={{ color: MEASURE_COLOR, weight: 3 }} />
      )}
      {points.map((p, i) => (
        <CircleMarker
          key={i}
          center={p}
          radius={4}
          pathOptions={{ color: MEASURE_COLOR, weight: 2, fillColor: "#fff", fillOpacity: 1 }}
        />
      ))}
    </>
  );
}

/**
 * Leaflet caches the map container's pixel size at load time and never
 * re-measures it on its own - a real DOM resize (like the Fullscreen API
 * expanding `.map-frame` to the whole viewport) leaves the tile grid stuck
 * rendering at the OLD size until something tells Leaflet to re-check. This
 * listens for the same native `fullscreenchange` event the outside toggle
 * button reacts to and calls `map.invalidateSize()` - a small delay because
 * the browser fires that event right as it applies the new dimensions, and
 * measuring one tick too early can still read the pre-resize size.
 */
function FullscreenInvalidate() {
  const map = useMap();
  useEffect(() => {
    function handleChange() {
      setTimeout(() => map.invalidateSize(), 100);
    }
    document.addEventListener("fullscreenchange", handleChange);
    return () => document.removeEventListener("fullscreenchange", handleChange);
  }, [map]);
  return null;
}

function initLayerState(layers) {
  const s = {};
  for (const l of layers) s[l.layer_id] = { visible: true, opacity: 1 };
  return s;
}

/**
 * Renders each project layer as a REAL tile layer (Wave A's tile_url_template,
 * signed token already embedded in the URL - nothing extra to wire client-side)
 * over a real satellite basemap, auto-fit to the layers' actual bounds. A layer
 * with no tile_url_template yet (pre-Wave-A dataset, COG conversion
 * pending/failed) falls back to its static preview_url, geo-placed at the same
 * bounds via ImageOverlay instead of just sitting in the card grid below.
 *
 * onRefreshLayers (optional): re-fetch GET /projects/{id}/layers, which mints a
 * fresh signed tile token every call (see ProjectService._tile_url_template).
 * Tile tokens are time-boxed (Wave A, default 1h) and don't auto-renew, so a
 * long-open tab will eventually see tile 404/403s. Chosen behavior: on the
 * first tile error, silently call onRefreshLayers() once to pick up fresh
 * tokens - covers the common "left the tab open past the TTL" case with no
 * user-visible interruption. If tiles still fail after that retry, the token
 * mint itself is failing (e.g. the user's own session/auth is what's actually
 * expired, since refreshing layers requires a valid Bearer token) - stop
 * retrying and show a clear message instead of silently blank tiles.
 */
export default function ProjectMap({ layers, onRefreshLayers }) {
  const [layerState, setLayerState] = useState(() => initLayerState(layers));
  const [symbologyState, setSymbologyState] = useState(() => initSymbologyState(layers));
  const [tilesExpired, setTilesExpired] = useState(false);
  const retriedRef = useRef(false);
  // Phase 3 Wave E: GEE-style Map/Satellite toggle - swaps only the BASE
  // reference layer underneath the project's own tiles, same free tile
  // sources as before (Esri satellite default, Carto "Map" - see
  // lib/basemap.js). Purely a display choice, unrelated to any layer's own
  // symbology/visibility state above.
  const [basemapMode, setBasemapMode] = useState("satellite");

  // Phase 3 Wave F: real browser Fullscreen API (not a CSS-only fake) on the
  // whole `.map-frame` card, so the map AND its overlaid chrome (toolbar,
  // Layers panel, etc.) all expand together, matching what a user actually
  // sees, not just the Leaflet canvas. `isFullscreen` is driven by the
  // `fullscreenchange` event, not the click itself - the browser's own Esc
  // key exits fullscreen without going through our click handler at all, so
  // tracking only "did the user click" would leave the icon stuck showing
  // the wrong state after Esc.
  const mapFrameRef = useRef(null);
  const [isFullscreen, setIsFullscreen] = useState(false);

  useEffect(() => {
    function handleChange() {
      setIsFullscreen(document.fullscreenElement === mapFrameRef.current);
    }
    document.addEventListener("fullscreenchange", handleChange);
    return () => document.removeEventListener("fullscreenchange", handleChange);
  }, []);

  function toggleFullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      mapFrameRef.current?.requestFullscreen();
    }
  }

  useEffect(() => {
    setLayerState((prev) => {
      const next = {};
      for (const l of layers) next[l.layer_id] = prev[l.layer_id] ?? { visible: true, opacity: 1 };
      return next;
    });
    setSymbologyState((prev) => {
      const fresh = initSymbologyState(layers);
      const next = {};
      for (const l of layers) next[l.layer_id] = prev[l.layer_id] ?? fresh[l.layer_id];
      return next;
    });
  }, [layers]);

  function updateSymbology(layerId, next) {
    setSymbologyState((prev) => ({ ...prev, [layerId]: next }));
  }

  // Phase 3 Wave D: measure tools + pixel inspection. Mutually exclusive via
  // a single `measureMode` - "inspect" (default, click = pixel lookup) or
  // "distance"/"area" (click = add a vertex) - see MapClickRouter.
  const [measureMode, setMeasureMode] = useState("inspect");
  const [measurePoints, setMeasurePoints] = useState([]);
  const [pixelPopup, setPixelPopup] = useState(null); // { latlng, loading, rows: [{layer, values?, error?}] }

  function selectMeasureMode(nextMode) {
    setMeasureMode(nextMode);
    setMeasurePoints([]);
    setPixelPopup(null);
  }

  function addMeasurePoint(latlng) {
    setMeasurePoints((prev) => [...prev, latlng]);
  }

  function clearMeasurement() {
    setMeasurePoints([]);
  }

  const measureResult = useMemo(() => {
    if (measureMode === "distance" && measurePoints.length >= 2) {
      return `${formatNumber(lineDistanceMeters(measurePoints), 1)} m`;
    }
    if (measureMode === "area" && measurePoints.length >= 3) {
      return formatHectares(polygonAreaHectares(measurePoints));
    }
    return null;
  }, [measureMode, measurePoints]);

  // Inspects whichever layer(s) are currently checked/visible in
  // LayersPanel - reused as-is instead of a second "which layer did you
  // mean" resolution just for clicks. With several layers visible at once
  // this naturally inspects all of them.
  async function inspectPixel(latlng) {
    // `symbologyLayers` is declared further down in this same function body,
    // but this closure only reads it when a click actually happens - always
    // after the full render (and its `const` assignments) has completed.
    const targets = symbologyLayers;
    if (targets.length === 0) return;
    setPixelPopup({ latlng, loading: true, rows: [] });
    const rows = await Promise.all(
      targets.map(async (l) => {
        try {
          const data = await apiFetch(`/layers/${l.layer_id}/pixel?lon=${latlng.lng}&lat=${latlng.lat}`);
          return { layer: l, values: data.values };
        } catch (err) {
          return { layer: l, error: err.message ?? "Could not read this pixel." };
        }
      })
    );
    setPixelPopup({ latlng, loading: false, rows });
  }

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

  function toggle(layerId, visible) {
    setLayerState((prev) => ({ ...prev, [layerId]: { ...prev[layerId], visible } }));
  }

  function setOpacity(layerId, opacity) {
    setLayerState((prev) => ({ ...prev, [layerId]: { ...prev[layerId], opacity } }));
  }

  function handleTileError() {
    if (!retriedRef.current) {
      retriedRef.current = true;
      onRefreshLayers?.();
    } else {
      setTilesExpired(true);
    }
  }

  function handleTileLoad() {
    retriedRef.current = false;
    setTilesExpired(false);
  }

  // Tile-vs-preview rendering for one layer.
  function renderLayer(l, { opacity = 1, key } = {}) {
    if (!l) return null;
    if (l.tile_url_template) {
      const hasLegend = !!(l.class_legend && Object.keys(l.class_legend).length > 0);
      return (
        <TileLayer
          key={key ?? `${l.layer_id}-tiles`}
          url={buildTileUrl(l.tile_url_template, symbologyState[l.layer_id], hasLegend)}
          opacity={opacity}
          eventHandlers={{ tileerror: handleTileError, load: handleTileLoad }}
        />
      );
    }
    return <ImageOverlay key={key ?? `${l.layer_id}-preview`} url={l.preview_url} bounds={l.bounds} opacity={opacity} />;
  }

  // Every layer is independently visible/opaque via its own checkbox in
  // LayersPanel (Wave G removed the old Time/Compare exclusivity) - render
  // whichever ones are currently checked, simultaneously.
  function renderGenericLayers(list) {
    return list.map((l) => {
      const state = layerState[l.layer_id] ?? { visible: true, opacity: 1 };
      if (!state.visible) return null;
      return renderLayer(l, { opacity: state.opacity });
    });
  }

  // Whichever layers are currently checked ARE the ones with a live tile
  // render - the same set drives both the Symbology gear popover's target
  // and Wave D's pixel-inspection targets, now naturally covering however
  // many layers happen to be visible at once instead of a single "active" one.
  const symbologyLayers = layers.filter(
    (l) => (layerState[l.layer_id] ?? { visible: true }).visible && l.tile_url_template
  );

  // One popup row per inspected layer: the classified label (from the same
  // persisted class_legend the Symbology panel already renders) when that
  // layer's current mode is "classified", otherwise its raw band values -
  // whichever mode is currently active there, no separate mode switch here.
  function renderPixelRow({ layer, values, error }) {
    if (error) return <div className="pixel-popup-error">{error}</div>;
    const symbology = symbologyState[layer.layer_id];
    const hasLegend = !!(layer.class_legend && Object.keys(layer.class_legend).length > 0);
    if (symbology?.mode === "classified" && hasLegend) {
      const raw = values[0];
      if (raw == null) return <div>No data at this point.</div>;
      const key = String(Math.round(raw));
      const entry = layer.class_legend[key];
      return (
        <div className="pixel-popup-value">
          <span className="legend-swatch" style={{ background: legendEntryColor(entry) }} aria-hidden="true" />
          {legendEntryLabel(key, entry)}
        </div>
      );
    }
    return (
      <div className="pixel-popup-value">
        {values.every((v) => v == null)
          ? "No data at this point."
          : values.map((v, i) => `B${i + 1}: ${v == null ? "—" : formatNumber(v, 0)}`).join(" · ")}
      </div>
    );
  }

  return (
    <div>
      <ErrorBanner
        message={
          tilesExpired
            ? "This map's tile session has expired. Reload the page to keep viewing imagery."
            : null
        }
        onRetry={() => window.location.reload()}
      />
      <div className="map-frame" ref={mapFrameRef}>
        {/* Phase 3 Wave E: plain React overlays, siblings of <MapContainer>
         * (not react-leaflet children) - none of these need Leaflet's map
         * context, just `.map-frame` itself as the positioned ancestor. */}
        <div className="map-overlay-topleft">
          <MeasureTools
            mode={measureMode}
            onModeChange={selectMeasureMode}
            onClear={clearMeasurement}
            result={measureResult}
            pointCount={measurePoints.length}
          />
        </div>
        <div className="map-overlay-topright">
          <div className="map-toolbar-row">
            <FullscreenToggle active={isFullscreen} onClick={toggleFullscreen} />
            <BasemapToggle mode={basemapMode} onChange={setBasemapMode} />
          </div>
          <LayersPanel
            layers={layers}
            layerState={layerState}
            symbologyState={symbologyState}
            onToggleVisibility={toggle}
            onOpacityChange={setOpacity}
            onSymbologyChange={updateSymbology}
          />
        </div>
        <MapContainer bounds={bounds} boundsOptions={{ padding: [24, 24] }} scrollWheelZoom={false} className="map-root">
          <ScrollZoomOnActivate />
          <FullscreenInvalidate />
          <CoordinateReadout />
          <MapClickRouter mode={measureMode} onInspect={inspectPixel} onMeasurePoint={addMeasurePoint} />
          <MeasureDrawing mode={measureMode} points={measurePoints} />
          {pixelPopup ? (
            <Popup position={pixelPopup.latlng} eventHandlers={{ remove: () => setPixelPopup(null) }}>
              <div className="pixel-popup">
                {pixelPopup.loading ? (
                  "Reading pixel…"
                ) : pixelPopup.rows.length === 0 ? (
                  "No active layer to inspect."
                ) : (
                  pixelPopup.rows.map((row) => (
                    <div className="pixel-popup-row" key={row.layer.layer_id}>
                      <strong>
                        {row.layer.type} · {row.layer.date_processed ?? "undated"}
                      </strong>
                      {renderPixelRow(row)}
                    </div>
                  ))
                )}
              </div>
            </Popup>
          ) : null}
          <TileLayer
            attribution={basemapMode === "map" ? CARTO_BASEMAP_ATTRIBUTION : BASEMAP_ATTRIBUTION}
            url={basemapMode === "map" ? CARTO_BASEMAP_URL : BASEMAP_URL}
          />
          {renderGenericLayers(layers)}
          {layers.map((l) => (
            <Rectangle
              key={l.layer_id}
              bounds={l.bounds}
              pathOptions={{
                color: DATASET_TYPE_COLORS[l.type] ?? "#0B6B46",
                weight: 2,
                fillOpacity: 0,
              }}
            >
              <Tooltip sticky>
                {l.type} · {l.date_processed ?? "undated"}
              </Tooltip>
            </Rectangle>
          ))}
        </MapContainer>
      </div>
    </div>
  );
}
