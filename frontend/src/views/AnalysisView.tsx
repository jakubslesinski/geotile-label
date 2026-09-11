import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  AlertIcon,
  Badge,
  Box,
  Button,
  Divider,
  HStack,
  Modal,
  ModalBody,
  ModalCloseButton,
  ModalContent,
  ModalHeader,
  ModalOverlay,
  Progress,
  Select,
  SimpleGrid,
  Spinner,
  Table,
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
import { useTranslation } from "react-i18next";
import Card from "../components/common/Card";
import InfoPopover from "../components/common/InfoPopover";
import ConfusionSimilarityMatrix from "../components/stats/ConfusionSimilarityMatrix";
import ClassExampleGallery from "../components/stats/ClassExampleGallery";
import * as api from "../api/client";
import type { DatasetRunSummary } from "../types";

/** Etapy workera → czytelny opis paska postępu. */
const STAGE_LABELS: Record<string, string> = {
  loading_backbone: "Loading embedding backbone",
  extracting_and_embedding: "Cutting object chips and embedding",
  analyzing: "Computing similarity and prototypes",
  detecting_duplicates: "Detecting near-duplicates",
  rendering_examples: "Rendering class examples",
  done: "Done",
};

const RUNNING = new Set(["queued", "running"]);

// Cache wyniku analizy per projekt — ponowne wejście w zakładkę pokazuje wynik od razu,
// a w tle rewaliduje (odczyt jest tani, ale bez cache miga „No analysis yet").
const analysisCache = new Map<
  string,
  { state: api.EmbeddingAnalysisState | null; result: api.EmbeddingAnalysisResult | null }
>();

export default function AnalysisView() {
  const { id } = useParams<{ id: string }>();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const toast = useToast();
  const muted = useColorModeValue("secondaryGray.600", "whiteAlpha.600");
  const cardBorder = useColorModeValue("gray.200", "whiteAlpha.300");

  // Inicjuj z cache (jeśli mamy) → ponowne wejście w zakładkę jest natychmiastowe,
  // bez migania spinnera; `load()` i tak rewaliduje w tle.
  const [state, setState] = useState<api.EmbeddingAnalysisState | null>(
    () => (id ? analysisCache.get(id)?.state ?? null : null),
  );
  const [result, setResult] = useState<api.EmbeddingAnalysisResult | null>(
    () => (id ? analysisCache.get(id)?.result ?? null : null),
  );
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(() => (id ? analysisCache.has(id) : false));
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [publishedRuns, setPublishedRuns] = useState<DatasetRunSummary[]>([]);
  const [splitRunId, setSplitRunId] = useState("");

  const neighborModal = useDisclosure();
  const [neighbors, setNeighbors] = useState<(api.AnalysisObject & { similarity: number })[] | null>(null);
  const [neighborBusy, setNeighborBusy] = useState(false);
  const seedRef = useRef<api.AnalysisObject | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    const cached = analysisCache.get(id);
    if (cached) {
      setState(cached.state);
      setResult(cached.result);
      setLoaded(true);
    }
    try {
      const data = await api.getEmbeddingAnalysis(id);
      setState(data.state);
      setResult(data.result);
      analysisCache.set(id, { state: data.state, result: data.result });
    } catch {
      /* brak wyniku to nie błąd — pierwszy raz */
    } finally {
      setLoaded(true);
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  // Opublikowane runy → opcjonalny split do wykrywania przecieku train/val.
  useEffect(() => {
    if (!id) return;
    api.getDatasetRuns(id)
      .then((index) => setPublishedRuns(
        index.runs.filter((r) => r.publication_status === "published" && r.status === "complete"),
      ))
      .catch(() => setPublishedRuns([]));
  }, [id]);

  // Poll tylko gdy coś liczy.
  useEffect(() => {
    if (!state || !RUNNING.has(state.status)) return;
    const timer = setInterval(load, 2000);
    return () => clearInterval(timer);
  }, [state, load]);

  const running = state != null && RUNNING.has(state.status);

  const runAnalysis = async () => {
    if (!id) return;
    setBusy(true);
    setUnavailable(null);
    try {
      await api.startEmbeddingAnalysis(id, { dataset_run_id: splitRunId || null });
      setState({ status: "queued", stage: "loading_backbone" });
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message;
      if (err?.response?.status === 400) {
        setUnavailable(detail);
      } else {
        toast({ title: t("Could not start analysis"), description: detail, status: "error", duration: 10000 });
      }
    } finally {
      setBusy(false);
    }
  };

  const deepLink = useCallback((obj: api.AnalysisObject) => {
    if (!id) return;
    // Edytor przyjmuje ognisko jako bbox pikselowy (x0,y0,x1,y1) w query `focus`.
    const focus = obj.bbox.map((v) => Math.round(v)).join(",");
    navigate(`/projects/${id}/scenes/${obj.scene_id}/label?focus=${focus}`);
  }, [id, navigate]);

  const showNeighbors = async (obj: api.AnalysisObject) => {
    if (!id || !obj.annotation_id) return;
    seedRef.current = obj;
    setNeighbors(null);
    setNeighborBusy(true);
    neighborModal.onOpen();
    try {
      const data = await api.getNearestObjects(id, obj.annotation_id, 20);
      setNeighbors(data.neighbors);
    } catch (err: any) {
      toast({
        title: t("Could not load similar objects"),
        description: err?.response?.data?.detail || err?.message,
        status: "error",
      });
      neighborModal.onClose();
    } finally {
      setNeighborBusy(false);
    }
  };

  const matrixData: api.ConfusionAnalysis | null = useMemo(() => {
    const cs = result?.class_similarity;
    if (!cs || cs.class_names.length < 2) return null;
    return {
      class_names: cs.class_names,
      matrix: [],
      orientation: null,
      similarity: cs.similarity,
      leaf_order: cs.leaf_order,
      confused_pairs: cs.similar_pairs,
    };
  }, [result]);

  const [selectedPair, setSelectedPair] = useState<[string, string] | null>(null);

  // Przykłady (galeria) zgrupowane po nazwie klasy + kolejność wyświetlania (leaf-order),
  // żeby paski szły w tej samej kolejności co macierz.
  const gallery = useMemo(() => {
    const cs = result?.class_similarity;
    const examples = result?.class_examples;
    if (!cs || !examples) return null;
    const idToName = new Map<number, string>();
    cs.class_ids.forEach((cid, i) => idToName.set(cid, cs.class_names[i]));
    const byName: Record<string, api.ClassExample[]> = {};
    for (const [cidStr, exs] of Object.entries(examples)) {
      const name = idToName.get(Number(cidStr));
      if (name) byName[name] = exs;
    }
    const order =
      cs.leaf_order.length === cs.class_names.length
        ? cs.leaf_order.map((i) => cs.class_names[i])
        : cs.class_names;
    const nameById: Record<number, string> = {};
    cs.class_ids.forEach((cid, i) => { nameById[cid] = cs.class_names[i]; });
    return { byName, order, nameById };
  }, [result]);

  if (!loaded) {
    return (
      <HStack p={6} spacing={3}><Spinner /><Text color={muted}>{t("Loading...")}</Text></HStack>
    );
  }

  return (
    <VStack align="stretch" spacing={5} p={2}>
      <Card>
        <VStack align="stretch" spacing={2}>
          <HStack justify="space-between" flexWrap="wrap" gap={2}>
            <Box>
              <Text fontWeight="bold">{t("Dataset analysis")}</Text>
              <Text fontSize="xs" color={muted}>
                {t("DINO embeddings over annotated objects - no training. Class similarity, suspected mislabels, outliers and near-duplicates, each linking back to the editor.")}
              </Text>
            </Box>
            <Button
              size="sm"
              colorScheme="brand"
              onClick={runAnalysis}
              isLoading={busy || running}
              loadingText={running ? t("Analyzing") : undefined}
            >
              {result ? t("Re-run analysis") : t("Run analysis")}
            </Button>
          </HStack>

          <Box borderWidth="1px" borderColor={cardBorder} borderRadius="md" p={3}>
            <HStack spacing={1} mb={1}>
              <Text fontSize="sm" fontWeight="600">{t("Train/val leakage check (optional)")}</Text>
              <InfoPopover
                titleKey="info.analysis.leakageCheck.title"
                bodyKey="info.analysis.leakageCheck.body"
                helpPage="datasets/analiza.html"
              />
            </HStack>
            {publishedRuns.length === 0 ? (
              <Text fontSize="xs" color="orange.400">
                {t("Publish a dataset version to check train/val leakage.")}
              </Text>
            ) : (
              <HStack spacing={2} flexWrap="wrap">
                <Text fontSize="xs" color={muted}>{t("Against version")}</Text>
                <Select
                  size="sm"
                  maxW="280px"
                  value={splitRunId}
                  onChange={(e) => setSplitRunId(e.target.value)}
                >
                  <option value="">{t("- none -")}</option>
                  {publishedRuns.map((r) => (
                    <option key={r.run_id} value={r.run_id}>
                      {r.publication_label || r.run_id.slice(0, 12)}
                    </option>
                  ))}
                </Select>
              </HStack>
            )}
            <Text fontSize="xs" color={muted} mt={1}>
              {t("Leakage = the same object in both train and val, which inflates validation metrics. Near-duplicate detection runs regardless; this only tags pairs that cross the split of the chosen version.")}
            </Text>
          </Box>

          {running && (
            <Box>
              <Progress size="sm" isIndeterminate borderRadius="sm" colorScheme="brand" />
              <Text fontSize="xs" color={muted} mt={1}>
                {t(STAGE_LABELS[state?.stage || ""] || "Working...")}
                {typeof state?.n_objects === "number" ? ` · ${state.n_objects} ${t("objects")}` : ""}
              </Text>
            </Box>
          )}

          {state?.status === "failed" && (
            <Alert status="error" borderRadius="md"><AlertIcon />{state.error || t("Analysis failed")}</Alert>
          )}
          {unavailable && (
            <Alert status="warning" borderRadius="md"><AlertIcon />{unavailable}</Alert>
          )}
        </VStack>
      </Card>

      {result && (
        <>
          <Card>
            <VStack align="stretch" spacing={2}>
              <HStack spacing={6} flexWrap="wrap">
                <Stat label={t("Objects")} value={String(result.n_objects)} />
                <Stat label={t("Classes")} value={String(result.per_class.length)} />
                <Stat label={t("Near-duplicates")} value={String(result.n_near_duplicates)} />
                {result.split_dataset_run_id && (
                  <Stat label={t("Train/val leaks")} value={String(result.n_leaks)} />
                )}
                <Stat label={t("Backbone")} value={result.backbone || "-"} mono />
                <Stat label={t("Generated")} value={new Date(result.generated_at).toLocaleString()} />
              </HStack>
              {result.split_dataset_run_id && !result.split_error && (
                <Text fontSize="xs" color={muted}>
                  {t("Leakage checked against a published run - {{tagged}} of {{total}} objects mapped to a split.", {
                    tagged: result.n_split_tagged, total: result.n_objects,
                  })}
                </Text>
              )}
              {result.split_error && (
                <Alert status="warning" borderRadius="md"><AlertIcon />{result.split_error}</Alert>
              )}
            </VStack>
          </Card>

          {matrixData && (
            <Card>
              <ConfusionSimilarityMatrix
                data={matrixData}
                title={t("Class similarity (embeddings)")}
                caption={t("Warmer tiles = classes DINO sees as more visually alike - available before any training. Order and similarity are guidance; merge/split decisions stay with you.")}
                contrastStretch
                onSelectPair={gallery ? (a, b) => setSelectedPair([a, b]) : undefined}
                selectedPair={selectedPair}
                info={{
                  titleKey: "info.analysis.classSimilarity.title",
                  bodyKey: "info.analysis.classSimilarity.body",
                  helpPage: "datasets/analiza.html",
                }}
              />
            </Card>
          )}

          {gallery && id && (
            <Card>
              <ClassExampleGallery
                projectId={id}
                examplesByName={gallery.byName}
                classOrder={gallery.order}
                classNameById={gallery.nameById}
                selectedPair={selectedPair}
                onClearPair={() => setSelectedPair(null)}
                info={{
                  titleKey: "info.analysis.classExamples.title",
                  bodyKey: "info.analysis.classExamples.body",
                  helpPage: "datasets/analiza.html",
                }}
              />
            </Card>
          )}

          <SimpleGrid columns={{ base: 1, xl: 2 }} gap={5}>
            {result.class_cohesion.length > 0 && (
              <Card>
                <HStack mb={1} spacing={1}>
                  <Text fontWeight="600">{t("Class cohesion")}</Text>
                  <InfoPopover titleKey="info.analysis.classCohesion.title" bodyKey="info.analysis.classCohesion.body" helpPage="datasets/analiza.html" />
                </HStack>
                <Text fontSize="xs" color={muted} mb={2}>
                  {t("Average similarity of a class's objects to their own prototype. Low = visually incoherent class.")}
                </Text>
                <Box overflowX="auto">
                  <Table size="sm">
                    <Thead><Tr>
                      <Th>{t("Class")}</Th><Th isNumeric>{t("Count")}</Th>
                      <Th isNumeric>{t("Cohesion")}</Th><Th isNumeric>{t("Spread")}</Th>
                    </Tr></Thead>
                    <Tbody>
                      {result.class_cohesion.map((c) => (
                        <Tr key={c.class_id}>
                          <Td>{c.name}</Td>
                          <Td isNumeric>{c.count}</Td>
                          <Td isNumeric>
                            <Badge colorScheme={c.cohesion < 0.85 ? "orange" : c.cohesion < 0.92 ? "yellow" : "green"}>
                              {c.cohesion.toFixed(3)}
                            </Badge>
                          </Td>
                          <Td isNumeric>{c.spread.toFixed(3)}</Td>
                        </Tr>
                      ))}
                    </Tbody>
                  </Table>
                </Box>
              </Card>
            )}

            <Card>
              <HStack mb={1} spacing={1}>
                <Text fontWeight="600">{t("Suspected mislabels")}</Text>
                <Badge colorScheme={result.suspected_mislabels.length ? "red" : "green"}>
                  {result.suspected_mislabels.length}
                </Badge>
                <InfoPopover titleKey="info.analysis.suspectedMislabels.title" bodyKey="info.analysis.suspectedMislabels.body" helpPage="datasets/analiza.html" />
              </HStack>
              <Text fontSize="xs" color={muted} mb={2}>
                {t("Objects closer to another class's prototype than their own. Review and fix in the editor.")}
              </Text>
              {result.suspected_mislabels.length === 0 ? (
                <Text fontSize="sm" color={muted}>{t("Nothing flagged.")}</Text>
              ) : (
                <VStack align="stretch" spacing={1} maxH="360px" overflowY="auto">
                  {result.suspected_mislabels.map((m, i) => (
                    <HStack key={i} justify="space-between" borderBottomWidth="1px" py={1} fontSize="sm">
                      <Box minW={0}>
                        <Text noOfLines={1}>
                          {m.class_name} <Text as="span" color={muted}>→</Text> {m.suggested_class_name}
                        </Text>
                        <Text fontSize="xs" color={muted}>
                          {t("gap")} {m.gap.toFixed(3)} · {t("own")} {m.own_similarity.toFixed(3)} · {t("suggested")} {m.suggested_similarity.toFixed(3)}
                        </Text>
                      </Box>
                      <HStack spacing={1}>
                        {m.annotation_id && (
                          <Button size="xs" variant="ghost" onClick={() => showNeighbors(m)}>{t("Similar")}</Button>
                        )}
                        <Button size="xs" variant="outline" onClick={() => deepLink(m)}>{t("Fix")}</Button>
                      </HStack>
                    </HStack>
                  ))}
                </VStack>
              )}
            </Card>
          </SimpleGrid>

          <SimpleGrid columns={{ base: 1, xl: 2 }} gap={5}>
            <Card>
              <HStack mb={1} spacing={1}>
                <Text fontWeight="600">{t("Class outliers")}</Text>
                <Badge>{result.class_outliers.length}</Badge>
                <InfoPopover titleKey="info.analysis.classOutliers.title" bodyKey="info.analysis.classOutliers.body" helpPage="datasets/analiza.html" />
              </HStack>
              <Text fontSize="xs" color={muted} mb={2}>
                {t("Objects far from their own class prototype (but not closer to another). Hard examples or bad crops.")}
              </Text>
              {result.class_outliers.length === 0 ? (
                <Text fontSize="sm" color={muted}>{t("Nothing flagged.")}</Text>
              ) : (
                <VStack align="stretch" spacing={1} maxH="360px" overflowY="auto">
                  {result.class_outliers.map((o, i) => (
                    <HStack key={i} justify="space-between" borderBottomWidth="1px" py={1} fontSize="sm">
                      <Text noOfLines={1}>{o.class_name}</Text>
                      <HStack spacing={1}>
                        <Text fontSize="xs" color={muted}>{o.own_similarity.toFixed(3)}</Text>
                        {o.annotation_id && (
                          <Button size="xs" variant="ghost" onClick={() => showNeighbors(o)}>{t("Similar")}</Button>
                        )}
                        <Button size="xs" variant="outline" onClick={() => deepLink(o)}>{t("Open")}</Button>
                      </HStack>
                    </HStack>
                  ))}
                </VStack>
              )}
            </Card>

            <Card>
              <HStack mb={1} spacing={1}>
                <Text fontWeight="600">{t("Near-duplicates")}</Text>
                <Badge colorScheme={result.near_duplicates.length ? "orange" : "green"}>
                  {result.near_duplicates.length}
                </Badge>
                <InfoPopover titleKey="info.analysis.nearDuplicates.title" bodyKey="info.analysis.nearDuplicates.body" helpPage="datasets/analiza.html" />
              </HStack>
              <Text fontSize="xs" color={muted} mb={2}>
                {t("Object pairs with near-identical embeddings - redundant annotations, or train/val leakage when split-tagged.")}
              </Text>
              {result.split_dataset_run_id ? (
                <Text fontSize="xs" color={result.n_leaks ? "red.400" : "green.400"} mb={2}>
                  {t("Leakage checked against {{version}} - {{count}} pair(s) cross train/val.", {
                    version:
                      publishedRuns.find((r) => r.run_id === result.split_dataset_run_id)?.publication_label ||
                      result.split_dataset_run_id.slice(0, 12),
                    count: result.n_leaks,
                  })}
                </Text>
              ) : (
                <Text fontSize="xs" color={muted} mb={2}>
                  {t("Not checked for leakage. Pick a dataset version above and re-run to tag train/val leaks.")}
                </Text>
              )}
              {result.near_duplicates.length === 0 ? (
                <Text fontSize="sm" color={muted}>{t("No near-duplicates above threshold.")}</Text>
              ) : (
                <VStack align="stretch" spacing={1} maxH="360px" overflowY="auto">
                  {result.near_duplicates.map((p, i) => (
                    <HStack key={i} justify="space-between" borderBottomWidth="1px" py={1} fontSize="sm">
                      <Box minW={0}>
                        <HStack spacing={1}>
                          <Text noOfLines={1}>{p.a.class_name} ↔ {p.b.class_name}</Text>
                          {p.cross_split && <Badge colorScheme="red">{t("leak")}</Badge>}
                          {!p.same_class && <Badge colorScheme="purple">{t("cross-class")}</Badge>}
                        </HStack>
                        <Text fontSize="xs" color={muted}>
                          {t("similarity")} {p.similarity.toFixed(3)}
                          {p.split_a && p.split_b ? ` · ${p.split_a} ↔ ${p.split_b}` : ""}
                        </Text>
                      </Box>
                      <HStack spacing={1}>
                        <Button size="xs" variant="outline" onClick={() => deepLink(p.a)}>A</Button>
                        <Button size="xs" variant="outline" onClick={() => deepLink(p.b)}>B</Button>
                      </HStack>
                    </HStack>
                  ))}
                </VStack>
              )}
            </Card>
          </SimpleGrid>
        </>
      )}

      {!result && !running && (
        <Alert status="info" borderRadius="md">
          <AlertIcon />
          {t("No analysis yet. Run it to embed the annotated objects and inspect class similarity, mislabels and duplicates.")}
        </Alert>
      )}

      <Modal isOpen={neighborModal.isOpen} onClose={neighborModal.onClose} size="lg" scrollBehavior="inside">
        <ModalOverlay />
        <ModalContent>
          <ModalHeader>
            {t("Most similar objects")}
            {seedRef.current?.class_name && (
              <Text fontSize="sm" fontWeight="normal" color={muted}>{seedRef.current.class_name}</Text>
            )}
          </ModalHeader>
          <ModalCloseButton />
          <ModalBody pb={4}>
            {neighborBusy ? (
              <HStack spacing={3}><Spinner /><Text color={muted}>{t("Loading...")}</Text></HStack>
            ) : neighbors && neighbors.length > 0 ? (
              <VStack align="stretch" spacing={1}>
                {neighbors.map((nb, i) => (
                  <HStack key={i} justify="space-between" borderBottomWidth="1px" py={1} fontSize="sm">
                    <Text noOfLines={1}>{nb.class_name}</Text>
                    <HStack spacing={2}>
                      <Text fontSize="xs" color={muted}>{nb.similarity.toFixed(3)}</Text>
                      <Button size="xs" variant="outline" onClick={() => { neighborModal.onClose(); deepLink(nb); }}>
                        {t("Open")}
                      </Button>
                    </HStack>
                  </HStack>
                ))}
              </VStack>
            ) : (
              <Text color={muted}>{t("No similar objects found.")}</Text>
            )}
          </ModalBody>
        </ModalContent>
      </Modal>
    </VStack>
  );
}

function Stat({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  const muted = useColorModeValue("secondaryGray.600", "whiteAlpha.600");
  return (
    <Box>
      <Text fontSize="xs" color={muted}>{label}</Text>
      <Text fontWeight="bold" fontFamily={mono ? "mono" : undefined} fontSize={mono ? "sm" : undefined}>
        {value}
      </Text>
    </Box>
  );
}
