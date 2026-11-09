// The page for visitors who haven't connected Strava yet.

import connectOrange from "../assets/btn_strava_connect_with_orange.svg";
import connectWhite from "../assets/btn_strava_connect_with_white.svg";
import { Icon } from "./parts";

export function Login({ error, pooled }: { error: string | null; pooled: boolean }) {
  return (
    <div className="wrap login">
      <div className="login-card">
        <div className="brand"><Icon name="gust" size={30} /></div>
        <h1>Gorges Gusts</h1>
        <p className="pitch">See which of your Strava segments the wind is helping today.</p>
        {error && <p className="notice error-notice" role="alert">{error}</p>}
        {/* Strava's own button, unchanged, as its brand guidelines require. */}
        <a className="connect" href="/login">
          <picture>
            <source srcSet={connectWhite} media="(prefers-color-scheme: dark)" />
            <img src={connectOrange} alt="Connect with Strava" width={237} height={48} />
          </picture>
        </a>
        <div className="privacy">
          <p>Strava's permission page asks for access to all your activities, including private ones. Private rides aren't added to your lists.</p>
          <p>Rides and segments are deleted from this app within 7 days.</p>
          {pooled ? (
            <p>
              Explore also includes segments other riders have attempted. In the same way, segments from your rides
              that are visible to everyone appear on other riders' lists, without your name, though friends may be able
              to guess who rode them. You can turn this off at any time after connecting, and still see their segments.
            </p>
          ) : (
            <p>Your data is only shown to you.</p>
          )}
        </div>
      </div>
    </div>
  );
}
