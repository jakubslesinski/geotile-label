export interface BasemapConfig {
  id: string;
  name: string;
  url: string;
  attribution: string;
  maxZoom: number;
}

export const BASEMAPS: BasemapConfig[] = [
  {
    id: "esri-imagery",
    name: "Esri World Imagery",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution: "\u00a9 Esri",
    maxZoom: 19,
  },
  {
    id: "osm",
    name: "OpenStreetMap",
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: "\u00a9 OpenStreetMap",
    maxZoom: 19,
  },
];
