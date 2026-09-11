import { useEffect, useState } from "react";
import {
  Alert,
  AlertIcon,
  Badge,
  Button,
  Checkbox,
  List,
  ListItem,
  Modal,
  ModalBody,
  ModalCloseButton,
  ModalContent,
  ModalFooter,
  ModalHeader,
  ModalOverlay,
  Spinner,
  Text,
  VStack,
  useColorModeValue,
  useToast,
} from "@chakra-ui/react";
import { useTranslation } from "react-i18next";
import * as api from "../../api/client";
import type { RunDeleteInfo } from "../../api/client";

export type DeleteRunKind = "dataset" | "training";

interface Props {
  isOpen: boolean;
  onClose: () => void;
  kind: DeleteRunKind;
  projectId: string | undefined;
  runId: string | null;
  /** Human-readable name shown in the dialog (label or short id). */
  runLabel: string;
  /** Called after a successful delete so the parent can refresh + fix selection. */
  onDeleted: (runId: string) => void | Promise<void>;
}

/**
 * Guarded permanent deletion of a dataset or training run (files + index).
 * Follows the "warn and force" rule: if the run has dependents (training runs /
 * models) or is published, deletion needs an explicit acknowledgement; an active
 * training run can never be deleted.
 */
export default function DeleteRunDialog({
  isOpen,
  onClose,
  kind,
  projectId,
  runId,
  runLabel,
  onDeleted,
}: Props) {
  const { t } = useTranslation();
  const toast = useToast();
  const muted = useColorModeValue("secondaryGray.600", "whiteAlpha.600");
  const [info, setInfo] = useState<RunDeleteInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [acknowledge, setAcknowledge] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (!isOpen || !projectId || !runId) return;
    setInfo(null);
    setAcknowledge(false);
    setLoading(true);
    const fetch = kind === "dataset" ? api.getDatasetRunDeleteInfo : api.getTrainingRunDeleteInfo;
    fetch(projectId, runId)
      .then(setInfo)
      .catch((err: any) =>
        toast({
          title: t("Could not load deletion details"),
          description: err.response?.data?.detail || err.message || String(err),
          status: "error",
          duration: 6000,
          isClosable: true,
        }),
      )
      .finally(() => setLoading(false));
  }, [isOpen, projectId, runId, kind, t, toast]);

  const active = !!info?.active;
  const blocked = !!info?.blocked;
  const canDelete = !active && (!blocked || acknowledge);

  const handleDelete = async () => {
    if (!projectId || !runId) return;
    setDeleting(true);
    try {
      const del = kind === "dataset" ? api.deleteDatasetRun : api.deleteTrainingRun;
      await del(projectId, runId, blocked);
      toast({ title: t("Run deleted"), status: "success", duration: 3000, isClosable: true });
      await onDeleted(runId);
      onClose();
    } catch (err: any) {
      toast({
        title: t("Could not delete run"),
        description: err.response?.data?.detail
          ? typeof err.response.data.detail === "string"
            ? err.response.data.detail
            : t("The run has dependents - reopen to review.")
          : err.message || String(err),
        status: "error",
        duration: 7000,
        isClosable: true,
      });
    } finally {
      setDeleting(false);
    }
  };

  const trainingDeps = info?.training_runs || [];
  const modelDeps = info?.models || [];

  return (
    <Modal isOpen={isOpen} onClose={onClose} isCentered>
      <ModalOverlay />
      <ModalContent>
        <ModalHeader>{t("Delete run permanently")}</ModalHeader>
        <ModalCloseButton />
        <ModalBody>
          <VStack align="stretch" spacing={3}>
            <Text fontSize="sm" color={muted} wordBreak="break-all">{runLabel}</Text>

            {loading && (
              <HStackSpinner label={t("Checking dependencies…")} />
            )}

            {!loading && info && (
              <>
                <Text fontSize="sm">
                  {t("This removes the run and all its files from disk. This cannot be undone.")}
                </Text>

                {active && (
                  <Alert status="error" borderRadius="md" fontSize="sm">
                    <AlertIcon />
                    {t("This training run is still active. Cancel it before deleting.")}
                  </Alert>
                )}

                {!active && blocked && (
                  <Alert status="warning" borderRadius="md" fontSize="xs" alignItems="flex-start">
                    <AlertIcon />
                    <VStack align="stretch" spacing={2} flex="1">
                      <Text>
                        {kind === "dataset"
                          ? t("Other artifacts were built from this dataset run. Deleting it leaves them orphaned (their lineage can no longer be traced).")
                          : t("Models are registered from this training run. Deleting it removes those model entries and clears the project model if it points here.")}
                      </Text>

                      {info.published && (
                        <Text><Badge colorScheme="green" mr={1}>{t("published")}</Badge>{t("This dataset run is published.")}</Text>
                      )}
                      {trainingDeps.length > 0 && (
                        <>
                          <Text fontWeight="600">{t("Dependent training runs")} ({trainingDeps.length}):</Text>
                          <List spacing={0}>
                            {trainingDeps.slice(0, 6).map((run) => (
                              <ListItem key={run.training_run_id} fontFamily="mono">
                                {run.base_model || "?"} · #{run.attempt ?? "?"} · {run.status || "?"}
                              </ListItem>
                            ))}
                            {trainingDeps.length > 6 && <ListItem>… +{trainingDeps.length - 6}</ListItem>}
                          </List>
                        </>
                      )}
                      {modelDeps.length > 0 && (
                        <>
                          <Text fontWeight="600">{t("Registered models")} ({modelDeps.length}):</Text>
                          <List spacing={0}>
                            {modelDeps.slice(0, 6).map((model) => (
                              <ListItem key={model.model_id} fontFamily="mono">
                                {model.model_id.slice(-12)}{model.is_current ? ` · ${t("project model")}` : ""}
                              </ListItem>
                            ))}
                          </List>
                        </>
                      )}

                      <Checkbox size="sm" isChecked={acknowledge} onChange={(e) => setAcknowledge(e.target.checked)}>
                        {t("I understand and want to delete it anyway")}
                      </Checkbox>
                    </VStack>
                  </Alert>
                )}

                {!active && !blocked && (
                  <Text fontSize="xs" color={muted}>
                    {t("No training runs or models depend on this run.")}
                  </Text>
                )}
              </>
            )}
          </VStack>
        </ModalBody>
        <ModalFooter gap={2}>
          <Button size="sm" variant="ghost" onClick={onClose}>{t("Cancel")}</Button>
          <Button
            size="sm"
            colorScheme="red"
            isLoading={deleting}
            isDisabled={loading || !info || !canDelete}
            onClick={handleDelete}
          >
            {blocked ? t("Delete anyway") : t("Delete")}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}

function HStackSpinner({ label }: { label: string }) {
  return (
    <VStack align="center" spacing={2} py={2}>
      <Spinner size="sm" />
      <Text fontSize="xs">{label}</Text>
    </VStack>
  );
}
