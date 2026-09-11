import { useEffect, useRef, useState } from "react";
import {
  VStack,
  HStack,
  Text,
  Slider,
  SliderTrack,
  SliderFilledTrack,
  SliderThumb,
  NumberInput,
  NumberInputField,
  NumberInputStepper,
  NumberIncrementStepper,
  NumberDecrementStepper,
  Button,
  Badge,
  SimpleGrid,
  Alert,
  AlertIcon,
  Checkbox,
  useColorModeValue,
} from "@chakra-ui/react";
import Card from "../common/Card";
import type { TileProgress, TilingConfig, TilingPreview } from "../../types";
import { useTranslation } from "react-i18next";

interface Props {
  config: TilingConfig;
  preview: TilingPreview | null;
  onConfigChange: (config: TilingConfig) => void;
  onExecute: () => void;
  isExecuting?: boolean;
  progress?: { done: number; total: number } | null;
  reviewProgress?: TileProgress;
}

export default function TilingPanel({
  config,
  preview,
  onConfigChange,
  onExecute,
  isExecuting,
  progress,
  reviewProgress,
}: Props) {
  const { t } = useTranslation();
  const textColor = useColorModeValue("navy.700", "white");
  const labelColor = useColorModeValue("secondaryGray.600", "secondaryGray.600");
  const statBg = useColorModeValue("gray.50", "whiteAlpha.100");
  const reviewedPct = reviewProgress && reviewProgress.total > 0
    ? Math.round((reviewProgress.reviewed / reviewProgress.total) * 100)
    : 0;

  // Zmiana rozmiaru/nakładania przebudowuje katalog kafli → nowe tile_id → wyzerowanie stanu
  // przeglądu. Jeśli jest co stracić (sprawdzone/wykluczone > 0), wymagamy świadomej akceptacji.
  const [acknowledged, setAcknowledged] = useState(false);
  const baselineRef = useRef({ tile_size: config.tile_size, buffer: config.buffer });
  const wasExecuting = useRef(false);
  useEffect(() => {
    // Po zakończonej przebudowie bieżąca konfiguracja staje się nową bazą odniesienia.
    if (wasExecuting.current && !isExecuting) {
      baselineRef.current = { tile_size: config.tile_size, buffer: config.buffer };
      setAcknowledged(false);
    }
    wasExecuting.current = !!isExecuting;
  }, [isExecuting, config.tile_size, config.buffer]);

  const paramsChanged =
    config.tile_size !== baselineRef.current.tile_size ||
    config.buffer !== baselineRef.current.buffer;
  const hasReviewProgress =
    !!reviewProgress && (reviewProgress.reviewed > 0 || reviewProgress.excluded > 0);
  const needsConfirm = paramsChanged && hasReviewProgress;
  useEffect(() => {
    if (!needsConfirm && acknowledged) setAcknowledged(false);
  }, [needsConfirm, acknowledged]);

  return (
    <Card>
      <VStack align="stretch" spacing={4}>
        <HStack justify="space-between" align="start">
          <VStack align="start" spacing={1}>
            <Text fontWeight="bold" color={textColor}>
              {t("Review grid configuration")}
            </Text>
            <Text fontSize="xs" color={labelColor}>
              {t("Review grid stores only geometry and review status. Dataset tiles are generated later in Dataset.")}
            </Text>
          </VStack>
          <Badge colorScheme="purple">{t("metadata only")}</Badge>
        </HStack>

        <VStack align="stretch" spacing={1}>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Grid cell size")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>{config.tile_size}px</Text>
          </HStack>
          <Slider
            min={128}
            max={1024}
            step={64}
            value={config.tile_size}
            onChange={(v) => onConfigChange({ ...config, tile_size: v })}
          >
            <SliderTrack><SliderFilledTrack /></SliderTrack>
            <SliderThumb />
          </Slider>
        </VStack>

        <VStack align="stretch" spacing={1}>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Grid overlap")}</Text>
            {/* Strzałki do precyzyjnego wyboru; suwak niżej do zgrubnego przeciągania. */}
            <NumberInput
              size="xs"
              maxW="90px"
              min={0}
              max={Math.floor(config.tile_size / 2)}
              step={8}
              value={config.buffer}
              onChange={(_, v) =>
                onConfigChange({
                  ...config,
                  buffer: Number.isNaN(v)
                    ? 0
                    : Math.max(0, Math.min(Math.floor(config.tile_size / 2), v)),
                })
              }
            >
              <NumberInputField fontWeight="bold" pr="1.2rem" />
              <NumberInputStepper>
                <NumberIncrementStepper />
                <NumberDecrementStepper />
              </NumberInputStepper>
            </NumberInput>
          </HStack>
          <Slider
            min={0}
            max={config.tile_size / 2}
            step={8}
            value={config.buffer}
            onChange={(v) => onConfigChange({ ...config, buffer: v })}
          >
            <SliderTrack><SliderFilledTrack /></SliderTrack>
            <SliderThumb />
          </Slider>
        </VStack>

        {preview && (
          <VStack align="stretch" spacing={0.5}>
            <Text fontSize="xs" color={labelColor}>
              {t("Grid")}: {preview.num_cols} x {preview.num_rows} = {preview.total_tiles} {t("cells")}
            </Text>
            <Text fontSize="xs" color={labelColor}>
              {t("Stride")}: {preview.stride}px
            </Text>
          </VStack>
        )}

        {reviewProgress && reviewProgress.total_all > 0 && (
          <SimpleGrid columns={2} spacing={2}>
            <GridStat label={t("Cells")} value={reviewProgress.total_all} bg={statBg} />
            <GridStat label={t("Reviewed")} value={`${reviewProgress.reviewed}/${reviewProgress.total}`} bg={statBg} />
            <GridStat label={t("Progress")} value={`${reviewedPct}%`} bg={statBg} />
            <GridStat label={t("Excluded")} value={reviewProgress.excluded} bg={statBg} />
          </SimpleGrid>
        )}

        {needsConfirm && (
          <Alert status="warning" borderRadius="md" fontSize="xs" alignItems="flex-start">
            <AlertIcon />
            <VStack align="stretch" spacing={2} flex="1">
              <Text>
                {t("Changing the tile size or overlap rebuilds the grid and resets review progress ({{reviewed}} reviewed, {{excluded}} excluded).", {
                  reviewed: reviewProgress?.reviewed ?? 0,
                  excluded: reviewProgress?.excluded ?? 0,
                })}
              </Text>
              <Checkbox size="sm" isChecked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)}>
                {t("I understand and want to rebuild the grid")}
              </Checkbox>
            </VStack>
          </Alert>
        )}

        <Button
          colorScheme="brand"
          onClick={onExecute}
          isDisabled={needsConfirm && !acknowledged}
          isLoading={isExecuting}
          loadingText={
            progress
              ? t("catalogProgress", { done: progress.done, total: progress.total })
              : t("Building catalog...")
          }
        >
          {t("Create review grid")}
        </Button>
        <Text fontSize="xs" color={labelColor}>
          {t("This does not create training image tiles. It only creates a review grid for progress tracking.")}
        </Text>
      </VStack>
    </Card>
  );
}

function GridStat({ label, value, bg }: { label: string; value: string | number; bg: string }) {
  return (
    <VStack align="start" spacing={0} borderRadius="md" bg={bg} p={2}>
      <Text fontSize="xs" color="secondaryGray.600">{label}</Text>
      <Text fontSize="sm" fontWeight="bold">{value}</Text>
    </VStack>
  );
}
