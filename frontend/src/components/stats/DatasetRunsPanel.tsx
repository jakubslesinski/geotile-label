import { useState } from "react";
import {
  Alert,
  AlertIcon,
  Badge,
  Button,
  Checkbox,
  Collapse,
  FormControl,
  FormHelperText,
  FormLabel,
  HStack,
  Input,
  Modal,
  ModalBody,
  ModalCloseButton,
  ModalContent,
  ModalFooter,
  ModalHeader,
  ModalOverlay,
  Select,
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
  useDisclosure,
  useToast,
} from "@chakra-ui/react";
import { MdDeleteOutline, MdExpandLess, MdExpandMore, MdHistory, MdPublish } from "react-icons/md";
import Card from "../common/Card";
import InfoPopover from "../common/InfoPopover";
import DeleteRunDialog from "../common/DeleteRunDialog";
import * as api from "../../api/client";
import type { DatasetPublicationStatus, DatasetRunSummary } from "../../types";
import { useTranslation } from "react-i18next";
import i18n from "../../i18n";

interface DatasetRunsPanelProps {
  runs: DatasetRunSummary[];
  latestRunId: string | null;
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
  projectId?: string;
  /** Wywoływane po zmianie publikacji, żeby odświeżyć indeks runów. */
  onPublicationChanged?: () => void | Promise<void>;
  /** Wywoływane po skasowaniu runu (odśwież indeks i popraw zaznaczenie). */
  onRunDeleted?: (runId: string) => void | Promise<void>;
}

function publicationStatusOf(run: DatasetRunSummary): DatasetPublicationStatus {
  return run.publication_status || "draft";
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString(i18n.language.startsWith("pl") ? "pl-PL" : "en-US");
}

export default function DatasetRunsPanel({
  runs,
  latestRunId,
  selectedRunId,
  onSelect,
  projectId,
  onPublicationChanged,
  onRunDeleted,
}: DatasetRunsPanelProps) {
  const { t } = useTranslation();
  const toast = useToast();
  const publishModal = useDisclosure();
  const deleteModal = useDisclosure();
  const [deleteRunId, setDeleteRunId] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [publishRunId, setPublishRunId] = useState<string | null>(null);
  const [publishLabel, setPublishLabel] = useState("");
  const [acknowledgeIssues, setAcknowledgeIssues] = useState(false);
  const [isSavingPublication, setIsSavingPublication] = useState(false);
  const textColor = useColorModeValue("navy.700", "white");
  const mutedColor = useColorModeValue("secondaryGray.600", "whiteAlpha.600");
  const selectedBg = useColorModeValue("brand.50", "whiteAlpha.100");
  const selectedRun = runs.find((run) => run.run_id === selectedRunId);
  const publishRun = runs.find((run) => run.run_id === publishRunId) || null;
  // Audyt/walidacja tylko uwidaczniają problemy — publikacja mimo nich to świadoma
  // decyzja człowieka (wymaga potwierdzenia).
  const publishHasIssues = !!(
    publishRun &&
    (publishRun.validation_status === "error" || publishRun.audit_readiness === "not_ready")
  );

  const openPublishDialog = (run: DatasetRunSummary) => {
    setPublishRunId(run.run_id);
    setPublishLabel(run.publication_label || "");
    setAcknowledgeIssues(false);
    publishModal.onOpen();
  };

  const openDeleteDialog = (run: DatasetRunSummary) => {
    setDeleteRunId(run.run_id);
    deleteModal.onOpen();
  };

  const deleteRun = runs.find((run) => run.run_id === deleteRunId) || null;
  const deleteRunLabel = deleteRun
    ? `${deleteRun.publication_label ? `${deleteRun.publication_label} · ` : ""}${formatDate(deleteRun.created_at)} · ${deleteRun.run_id.slice(-17)}`
    : "";

  const applyPublication = async (status: DatasetPublicationStatus) => {
    if (!projectId || !publishRunId) return;
    setIsSavingPublication(true);
    try {
      await api.setDatasetRunPublication(projectId, publishRunId, {
        status,
        label: status === "published" ? publishLabel.trim() : undefined,
        acknowledge_issues: status === "published" ? acknowledgeIssues : undefined,
      });
      await onPublicationChanged?.();
      publishModal.onClose();
    } catch (err: any) {
      // Komunikaty bramki jakości (audyt, walidacja splitu, zajęta etykieta)
      // przychodzą z backendu i muszą dotrzeć do użytkownika w całości.
      toast({
        title: t("Could not change dataset publication"),
        description: err.response?.data?.detail || err.message || String(err),
        status: "error",
        duration: 8000,
        isClosable: true,
      });
    } finally {
      setIsSavingPublication(false);
    }
  };

  return (
    <Card>
      <VStack align="stretch" spacing={3}>
        <HStack justify="space-between" align={{ base: "stretch", md: "center" }} flexDirection={{ base: "column", md: "row" }}>
          <HStack spacing={3} minW="180px">
            <MdHistory />
            <Text fontWeight="bold" color={textColor}>{t("Dataset Run")}</Text>
            <InfoPopover titleKey="info.dataset.datasetRun.title" bodyKey="info.dataset.datasetRun.body" />
            <Badge colorScheme="brand">{runs.length}</Badge>
          </HStack>

          {runs.length > 0 ? (
            <Select
              size="sm"
              maxW={{ base: "100%", md: "560px" }}
              value={selectedRunId || ""}
              onChange={(event) => onSelect(event.target.value)}
            >
              {runs.map((run) => (
                <option key={run.run_id} value={run.run_id} disabled={run.status !== "complete"}>
                  {formatDate(run.created_at)} - {t(run.split_mode || "legacy")} - {run.total_tiles} {t("Tiles").toLowerCase()}
                </option>
              ))}
            </Select>
          ) : (
            <Text flex="1" fontSize="sm" color={mutedColor}>
              {t("No runs yet. Configure and generate the first dataset.")}
            </Text>
          )}

          <HStack justify="flex-end" flexWrap="wrap">
            {selectedRun && <RunBadges run={selectedRun} latestRunId={latestRunId} />}
            {projectId && selectedRun && selectedRun.status === "complete" && (
              <Button
                size="sm"
                variant="outline"
                leftIcon={<MdPublish />}
                onClick={() => openPublishDialog(selectedRun)}
              >
                {publicationStatusOf(selectedRun) === "published" ? t("Publication") : t("Publish")}
              </Button>
            )}
            {runs.length > 0 && (
              <Button
                size="sm"
                variant="outline"
                rightIcon={showHistory ? <MdExpandLess /> : <MdExpandMore />}
                onClick={() => setShowHistory((current) => !current)}
              >
                {t("History")}
              </Button>
            )}
          </HStack>
        </HStack>

        <Collapse in={showHistory} animateOpacity>
          <TableContainer maxH="320px" overflowY="auto" pt={2}>
            <Table size="sm">
              <Thead>
                <Tr>
                  <Th>{t("Created")}</Th>
                  <Th>{t("Configuration")}</Th>
                  <Th isNumeric>{t("Tiles")}</Th>
                  <Th isNumeric>{t("Annotations")}</Th>
                  <Th>{t("Status")}</Th>
                  <Th />
                </Tr>
              </Thead>
              <Tbody>
                {runs.map((run) => {
                  const isSelected = run.run_id === selectedRunId;
                  return (
                    <Tr key={run.run_id} bg={isSelected ? selectedBg : undefined}>
                      <Td>
                        <Text fontSize="xs" fontWeight="600">{formatDate(run.created_at)}</Text>
                        <Text fontSize="xx-small" color={mutedColor} title={run.run_id}>
                          {run.run_id.slice(-17)}
                        </Text>
                      </Td>
                      <Td>
                        <Text fontSize="xs">{t(run.split_mode || "legacy")}</Text>
                        <Text fontSize="xx-small" color={mutedColor}>
                          {t("Seed").toLowerCase()} {run.split_seed ?? "-"} - {t("Tile").toLowerCase()} {run.tile_size ?? "-"}
                        </Text>
                        <Text fontSize="xx-small" color={mutedColor} title={run.preprocessing_profile_hash || undefined}>
                          {run.preprocessing_profile_id || "legacy preprocessing"}
                        </Text>
                      </Td>
                      <Td isNumeric>{run.total_tiles}</Td>
                      <Td isNumeric>{run.total_annotations}</Td>
                      <Td><RunBadges run={run} latestRunId={latestRunId} /></Td>
                      <Td>
                        <HStack spacing={1}>
                          <Button
                            size="xs"
                            variant={isSelected ? "solid" : "outline"}
                            colorScheme="brand"
                            isDisabled={run.status !== "complete" || isSelected}
                            onClick={() => onSelect(run.run_id)}
                          >
                            {isSelected ? t("Selected") : t("Select")}
                          </Button>
                          {projectId && run.status === "complete" && (
                            <Button
                              size="xs"
                              variant="ghost"
                              onClick={() => openPublishDialog(run)}
                            >
                              {t("Publication")}
                            </Button>
                          )}
                          {projectId && (
                            <Button
                              size="xs"
                              variant="ghost"
                              colorScheme="red"
                              leftIcon={<MdDeleteOutline />}
                              onClick={() => openDeleteDialog(run)}
                            >
                              {t("Delete")}
                            </Button>
                          )}
                        </HStack>
                      </Td>
                    </Tr>
                  );
                })}
              </Tbody>
            </Table>
          </TableContainer>
        </Collapse>
      </VStack>

      <Modal isOpen={publishModal.isOpen} onClose={publishModal.onClose} isCentered>
        <ModalOverlay />
        <ModalContent>
          <ModalHeader>{t("Dataset publication")}</ModalHeader>
          <ModalCloseButton />
          <ModalBody>
            <VStack align="stretch" spacing={3}>
              <Text fontSize="sm" color={mutedColor}>
                {publishRun ? formatDate(publishRun.created_at) : ""}
                {publishRun ? ` · ${publishRun.total_tiles} ${t("Tiles").toLowerCase()}` : ""}
              </Text>
              <HStack>
                <Text fontSize="sm">{t("Current status")}:</Text>
                <PublicationBadge run={publishRun} />
              </HStack>
              <FormControl>
                <FormLabel fontSize="sm">{t("Version label")}</FormLabel>
                <Input
                  size="sm"
                  placeholder="v1"
                  value={publishLabel}
                  onChange={(event) => setPublishLabel(event.target.value)}
                />
                <FormHelperText fontSize="xs">
                  {t("A published dataset needs a label unique in the project - it is what identifies the data a model was trained on.")}
                </FormHelperText>
              </FormControl>
              {publishHasIssues ? (
                <Alert status="warning" borderRadius="md" fontSize="xs" alignItems="flex-start">
                  <AlertIcon />
                  <VStack align="stretch" spacing={2} flex="1">
                    <Text>
                      {t("This run reports audit or split-validation errors. Publishing is your decision - the acknowledged issues are recorded with the publication.")}
                    </Text>
                    <Checkbox
                      size="sm"
                      isChecked={acknowledgeIssues}
                      onChange={(event) => setAcknowledgeIssues(event.target.checked)}
                    >
                      {t("I understand and want to publish anyway")}
                    </Checkbox>
                  </VStack>
                </Alert>
              ) : (
                <Text fontSize="xs" color={mutedColor}>
                  {t("Publishing requires a completed run with a unique label. Audit/validation errors are surfaced but do not block - the decision stays with you.")}
                </Text>
              )}
            </VStack>
          </ModalBody>
          <ModalFooter gap={2}>
            {publishRun && publicationStatusOf(publishRun) !== "draft" && (
              <Button
                size="sm"
                variant="ghost"
                isLoading={isSavingPublication}
                onClick={() => applyPublication("draft")}
              >
                {t("Back to draft")}
              </Button>
            )}
            {publishRun && publicationStatusOf(publishRun) === "published" && (
              <Button
                size="sm"
                variant="outline"
                colorScheme="orange"
                isLoading={isSavingPublication}
                onClick={() => applyPublication("deprecated")}
              >
                {t("Deprecate")}
              </Button>
            )}
            <Button
              size="sm"
              colorScheme="brand"
              isLoading={isSavingPublication}
              isDisabled={!publishLabel.trim() || (publishHasIssues && !acknowledgeIssues)}
              onClick={() => applyPublication("published")}
            >
              {t("Publish")}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>

      <DeleteRunDialog
        isOpen={deleteModal.isOpen}
        onClose={deleteModal.onClose}
        kind="dataset"
        projectId={projectId}
        runId={deleteRunId}
        runLabel={deleteRunLabel}
        onDeleted={async (runId) => {
          await onRunDeleted?.(runId);
        }}
      />
    </Card>
  );
}

function PublicationBadge({ run }: { run: DatasetRunSummary | null }) {
  const { t } = useTranslation();
  if (!run) return null;
  const status = publicationStatusOf(run);
  const scheme = status === "published" ? "green" : status === "deprecated" ? "orange" : "gray";
  return (
    <Badge colorScheme={scheme}>
      {run.publication_label ? `${run.publication_label} · ` : ""}
      {t(status)}
    </Badge>
  );
}

function RunBadges({ run, latestRunId }: { run: DatasetRunSummary; latestRunId: string | null }) {
  const { t } = useTranslation();
  return (
    <HStack spacing={1} flexWrap="wrap">
      {publicationStatusOf(run) !== "draft" && <PublicationBadge run={run} />}
      <Badge colorScheme={run.status === "complete" ? "green" : "red"}>{t(run.status)}</Badge>
      {run.run_id === latestRunId && <Badge colorScheme="blue">{t("latest")}</Badge>}
      {run.validation_status && run.validation_status !== "ok" && (
        <Badge colorScheme={run.validation_status === "error" ? "red" : "orange"}>
          {t("Split")}: {t(run.validation_status)}
        </Badge>
      )}
      {run.audit_readiness && (
        <Badge colorScheme={run.audit_readiness === "ready" ? "green" : run.audit_readiness === "not_ready" ? "red" : "orange"}>
          audit {run.audit_quality_score ?? "-"}
        </Badge>
      )}
    </HStack>
  );
}
