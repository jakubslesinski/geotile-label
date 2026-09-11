// Stała paleta dla przebiegów treningu w dashboardzie porównawczym (Results).
// Kolor przypisujemy deterministycznie wg kolejności zaznaczenia — spójnie w railu,
// krzywych, scorecard i radarze. Zbiór (Set) w JS zachowuje kolejność wstawiania,
// więc `[...selectedRunIds]` daje stabilną kolejność dla przydziału.

export const RUN_PALETTE = [
  "#7C4FE0", // akcent aplikacji
  "#2DB4A6",
  "#E8973A",
  "#4C8DFF",
  "#E5556A",
  "#9B6CFF",
  "#3BB273",
  "#D96BB0",
  "#C0A020",
  "#5AC8E0",
];

/** Mapa runId -> kolor, przydzielana wg kolejności podanych identyfikatorów. */
export function assignRunColors(orderedRunIds: string[]): Map<string, string> {
  const map = new Map<string, string>();
  orderedRunIds.forEach((runId, index) => {
    map.set(runId, RUN_PALETTE[index % RUN_PALETTE.length]);
  });
  return map;
}
