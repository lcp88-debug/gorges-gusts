// The You tab: weight and power, sharing, refresh, log out and the data credits.

import { useState } from "react";
import { api, Refused, type LoggedIn } from "../api";
import { syncText } from "./ListScreen";
import { ago } from "./parts";

export function YouScreen({ state, onChanged, onRefresh, onLogout }: {
  state: LoggedIn;
  onChanged: () => void;
  onRefresh: () => void;
  onLogout: () => void;
}) {
  const profile = state.profile;
  const [weight, setWeight] = useState(profile.weight_kg?.toString() ?? "");
  const [power, setPower] = useState(profile.power_20min_w?.toString() ?? "");
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);

  function save(event: React.FormEvent) {
    event.preventDefault();
    api.saveProfile(weight, power).then(
      () => {
        setMessage({ error: false, text: "Saved. Your wind numbers are being worked out again." });
        onChanged();
      },
      (error) => setMessage({ error: true, text: error instanceof Refused ? error.message : "Couldn't save. Check your connection." }),
    );
  }

  function share(on: boolean) {
    api.setContributing(on).then(onChanged, () => setMessage({ error: true, text: "Couldn't change sharing. Check your connection." }));
  }

  const syncing = syncText(state);
  return (
    <main className="wrap">
      <div className="you">
        {/* noValidate: the server's message says what the limits are, in the app's own words. */}
        <form className="panel" onSubmit={save} noValidate>
          <h2>Your weight and power</h2>
          <div className="profile">
            <label className="field">Weight (kg)
              <input id="weight_kg" type="number" step="0.1" min={profile.weight_range[0]} max={profile.weight_range[1]}
                placeholder={String(Math.round(profile.default_weight_kg))} value={weight} onChange={(event) => setWeight(event.target.value)} />
            </label>
            <label className="field">Best 20-minute power (W)
              <input id="power_20min_w" type="number" step="1" min={profile.power_range[0]} max={profile.power_range[1]}
                placeholder={String(Math.round(profile.default_power_20min_w))} value={power} onChange={(event) => setPower(event.target.value)} />
            </label>
          </div>
          <p className="muted small">Leave a box empty to use the standard value shown in it.</p>
          {message && <p className={message.error ? "error" : "saved"} role={message.error ? "alert" : "status"}>{message.text}</p>}
          <div><button className="btn">Save</button></div>
        </form>

        {state.pooled && (
          <section className="panel">
            <div className="switch-row">
              <div>
                <h2>Share my segments</h2>
                <p className="muted small" style={{ marginTop: ".35rem" }}>
                  {state.contributes
                    ? "Segments from your rides that are visible to everyone can appear on other riders' Explore lists, without your name."
                    : "Segments from your rides don't appear on other riders' lists."}
                </p>
              </div>
              <button className="switch" role="switch" aria-checked={!!state.contributes} aria-label="Share my segments"
                onClick={() => share(!state.contributes)} />
            </div>
          </section>
        )}

        <section className="panel">
          <h2>Strava</h2>
          <p className="muted small" role="status">{syncing ?? `Updated ${ago(state.last_synced_at)}.`}</p>
          {state.job?.status === "failed" && state.job.error && <p className="error" role="alert">{state.job.error}</p>}
          <div style={{ display: "flex", gap: ".5rem", flexWrap: "wrap" }}>
            <button className="btn primary" disabled={state.syncing} onClick={onRefresh}>Refresh</button>
            <button className="btn" onClick={onLogout}>Log out</button>
          </div>
        </section>

        <p className="credits">
          Weather data by <a href="https://open-meteo.com/" target="_blank" rel="noopener">Open-Meteo</a>.
          Land cover: © ESA WorldCover project 2021, contains modified Copernicus Sentinel data (2021) processed by the ESA WorldCover consortium.
          Elevation: Copernicus DEM GLO-30, © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018, provided under COPERNICUS by the European Union and ESA.
        </p>
      </div>
    </main>
  );
}
