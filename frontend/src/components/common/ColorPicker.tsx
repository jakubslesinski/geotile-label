import { useEffect, useState } from "react";
import {
  Box,
  Button,
  HStack,
  Input,
  Popover,
  PopoverArrow,
  PopoverBody,
  PopoverContent,
  PopoverTrigger,
  Portal,
  SimpleGrid,
  Text,
  Tooltip,
  VStack,
  useColorModeValue,
  useDisclosure,
} from "@chakra-ui/react";
import { useTranslation } from "react-i18next";

/**
 * Paleta domyślna backendu - `DEFAULT_COLORS` w `backend/routers/classes.py`, ta sama,
 * którą dostają kolejne klasy zakładane bez wskazania koloru. Powtórzona tutaj świadomie:
 * nie ma endpointu, który by ją wystawiał, a swatche mają pokazywać dokładnie te kolory,
 * które projekt nadaje sam, żeby ręczny wybór i automat nie rozjeżdżały się wizualnie.
 */
export const CLASS_PALETTE = [
  "#FF0000", "#00FF00", "#0000FF", "#FFFF00", "#FF00FF",
  "#00FFFF", "#FF8000", "#8000FF", "#00FF80",
];

const HEX_COLOR = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

/** `#abc` i `abc` → `#AABBCC`; `null`, gdy to nie jest kolor. Ten sam kontrakt co walidator
 *  po stronie backendu (`_hex_color` w `backend/models/annotation.py`). */
export function normalizeHexColor(value: string): string | null {
  const text = value.trim();
  const withHash = text.startsWith("#") ? text : `#${text}`;
  if (!HEX_COLOR.test(withHash)) return null;
  if (withHash.length === 4) {
    return `#${withHash.slice(1).split("").map((char) => char + char).join("")}`.toUpperCase();
  }
  return withHash.toUpperCase();
}

interface Props {
  color: string;
  /** Wywoływane dopiero po zatwierdzeniu, nigdy w trakcie przesuwania po palecie. */
  onChange: (color: string) => void;
  /** Opis celu kliknięcia dla czytnika ekranu, np. „Kolor klasy: pojazd". */
  label: string;
  isDisabled?: boolean;
}

/**
 * Kwadrat z próbką koloru, który po kliknięciu otwiera wybór: paleta domyślna, systemowy
 * dialog koloru albo wpisanie HEX.
 *
 * Wybór jest trzymany w stanie roboczym i oddawany wywołującemu **dopiero po
 * „Zatwierdź"**. Zmiana koloru klasy zapisuje się do pliku klas projektu, więc
 * przypadkowe trafienie w swatch nie ma prawa niczego utrwalić.
 */
export default function ColorPicker({ color, onChange, label, isDisabled = false }: Props) {
  const { t } = useTranslation();
  const { isOpen, onOpen, onClose } = useDisclosure();
  const [draft, setDraft] = useState(color);
  const [text, setText] = useState(color);

  const background = useColorModeValue("white", "navy.800");
  const borderColor = useColorModeValue("secondaryGray.200", "whiteAlpha.300");
  const textColor = useColorModeValue("navy.700", "whiteAlpha.900");
  const swatchBorder = useColorModeValue("blackAlpha.300", "whiteAlpha.400");

  // Każde otwarcie zaczyna od koloru obecnie zapisanego, a nie od porzuconej próby
  // z poprzedniego razu.
  useEffect(() => {
    if (isOpen) {
      setDraft(color);
      setText(color);
    }
  }, [isOpen, color]);

  const typed = normalizeHexColor(text);
  const isTextValid = typed !== null;

  const handleText = (value: string) => {
    setText(value);
    const normalized = normalizeHexColor(value);
    if (normalized) setDraft(normalized);
  };

  const handleApply = () => {
    const next = normalizeHexColor(text) ?? draft;
    onClose();
    if (next !== color) onChange(next);
  };

  return (
    <Popover isOpen={isOpen} onOpen={onOpen} onClose={onClose} placement="bottom-start" isLazy>
      <Tooltip label={label} hasArrow openDelay={350}>
        <Box as="span" display="inline-flex" flexShrink={0}>
          <PopoverTrigger>
            <Button
              aria-label={label}
              isDisabled={isDisabled}
              minW="20px"
              w="20px"
              h="20px"
              p={0}
              borderRadius="sm"
              border="1px solid"
              borderColor={swatchBorder}
              bg={color}
              _hover={{ bg: color, transform: "scale(1.1)" }}
              _active={{ bg: color }}
            />
          </PopoverTrigger>
        </Box>
      </Tooltip>
      <Portal>
        <PopoverContent
          w="228px"
          bg={background}
          borderColor={borderColor}
          _focus={{ boxShadow: "none" }}
        >
          <PopoverArrow bg={background} />
          <PopoverBody>
            <VStack align="stretch" spacing={3}>
              <Text fontSize="xs" fontWeight="bold" color={textColor}>
                {t("Default palette")}
              </Text>
              <SimpleGrid columns={9} spacing={1}>
                {CLASS_PALETTE.map((preset) => (
                  <Box
                    as="button"
                    type="button"
                    key={preset}
                    aria-label={preset}
                    title={preset}
                    h="18px"
                    borderRadius="sm"
                    border="2px solid"
                    borderColor={draft.toUpperCase() === preset ? textColor : "transparent"}
                    bg={preset}
                    onClick={() => {
                      setDraft(preset);
                      setText(preset);
                    }}
                  />
                ))}
              </SimpleGrid>

              <HStack spacing={2}>
                {/* Systemowy dialog koloru. `Input type="color"` to jedyne miejsce, gdzie
                    przeglądarka daje pełne koło barw bez dokładania zależności. */}
                <Input
                  type="color"
                  aria-label={t("Pick a color")}
                  value={draft}
                  onChange={(event) => {
                    setDraft(event.target.value.toUpperCase());
                    setText(event.target.value.toUpperCase());
                  }}
                  w="36px"
                  h="32px"
                  p={0}
                  border="1px solid"
                  borderColor={swatchBorder}
                  cursor="pointer"
                />
                <Input
                  size="sm"
                  aria-label={t("Hex value")}
                  placeholder="#1A9E5C"
                  value={text}
                  isInvalid={!isTextValid}
                  onChange={(event) => handleText(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && isTextValid) handleApply();
                  }}
                  fontFamily="mono"
                />
              </HStack>

              <HStack justify="flex-end" spacing={2}>
                <Button size="xs" variant="ghost" onClick={onClose}>
                  {t("Cancel")}
                </Button>
                <Button size="xs" colorScheme="brand" isDisabled={!isTextValid} onClick={handleApply}>
                  {t("Apply")}
                </Button>
              </HStack>
            </VStack>
          </PopoverBody>
        </PopoverContent>
      </Portal>
    </Popover>
  );
}
