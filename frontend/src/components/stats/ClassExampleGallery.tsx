import { useEffect, useMemo, useState } from "react";
import {
  Box,
  HStack,
  IconButton,
  Image,
  Modal,
  ModalBody,
  ModalCloseButton,
  ModalContent,
  ModalOverlay,
  SimpleGrid,
  Text,
  VStack,
  useColorModeValue,
} from "@chakra-ui/react";
import { MdChevronLeft, MdChevronRight, MdClose } from "react-icons/md";
import { useTranslation } from "react-i18next";
import InfoPopover from "../common/InfoPopover";
import * as api from "../../api/client";
import type { ClassExample } from "../../api/client";

interface Props {
  projectId: string;
  /** Przykłady zgrupowane po nazwie klasy. */
  examplesByName: Record<string, ClassExample[]>;
  /** Nazwy klas w kolejności wyświetlania (spójnej z macierzą). */
  classOrder: string[];
  /** Mapa class_id -> nazwa (do opisu „mylone z …"). */
  classNameById: Record<number, string>;
  /** Zaznaczona para do porównania obok siebie (nazwy klas). */
  selectedPair: [string, string] | null;
  onClearPair: () => void;
  /** Opcjonalna ikona (i) przy tytule. */
  info?: { titleKey: string; bodyKey: string; helpPage?: string };
}

interface FlatChip {
  key: string;
  src: string;
  className: string;
  ex: ClassExample;
}

const CHIP = 64;

/**
 * Galeria wycinków przykładów per klasa (bbox + margines kontekstu, renderowane raz razem
 * z analizą embeddingów). Domyka pętlę „macierz pokazuje że klasy są podobne → zobacz na
 * czym". W każdej klasie przykłady są rozdzielone na **typowe** (najbliżej wzorca klasy)
 * i **graniczne** (najbliżej mylonej klasy) — grupowane podpisem, nie kruchą ramką. Klik w
 * kafel otwiera powiększony podgląd z nawigacją strzałkami.
 */
export default function ClassExampleGallery({
  projectId,
  examplesByName,
  classOrder,
  classNameById,
  selectedPair,
  onClearPair,
  info,
}: Props) {
  const { t } = useTranslation();
  const border = useColorModeValue("gray.200", "whiteAlpha.300");
  const muted = useColorModeValue("secondaryGray.600", "whiteAlpha.600");
  const panelBg = useColorModeValue("blackAlpha.50", "whiteAlpha.100");

  const withExamples = useMemo(
    () => classOrder.filter((name) => (examplesByName[name]?.length ?? 0) > 0),
    [classOrder, examplesByName],
  );

  // Płaska lista wszystkich kafli w kolejności wyświetlania (typowe → graniczne w każdej
  // klasie) — po niej nawiguje powiększony podgląd.
  const flat = useMemo<FlatChip[]>(() => {
    const arr: FlatChip[] = [];
    for (const name of withExamples) {
      const exs = examplesByName[name] || [];
      for (const ex of [...exs].sort((a, b) => rank(a) - rank(b))) {
        arr.push({
          key: `${name}::${ex.annotation_id}`,
          src: api.embeddingChipUrl(projectId, ex.thumb),
          className: name,
          ex,
        });
      }
    }
    return arr;
  }, [withExamples, examplesByName, projectId]);

  const [zoomKey, setZoomKey] = useState<string | null>(null);
  const zoomIndex = zoomKey ? flat.findIndex((f) => f.key === zoomKey) : -1;
  const zoom = zoomIndex >= 0 ? flat[zoomIndex] : null;

  useEffect(() => {
    if (zoomIndex < 0) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "ArrowRight") setZoomKey(flat[Math.min(zoomIndex + 1, flat.length - 1)]?.key ?? null);
      else if (event.key === "ArrowLeft") setZoomKey(flat[Math.max(zoomIndex - 1, 0)]?.key ?? null);
      else if (event.key === "Escape") setZoomKey(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoomIndex, flat]);

  if (withExamples.length === 0) return null;

  const openZoom = (name: string, ex: ClassExample) => setZoomKey(`${name}::${ex.annotation_id}`);

  return (
    <Box borderWidth="1px" borderColor={border} borderRadius="md" p={3} minW={0}>
      <HStack spacing={1} mb={1}>
        <Text fontSize="sm" fontWeight="600">{t("Class examples")}</Text>
        {info && <InfoPopover titleKey={info.titleKey} bodyKey={info.bodyKey} helpPage={info.helpPage} />}
      </HStack>
      <Text fontSize="xs" color={muted} mb={3}>
        {t("Crops from the scene (box + context). Click a tile to enlarge. Typical = near the class prototype; boundary = nearest the confusable class.")}
      </Text>

      {selectedPair && (
        <Box bg={panelBg} borderRadius="md" p={3} mb={3}>
          <HStack justify="space-between" mb={2}>
            <Text fontSize="sm" fontWeight="600">{selectedPair[0]} ↔ {selectedPair[1]}</Text>
            <IconButton aria-label={t("Clear selection")} size="xs" variant="ghost" icon={<MdClose />} onClick={onClearPair} />
          </HStack>
          <SimpleGrid columns={{ base: 1, md: 2 }} spacing={4}>
            {selectedPair.map((name) => (
              <ClassChips
                key={name}
                name={name}
                examples={examplesByName[name] || []}
                projectId={projectId}
                classNameById={classNameById}
                muted={muted}
                onZoom={openZoom}
                t={t}
                showHeading
              />
            ))}
          </SimpleGrid>
        </Box>
      )}

      <VStack align="stretch" spacing={3}>
        {withExamples.map((name) => (
          <ClassChips
            key={name}
            name={name}
            examples={examplesByName[name] || []}
            projectId={projectId}
            classNameById={classNameById}
            muted={muted}
            onZoom={openZoom}
            t={t}
            showHeading
          />
        ))}
      </VStack>

      <Modal isOpen={zoomIndex >= 0} onClose={() => setZoomKey(null)} isCentered size="xl">
        <ModalOverlay />
        <ModalContent bg="transparent" boxShadow="none">
          <ModalCloseButton color="white" bg="blackAlpha.600" borderRadius="full" zIndex={2} />
          <ModalBody p={0}>
            {zoom && (
              <VStack spacing={2}>
                <HStack w="100%" justify="center" align="center" spacing={3}>
                  <IconButton
                    aria-label={t("Previous")}
                    icon={<MdChevronLeft size={28} />}
                    onClick={() => setZoomKey(flat[Math.max(zoomIndex - 1, 0)]?.key ?? null)}
                    isDisabled={zoomIndex <= 0}
                    variant="ghost"
                    color="white"
                    _hover={{ bg: "whiteAlpha.300" }}
                  />
                  <Image
                    src={zoom.src}
                    alt={zoom.className}
                    maxH="70vh"
                    maxW="70vw"
                    minW="240px"
                    borderRadius="md"
                    objectFit="contain"
                    bg="black"
                  />
                  <IconButton
                    aria-label={t("Next")}
                    icon={<MdChevronRight size={28} />}
                    onClick={() => setZoomKey(flat[Math.min(zoomIndex + 1, flat.length - 1)]?.key ?? null)}
                    isDisabled={zoomIndex >= flat.length - 1}
                    variant="ghost"
                    color="white"
                    _hover={{ bg: "whiteAlpha.300" }}
                  />
                </HStack>
                <Box bg="blackAlpha.700" borderRadius="md" px={3} py={1} color="white" textAlign="center">
                  <Text fontSize="sm" fontWeight="600">
                    {zoom.className}
                    {" · "}
                    {zoom.ex.kind === "boundary" ? t("Boundary") : t("Typical")}
                  </Text>
                  <Text fontSize="xs" opacity={0.8}>
                    {zoom.ex.kind === "boundary" && zoom.ex.other_class_id != null
                      ? `${t("confused with {{other}}", { other: classNameById[zoom.ex.other_class_id] ?? "?" })} · `
                      : ""}
                    {t("similarity")} {zoom.ex.own_similarity.toFixed(3)} · {zoomIndex + 1} / {flat.length}
                  </Text>
                </Box>
              </VStack>
            )}
          </ModalBody>
        </ModalContent>
      </Modal>
    </Box>
  );
}

function rank(ex: ClassExample): number {
  return ex.kind === "boundary" ? 1 : 0; // typowe pierwsze, graniczne po nich
}

function ClassChips({
  name,
  examples,
  projectId,
  classNameById,
  muted,
  onZoom,
  t,
  showHeading,
}: {
  name: string;
  examples: ClassExample[];
  projectId: string;
  classNameById: Record<number, string>;
  muted: string;
  onZoom: (name: string, ex: ClassExample) => void;
  t: (key: string, opts?: Record<string, unknown>) => string;
  showHeading?: boolean;
}) {
  const proto = examples.filter((ex) => ex.kind !== "boundary");
  const boundary = examples.filter((ex) => ex.kind === "boundary");
  const otherId = boundary.find((ex) => ex.other_class_id != null)?.other_class_id;
  const otherName = otherId != null ? classNameById[otherId] : undefined;

  return (
    <Box>
      {showHeading && <Text fontSize="xs" fontWeight="700" mb={1}>{name}</Text>}
      {proto.length > 0 && (
        <ChipGroup
          label={t("Typical")}
          examples={proto}
          projectId={projectId}
          name={name}
          muted={muted}
          onZoom={onZoom}
          t={t}
        />
      )}
      {boundary.length > 0 && (
        <ChipGroup
          label={otherName ? `${t("Boundary")} - ${t("confused with {{other}}", { other: otherName })}` : t("Boundary")}
          examples={boundary}
          projectId={projectId}
          name={name}
          muted={muted}
          onZoom={onZoom}
          t={t}
        />
      )}
    </Box>
  );
}

function ChipGroup({
  label,
  examples,
  projectId,
  name,
  muted,
  onZoom,
  t,
}: {
  label: string;
  examples: ClassExample[];
  projectId: string;
  name: string;
  muted: string;
  onZoom: (name: string, ex: ClassExample) => void;
  t: (key: string, opts?: Record<string, unknown>) => string;
}) {
  return (
    <Box mb={1}>
      <Text fontSize="10px" color={muted} mb={1}>{label}</Text>
      <HStack spacing={2} flexWrap="wrap" align="flex-start">
        {examples.map((ex) => (
          <Image
            key={ex.annotation_id}
            src={api.embeddingChipUrl(projectId, ex.thumb)}
            alt={name}
            title={`${t("similarity")} ${ex.own_similarity.toFixed(2)}`}
            boxSize={`${CHIP}px`}
            objectFit="cover"
            borderRadius="md"
            cursor="zoom-in"
            flexShrink={0}
            loading="lazy"
            onClick={() => onZoom(name, ex)}
            _hover={{ outline: "2px solid", outlineColor: "brand.400" }}
          />
        ))}
      </HStack>
    </Box>
  );
}
