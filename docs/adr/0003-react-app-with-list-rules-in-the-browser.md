# Rebuild the screens as a React app, with list rules in the browser

Decided 2026-11-08, as part of the UI overhaul.

The screens move from server-rendered pages to a single-page React app. Sorting, filtering and the time range move from `app/lists.py` into the browser, so a change shows instantly with no wait for the server. The server hands the app the list's segments and stores each rider's saved choices. It no longer works out which segments pass a filter or how they're ordered.

The rider chose instant changes, and an app-like feel on a weak signal, over keeping the rules in Python where they're already tested. The rules move to the browser; they aren't copied. Their tests move with them, so there's still only one version of each rule.

## Considered options

- **Server-rendered pages with a little JavaScript**, keeping the forms as a fallback. Much less work, but every change still waits for the server.
- **React with the rules left on the server.** Keeps `app/lists.py` as it is, but each change needs a connection, and live controls (search as you type, dragging the time range) update only when the rider pauses.

## Consequences

- The project gains a JavaScript toolchain. Node is installed through conda, as Postgres is, because the developer's Mac has no Homebrew.
- The careful rules in `app/lists.py` are rewritten in JavaScript: comparing values as the list shows them, rounding time range hours, and keeping saved ticks that have no box. Each existing test needs a JavaScript equivalent before the Python one is removed.
- No list data is kept on the device between visits. An offline copy would store Strava data on the phone, which would need checking against the 7-day limit (s.6.2).
- A clickable mock-up with made-up data comes before the rewrite, so the design can change cheaply.
