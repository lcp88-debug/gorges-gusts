// Segment times as riders read and type them: m:ss, or h:mm:ss for an hour or more.

/** "m:ss" or "h:mm:ss" as seconds, or null for anything else. */
export function parseTime(text: string): number | null {
  const parts = text.split(":");
  if (parts.length < 2 || parts.length > 3 || !parts.every((part) => /^\d+$/.test(part))) {
    return null;
  }
  const numbers = parts.map(Number);
  if (numbers[numbers.length - 1] > 59 || (numbers.length === 3 && numbers[1] > 59)) {
    return null;
  }
  return numbers.reduce((total, number) => total * 60 + number, 0);
}

/** Seconds as m:ss, or h:mm:ss for an hour or more. */
export function formatTime(seconds: number): string {
  const whole = Math.round(seconds);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = whole % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return hours ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${minutes}:${pad(secs)}`;
}
