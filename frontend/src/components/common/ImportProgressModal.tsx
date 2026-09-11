import { useEffect, useRef, useState } from "react";
import {
  Badge,
  Box,
  Button,
  HStack,
  List,
  ListItem,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
  ModalOverlay,
  Progress,
  Spinner,
  Text,
  VStack,
  useColorModeValue,
  useToast,
} from "@chakra-ui/react";
import { useTranslation } from "react-i18next";
import * as api from "../../api/client";
import type { ImportJob } from "../../api/client";

interface Props {
  projectId: string | null;
  jobId: string | null;
  isOpen: boolean;
  /** Zamknij okno, ale zostaw import działający w tle (nie nawiguje). */
  onClose: () => void;
  /** Import zakończony sukcesem — przejdź do projektu. */
  onDone: (job: ImportJob) => void;
}

const POLL_MS = 1000;

export default function ImportProgressModal({ projectId, jobId, isOpen, onClose, onDone }: Props) {
  const { t } = useTranslation();
  const toast = useToast();
  const [copying, setCopying] = useState(false);
  const [job, setJob] = useState<ImportJob | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const okColor = useColorModeValue("green.600", "green.300");
  const errColor = useColorModeValue("red.600", "red.300");
  const logBg = useColorModeValue("gray.50", "gray.800");
  const doneRef = useRef(false);

  useEffect(() => {
    if (!isOpen || !projectId || !jobId) return;
    doneRef.current = false;
    setCancelling(false);
    setJob(null);
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async () => {
      try {
        const next = await api.getImportJob(projectId, jobId);
        if (cancelled) return;
        setJob(next);
        if (next.state === "done" && !doneRef.current) {
          doneRef.current = true;
          onDone(next);
          return;
        }
        if (next.state === "error" || next.state === "cancelled") return; // stan terminalny — zatrzymaj polling
      } catch {
        // przejściowy błąd sieci — spróbuj ponownie w następnym cyklu
      }
      if (!cancelled) timer = setTimeout(tick, POLL_MS);
    };
    tick();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [isOpen, projectId, jobId, onDone]);

  const total = job?.total ?? 0;
  const done = job?.done ?? 0;
  const isError = job?.state === "error";
  const isCancelled = job?.state === "cancelled";
  const isDone = job?.state === "done";
  const isTerminal = isError || isCancelled || isDone;
  const percent = total > 0 ? Math.round((done / total) * 100) : isDone ? 100 : 0;
  const phase = job?.phase ?? "catalogue";

  const scanId = job?.report?.scan_id;

  /** Skopiuj pełny raport importu do schowka. Wariant `anonymous` zamienia ścieżki i nazwy
   *  plików na stabilne skróty, żeby dało się go przekazać dalej bez ujawniania struktury
   *  katalogów (DESIGN_DECISIONS.md, scene-import P1.6). */
  const copyReport = async (anonymous: boolean) => {
    if (!projectId || !scanId) return;
    setCopying(true);
    try {
      const report = await api.getSceneImportReport(projectId, scanId, anonymous);
      await navigator.clipboard.writeText(JSON.stringify(report, null, 2));
      toast({ status: "success", title: t("Import report copied") });
    } catch {
      toast({ status: "error", title: t("Could not copy the import report") });
    } finally {
      setCopying(false);
    }
  };

  const stageLabel = (stage?: string) => {
    if (stage === "identity") return t("Computing file identity");
    if (stage === "overviews") return t("Building display pyramids");
    return t("Reading scene");
  };

  const handleCancel = async () => {
    if (!projectId || !jobId) return;
    setCancelling(true);
    try {
      await api.cancelImportJob(projectId, jobId);
    } catch (err: any) {
      setCancelling(false);
      toast({
        title: t("Cancel import"),
        description: err?.response?.data?.detail || err?.message,
        status: "error",
      });
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} closeOnOverlayClick={false} size="xl" isCentered>
      <ModalOverlay />
      <ModalContent>
        <ModalHeader>
          <HStack>
            {!isTerminal && <Spinner size="sm" />}
            <Text>{t("Importing scenes")}</Text>
            {isDone && <Badge colorScheme="green">{t("Done")}</Badge>}
            {isError && <Badge colorScheme="red">{t("Error")}</Badge>}
            {isCancelled && <Badge colorScheme="orange">{t("Cancelled")}</Badge>}
          </HStack>
        </ModalHeader>
        <ModalBody>
          <VStack align="stretch" spacing={3}>
            <Box>
              <HStack justify="space-between" mb={1}>
                <Text fontSize="sm" fontWeight="medium">{t("Catalogued scenes")}</Text>
                <Text fontSize="sm" color="gray.500">{done} / {total || "?"}</Text>
              </HStack>
              <Progress
                value={percent}
                size="sm"
                colorScheme={isError ? "red" : isCancelled ? "orange" : "brand"}
                borderRadius="full"
                hasStripe={!isTerminal}
                isAnimated={!isTerminal}
              />
            </Box>

            {(job?.identity_total ?? 0) > 0 && (
              <Box>
                <HStack justify="space-between" mb={1}>
                  <Text fontSize="xs" color="gray.500">{t("Computing file identity")}</Text>
                  <Text fontSize="xs" color="gray.500">{job?.identity_done ?? 0} / {job?.identity_total}</Text>
                </HStack>
                <Progress
                  value={Math.round(((job?.identity_done ?? 0) / (job?.identity_total || 1)) * 100)}
                  size="xs"
                  colorScheme="purple"
                  borderRadius="full"
                  hasStripe={phase === "identity" && !isTerminal}
                  isAnimated={phase === "identity" && !isTerminal}
                />
              </Box>
            )}

            {(job?.overviews_total ?? 0) > 0 && (
              <Box>
                <HStack justify="space-between" mb={1}>
                  <Text fontSize="xs" color="gray.500">{t("Building display pyramids")}</Text>
                  <Text fontSize="xs" color="gray.500">{job?.overviews_done ?? 0} / {job?.overviews_total}</Text>
                </HStack>
                <Progress
                  value={Math.round(((job?.overviews_done ?? 0) / (job?.overviews_total || 1)) * 100)}
                  size="xs"
                  colorScheme="teal"
                  borderRadius="full"
                  hasStripe={phase === "overviews" && !isTerminal}
                  isAnimated={phase === "overviews" && !isTerminal}
                />
              </Box>
            )}

            {job?.current && !isTerminal && (
              <Text fontSize="sm" noOfLines={1}>
                <Text as="span" color="gray.500">{stageLabel(job.current.stage)}: </Text>
                <Text as="span" fontFamily="mono">{job.current.filename}</Text>
              </Text>
            )}

            {isError && <Text fontSize="sm" color={errColor}>{job?.error}</Text>}

            {(job?.recent?.length ?? 0) > 0 && (
              <Box bg={logBg} borderRadius="md" p={2} maxH="200px" overflowY="auto">
                <List spacing={0} fontSize="xs" fontFamily="mono">
                  {[...(job?.recent ?? [])].reverse().map((item, index) => (
                    <ListItem key={`${item.filename}-${index}`} color={item.status === "ok" ? okColor : errColor}>
                      {item.status === "ok" ? "✓" : "✗"} {item.filename} <Text as="span" color="gray.500">({item.ms} ms)</Text>
                    </ListItem>
                  ))}
                </List>
              </Box>
            )}

            {(job?.failed ?? 0) > 0 && (
              <Text fontSize="xs" color={errColor}>{t("Failed scenes")}: {job?.failed}</Text>
            )}

            {/* Raport importu (P1.6): lista ostatnich pozycji wyżej jest przycięta do 20,
                natomiast zapisany raport zawiera KAŻDĄ scenę wraz z przyczyną. */}
            {isTerminal && scanId && (
              <HStack fontSize="xs" spacing={2}>
                <Button size="xs" variant="outline" onClick={() => copyReport(false)} isLoading={copying}>
                  {t("Copy import report")}
                </Button>
                <Button size="xs" variant="ghost" onClick={() => copyReport(true)} isLoading={copying}>
                  {t("Copy without paths")}
                </Button>
              </HStack>
            )}
          </VStack>
        </ModalBody>
        <ModalFooter>
          <HStack>
            {!isTerminal && (
              <Button variant="outline" colorScheme="orange" onClick={handleCancel} isLoading={cancelling}>
                {t("Cancel import")}
              </Button>
            )}
            <Button variant="ghost" onClick={onClose}>
              {isTerminal ? t("Close") : t("Run in background")}
            </Button>
            <Button colorScheme="brand" isDisabled={!isDone} onClick={() => job && onDone(job)}>
              {t("Open project")}
            </Button>
          </HStack>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
