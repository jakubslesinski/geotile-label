import { useCallback, useRef, useState } from "react";
import {
  Alert,
  AlertIcon,
  Box,
  Button,
  Collapse,
  Divider,
  HStack,
  Spinner,
  Text,
  VStack,
  useColorModeValue,
  useToast,
} from "@chakra-ui/react";
import { MdExpandLess, MdExpandMore, MdFolderOpen, MdRefresh } from "react-icons/md";
import { useTranslation } from "react-i18next";
import Card from "../common/Card";
import InfoPopover from "../common/InfoPopover";
import * as api from "../../api/client";
import {
  canOpenWorkingFolder,
  categoryRows,
  formatBytes,
  formatScanTime,
  initialPanelState,
  reducePanel,
  type PanelEvent,
} from "../../utils/sceneWorkingFiles";

interface Props {
  projectId: string;
  isDesktop: boolean;
}

/**
 * Rozmiar plikow roboczych scen (`derived_scenes`) — panel wylacznie odczytowy.
 *
 * Zastapil „Przechowywanie katalogu kafelkow”, ktory mierzyl PNG-i w
 * `tile_catalogs/*​/preview_cache` i po odejsciu konsumenta `tilePreviewUrl()` stale
 * pokazywal `0 B`, oferujac przy tym dwa przyciski kasujace. Tutaj nie ma zadnej akcji
 * niszczacej: uzytkownik widzi, co zajmuje miejsce, i moze otworzyc folder, a decyzje o
 * usunieciu podejmuje swiadomie poza aplikacja.
 *
 * Dane laduja sie dopiero po pierwszym rozwinieciu. Skan `derived_scenes` konkurowalby
 * o I/O z budowa COG i piramid, a dashboard ma sie otwierac natychmiast — dlatego panel
 * nie ma tez pollingu. Nieaktualnosc wyniku sygnalizuje godzina odczytu, a odswieza go
 * jawny przycisk.
 */
export default function SceneWorkingFilesPanel({ projectId, isDesktop }: Props) {
  const { t } = useTranslation();
  const toast = useToast();
  const textColor = useColorModeValue("navy.700", "white");
  const rowBorder = useColorModeValue("secondaryGray.200", "whiteAlpha.200");
  const [state, setState] = useState(initialPanelState);
  // Straznik przed podwojnym zadaniem, gdy uzytkownik szybko klika „Odswiez”: stan
  // Reacta aktualizuje sie asynchronicznie, a `loading` w reduktorze wida dopiero po
  // przerysowaniu.
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const summary = await api.getSceneWorkingStorage(projectId);
      setState((current) => reducePanel(current, { type: "loaded", summary }).state);
    } catch (err: any) {
      const message =
        err?.response?.data?.detail || err?.message || String(err ?? t("Unknown error"));
      setState((current) => reducePanel(current, { type: "failed", error: message }).state);
    } finally {
      inFlight.current = false;
    }
  }, [projectId, t]);

  const dispatch = useCallback(
    (event: PanelEvent) => {
      let shouldFetch = false;
      setState((current) => {
        const transition = reducePanel(current, event);
        shouldFetch = transition.fetch;
        return transition.state;
      });
      if (shouldFetch) void load();
    },
    [load],
  );

  const { expanded, loading, summary, error } = state;
  const rows = summary ? categoryRows(summary) : [];
  const canOpen = canOpenWorkingFolder(summary, isDesktop);
  const isEmpty = Boolean(summary && !summary.directory_exists);

  const handleOpenFolder = async () => {
    if (!summary) return;
    try {
      // Sciezka pochodzi WYLACZNIE z odpowiedzi backendu. Skladanie jej w TypeScripcie
      // z nazwy projektu otworzyloby przy pierwszej rozbieznosci cudzy katalog.
      await api.openPathInFileManager(summary.working_files_dir);
    } catch (err: any) {
      toast({
        title: t("Cannot open the working files folder"),
        description: err?.message || String(err),
        status: "error",
      });
    }
  };

  return (
    <Card>
      <VStack align="stretch" spacing={expanded ? 3 : 0}>
        {/* Naglowek jest JEDNYM przyciskiem obejmujacym tytul, opis i strzalke — stad
            poprawne `aria-expanded` i obsluga klawiatury bez wlasnych handlerow. Popover
            i suma stoja OBOK niego, a nie w srodku: przycisk w przycisku jest nieprawidlowym
            HTML-em i w czytniku ekranu daje dwa nakladajace sie sterowania. */}
        <HStack justify="space-between" align="center" gap={2}>
          <HStack minW={0} spacing={1}>
            <Box
              as="button"
              type="button"
              aria-expanded={expanded}
              onClick={() => dispatch({ type: "toggle" })}
              display="flex"
              alignItems="center"
              gap={2}
              textAlign="left"
              minW={0}
            >
              <VStack align="start" spacing={0} minW={0}>
                <Text fontWeight="bold" color={textColor}>{t("Scene working files")}</Text>
                <Text fontSize="xs" color="secondaryGray.600">
                  {t("Overviews, VRTs and COGs stored in the project folder.")}
                </Text>
              </VStack>
              <Box as="span" color="secondaryGray.600" flexShrink={0} fontSize="xl" display="flex">
                {expanded ? <MdExpandLess /> : <MdExpandMore />}
              </Box>
            </Box>
            <InfoPopover
              titleKey="info.project.sceneWorkingFiles.title"
              bodyKey="info.project.sceneWorkingFiles.body"
            />
          </HStack>
          <HStack spacing={2} flexShrink={0}>
            {loading && <Spinner size="sm" />}
            <Text fontWeight="bold" color={textColor}>
              {/* Przed pierwszym odczytem „—”, nie „0 B”: zero jest zdaniem o dysku,
                  ktorego panel jeszcze nie wypowiedzial. */}
              {summary ? formatBytes(summary.total.bytes) : "-"}
            </Text>
          </HStack>
        </HStack>

        <Collapse in={expanded} animateOpacity>
          <VStack align="stretch" spacing={3} pt={1}>
            {error && (
              <Alert status="error" borderRadius="md" fontSize="sm">
                <AlertIcon />
                <VStack align="start" spacing={2} flex="1" minW={0}>
                  <Text>{error}</Text>
                  <Button size="xs" onClick={() => dispatch({ type: "refresh" })}>
                    {t("Try again")}
                  </Button>
                </VStack>
              </Alert>
            )}

            {!error && !summary && loading && (
              <Text fontSize="sm" color="secondaryGray.600">{t("Reading folder size")}…</Text>
            )}

            {!error && isEmpty && (
              <Text fontSize="sm" color="secondaryGray.600">
                {t("No scene working files yet")}
              </Text>
            )}

            {!error && summary && !isEmpty && (
              <>
                <VStack align="stretch" spacing={0}>
                  {rows.map((row) => (
                    <HStack
                      key={row.category}
                      justify="space-between"
                      gap={3}
                      py={1.5}
                      borderBottomWidth="1px"
                      borderColor={rowBorder}
                    >
                      <Text fontSize="sm" color={textColor} minW={0} wordBreak="break-word">
                        {t(row.labelKey)}
                      </Text>
                      <HStack spacing={3} flexShrink={0}>
                        <Text fontSize="sm" color={textColor} minW="8ch" textAlign="right">
                          {formatBytes(row.bytes)}
                        </Text>
                        <Text fontSize="xs" color="secondaryGray.600" minW="9ch" textAlign="right">
                          {t("fileCount", { count: row.fileCount })}
                        </Text>
                        {/* Liczba scen tylko tam, gdzie cos znaczy: przy manifestach i
                            pustych plikach przejsciowych bylaby szumem. */}
                        <Text fontSize="xs" color="secondaryGray.600" minW="9ch" textAlign="right">
                          {row.sceneCount > 0 ? t("sceneCount", { count: row.sceneCount }) : ""}
                        </Text>
                      </HStack>
                    </HStack>
                  ))}
                </VStack>

                {summary.largest_scenes.length > 0 && (
                  <VStack align="stretch" spacing={1}>
                    <Text fontSize="xs" fontWeight="bold" color="secondaryGray.600">
                      {t("Largest scenes")}
                    </Text>
                    {summary.largest_scenes.map((scene) => (
                      <HStack key={scene.scene_id} justify="space-between" gap={3}>
                        <Text fontSize="sm" color={textColor} minW={0} noOfLines={1}>
                          {scene.display_name || scene.scene_id}
                        </Text>
                        <Text fontSize="sm" color={textColor} flexShrink={0}>
                          {formatBytes(scene.bytes)}
                        </Text>
                      </HStack>
                    ))}
                  </VStack>
                )}

                {summary.scan_errors > 0 && (
                  // Informacja, nie wezwanie do dzialania: panel nie naprawia i nie kasuje.
                  <Alert status="info" borderRadius="md" fontSize="sm">
                    <AlertIcon />
                    {t("scanErrorsNotice", { count: summary.scan_errors })}
                  </Alert>
                )}
              </>
            )}

            <Divider />
            <HStack justify="space-between" gap={3} flexWrap="wrap">
              <Text fontSize="xs" color="secondaryGray.600">
                {summary
                  ? `${t("Scanned at")}: ${formatScanTime(summary.generated_at)} · ${summary.scan_duration_ms} ms`
                  : ""}
              </Text>
              <HStack spacing={2} flexWrap="wrap">
                <Button
                  size="sm"
                  variant="outline"
                  leftIcon={<MdRefresh />}
                  isLoading={loading}
                  onClick={() => dispatch({ type: "refresh" })}
                >
                  {t("Refresh storage information")}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  leftIcon={<MdFolderOpen />}
                  isDisabled={!canOpen}
                  title={isDesktop ? undefined : t("Available in the desktop application only")}
                  onClick={handleOpenFolder}
                >
                  {t("Open working files folder")}
                </Button>
              </HStack>
            </HStack>
          </VStack>
        </Collapse>
      </VStack>
    </Card>
  );
}
