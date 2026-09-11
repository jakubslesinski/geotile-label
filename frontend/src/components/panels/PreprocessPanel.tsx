import {
  VStack,
  HStack,
  Text,
  Slider,
  SliderTrack,
  SliderFilledTrack,
  SliderThumb,
  useColorModeValue,
} from "@chakra-ui/react";
import Card from "../common/Card";
import { useTranslation } from "react-i18next";

interface Props {
  brightness: number;
  gamma: number;
  sharpen: number;
  onChange: (values: { brightness: number; gamma: number; sharpen: number }) => void;
}

export default function PreprocessPanel({
  brightness,
  gamma,
  sharpen,
  onChange,
}: Props) {
  const { t } = useTranslation();
  const textColor = useColorModeValue("navy.700", "white");
  const labelColor = useColorModeValue("secondaryGray.600", "secondaryGray.600");

  return (
    <Card>
      <VStack align="stretch" spacing={4}>
        <Text fontWeight="bold" color={textColor}>
          {t("Preprocessing")}
        </Text>

        <VStack align="stretch" spacing={1}>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Brightness")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>
              {brightness.toFixed(1)}
            </Text>
          </HStack>
          <Slider
            min={0.2}
            max={3}
            step={0.1}
            value={brightness}
            onChange={(v) => onChange({ brightness: v, gamma, sharpen })}
          >
            <SliderTrack><SliderFilledTrack /></SliderTrack>
            <SliderThumb />
          </Slider>
        </VStack>

        <VStack align="stretch" spacing={1}>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Gamma")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>
              {gamma.toFixed(1)}
            </Text>
          </HStack>
          <Slider
            min={0.2}
            max={3}
            step={0.1}
            value={gamma}
            onChange={(v) => onChange({ brightness, gamma: v, sharpen })}
          >
            <SliderTrack><SliderFilledTrack /></SliderTrack>
            <SliderThumb />
          </Slider>
        </VStack>

        <VStack align="stretch" spacing={1}>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Sharpen")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>
              {sharpen.toFixed(1)}
            </Text>
          </HStack>
          <Slider
            min={0}
            max={3}
            step={0.1}
            value={sharpen}
            onChange={(v) => onChange({ brightness, gamma, sharpen: v })}
          >
            <SliderTrack><SliderFilledTrack /></SliderTrack>
            <SliderThumb />
          </Slider>
        </VStack>
      </VStack>
    </Card>
  );
}
