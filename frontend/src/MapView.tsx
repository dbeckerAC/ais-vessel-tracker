import { useEffect, useRef, useState } from "react";
import { GeoJSONSource, LngLatBounds, Map, NavigationControl } from "maplibre-gl";
import type { MapLayerMouseEvent } from "maplibre-gl";
import type { FeatureCollection, Point } from "geojson";
import type { TrackFeature, TrackFitRequest, Vessel } from "./types";

type Props = {
  vessels: Vessel[];
  track: TrackFeature | null;
  fitTrackRequest: TrackFitRequest | null;
  selectedMmsi: string | null;
  focusRequest: { mmsi: string; id: number } | null;
  showTrackArrows: boolean;
  showSpeedColors: boolean;
  onSelect: (mmsi: string) => void;
};

const emptyCollection: FeatureCollection = { type: "FeatureCollection", features: [] };
const historyColor = "#f0b45d";

function vesselDirection(vessel: Vessel): number | null {
  const direction = vessel.true_heading_degrees ?? vessel.course_over_ground_degrees;
  return direction != null && direction >= 0 && direction < 360 ? direction : null;
}

function compactReportTime(vessel: Vessel): string | null {
  const value = vessel.received_at || vessel.position_received_at;
  if (!value) return null;
  const report = new Date(value);
  if (Number.isNaN(report.getTime())) return null;
  const now = new Date();
  const sameDay = report.toDateString() === now.toDateString();
  return report.toLocaleString([], sameDay
    ? { hour: "2-digit", minute: "2-digit" }
    : { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function vesselArrow(fill: string): ImageData {
  const canvas = document.createElement("canvas");
  canvas.width = 44;
  canvas.height = 44;
  const context = canvas.getContext("2d")!;
  context.beginPath();
  context.moveTo(22, 3);
  context.lineTo(37, 38);
  context.lineTo(22, 31);
  context.lineTo(7, 38);
  context.closePath();
  context.fillStyle = fill;
  context.strokeStyle = "#071b22";
  context.lineWidth = 3;
  context.lineJoin = "round";
  context.fill();
  context.stroke();
  return context.getImageData(0, 0, canvas.width, canvas.height);
}

function trackArrow(fill: string): ImageData {
  const canvas = document.createElement("canvas");
  canvas.width = 24;
  canvas.height = 24;
  const context = canvas.getContext("2d")!;
  context.beginPath();
  context.moveTo(21, 12);
  context.lineTo(4, 4);
  context.lineTo(8, 12);
  context.lineTo(4, 20);
  context.closePath();
  context.fillStyle = fill;
  context.strokeStyle = "#071b22";
  context.lineWidth = 2;
  context.lineJoin = "round";
  context.fill();
  context.stroke();
  return context.getImageData(0, 0, canvas.width, canvas.height);
}

function trackCollection(track: TrackFeature | null, showSpeedColors = false): FeatureCollection {
  if (!track) return emptyCollection;
  if (showSpeedColors && track.segments?.length) {
    return { type: "FeatureCollection", features: track.segments };
  }
  return track.geometry
    ? { type: "FeatureCollection", features: [{ type: "Feature", geometry: track.geometry, properties: track.properties }] }
    : emptyCollection;
}

function borderCollection(track: TrackFeature | null): FeatureCollection {
  if (!track?.segments?.length) return emptyCollection;
  return {
    type: "FeatureCollection",
    features: track.segments.filter((segment) => !segment.properties.is_gap),
  };
}

function gapCollection(track: TrackFeature | null): FeatureCollection {
  if (!track?.segments?.length) return emptyCollection;
  return {
    type: "FeatureCollection",
    features: track.segments.filter((segment) => segment.properties.is_gap),
  };
}

function vesselGeoJSON(vessels: Vessel[]): FeatureCollection<Point> {
  return {
    type: "FeatureCollection",
    features: vessels
      .filter((v) => Number.isFinite(v.latitude) && Number.isFinite(v.longitude))
      .map((v) => {
        const label = v.personal_label || v.display_name || v.mmsi;
        const reportTime = compactReportTime(v);
        const direction = vesselDirection(v);
        return {
          type: "Feature",
          id: v.mmsi,
          geometry: { type: "Point", coordinates: [v.longitude!, v.latitude!] },
          properties: {
            mmsi: v.mmsi,
            label: reportTime ? `${label} · ${reportTime}` : label,
            speed: v.speed_over_ground_knots ?? 0,
            heading: direction ?? 0,
            moving: direction != null && (v.speed_over_ground_knots ?? 0) >= 0.5,
          },
        };
      }),
  };
}

export function MapView({ vessels, track, fitTrackRequest, selectedMmsi, focusRequest, showTrackArrows, showSpeedColors, onSelect }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Map | null>(null);
  const hasFramedVessels = useRef(false);
  const [mapError, setMapError] = useState<string | null>(null);
  const onSelectRef = useRef(onSelect);
  const vesselsRef = useRef(vessels);
  const trackRef = useRef(track);
  const fitTrackRequestRef = useRef(fitTrackRequest);
  const lastFittedTrackRequest = useRef(0);
  const selectedRef = useRef(selectedMmsi);
  const focusRequestRef = useRef(focusRequest);
  const showTrackArrowsRef = useRef(showTrackArrows);
  const showSpeedColorsRef = useRef(showSpeedColors);
  onSelectRef.current = onSelect;
  vesselsRef.current = vessels;
  trackRef.current = track;
  fitTrackRequestRef.current = fitTrackRequest;
  selectedRef.current = selectedMmsi;
  focusRequestRef.current = focusRequest;
  showTrackArrowsRef.current = showTrackArrows;
  showSpeedColorsRef.current = showSpeedColors;

  useEffect(() => {
    if (!container.current || mapRef.current) return;
    const map = new Map({
      container: container.current,
      style: "https://tiles.openfreemap.org/styles/liberty",
      center: [7, 48],
      zoom: 3,
      attributionControl: { compact: true },
    });
    map.addControl(new NavigationControl(), "top-right");
    map.on("error", (event) => {
      setMapError(event.error?.message || "The basemap could not be loaded.");
    });
    map.on("load", () => {
      setMapError(null);
      map.addSource("history", {
        type: "geojson",
        data: trackCollection(trackRef.current, showSpeedColorsRef.current),
      });
      map.addSource("history-border", {
        type: "geojson",
        data: borderCollection(trackRef.current),
      });
      map.addSource("history-gaps", {
        type: "geojson",
        data: gapCollection(trackRef.current),
      });
      map.addLayer({
        id: "history-halo",
        type: "line",
        source: "history-border",
        paint: { "line-color": "#06161c", "line-width": 4, "line-opacity": 0.52 },
      });
      map.addLayer({
        id: "history-gap-halo",
        type: "line",
        source: "history-gaps",
        paint: {
          "line-color": "#06161c",
          "line-width": 4,
          "line-opacity": 0.82,
          "line-dasharray": [2, 2],
        },
      });
      map.addLayer({
        id: "history-line",
        type: "line",
        source: "history",
        paint: { "line-color": historyColor, "line-width": 1.8, "line-opacity": 0.95 },
        layout: { visibility: showSpeedColorsRef.current ? "none" : "visible" },
      });
      map.addLayer({
        id: "history-speed",
        type: "line",
        source: "history",
        paint: {
          "line-color": [
            "interpolate",
            ["linear"],
            ["coalesce", ["get", "speed_knots"], 0],
            0, "#7048a8",
            2.5, "#6559ba",
            5, "#2fbf91",
            7.5, "#a5cf63",
            10, "#e1c45a",
            12.5, "#e7a04f",
            15, "#de7b54",
            17.5, "#d96561",
            20, "#cb5a73",
          ],
          "line-width": 1.8,
          "line-opacity": 0.95,
        },
        layout: { visibility: showSpeedColorsRef.current ? "visible" : "none" },
      });
      map.addSource("history-arrows", {
        type: "geojson",
        data: trackCollection(trackRef.current),
      });
      map.addImage("track-arrow", trackArrow(historyColor));
      map.addLayer({
        id: "history-arrows",
        type: "symbol",
        source: "history-arrows",
        layout: {
          "symbol-placement": "line",
          "symbol-spacing": 160,
          "icon-image": "track-arrow",
          "icon-size": 0.45,
          "icon-allow-overlap": true,
          "icon-ignore-placement": true,
          "visibility": showTrackArrowsRef.current ? "visible" : "none",
        },
      });
      map.addSource("vessels", {
        type: "geojson",
        data: vesselGeoJSON(vesselsRef.current),
        promoteId: "mmsi",
      });
      map.addImage("vessel-arrow", vesselArrow("#58d5ed"));
      map.addImage("vessel-arrow-selected", vesselArrow("#f0b45d"));
      map.addLayer({
        id: "vessels-glow",
        type: "circle",
        source: "vessels",
        paint: {
          "circle-radius": 12,
          "circle-color": "#66e3ff",
          "circle-opacity": 0.2,
        },
      });
      map.addLayer({
        id: "vessels-points",
        type: "circle",
        source: "vessels",
        filter: ["==", ["get", "moving"], false],
        paint: {
          "circle-radius": ["case", ["==", ["get", "mmsi"], selectedRef.current || ""], 8, 6],
          "circle-color": ["case", ["==", ["get", "mmsi"], selectedRef.current || ""], "#f0b45d", "#58d5ed"],
          "circle-stroke-width": 2,
          "circle-stroke-color": "#071b22",
        },
      });
      map.addLayer({
        id: "vessels-direction",
        type: "symbol",
        source: "vessels",
        filter: ["==", ["get", "moving"], true],
        layout: {
          "icon-image": [
            "case",
            ["==", ["get", "mmsi"], selectedRef.current || ""],
            "vessel-arrow-selected",
            "vessel-arrow",
          ],
          "icon-size": 0.72,
          "icon-rotate": ["get", "heading"],
          "icon-rotation-alignment": "map",
          "icon-pitch-alignment": "map",
          "icon-allow-overlap": true,
        },
      });
      map.addLayer({
        id: "vessels-labels",
        type: "symbol",
        source: "vessels",
        layout: {
          "text-field": ["get", "label"],
          "text-size": 12,
          "text-offset": [1.35, 0],
          "text-anchor": "left",
          "text-max-width": 18,
          "text-allow-overlap": false,
          "text-optional": true,
        },
        paint: { "text-color": "#e9f7f8", "text-halo-color": "#071b22", "text-halo-width": 1.5 },
      });
      const selectMapVessel = (event: MapLayerMouseEvent) => {
        const mmsi = event.features?.[0]?.properties?.mmsi;
        if (mmsi) onSelectRef.current(String(mmsi));
      };
      ["vessels-points", "vessels-direction"].forEach((layer) => {
        map.on("click", layer, selectMapVessel);
        map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
      });
      if (focusRequestRef.current) {
        focusVessel(map, vesselsRef.current, focusRequestRef.current.mmsi);
      } else {
        frameVessels(map, vesselsRef.current);
      }
      hasFramedVessels.current = vesselsRef.current.some(
        (vessel) => Number.isFinite(vessel.latitude) && Number.isFinite(vessel.longitude),
      );
      fitTrackIfRequested(map, fitTrackRequestRef.current, lastFittedTrackRequest);
    });
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.isStyleLoaded()) return;
    (map.getSource("vessels") as GeoJSONSource | undefined)?.setData(vesselGeoJSON(vessels));
    if (!hasFramedVessels.current && vessels.some((vessel) => Number.isFinite(vessel.latitude) && Number.isFinite(vessel.longitude))) {
      frameVessels(map, vessels);
      hasFramedVessels.current = true;
    }
    if (map.getLayer("vessels-points")) {
      map.setPaintProperty("vessels-points", "circle-radius", [
        "case", ["==", ["get", "mmsi"], selectedMmsi || ""], 8, 6,
      ]);
      map.setPaintProperty("vessels-points", "circle-color", [
        "case", ["==", ["get", "mmsi"], selectedMmsi || ""], "#f0b45d", "#58d5ed",
      ]);
    }
    if (map.getLayer("vessels-direction")) {
      map.setLayoutProperty("vessels-direction", "icon-image", [
        "case",
        ["==", ["get", "mmsi"], selectedMmsi || ""],
        "vessel-arrow-selected",
        "vessel-arrow",
      ]);
    }
  }, [vessels, selectedMmsi]);

  useEffect(() => {
    const map = mapRef.current;
    if (!focusRequest || !map?.isStyleLoaded()) return;
    focusVessel(map, vesselsRef.current, focusRequest.mmsi);
  }, [focusRequest]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.isStyleLoaded() || !map.getLayer("history-arrows")) return;
    map.setLayoutProperty("history-arrows", "visibility", showTrackArrows ? "visible" : "none");
  }, [showTrackArrows]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.isStyleLoaded()) return;
    const data = trackCollection(trackRef.current, showSpeedColors);
    (map.getSource("history") as GeoJSONSource | undefined)?.setData(data);
    if (map.getLayer("history-line")) {
      map.setLayoutProperty("history-line", "visibility", showSpeedColors ? "none" : "visible");
    }
    if (map.getLayer("history-speed")) {
      map.setLayoutProperty("history-speed", "visibility", showSpeedColors ? "visible" : "none");
    }
  }, [showSpeedColors]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.isStyleLoaded()) return;
    const data = trackCollection(track, showSpeedColorsRef.current);
    (map.getSource("history") as GeoJSONSource | undefined)?.setData(data);
    (map.getSource("history-border") as GeoJSONSource | undefined)?.setData(borderCollection(track));
    (map.getSource("history-gaps") as GeoJSONSource | undefined)?.setData(gapCollection(track));
    (map.getSource("history-arrows") as GeoJSONSource | undefined)?.setData(trackCollection(track));
  }, [track]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !fitTrackRequest) return;
    fitTrackIfRequested(map, fitTrackRequest, lastFittedTrackRequest);
  }, [fitTrackRequest]);

  return (
    <>
      <div className="map" ref={container} aria-label="Live AIS vessel map" />
      {mapError && <div className="map-error">Map error: {mapError}</div>}
    </>
  );
}

function fitTrackIfRequested(
  map: Map,
  request: TrackFitRequest | null,
  lastFittedRequest: { current: number },
) {
  if (!request || request.id <= lastFittedRequest.current) return;
  const track = request.track;
  if (!track?.geometry?.coordinates.length) return;
  const bounds = new LngLatBounds();
  track.geometry.coordinates.forEach((coordinate) => bounds.extend(coordinate as [number, number]));
  map.fitBounds(bounds, { padding: 70, maxZoom: 12, duration: 700 });
  lastFittedRequest.current = request.id;
}

function focusVessel(map: Map, vessels: Vessel[], mmsi: string) {
  const vessel = vessels.find(
    (item) => item.mmsi === mmsi && Number.isFinite(item.latitude) && Number.isFinite(item.longitude),
  );
  if (!vessel) return;
  map.easeTo({
    center: [vessel.longitude!, vessel.latitude!],
    zoom: Math.max(map.getZoom(), 10),
    duration: 700,
  });
}

function frameVessels(map: Map, vessels: Vessel[]) {
  const positioned = vessels.filter(
    (vessel) => Number.isFinite(vessel.latitude) && Number.isFinite(vessel.longitude),
  );
  if (positioned.length === 1) {
    map.easeTo({
      center: [positioned[0].longitude!, positioned[0].latitude!],
      zoom: 8,
      duration: 700,
    });
    return;
  }
  if (positioned.length > 1) {
    const bounds = new LngLatBounds();
    positioned.forEach((vessel) => bounds.extend([vessel.longitude!, vessel.latitude!]));
    map.fitBounds(bounds, { padding: 80, maxZoom: 9, duration: 700 });
  }
}
