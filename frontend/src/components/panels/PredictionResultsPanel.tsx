import { Badge, Box, Button, Divider, HStack, IconButton, Text, VStack, useColorModeValue } from "@chakra-ui/react";
import { MdDelete } from "react-icons/md";
import { useTranslation } from "react-i18next";
import type { Prediction } from "../../types";
import Card from "../common/Card";
import InfoPopover from "../common/InfoPopover";

interface Props {
  predictions: Prediction[];
  pendingCount: number;
  acceptedCount: number;
  onAcceptAll: () => void;
  onClearAll: () => void;
  onAccept: (ids: string[]) => void;
  onDelete: (ids: string[]) => void;
  onFlipFront?: (id: string) => void;
  selectedPredictionId?: string | null;
  selectedPredictionIds?: string[];
  /** Modyfikatory (Ctrl/Shift) pozwalają na multi-select z tabeli. */
  onSelect?: (id: string | null, modifiers?: { ctrl: boolean; shift: boolean }) => void;
  onDoubleClick?: (id: string) => void;
}

export default function PredictionResultsPanel({
  predictions,
  pendingCount,
  acceptedCount,
  onAcceptAll,
  onClearAll,
  onAccept,
  onDelete,
  onFlipFront,
  selectedPredictionId,
  selectedPredictionIds = [],
  onSelect,
  onDoubleClick,
}: Props) {
  const { t } = useTranslation();
  const textColor = useColorModeValue("navy.700", "white");
  const mutedColor = useColorModeValue("gray.500", "whiteAlpha.600");
  const hoverBg = useColorModeValue("gray.50", "whiteAlpha.100");
  const selectedBg = useColorModeValue("rgba(124, 79, 224, 0.12)", "rgba(155, 108, 255, 0.15)");
  const pendingPredictions = predictions.filter((prediction) => prediction.status === "pending");
  const selectedPredictionSet = new Set(selectedPredictionIds);
  const selectedPendingIds = pendingPredictions
    .filter((prediction) => prediction.id === selectedPredictionId || selectedPredictionSet.has(prediction.id))
    .map((prediction) => prediction.id);

  if (pendingPredictions.length === 0) return null;

  return (
    <Card>
      <VStack align="stretch" spacing={3}>
        <HStack spacing={1}>
          <Text fontWeight="bold" color={textColor}>{t("AI proposals")}</Text>
          <InfoPopover titleKey="info.prediction.pendingResults.title" bodyKey="info.prediction.pendingResults.body" />
        </HStack>
        <Text fontSize="xs" color={mutedColor}>
          {t("Proposals from YOLO, SAM and similar-object search wait here for review.")}
        </Text>
        <HStack spacing={2} flexWrap="wrap">
          <Badge colorScheme="orange">{pendingCount} {t("pending")}</Badge>
          <Badge colorScheme="green">{acceptedCount} {t("accepted")}</Badge>
        </HStack>
        {pendingCount > 0 && (
          <Button size="xs" colorScheme="green" onClick={onAcceptAll}>{t("Accept all")}</Button>
        )}
        {selectedPendingIds.length > 0 && (
          <Button size="xs" colorScheme="red" variant="outline" onClick={() => onDelete(selectedPendingIds)}>
            {t("Delete selected")} ({selectedPendingIds.length})
          </Button>
        )}
        <Button size="xs" variant="outline" onClick={onClearAll}>{t("Clear predictions")}</Button>
        <Divider />
        <Box maxH="250px" overflowY="auto">
          <VStack align="stretch" spacing={1}>
            {pendingPredictions.map((prediction) => {
              const isSelected = prediction.id === selectedPredictionId || selectedPredictionSet.has(prediction.id);
              return (
              <HStack
                key={prediction.id}
                justify="space-between"
                p={1}
                borderRadius="md"
                bg={isSelected ? selectedBg : "transparent"}
                cursor="pointer"
                userSelect="none"
                onClick={(event) => onSelect?.(prediction.id, { ctrl: event.ctrlKey || event.metaKey, shift: event.shiftKey })}
                onDoubleClick={() => onDoubleClick?.(prediction.id)}
                _hover={{ bg: isSelected ? selectedBg : hoverBg }}
              >
                <VStack align="start" spacing={0} minW={0}>
                  <Text fontSize="xs" color={textColor} fontWeight="bold" noOfLines={1}>
                    {prediction.class_name}
                    {prediction.needs_front_direction && (
                      <Text as="span" ml={1} fontSize="xx-small" color="orange.400">{t("OBB")}</Text>
                    )}
                  </Text>
                  <Text fontSize="xx-small" color={mutedColor}>
                    {prediction.source_model} · {prediction.confidence.toFixed(3)}
                  </Text>
                </VStack>
                <HStack spacing={1}>
                  {prediction.needs_front_direction && onFlipFront && (
                    <Button
                      size="xs"
                      variant="ghost"
                      title={t("Rotate front direction by 90 degrees")}
                      onClick={(event) => {
                        event.stopPropagation();
                        onFlipFront(prediction.id);
                      }}
                    >↻</Button>
                  )}
                  <Button size="xs" colorScheme="green" variant="ghost" onClick={(event) => { event.stopPropagation(); onAccept([prediction.id]); }}>+</Button>
                  <IconButton
                    aria-label={t("Delete")}
                    icon={<MdDelete />}
                    size="xs"
                    colorScheme="red"
                    variant="ghost"
                    onClick={(event) => {
                      event.stopPropagation();
                      onDelete([prediction.id]);
                    }}
                  />
                </HStack>
              </HStack>
              );
            })}
          </VStack>
        </Box>
      </VStack>
    </Card>
  );
}
