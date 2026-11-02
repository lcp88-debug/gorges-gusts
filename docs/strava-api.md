# Strava API notes

Last checked: 2026-11-01. API Agreement and API Policy versions effective 2026-06-01.

These notes are a summary. The live pages are the source of truth: check them before relying on a rule or limit, and update this file when they differ.

## Sources

- [API reference](https://developers.strava.com/docs/reference/)
- [Getting started](https://developers.strava.com/docs/getting-started/)
- [Rate limits](https://developers.strava.com/docs/rate-limits/)
- [Webhooks](https://developers.strava.com/docs/webhooks/)
- [Brand guidelines](https://developers.strava.com/guidelines/)
- [API Agreement](https://www.strava.com/legal/api)
- [API Policy](https://www.strava.com/legal/api_policy)

Section numbers below refer to the API Policy unless stated otherwise.

## Rules

### Storage and deletion

- Cached Strava Data can be kept for at most 7 days, and the cache must be transient (s.6.2, s.5.5).
- Strava Data, or data derived from it, can't be stored in a "Persistent Index" (s.5.5). The Policy page doesn't define this term.
- Data can't be accumulated through repeated calls into a corpus, dataset, archive or database beyond the app's operational scope (s.5.5).
- Remove a resource from the cache immediately once it's no longer available (s.6.2).
- Reflect data an athlete deletes on Strava within 48 hours (s.6.3).
- Delete a user's data within 30 days of deauthorization, account deletion or their request (s.7.4), and confirm the deletion to them in writing (s.2.5).
- Delete all Strava Data if the Agreement ends (Agreement s.4.4).

### Sharing and display

- Strava Data from a user can be shown only to that user, even if it's public on Strava (s.2.3). s.6.1 words this rule differently for apps with an athlete capacity of 9,999 or less, and s.2.3 has no such exception.
- Nobody else can see a user's data without that user's prior express consent (s.5.13).
- Strava Data can't be processed or disclosed for analytics or insights, even when aggregated or anonymized (s.5.4).
- Strava Data includes segment and leaderboard data (Agreement s.2.3).
- Users must be told what data is collected, how, and how to withdraw consent and request deletion (s.2.1, s.7.2).

### AI

- Strava API Materials and Strava Data can't be used in the development, training, evaluation or operation of any AI Application. This includes grounding, embedding generation, retrieval-augmented generation, and ingestion into a context window (s.5.3).
- Strava Data can't be disclosed to AI Application providers or model developers (s.5.10).
- The Strava MCP is the only exception, for subscribers' own data (s.3.5).

### Competition, charging and branding

- Use can't be competitive with Strava, and apps can't imitate its look or brand identity (s.5.2).
- Apps can't charge users for access to the API Materials, and paid functionality can't substantially duplicate Strava's (s.5.8).
- "Strava" can't appear in the app's name or logo (s.4.1).
- Links back to Strava data must use the text "View on Strava" (brand guidelines).

## Access tiers

| Tier | User limit | How to get it |
|---|---|---|
| Standard, single-player | 1 (the developer) | Default for new apps |
| Standard | 10 | Self-service upgrade in API settings |
| Standard | 9,999 | Submit for review; approval isn't guaranteed |
| Extended Access | Generally 10,000+ | Case-by-case approval from Strava |

- Creating an app requires a Strava subscription (Getting started).
- Standard Tier apps can be subject to a requirement that "the developer or specified end users" keep an active subscription (s.3.3).
- Developer contact: developers@strava.com.

## Rate limits

Limits apply per application, not per user.

| | Read, 15 min | Read, daily | Overall, 15 min | Overall, daily |
|---|---|---|---|---|
| Default | 100 | 1,000 | 200 | 2,000 |
| After upgrading to 10 users | 200 | 2,000 | 400 | 4,000 |

- The 15-minute window resets at 0, 15, 30 and 45 minutes past the hour. The daily limit resets at midnight UTC.
- Going over returns `429 Too Many Requests`, and those requests still count toward the daily limit.
- Current usage is in the `X-RateLimit-*` and `X-ReadRateLimit-*` response headers, as `15-minute,daily`.

## Scopes

- `activity:read`: Everyone and Followers activities.
- `activity:read_all`: also Only Me activities and private segments.

## Endpoints

Paths are relative to `https://www.strava.com/api/v3`. Requests send `Authorization: Bearer <access token>`.

### `GET /athlete/activities`

Lists the athlete's activities as summaries, without segments. Takes `after` (Unix time), `per_page` (up to 200) and `page`.

Fields include `id`, `name`, `sport_type`, `start_date`, `distance`, `trainer`, `manual` and `private`.

### `GET /activities/{id}?include_all_efforts=true`

Returns one activity owned by the athlete, including a `segment_efforts` list.

Each effort has:
- `id`, `name`, `elapsed_time`, `moving_time`, `start_date`, `start_date_local`, `distance`
- `pr_rank`, `kom_rank`, `achievements`, `hidden`, `visibility`, `device_watts`
- `start_index`, `end_index`
- `segment`, a summary

Each effort's `segment` summary has:
- `id`, `name`, `activity_type`, `city`, `state`, `country`
- `distance`, `average_grade`, `maximum_grade`, `elevation_high`, `elevation_low`, `climb_category`
- `start_latlng`, `end_latlng`, `elevation_profile`, `elevation_profiles`
- `hazardous`, `private`, `starred`

The summary has no `map` field, so it doesn't include the polyline.

### `GET /segments/{id}`

Returns the summary fields above, plus:
- `map.polyline`: the route as an encoded polyline
- `total_elevation_gain`, `effort_count`, `athlete_count`, `star_count`, `created_at`, `updated_at`, `local_legend`
- `athlete_segment_stats`: the authenticated athlete's `pr_elapsed_time`, `pr_date`, `pr_activity_id`, `effort_count`
- `xoms`: `kom`, `qom` and `overall` as display text such as `"3:49"`

`read_all` is required for athlete-specific segment information and for private segments.

### Restricted endpoints

- `GET /segments/explore`: returns the top 10 segments matching a query. Extended Access apps only, with granted permission.
- `GET /segment_efforts` and `GET /segment_efforts/{id}`: require a subscription.

The field lists above come from live responses and may not be complete.

## Webhooks

- Each application can have one subscription, covering all its authorized athletes. Manage it at `/push_subscriptions`.
- Getting started says the API terms require webhooks to learn when an athlete deauthorizes the app. The Agreement and Policy don't mention webhooks.
- For new activities, webhooks are encouraged and polling isn't banned. Rate-limit guidance names "Implement webhooks" as the fix for polling that hits the daily limit.

**Validation:** Strava sends a `GET` with `hub.mode`, `hub.challenge` and `hub.verify_token`. Respond within 2 seconds with a `200` and JSON echoing the challenge: `{ "hub.challenge": "..." }`.

**Events:** Strava sends a `POST`. Respond with a `200` within 2 seconds and do further work asynchronously. Delivery gets three attempts in total; the docs don't give the interval.

| Field | Values |
|---|---|
| `object_type` | `activity`, `athlete` |
| `object_id` | Activity or athlete ID |
| `aspect_type` | `create`, `update`, `delete` |
| `updates` | Changed keys: `title`, `type`, `private`; `authorized: "false"` on deauthorization |
| `owner_id` | Athlete ID |
| `subscription_id` | Subscription ID |
| `event_time` | Unix time |

- Events carry IDs and changed keys only, so activity data has to be fetched separately.
- One save can produce several events.
- Activity updates fire only for title, type and privacy changes.
