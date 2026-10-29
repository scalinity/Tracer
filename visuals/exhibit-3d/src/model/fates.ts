// The four true fates the TRY presets script. They are theatre, declared in the help sheet: the
// model never sees them. It sees only the scans a fate produces (collected at 0, then a receipt and
// a plating scan, or nothing). Minutes from the collection scan. The stalled fate is the specimen of
// architecture.md §3 (received 68, plated 87); the on-time fate takes the normal transit's median,
// 24 min, and the same 19 minutes in the lab.

export type FateId = 'arrives' | 'stalls' | 'lost' | 'unsent';

/** Where the real carrier is. Moving spots carry a progress u from 0 to 1. */
export type Spot =
  | 'ward' // standing in the ward's send sleeve
  | 'launch' // drawn up the sleeve into the tube
  | 'tube' // riding the tube to the diverter
  | 'drop' // falling from the diverter into the station's receiver
  | 'station' // in the receiver, waiting or stalled
  | 'carry' // carried from the receiver to the receiving scanner
  | 'lab' // on the receiving tray, scanned
  | 'branch' // misrouted down the branch toward the lost bin
  | 'bin'; // in the lost bin

/** The model's places, plus the lab once a receipt scan has located the specimen. */
export type Place = 'ward' | 'tube' | 'station' | 'lost' | 'lab';

interface Segment { from: number; to: number; spot: Spot }

export interface Fate {
  id: FateId;
  /** Button label: one or two words. */
  label: string;
  /** Receipt scan minute, or null when none comes. */
  received: number | null;
  /** Plating scan minute, or null. */
  plated: number | null;
  segments: Segment[];
}

const DISPATCH = 6;
const LAUNCHED = 6.6;
const AT_DIVERTER = 10;
const IN_RECEIVER = 10.6;

const toStation: Segment[] = [
  { from: 0, to: DISPATCH, spot: 'ward' },
  { from: DISPATCH, to: LAUNCHED, spot: 'launch' },
  { from: LAUNCHED, to: AT_DIVERTER, spot: 'tube' },
  { from: AT_DIVERTER, to: IN_RECEIVER, spot: 'drop' },
];

export const FATES: Record<FateId, Fate> = {
  arrives: {
    id: 'arrives', label: 'On time', received: 24, plated: 43,
    segments: [...toStation, { from: IN_RECEIVER, to: 22, spot: 'station' }, { from: 22, to: 24, spot: 'carry' }, { from: 24, to: Infinity, spot: 'lab' }],
  },
  stalls: {
    id: 'stalls', label: 'Stalls', received: 68, plated: 87,
    segments: [...toStation, { from: IN_RECEIVER, to: 66, spot: 'station' }, { from: 66, to: 68, spot: 'carry' }, { from: 68, to: Infinity, spot: 'lab' }],
  },
  lost: {
    id: 'lost', label: 'Lost', received: null, plated: null,
    segments: [
      { from: 0, to: DISPATCH, spot: 'ward' },
      { from: DISPATCH, to: LAUNCHED, spot: 'launch' },
      { from: LAUNCHED, to: AT_DIVERTER, spot: 'tube' },
      { from: AT_DIVERTER, to: 11.6, spot: 'branch' },
      { from: 11.6, to: Infinity, spot: 'bin' },
    ],
  },
  unsent: {
    id: 'unsent', label: 'Never sent', received: null, plated: null,
    segments: [{ from: 0, to: Infinity, spot: 'ward' }],
  },
};

export const FATE_IDS: FateId[] = ['arrives', 'stalls', 'lost', 'unsent'];

/** Where the real carrier is at minute t (continuous), with its progress through a moving spot. */
export function truthAt(fate: Fate, t: number): { spot: Spot; u: number } {
  const time = Math.max(0, t);
  for (const s of fate.segments) {
    if (time < s.to) {
      const u = Number.isFinite(s.to) && s.to > s.from ? (time - s.from) / (s.to - s.from) : 0;
      return { spot: s.spot, u: Math.min(1, Math.max(0, u)) };
    }
  }
  const last = fate.segments[fate.segments.length - 1];
  return { spot: last.spot, u: 1 };
}

/** The model place a truth spot belongs to (for the TRUTH mark on the location bars). */
export function placeOfSpot(spot: Spot): Place {
  switch (spot) {
    case 'ward': return 'ward';
    case 'launch': case 'tube': return 'tube';
    case 'drop': case 'station': case 'carry': return 'station';
    case 'lab': return 'lab';
    case 'branch': case 'bin': return 'lost';
  }
}

/** Plain words for the truth label: "stalled at the station". */
export function describeSpot(spot: Spot, t: number, fate: Fate): string {
  switch (spot) {
    case 'ward': return t < DISPATCH ? 'waiting at the ward' : 'still at the ward';
    case 'launch': case 'tube': return 'in the tube';
    case 'drop': return 'arriving at the station';
    case 'station': return t > 25 ? 'stalled at the station' : 'waiting at the station';
    case 'carry': return 'carried to the scanner';
    case 'lab': return fate.plated !== null && t >= fate.plated ? 'plated in the lab' : 'scanned in the lab';
    case 'branch': return 'misrouted';
    case 'bin': return 'in the lost bin';
  }
}
