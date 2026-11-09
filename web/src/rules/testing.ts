// Made-up segments for the rules' tests: the same three the Python tests use.

import type { Segment, WindHour } from "./types";

export const THIS_HOUR = "2026-11-01T12:00:00+00:00";

export function hour(offset: number): string {
  return new Date(Date.parse(THIS_HOUR) + offset * 3600e3).toISOString().replace(".000Z", "+00:00");
}

export function windHours(percents: (number | null)[], from = 0): WindHour[] {
  return percents.flatMap((percent, index) =>
    percent == null ? [] : [{ hour: hour(from + index), percent, speed_ms: 4.4704, from_degrees: 225 }],
  );
}

export function segment(id: number, overrides: Partial<Segment> = {}): Segment {
  return {
    id,
    name: `Segment ${id}`,
    town: null,
    distance_m: 1609.34,
    average_grade: 4.5,
    maximum_grade: null,
    elevation_gain_m: null,
    climb_category: null,
    recent_efforts: 1,
    recent_best: null,
    athlete_count: 10,
    effort_count: 20,
    less_certain: false,
    wind_hours: [],
    ...overrides,
  };
}

/** 500 attempted twice, 600 and 700 once. People on Strava: 600, 700, then 500. */
export function threeSegments(changes: Record<number, Partial<Segment>> = {}): Segment[] {
  return [
    segment(500, { name: "Often <Climb>", town: "Ithaca", distance_m: 3218.68, average_grade: 5.25, recent_efforts: 2, athlete_count: 12, effort_count: 3456, ...changes[500] }),
    segment(600, { name: "Popular Road", average_grade: 1.0, athlete_count: 9000, effort_count: 50000, ...changes[600] }),
    segment(700, { name: "Third Hill", distance_m: 800, average_grade: 2.0, athlete_count: 40, effort_count: 100, ...changes[700] }),
  ];
}

/** Sets one column on each segment: {500: value, 600: value, 700: value}. */
export function withColumn<K extends keyof Segment>(key: K, values: Record<number, Segment[K]>): Record<number, Partial<Segment>> {
  return Object.fromEntries(Object.entries(values).map(([id, value]) => [id, { [key]: value }]));
}

/** Gives each segment a wind effect for this hour only, or none for null. */
export function withWindNow(values: Record<number, number | null>): Record<number, Partial<Segment>> {
  return Object.fromEntries(Object.entries(values).map(([id, value]) => [id, { wind_hours: windHours([value]) }]));
}
