import { describe, expect, it } from 'vitest';

import {
  formatDateTime,
  formatDistance,
  formatDuration,
  formatElevation,
  formatHeartRate,
  formatPace,
  formatSpeed,
} from '@/lib/format';

const EM_DASH = '—';

/** Non-breaking spaces make assertions unreadable; compare on normal ones. */
function normalize(value: string): string {
  // Escaped rather than written literally: a bare U+00A0 in source is invisible
  // and indistinguishable from an ordinary space when reading a diff.
  return value.replace(/\u00A0/g, ' ');
}

describe('formatDistance', () => {
  it('renders metres as kilometres with Czech decimals', () => {
    expect(normalize(formatDistance(10520))).toBe('10,52 km');
    expect(normalize(formatDistance(5000))).toBe('5,00 km');
  });

  it('renders a sub-kilometre distance', () => {
    expect(normalize(formatDistance(450))).toBe('0,45 km');
  });

  it('renders zero rather than treating it as missing', () => {
    expect(normalize(formatDistance(0))).toBe('0,00 km');
  });
});

describe('formatDuration', () => {
  it('drops the hour component below an hour', () => {
    expect(formatDuration(2535)).toBe('42:15');
    expect(formatDuration(59)).toBe('0:59');
  });

  it('includes hours once there are any, zero-padding the minutes', () => {
    expect(formatDuration(3600)).toBe('1:00:00');
    expect(formatDuration(3725)).toBe('1:02:05');
    expect(formatDuration(36000)).toBe('10:00:00');
  });

  it('rounds fractional seconds', () => {
    expect(formatDuration(59.6)).toBe('1:00');
  });

  it('rejects a negative duration as missing data', () => {
    expect(formatDuration(-1)).toBe(EM_DASH);
  });
});

describe('formatPace', () => {
  it('renders minutes per kilometre', () => {
    expect(formatPace(315)).toBe('5:15/km');
    expect(formatPace(240)).toBe('4:00/km');
  });

  it('zero-pads the seconds', () => {
    expect(formatPace(305)).toBe('5:05/km');
  });

  it('treats a zero or negative pace as missing', () => {
    expect(formatPace(0)).toBe(EM_DASH);
    expect(formatPace(-30)).toBe(EM_DASH);
  });
});

describe('formatSpeed', () => {
  it('converts metres per second to km/h', () => {
    expect(normalize(formatSpeed(10))).toBe('36 km/h');
    expect(normalize(formatSpeed(7.9))).toBe('28,4 km/h');
  });
});

describe('formatElevation and formatHeartRate', () => {
  it('round to whole units', () => {
    expect(normalize(formatElevation(426.4))).toBe('426 m');
    expect(normalize(formatHeartRate(152.6))).toBe('153 tep./min');
  });
});

describe('formatDateTime', () => {
  it('renders an ISO timestamp in Czech convention', () => {
    // Asserted loosely: the exact separators are the platform ICU's business,
    // and pinning them would make this test fail on a Node upgrade.
    const formatted = normalize(formatDateTime('2026-01-15T06:30:00Z'));
    expect(formatted).toContain('2026');
    expect(formatted).toContain('15');
  });

  it('renders unparseable input as missing rather than "Invalid Date"', () => {
    expect(formatDateTime('not a date')).toBe(EM_DASH);
    expect(formatDateTime('')).toBe(EM_DASH);
  });
});

describe('missing values', () => {
  it.each([
    ['formatDistance', formatDistance],
    ['formatElevation', formatElevation],
    ['formatDuration', formatDuration],
    ['formatPace', formatPace],
    ['formatSpeed', formatSpeed],
    ['formatHeartRate', formatHeartRate],
  ])('%s renders null, undefined and NaN as a dash', (_name, format) => {
    expect(format(null)).toBe(EM_DASH);
    expect(format(undefined)).toBe(EM_DASH);
    expect(format(Number.NaN)).toBe(EM_DASH);
    expect(format(Number.POSITIVE_INFINITY)).toBe(EM_DASH);
  });
});
