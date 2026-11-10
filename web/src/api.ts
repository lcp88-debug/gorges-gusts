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

// Requests on their way, so logging out can wait for them (see `settled`).
let inFlight = 0;
let whenSettled: (() => void)[] = [];

async function call<T>(method: string, path: string, body?: unknown, keepalive = false): Promise<T> {
  inFlight += 1;
  try {
    const response = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      keepalive,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Refused(data.error ?? "Something went wrong. Please try again.");
    }
    return data as T;
  } finally {
    inFlight -= 1;
    if (inFlight === 0) {
      const waiting = whenSettled;
      whenSettled = [];
      waiting.forEach((resolve) => resolve());
    }
  }
}

// The rider's choices are saved one at a time, in the order they were made, so a slow
// reply can't let an older choice land after a newer one. Time ranges share one entry in
// the session cookie, so two at once could also lose one of them.
let saves: Promise<unknown> = Promise.resolve();

function save<T>(method: string, path: string, body?: unknown): Promise<T> {
  const next = saves.then(() => call<T>(method, path, body));
  saves = next.catch(() => undefined);
  return next;
}

export const api = {
  state: () => call<State>("GET", "/api/state"),
  list: (kind: ListKind) => call<ListData>("GET", `/api/lists/${kind}`),
  saveSort: (kind: ListKind, sort: Sort) =>
    save("PUT", `/api/lists/${kind}/sort`, { field: sort.field, direction: sort.direction }),
  saveFilters: (kind: ListKind, filters: Filters) => save("PUT", `/api/lists/${kind}/filters`, { filters }),
  /** For a page that's closing: sent at once, and finished even after the page has gone. */
  saveFiltersNow: (kind: ListKind, filters: Filters) =>
    call("PUT", `/api/lists/${kind}/filters`, { filters }, true),
  saveTimeRange: (kind: ListKind, range: TimeRange) =>
    range
      ? save<{ time_range: TimeRange }>("PUT", `/api/lists/${kind}/time-range`, { start: range[0], end: range[1] })
      : save<{ time_range: TimeRange }>("DELETE", `/api/lists/${kind}/time-range`),
  refresh: () => call("POST", "/api/refresh"),
  saveProfile: (weight: string, power: string) =>
    call("PUT", "/api/profile", { weight_kg: weight, power_20min_w: power }),
  setContributing: (on: boolean) => call("PUT", "/api/contributing", { on }),
  logout: () => call("POST", "/api/logout"),
  /** Resolves once no request is on its way: a reply that lands later can't change the session. */
  settled: (): Promise<void> =>
    saves.then(() => (inFlight === 0 ? undefined : new Promise<void>((resolve) => whenSettled.push(resolve)))),
};
