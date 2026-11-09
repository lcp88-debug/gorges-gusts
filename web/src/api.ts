// Calls to the server's JSON routes (app/api.py).

import type { Filters, ListData, ListKind, Sort, TimeRange } from "./rules/types";

export interface Job {
  status: "pending" | "running" | "paused" | "done" | "failed";
  phase: string | null;
  done: number;
  total: number | null;
  resume_at: string | null;
  error: string | null;
}

export interface Profile {
  weight_kg: number | null;
  power_20min_w: number | null;
  default_weight_kg: number;
  default_power_20min_w: number;
  weight_range: [number, number];
  power_range: [number, number];
}

export type State =
  | { logged_in: false; login_error: string | null; pooled: boolean }
  | {
      logged_in: true;
      pooled: boolean;
      fastest_time: boolean;
      job: Job | null;
      syncing: boolean;
      show_lists: boolean;
      last_synced_at: string | null;
      wind_pending: boolean;
      busy: boolean;
      lists_version: string;
      this_hour: string;
      profile: Profile;
      contributes: boolean | null;
    };

export type LoggedIn = Extract<State, { logged_in: true }>;

/** A change the server refused, with its message for the rider. */
export class Refused extends Error {}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Refused(data.error ?? "Something went wrong. Please try again.");
  }
  return data as T;
}

export const api = {
  state: () => call<State>("GET", "/api/state"),
  list: (kind: ListKind) => call<ListData>("GET", `/api/lists/${kind}`),
  saveSort: (kind: ListKind, sort: Sort) =>
    call("PUT", `/api/lists/${kind}/sort`, { field: sort.field, direction: sort.direction }),
  saveFilters: (kind: ListKind, filters: Filters) => call("PUT", `/api/lists/${kind}/filters`, { filters }),
  saveTimeRange: (kind: ListKind, range: TimeRange) =>
    range
      ? call<{ time_range: TimeRange }>("PUT", `/api/lists/${kind}/time-range`, { start: range[0], end: range[1] })
      : call<{ time_range: TimeRange }>("DELETE", `/api/lists/${kind}/time-range`),
  refresh: () => call("POST", "/api/refresh"),
  saveProfile: (weight: string, power: string) =>
    call("PUT", "/api/profile", { weight_kg: weight, power_20min_w: power }),
  setContributing: (on: boolean) => call("PUT", "/api/contributing", { on }),
  logout: () => call("POST", "/api/logout"),
};
