/**
 * Vendored MGRS <-> WGS84 conversion for GEO scenes (offline, zero-dependency).
 *
 * Ported to TypeScript from proj4js/mgrs (MIT License) so the desktop app can format
 * and parse Military Grid Reference System coordinates without a runtime dependency or
 * a backend round-trip (the cursor read-out fires on every mousemove, so it must be
 * local and cheap). WGS84 ellipsoid only — MGRS itself is defined on WGS84.
 *
 * Public API:
 *   latLonToMgrs(lat, lon, opts?)  -> "34U DA 12345 67890"
 *   mgrsToLatLon(mgrs)             -> { lat, lon }  (centre of the grid square)
 *   parseCoordinateInput(text)     -> { lat, lon } | null  (MGRS or "lat, lon")
 *
 * MGRS covers 80°S..84°N (UTM band letters C..X, excluding I and O). Outside that,
 * conversion throws / returns null (polar UPS is not supported — irrelevant for EO/SAR).
 */

// --- constants ---------------------------------------------------------------

const A = 65; // A
const I = 73; // I
const O = 79; // O
const V = 86; // V
const Z = 90; // Z

const NUM_100K_SETS = 6;
const SET_ORIGIN_COLUMN_LETTERS = "AJSAJS";
const SET_ORIGIN_ROW_LETTERS = "AFAFAF";

const DEG_2_RAD = Math.PI / 180;
const RAD_2_DEG = 180 / Math.PI;

interface Utm {
  easting: number;
  northing: number;
  zoneNumber: number;
  zoneLetter: string;
}

// --- forward: lat/lon -> MGRS -----------------------------------------------

/** Latitude band letter (C..X, minus I/O). Returns "Z" when outside UTM limits. */
function getLetterDesignator(lat: number): string {
  if (84 >= lat && lat >= 72) return "X";
  if (72 > lat && lat >= 64) return "W";
  if (64 > lat && lat >= 56) return "V";
  if (56 > lat && lat >= 48) return "U";
  if (48 > lat && lat >= 40) return "T";
  if (40 > lat && lat >= 32) return "S";
  if (32 > lat && lat >= 24) return "R";
  if (24 > lat && lat >= 16) return "Q";
  if (16 > lat && lat >= 8) return "P";
  if (8 > lat && lat >= 0) return "N";
  if (0 > lat && lat >= -8) return "M";
  if (-8 > lat && lat >= -16) return "L";
  if (-16 > lat && lat >= -24) return "K";
  if (-24 > lat && lat >= -32) return "J";
  if (-32 > lat && lat >= -40) return "H";
  if (-40 > lat && lat >= -48) return "G";
  if (-48 > lat && lat >= -56) return "F";
  if (-56 > lat && lat >= -64) return "E";
  if (-64 > lat && lat >= -72) return "D";
  if (-72 > lat && lat >= -80) return "C";
  return "Z"; // outside the UTM/MGRS latitude range
}

function llToUtm(lat: number, lon: number): Utm {
  const a = 6378137.0;
  const eccSquared = 0.00669438;
  const k0 = 0.9996;

  const latRad = lat * DEG_2_RAD;
  const lonRad = lon * DEG_2_RAD;

  let zoneNumber = Math.floor((lon + 180) / 6) + 1;
  if (lon === 180) zoneNumber = 60;
  // Norway / Svalbard special zones.
  if (lat >= 56.0 && lat < 64.0 && lon >= 3.0 && lon < 12.0) zoneNumber = 32;
  if (lat >= 72.0 && lat < 84.0) {
    if (lon >= 0.0 && lon < 9.0) zoneNumber = 31;
    else if (lon >= 9.0 && lon < 21.0) zoneNumber = 33;
    else if (lon >= 21.0 && lon < 33.0) zoneNumber = 35;
    else if (lon >= 33.0 && lon < 42.0) zoneNumber = 37;
  }

  const lonOrigin = (zoneNumber - 1) * 6 - 180 + 3;
  const lonOriginRad = lonOrigin * DEG_2_RAD;
  const eccPrimeSquared = eccSquared / (1 - eccSquared);

  const N = a / Math.sqrt(1 - eccSquared * Math.sin(latRad) * Math.sin(latRad));
  const T = Math.tan(latRad) * Math.tan(latRad);
  const C = eccPrimeSquared * Math.cos(latRad) * Math.cos(latRad);
  const A2 = Math.cos(latRad) * (lonRad - lonOriginRad);
  const M =
    a *
    ((1 - eccSquared / 4 - (3 * eccSquared * eccSquared) / 64 - (5 * eccSquared * eccSquared * eccSquared) / 256) * latRad -
      ((3 * eccSquared) / 8 + (3 * eccSquared * eccSquared) / 32 + (45 * eccSquared * eccSquared * eccSquared) / 1024) *
        Math.sin(2 * latRad) +
      ((15 * eccSquared * eccSquared) / 256 + (45 * eccSquared * eccSquared * eccSquared) / 1024) * Math.sin(4 * latRad) -
      ((35 * eccSquared * eccSquared * eccSquared) / 3072) * Math.sin(6 * latRad));

  const easting =
    k0 *
      N *
      (A2 +
        ((1 - T + C) * A2 * A2 * A2) / 6 +
        ((5 - 18 * T + T * T + 72 * C - 58 * eccPrimeSquared) * A2 * A2 * A2 * A2 * A2) / 120) +
    500000.0;

  let northing =
    k0 *
    (M +
      N *
        Math.tan(latRad) *
        ((A2 * A2) / 2 +
          ((5 - T + 9 * C + 4 * C * C) * A2 * A2 * A2 * A2) / 24 +
          ((61 - 58 * T + T * T + 600 * C - 330 * eccPrimeSquared) * A2 * A2 * A2 * A2 * A2 * A2) / 720));
  if (lat < 0.0) northing += 10000000.0;

  return {
    easting: Math.round(easting),
    northing: Math.round(northing),
    zoneNumber,
    zoneLetter: getLetterDesignator(lat),
  };
}

function get100kSetForZone(zoneNumber: number): number {
  let setParm = zoneNumber % NUM_100K_SETS;
  if (setParm === 0) setParm = NUM_100K_SETS;
  return setParm;
}

/** Two-letter 100km square ID for a UTM easting/northing in a zone. */
function get100kId(easting: number, northing: number, zoneNumber: number): string {
  const setParm = get100kSetForZone(zoneNumber);
  const setColumn = Math.floor(easting / 100000);
  const setRow = Math.floor(northing / 100000) % 20;
  return getLetter100kId(setColumn, setRow, setParm);
}

function getLetter100kId(column: number, row: number, parm: number): string {
  const index = parm - 1;
  const colOrigin = SET_ORIGIN_COLUMN_LETTERS.charCodeAt(index);
  const rowOrigin = SET_ORIGIN_ROW_LETTERS.charCodeAt(index);

  let colInt = colOrigin + column - 1;
  let rowInt = rowOrigin + row;
  let rollover = false;

  if (colInt > Z) {
    colInt = colInt - Z + A - 1;
    rollover = true;
  }
  if (colInt === I || (colOrigin < I && colInt > I) || ((colInt > I || colOrigin < I) && rollover)) {
    colInt++;
  }
  if (colInt === O || (colOrigin < O && colInt > O) || ((colInt > O || colOrigin < O) && rollover)) {
    colInt++;
    if (colInt === I) colInt++;
  }
  if (colInt > Z) colInt = colInt - Z + A - 1;

  if (rowInt > V) {
    rowInt = rowInt - V + A - 1;
    rollover = true;
  } else {
    rollover = false;
  }
  if (rowInt === I || (rowOrigin < I && rowInt > I) || ((rowInt > I || rowOrigin < I) && rollover)) {
    rowInt++;
  }
  if (rowInt === O || (rowOrigin < O && rowInt > O) || ((rowInt > O || rowOrigin < O) && rollover)) {
    rowInt++;
    if (rowInt === I) rowInt++;
  }
  if (rowInt > V) rowInt = rowInt - V + A - 1;

  return String.fromCharCode(colInt) + String.fromCharCode(rowInt);
}

export interface MgrsFormatOptions {
  /** Digits per axis: 5 = 1 m (default), 4 = 10 m, 3 = 100 m, ... 0 = 100 km square only. */
  digits?: number;
  /** Insert spaces between grid-zone, 100km square and the two numeric groups (default true). */
  spaced?: boolean;
}

/**
 * Format a WGS84 point as an MGRS string. Returns "" when the point is outside the
 * MGRS latitude range (80°S..84°N) — callers should treat "" as "no MGRS here".
 */
export function latLonToMgrs(lat: number, lon: number, opts: MgrsFormatOptions = {}): string {
  const digits = opts.digits ?? 5;
  const spaced = opts.spaced ?? true;
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || lat > 84 || lat < -80) return "";

  const utm = llToUtm(lat, lon);
  if (utm.zoneLetter === "Z") return "";

  const seasting = "00000" + utm.easting;
  const snorthing = "00000" + utm.northing;
  const square = get100kId(utm.easting, utm.northing, utm.zoneNumber);
  const eastPart = seasting.substr(seasting.length - 5, digits);
  const northPart = snorthing.substr(snorthing.length - 5, digits);

  if (!spaced) return `${utm.zoneNumber}${utm.zoneLetter}${square}${eastPart}${northPart}`;
  const numeric = digits > 0 ? ` ${eastPart} ${northPart}` : "";
  return `${utm.zoneNumber}${utm.zoneLetter} ${square}${numeric}`;
}

// --- inverse: MGRS -> lat/lon -----------------------------------------------

interface DecodedMgrs {
  easting: number;
  northing: number;
  zoneNumber: number;
  zoneLetter: string;
  accuracy: number; // size (m) of the referenced square; 0 for a 100km-only ref
}

function getEastingFromChar(e: string, set: number): number {
  let curCol = SET_ORIGIN_COLUMN_LETTERS.charCodeAt(set - 1);
  let eastingValue = 100000.0;
  let rewind = false;
  while (curCol !== e.charCodeAt(0)) {
    curCol++;
    if (curCol === I) curCol++;
    if (curCol === O) curCol++;
    if (curCol > Z) {
      if (rewind) throw new Error(`Bad MGRS 100km column letter: ${e}`);
      curCol = A;
      rewind = true;
    }
    eastingValue += 100000.0;
  }
  return eastingValue;
}

function getNorthingFromChar(n: string, set: number): number {
  if (n.charCodeAt(0) > V) throw new Error(`Invalid MGRS northing letter: ${n}`);
  let curRow = SET_ORIGIN_ROW_LETTERS.charCodeAt(set - 1);
  let northingValue = 0.0;
  let rewind = false;
  while (curRow !== n.charCodeAt(0)) {
    curRow++;
    if (curRow === I) curRow++;
    if (curRow === O) curRow++;
    if (curRow > V) {
      if (rewind) throw new Error(`Bad MGRS 100km row letter: ${n}`);
      curRow = A;
      rewind = true;
    }
    northingValue += 100000.0;
  }
  return northingValue;
}

function getMinNorthing(zoneLetter: string): number {
  const table: Record<string, number> = {
    C: 1100000.0, D: 2000000.0, E: 2800000.0, F: 3700000.0, G: 4600000.0,
    H: 5500000.0, J: 6400000.0, K: 7300000.0, L: 8200000.0, M: 9100000.0,
    N: 0.0, P: 800000.0, Q: 1500000.0, R: 2300000.0, S: 3200000.0,
    T: 4100000.0, U: 4800000.0, V: 5700000.0, W: 6600000.0, X: 7500000.0,
  };
  const northing = table[zoneLetter];
  if (northing === undefined) throw new Error(`Invalid MGRS zone letter: ${zoneLetter}`);
  return northing;
}

function decodeMgrs(mgrs: string): DecodedMgrs {
  const clean = mgrs.toUpperCase().replace(/\s+/g, "");
  if (clean.length === 0) throw new Error("Empty MGRS string");

  let i = 0;
  let zoneSb = "";
  let ch: string;
  while (!/[A-Z]/.test((ch = clean.charAt(i)))) {
    if (i >= 2) throw new Error(`Bad MGRS zone number: ${mgrs}`);
    zoneSb += ch;
    i++;
  }
  const zoneNumber = parseInt(zoneSb, 10);
  if (i === 0 || i + 3 > clean.length) throw new Error(`MGRS too short: ${mgrs}`);

  const zoneLetter = clean.charAt(i++);
  if (
    zoneLetter <= "A" || zoneLetter === "B" || zoneLetter === "Y" ||
    zoneLetter >= "Z" || zoneLetter === "I" || zoneLetter === "O"
  ) {
    throw new Error(`Invalid MGRS band letter: ${zoneLetter}`);
  }

  const square = clean.substring(i, (i += 2));
  const set = get100kSetForZone(zoneNumber);
  const east100k = getEastingFromChar(square.charAt(0), set);
  let north100k = getNorthingFromChar(square.charAt(1), set);
  while (north100k < getMinNorthing(zoneLetter)) north100k += 2000000;

  const remainder = clean.length - i;
  if (remainder % 2 !== 0) throw new Error(`MGRS numeric part must be even length: ${mgrs}`);
  const sep = remainder / 2;

  let easting = east100k;
  let northing = north100k;
  let accuracy = 0;
  if (sep > 0) {
    accuracy = 100000.0 / Math.pow(10, sep);
    easting += parseFloat(clean.substring(i, i + sep)) * accuracy;
    northing += parseFloat(clean.substring(i + sep)) * accuracy;
  }
  return { easting, northing, zoneNumber, zoneLetter, accuracy };
}

function utmToLatLon(utm: DecodedMgrs): { lat: number; lon: number } {
  const k0 = 0.9996;
  const a = 6378137.0;
  const eccSquared = 0.00669438;
  const e1 = (1 - Math.sqrt(1 - eccSquared)) / (1 + Math.sqrt(1 - eccSquared));

  const x = utm.easting - 500000.0;
  let y = utm.northing;
  if (utm.zoneLetter < "N") y -= 10000000.0; // southern hemisphere

  const lonOrigin = (utm.zoneNumber - 1) * 6 - 180 + 3;
  const eccPrimeSquared = eccSquared / (1 - eccSquared);

  const M = y / k0;
  const mu =
    M / (a * (1 - eccSquared / 4 - (3 * eccSquared * eccSquared) / 64 - (5 * eccSquared * eccSquared * eccSquared) / 256));
  const phi1Rad =
    mu +
    ((3 * e1) / 2 - (27 * e1 * e1 * e1) / 32) * Math.sin(2 * mu) +
    ((21 * e1 * e1) / 16 - (55 * e1 * e1 * e1 * e1) / 32) * Math.sin(4 * mu) +
    ((151 * e1 * e1 * e1) / 96) * Math.sin(6 * mu);

  const N1 = a / Math.sqrt(1 - eccSquared * Math.sin(phi1Rad) * Math.sin(phi1Rad));
  const T1 = Math.tan(phi1Rad) * Math.tan(phi1Rad);
  const C1 = eccPrimeSquared * Math.cos(phi1Rad) * Math.cos(phi1Rad);
  const R1 = (a * (1 - eccSquared)) / Math.pow(1 - eccSquared * Math.sin(phi1Rad) * Math.sin(phi1Rad), 1.5);
  const D = x / (N1 * k0);

  let lat =
    phi1Rad -
    ((N1 * Math.tan(phi1Rad)) / R1) *
      ((D * D) / 2 -
        ((5 + 3 * T1 + 10 * C1 - 4 * C1 * C1 - 9 * eccPrimeSquared) * D * D * D * D) / 24 +
        ((61 + 90 * T1 + 298 * C1 + 45 * T1 * T1 - 252 * eccPrimeSquared - 3 * C1 * C1) * D * D * D * D * D * D) / 720);
  lat = lat * RAD_2_DEG;

  let lon =
    (D -
      ((1 + 2 * T1 + C1) * D * D * D) / 6 +
      ((5 - 2 * C1 + 28 * T1 - 3 * C1 * C1 + 8 * eccPrimeSquared + 24 * T1 * T1) * D * D * D * D * D) / 120) /
    Math.cos(phi1Rad);
  lon = lonOrigin + lon * RAD_2_DEG;

  return { lat, lon };
}

/** Parse an MGRS string and return the CENTRE of the referenced grid square. */
export function mgrsToLatLon(mgrs: string): { lat: number; lon: number } {
  const decoded = decodeMgrs(mgrs);
  const half = decoded.accuracy ? decoded.accuracy / 2 : 0;
  return utmToLatLon({
    ...decoded,
    easting: decoded.easting + half,
    northing: decoded.northing + half,
  });
}

// --- flexible input parsing --------------------------------------------------

/** True if the text looks like an MGRS reference (zone digits + band + square [+ evens]). */
function looksLikeMgrs(text: string): boolean {
  return /^\d{1,2}[C-X][A-HJ-NP-Z]{2}(\d{2}|\d{4}|\d{6}|\d{8}|\d{10})?$/.test(text.toUpperCase().replace(/\s+/g, ""));
}

/**
 * Accept either an MGRS reference or a decimal "lat, lon" (comma- or space-separated,
 * latitude first) and return WGS84 degrees, or null when it cannot be parsed / is
 * out of range. Used by the "go to coordinates" box.
 */
export function parseCoordinateInput(text: string): { lat: number; lon: number } | null {
  const trimmed = text.trim();
  if (!trimmed) return null;

  // Decimal lat/lon first (two signed floats separated by comma and/or whitespace).
  const decimal = trimmed.match(/^(-?\d+(?:\.\d+)?)\s*[,\s]\s*(-?\d+(?:\.\d+)?)$/);
  if (decimal) {
    const lat = parseFloat(decimal[1]);
    const lon = parseFloat(decimal[2]);
    if (Number.isFinite(lat) && Number.isFinite(lon) && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180) {
      return { lat, lon };
    }
    return null;
  }

  if (looksLikeMgrs(trimmed)) {
    try {
      const { lat, lon } = mgrsToLatLon(trimmed);
      if (Number.isFinite(lat) && Number.isFinite(lon)) return { lat, lon };
    } catch {
      return null;
    }
  }
  return null;
}

/** Format a WGS84 point as "lat, lon" with a fixed number of decimals (default 6 ≈ 0.1 m). */
export function formatLatLon(lat: number, lon: number, decimals = 6): string {
  return `${lat.toFixed(decimals)}, ${lon.toFixed(decimals)}`;
}
