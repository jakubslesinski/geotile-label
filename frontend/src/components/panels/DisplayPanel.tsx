import { useState, useEffect, useRef } from "react";
import {
  VStack,
  HStack,
  Wrap,
  WrapItem,
  Text,
  Slider,
  SliderTrack,
  SliderFilledTrack,
  SliderThumb,
  Button,
  ButtonGroup,
  Collapse,
  IconButton,
  Tooltip,
  FormControl,
  FormLabel,
  Select,
  useColorModeValue,
  useDisclosure,
} from "@chakra-ui/react";
import {
  MdChevronLeft,
  MdChevronRight,
  MdExpandMore,
  MdExpandLess,
  MdLock,
  MdLockOpen,
} from "react-icons/md";
import Card from "../common/Card";
import InfoPopover from "../common/InfoPopover";
import HistogramStretch, { sigmaStretch } from "./HistogramStretch";
import type { HistogramView } from "./HistogramStretch";
import type { ViewStretchStatus } from "../../hooks/useViewStretch";
import type {
  DisplayParams,
  PreprocessingProfile,
  ProjectModality,
  SceneHistogram,
  StretchScope,
} from "../../types";
import { DEFAULT_DISPLAY_PARAMS, SAR_DISPLAY_PARAMS } from "../../types";
import { useTranslation } from "react-i18next";

interface Props {
  params: DisplayParams;
  onChange: (params: DisplayParams) => void;
  profiles?: PreprocessingProfile[];
  selectedProfileId?: string | null;
  onProfileSelect?: (profileId: string) => void;
  onCopyToDataset?: (params: DisplayParams, profileId?: string | null) => void | Promise<void>;
  projectModality?: ProjectModality;
  sceneModality?: string | null;
  histogram?: SceneHistogram | null;
  compact?: boolean;
  /** Zakres statystyk rozciągnięcia (DESIGN_DECISIONS.md, display-stretch D). Bez handlera — ukryty. */
  stretchScope?: StretchScope;
  onStretchScopeChange?: (scope: StretchScope) => void;
  viewStretchAvailable?: boolean;
  viewFrozen?: boolean;
  onViewFrozenChange?: (frozen: boolean) => void;
  viewStatus?: ViewStretchStatus;
  /** Histogram bieżącego widoku; w zakresie „Widok” zastępuje histogram sceny. */
  viewHistogram?: SceneHistogram | null;
}

export default function DisplayPanel({
  params,
  onChange,
  profiles = [],
  selectedProfileId,
  onProfileSelect,
  onCopyToDataset,
  projectModality,
  sceneModality,
  histogram: sceneHistogram = null,
  compact = false,
  stretchScope = "scene",
  onStretchScopeChange,
  viewStretchAvailable = false,
  viewFrozen = false,
  onViewFrozenChange,
  viewStatus = "idle",
  viewHistogram = null,
}: Props) {
  const viewScope = stretchScope === "view" && viewStretchAvailable;
  // Uchwyty, etykieta „DN a–b” i presety μ±kσ liczą z aktywnego zakresu. Dopóki statystyki
  // widoku nie przyszły, zostaje histogram sceny — tak samo robią kafle.
  const histogram = viewScope && viewHistogram ? viewHistogram : sceneHistogram;
  const { t } = useTranslation();
  const textColor = useColorModeValue("navy.700", "white");
  const labelColor = useColorModeValue("secondaryGray.600", "whiteAlpha.700");

  // Local state for smooth slider dragging — commit to parent only on release
  const [local, setLocal] = useState<DisplayParams>(params);
  const [logScale, setLogScale] = useState(false);
  // Widok histogramu jest ustawieniem PODGLĄDU, nie sceny: nie wchodzi do parametrów
  // wyświetlania ani do klucza kafla, bo nie zmienia renderowanego obrazu.
  const [histogramView, setHistogramView] = useState<HistogramView>("bands");
  // Przelacznik ma sens tylko tam, gdzie sa co najmniej trzy pasma — dla PAN i SAR
  // obie opcje daja te sama krzywa, wiec go nie pokazujemy.
  const multiBandHistogram = (histogram?.band_count ?? 0) >= 3 && !!histogram?.bands?.length;
  const { isOpen: advancedOpen, onToggle: toggleAdvanced } = useDisclosure({ defaultIsOpen: true });
  const pendingCommitRef = useRef<number | null>(null);

  // Sync local state when parent changes (e.g., Reset button)
  useEffect(() => {
    setLocal(params);
  }, [params]);

  useEffect(() => () => {
    if (pendingCommitRef.current !== null) {
      window.clearTimeout(pendingCommitRef.current);
    }
  }, []);

  const commit = (p: DisplayParams) => {
    if (pendingCommitRef.current !== null) {
      window.clearTimeout(pendingCommitRef.current);
      pendingCommitRef.current = null;
    }
    setLocal(p);
    onChange(p);
  };

  const preview = (p: DisplayParams) => {
    setLocal(p);
    if (pendingCommitRef.current !== null) {
      window.clearTimeout(pendingCommitRef.current);
    }
    pendingCommitRef.current = window.setTimeout(() => {
      pendingCommitRef.current = null;
      onChange(p);
    }, 180);
  };

  const stepParameter = (
    key: keyof DisplayParams,
    min: number,
    max: number,
    step: number,
    direction: -1 | 1,
  ) => {
    const nextValue = Math.min(max, Math.max(min, local[key] + direction * step));
    commit({ ...local, [key]: Number(nextValue.toFixed(2)) });
  };

  const renderSlider = (
    key: keyof DisplayParams,
    labelKey: string,
    min: number,
    max: number,
    step: number,
    format: (value: number) => string,
    infoKeys?: { titleKey: string; bodyKey: string },
  ) => (
    <VStack align="stretch" spacing={1}>
      <HStack justify="space-between">
        <HStack spacing={1}>
          <Text fontSize="sm" color={labelColor}>{t(labelKey)}</Text>
          {infoKeys && <InfoPopover titleKey={infoKeys.titleKey} bodyKey={infoKeys.bodyKey} />}
        </HStack>
        <Text fontSize="sm" fontWeight="bold" color={textColor}>{format(local[key])}</Text>
      </HStack>
      <HStack spacing={1}>
        <Tooltip label={t("Decrease parameter", { parameter: t(labelKey) })} hasArrow>
          <IconButton
            aria-label={t("Decrease parameter", { parameter: t(labelKey) })}
            icon={<MdChevronLeft />}
            size="xs"
            variant="outline"
            isDisabled={local[key] <= min}
            onClick={() => stepParameter(key, min, max, step, -1)}
          />
        </Tooltip>
        <Slider
          flex="1"
          min={min}
          max={max}
          step={step}
          value={local[key]}
          onChange={(value) => preview({ ...local, [key]: value })}
          onChangeEnd={(value) => commit({ ...local, [key]: value })}
        >
          <SliderTrack><SliderFilledTrack /></SliderTrack>
          <SliderThumb />
        </Slider>
        <Tooltip label={t("Increase parameter", { parameter: t(labelKey) })} hasArrow>
          <IconButton
            aria-label={t("Increase parameter", { parameter: t(labelKey) })}
            icon={<MdChevronRight />}
            size="xs"
            variant="outline"
            isDisabled={local[key] >= max}
            onClick={() => stepParameter(key, min, max, step, 1)}
          />
        </Tooltip>
      </HStack>
    </VStack>
  );

  const applyProfile = (profileId: string) => {
    const profile = profiles.find((item) => item.profile_id === profileId);
    if (!profile) return;
    onProfileSelect?.(profileId);
    commit({
      brightness: profile.brightness,
      contrast: profile.contrast,
      gamma: profile.gamma,
      stretch_low: profile.stretch_low,
      stretch_high: profile.stretch_high,
    });
  };

  const selectedProfile = profiles.find((item) => item.profile_id === selectedProfileId);
  const expectedModality = sceneModality || projectModality;
  const isSar = String(expectedModality || "").toUpperCase() === "SAR";
  const profileMismatch = !!(
    selectedProfile && expectedModality && selectedProfile.modality !== expectedModality
  );

  const isDefault =
    local.brightness === 1.0 &&
    local.contrast === 1.0 &&
    local.gamma === 1.0 &&
    local.stretch_low === 0 &&
    local.stretch_high === 100;

  const content = (
      <VStack align="stretch" spacing={compact ? 3 : 4}>
        {(!compact || !isDefault) && (
          <HStack justify={compact ? "flex-end" : "space-between"}>
            {!compact && (
              <HStack spacing={1}>
                <Text fontWeight="bold" color={textColor}>{t("Display Settings")}</Text>
                <InfoPopover titleKey="info.display.profile.title" bodyKey="info.display.profile.body" />
              </HStack>
            )}
            {!isDefault && (
            <Button
              size="xs"
              variant="outline"
              onClick={() => commit({ ...DEFAULT_DISPLAY_PARAMS })}
            >
              {t("Reset")}
            </Button>
            )}
          </HStack>
        )}

        {profiles.length > 0 && (
          <FormControl>
            <FormLabel fontSize="sm" color={labelColor}>
              <HStack spacing={1}>
                <Text>{t("Display preset")}</Text>
                <InfoPopover titleKey="info.display.profile.title" bodyKey="info.display.profile.body" />
              </HStack>
            </FormLabel>
            <Select
              size="sm"
              value={selectedProfileId || ""}
              onChange={(event) => applyProfile(event.target.value)}
            >
              {profiles.map((profile) => (
                <option key={profile.profile_id} value={profile.profile_id}>
                  {profile.name} ({profile.modality})
                </option>
              ))}
            </Select>
            {selectedProfile && (
              <Text mt={1} fontSize="xs" color={profileMismatch ? "orange.400" : labelColor}>
                v{selectedProfile.profile_version} · {selectedProfile.radiometric_transform} · {selectedProfile.profile_hash.slice(0, 12)}
                {profileMismatch ? ` · ${t("Warning: expected modality", { modality: expectedModality })}` : ""}
              </Text>
            )}
          </FormControl>
        )}

        {/* Tonal stretch — dual-handle range on the value histogram */}
        <VStack align="stretch" spacing={2}>
          <HStack justify="space-between">
            <HStack spacing={1}>
              <Text fontSize="sm" color={labelColor}>{t("Stretch")}</Text>
              <InfoPopover titleKey="info.display.stretch.title" bodyKey="info.display.stretch.body" />
            </HStack>
            <HStack spacing={1}>
              {multiBandHistogram && (
                <ButtonGroup size="xs" isAttached variant="outline">
                  <Button
                    onClick={() => setHistogramView("bands")}
                    isActive={histogramView === "bands"}
                    title={t("Per-band histogram")}
                  >
                    {t("bands")}
                  </Button>
                  <Button
                    onClick={() => setHistogramView("luminance")}
                    isActive={histogramView === "luminance"}
                    title={t("Perceived brightness histogram")}
                  >
                    {t("luminance")}
                  </Button>
                </ButtonGroup>
              )}
              <ButtonGroup size="xs" isAttached variant="outline">
                <Button onClick={() => setLogScale(true)} isActive={logScale}>{t("log")}</Button>
                <Button onClick={() => setLogScale(false)} isActive={!logScale}>{t("linear")}</Button>
              </ButtonGroup>
            </HStack>
          </HStack>
          {viewStretchAvailable && onStretchScopeChange && (
            <HStack justify="space-between">
              <HStack spacing={1}>
                <Text fontSize="xs" color={labelColor}>{t("Statistics")}</Text>
                <InfoPopover titleKey="info.display.scope.title" bodyKey="info.display.scope.body" />
              </HStack>
              <HStack spacing={1}>
                <ButtonGroup size="xs" isAttached variant="outline">
                  <Button
                    onClick={() => onStretchScopeChange("scene")}
                    isActive={stretchScope === "scene"}
                    title={t("Thresholds from the whole scene")}
                  >
                    {t("Scene")}
                  </Button>
                  <Button
                    onClick={() => onStretchScopeChange("view")}
                    isActive={stretchScope === "view"}
                    title={t("Thresholds from the current map view")}
                  >
                    {t("View")}
                  </Button>
                </ButtonGroup>
                {viewScope && onViewFrozenChange && (
                  <Tooltip label={viewFrozen ? t("Unfreeze thresholds") : t("Freeze thresholds")} hasArrow>
                    <IconButton
                      aria-label={viewFrozen ? t("Unfreeze thresholds") : t("Freeze thresholds")}
                      icon={viewFrozen ? <MdLock /> : <MdLockOpen />}
                      size="xs"
                      variant={viewFrozen ? "solid" : "outline"}
                      onClick={() => onViewFrozenChange(!viewFrozen)}
                    />
                  </Tooltip>
                )}
              </HStack>
            </HStack>
          )}
          {viewScope && (
            <Text fontSize="xs" color={viewStatus === "error" || viewStatus === "insufficient" ? "orange.400" : labelColor}>
              {viewStatus === "loading"
                ? t("Computing view statistics…")
                : viewStatus === "insufficient"
                  ? t("Too little data in view - keeping previous thresholds")
                  : viewStatus === "error"
                    ? t("View statistics unavailable - using scene thresholds")
                    : viewFrozen
                      ? t("Histogram: current view (frozen)")
                      : t("Histogram: current view")}
            </Text>
          )}
          <HistogramStretch
            histogram={histogram}
            low={local.stretch_low}
            high={local.stretch_high}
            logScale={logScale}
            view={multiBandHistogram ? histogramView : "pooled"}
            onChange={(low, high) => preview({ ...local, stretch_low: low, stretch_high: high })}
            onChangeEnd={(low, high) => commit({ ...local, stretch_low: low, stretch_high: high })}
          />
          <Wrap spacing={1} justify="center">
            {[
              { label: "2–98%", low: 2, high: 98 },
              { label: "1–99%", low: 1, high: 99 },
              ...(isSar
                ? [{
                    label: t("SAR 1–99.8%"),
                    low: SAR_DISPLAY_PARAMS.stretch_low,
                    high: SAR_DISPLAY_PARAMS.stretch_high,
                  }]
                : []),
              { label: t("Full range"), low: 0, high: 100 },
            ].map((preset) => (
              <WrapItem key={preset.label}>
                <Button
                  size="xs"
                  variant="outline"
                  onClick={() => commit({ ...local, stretch_low: preset.low, stretch_high: preset.high })}
                >
                  {preset.label}
                </Button>
              </WrapItem>
            ))}
            {[2, 3].map((k) => (
              <WrapItem key={`sigma-${k}`}>
                <Button
                  size="xs"
                  variant="outline"
                  isDisabled={!histogram || histogram.sample_count === 0}
                  onClick={() => {
                    if (!histogram) return;
                    const { low, high } = sigmaStretch(histogram, k);
                    commit({ ...local, stretch_low: low, stretch_high: high });
                  }}
                >
                  μ±{k}σ
                </Button>
              </WrapItem>
            ))}
          </Wrap>
        </VStack>

        {/* Advanced tonal controls — collapsed by default to keep the panel short */}
        <VStack align="stretch" spacing={advancedOpen ? 3 : 0}>
          <Button
            size="xs"
            variant="ghost"
            alignSelf="flex-start"
            leftIcon={advancedOpen ? <MdExpandLess /> : <MdExpandMore />}
            onClick={toggleAdvanced}
          >
            {t("Advanced")}
          </Button>
          <Collapse in={advancedOpen} animateOpacity>
            <VStack align="stretch" spacing={4}>
              {renderSlider("brightness", "Brightness", 0.1, 3, 0.05, (value) => value.toFixed(2))}
              {renderSlider("contrast", "Contrast", 0.1, 3, 0.05, (value) => value.toFixed(2))}
              {renderSlider(
                "gamma",
                "Gamma",
                0.1,
                5,
                0.05,
                (value) => value.toFixed(2),
                { titleKey: "info.display.gamma.title", bodyKey: "info.display.gamma.body" },
              )}
            </VStack>
          </Collapse>
        </VStack>

        {onCopyToDataset && (
          <HStack spacing={1}>
            <Button
              size="sm"
              variant="outline"
              colorScheme="brand"
              flex="1"
              onClick={() => onCopyToDataset(local, selectedProfileId)}
            >
              {t("Copy display settings to dataset profile")}
            </Button>
            <InfoPopover titleKey="info.display.copyToDataset.title" bodyKey="info.display.copyToDataset.body" />
          </HStack>
        )}

        <Text fontSize="xs" color={labelColor}>
          {t("Display presets copy tonal controls to the map. Their radiometric transform is applied only during dataset generation from source scene pixels.")}
        </Text>
      </VStack>
  );

  return compact ? content : <Card>{content}</Card>;
}
