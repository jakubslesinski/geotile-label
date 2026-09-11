import { type ReactNode, useState } from "react";
import {
  Box,
  IconButton,
  VStack,
  Radio,
  RadioGroup,
  Text,
  Slider,
  SliderTrack,
  SliderFilledTrack,
  SliderThumb,
  HStack,
  Switch,
  useColorModeValue,
} from "@chakra-ui/react";
import { MdDisplaySettings, MdLayers } from "react-icons/md";
import { BASEMAPS } from "../../config/basemaps";
import { useTranslation } from "react-i18next";

interface Props {
  geoMode?: boolean;
  activeBasemapId: string | null;
  onBasemapChange: (id: string | null) => void;
  sceneOpacity: number;
  onOpacityChange: (value: number) => void;
  showAnnotationBoxes: boolean;
  onShowAnnotationBoxesChange: (value: boolean) => void;
  showAnnotationLabels: boolean;
  onShowAnnotationLabelsChange: (value: boolean) => void;
  showSceneOpacity?: boolean;
  showSceneVisibilityToggle?: boolean;
  sceneVisible?: boolean;
  onSceneVisibleChange?: (value: boolean) => void;
  showDisplayControl?: boolean;
  displayContent?: ReactNode;
}

export default function LayerControl({
  geoMode = false,
  activeBasemapId,
  onBasemapChange,
  sceneOpacity,
  onOpacityChange,
  showAnnotationBoxes,
  onShowAnnotationBoxesChange,
  showAnnotationLabels,
  onShowAnnotationLabelsChange,
  showSceneOpacity = true,
  showSceneVisibilityToggle = false,
  sceneVisible = true,
  onSceneVisibleChange,
  showDisplayControl = false,
  displayContent,
}: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [displayOpen, setDisplayOpen] = useState(false);
  const panelBg = useColorModeValue("white", "rgba(28, 28, 31, 0.94)");
  const textColor = useColorModeValue("navy.700", "white");
  const hoverBg = useColorModeValue("gray.100", "navy.700");

  const handleBasemapChange = (value: string) => {
    if (value === "none") {
      onBasemapChange(null);
    } else {
      const bm = BASEMAPS.find((b) => b.id === value);
      if (bm) onBasemapChange(bm.id);
    }
  };

  const currentValue = activeBasemapId && BASEMAPS.some((item) => item.id === activeBasemapId)
    ? activeBasemapId
    : "none";

  const handleLayersToggle = () => {
    setOpen(!open);
    setDisplayOpen(false);
  };

  const handleDisplayToggle = () => {
    setDisplayOpen(!displayOpen);
    setOpen(false);
  };

  const closeActivePanel = () => {
    setOpen(false);
    setDisplayOpen(false);
  };

  return (
    <Box position="absolute" top="92px" right="10px" zIndex={1000}>
      <HStack align="flex-start" spacing={2}>
        {(open || displayOpen) && (
        <Box
          bg={panelBg}
          borderRadius="md"
          borderWidth="1px"
          borderColor={useColorModeValue("gray.200", "rgba(255, 255, 255, 0.10)")}
          boxShadow="lg"
          backdropFilter="blur(10px)"
          p={3}
          minW={displayOpen ? "360px" : "220px"}
          maxW="min(440px, calc(100vw - 120px))"
          maxH="calc(100vh - 150px)"
          overflowY="auto"
          onClick={(e) => e.stopPropagation()}
        >
          <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
            <Text fontSize="xs" fontWeight="bold" color={textColor}>
              {displayOpen ? t("Display Settings") : t("Layers")}
            </Text>
            <IconButton
              aria-label={t("Close")}
              icon={displayOpen ? <MdDisplaySettings /> : <MdLayers />}
              size="xs"
              variant="ghost"
              onClick={closeActivePanel}
            />
          </Box>

          {displayOpen ? displayContent : (
            <>
          <VStack align="stretch" spacing={2}>
            <HStack justify="space-between">
              <Text fontSize="xs" color={textColor}>{t("Bounding boxes")}</Text>
              <Switch
                size="sm"
                colorScheme="brand"
                isChecked={showAnnotationBoxes}
                onChange={(e) => onShowAnnotationBoxesChange(e.target.checked)}
              />
            </HStack>
            <HStack justify="space-between">
              <Text fontSize="xs" color={textColor}>{t("Box labels")}</Text>
              <Switch
                size="sm"
                colorScheme="brand"
                isChecked={showAnnotationLabels}
                onChange={(e) => onShowAnnotationLabelsChange(e.target.checked)}
              />
            </HStack>
            {showSceneVisibilityToggle && (
              <HStack justify="space-between">
                <Text fontSize="xs" color={textColor}>{t("Scene raster")}</Text>
                <Switch
                  size="sm"
                  colorScheme="brand"
                  isChecked={sceneVisible}
                  onChange={(e) => onSceneVisibleChange?.(e.target.checked)}
                />
              </HStack>
            )}
          </VStack>

          {geoMode && (
            <>
              <Text fontSize="xs" fontWeight="bold" color={textColor} mt={3} mb={1}>
                {t("Basemap")}
              </Text>
              <RadioGroup value={currentValue} onChange={handleBasemapChange}>
                <VStack align="start" spacing={1}>
                  <Radio value="none" size="sm" colorScheme="brand">
                    <Text fontSize="xs" color={textColor}>{t("None")}</Text>
                  </Radio>
                  {BASEMAPS.map((bm) => (
                    <Radio key={bm.id} value={bm.id} size="sm" colorScheme="brand">
                      <Text fontSize="xs" color={textColor}>{bm.name}</Text>
                    </Radio>
                  ))}
                </VStack>
              </RadioGroup>

              {showSceneOpacity && (
                <>
                  <Text fontSize="xs" fontWeight="bold" color={textColor} mt={3} mb={1}>
                    {t("Scene opacity")}: {sceneOpacity}%
                  </Text>
                  <Slider
                    min={30}
                    max={100}
                    step={5}
                    value={sceneOpacity}
                    onChange={onOpacityChange}
                    isDisabled={!sceneVisible}
                    colorScheme="brand"
                    size="sm"
                  >
                    <SliderTrack>
                      <SliderFilledTrack />
                    </SliderTrack>
                    <SliderThumb />
                  </Slider>
                </>
              )}
            </>
          )}
            </>
          )}
        </Box>
        )}

        <VStack spacing={2}>
          <IconButton
            aria-label={t("Layers")}
            icon={<MdLayers />}
            size="sm"
            onClick={handleLayersToggle}
            bg={panelBg}
            colorScheme={open ? "brand" : undefined}
            boxShadow="md"
            _hover={{ bg: hoverBg }}
          />
          {showDisplayControl && displayContent && (
            <IconButton
              aria-label={t("Display Settings")}
              icon={<MdDisplaySettings />}
              size="sm"
              onClick={handleDisplayToggle}
              bg={panelBg}
              colorScheme={displayOpen ? "brand" : undefined}
              boxShadow="md"
              _hover={{ bg: hoverBg }}
            />
          )}
        </VStack>
      </HStack>
    </Box>
  );
}
