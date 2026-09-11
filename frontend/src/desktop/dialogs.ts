import { open, save } from "@tauri-apps/plugin-dialog";
import i18n from "../i18n";

export function isTauriRuntime(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

function normalizeSelection(selection: string | string[] | null): string | null {
  if (!selection) return null;
  return Array.isArray(selection) ? selection[0] || null : selection;
}

function normalizeSelections(selection: string | string[] | null): string[] {
  if (!selection) return [];
  return Array.isArray(selection) ? selection : [selection];
}

export async function openSceneFolderDialog(): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  const selected = await open({
    directory: true,
    multiple: false,
    title: i18n.t("Select scene folder"),
  });
  return normalizeSelection(selected);
}

export async function openClassesFileDialog(): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  const selected = await open({
    multiple: false,
    title: i18n.t("Select classes JSON file"),
    filters: [{ name: "JSON", extensions: ["json"] }],
  });
  return normalizeSelection(selected);
}

export async function openModelFileDialog(): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  const selected = await open({
    multiple: false,
    title: i18n.t("Select YOLO model"),
    filters: [{ name: i18n.t("YOLO model"), extensions: ["pt"] }],
  });
  return normalizeSelection(selected);
}

export async function openSamModelFileDialog(): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  const selected = await open({
    multiple: false,
    title: i18n.t("Select SAM model"),
    filters: [{ name: i18n.t("SAM model"), extensions: ["pt", "pth"] }],
  });
  return normalizeSelection(selected);
}

export async function openSamModelsFolderDialog(): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  const selected = await open({
    directory: true,
    multiple: false,
    title: i18n.t("Select SAM models folder"),
  });
  return normalizeSelection(selected);
}

export async function openDinoModelsFolderDialog(): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  const selected = await open({
    directory: true,
    multiple: false,
    title: i18n.t("Select DINO weights folder"),
  });
  return normalizeSelection(selected);
}

export async function openBackupFileDialog(): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  const selected = await open({
    multiple: false,
    title: i18n.t("Select GeoTile Label backup ZIP"),
    filters: [{ name: i18n.t("GeoTile Label backup"), extensions: ["zip"] }],
  });
  return normalizeSelection(selected);
}

export async function openProjectFolderDialog(): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  const selected = await open({
    directory: true,
    multiple: false,
    title: i18n.t("Select existing GeoTile Label project folder"),
  });
  return normalizeSelection(selected);
}

export async function openProjectLocationDialog(): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  const selected = await open({
    directory: true,
    multiple: false,
    title: i18n.t("Select project storage location"),
  });
  return normalizeSelection(selected);
}

export async function saveDatasetZipDialog(defaultPath?: string): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  const selected = await save({
    title: i18n.t("Save dataset ZIP"),
    defaultPath,
    filters: [{ name: i18n.t("ZIP archive"), extensions: ["zip"] }],
  });
  return selected || null;
}

export async function saveSourceAnnotationsGeoParquetDialog(defaultPath?: string): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  const selected = await save({
    title: i18n.t("Save source annotations GeoParquet"),
    defaultPath,
    filters: [{ name: "GeoParquet", extensions: ["geoparquet", "parquet"] }],
  });
  return selected || null;
}

export async function saveAnnotationPackageDialog(defaultPath?: string): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  const selected = await save({
    title: i18n.t("Save annotation package"),
    defaultPath,
    filters: [{ name: i18n.t("GeoTile annotation package"), extensions: ["zip"] }],
  });
  return selected || null;
}

export async function openAnnotationPackagesDialog(): Promise<string[]> {
  if (!isTauriRuntime()) return [];
  const selected = await open({
    multiple: true,
    title: i18n.t("Select annotation packages"),
    filters: [{ name: i18n.t("GeoTile annotation package"), extensions: ["zip"] }],
  });
  return normalizeSelections(selected);
}

/** Pakiet CUDA bywa dystrybuowany w częściach, więc pozwalamy wskazać wiele plików. */
export async function openCudaPackDialog(): Promise<string[]> {
  if (!isTauriRuntime()) return [];
  const selected = await open({
    multiple: true,
    title: i18n.t("Select all pack files - runtime and base weights"),
    filters: [{ name: i18n.t("GeoTile CUDA pack"), extensions: ["gz", "001", "002", "003", "004"] }],
  });
  return normalizeSelections(selected);
}

export async function saveReviewPackageDialog(defaultPath?: string): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  const selected = await save({
    title: i18n.t("Save review package"),
    defaultPath,
    filters: [{ name: i18n.t("GeoTile review package"), extensions: ["zip"] }],
  });
  return selected || null;
}

export async function openReviewPackagesDialog(): Promise<string[]> {
  if (!isTauriRuntime()) return [];
  const selected = await open({
    multiple: true,
    title: i18n.t("Select review packages"),
    filters: [{ name: i18n.t("GeoTile review package"), extensions: ["zip"] }],
  });
  return normalizeSelections(selected);
}
