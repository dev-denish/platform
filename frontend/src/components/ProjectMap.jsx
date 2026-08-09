import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  MapContainer,
  TileLayer,
  WMSTileLayer,
  GeoJSON,
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
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { toPng } from "html-to-image";
import { Layers as LayersIcon, Wrench } from "lucide-react";
import { apiFetch, API_BASE } from "../config.js";
import { DATASET_TYPE_COLORS } from "../lib/colors.js";
import { basemapFor } from "../lib/basemap.js";
import { initSymbologyState, buildTileUrl, legendEntryLabel, legendEntryColor } from "../lib/symbology.js";
import { lineDistanceMeters, polygonAreaHectares, scaleRatioLabel } from "../lib/measure.js";
import { formatNumber } from "../lib/format.js";
import { useCollapse } from "../lib/useCollapse.js";
import LayersPanel from "./LayersPanel.jsx";
import MeasureTools from "./MeasureTools.jsx";
import DrawTools from "./DrawTools.jsx";
import MapToolbar from "./MapToolbar.jsx";
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
 * Bugfix (scroll-zoom stuck disabled after switching toolbar tools):
 * `click` goes through Leaflet's own event system (`useMapEvents` ->
 * `map.on(...)`) and is reliable - proven directly (instrumented
 * `scrollWheelZoom.enable`/`disable` with call logging against the real
 * running app) to always fire correctly. Hover re-activation used to go
 * through Leaflet's `mouseover`/`mouseout` map events too, which worked in
 * isolation but broke specifically after a real wheel-zoom: Leaflet's
 * container-level mouseover/mouseout filters bubbled DOM events by checking
 * `relatedTarget` is truly outside the container, and a wheel-zoom's
 * pane/tile reflow was proven (same instrumentation, plus a direct
 * `map.fire('mouseover')` sanity check showing the registered listener
 * itself was still intact and correctly wired) to make that relatedTarget
 * check silently fail to recognize a genuine re-entry after the mouse had
 * been over a toolbar button outside the map. `mouseenter`/`mouseleave` are
 * the actual browser-native primitives for "did the pointer truly cross
 * this element's boundary" - non-bubbling, no relatedTarget filtering
 * needed - so hover re-activation now uses those, as plain DOM listeners on
 * `map.getContainer()` (same place focus/blur already lived), leaving click
 * on Leaflet's own system since that part was never the problem.
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

  const mapEventHandlers = useMemo(() => ({ click: activate }), [activate]);
  useMapEvents(mapEventHandlers);

  useEffect(() => {
    map.scrollWheelZoom.disable();
    const container = map.getContainer();
    container.addEventListener("mouseenter", activate);
    container.addEventListener("mouseleave", deactivate);
    container.addEventListener("focus", activate);
    container.addEventListener("blur", deactivate);
    return () => {
      container.removeEventListener("mouseenter", activate);
      container.removeEventListener("mouseleave", deactivate);
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
 * Feeds the top toolbar's live Lat/Lon/Zoom/Scale readout (Wave: map UI
 * redesign - relocates Phase 3 Wave D's coordinate readout out of its own
 * floating corner pill and into the toolbar, plus reports zoom/center for
 * the two new derived readouts). Also hands the real Leaflet map instance up
 * once, via `onReady`, so the toolbar's zoom in/out/Extent buttons can call
 * it directly - those buttons live outside <MapContainer> (a real full-width
 * bar above the map, not an overlay control), so they have no other way to
 * reach the map. EPSG:4326 always, same as everywhere else in this app (see
 * LayerOut.crs) - a metric reprojection only ever happens internally, inside
 * the measure-tool math (lib/measure.js).
 *
 * `mousemove` is rAF-throttled: the raw browser event can fire well over
 * 60/sec (high-polling-rate mice), and every one of those used to be a
 * setState -> re-render of the whole toolbar readout. Coalescing to one update
 * per animation frame keeps the readout live (it still tracks the pointer) but
 * never asks React to render faster than the screen can paint. Only mousemove
 * needs this - zoomend/moveend/mouseout are already one-shot events.
 */
function MapViewSync({ onReady, onChange }) {
  const map = useMap();
  const rafRef = useRef(null);
  const pendingPosRef = useRef(null);
  useEffect(() => {
    onReady(map);
    onChange((v) => ({ ...v, zoom: map.getZoom(), center: map.getCenter() }));
  }, [map, onReady, onChange]);
  useEffect(() => () => {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
  }, []);
  useMapEvents({
    mousemove(e) {
      pendingPosRef.current = e.latlng;
      if (rafRef.current !== null) return;
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        onChange((v) => ({ ...v, pos: pendingPosRef.current }));
      });
    },
    mouseout() {
      // Drop any frame still queued from the last mousemove, otherwise it
      // lands AFTER this and re-shows a stale position the pointer has left.
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      onChange((v) => ({ ...v, pos: null }));
    },
    zoomend() {
      onChange((v) => ({ ...v, zoom: map.getZoom(), center: map.getCenter() }));
    },
    moveend() {
      onChange((v) => ({ ...v, zoom: map.getZoom(), center: map.getCenter() }));
    },
  });
  return null;
}

/** Real cartographic distance bar (Wave: map UI redesign), bottom-right of
 * the map - Leaflet's own built-in control, just added via the hook API
 * (react-leaflet has no <ScaleControl> component) so it shares this file's
 * existing add-a-control-via-useMap pattern. */
function ScaleControl() {
  const map = useMap();
  useEffect(() => {
    const control = L.control.scale({ position: "bottomright", metric: true, imperial: false }).addTo(map);
    return () => control.remove();
  }, [map]);
  return null;
}

/**
 * Live Lat/Lon/Zoom/Scale readout (Wave: floating map controls) - relocated
 * out of the (now floating, collapsed-by-default) toolbar into its own small
 * badge in the map's bottom-right corner, sharing that corner with Leaflet's
 * own scale bar above (ScaleControl). A plain sibling React overlay at a
 * hand-picked bottom/right offset would have to guess the scale bar's own
 * height to avoid overlapping it, and that guess breaks the moment either
 * badge's content changes size. Adding this as a genuine `L.control` at the
 * same "bottomright" position instead lets Leaflet's own corner-stacking
 * (same mechanism that already places the scale bar) lay the two badges out
 * for free, in DOM order - "two small badges sharing that corner" with no
 * manual math.
 *
 * The control's DOM node is created once (onAdd) and handed to a React
 * portal rather than built with string/innerHTML updates, so click-to-copy
 * (carried over from the old toolbar readout) stays real React state/JSX -
 * same "keep the actual rendering inside React's model" reasoning this
 * file's other hand-rolled Leaflet integrations (SwipeClip, etc.) already
 * follow.
 */
function CoordinateBadge({ lat, lon, zoom, scaleLabel }) {
  const map = useMap();
  const [container, setContainer] = useState(null);
  const [copied, setCopied] = useState(false);
  const copyTimerRef = useRef(null);

  useEffect(() => {
    const control = L.control({ position: "bottomright" });
    control.onAdd = () => {
      const div = L.DomUtil.create("div", "map-coord-badge");
      // Without this, a click on the copy button also reaches the map
      // underneath (pixel-inspect / measure-point), same as any other
      // interactive control content sitting over the map canvas.
      L.DomEvent.disableClickPropagation(div);
      return div;
    };
    control.addTo(map);
    setContainer(control.getContainer());
    return () => {
      control.remove();
      setContainer(null);
    };
  }, [map]);

  useEffect(() => () => clearTimeout(copyTimerRef.current), []);

  async function copyCoords() {
    if (lat == null || lon == null) return;
    try {
      await navigator.clipboard.writeText(`${lat.toFixed(5)}, ${lon.toFixed(5)}`);
      setCopied(true);
      // Restart rather than stack, so a second click doesn't have the first
      // click's timer cut its own confirmation short.
      clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard permission denied / insecure context - nothing useful to say
      // in a one-line readout, and the coordinates are still visible to select.
    }
  }

  if (!container) return null;

  return createPortal(
    <button
      type="button"
      className="map-toolbar-copy"
      onClick={copyCoords}
      disabled={lat == null || lon == null}
      title="Copy these coordinates"
    >
      {lat != null && lon != null ? (
        <>
          Lat: {lat.toFixed(5)}° Lon: {lon.toFixed(5)}°
        </>
      ) : (
        "Lat: — Lon: —"
      )}
      {copied ? <span className="map-toolbar-copied"> Copied!</span> : null}
      {" | "}Zoom: {zoom ?? "—"}
      {" | "}Scale: {scaleLabel ?? "—"}
    </button>,
    container
  );
}

/**
 * Before/after swipe comparison: clips the TOP ("after") tile layer to the
 * right of the divider so the bottom ("before") layer shows through on the
 * left. Only the top layer is clipped - the bottom one just renders whole.
 *
 * Hand-rolled rather than pulling in `leaflet-side-by-side`: that plugin is an
 * L.Control that takes Leaflet layer INSTANCES and adds/removes them itself,
 * which is exactly the imperative layer management react-leaflet's declarative
 * <TileLayer> children own here - it would fight the framework for the same
 * job. Its actual clipping technique is ~10 lines, reproduced below, and this
 * file already hand-rolls the measure/draw click handling in the same spirit.
 *
 * The coordinate space is the subtle part (and the reason the plugin exists):
 * a Leaflet layer container is an unsized `position:absolute` div inside a
 * pane, so percentage/edge-relative clipping has no box to resolve against.
 * Both the plugin and this compute the rectangle in LAYER-POINT pixels (which
 * is what the container's origin is in) and re-apply it whenever the map
 * moves, since the layer-point origin shifts with every pan/zoom.
 */
function SwipeClip({ layer, pct }) {
  const map = useMap();
  useEffect(() => {
    if (!layer) return undefined;
    function apply() {
      // The container element is resolved per call, not captured once:
      // react-leaflet hands the layer instance to our ref during the
      // layout-effect phase, but Leaflet only creates that container when the
      // layer is actually added to the map, in the passive-effect phase right
      // after. `layeradd` below covers that gap without depending on which
      // effect wins the race.
      const el = layer.getContainer?.();
      if (!el) return;
      const size = map.getSize();
      const nw = map.containerPointToLayerPoint([0, 0]);
      const se = map.containerPointToLayerPoint([size.x, size.y]);
      const x = nw.x + (size.x * pct) / 100;
      el.style.clipPath = `polygon(${x}px ${nw.y}px, ${se.x}px ${nw.y}px, ${se.x}px ${se.y}px, ${x}px ${se.y}px)`;
    }
    apply();
    map.on("move zoomend resize layeradd", apply);
    return () => {
      map.off("move zoomend resize layeradd", apply);
      const el = layer.getContainer?.();
      if (el) el.style.clipPath = "";
    };
  }, [map, layer, pct]);
  return null;
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
function MapClickRouter({ mode, drawMode, onInspect, onMeasurePoint, onDrawPoint }) {
  useMapEvents({
    click(e) {
      // Draw wins while it's active: selecting a draw mode already forces
      // measure back to "inspect" (selectDrawMode), so these never overlap.
      if (drawMode !== "none") onDrawPoint(e.latlng);
      else if (mode === "inspect") onInspect(e.latlng);
      else onMeasurePoint(e.latlng);
    },
  });
  return null;
}

const MEASURE_COLOR = "#e0692f";
// Deliberately not the measure orange (which an upcoming redesign reserves as
// the primary accent) - a drawn shape must stay tellable-apart from an
// in-progress measurement. No violet token exists in index.css yet.
const DRAW_COLOR = "#7c3aed";

// Hoisted out of renderLayer: react-leaflet's WMSTileLayer calls
// layer.setParams() (which redraws - re-requests every tile) whenever this
// object's REFERENCE changes, not its contents (see
// @react-leaflet/core WMSTileLayer.js's `props.params !== prevProps.params`).
// An inline `params={{...}}` literal is a new object every render, so any
// unrelated re-render of ProjectMap (e.g. the mousemove-driven `mapView`
// state on every animation frame while the cursor crosses the map) redrew
// every WMS layer in a tight loop even though nothing about the request
// actually changed. One shared constant fixes it for every WMS layer at once.
const WMS_TILE_PARAMS = { layers: "_", format: "image/png", transparent: true };

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

/** Same purely-visual job as MeasureDrawing, for the draw tool's shape (in
 * progress or finished) - a lone vertex is just the marker, no line yet. */
function DrawDrawing({ mode, points }) {
  if (mode === "none" || points.length === 0) return null;
  // interactive: false on every piece - a Leaflet path consumes the click that
  // hits it instead of letting it reach the map, so once the polygon had a fill
  // (3+ vertices) any further click INSIDE the shape silently added no vertex.
  return (
    <>
      {mode === "polygon" && points.length >= 3 ? (
        <Polygon
          positions={points}
          pathOptions={{ color: DRAW_COLOR, weight: 2, fillOpacity: 0.15, interactive: false }}
        />
      ) : points.length >= 2 ? (
        <Polyline positions={points} pathOptions={{ color: DRAW_COLOR, weight: 3, interactive: false }} />
      ) : null}
      {points.map((p, i) => (
        <CircleMarker
          key={i}
          center={p}
          radius={4}
          pathOptions={{ color: DRAW_COLOR, weight: 2, fillColor: "#fff", fillOpacity: 1, interactive: false }}
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
 * Tile tokens are time-boxed (default 1h) and don't auto-renew.
 *
 * Wave: tile-expiry UX (root-caused before this fix). Confirmed via a real
 * browser network log, not assumption: `access_token_ttl_minutes` (15) is far
 * shorter than `tile_token_ttl_seconds` (1h), so by the time a tile token
 * expires, the access token has ALSO been expired for ~45 minutes already. The
 * reactive retry below calls `onRefreshLayers`, which goes through the app's
 * one shared `apiFetch` (see config.js) - that wrapper already transparently
 * catches a 401, rotates the access token via the refresh token, and retries,
 * so the reactive path was never structurally broken. What IS fragile about a
 * purely reactive design: it's a single attempt with no retry-of-the-retry, so
 * any transient hiccup right at that moment (slow network, a refresh racing
 * with another one from a different tab) permanently strands the user behind
 * the banner below until a manual reload - there's no second chance.
 *
 * Fix: a PROACTIVE refresh timer (well under the 1h tile-token TTL) is now the
 * primary defense - see the effect below - so a real session essentially never
 * reaches the reactive path during normal use. The reactive one-shot
 * retry-then-banner stays as the fallback for genuine edge cases (e.g. the
 * proactive refresh itself failing due to lost connectivity). If the REFRESH
 * token itself is expired/rejected, `onRefreshLayers` resolves to `false` and
 * the banner is the correct, intended outcome - that session is genuinely
 * over and a real reload/re-login is required, not papered over.
 *
 * onLegendChanged (optional, Wave: editable class legend): passed through to
 * LayersPanel/ClassLegendEditor, called after a legend PATCH succeeds. Unlike
 * onRefreshLayers (just a fresh tile token), this is the caller's cue to
 * re-fetch KPIs/evolution too - a legend edit changes Total Area and
 * per-class numbers, not just which pixels render which color.
 */
// Well under the 1h tile-token TTL, so a proactive refresh always lands with
// margin to spare even if the tab was briefly backgrounded/throttled around
// the scheduled tick.
const PROACTIVE_REFRESH_INTERVAL_MS = 45 * 60 * 1000;

// Wave: GEE analysis registry. `overlayTileUrl` (optional) is one extra XYZ
// tile layer drawn on top of everything else - the selected analysis's GEE
// tiles (see AnalysisPanel.jsx). Deliberately a prop on THIS map rather than a
// second independent MapContainer, so an analysis result is seen over the
// project's own layers, with the same toolbar/measure/basemap chrome.
export default function ProjectMap({
  layers,
  onRefreshLayers,
  onLegendChanged,
  projectId,
  overlayTileUrl,
  activeAnalysis = null,
}) {
  const [layerState, setLayerState] = useState(() => initLayerState(layers));
  // Wave: map UI redesign. The Leaflet map instance (once mounted) and its
  // live zoom/center/hovered-position, both consumed by the toolbar above -
  // see MapViewSync's own docstring for why these can't just live inside the
  // toolbar component itself.
  const [mapRef, setMapRef] = useState(null);
  const [mapView, setMapView] = useState({ pos: null, zoom: null, center: null });
  const [symbologyState, setSymbologyState] = useState(() => initSymbologyState(layers));
  const [tilesExpired, setTilesExpired] = useState(false);
  // 'idle' (no retry outstanding) | 'pending' (one in flight) | 'failed' (the
  // one attempt already lost). Replaces a plain boolean: with a boolean, an
  // error batch arriving WHILE the first retry's `onRefreshLayers()` is still
  // in flight (real, observed with 2+ simultaneous raster layers - their tile
  // batches settle independently but share these refs) fell through to the
  // "already retried, give up" branch and flashed the banner even though that
  // in-flight retry was about to succeed. 'pending' lets a second settle
  // event just wait for the first attempt's outcome instead of pre-judging it.
  const retryStateRef = useRef("idle");
  // Wave: multi-format layers. layer_id -> GeoJSON FeatureCollection (vector
  // layers) or `null` on fetch failure - fetched once per layer via
  // features_url (GET /layers/{id}/geojson or the WFS proxy) and cached here,
  // unlike raster tiles which stream themselves through <TileLayer>.
  const [vectorData, setVectorData] = useState({});

  // Wave: tile-expiry UX. Proactive refresh is the PRIMARY defense (see this
  // component's docstring) - re-mints fresh layer/tile tokens on a timer, well
  // before the 1h tile-token TTL, so the reactive fallback below rarely fires
  // in real use. `onRefreshLayers` is a plain function re-created by the
  // parent on every render (not memoized there), so it's read through a ref
  // rather than placed in this effect's dependency array - otherwise a
  // same-page re-render unrelated to layers (e.g. opening a dialog) would
  // tear down and restart this interval, pushing the actual refresh later and
  // later. The effect itself still only runs once, for this component's
  // lifetime on this project page.
  const onRefreshLayersRef = useRef(onRefreshLayers);
  useEffect(() => {
    onRefreshLayersRef.current = onRefreshLayers;
  }, [onRefreshLayers]);

  useEffect(() => {
    const id = setInterval(() => {
      // Skip a tick while backgrounded - nothing is watching these tiles
      // right now, and the reactive fallback still covers the tab regaining
      // focus after the tile token has since expired.
      if (document.visibilityState === "visible") onRefreshLayersRef.current?.();
    }, PROACTIVE_REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const toFetch = layers.filter((l) => l.features_url && !(l.layer_id in vectorData));
    if (toFetch.length === 0) return undefined;
    let cancelled = false;
    (async () => {
      for (const l of toFetch) {
        try {
          // `features_url` (LayerRepository._features_url) already carries the
          // full `/api/v1/...` path, same as tile_url_template - but unlike
          // that one (used raw as a Leaflet tile URL), this goes through
          // apiFetch, which itself prepends API_BASE. Strip it first or every
          // vector/WFS layer 404s on a doubled `/api/v1/api/v1/...` path.
          const data = await apiFetch(l.features_url.replace(API_BASE, ""));
          if (!cancelled) setVectorData((prev) => ({ ...prev, [l.layer_id]: data }));
        } catch {
          if (!cancelled) setVectorData((prev) => ({ ...prev, [l.layer_id]: null }));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [layers, vectorData]);
  const batchLoadedRef = useRef(0);
  const batchErroredRef = useRef(0);
  // Phase 3 Wave E: GEE-style Map/Satellite toggle - swaps only the BASE
  // reference layer underneath the project's own tiles, same free tile
  // sources as before (Esri satellite default, Carto "Map" - see
  // lib/basemap.js). Purely a display choice, unrelated to any layer's own
  // symbology/visibility state above.
  const [basemapMode, setBasemapMode] = useState("satellite");
  const basemap = basemapFor(basemapMode);

  // Wave: map toolbar capabilities. Compare mode: { before, after } layer ids,
  // or null when off. `swipePct` is the divider's position across the map
  // canvas (0 = far left, 100 = far right); `swipeLayer` is the real Leaflet
  // TileLayer instance of the "after" side, captured by ref because SwipeClip
  // clips its container element directly (see SwipeClip's docstring).
  const [compare, setCompare] = useState(null);
  const [swipePct, setSwipePct] = useState(50);
  const [swipeLayer, setSwipeLayer] = useState(null);
  const canvasRef = useRef(null);

  // Wave: floating map controls. The toolbar and Layers panel are now
  // floating overlays, not docked-in-flow columns - each toggle is its own
  // sessionStorage-backed useCollapse, same pattern the rest of the app
  // already uses for collapsible state, but now defaulting to CLOSED so the
  // map gets full width/space on first load. Neither toggle affects the map
  // canvas's own box size any more (they float on top of it), so there is no
  // longer an invalidateSize effect tied to either - see the old
  // `panelOpen`-driven one this replaced.
  const [toolbarOpen, toggleToolbar] = useCollapse("collapse:map-toolbar-panel", false);
  const [layersOpen, toggleLayers] = useCollapse("collapse:map-layers-panel", false);

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

  // These three are handed straight to the memoized <LayersPanel> - useCallback
  // (empty deps: they only ever call the always-stable setState updaters) is
  // what makes that React.memo actually skip re-renders instead of being a
  // no-op on a fresh function identity every render.
  const updateSymbology = useCallback((layerId, next) => {
    setSymbologyState((prev) => ({ ...prev, [layerId]: next }));
  }, []);

  const toggle = useCallback((layerId, visible) => {
    setLayerState((prev) => ({ ...prev, [layerId]: { ...prev[layerId], visible } }));
  }, []);

  const setOpacity = useCallback((layerId, opacity) => {
    setLayerState((prev) => ({ ...prev, [layerId]: { ...prev[layerId], opacity } }));
  }, []);

  // Phase 3 Wave D: measure tools + pixel inspection. Mutually exclusive via
  // a single `measureMode` - "inspect" (default, click = pixel lookup) or
  // "distance"/"area" (click = add a vertex) - see MapClickRouter.
  const [measureMode, setMeasureMode] = useState("inspect");
  const [measurePoints, setMeasurePoints] = useState([]);
  const [pixelPopup, setPixelPopup] = useState(null); // { latlng, loading, rows: [{layer, values?, error?}] }

  // Draw tools: point/line/polygon vertices collected by the same click
  // gesture the measure tools use, so the two are mutually exclusive - picking
  // either one resets the other, same convention selectMeasureMode already had
  // for pixel inspection.
  const [drawMode, setDrawMode] = useState("none");
  const [drawPoints, setDrawPoints] = useState([]);
  const [drawFinished, setDrawFinished] = useState(false);

  function resetDraw(nextMode) {
    setDrawMode(nextMode);
    setDrawPoints([]);
    setDrawFinished(false);
  }

  function selectMeasureMode(nextMode) {
    setMeasureMode(nextMode);
    setMeasurePoints([]);
    setPixelPopup(null);
    resetDraw("none");
  }

  function selectDrawMode(nextMode) {
    setMeasureMode("inspect");
    setMeasurePoints([]);
    setPixelPopup(null);
    resetDraw(nextMode);
  }

  function addDrawPoint(latlng) {
    if (drawFinished) return;
    // A point needs no Finish - the single click IS the whole shape.
    if (drawMode === "point") {
      setDrawPoints([latlng]);
      setDrawFinished(true);
      return;
    }
    setDrawPoints((prev) => [...prev, latlng]);
  }

  function addMeasurePoint(latlng) {
    setMeasurePoints((prev) => [...prev, latlng]);
  }

  function clearMeasurement() {
    setMeasurePoints([]);
  }

  // Raw base-unit value (meters / hectares); MeasureTools formats it in
  // whichever unit the user picked there.
  const measureResult = useMemo(() => {
    if (measureMode === "distance" && measurePoints.length >= 2) {
      return lineDistanceMeters(measurePoints);
    }
    if (measureMode === "area" && measurePoints.length >= 3) {
      return polygonAreaHectares(measurePoints);
    }
    return null;
  }, [measureMode, measurePoints]);

  // Inspects whichever layer(s) are currently checked/visible in
  // LayersPanel, PLUS the Analysis tab's one selected GEE analysis
  // (`activeAnalysis`, passed down from AnalysisPanel - null on the Layers
  // tab's own ProjectMap instance, which has no such thing) - reused as-is
  // instead of a second "which layer did you mean" resolution just for
  // clicks. With several targets active at once this naturally inspects all
  // of them, each labeled with its own source, rather than silently picking
  // one - see renderGeePixelRow below for how a not-yet-computed async
  // analysis surfaces as a row instead of a network error.
  async function inspectPixel(latlng) {
    // `symbologyLayers` is declared further down in this same function body,
    // but this closure only reads it when a click actually happens - always
    // after the full render (and its `const` assignments) has completed.
    const targets = symbologyLayers;
    if (targets.length === 0 && !activeAnalysis) return;
    setPixelPopup({ latlng, loading: true, rows: [] });
    const cogRows = await Promise.all(
      targets.map(async (l) => {
        try {
          const data = await apiFetch(`/layers/${l.layer_id}/pixel?lon=${latlng.lng}&lat=${latlng.lat}`);
          return { layer: l, values: data.values };
        } catch (err) {
          return { layer: l, error: err.message ?? "Could not read this pixel." };
        }
      })
    );
    const geeRows = [];
    if (activeAnalysis) {
      try {
        // Wave: raw-imagery browsing. `year`, only set for the 3 browse
        // analyses, keeps the click sampling the SAME scene the rendered
        // tile shows - see AnalysisPanel's own comment on where this value
        // comes from.
        const yearParam = activeAnalysis.year != null ? `&year=${activeAnalysis.year}` : "";
        const data = await apiFetch(
          `/projects/${projectId}/analyses/${activeAnalysis.id}/point?lon=${latlng.lng}&lat=${latlng.lat}${yearParam}`
        );
        geeRows.push({ gee: true, analysisName: activeAnalysis.name, data });
      } catch (err) {
        geeRows.push({
          gee: true,
          analysisName: activeAnalysis.name,
          error: err.message ?? "Could not read this pixel.",
        });
      }
    }
    setPixelPopup({ latlng, loading: false, rows: [...cogRows, ...geeRows] });
  }

  // Wave: multi-format layers. An external_wms/external_wfs layer's stored
  // bbox is a meaningless full-earth placeholder (its real extent isn't
  // known without parsing GetCapabilities, which this wave doesn't do) - a
  // real raster/vector layer's actual bounds always drive the auto-fit;
  // an external layer never widens it out to the whole world.
  const boundsLayers = useMemo(
    () => layers.filter((l) => l.layer_kind !== "external_wms" && l.layer_kind !== "external_wfs"),
    [layers]
  );

  const bounds = useMemo(() => {
    const all = boundsLayers.flatMap((l) => l.bounds);
    if (all.length === 0) {
      // A project with ONLY external_wms/wfs layers has no real bounds to
      // fit to (see boundsLayers' own docstring) - fall back to a world
      // view rather than rendering nothing at all.
      return layers.length > 0 ? [[-60, -170], [75, 170]] : null;
    }
    const lats = all.map((p) => p[0]);
    const lngs = all.map((p) => p[1]);
    return [
      [Math.min(...lats), Math.min(...lngs)],
      [Math.max(...lats), Math.max(...lngs)],
    ];
  }, [boundsLayers, layers]);

  // Toolbar zoom/Extent buttons live OUTSIDE <MapContainer> (see MapViewSync)
  // so they drive the map through `mapRef` - memoized so a mousemove-driven
  // toolbar re-render doesn't hand it three new function identities as well.
  const zoomIn = useCallback(() => mapRef?.zoomIn(), [mapRef]);
  const zoomOut = useCallback(() => mapRef?.zoomOut(), [mapRef]);
  const fitExtent = useCallback(() => mapRef?.fitBounds(bounds, { padding: [24, 24] }), [mapRef, bounds]);

  // Wave: AOI clip. Loading a GEE analysis (overlayTileUrl going from null to
  // a real url, or switching to a different one) fits the map to the
  // project's AOI specifically - not the generic `bounds` above, which spans
  // EVERY layer and would zoom out past a small AOI on a project with other,
  // larger-footprint layers. Reuses the Boundary layer's own `bounds` field
  // (the SAME one the boundLayers Rectangle outlines below already render
  // from) via a ref rather than a direct effect dependency, so this only
  // re-fits when overlayTileUrl itself changes, not on every unrelated
  // `layers` refetch (e.g. a background poll) that happens to run while an
  // analysis is already showing.
  const boundaryBoundsRef = useRef(null);
  useEffect(() => {
    boundaryBoundsRef.current = layers.find((l) => l.type === "Boundary")?.bounds ?? null;
  }, [layers]);
  useEffect(() => {
    if (!mapRef || !overlayTileUrl || !boundaryBoundsRef.current) return;
    mapRef.fitBounds(boundaryBoundsRef.current, { padding: [24, 24] });
  }, [overlayTileUrl, mapRef]);

  // Toolbar's coordinate jump + saved-view bookmarks both land here. No zoom
  // given (raw coordinate entry) = keep the current zoom unless it's zoomed
  // way out, where landing at z7 on a point is useless.
  const jumpTo = useCallback(
    (lat, lon, zoom) => {
      if (!mapRef) return;
      // A still-open Identify/pixel-inspect popup runs its own auto-pan
      // (Leaflet's Popup._adjustPan) to keep itself on-screen whenever the
      // view changes - confirmed via instrumentation that this, not any app
      // code, is what was yanking the map back toward its old position right
      // after setView below. Closing it first removes the thing fighting us.
      mapRef.closePopup();
      mapRef.setView([lat, lon], zoom ?? Math.max(mapRef.getZoom(), 14));
    },
    [mapRef]
  );

  // Wave: map toolbar capabilities - "Save image". Captures the Leaflet
  // container itself (tiles, drawn shapes, popups, scale bar), not the whole
  // frame, so the toolbar/Layers chrome around it stays out of the picture.
  // html-to-image re-fetches each tile to inline it; every basemap this app
  // offers sends `Access-Control-Allow-Origin: *` and our own tile endpoint is
  // same-origin, so nothing taints the canvas.
  //
  // A classified raster layer's real coverage is normally SMALLER than the
  // viewport (see the LULC extent rectangle any project screenshot shows) -
  // Leaflet requests the full rectangular tile grid for the viewport anyway,
  // and every tile outside the layer's real bounds legitimately 404s (see
  // tiles.py's documented NotFoundError - confirmed benign, not a bug). html-
  // to-image doesn't know that: it tries to re-fetch/embed every <img> tile
  // in the DOM regardless, and ANY single failed one rejects the whole
  // capture with a bare DOM error Event (not even a real Error - confirmed by
  // temporarily logging the caught value). `filter` excludes exactly the
  // tiles Leaflet itself never marked `leaflet-tile-loaded` (only added on a
  // real `load` event, never on `error` - see leaflet-src.js's _tileOnLoad),
  // so the export just omits the same blank area the user already sees.
  const exportImage = useCallback(async () => {
    if (!mapRef) return;
    try {
      const dataUrl = await toPng(mapRef.getContainer(), {
        backgroundColor: "#ffffff",
        filter: (node) =>
          !(node.tagName === "IMG" && node.classList?.contains("leaflet-tile") && !node.classList.contains("leaflet-tile-loaded")),
      });
      const a = document.createElement("a");
      a.href = dataUrl;
      a.download = `map-${new Date().toISOString().slice(0, 10)}.png`;
      a.click();
    } catch (err) {
      console.error("Map image export failed:", err);
      // A user-initiated action that silently does nothing is worse than a
      // blunt native alert - and this needs no layout of its own.
      window.alert("Could not save this view as an image. Try again once all tiles have finished loading.");
    }
  }, [mapRef]);

  // Dated raster layers are the only ones a before/after swipe is meaningful
  // for (and the only kind that renders as a plain <TileLayer>, which is what
  // SwipeClip clips). Oldest first, so the toolbar's defaults read
  // oldest -> newest.
  const compareOptions = useMemo(
    () =>
      layers
        .filter((l) => l.layer_kind === "raster" && l.tile_url_template && l.date_processed)
        .sort((a, b) => (a.date_processed < b.date_processed ? -1 : 1))
        .map((l) => ({
          layer_id: l.layer_id,
          label: `${l.display_name ?? l.type} · ${l.date_processed}`,
        })),
    [layers]
  );

  // Compare mode needs at least two dated layers; if a layer disappears (or
  // the project never had two), drop out rather than render half a comparison.
  useEffect(() => {
    if (compare && compareOptions.length < 2) setCompare(null);
  }, [compare, compareOptions]);

  // Every hook above this line - the compare-mode effect is the last one, and
  // bailing out early must happen after all of them, never between two.
  if (!bounds) return null;

  // A real expired/invalid token fails EVERY tile of a layer, since they all
  // share the one signed token in the URL. Tiles at the edge of a layer's
  // bounding box legitimately 404 too (outside the COG's actual pixel data -
  // see tile_service.render's "No data at this tile"), but that's permanent
  // and unrelated to auth, and recurs on every reload - a plain <img> error
  // event can't see the HTTP status to tell the two apart directly. So
  // instead of reacting per-error, wait for the whole batch to settle
  // (`load`, which Leaflet fires once all tiles finish loading OR erroring)
  // and only treat it as expiry when NOTHING in that batch loaded - a mix of
  // some successes and some errors is just normal missing-data edges and is
  // ignored, so it can't loop forever re-minting tokens for a 404 that will
  // never go away.
  function handleTileLoadStart() {
    batchLoadedRef.current = 0;
    batchErroredRef.current = 0;
  }

  function handleTileLoad() {
    batchLoadedRef.current += 1;
  }

  function handleTileError() {
    batchErroredRef.current += 1;
  }

  async function handleBatchSettled() {
    if (batchErroredRef.current === 0) return;
    if (batchLoadedRef.current > 0) {
      retryStateRef.current = "idle";
      setTilesExpired(false);
      return;
    }
    if (retryStateRef.current === "idle") {
      retryStateRef.current = "pending";
      const ok = await onRefreshLayers?.();
      retryStateRef.current = ok === false ? "failed" : "idle";
      if (ok === false) setTilesExpired(true);
    } else if (retryStateRef.current === "failed") {
      setTilesExpired(true);
    }
    // 'pending': a retry from an earlier, still-unsettled batch is already in
    // flight (see retryStateRef's own docstring above) - nothing to do here
    // but wait for it; that attempt's own resolution will set 'idle'/'failed'.
  }

  // Wave: multi-format layers. Real geometries (vector layers, and an
  // external_wfs layer's live GetFeature response - both fetched once into
  // `vectorData` above) render as an actual Leaflet <GeoJSON>, not a raster
  // tile/image at all - `undefined` while still loading, `null` on a failed
  // fetch, either way nothing to render yet.
  function renderVectorLayer(l, { opacity = 1, key } = {}) {
    const data = vectorData[l.layer_id];
    if (!data) return null;
    const color = DATASET_TYPE_COLORS[l.type] ?? "#0B6B46";
    return (
      <GeoJSON
        key={key ?? `${l.layer_id}-vector`}
        data={data}
        style={() => ({ color, weight: 2, fillOpacity: 0.15, opacity })}
        pointToLayer={(_feature, latlng) =>
          L.circleMarker(latlng, { radius: 5, color, weight: 2, fillOpacity: 0.7 * opacity, opacity })
        }
      />
    );
  }

  // Tile-vs-preview rendering for one layer.
  function renderLayer(l, { opacity = 1, key } = {}) {
    if (!l) return null;
    if (l.layer_kind === "vector" || l.layer_kind === "external_wfs") {
      return renderVectorLayer(l, { opacity, key });
    }
    if (l.layer_kind === "external_wms" && l.tile_url_template) {
      // A real WMS tile server, not our own {z}/{x}/{y} COG grid - Leaflet's
      // WMSTileLayer computes bbox/width/height/srs per tile itself and
      // appends them as query params to `url` (our SSRF-guarded proxy,
      // never the third-party server directly - see
      // ProjectService._tile_url_template_for). `layers` here is a
      // required prop for L.tileLayer.wms's own bookkeeping only - the
      // proxy always serves the layer_name stored at creation time
      // regardless of what's sent, so its value doesn't matter.
      return (
        <WMSTileLayer
          key={key ?? `${l.layer_id}-wms`}
          url={l.tile_url_template}
          params={WMS_TILE_PARAMS}
          opacity={opacity}
        />
      );
    }
    if (l.tile_url_template) {
      const hasLegend = !!(l.class_legend && Object.keys(l.class_legend).length > 0);
      return (
        <TileLayer
          key={key ?? `${l.layer_id}-tiles`}
          url={buildTileUrl(l.tile_url_template, symbologyState[l.layer_id], hasLegend)}
          opacity={opacity}
          maxZoom={22}
          eventHandlers={{
            loading: handleTileLoadStart,
            tileload: handleTileLoad,
            tileerror: handleTileError,
            load: handleBatchSettled,
          }}
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

  /** One side of the swipe comparison. Only the "after" side gets a ref - it's
   * the layer SwipeClip clips; the "before" side renders whole underneath. */
  function renderCompareSide(layerId, side) {
    const l = layers.find((x) => x.layer_id === layerId);
    if (!l) return null;
    const hasLegend = !!(l.class_legend && Object.keys(l.class_legend).length > 0);
    return (
      <TileLayer
        key={`compare-${side}-${layerId}`}
        ref={side === "after" ? setSwipeLayer : undefined}
        url={buildTileUrl(l.tile_url_template, symbologyState[l.layer_id], hasLegend)}
        maxZoom={22}
      />
    );
  }

  // Plain pointer events on the divider, not a Leaflet handler: the divider is
  // a sibling overlay above the map container, and preventDefault on
  // pointerdown keeps the gesture from also starting a map drag.
  function startSwipeDrag(e) {
    e.preventDefault();
    const rect = canvasRef.current.getBoundingClientRect();
    function move(ev) {
      setSwipePct(Math.min(100, Math.max(0, ((ev.clientX - rect.left) / rect.width) * 100)));
    }
    function up() {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  // Whichever layers are currently checked ARE the ones with a live tile
  // render - the same set drives both the Symbology gear popover's target
  // and Wave D's pixel-inspection targets, now naturally covering however
  // many layers happen to be visible at once instead of a single "active" one.
  // Wave: multi-format layers - restricted to `raster` here: GET
  // /layers/{id}/pixel only ever reads a COG (TileService.read_pixel), so a
  // vector/external_wms/wfs layer has nothing for it to inspect.
  const symbologyLayers = layers.filter(
    (l) =>
      (layerState[l.layer_id] ?? { visible: true }).visible &&
      l.layer_kind === "raster" &&
      l.tile_url_template
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

  // GEE-analysis counterpart to renderPixelRow above, for the one row
  // `inspectPixel` adds from `activeAnalysis` (GET /projects/{id}/analyses/
  // {id}/point). A 422 from that endpoint - e.g. an async vegetation index
  // that's never been run yet - lands here as `error`, the SAME clear
  // message the backend wrote ("Run 'NDVI' first - ..."), not a generic
  // failure or a silent no-op.
  function renderGeePixelRow({ error, data }) {
    if (error) return <div className="pixel-popup-error">{error}</div>;
    // Wave: AOI clip. A third, distinct "no value" state from the error
    // branch above and the "No data at this point" fallback below - the
    // click itself was valid, it just landed outside the clipped AOI the
    // map tile already visually shows as empty. De-emphasized, not styled
    // as a failure.
    if (data.outside_boundary) return <div className="pixel-popup-muted">Outside the project boundary.</div>;
    if (data.class_name) {
      return (
        <div className="pixel-popup-value">
          <span className="legend-swatch" style={{ background: data.class_color || "#999" }} aria-hidden="true" />
          {data.class_name}
        </div>
      );
    }
    // Wave: raw-imagery browsing. The 3 browse analyses have no single
    // scalar value (RGB/dual-pol) - only a formatted `detail` string. Must
    // be checked BEFORE the "No data at this point" fallback, or a real
    // detail string would never render.
    if (data.value == null && data.detail) return <div className="pixel-popup-detail">{data.detail}</div>;
    if (data.value == null) return <div>No data at this point.</div>;
    const digits = data.unit === "% tree cover (2000)" ? 1 : 3;
    return (
      <div className="pixel-popup-value-column">
        <div className="pixel-popup-value">
          {formatNumber(data.value, digits)}
          {data.unit ? <span className="pixel-popup-unit"> {data.unit}</span> : null}
        </div>
        {data.detail ? <div className="pixel-popup-detail">{data.detail}</div> : null}
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
        <div className="map-canvas-wrap" ref={canvasRef}>
          {/* Phase 3 Wave E: plain React overlays, siblings of
           * <MapContainer> (not react-leaflet children) - none of these
           * need Leaflet's map context, just `.map-canvas-wrap` itself as
           * the positioned ancestor.
           *
           * Wave: floating map controls. The toolbar and Layers panel used
           * to be docked-in-flow (a full-width bar above the map, a fixed
           * column beside it) - both are now floating overlays living here
           * instead, so the map canvas gets the full frame. The two toggles
           * sit in their own row; the toolbar/Layers panels (when open) and
           * the measure/draw floating badges are simply later children of
           * this same flex-column overlay, which is what keeps them all
           * stacked without overlap whether one, both, or neither panel is
           * open - no extra positioning math needed. */}
          <div className="map-overlay-topleft">
            <div className="map-floating-toggle-row">
              <button
                type="button"
                className="map-floating-toggle map-floating-toggle-toolbar"
                onClick={toggleToolbar}
                aria-expanded={toolbarOpen}
                aria-label={toolbarOpen ? "Hide map tools" : "Show map tools"}
                title={toolbarOpen ? "Hide map tools" : "Show map tools"}
              >
                <Wrench size={16} strokeWidth={2} className="icon" />
              </button>
              {layers.length > 0 ? (
                <button
                  type="button"
                  className="map-floating-toggle map-floating-toggle-layers"
                  onClick={toggleLayers}
                  aria-expanded={layersOpen}
                  aria-label={layersOpen ? "Hide the Layers panel" : "Show the Layers panel"}
                  title={layersOpen ? "Hide the Layers panel" : "Show the Layers panel"}
                >
                  <LayersIcon size={16} strokeWidth={2} className="icon" />
                </button>
              ) : null}
            </div>
            {toolbarOpen ? (
              <MapToolbar
                onZoomIn={zoomIn}
                onZoomOut={zoomOut}
                onExtent={fitExtent}
                measureMode={measureMode}
                onSelectMeasureMode={selectMeasureMode}
                drawMode={drawMode}
                onSelectDrawMode={selectDrawMode}
                basemapMode={basemapMode}
                onBasemapChange={setBasemapMode}
                compareOptions={compareOptions}
                compare={compare}
                onCompareChange={setCompare}
                onExportImage={exportImage}
                onJump={jumpTo}
                projectId={projectId}
                center={mapView.center}
                zoom={mapView.zoom}
              />
            ) : null}
            {layersOpen && layers.length > 0 ? (
              <LayersPanel
                layers={layers}
                layerState={layerState}
                symbologyState={symbologyState}
                vectorData={vectorData}
                onToggleVisibility={toggle}
                onOpacityChange={setOpacity}
                onSymbologyChange={updateSymbology}
                onRefreshLayers={onRefreshLayers}
                onLegendChanged={onLegendChanged}
                projectId={projectId}
              />
            ) : null}
            <MeasureTools
              mode={measureMode}
              onClear={clearMeasurement}
              result={measureResult}
              pointCount={measurePoints.length}
            />
            <DrawTools
              mode={drawMode}
              points={drawPoints}
              finished={drawFinished}
              onFinish={() => setDrawFinished(true)}
              onReset={() => resetDraw(drawMode)}
              onCancel={() => resetDraw("none")}
              projectId={projectId}
              onRefreshLayers={onRefreshLayers}
            />
          </div>
          <div className="map-overlay-topright">
            <FullscreenToggle active={isFullscreen} onClick={toggleFullscreen} />
          </div>
          {compare ? (
              <div
                className="map-swipe-divider"
                style={{ left: `${swipePct}%` }}
                role="separator"
                aria-label="Drag to compare the two dates"
                onPointerDown={startSwipeDrag}
              >
                <span className="map-swipe-grip" aria-hidden="true" />
              </div>
            ) : null}
            <MapContainer
              bounds={bounds}
              boundsOptions={{ padding: [24, 24] }}
              scrollWheelZoom={false}
              zoomControl={false}
              maxZoom={22}
              className="map-root"
            >
              <ScrollZoomOnActivate />
              <FullscreenInvalidate />
              <MapViewSync onReady={setMapRef} onChange={setMapView} />
              <ScaleControl />
              <CoordinateBadge
                lat={(mapView.pos ?? mapView.center)?.lat}
                lon={(mapView.pos ?? mapView.center)?.lng}
                zoom={mapView.zoom}
                scaleLabel={mapView.zoom != null ? scaleRatioLabel(mapView.zoom, (mapView.pos ?? mapView.center)?.lat ?? 0) : null}
              />
              <MapClickRouter
                mode={measureMode}
                drawMode={drawMode}
                onInspect={inspectPixel}
                onMeasurePoint={addMeasurePoint}
                onDrawPoint={addDrawPoint}
              />
              <MeasureDrawing mode={measureMode} points={measurePoints} />
              <DrawDrawing mode={drawMode} points={drawPoints} />
              {pixelPopup ? (
                <Popup position={pixelPopup.latlng} eventHandlers={{ remove: () => setPixelPopup(null) }}>
                  <div className="pixel-popup">
                    {pixelPopup.loading ? (
                      "Reading pixel…"
                    ) : pixelPopup.rows.length === 0 ? (
                      "No active layer to inspect."
                    ) : (
                      pixelPopup.rows.map((row, i) => (
                        <div className="pixel-popup-row" key={row.gee ? `gee-${row.analysisName}` : row.layer.layer_id ?? i}>
                          <strong>{row.gee ? row.analysisName : `${row.layer.type} · ${row.layer.date_processed ?? "undated"}`}</strong>
                          {row.gee ? renderGeePixelRow(row) : renderPixelRow(row)}
                        </div>
                      ))
                    )}
                  </div>
                </Popup>
              ) : null}
              {/* `key` forces a real remount when the basemap changes.
                * react-leaflet only pushes `url` (and opacity/zIndex) into an
                * existing tile layer - `maxNativeZoom` is a construction-time
                * option it never updates (confirmed in
                * @react-leaflet/core's updateGridLayer/updateTileLayer), so
                * without this the per-source native-zoom cap silently kept
                * whichever basemap was mounted FIRST and the blank-tile bug
                * came back the moment you switched sources. */}
              <TileLayer
                key={basemap.key}
                attribution={basemap.attribution}
                url={basemap.url}
                maxNativeZoom={basemap.maxNativeZoom}
                // "Save image" (exportImage) rasterizes this whole container to a
                // canvas via html-to-image. Every basemap sends a real
                // Access-Control-Allow-Origin header (verified directly), but the
                // browser only honors that for canvas purposes if the <img> itself
                // was fetched in CORS mode - without this, the canvas silently
                // taints and toPng()'s toDataURL() throws a SecurityError the
                // moment any cross-origin basemap tile has loaded.
                crossOrigin="anonymous"
              />
              {/* Compare mode replaces the normal layer stack with exactly the
               * two chosen dates - anything else on top would sit over both
               * sides of the swipe and make the comparison unreadable. */}
              {compare ? (
                <>
                  {renderCompareSide(compare.before, "before")}
                  {renderCompareSide(compare.after, "after")}
                  <SwipeClip layer={swipeLayer} pct={swipePct} />
                </>
              ) : (
                renderGenericLayers(layers)
              )}
              {boundsLayers.map((l) => (
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
              {/* Last child + explicit zIndex so it draws over the project's
                * own tiles rather than depending on add order alone. No
                * tile-error handlers: a GEE tile outside the analysed
                * boundary legitimately comes back empty and must not trip the
                * tile-expiry retry/banner above, which is about OUR signed
                * tile tokens only. `key` forces a real remount when the
                * selected analysis changes - a GEE tile URL carries its own
                * map id, so the whole grid has to be re-requested. */}
              {overlayTileUrl ? (
                <TileLayer key={overlayTileUrl} url={overlayTileUrl} opacity={0.8} zIndex={400} maxZoom={22} />
              ) : null}
            </MapContainer>
        </div>
      </div>
    </div>
  );
}
