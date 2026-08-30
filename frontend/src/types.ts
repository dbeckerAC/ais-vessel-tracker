import type { Feature, LineString } from "geojson";

export type Vessel = {
  mmsi: string;
  active?: boolean;
  personal_label?: string | null;
  display_name?: string | null;
  call_sign?: string | null;
  imo?: number | null;
  ship_type?: number | null;
  destination?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  speed_over_ground_knots?: number | null;
  course_over_ground_degrees?: number | null;
  true_heading_degrees?: number | null;
  navigational_status?: number | null;
  received_at?: string | null;
  position_received_at?: string | null;
  data_provider?: string | null;
  data_source?: string | null;
  source_station?: string | null;
};

export type TrackFeature = {
  type: "Feature";
  geometry: LineString | null;
  properties: {
    mmsi: string;
    source_point_count: number;
    returned_point_count: number;
    simplified: boolean;
    tolerance_m?: number;
    gap_threshold_minutes?: number;
    started_at?: string;
    ended_at?: string;
  };
  segments?: TrackSegment[];
};

export type TrackFitRequest = {
  id: number;
  track: TrackFeature;
};

export type TrackPointData = {
  received_at: string;
  speed_over_ground_knots?: number | null;
  course_over_ground_degrees?: number | null;
  true_heading_degrees?: number | null;
  navigational_status?: number | null;
};

export type TrackSegmentProperties = {
  mmsi: string;
  started_at: string;
  ended_at: string;
  gap_seconds?: number | null;
  is_gap?: boolean;
  speed_knots?: number | null;
  start: TrackPointData;
  end: TrackPointData;
};

export type TrackSegment = Feature<LineString, TrackSegmentProperties>;
