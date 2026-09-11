import {
  Alert,
  AlertIcon,
  Badge,
  Box,
  Button,
  HStack,
  Spinner,
  SimpleGrid,
  Table,
  TableContainer,
  Tbody,
  Td,
  Text,
  Th,
  Thead,
  Tr,
  VStack,
  useColorModeValue,
  useToast,
} from "@chakra-ui/react";
import { MdDownload, MdRefresh } from "react-icons/md";

import * as api from "../../api/client";
import type {
  DatasetAuditCheck,
  DatasetAuditReport,
} from "../../types";
import { isTauriRuntime } from "../../desktop/dialogs";
import Card from "../common/Card";
import InfoPopover from "../common/InfoPopover";
import MiniStatistics from "../common/MiniStatistics";
import { useTranslation } from "react-i18next";
import i18n from "../../i18n";
import {
  translateAuditCheckMessage,
  translateAuditRecommendation,
} from "../../utils/datasetMessages";

interface Props {
  projectId: string;
  runId?: string | null;
  audit: DatasetAuditReport | null;
  isLoading?: boolean;
  isRefreshing?: boolean;
  onRefresh: () => Promise<void>;
}

export default function DatasetAuditPanel({
  projectId,
  runId,
  audit,
  isLoading = false,
  isRefreshing = false,
  onRefresh,
}: Props) {
  const { t } = useTranslation();
  const textColor = useColorModeValue("navy.700", "white");
  const mutedColor = useColorModeValue("secondaryGray.600", "whiteAlpha.700");
  const toast = useToast();

  const saveAudit = async (format: "json" | "csv") => {
    try {
      if (!isTauriRuntime()) {
        window.open(api.datasetAuditExportUrl(projectId, format, runId), "_blank");
        return;
      }
      const { save } = await import("@tauri-apps/plugin-dialog");
      const selected = await save({
        title: t("Save dataset audit format", { format: format.toUpperCase() }),
        defaultPath: `GeoTileLabel_audit_${runId || "latest"}.${format}`,
        filters: [{ name: format.toUpperCase(), extensions: [format] }],
      });
      if (!selected) return;
      const result = await api.saveDatasetAudit(projectId, selected, format, runId);
      toast({
        title: t("Audit format saved", { format: format.toUpperCase() }),
        description: result.output_path,
        status: "success",
        duration: 3000,
      });
    } catch (error: any) {
      toast({
        title: t("Cannot save audit report"),
        description: error?.response?.data?.detail || error?.message,
        status: "error",
      });
    }
  };

  if (!audit) {
    return (
      <Card>
        {isLoading ? (
          <HStack spacing={3} py={2}>
            <Spinner size="sm" color="brand.500" />
            <Text color={mutedColor}>{t("Loading dataset audit…")}</Text>
          </HStack>
        ) : (
          <HStack justify="space-between">
            <Text color={mutedColor}>{t("Generate and select a dataset run to audit it.")}</Text>
            <Button size="sm" leftIcon={<MdRefresh />} onClick={onRefresh} isDisabled={!runId}>
              {t("Run audit")}
            </Button>
          </HStack>
        )}
      </Card>
    );
  }

  const summary = audit.summary;
  const annotationSummary = audit.annotation_summary;
  const preprocessing = audit.distributions.by_preprocessing[0];
  const checkGroups = groupChecks(audit.checks);

  return (
    <VStack align="stretch" spacing={4}>
      <Card>
        <HStack justify="space-between" align="start" flexWrap="wrap" gap={3}>
          <Box>
            <HStack>
              <Text fontWeight="bold" color={textColor}>{t("Dataset Audit")}</Text>
              <InfoPopover titleKey="info.audit.readiness.title" bodyKey="info.audit.readiness.body" />
              <Badge colorScheme={statusColor(audit.status)}>{t(audit.status)}</Badge>
              <Badge colorScheme={readinessColor(audit.readiness)}>{t(audit.readiness)}</Badge>
            </HStack>
            <Text mt={1} fontSize="xs" color={mutedColor}>
              {t("Run")}: {audit.run_id || t("latest")} · {t("generated")} {new Date(audit.generated_at).toLocaleString(i18n.language.startsWith("pl") ? "pl-PL" : "en-US")}
            </Text>
            {preprocessing && (
              <Text fontSize="xs" color={mutedColor}>
                {t("Preprocessing")}: {String(preprocessing.profile_id || t("unknown"))}
                {preprocessing.profile_hash ? ` · ${String(preprocessing.profile_hash).slice(0, 12)}` : ""}
              </Text>
            )}
          </Box>
          <HStack>
            <Button size="sm" leftIcon={<MdRefresh />} onClick={onRefresh} isLoading={isRefreshing}>
              {t("Refresh")}
            </Button>
            <Button size="sm" leftIcon={<MdDownload />} variant="outline" onClick={() => saveAudit("json")}>
              JSON
            </Button>
            <Button size="sm" leftIcon={<MdDownload />} variant="outline" onClick={() => saveAudit("csv")}>
              CSV
            </Button>
          </HStack>
        </HStack>
      </Card>

      <SimpleGrid columns={{ base: 2, md: 5 }} gap={4}>
        <MiniStatistics
          label={t("Quality Score")}
          value={`${audit.quality_score}/100`}
          infoTitleKey="info.audit.qualityScore.title"
          infoBodyKey="info.audit.qualityScore.body"
        />
        <MiniStatistics
          label={t("Passed")}
          value={summary.passed}
          infoTitleKey="info.audit.passed.title"
          infoBodyKey="info.audit.passed.body"
        />
        <MiniStatistics
          label={t("Warnings")}
          value={summary.warnings}
          infoTitleKey="info.audit.warnings.title"
          infoBodyKey="info.audit.warnings.body"
        />
        <MiniStatistics
          label={t("Errors")}
          value={summary.errors}
          infoTitleKey="info.audit.errors.title"
          infoBodyKey="info.audit.errors.body"
        />
        <MiniStatistics
          label={t("Checks")}
          value={summary.total_checks}
          infoTitleKey="info.audit.checks.title"
          infoBodyKey="info.audit.checks.body"
        />
      </SimpleGrid>

      {audit.recommendations.length > 0 && (
        <VStack align="stretch" spacing={2}>
          {audit.recommendations.map((message) => (
            <Alert key={message} status="warning" borderRadius="12px">
              <AlertIcon />
              <Text fontSize="sm">{translateAuditRecommendation(t, message)}</Text>
            </Alert>
          ))}
        </VStack>
      )}

      <SimpleGrid columns={{ base: 1, xl: 3 }} gap={4}>
        <DistributionTable title={t("Per sensor")} labelKey="sensor" rows={audit.distributions.by_sensor} />
        <DistributionTable title={t("Per modality")} labelKey="modality" rows={audit.distributions.by_modality} />
        <Card>
          <Text fontWeight="bold" color={textColor} mb={3}>{t("Annotation provenance")}</Text>
          <VStack align="stretch" spacing={2}>
            <AuditMetric label={t("Source annotations")} value={numberValue(annotationSummary.total_source_annotations)} />
            <AuditMetric label={t("Model-assisted accepted")} value={numberValue(annotationSummary.model_assisted_accepted)} />
            <AuditMetric label={t("Unreviewed predictions")} value={numberValue(annotationSummary.unreviewed_prediction_annotations)} />
            <AuditMetric label={t("Missing author")} value={numberValue(annotationSummary.missing_annotator_email)} />
            <AuditMetric label={t("Partial attributes")} value={numberValue(annotationSummary.partial_or_failed_attributes)} />
          </VStack>
        </Card>
      </SimpleGrid>

      <SimpleGrid columns={{ base: 1, xl: 2 }} gap={4}>
        <SplitAuditTable rows={audit.distributions.by_split} />
        <LocationAuditTable rows={audit.distributions.locations} />
      </SimpleGrid>

      {Object.entries(checkGroups).map(([category, checks]) => (
        <Card key={category}>
          <HStack justify="space-between" mb={3}>
            <HStack spacing={1}>
              <Text fontWeight="bold" color={textColor}>{t(category)}</Text>
              {auditCategoryInfo(category) && (
                <InfoPopover
                  titleKey={`${auditCategoryInfo(category)}.title`}
                  bodyKey={`${auditCategoryInfo(category)}.body`}
                />
              )}
            </HStack>
            <Badge>{checks.length} {t("Checks").toLowerCase()}</Badge>
          </HStack>
          <TableContainer>
            <Table size="sm">
              <Thead>
                <Tr>
                  <Th>{t("Status")}</Th>
                  <Th>{t("Check")}</Th>
                  <Th>{t("Result")}</Th>
                  <Th isNumeric>{t("Count")}</Th>
                </Tr>
              </Thead>
              <Tbody>
                {checks.map((check) => (
                  <Tr key={check.check_id}>
                    <Td><Badge colorScheme={checkColor(check.status)}>{t(check.status)}</Badge></Td>
                    <Td fontWeight="600">{t(check.title)}</Td>
                    <Td maxW="650px">
                      <Text fontSize="sm">{translateAuditCheckMessage(t, check)}</Text>
                      {check.details.length > 0 && (
                        <Text fontSize="xs" color={mutedColor} noOfLines={2} title={check.details.join(", ")}>
                          {check.details.join(", ")}
                        </Text>
                      )}
                    </Td>
                    <Td isNumeric>{check.count ?? "-"}</Td>
                  </Tr>
                ))}
              </Tbody>
            </Table>
          </TableContainer>
        </Card>
      ))}
    </VStack>
  );
}

function DistributionTable({
  title,
  labelKey,
  rows,
}: {
  title: string;
  labelKey: string;
  rows: Array<Record<string, string | number>>;
}) {
  const { t } = useTranslation();
  const textColor = useColorModeValue("navy.700", "white");
  return (
    <Card>
      <Text fontWeight="bold" color={textColor} mb={3}>{title}</Text>
      <TableContainer>
        <Table size="sm">
          <Thead><Tr><Th>{t("Name")}</Th><Th isNumeric>{t("Scenes")}</Th><Th isNumeric>{t("Tiles")}</Th><Th isNumeric>{t("Labels")}</Th></Tr></Thead>
          <Tbody>
            {rows.map((row, index) => (
              <Tr key={`${String(row[labelKey])}-${index}`}>
                <Td>{String(row[labelKey] ?? "unknown")}</Td>
                <Td isNumeric>{Number(row.scenes || 0)}</Td>
                <Td isNumeric>{Number(row.used_tiles || 0)}</Td>
                <Td isNumeric>{Number(row.annotations || 0)}</Td>
              </Tr>
            ))}
          </Tbody>
        </Table>
      </TableContainer>
    </Card>
  );
}

function SplitAuditTable({ rows }: { rows: Array<Record<string, unknown>> }) {
  const { t } = useTranslation();
  const textColor = useColorModeValue("navy.700", "white");
  return (
    <Card>
      <Text fontWeight="bold" color={textColor} mb={3}>{t("Per split")}</Text>
      <TableContainer>
        <Table size="sm">
          <Thead>
            <Tr>
              <Th>{t("Split")}</Th>
              <Th isNumeric>{t("Images")}</Th>
              <Th isNumeric>{t("Positive")}</Th>
              <Th isNumeric>{t("Used checked empty")}</Th>
              <Th isNumeric>{t("Labels")}</Th>
            </Tr>
          </Thead>
          <Tbody>
            {rows.map((row, index) => (
              <Tr key={`${String(row.split)}-${index}`}>
                <Td>{t(String(row.split || "unknown"))}</Td>
                <Td isNumeric>{Number(row.images || 0)}</Td>
                <Td isNumeric>{Number(row.positive_tiles || 0)}</Td>
                <Td isNumeric>{Number(row.negative_tiles || 0)}</Td>
                <Td isNumeric>{Number(row.annotations || 0)}</Td>
              </Tr>
            ))}
          </Tbody>
        </Table>
      </TableContainer>
    </Card>
  );
}

function LocationAuditTable({ rows }: { rows: Array<Record<string, unknown>> }) {
  const { t } = useTranslation();
  const textColor = useColorModeValue("navy.700", "white");
  return (
    <Card>
      <Text fontWeight="bold" color={textColor} mb={3}>{t("Per scene and location")}</Text>
      <TableContainer maxH="320px" overflowY="auto">
        <Table size="sm">
          <Thead><Tr><Th>{t("Scene")}</Th><Th>{t("Center WGS84")}</Th><Th>{t("Sensor")}</Th><Th isNumeric>{t("Tiles")}</Th><Th isNumeric>{t("Labels")}</Th></Tr></Thead>
          <Tbody>
            {rows.map((row, index) => (
              <Tr key={`${String(row.scene_id)}-${index}`}>
                <Td maxW="220px" overflow="hidden" textOverflow="ellipsis" title={String(row.filename || "")}>
                  {String(row.filename || row.scene_id || "unknown")}
                </Td>
                <Td>{formatCenter(row.centroid_lon, row.centroid_lat)}</Td>
                <Td>{String(row.sensor || "unknown")}</Td>
                <Td isNumeric>{Number(row.used_tiles || 0)}</Td>
                <Td isNumeric>{Number(row.annotations || 0)}</Td>
              </Tr>
            ))}
          </Tbody>
        </Table>
      </TableContainer>
    </Card>
  );
}

function AuditMetric({ label, value }: { label: string; value: number }) {
  return (
    <HStack justify="space-between">
      <Text fontSize="sm">{label}</Text>
      <Text fontSize="sm" fontWeight="bold">{value}</Text>
    </HStack>
  );
}

function groupChecks(checks: DatasetAuditCheck[]): Record<string, DatasetAuditCheck[]> {
  return checks.reduce<Record<string, DatasetAuditCheck[]>>((result, check) => {
    (result[check.category] ||= []).push(check);
    return result;
  }, {});
}

function auditCategoryInfo(category: string): string | null {
  const normalized = category.toLowerCase();
  if (normalized === "completeness") return "info.audit.completeness";
  if (normalized === "split") return "info.audit.splitIntegrity";
  if (normalized === "review") return "info.audit.reviewGrid";
  if (normalized === "classes") return "info.audit.classes";
  if (normalized === "scenes") return "info.audit.scenes";
  if (normalized === "annotations") return "info.audit.annotationProvenance";
  if (normalized === "metadata") return "info.audit.metadata";
  if (normalized === "sidecars") return "info.audit.sidecars";
  if (normalized === "preprocessing") return "info.audit.preprocessing";
  if (normalized === "provenance") return "info.audit.annotationProvenance";
  return null;
}

function numberValue(value: unknown): number {
  return typeof value === "number" ? value : Number(value || 0);
}

function formatCenter(lon: unknown, lat: unknown): string {
  const longitude = Number(lon);
  const latitude = Number(lat);
  if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) return "NO GEO";
  return `${longitude.toFixed(4)}, ${latitude.toFixed(4)}`;
}

function statusColor(status: string): string {
  return status === "ok" ? "green" : status === "error" ? "red" : "orange";
}

function readinessColor(value: string): string {
  return value === "ready" ? "green" : value === "not_ready" ? "red" : "orange";
}

function checkColor(status: string): string {
  if (status === "passed") return "green";
  if (status === "error") return "red";
  if (status === "warning") return "orange";
  if (status === "not_available") return "gray";
  return "blue";
}
