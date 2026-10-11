/**
 * Time, as the account experiences it.
 *
 * A demonstration account replays a 2020 recording: its "now" is the replay position,
 * and the recording's UTC-labelled timestamps are the participant's local wall clock.
 * Showing those as calendar dates would present old data as today's, so they are shown
 * as "Day N" of the recording instead. Every other account uses real time in its own
 * timezone.
 */
import type { ReplayState } from "./types";

const DAY_MS = 86_400_000;

export interface Clock {
  isReplay: boolean;
  /** The account's present moment. */
  now: () => Date;
  time: (iso: string | null | undefined) => string;
  day: (iso: string | null | undefined) => string;
  dateTime: (iso: string | null | undefined) => string;
  /** "12 min ago", relative to the account's present moment. */
  ago: (iso: string | null | undefined) => string;
  timezone: string;
}

const utcDay = (date: Date) =>
  Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());

function safeFormatter(options: Intl.DateTimeFormatOptions, timezone: string) {
  try {
    return new Intl.DateTimeFormat("en-US", { ...options, timeZone: timezone });
  } catch {
    return new Intl.DateTimeFormat("en-US", { ...options, timeZone: "UTC" });
  }
}

function relative(from: Date, to: Date): string {
  const minutes = Math.round((to.getTime() - from.getTime()) / 60000);
  if (minutes < 0) return `in ${formatSpan(-minutes)}`;
  if (minutes < 1) return "just now";
  return `${formatSpan(minutes)} ago`;
}

export function formatSpan(minutes: number): string {
  if (minutes < 60) return `${Math.round(minutes)} min`;
  if (minutes < 1440) {
    const hours = Math.floor(minutes / 60);
    const rest = Math.round(minutes - hours * 60);
    return rest ? `${hours} h ${rest} min` : `${hours} h`;
  }
  const days = Math.round(minutes / 1440);
  return `${days} day${days === 1 ? "" : "s"}`;
}

export function makeClock(replay: ReplayState | null, timezone: string): Clock {
  if (replay) {
    const origin = utcDay(new Date(replay.window.start));
    const time = safeFormatter({ hour: "numeric", minute: "2-digit" }, "UTC");
    const day = (iso: string | null | undefined) => {
      if (!iso) return "–";
      const number = Math.floor((utcDay(new Date(iso)) - origin) / DAY_MS) + 1;
      // The recording holds some history from before its first presented day.
      if (number < 1) return `${1 - number} day${number === 0 ? "" : "s"} before Day 1`;
      return `Day ${number}`;
    };
    const now = () => new Date(replay.position);
    return {
      isReplay: true,
      now,
      timezone: "recording time",
      time: (iso) => (iso ? time.format(new Date(iso)) : "–"),
      day,
      dateTime: (iso) => (iso ? `${day(iso)}, ${time.format(new Date(iso))}` : "–"),
      ago: (iso) => (iso ? relative(new Date(iso), now()) : "–"),
    };
  }
  const time = safeFormatter({ hour: "numeric", minute: "2-digit" }, timezone);
  const date = safeFormatter({ weekday: "short", month: "short", day: "numeric" }, timezone);
  const key = safeFormatter({ year: "numeric", month: "2-digit", day: "2-digit" }, timezone);
  const day = (iso: string | null | undefined) => {
    if (!iso) return "–";
    const value = new Date(iso);
    if (key.format(value) === key.format(new Date())) return "Today";
    if (key.format(value) === key.format(new Date(Date.now() - DAY_MS))) return "Yesterday";
    return date.format(value);
  };
  return {
    isReplay: false,
    now: () => new Date(),
    timezone,
    time: (iso) => (iso ? time.format(new Date(iso)) : "–"),
    day,
    dateTime: (iso) => (iso ? `${day(iso)}, ${time.format(new Date(iso))}` : "–"),
    ago: (iso) => (iso ? relative(new Date(iso), new Date()) : "–"),
  };
}

/** An instant as the backend wants it: RFC 3339 with a UTC offset, to the second. */
export const toInstant = (date: Date) => date.toISOString().replace(/\.\d{3}Z$/, "Z");
