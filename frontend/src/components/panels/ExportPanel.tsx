import {
  VStack,
  HStack,
  Button,
  Text,
  Divider,
  useColorModeValue,
  useToast,
  Alert,
  AlertIcon,
  Checkbox,
} from "@chakra-ui/react";
import { MdDownload, MdFolderOpen, MdSaveAlt } from "react-icons/md";
import Card from "../common/Card";
import InfoPopover from "../common/InfoPopover";
import * as api from "../../api/client";
import { isTauriRuntime, saveDatasetZipDialog } from "../../desktop/dialogs";
import type { DatasetAuditReport } from "../../types";
import { useTranslation } from "react-i18next";
import { useState } from "react";

interface Props {
  projectId: string;
  runId?: string | null;
  audit?: DatasetAuditReport | null;
}

/**
 * Zwięzły pasek akcji przy wybranej wersji datasetu. Format główny (YOLO AABB/OBB) powstaje
 * już przy generowaniu, zgodnie z geometrią projektu — tu zostają tylko akcje na żądanie:
 * konwersja na formaty wtórne (COCO/VOC), przenośna paczka ZIP i otwarcie folderu.
 */
export default function ExportPanel({ projectId, runId, audit }: Props) {
  const { t } = useTranslation();
  const textColor = useColorModeValue("navy.700", "white");
  const mutedColor = useColorModeValue("secondaryGray.600", "whiteAlpha.600");
  const toast = useToast();
  const isDesktop = isTauriRuntime();
  const [formats, setFormats] = useState<api.DatasetExportFormat[]>(["yolo"]);
  const [isExporting, setIsExporting] = useState(false);

  const toggleFormat = (format: api.DatasetExportFormat) => {
    setFormats((current) =>
      current.includes(format)
        ? current.filter((value) => value !== format)
        : [...current, format],
    );
  };

  const handleOpenDatasetFolder = async () => {
    try {
      const location = await api.getDatasetLocation(projectId, runId);
      if (!location.exists) {
        toast({
          title: t("Dataset folder not found"),
          description: t("Generate a dataset first."),
          status: "warning",
          duration: 4000,
        });
        return;
      }
      await api.openPathInFileManager(location.dataset_dir);
    } catch (err: any) {
      toast({
        title: t("Cannot open dataset folder"),
        description: err.response?.data?.detail || err.message || String(err),
        status: "error",
        duration: 5000,
      });
    }
  };

  const handleStartExport = async () => {
    try {
      if (formats.length === 0) return;
      let selected: string | null = null;
      if (isDesktop) {
        selected = await saveDatasetZipDialog(runId ? `dataset_${runId}.zip` : "dataset.zip");
        if (!selected) return;
      }
      setIsExporting(true);
      const result = await api.startDatasetExportJob(projectId, formats, runId, selected);
      toast({
        title: t("Dataset export queued"),
        description:
          t("Progress and the downloadable artifact are available in Background jobs.") +
          ` (${result.job.job_id})`,
        status: "info",
        duration: 5000,
      });
    } catch (err: any) {
      toast({
        title: t("Cannot save dataset ZIP"),
        description: err.response?.data?.detail || err.message,
        status: "error",
        duration: 5000,
      });
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <Card>
      <VStack align="stretch" spacing={3}>
        <HStack justify="space-between" flexWrap="wrap">
          <HStack spacing={1}>
            <Text fontWeight="bold" color={textColor}>
              {t("Export & convert")}
            </Text>
            <InfoPopover titleKey="info.export.package.title" bodyKey="info.export.package.body" />
          </HStack>
          {runId && (
            <Text fontSize="xs" color={mutedColor} title={runId}>
              {t("Selected run")}: {runId.slice(-17)}
            </Text>
          )}
        </HStack>

        {!runId ? (
          <Text fontSize="sm" color={mutedColor}>
            {t("Generate or select a dataset run before exporting.")}
          </Text>
        ) : (
          <>
            {audit && audit.status !== "ok" && (
              <Alert status={audit.status === "error" ? "error" : "warning"} borderRadius="10px" fontSize="sm" py={2}>
                <AlertIcon />
                {t("Audit export warning", { readiness: audit.readiness, errors: audit.summary.errors, warnings: audit.summary.warnings })}
              </Alert>
            )}

            <HStack spacing={3} flexWrap="wrap" align="center">
              <HStack spacing={1}>
                <Text fontSize="sm" color={mutedColor}>{t("Package formats")}:</Text>
                <Checkbox isChecked={formats.includes("yolo")} onChange={() => toggleFormat("yolo")}>
                  YOLO AABB/OBB
                </Checkbox>
                <Checkbox isChecked={formats.includes("coco")} onChange={() => toggleFormat("coco")}>
                  COCO
                </Checkbox>
                <Checkbox isChecked={formats.includes("voc")} onChange={() => toggleFormat("voc")}>
                  Pascal VOC
                </Checkbox>
              </HStack>

              <Divider orientation="vertical" height="24px" display={{ base: "none", md: "block" }} />

              <HStack spacing={1}>
                <Button
                  size="sm"
                  leftIcon={isDesktop ? <MdSaveAlt /> : <MdDownload />}
                  colorScheme="brand"
                  onClick={handleStartExport}
                  isDisabled={formats.length === 0}
                  isLoading={isExporting}
                >
                  {isDesktop ? t("Export package ZIP") : t("Build package ZIP")}
                </Button>
                <InfoPopover titleKey="info.export.saveZip.title" bodyKey="info.export.saveZip.body" />
              </HStack>
              {isDesktop && (
                  <HStack spacing={1}>
                    <Button size="sm" variant="outline" leftIcon={<MdFolderOpen />} onClick={handleOpenDatasetFolder}>
                      {t("Open dataset folder")}
                    </Button>
                    <InfoPopover titleKey="info.export.openDatasetFolder.title" bodyKey="info.export.openDatasetFolder.body" />
                  </HStack>
              )}
            </HStack>
          </>
        )}
      </VStack>
    </Card>
  );
}
