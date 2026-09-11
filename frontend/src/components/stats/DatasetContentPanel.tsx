import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  HStack,
  Image,
  Modal,
  ModalBody,
  ModalCloseButton,
  ModalContent,
  ModalHeader,
  ModalOverlay,
  Select,
  SimpleGrid,
  Spinner,
  Text,
  VStack,
  useColorModeValue,
  useDisclosure,
} from "@chakra-ui/react";
import { useTranslation } from "react-i18next";
import Card from "../common/Card";
import * as api from "../../api/client";

interface Props {
  projectId: string;
  runId: string | null;
}

const PAGE = 60;
const FALLBACK_COLOR = "#8a8f98";

/**
 * Przegląd zawartości opublikowanego, zamrożonego runu (DI-F): statystyki + realne kafle
 * z nałożonymi boxami i klasami, filtr split×klasa, oraz deep-link „otwórz scenę w tym
 * miejscu". Read-only wobec runu — poprawki idą na żywą scenę i nową wersję datasetu.
 */
export default function DatasetContentPanel({ projectId, runId }: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const muted = useColorModeValue("secondaryGray.600", "whiteAlpha.600");
  const tileBg = useColorModeValue("gray.100", "whiteAlpha.100");

  const [summary, setSummary] = useState<api.DatasetContentSummary | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [split, setSplit] = useState<string>("");
  const [classId, setClassId] = useState<number | null>(null);

  const [items, setItems] = useState<api.DatasetContentSample[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const [active, setActive] = useState<api.DatasetContentSample | null>(null);
  const { isOpen, onOpen, onClose } = useDisclosure();

  // Nowy run → nowe podsumowanie i domyślny split.
  useEffect(() => {
    setSummary(null);
    setUnavailable(false);
    setItems([]);
    setTotal(0);
    if (!runId) return;
    let cancelled = false;
    api
      .getDatasetContentSummary(projectId, runId)
      .then((data) => {
        if (cancelled) return;
        setSummary(data);
        setSplit(data.splits[0]?.split ?? "");
        setClassId(null);
      })
      .catch(() => {
        if (!cancelled) setUnavailable(true);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, runId]);

  const loadPage = useCallback(
    async (offset: number, replace: boolean) => {
      if (!runId || !split) return;
      setLoading(true);
      try {
        const data = await api.getDatasetContentSamples(projectId, runId, {
          split,
          class_id: classId,
          offset,
          limit: PAGE,
        });
        setTotal(data.total);
        setItems((prev) => (replace ? data.items : [...prev, ...data.items]));
      } catch {
        if (replace) setItems([]);
      } finally {
        setLoading(false);
      }
    },
    [projectId, runId, split, classId]
  );

  // Zmiana filtra split/klasa → od nowa.
  useEffect(() => {
    if (!runId || !split) return;
    void loadPage(0, true);
  }, [runId, split, classId, loadPage]);

  const classOptions = useMemo(() => summary?.classes ?? [], [summary]);

  const openSample = (sample: api.DatasetContentSample) => {
    setActive(sample);
    onOpen();
  };

  const openInEditor = (sample: api.DatasetContentSample) => {
    if (!sample.scene_id || sample.tile_x0 == null || sample.tile_y0 == null) return;
    const x1 = sample.tile_x0 + sample.tile_size;
    const y1 = sample.tile_y0 + sample.tile_size;
    const focus = `${sample.tile_x0},${sample.tile_y0},${x1},${y1}`;
    navigate(`/projects/${projectId}/scenes/${sample.scene_id}/label?focus=${focus}`);
  };

  if (!runId) {
    return (
      <Card>
        <Text fontSize="sm" color={muted}>
          {t("Select a dataset run to inspect its contents.")}
        </Text>
      </Card>
    );
  }

  if (unavailable) {
    return (
      <Card>
        <Text fontSize="sm" color={muted}>
          {t("Dataset content is not available for this run.")}
        </Text>
      </Card>
    );
  }

  if (!summary) {
    return (
      <Card>
        <HStack color={muted}>
          <Spinner size="sm" />
          <Text fontSize="sm">{t("Loading dataset content...")}</Text>
        </HStack>
      </Card>
    );
  }

  return (
    <Card>
      <VStack align="stretch" spacing={4}>
        <HStack spacing={3} flexWrap="wrap">
          <VStack align="start" spacing={0} minW="120px">
            <Text fontSize="xs" color={muted}>{t("Split")}</Text>
            <Select size="sm" value={split} onChange={(e) => setSplit(e.target.value)} maxW="180px">
              {summary.splits.map((s) => (
                <option key={s.split} value={s.split}>
                  {s.split} ({s.tiles})
                </option>
              ))}
            </Select>
          </VStack>
          <VStack align="start" spacing={0} minW="180px">
            <Text fontSize="xs" color={muted}>{t("Class")}</Text>
            <Select
              size="sm"
              value={classId ?? ""}
              onChange={(e) => setClassId(e.target.value === "" ? null : Number(e.target.value))}
              maxW="260px"
            >
              <option value="">{t("All classes")}</option>
              {classOptions.map((c) => (
                <option key={c.class_id} value={c.class_id}>
                  {c.name} ({c.per_split[split] ?? 0})
                </option>
              ))}
            </Select>
          </VStack>
          <VStack align="start" spacing={0} justify="flex-end" flex="1">
            <Text fontSize="xs" color={muted}>&nbsp;</Text>
            <Text fontSize="sm" color={muted}>
              {t("Showing")} {items.length} / {total}
            </Text>
          </VStack>
        </HStack>

        <SimpleGrid columns={{ base: 2, sm: 3, md: 4, lg: 6 }} spacing={2}>
          {items.map((sample) => (
            <TileThumb
              key={`${sample.split}/${sample.filename}`}
              projectId={projectId}
              runId={runId}
              sample={sample}
              bg={tileBg}
              onClick={() => openSample(sample)}
            />
          ))}
        </SimpleGrid>

        {items.length === 0 && !loading && (
          <Text fontSize="sm" color={muted}>{t("No tiles match this filter.")}</Text>
        )}

        {items.length < total && (
          <Button
            size="sm"
            variant="outline"
            alignSelf="center"
            isLoading={loading}
            onClick={() => void loadPage(items.length, false)}
          >
            {t("Load more")}
          </Button>
        )}
      </VStack>

      <Modal isOpen={isOpen} onClose={onClose} size="xl" isCentered>
        <ModalOverlay />
        <ModalContent>
          <ModalHeader fontSize="sm">
            {active?.filename}
            {active?.scene_id && (
              <Text as="span" fontSize="xs" color={muted} ml={2}>
                {t("scene")}: {active.scene_id}
              </Text>
            )}
          </ModalHeader>
          <ModalCloseButton />
          <ModalBody pb={5}>
            {active && (
              <VStack align="stretch" spacing={3}>
                <TileImageWithBoxes
                  src={api.datasetSampleImageUrl(projectId, runId, active.split, active.filename)}
                  boxes={active.boxes}
                  bg={tileBg}
                />
                <TileLegend boxes={active.boxes} muted={muted} />
                <HStack justify="space-between" flexWrap="wrap" gap={2}>
                  <Text fontSize="xs" color={muted}>
                    {active.boxes.length} {t("boxes")}
                  </Text>
                  <Button
                    size="sm"
                    colorScheme="brand"
                    isDisabled={!active.scene_id || active.tile_x0 == null}
                    onClick={() => openInEditor(active)}
                  >
                    {t("Open scene here")}
                  </Button>
                </HStack>
                <Text fontSize="xs" color={muted}>
                  {t("Opens the live scene. Fixes create a new dataset version; this run stays frozen.")}
                </Text>
              </VStack>
            )}
          </ModalBody>
        </ModalContent>
      </Modal>
    </Card>
  );
}

function TileThumb({
  projectId,
  runId,
  sample,
  bg,
  onClick,
}: {
  projectId: string;
  runId: string;
  sample: api.DatasetContentSample;
  bg: string;
  onClick: () => void;
}) {
  // Legenda celowo TYLKO w powiększeniu — w galerii zmienna liczba klas na kaflu
  // rozjeżdżałaby wysokości kafelków. W galerii identyfikacja przez kolor + filtr klasy.
  return (
    <Box cursor="pointer" onClick={onClick} borderRadius="md" overflow="hidden">
      <TileImageWithBoxes
        src={api.datasetSampleImageUrl(projectId, runId, sample.split, sample.filename)}
        boxes={sample.boxes}
        bg={bg}
      />
    </Box>
  );
}

function TileImageWithBoxes({
  src,
  boxes,
  bg,
}: {
  src: string;
  boxes: api.DatasetContentBox[];
  bg: string;
}) {
  // Boxy YOLO są znormalizowane 0..1 do kafla, więc pozycjonujemy je procentowo nad
  // obrazem — skalują się automatycznie z rozmiarem miniatury. Bez podpisów na obrazie
  // (zasłaniałyby treść) — klasy czyta się z legendy pod kaflem.
  return (
    <Box position="relative" w="100%" sx={{ aspectRatio: "1 / 1" }} bg={bg}>
      <Image src={src} alt="" w="100%" h="100%" objectFit="contain" loading="lazy" draggable={false} />
      {boxes.map((box, i) => (
        <Box
          key={i}
          position="absolute"
          left={`${(box.cx - box.w / 2) * 100}%`}
          top={`${(box.cy - box.h / 2) * 100}%`}
          w={`${box.w * 100}%`}
          h={`${box.h * 100}%`}
          border="2px solid"
          borderColor={box.color || FALLBACK_COLOR}
          pointerEvents="none"
        />
      ))}
    </Box>
  );
}

/** Klasy obecne na danym kaflu (bez duplikatów), zachowując kolejność pierwszego wystąpienia. */
function tileClasses(boxes: api.DatasetContentBox[]) {
  const seen = new Map<number, { name: string; color: string }>();
  for (const box of boxes) {
    if (!seen.has(box.class_id)) {
      seen.set(box.class_id, { name: box.name, color: box.color || FALLBACK_COLOR });
    }
  }
  return [...seen.entries()].map(([classId, value]) => ({ classId, ...value }));
}

/** Legenda kolorów pod kaflem — tylko klasy występujące na tym kaflu. */
function TileLegend({ boxes, muted }: { boxes: api.DatasetContentBox[]; muted: string }) {
  const classes = tileClasses(boxes);
  if (classes.length === 0) return null;
  return (
    <Box display="flex" flexWrap="wrap" gap="2px 8px" mt="3px" px="1px">
      {classes.map((cls) => (
        <Box key={cls.classId} display="flex" alignItems="center" gap="4px" minW={0}>
          <Box w="8px" h="8px" borderRadius="2px" bg={cls.color} flexShrink={0} />
          <Text fontSize="10px" lineHeight="1.2" color={muted} noOfLines={1}>
            {cls.name}
          </Text>
        </Box>
      ))}
    </Box>
  );
}
