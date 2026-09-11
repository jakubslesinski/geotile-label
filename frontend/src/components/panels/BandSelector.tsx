import { useState } from "react";
import {
  Box,
  Button,
  HStack,
  Select,
  Text,
  VStack,
  useColorModeValue,
} from "@chakra-ui/react";
import { useTranslation } from "react-i18next";

interface Props {
  channels: number;
  initial?: number[]; // current [R, G, B] source band indices (1-based)
  isBusy?: boolean;
  onApply: (rgbBands: number[]) => void;
}

// Band selection for local multiband scenes (>3 bands): pick which source bands
// render as R/G/B. Provider packages select bands during import; this covers plain
// folder rasters.
export default function BandSelector({ channels, initial, onApply, isBusy = false }: Props) {
  const { t } = useTranslation();
  const textColor = useColorModeValue("navy.700", "white");
  const labelColor = useColorModeValue("secondaryGray.600", "whiteAlpha.700");

  const start = initial && initial.length === 3 ? initial : [1, 2, 3];
  const [bands, setBands] = useState<number[]>(start);

  const options = Array.from({ length: channels }, (_, i) => i + 1);
  const setBand = (index: number, value: number) =>
    setBands((prev) => prev.map((b, i) => (i === index ? value : b)));

  const dirty = bands.some((b, i) => b !== start[i]);

  return (
    <Box>
      <Text fontWeight="bold" color={textColor} mb={1}>{t("RGB bands")}</Text>
      <Text fontSize="xs" color={labelColor} mb={2}>
        {t("Scene has {{count}} bands - choose which map to red, green and blue.", { count: channels })}
      </Text>
      <VStack align="stretch" spacing={2}>
        {["R", "G", "B"].map((channel, index) => (
          <HStack key={channel} spacing={2}>
            <Text fontSize="sm" w="18px" color={labelColor}>{channel}</Text>
            <Select
              size="sm"
              value={bands[index]}
              onChange={(event) => setBand(index, Number(event.target.value))}
            >
              {options.map((band) => (
                <option key={band} value={band}>{t("Band {{n}}", { n: band })}</option>
              ))}
            </Select>
          </HStack>
        ))}
        <Button
          size="sm"
          colorScheme="brand"
          variant="outline"
          isLoading={isBusy}
          isDisabled={!dirty}
          onClick={() => onApply(bands)}
        >
          {t("Apply bands")}
        </Button>
      </VStack>
    </Box>
  );
}
