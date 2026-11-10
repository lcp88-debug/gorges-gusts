// The whole app: login, the tabs, and keeping the lists up to date.

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type LoggedIn, type State } from "./api";
import type { ListData, ListKind } from "./rules/types";
import { ListScreen, syncText } from "./screens/ListScreen";
import { Login } from "./screens/Login";
import { ago, Icon, useWide } from "./screens/parts";
import { YouScreen } from "./screens/YouScreen";

type Tab = ListKind | "you";

const TABS: [Tab, string][] = [["my_segments", "My Segments"], ["explore", "Explore"], ["you", "You"]];

// While Strava data or wind numbers are coming in, the app checks often. Otherwise it
// checks now and then, so wind now moves on with the hour.
const BUSY_CHECK_MS = 3000;
const IDLE_CHECK_MS = 5 * 60 * 1000;

function tabFromHash(): Tab {
  const name = window.location.hash.slice(1);
  return name === "explore" || name === "you" ? name : "my_segments";
}

export function App() {
  const wide = useWide();
  const [state, setState] = useState<State | null>(null);
  const [offline, setOffline] = useState(false);
  const [tab, setTab] = useState<Tab>(tabFromHash);
  const [lists, setLists] = useState<Partial<Record<ListKind, ListData & { version: string }>>>({});
  // Lists that failed to load, tried again on the next check that reaches the server.
  const [failed, setFailed] = useState<Partial<Record<ListKind, boolean>>>({});
  // The version of each list being fetched right now, so a fetch isn't started twice.
  const fetching = useRef<Partial<Record<ListKind, string>>>({});
  const loggingOut = useRef(false);

  const check = useCallback(() => {
    if (loggingOut.current) return;
    api.state().then(
      (next) => {
        setState(next);
        setOffline(false);
      },
      () => setOffline(true),
    );
  }, []);

  useEffect(check, [check]);

  // Check again on a timer while the page is in view, and as soon as it's back in view.
  const busy = state?.logged_in === true && state.busy;
  useEffect(() => {
    const timer = window.setInterval(() => document.hidden || check(), busy ? BUSY_CHECK_MS : IDLE_CHECK_MS);
    const visible = () => document.hidden || check();
    document.addEventListener("visibilitychange", visible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [busy, check]);

  useEffect(() => {
    const changed = () => setTab(tabFromHash());
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);

  useEffect(() => {
    document.body.className = wide ? "wide" : "narrow";
  }, [wide]);

  // Fetch the open list, and fetch it again when new rides or wind numbers arrive, or the
  // hour moves on. The rider's own choices on the page are kept.
  const loggedIn = state?.logged_in ? state : null;
  const version = loggedIn ? `${loggedIn.lists_version}|${loggedIn.this_hour}` : null;
  const showLists = loggedIn?.show_lists ?? false;
  // `state` is a dependency so a failed fetch is tried again after the next good check.
  useEffect(() => {
    if (tab === "you" || !version || !showLists || lists[tab]?.version === version) return;
    const kind = tab;
    if (fetching.current[kind] === version) return;
    fetching.current[kind] = version;
    api.list(kind).then(
      (fetched) => {
        setLists((current) => {
          const kept = current[kind];
          return {
            ...current,
            [kind]: { ...fetched, version, sort: kept?.sort ?? fetched.sort, filters: kept?.filters ?? fetched.filters },
          };
        });
        setFailed((current) => ({ ...current, [kind]: false }));
      },
      () => {
        setFailed((current) => ({ ...current, [kind]: true }));
        setOffline(true);
      },
    ).finally(() => {
      if (fetching.current[kind] === version) delete fetching.current[kind];
    });
  }, [tab, version, showLists, lists, state]);

  const remember = useCallback((kind: ListKind, choices: Partial<Pick<ListData, "sort" | "filters" | "time_range">>) => {
    setLists((current) => (current[kind] ? { ...current, [kind]: { ...current[kind]!, ...choices } } : current));
  }, []);

  const refresh = useCallback(() => {
    api.refresh().then(check, () => setOffline(true));
  }, [check]);

  // Checks stop first, and logging out waits for replies already on their way: each one
  // carries the session cookie, and a late one must not log the rider back in.
  const logout = () => {
    loggingOut.current = true;
    setLists({});
    api
      .settled()
      .then(api.logout)
      .then(
        () => setOffline(false),
        () => setOffline(true),
      )
      .finally(() => {
        loggingOut.current = false;
        check();
      });
  };

  const go = (next: Tab) => {
    window.location.hash = next === "my_segments" ? "" : next;
    setTab(next);
    window.scrollTo(0, 0);
  };

  if (state === null) {
    return offline ? <p className="wrap empty">Can't reach Gorges Gusts. Check your connection.</p> : null;
  }
  if (!state.logged_in) return <Login error={state.login_error} pooled={state.pooled} />;

  const data = tab === "you" ? undefined : lists[tab];
  return (
    <>
      <header className="app">
        <div className="wrap">
          <span className="brand"><Icon name="gust" />Gorges Gusts</span>
          {wide && (
            <nav className="head-tabs" aria-label="Lists">
              {TABS.map(([key, label]) => (
                <button key={key} aria-current={tab === key ? "page" : undefined} onClick={() => go(key)}>{label}</button>
              ))}
            </nav>
          )}
          {wide && (
            <div className="freshness">
              <span className="muted small">{syncText(state) ?? `Updated ${ago(state.last_synced_at)}`}</span>
              <button className="btn" disabled={state.syncing} onClick={refresh}>Refresh</button>
            </div>
          )}
        </div>
      </header>

      {offline && <p className="wrap notice error-notice" role="alert">Can't reach Gorges Gusts. Check your connection.</p>}

      {tab === "you" ? (
        <YouScreen state={state} onChanged={check} onRefresh={refresh} onLogout={logout} />
      ) : !state.show_lists ? (
        <FirstLoad state={state} />
      ) : data ? (
        <ListScreen key={tab} kind={tab} state={state} data={data} wide={wide} onRefresh={refresh}
          onChoices={(choices) => remember(tab, choices)} />
      ) : failed[tab] && !offline ? (
        <main className="wrap">
          <p className="notice error-notice" role="alert">
            Couldn't load this list. Trying again…
          </p>
        </main>
      ) : null}

      {!wide && (
        <nav className="bottom" aria-label="Lists">
          {TABS.map(([key, label]) => (
            <button key={key} aria-current={tab === key ? "page" : undefined} onClick={() => go(key)}>
              <Icon name={key} />
              {label}
            </button>
          ))}
        </nav>
      )}
    </>
  );
}

/** Before the first lists exist: how fetching from Strava is going, or why it stopped. */
function FirstLoad({ state }: { state: LoggedIn }) {
  const job = state.job;
  if (job?.status === "failed") {
    return <main className="wrap"><p className="notice error-notice" role="alert">{job.error}</p></main>;
  }
  return (
    <main className="wrap">
      <div className="notice status" role="status">
        {syncText(state) ?? "Checking Strava for your rides…"}
        {job?.total ? <progress value={job.done} max={job.total} /> : null}
      </div>
    </main>
  );
}
