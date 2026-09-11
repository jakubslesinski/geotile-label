import { useEffect, useRef, useState } from "react";
import {
  Box,
  Button,
  HStack,
  IconButton,
  Input,
  Text,
  Tooltip,
  VStack,
  useColorModeValue,
} from "@chakra-ui/react";
import { MdAutoAwesome, MdAutoFixHigh, MdGridView, MdTextFields } from "react-icons/md";
import { useTranslation } from "react-i18next";
import type { MapTool } from "../../types";
import type { ExemplarEngine, ExemplarSearchMode, SamModelInfo } from "../../api/client";

interface Props {
  /** Only shown in geo mode (scenes rendered on a map). */
  visible: boolean;
  activeTool: MapTool;
  onToolChange: (tool: MapTool) => void;
  /** Exemplar propagation is available for the current scene. */
  exemplarSupported: boolean;
  /** The exemplar backend is ready. */
  exemplarReady: boolean;
  /** A SAM checkpoint is configured (or mock backend is on). */
  samReady: boolean;
  /** An active class is selected (proposals need a class). */
  hasActiveClass: boolean;
  /** An annotation is selected to use as an exemplar. */
  hasSelection: boolean;
  /** Exemplar request in flight. */
  findSimilarBusy: boolean;
  onFindSimilar: () => void;
  exemplarSearchMode: ExemplarSearchMode;
  onExemplarSearchModeChange: (mode: ExemplarSearchMode) => void;
  /** Matching engine: template NCC vs few-shot DINO embeddings. */
  exemplarEngine?: ExemplarEngine;
  onExemplarEngineChange?: (engine: ExemplarEngine) => void;
  /** The selected checkpoint is SAM3 (or mock) — text mode needs it. */
  sam3Ready?: boolean;
  /** SAM3 text request in flight. */
  samTextBusy?: boolean;
  /** Called when the SAM3-text popover opens (e.g. to prefill the prompt). */
  onSamText?: () => void;
  /** SAM3-text prompt (open-vocabulary phrase) and confidence, edited in the popover. */
  samTextPrompt?: string;
  onSamTextPromptChange?: (value: string) => void;
  samTextConf?: number;
  onSamTextConfChange?: (value: number) => void;
  /** Run SAM3 text over the current view, or draw a search region first. */
  onSamTextRun?: () => void;
  onSamTextDrawRegion?: () => void;
  samModels?: SamModelInfo[];
  samModelsDirPath?: string | null;
  selectedSamCheckpoint?: string | null;
  onSamCheckpointChange?: (checkpoint: string | null) => void | Promise<void>;
}

export default function AiToolControl({
  visible,
  activeTool,
  onToolChange,
  exemplarSupported,
  exemplarReady,
  samReady,
  hasActiveClass,
  hasSelection,
  findSimilarBusy,
  onFindSimilar,
  exemplarSearchMode,
  onExemplarSearchModeChange,
  exemplarEngine = "template",
  onExemplarEngineChange,
  sam3Ready = false,
  samTextBusy = false,
  onSamText,
  samTextPrompt = "",
  onSamTextPromptChange,
  samTextConf = 0.25,
  onSamTextConfChange,
  onSamTextRun,
  onSamTextDrawRegion,
  samModels = [],
  samModelsDirPath,
  selectedSamCheckpoint,
  onSamCheckpointChange,
}: Props) {
  const { t } = useTranslation();
  const bg = useColorModeValue("white", "rgba(28, 28, 31, 0.94)");
  const borderColor = useColorModeValue("rgba(124, 79, 224, 0.30)", "rgba(155, 108, 255, 0.65)");
  const activeBg = useColorModeValue("brand.500", "brand.200");
  const inactiveColor = useColorModeValue("navy.700", "whiteAlpha.900");
  const accent = useColorModeValue("brand.500", "brand.300");
  const menuBg = useColorModeValue("white", "#242428");
  const menuHoverBg = useColorModeValue("purple.50", "rgba(155,108,255,0.15)");
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [searchMenuOpen, setSearchMenuOpen] = useState(false);
  const [textMenuOpen, setTextMenuOpen] = useState(false);
  const modelMenuRef = useRef<HTMLDivElement>(null);
  const searchMenuRef = useRef<HTMLDivElement>(null);
  const textMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!modelMenuOpen) return;
    const closeMenu = (event: MouseEvent) => {
      if (!modelMenuRef.current?.contains(event.target as Node)) setModelMenuOpen(false);
    };
    document.addEventListener("mousedown", closeMenu);
    return () => document.removeEventListener("mousedown", closeMenu);
  }, [modelMenuOpen]);

  useEffect(() => {
    if (!searchMenuOpen) return;
    const closeMenu = (event: MouseEvent) => {
      if (!searchMenuRef.current?.contains(event.target as Node)) setSearchMenuOpen(false);
    };
    document.addEventListener("mousedown", closeMenu);
    return () => document.removeEventListener("mousedown", closeMenu);
  }, [searchMenuOpen]);

  useEffect(() => {
    if (!textMenuOpen) return;
    const closeMenu = (event: MouseEvent) => {
      if (!textMenuRef.current?.contains(event.target as Node)) setTextMenuOpen(false);
    };
    document.addEventListener("mousedown", closeMenu);
    return () => document.removeEventListener("mousedown", closeMenu);
  }, [textMenuOpen]);

  if (!visible) return null;

  const disabledReason = !samReady
    ? t("Configure a SAM checkpoint in the Predict panel")
    : !hasActiveClass
      ? t("Select an active class first")
      : "";
  const samDisabled = !!disabledReason;
  const isActive = activeTool === "sam_click";
  const label = samDisabled
    ? `${t("SAM click-to-box")} - ${disabledReason}`
    : t("SAM click-to-box");

  return (
    <Box
      position="absolute"
      left="16px"
      top="356px"
      zIndex={1100}
      bg={bg}
      borderWidth="1px"
      borderColor={borderColor}
      borderRadius="12px"
      boxShadow="lg"
      backdropFilter="blur(10px)"
      p="4px"
      pointerEvents="auto"
    >
      <VStack spacing="4px">
        <HStack spacing="2px" px="2px" pt="1px" alignSelf="stretch" justify="center">
          <MdAutoAwesome size={12} color={accent} />
          <Text fontSize="9px" fontWeight="bold" color={accent} letterSpacing="0.5px">
            {t("AI")}
          </Text>
        </HStack>
        <Box
          ref={modelMenuRef}
          position="relative"
          onContextMenu={(event) => {
            event.preventDefault();
            setSearchMenuOpen(false);
            setModelMenuOpen((open) => !open);
          }}
        >
          <Tooltip label={`${label}. ${t("Right-click to select a SAM model.")}`} placement="right" hasArrow>
            <IconButton
              aria-label={label}
              icon={<MdAutoFixHigh size={18} />}
              size="sm"
              minW="34px"
              h="34px"
              borderRadius="9px"
              variant={isActive ? "solid" : "ghost"}
              bg={isActive ? activeBg : "transparent"}
              color={isActive ? "white" : inactiveColor}
              aria-disabled={samDisabled}
              opacity={samDisabled ? 0.45 : 1}
              cursor={samDisabled ? "not-allowed" : "pointer"}
              _hover={{ bg: isActive ? activeBg : "blackAlpha.100" }}
              onClick={() => {
                if (!samDisabled) onToolChange("sam_click");
              }}
            />
          </Tooltip>
          {modelMenuOpen && (
            <VStack
              position="absolute"
              left="42px"
              top="0"
              zIndex={1400}
              minW="270px"
              maxW="360px"
              maxH="320px"
              overflowY="auto"
              align="stretch"
              spacing={1}
              bg={menuBg}
              borderWidth="1px"
              borderColor={borderColor}
              borderRadius="lg"
              boxShadow="2xl"
              p={2}
            >
              <Text px={2} py={1} fontSize="xs" fontWeight="bold" color={inactiveColor}>
                {t("Select SAM model")}
              </Text>
              {samModelsDirPath && (
                <Text px={2} fontSize="10px" color="gray.500" noOfLines={1} title={samModelsDirPath}>
                  {samModelsDirPath}
                </Text>
              )}
              {samModels.length === 0 ? (
                <Text px={2} py={1} fontSize="xs" color="gray.500">{t("No supported SAM models found")}</Text>
              ) : samModels.map((model) => (
                <Button
                  key={model.path}
                  size="xs"
                  height="auto"
                  py={2}
                  justifyContent="flex-start"
                  whiteSpace="normal"
                  textAlign="left"
                  variant="ghost"
                  bg={selectedSamCheckpoint === model.path ? menuHoverBg : "transparent"}
                  onClick={async () => {
                    await onSamCheckpointChange?.(model.path);
                    setModelMenuOpen(false);
                  }}
                >
                  {model.name} {model.family ? `(${model.family.toUpperCase()})` : ""}
                </Button>
              ))}
            </VStack>
          )}
        </Box>
        <Box
          ref={searchMenuRef}
          position="relative"
          onContextMenu={(event) => {
            event.preventDefault();
            if (exemplarSupported) {
              setModelMenuOpen(false);
              setSearchMenuOpen((open) => !open);
            }
          }}
        >
          <Tooltip
            label={
              !exemplarSupported
                ? t("Exemplar propagation is not available for this scene")
                : !exemplarReady
                  ? t("Configure a SAR backbone in the Predict panel")
                  : !hasSelection
                    ? t("Select an annotation to find similar objects")
                    : `${t("Find similar to selection")}. ${t("Right-click to select the search range.")}`
            }
            placement="right"
            hasArrow
          >
            <IconButton
              aria-label={t("Find similar to selection")}
              icon={<MdGridView size={18} />}
              size="sm"
              minW="34px"
              h="34px"
              borderRadius="9px"
              variant="ghost"
              color={inactiveColor}
              isDisabled={!exemplarSupported || !exemplarReady || !hasSelection}
              isLoading={findSimilarBusy}
              _hover={{ bg: "blackAlpha.100" }}
              onClick={onFindSimilar}
            />
          </Tooltip>
          {searchMenuOpen && (
            <VStack
              position="absolute"
              left="42px"
              top="0"
              zIndex={1400}
              minW="260px"
              align="stretch"
              spacing={1}
              bg={menuBg}
              borderWidth="1px"
              borderColor={borderColor}
              borderRadius="lg"
              boxShadow="2xl"
              p={2}
            >
              {onExemplarEngineChange && (
                <>
                  <Text px={2} py={1} fontSize="xs" fontWeight="bold" color={inactiveColor}>
                    {t("Matching engine")}
                  </Text>
                  {([
                    ["template", "Template (NCC)"],
                    ["dino", "DINO (few-shot)"],
                  ] as [ExemplarEngine, string][]).map(([eng, text]) => (
                    <Button
                      key={eng}
                      size="xs"
                      height="auto"
                      py={2}
                      justifyContent="flex-start"
                      variant="ghost"
                      bg={exemplarEngine === eng ? menuHoverBg : "transparent"}
                      onClick={() => onExemplarEngineChange(eng)}
                    >
                      {t(text)}
                    </Button>
                  ))}
                </>
              )}
              <Text px={2} py={1} fontSize="xs" fontWeight="bold" color={inactiveColor}>
                {t("Template matching search range")}
              </Text>
              {([
                ["local", "Local area (2048 px)"],
                ["viewport", "Current map view"],
                ["scene", "Whole scene"],
              ] as [ExemplarSearchMode, string][]).map(([mode, text]) => {
                return (
                  <Button
                    key={mode}
                    size="xs"
                    height="auto"
                    py={2}
                    justifyContent="flex-start"
                    variant="ghost"
                    bg={exemplarSearchMode === mode ? menuHoverBg : "transparent"}
                    onClick={() => {
                      onExemplarSearchModeChange(mode);
                      setSearchMenuOpen(false);
                    }}
                  >
                    {t(text)}
                  </Button>
                );
              })}
            </VStack>
          )}
        </Box>
        {onSamTextRun && (() => {
          const textDisabled = !samReady || !sam3Ready || !hasActiveClass;
          const textReason = !samReady
            ? t("Configure a SAM checkpoint in the Predict panel")
            : !sam3Ready
              ? t("Text mode requires a SAM3 checkpoint")
              : !hasActiveClass
                ? t("Select an active class first")
                : "";
          const textLabel = textDisabled
            ? `${t("SAM3 text")} - ${textReason}`
            : t("SAM3 text");
          const canRun = !!samTextPrompt.trim();
          return (
            <Box ref={textMenuRef} position="relative">
              <Tooltip label={textLabel} placement="right" hasArrow>
                <IconButton
                  aria-label={textLabel}
                  icon={<MdTextFields size={18} />}
                  size="sm"
                  minW="34px"
                  h="34px"
                  borderRadius="9px"
                  variant={textMenuOpen ? "solid" : "ghost"}
                  bg={textMenuOpen ? activeBg : "transparent"}
                  color={textMenuOpen ? "white" : inactiveColor}
                  isDisabled={textDisabled}
                  isLoading={samTextBusy}
                  _hover={{ bg: textMenuOpen ? activeBg : "blackAlpha.100" }}
                  onClick={() => {
                    if (textDisabled) return;
                    if (!textMenuOpen) onSamText?.();  // prefill on open
                    setTextMenuOpen((open) => !open);
                  }}
                />
              </Tooltip>
              {textMenuOpen && (
                <VStack
                  position="absolute"
                  left="42px"
                  top="0"
                  zIndex={1400}
                  w="280px"
                  align="stretch"
                  spacing={2}
                  bg={menuBg}
                  borderWidth="1px"
                  borderColor={borderColor}
                  borderRadius="lg"
                  boxShadow="2xl"
                  p={3}
                >
                  <Text fontSize="xs" fontWeight="bold" color={inactiveColor}>
                    {t("SAM3 text (active class)")}
                  </Text>
                  <Input
                    size="sm"
                    autoFocus
                    value={samTextPrompt}
                    placeholder={t("e.g. military vehicle")}
                    onChange={(e) => onSamTextPromptChange?.(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && canRun) { onSamTextRun(); setTextMenuOpen(false); }
                    }}
                  />
                  <Text fontSize="10px" color={inactiveColor} opacity={0.7}>
                    {t("Open-vocabulary - a natural phrase beats a class code like pojazd_transportowy.")}
                  </Text>
                  <HStack justify="space-between" align="center">
                    <Text fontSize="xs" color={inactiveColor}>{t("Confidence threshold")}</Text>
                    <Input
                      size="xs"
                      w="64px"
                      type="number"
                      min={0}
                      max={1}
                      step={0.05}
                      value={samTextConf}
                      onChange={(e) => onSamTextConfChange?.(Math.max(0, Math.min(1, Number(e.target.value) || 0)))}
                    />
                  </HStack>
                  <Button
                    size="sm"
                    colorScheme="brand"
                    isDisabled={!canRun}
                    isLoading={samTextBusy}
                    onClick={() => { onSamTextRun(); setTextMenuOpen(false); }}
                  >
                    {t("Run on current view")}
                  </Button>
                  {onSamTextDrawRegion && (
                    <Button
                      size="sm"
                      variant="ghost"
                      isDisabled={!canRun}
                      onClick={() => { onSamTextDrawRegion(); setTextMenuOpen(false); }}
                    >
                      {t("Draw search area")}
                    </Button>
                  )}
                </VStack>
              )}
            </Box>
          );
        })()}
      </VStack>
    </Box>
  );
}
