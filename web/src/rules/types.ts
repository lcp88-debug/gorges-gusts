// What the server sends (app/api.py), and the list rows the rules work on.

export type ListKind = "my_segments" | "explore";
export type Direction = "asc" | "desc";

export interface WindHour {
  hour: string; // ISO, UTC
  percent: number;
  speed_ms: number;
  from_degrees: number;
}

export interface Segment {
  id: number;
  name: string;
  town: string | null;
  distance_m: number | null;
  average_grade: number | null;
  maximum_grade: number | null;
  elevation_gain_m: number | null;
  climb_category: number | null;
  recent_efforts: number | null;
  recent_best: number | null;
  athlete_count: number | null;
  effort_count: number | null;
  fastest_time_s?: number | null;
  less_certain: boolean;
  wind_hours: WindHour[];
}

// A segment as a list shows it: its wind now, and its best hour inside the time range.
export interface Row extends Segment {
  wind_now: WindHour | null;
  wind_best: WindHour | null;
}

export interface Sort {
  field: string;
  direction: Direction;
}

// Saved filters, {field key: filter}, as the server stores them (see app/lists.py):
//   number: {"min": 1.5, "max": 4}  in the units the list shows (seconds for a time)
//   ticks:  ["Ithaca", "Dryden"] or [0, 4, 5]
//   search: "gorge"
export type NumberFilter = { min?: number; max?: number };
export type TickValue = string | number;
export type Filter = NumberFilter | TickValue[] | string;
export type Filters = Record<string, Filter>;

// [first hour, last hour], both included, as ISO strings. null is all 48 hours.
export type TimeRange = [string, string] | null;

export interface ListData {
  segments: Segment[];
  sort: Sort;
  filters: Filters;
  time_range: TimeRange;
  hours: string[];
  this_hour: string;
}
