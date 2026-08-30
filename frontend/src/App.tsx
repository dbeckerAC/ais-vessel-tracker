import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  addTrackedVessel,
  deactivateTrackedVessel,
  getCurrentVessels,
  getStatus,
  getTrack,
  getTrackedVessels,
  liveSocket,
} from "./api";
import { MapView } from "./MapView";
import type { TrackFeature, TrackFitRequest, Vessel } from "./types";

type HistoryMode = "recent" | "custom";
type RecentUnit = "days" | "months";

function inputDate(date: Date): string {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function recentStart(end: Date, amount: number, unit: RecentUnit): Date {
  const start = new Date(end);
  if (unit === "months") {
    start.setMonth(start.getMonth() - amount);
  } else {
    start.setDate(start.getDate() - amount);
  }
  return start;
}

function vesselTitle(vessel: Vessel): string {
  return vessel.personal_label || vessel.display_name || vessel.mmsi;
}

function mergeVessel(base: Vessel, update?: Vessel): Vessel {
  if (!update) return base;
  return {
    ...base,
    ...update,
    display_name: update.display_name?.trim() || base.display_name,
  };
}

function valueOrDash(value: string | number | null | undefined): string {
  return value == null || value === "" ? "—" : String(value);
}

function navigationStatus(value: number | null | undefined): string {
  const statuses: Record<number, string> = {
    0: "Under way using engine",
    1: "At anchor",
    2: "Not under command",
    3: "Restricted manoeuvrability",
    4: "Constrained by draught",
    5: "Moored",
    6: "Aground",
    7: "Fishing",
    8: "Under sail",
    14: "AIS-SART active",
    15: "Not defined",
  };
  return value == null ? "—" : statuses[value] || `AIS status ${value}`;
}

function vesselType(value: number | null | undefined): string {
  if (value == null || value === 0) return "—";
  const exact: Record<number, string> = {
    30: "Fishing",
    31: "Towing",
    32: "Large tow",
    33: "Dredging / underwater operations",
    34: "Diving operations",
    35: "Military operations",
    36: "Sailing vessel",
    37: "Pleasure craft",
    50: "Pilot vessel",
    51: "Search and rescue",
    52: "Tug",
    53: "Port tender",
    54: "Anti-pollution vessel",
    55: "Law enforcement",
    58: "Medical transport",
  };
  if (exact[value]) return `${exact[value]} (${value})`;
  if (value >= 40 && value <= 49) return `High-speed craft (${value})`;
  if (value >= 60 && value <= 69) return `Passenger ship (${value})`;
  if (value >= 70 && value <= 79) return `Cargo ship (${value})`;
  if (value >= 80 && value <= 89) return `Tanker (${value})`;
  return `AIS type ${value}`;
}

function reportTime(vessel: Vessel): string | null {
  const value = vessel.received_at || vessel.position_received_at;
  return value ? new Date(value).toLocaleString() : null;
}

function reportSource(vessel: Vessel): string {
  if (vessel.data_provider === "openwaters") {
    return vessel.data_source && vessel.data_source !== "unknown"
      ? `Open Waters · ${vessel.data_source}`
      : "Open Waters";
  }
  return vessel.data_provider === "aisstream" ? "AISStream" : valueOrDash(vessel.data_provider);
}

export function App() {
  const [tracked, setTracked] = useState<Vessel[]>([]);
  const [live, setLive] = useState<Record<string, Vessel>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [mapFocusRequest, setMapFocusRequest] = useState<{ mmsi: string; id: number } | null>(null);
  const [showTrackArrows, setShowTrackArrows] = useState(true);
  const [showSpeedColors, setShowSpeedColors] = useState(false);
  const [track, setTrack] = useState<TrackFeature | null>(null);
  const [trackFitRequest, setTrackFitRequest] = useState<TrackFitRequest | null>(null);
  const [socketState, setSocketState] = useState("connecting");
  const [upstreamState, setUpstreamState] = useState("starting");
  const [openWatersState, setOpenWatersState] = useState("starting");
  const [sampleSeconds, setSampleSeconds] = useState(60);
  const [historyMode, setHistoryMode] = useState<HistoryMode>("recent");
  const [historyShown, setHistoryShown] = useState(false);
  const [recentAmount, setRecentAmount] = useState(7);
  const [recentUnit, setRecentUnit] = useState<RecentUnit>("days");
  const [from, setFrom] = useState(inputDate(new Date(Date.now() - 7 * 86400_000)));
  const [to, setTo] = useState(inputDate(new Date()));
  const [newMmsi, setNewMmsi] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const lastHistoryLiveAt = useRef<string | null>(null);
  const liveRef = useRef(live);
  const busyRef = useRef(busy);
  liveRef.current = live;
  busyRef.current = busy;

  const refreshTracked = async () => {
    const rows = await getTrackedVessels();
    setTracked(rows);
    if (!selected) setSelected(rows.find((row) => row.active)?.mmsi || null);
  };

  useEffect(() => {
    Promise.all([refreshTracked(), getCurrentVessels(), getStatus()])
      .then(([, current, status]) => {
        setLive(Object.fromEntries(current.map((v) => [v.mmsi, v])));
        setUpstreamState(status.stream.state);
        setOpenWatersState(status.openwaters?.state || "unavailable");
        setSampleSeconds(status.history_sample_seconds);
      })
      .catch((error) => setNotice(error.message));
    const stop = liveSocket(
      (vessel) => setLive((previous) => ({
        ...previous,
        [vessel.mmsi]: mergeVessel(previous[vessel.mmsi] || { mmsi: vessel.mmsi }, vessel),
      })),
      setSocketState,
    );
    const statusTimer = window.setInterval(() => {
      getStatus()
        .then((status) => {
          setUpstreamState(status.stream.state);
          setOpenWatersState(status.openwaters?.state || "unavailable");
        })
        .catch(() => {
          setUpstreamState("unavailable");
          setOpenWatersState("unavailable");
        });
    }, 10_000);
    return () => {
      stop();
      window.clearInterval(statusTimer);
    };
  }, []);

  const vessels = useMemo(
    () =>
      tracked
        .filter((item) => item.active)
        .map((item) => ({
          ...mergeVessel(item, live[item.mmsi]),
          personal_label: item.personal_label,
        })),
    [tracked, live],
  );
  const selectedVessel = vessels.find((item) => item.mmsi === selected) || null;
  const reportingCount = vessels.filter(
    (item) => Number.isFinite(item.latitude) && Number.isFinite(item.longitude),
  ).length;

  async function loadHistory(mmsi = selected, rangeFrom?: string, rangeTo?: string, fitTrack = true) {
    if (!mmsi) return;
    setBusy(true);
    setNotice(null);
    try {
      let startDate: Date;
      let endDate: Date;
      if (rangeFrom && rangeTo) {
        startDate = new Date(rangeFrom);
        endDate = new Date(rangeTo);
      } else if (historyMode === "recent") {
        endDate = new Date();
        startDate = recentStart(endDate, recentAmount, recentUnit);
        setFrom(inputDate(startDate));
        setTo(inputDate(endDate));
      } else {
        startDate = new Date(from);
        endDate = new Date(to);
      }
      const start = startDate.toISOString();
      const end = endDate.toISOString();
      const feature = await getTrack(mmsi, start, end);
      setTrack(feature);
      if (fitTrack) {
        setTrackFitRequest((request) => ({ id: (request?.id || 0) + 1, track: feature }));
      }
      setHistoryShown(true);
      if (mmsi === selected) {
        lastHistoryLiveAt.current = live[mmsi]?.received_at || live[mmsi]?.position_received_at || null;
      }
      if (!feature.geometry) {
        setNotice(
          feature.properties.source_point_count === 1
            ? "One position is stored. A track line appears after the second point."
            : "No stored positions in this time range yet.",
        );
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Failed to load history");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (historyMode !== "recent" || !selected || !historyShown) return;

    // History is sampled in the backend, so refresh at most every 10 seconds
    // and only after a newer live position has arrived.
    const timer = window.setInterval(() => {
      const liveVessel = liveRef.current[selected];
      const liveAt = liveVessel?.received_at || liveVessel?.position_received_at || null;
      if (!liveAt || liveAt === lastHistoryLiveAt.current || busyRef.current) return;
      lastHistoryLiveAt.current = liveAt;
      void loadHistory(selected, undefined, undefined, false);
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [historyMode, historyShown, recentAmount, recentUnit, selected]);

  async function addVessel(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setNotice(null);
    try {
      await addTrackedVessel(newMmsi, newLabel);
      setNewMmsi("");
      setNewLabel("");
      await refreshTracked();
      setSelected(newMmsi);
      setAddDialogOpen(false);
      setNotice("Vessel added. Collection begins when AISStream confirms the updated subscription.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Failed to add vessel");
    } finally {
      setBusy(false);
    }
  }

  async function deactivate(mmsi: string) {
    if (!window.confirm(`Stop future collection for MMSI ${mmsi}? Existing history is preserved.`)) return;
    await deactivateTrackedVessel(mmsi);
    await refreshTracked();
    if (selected === mmsi) {
      setSelected(null);
      setTrack(null);
      setHistoryShown(false);
      lastHistoryLiveAt.current = null;
    }
  }

  function selectVessel(mmsi: string) {
    if (mmsi !== selected) {
      setTrack(null);
      setHistoryShown(false);
      lastHistoryLiveAt.current = null;
      setNotice(null);
    }
    setSelected(mmsi);
  }

  function selectVesselFromList(mmsi: string) {
    selectVessel(mmsi);
    setMapFocusRequest((previous) => ({ mmsi, id: (previous?.id || 0) + 1 }));
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <header className="brand">
          <div className="eyebrow">Self-hosted AIS tracker</div>
          <h1>Vessel tracker</h1>
          <p>Live positions and historical tracks.</p>
        </header>

        <div className="status-row">
          <span className={`status-dot ${upstreamState === "streaming" ? "ok" : "warn"}`} />
          <span>AISStream: {upstreamState.replaceAll("_", " ")}</span>
          <span className="socket-state">UI {socketState}</span>
        </div>
        <div className="status-row provider-status">
          <span className={`status-dot ${openWatersState === "streaming" ? "ok" : "warn"}`} />
          <span>Open Waters: {openWatersState.replaceAll("_", " ")}</span>
        </div>
        <p className="stream-hint">
          Both providers push new reports. The newest timestamp wins; duplicates and older fallback positions are ignored.
        </p>

        <div className="add-vessel-row">
          <h2>Add vessel</h2>
          <button
            type="button"
            className="add-vessel-button"
            aria-label="Add vessel"
            onClick={() => setAddDialogOpen(true)}
          >
            +
          </button>
        </div>

        <section className="vessel-list">
          <div className="section-heading">
            <h2>Tracked vessels</h2>
            <span>{reportingCount} reporting · {vessels.length} tracked</span>
          </div>
          {vessels.map((vessel) => (
            <article
              key={vessel.mmsi}
              className={`vessel-card ${selected === vessel.mmsi ? "selected" : ""}`}
              onClick={() => selectVesselFromList(vessel.mmsi)}
            >
              <div>
                <strong>{vesselTitle(vessel)}</strong>
                <small>{vessel.mmsi}{vessel.display_name && vessel.personal_label ? ` · ${vessel.display_name}` : ""}</small>
              </div>
              <div className="vessel-meta">
                {vessel.speed_over_ground_knots != null
                  ? `${vessel.speed_over_ground_knots.toFixed(1)} kn`
                  : "awaiting AIS"}
              </div>
              <button className="icon-button" title="Deactivate" onClick={(event) => { event.stopPropagation(); deactivate(vessel.mmsi); }}>×</button>
            </article>
          ))}
        </section>

        {selectedVessel && (
          <section className="live-panel" aria-live="polite">
            <div className="section-heading">
              <h2>Latest AIS report</h2>
              <span className={reportTime(selectedVessel) ? "live-label" : "waiting-label"}>
                {reportTime(selectedVessel) ? "received" : "awaiting AIS"}
              </span>
            </div>
            <div className="live-title">
              <div>
                <strong>{vesselTitle(selectedVessel)}</strong>
                <span>{selectedVessel.mmsi}</span>
              </div>
              <div className="live-speed">
                <strong>
                  {selectedVessel.speed_over_ground_knots == null
                    ? "—"
                    : selectedVessel.speed_over_ground_knots.toFixed(1)}
                </strong>
                <span>knots</span>
              </div>
            </div>
            {reportTime(selectedVessel) ? (
              <dl className="live-grid">
                <div className="wide"><dt>Last report</dt><dd>{reportTime(selectedVessel)}</dd></div>
                <div className="wide"><dt>Position source</dt><dd>{reportSource(selectedVessel)}</dd></div>
                <div className="wide"><dt>Navigation status</dt><dd>{navigationStatus(selectedVessel.navigational_status)}</dd></div>
                <div><dt>Course</dt><dd>{selectedVessel.course_over_ground_degrees == null ? "—" : `${selectedVessel.course_over_ground_degrees.toFixed(1)}°`}</dd></div>
                <div><dt>Heading</dt><dd>{selectedVessel.true_heading_degrees == null ? "—" : `${selectedVessel.true_heading_degrees}°`}</dd></div>
                <div><dt>Latitude</dt><dd>{selectedVessel.latitude == null ? "—" : selectedVessel.latitude.toFixed(5)}</dd></div>
                <div><dt>Longitude</dt><dd>{selectedVessel.longitude == null ? "—" : selectedVessel.longitude.toFixed(5)}</dd></div>
                <div className="wide"><dt>Destination</dt><dd>{valueOrDash(selectedVessel.destination)}</dd></div>
                <div><dt>Call sign</dt><dd>{valueOrDash(selectedVessel.call_sign)}</dd></div>
                <div><dt>IMO</dt><dd>{valueOrDash(selectedVessel.imo)}</dd></div>
                <div className="wide"><dt>Vessel type</dt><dd>{vesselType(selectedVessel.ship_type)}</dd></div>
              </dl>
            ) : (
              <p className="awaiting-copy">
                No report has been delivered for this MMSI since tracking began.
              </p>
            )}
          </section>
        )}

        {selectedVessel && (
          <section className="history-panel">
            <div className="section-heading"><h2>History</h2><span>{selectedVessel.mmsi}</span></div>
            <div className="history-mode" role="group" aria-label="History range mode">
              <button type="button" className={historyMode === "recent" ? "active" : ""} onClick={() => { setHistoryMode("recent"); setHistoryShown(false); }}>Recent period</button>
              <button type="button" className={historyMode === "custom" ? "active" : ""} onClick={() => { setHistoryMode("custom"); setHistoryShown(false); }}>Custom dates</button>
            </div>
            {historyMode === "recent" ? (
              <div className="recent-grid">
                <label>
                  Last
                  <input
                    type="number"
                    min="1"
                    max="3650"
                    value={recentAmount}
                    onChange={(event) => setRecentAmount(Math.max(1, Number(event.target.value) || 1))}
                  />
                </label>
                <label>
                  Unit
                  <select value={recentUnit} onChange={(event) => setRecentUnit(event.target.value as RecentUnit)}>
                    <option value="days">Days</option>
                    <option value="months">Months</option>
                  </select>
                </label>
                <p>Moving window ending at the time the track is loaded.</p>
              </div>
            ) : (
              <div className="date-grid">
                <label>From<input type="datetime-local" value={from} onChange={(event) => setFrom(event.target.value)} /></label>
                <label>To<input type="datetime-local" value={to} onChange={(event) => setTo(event.target.value)} /></label>
              </div>
            )}
            <button className="secondary" onClick={() => loadHistory()} disabled={busy}>{busy ? "Loading…" : "Show track"}</button>
            {track?.properties && (
              <p className="track-stats">
                {track.properties.source_point_count.toLocaleString()} stored points → {track.properties.returned_point_count.toLocaleString()} rendered
              </p>
            )}
          </section>
        )}
        {notice && <div className="notice">{notice}</div>}
        <footer className="app-footer">
          <span>© 2026 Daniel Becker · MIT</span>
          <span>
            AIS data via AISStream and Open Waters · source terms apply · not for navigation
          </span>
        </footer>
      </aside>
      {addDialogOpen && (
        <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setAddDialogOpen(false);
        }}>
          <div className="add-dialog" role="dialog" aria-modal="true" aria-labelledby="add-vessel-title">
            <div className="dialog-heading">
              <h2 id="add-vessel-title">Add vessel</h2>
              <button type="button" className="icon-button" aria-label="Close" onClick={() => setAddDialogOpen(false)}>×</button>
            </div>
            <form className="add-form dialog-form" onSubmit={addVessel}>
              <label>
                MMSI
                <input
                  autoFocus
                  value={newMmsi}
                  onChange={(event) => setNewMmsi(event.target.value.replace(/\D/g, "").slice(0, 9))}
                  placeholder="9 digits"
                  pattern="[0-9]{9}"
                  required
                />
              </label>
              <label>
                Personal label <span>optional</span>
                <input value={newLabel} onChange={(event) => setNewLabel(event.target.value)} placeholder="e.g. Family boat" />
              </label>
              <button disabled={busy || newMmsi.length !== 9}>{busy ? "Adding…" : "Add vessel"}</button>
            </form>
          </div>
        </div>
      )}
      <section className="map-panel">
        <MapView
          vessels={vessels}
          track={track}
          fitTrackRequest={trackFitRequest}
          selectedMmsi={selected}
          focusRequest={mapFocusRequest}
          showTrackArrows={showTrackArrows}
          showSpeedColors={showSpeedColors}
          onSelect={selectVessel}
        />
        <div className="map-legend">
          <i className="legend-moving" /> Moving
          <i className="legend-still" /> Stationary
          <b /> Historical track
          <label className="map-toggle">
            <input type="checkbox" checked={showTrackArrows} onChange={(event) => setShowTrackArrows(event.target.checked)} />
            Direction
          </label>
          <label className="map-toggle">
            <input type="checkbox" checked={showSpeedColors} onChange={(event) => setShowSpeedColors(event.target.checked)} />
            <i className="legend-speed" /> Speed 0–20 kn
          </label>
        </div>
      </section>
    </main>
  );
}
