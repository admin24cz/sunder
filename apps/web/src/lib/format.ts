/**
 * Formatting helpers for training figures.
 *
 * The UI is Czech (spec section 10), so these produce Czech conventions: a
 * comma as the decimal separator and a non-breaking space before the unit, so
 * a value never wraps away from what it measures.
 *
 * Every function accepts `null` and `undefined`, because most metrics are
 * genuinely optional — an indoor run has no GPS trace, a watch worn without a
 * strap records no heart rate — and returning a dash is more useful at every
 * call site than making each one guard first.
 */

const NBSP = ' ';
const EM_DASH = '—';

const csNumber = new Intl.NumberFormat('cs-CZ', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const csInteger = new Intl.NumberFormat('cs-CZ', { maximumFractionDigits: 0 });

/** Format metres as kilometres, e.g. `10,52 km`. */
export function formatDistance(meters: number | null | undefined): string {
  if (meters == null || !Number.isFinite(meters)) return EM_DASH;
  return `${csNumber.format(meters / 1000)}${NBSP}km`;
}

/** Format metres of climb, e.g. `426 m`. */
export function formatElevation(meters: number | null | undefined): string {
  if (meters == null || !Number.isFinite(meters)) return EM_DASH;
  return `${csInteger.format(Math.round(meters))}${NBSP}m`;
}

/**
 * Format a duration as `h:mm:ss`, dropping the hour component below an hour.
 *
 * A 42-minute run reads better as `42:15` than as `0:42:15`, and activities
 * under an hour are the common case.
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return EM_DASH;

  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;

  const paddedSeconds = String(secs).padStart(2, '0');
  if (hours === 0) return `${String(minutes)}:${paddedSeconds}`;
  return `${String(hours)}:${String(minutes).padStart(2, '0')}:${paddedSeconds}`;
}

/**
 * Format running pace as `m:ss/km`.
 *
 * Pace is minutes per kilometre and is never shown with an hour component: a
 * pace slower than 60 min/km is a data error, not a slow runner.
 */
export function formatPace(secondsPerKm: number | null | undefined): string {
  if (secondsPerKm == null || !Number.isFinite(secondsPerKm) || secondsPerKm <= 0) return EM_DASH;

  const total = Math.round(secondsPerKm);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${String(minutes)}:${String(seconds).padStart(2, '0')}/km`;
}

/** Format cycling speed as `km/h`, e.g. `28,4 km/h`. */
export function formatSpeed(metersPerSecond: number | null | undefined): string {
  if (metersPerSecond == null || !Number.isFinite(metersPerSecond) || metersPerSecond < 0) {
    return EM_DASH;
  }
  const kmh = new Intl.NumberFormat('cs-CZ', { maximumFractionDigits: 1 });
  return `${kmh.format(metersPerSecond * 3.6)}${NBSP}km/h`;
}

/** Format a heart rate, e.g. `152 tep./min`. */
export function formatHeartRate(bpm: number | null | undefined): string {
  if (bpm == null || !Number.isFinite(bpm)) return EM_DASH;
  return `${csInteger.format(Math.round(bpm))}${NBSP}tep./min`;
}

/**
 * Format an ISO timestamp as a Czech date and time, e.g. `15. 1. 2026 6:30`.
 *
 * Returns a dash for an unparseable input rather than `Invalid Date`, so bad
 * data from an import renders as missing instead of as noise.
 */
export function formatDateTime(isoTimestamp: string | null | undefined): string {
  if (isoTimestamp == null || isoTimestamp === '') return EM_DASH;

  const date = new Date(isoTimestamp);
  if (Number.isNaN(date.getTime())) return EM_DASH;

  return new Intl.DateTimeFormat('cs-CZ', {
    day: 'numeric',
    month: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date);
}
