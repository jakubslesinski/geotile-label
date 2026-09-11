import { useMemo, useState } from "react";
import {
  Badge,
  Box,
  Checkbox,
  HStack,
  Icon,
  IconButton,
  Input,
  Text,
  Tooltip,
  VStack,
  useColorModeValue,
} from "@chakra-ui/react";
import { MdChevronRight, MdDeleteOutline, MdStar, MdStarOutline } from "react-icons/md";
import { useTranslation } from "react-i18next";
import type { TrainingRunSummary } from "../../api/client";

const STATUS_COLORS: Record<string, string> = {
  complete: "green",
  completed: "green",
  running: "purple",
  queued: "blue",
  cancelled: "gray",
  failed: "red",
  interrupted: "orange",
};

function metric(run: TrainingRunSummary, key: string): number | null {
  const value = run.metrics?.[key];
  return typeof value === "number" ? value : null;
}

function fmt(value: number | null): string {
  return value == null ? "-" : value.toFixed(3);
}

interface RunsRailProps {
  runs: TrainingRunSummary[];
  selectedRunIds: Set<string>;
  focusRunId: string | null;
  baselineRunId: string | null;
  runColors: Map<string, string>;
  onToggleSelect: (runId: string) => void;
  onSetFocus: (runId: string) => void;
  onSetBaseline: (runId: string) => void;
  onDeleteRun?: (runId: string) => void;
  /** Zwinięcie railu (chowa panel, oddaje szerokość wykresom). */
  onCollapse?: () => void;
}

/**
 * Zwijany panel przebiegów treningu (styl W&B). Wielokrotny wybór (checkbox) steruje panelami
 * porównawczymi; klik w nazwę ustawia „focus" dla paneli jednorunowych; gwiazdka wybiera
 * baseline do delt. Runy grupowane po konfiguracji (najlepszy mAP na górze), kolor z `runColors`.
 */
export default function RunsRail({
  runs,
  selectedRunIds,
  focusRunId,
  baselineRunId,
  runColors,
  onToggleSelect,
  onSetFocus,
  onSetBaseline,
  onDeleteRun,
  onCollapse,
}: RunsRailProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");

  const border = useColorModeValue("gray.200", "whiteAlpha.300");
  const muted = useColorModeValue("secondaryGray.600", "whiteAlpha.600");
  const focusBg = useColorModeValue("blackAlpha.50", "whiteAlpha.100");
  const groupBg = useColorModeValue("gray.50", "whiteAlpha.100");
  const dotEmpty = useColorModeValue("#CBD5E0", "#4A5568");

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = runs.filter((run) => {
      if (!q) return true;
      const hay = `${run.base_model ?? ""} ${run.task ?? ""} #${run.attempt ?? ""} ${run.config_fingerprint ?? ""}`.toLowerCase();
      return hay.includes(q);
    });
    const byFingerprint = new Map<string, TrainingRunSummary[]>();
    for (const run of filtered) {
      const key = run.config_fingerprint || run.training_run_id;
      byFingerprint.set(key, [...(byFingerprint.get(key) || []), run]);
    }
    // Domyślny układ: najlepszy mAP50-95 na górze (w grupie i między grupami).
    const sortRun = (a: TrainingRunSummary, b: TrainingRunSummary) =>
      (metric(b, "mAP50-95") ?? -1) - (metric(a, "mAP50-95") ?? -1);
    const entries = [...byFingerprint.entries()].map(([fingerprint, items]) => ({
      fingerprint,
      label: `${items[0].base_model || "?"} · ${items[0].task || "?"}`,
      bestMap: Math.max(...items.map((r) => metric(r, "mAP50-95") ?? -1)),
      runs: [...items].sort(sortRun),
    }));
    entries.sort((a, b) => b.bestMap - a.bestMap);
    return entries;
  }, [runs, query]);

  return (
    <Box borderWidth="1px" borderColor={border} borderRadius="md" p={2} minW={0}>
      <HStack mb={2} spacing={2}>
        <Text fontSize="sm" fontWeight="700" flex="1">{t("Training runs")}</Text>
        <Badge>{runs.length}</Badge>
        {onCollapse && (
          <Tooltip label={t("Collapse")} openDelay={400}>
            <IconButton
              aria-label={t("Collapse")}
              size="xs"
              variant="ghost"
              icon={<Icon as={MdChevronRight} />}
              onClick={onCollapse}
            />
          </Tooltip>
        )}
      </HStack>
      <Input
        size="sm"
        mb={2}
        placeholder={t("Search runs")}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

      {/* Bez własnego scrolla: sticky panel obok głównego scrolla dawał DWA suwaki przy
          prawej krawędzi, nachodzące na przyciski wiersza (gwiazdka/kosz) i utrudniające klik.
          Lista płynie w jednym, głównym scrollu; panel pozostaje sticky. */}
      <VStack align="stretch" spacing={2}>
        {groups.length === 0 && (
          <Text fontSize="xs" color={muted} px={1}>{t("No training runs for this dataset yet.")}</Text>
        )}
        {groups.map((group) => (
          <Box key={group.fingerprint}>
            <HStack bg={groupBg} px={2} py={1} borderRadius="sm" spacing={2}>
              <Text fontSize="xs" fontWeight="700" noOfLines={1}>{group.label}</Text>
              <Text fontSize="10px" color={muted}>{group.fingerprint.slice(0, 8)}</Text>
            </HStack>
            {group.runs.map((run) => {
              const rid = run.training_run_id;
              const selected = selectedRunIds.has(rid);
              const color = runColors.get(rid);
              const isFocus = focusRunId === rid;
              const isBaseline = baselineRunId === rid;
              return (
                <HStack
                  key={rid}
                  px={2}
                  py={1}
                  spacing={2}
                  borderRadius="sm"
                  bg={isFocus ? focusBg : undefined}
                  _hover={{ bg: focusBg }}
                >
                  <Checkbox
                    size="sm"
                    isChecked={selected}
                    onChange={() => onToggleSelect(rid)}
                    aria-label={t("Include in comparison")}
                  />
                  <Box
                    w="10px"
                    h="10px"
                    borderRadius="full"
                    flexShrink={0}
                    bg={selected && color ? color : dotEmpty}
                  />
                  <Box flex="1" minW={0} cursor="pointer" onClick={() => onSetFocus(rid)}>
                    <Text fontSize="xs" fontWeight={isFocus ? "700" : "500"} noOfLines={1}>
                      #{run.attempt} · {run.base_model || "?"}
                    </Text>
                    <HStack spacing={2}>
                      <Badge fontSize="9px" colorScheme={STATUS_COLORS[run.status] || "gray"}>
                        {t(`trainingStatus.${run.status}`)}
                      </Badge>
                      <Text fontSize="10px" color={muted}>mAP50-95 {fmt(metric(run, "mAP50-95"))}</Text>
                    </HStack>
                  </Box>
                  <Tooltip label={t("Set as baseline (deltas reference)")} openDelay={400}>
                    <IconButton
                      aria-label={t("Set as baseline (deltas reference)")}
                      size="xs"
                      variant="ghost"
                      isDisabled={!selected}
                      color={isBaseline ? "yellow.400" : muted}
                      icon={<Icon as={isBaseline ? MdStar : MdStarOutline} />}
                      onClick={() => onSetBaseline(rid)}
                    />
                  </Tooltip>
                  {onDeleteRun && (
                    <Tooltip label={t("Delete run permanently")} openDelay={400}>
                      <IconButton
                        aria-label={t("Delete run permanently")}
                        size="xs"
                        variant="ghost"
                        color={muted}
                        _hover={{ color: "red.400" }}
                        icon={<Icon as={MdDeleteOutline} />}
                        onClick={() => onDeleteRun(rid)}
                      />
                    </Tooltip>
                  )}
                </HStack>
              );
            })}
          </Box>
        ))}
      </VStack>
    </Box>
  );
}
