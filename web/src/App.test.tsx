// The screens, with the server's replies made up.

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { App } from "./App";
import type { State } from "./api";
import { THIS_HOUR, hour, threeSegments, windHours } from "./rules/testing";
import type { ListData } from "./rules/types";

const LOGGED_IN: Extract<State, { logged_in: true }> = {
  logged_in: true,
  pooled: false,
  fastest_time: false,
  job: { status: "done", phase: null, done: 0, total: null, resume_at: null, error: null },
  syncing: false,
  show_lists: true,
  last_synced_at: new Date(Date.now() - 2 * 3600e3).toISOString(),
  wind_pending: false,
  busy: false,
  lists_version: "v1",
  this_hour: THIS_HOUR,
  profile: {
    weight_kg: null, power_20min_w: null, default_weight_kg: 75, default_power_20min_w: 200,
    weight_range: [30, 200], power_range: [50, 600],
  },
  contributes: null,
};

function list(overrides: Partial<ListData> = {}): ListData {
  return {
    segments: threeSegments({
      500: { wind_hours: windHours([3.4, -8.6]) },
      600: { town: "Dryden", wind_hours: windHours([-1, -2]), less_certain: true },
    }),
    sort: { field: "your_attempts", direction: "desc" },
    filters: {},
    time_range: null,
    hours: Array.from({ length: 49 }, (_, i) => hour(i)),
    this_hour: THIS_HOUR,
    ...overrides,
  };
}

type Call = { method: string; path: string; body: unknown };

/** Answers the app's calls from `routes`, and records every call. */
function serve(routes: Record<string, unknown>) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    calls.push({ method, path, body: init.body ? JSON.parse(String(init.body)) : undefined });
    const reply = routes[`${method} ${path}`] ?? { ok: true };
    const failed = reply instanceof Response;
    return failed ? reply : new Response(JSON.stringify(reply), { status: 200 });
  }));
  return calls;
}

function setWidth(wide: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: wide && query.includes("min-width"),
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

beforeEach(() => {
  window.location.hash = "";
  setWidth(false);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const cardNames = () => screen.getAllByRole("article").map((card) => within(card).getByRole("button").querySelector("b")!.textContent);

test("a visitor who isn't logged in sees the connect page", async () => {
  serve({ "GET /api/state": { logged_in: false, login_error: "Strava login was cancelled.", pooled: false } });
  render(<App />);

  expect(await screen.findByRole("link", { name: "Connect with Strava" })).toHaveProperty("href", expect.stringContaining("/login"));
  expect(screen.getByRole("alert").textContent).toBe("Strava login was cancelled.");
  expect(screen.getByText("Your data is only shown to you.")).toBeTruthy();
});

test("the connect page explains sharing when Explore is pooled", async () => {
  serve({ "GET /api/state": { logged_in: false, login_error: null, pooled: true } });
  render(<App />);

  expect(await screen.findByText(/segments from your rides that are visible to everyone/)).toBeTruthy();
  expect(screen.queryByText("Your data is only shown to you.")).toBeNull();
});

test("on a phone, each segment is a card with wind now and best hour", async () => {
  serve({ "GET /api/state": LOGGED_IN, "GET /api/lists/my_segments": list() });
  render(<App />);

  const card = await screen.findByRole("button", { name: /Often <Climb>/ });
  expect(card.textContent).toContain("Ithaca");
  expect(card.textContent).toContain("+3%"); // Wind now.
  expect(card.textContent).toContain("−9%"); // Best hour.
  expect(screen.getByRole("button", { name: /Popular Road/ }).querySelector("abbr")?.title).toMatch(/Less certain/);
  expect(cardNames()).toEqual(["Often <Climb>", "Popular Road", "Third Hill"]);
});

test("tapping a card shows its details, and See all hours shows every hour", async () => {
  serve({ "GET /api/state": LOGGED_IN, "GET /api/lists/my_segments": list() });
  render(<App />);

  fireEvent.click(await screen.findByRole("button", { name: /Often <Climb>/ }));
  expect(screen.getByText("Distance").nextSibling?.textContent).toBe("2.0 mi");
  expect(screen.getByRole("link", { name: "View on Strava" })).toHaveProperty("href", "https://www.strava.com/segments/500");

  fireEvent.click(screen.getByRole("button", { name: "See all hours" }));
  const hours = screen.getByRole("dialog", { name: "Often <Climb>" });
  expect(within(hours).getAllByText(/mph from SW/)).toHaveLength(2);
});

test("filtering changes the list as the rider types, and is saved", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const calls = serve({ "GET /api/state": LOGGED_IN, "GET /api/lists/my_segments": list() });
  render(<App />);
  await screen.findByRole("button", { name: /Often <Climb>/ });

  fireEvent.click(screen.getByRole("button", { name: "Filter" }));
  fireEvent.change(screen.getByLabelText("Segment contains"), { target: { value: "road" } });

  expect(cardNames()).toEqual(["Popular Road"]);
  expect(screen.getByRole("button", { name: /Filter/ }).textContent).toBe("Filter(1)");
  await act(() => vi.advanceTimersByTimeAsync(600));
  expect(calls).toContainEqual({ method: "PUT", path: "/api/lists/my_segments/filters", body: { filters: { name: "road" } } });
});

test("a filter box that can't be read says why and changes nothing", async () => {
  serve({ "GET /api/state": LOGGED_IN, "GET /api/lists/my_segments": list() });
  render(<App />);
  await screen.findByRole("button", { name: /Often <Climb>/ });

  fireEvent.click(screen.getByRole("button", { name: "Filter" }));
  fireEvent.change(document.getElementById("distance_min")!, { target: { value: "soon" } });

  expect(screen.getByRole("alert").textContent).toBe("Distance: “soon” isn't a number.");
  expect(cardNames()).toHaveLength(3);
});

test("sorting from the Sort panel reorders the list and is saved", async () => {
  const calls = serve({ "GET /api/state": LOGGED_IN, "GET /api/lists/my_segments": list() });
  render(<App />);
  await screen.findByRole("button", { name: /Often <Climb>/ });

  fireEvent.click(screen.getByRole("button", { name: /Sort/ }));
  fireEvent.click(within(screen.getByRole("dialog", { name: "Sort by" })).getByRole("button", { name: /Wind now/ }));

  expect(cardNames()).toEqual(["Popular Road", "Often <Climb>", "Third Hill"]);
  expect(calls).toContainEqual({ method: "PUT", path: "/api/lists/my_segments/sort", body: { field: "wind_now", direction: "asc" } });
});

test("on a computer, the table's headings sort, and a row opens its details", async () => {
  setWidth(true);
  serve({ "GET /api/state": LOGGED_IN, "GET /api/lists/my_segments": list() });
  render(<App />);
  await screen.findByRole("table");

  const headings = screen.getAllByRole("columnheader").map((th) => th.textContent);
  expect(headings).toEqual(["Open", "Wind now", "Best hour", "Segment", "Town", "Distance", "Avg grade", "Your attempts, 90 days↓"]);

  fireEvent.click(screen.getByRole("button", { name: "Distance" }));
  const rowNames = () => screen.getAllByRole("row").slice(1).map((row) => row.querySelector(".name")?.textContent);
  expect(rowNames()).toEqual(["Often <Climb>", "Popular Road", "Third Hill"]);
  expect(screen.getByRole("columnheader", { name: /Distance/ }).getAttribute("aria-sort")).toBe("descending");

  fireEvent.click(screen.getByRole("button", { name: "Open Popular Road" }));
  expect(screen.getByText("People on Strava").nextSibling?.textContent).toBe("9,000");
});

test("a time range changes best hour and is saved", async () => {
  const calls = serve({
    "GET /api/state": LOGGED_IN,
    "GET /api/lists/my_segments": list(),
    "PUT /api/lists/my_segments/time-range": { time_range: [hour(0), hour(0)] },
  });
  render(<App />);
  await screen.findByRole("button", { name: /Often <Climb>/ });

  fireEvent.click(screen.getByRole("button", { name: /Time/ }));
  fireEvent.change(screen.getByLabelText("To"), { target: { value: "0" } });

  expect(screen.getByRole("button", { name: /Often <Climb>/ }).textContent).not.toContain("−9%");
  expect(calls).toContainEqual({
    method: "PUT", path: "/api/lists/my_segments/time-range", body: { start: hour(0), end: hour(0) },
  });
});

test("while the first sync runs, its progress shows instead of the lists", async () => {
  serve({
    "GET /api/state": {
      ...LOGGED_IN, show_lists: false, syncing: true, busy: true,
      job: { status: "running", phase: "rides", done: 3, total: 10, resume_at: null, error: null },
    },
  });
  render(<App />);

  expect((await screen.findByRole("status")).textContent).toContain("Reading your rides: 3 of 10");
  expect(screen.queryByRole("article")).toBeNull();
});

test("a failed first sync shows Strava's error", async () => {
  serve({
    "GET /api/state": {
      ...LOGGED_IN, show_lists: false,
      job: { status: "failed", phase: null, done: 0, total: null, resume_at: null, error: "Please log in with Strava again." },
    },
  });
  render(<App />);

  expect((await screen.findByRole("alert")).textContent).toBe("Please log in with Strava again.");
});

test("Explore explains that it matches My Segments while sharing is off", async () => {
  window.location.hash = "#explore";
  serve({ "GET /api/state": LOGGED_IN, "GET /api/lists/explore": list() });
  render(<App />);

  expect(await screen.findByText(/other riders' segments will appear once sharing is turned on/)).toBeTruthy();
});

test("the You tab saves weight and power and explains a refusal", async () => {
  const refusal = "Weight must be 30–200 kg and power 50–600 W. Nothing was changed.";
  const calls = serve({
    "GET /api/state": LOGGED_IN,
    "PUT /api/profile": new Response(JSON.stringify({ error: refusal }), { status: 422 }),
  });
  window.location.hash = "#you";
  render(<App />);

  fireEvent.change(await screen.findByLabelText("Weight (kg)"), { target: { value: "5" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  expect((await screen.findByRole("alert")).textContent).toBe(refusal);
  expect(calls).toContainEqual({ method: "PUT", path: "/api/profile", body: { weight_kg: "5", power_20min_w: "" } });
});

test("the sharing switch only shows while Explore is pooled", async () => {
  window.location.hash = "#you";
  serve({ "GET /api/state": { ...LOGGED_IN, pooled: true, contributes: true } });
  render(<App />);

  expect((await screen.findByRole("switch", { name: "Share my segments" })).getAttribute("aria-checked")).toBe("true");
});

test("an empty list says why", async () => {
  serve({ "GET /api/state": LOGGED_IN, "GET /api/lists/my_segments": list({ segments: [] }) });
  render(<App />);

  expect(await screen.findByText("No segments in this area found in your road rides from the last 90 days.")).toBeTruthy();
});
