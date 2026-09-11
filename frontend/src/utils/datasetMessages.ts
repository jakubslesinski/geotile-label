import type { TFunction } from "i18next";

import type { DatasetAuditCheck, DatasetValidationIssue } from "../types";

export function translateDatasetIssue(t: TFunction, issue: DatasetValidationIssue): string {
  const count = issue.count ?? issue.values?.length ?? 0;
  const values = issue.values?.join(", ") || "";
  switch (issue.code) {
    case "empty_splits":
      return t("Expected splits without images: {{values}}.", { values });
    case "spatial_split_without_georeferencing":
      return t("Spatial split selected without georeferencing; affected scenes use image-block fallback.");
    case "image_split_for_geo_project":
      return t("A GEO project uses image_block_split; spatial_block_split better protects overlapping geographic areas.");
    case "random_tile_leakage_risk":
      return t("random_tile can place neighboring or overlapping tiles in different splits.");
    case "cross_split_source_annotations":
      return t("Source annotations in multiple splits", { count });
    case "near_duplicate_tiles":
      return t("Visually similar tile pairs in different splits", { count });
    case "near_duplicate_scan_errors":
      return t("Tiles that could not be fingerprinted", { count });
    case "near_duplicate_scan_truncated":
      return t("Similarity scanning was limited to keep validation efficient on a large dataset.");
    default:
      return t(issue.message);
  }
}

export function translateAuditRecommendation(t: TFunction, message: string): string {
  return t(message);
}

export function translateAuditCheckMessage(t: TFunction, check: DatasetAuditCheck): string {
  const splitCode = check.check_id.startsWith("split_")
    ? check.check_id.slice("split_".length)
    : null;
  if (splitCode) {
    return translateDatasetIssue(t, {
      code: splitCode,
      severity: check.status,
      message: check.message,
      count: check.count ?? undefined,
      values: check.details,
    });
  }
  if (check.check_id === "near_duplicate_tiles") {
    return check.count
      ? t("Visually similar tile pairs in different splits", { count: check.count })
      : t("No visually similar tile pairs were found across splits.");
  }
  if (check.check_id === "review_coverage_total") {
    return t("Review grid coverage message", {
      percent: formatNumber(check.data?.coverage_pct),
      reviewed: check.data?.reviewed ?? 0,
      active: check.data?.active ?? 0,
      threshold: formatNumber(check.data?.threshold_pct),
    });
  }
  if (check.check_id === "review_coverage_by_scene") {
    return check.count
      ? t("Scenes below review coverage threshold", {
        count: check.count,
        threshold: formatNumber(check.data?.threshold_pct),
      })
      : t("All scenes meet review coverage threshold.");
  }
  if (check.check_id === "unreviewed_grid_cells") {
    return check.count
      ? t("Unreviewed grid cells remain", { count: check.count })
      : t("All active grid cells are reviewed.");
  }
  if (check.check_id === "review_positive_empty_cells") {
    return t("Review grid positive empty balance", {
      positive: check.data?.positive ?? 0,
      empty: check.data?.reviewed_empty ?? 0,
    });
  }
  if (check.check_id === "dataset_uses_unreviewed_tiles") {
    return check.count
      ? t("Dataset uses unchecked grid cells", { count: check.count })
      : t("Dataset uses only reviewed or annotated grid cells.");
  }
  if (check.check_id === "dataset_uses_excluded_tiles") {
    return check.count
      ? t("Dataset contains excluded grid cells", { count: check.count })
      : t("Dataset does not contain excluded grid cells.");
  }
  if (check.check_id === "dataset_intersects_excluded_tiles") {
    return check.count
      ? t("Dataset tiles intersect excluded areas message", { count: check.count })
      : t("Dataset tiles do not intersect excluded areas.");
  }
  if (check.message === "Check passed.") return t("Check passed.");
  if (check.message.startsWith("Affected records:")) {
    return t("Affected records: {{count}}.", { count: check.count ?? 0 });
  }
  return t(check.message);
}

function formatNumber(value: unknown): string {
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) return "0";
  return numberValue.toFixed(numberValue % 1 === 0 ? 0 : 1);
}
