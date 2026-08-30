import type { TrackFeature, Vessel } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `${response.status} ${response.statusText}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function getTrackedVessels(): Promise<Vessel[]> {
  return (await request<{ vessels: Vessel[] }>("/api/v1/tracked-vessels")).vessels;
}

export async function getCurrentVessels(): Promise<Vessel[]> {
  return (await request<{ vessels: Vessel[] }>("/api/v1/vessels")).vessels;
}

export async function addTrackedVessel(mmsi: string, personalLabel?: string): Promise<void> {
  await request("/api/v1/tracked-vessels", {
    method: "POST",
    body: JSON.stringify({ mmsi, personal_label: personalLabel || null }),
  });
}

export async function deactivateTrackedVessel(mmsi: string): Promise<void> {
  await request(`/api/v1/tracked-vessels/${mmsi}`, { method: "DELETE" });
}

export async function getTrack(
  mmsi: string,
  from: string,
  to: string,
  toleranceM = 25,
): Promise<TrackFeature> {
  const query = new URLSearchParams({ from, to, tolerance_m: String(toleranceM) });
  return request(`/api/v1/vessels/${mmsi}/positions?${query}`);
}

export async function getStatus(): Promise<Record<string, any>> {
  return request("/api/v1/status");
}

export function liveSocket(onVessel: (vessel: Vessel) => void, onState: (state: string) => void) {
  let stopped = false;
  let socket: WebSocket | null = null;
  let retry = 1000;

  const connect = () => {
    if (stopped) return;
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${scheme}://${location.host}/ws/v1/vessels`);
    onState("connecting");
    socket.onopen = () => {
      retry = 1000;
      onState("connected");
    };
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "vessel_update") onVessel(message.vessel);
    };
    socket.onerror = () => socket?.close();
    socket.onclose = () => {
      onState("reconnecting");
      if (!stopped) {
        window.setTimeout(connect, retry);
        retry = Math.min(retry * 2, 15_000);
      }
    };
  };
  connect();

  return () => {
    stopped = true;
    socket?.close();
  };
}
