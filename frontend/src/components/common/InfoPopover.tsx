import {
  Box,
  Button,
  IconButton,
  Popover,
  PopoverArrow,
  PopoverBody,
  PopoverCloseButton,
  PopoverContent,
  PopoverHeader,
  PopoverTrigger,
  Portal,
  Tooltip,
  useColorModeValue,
} from "@chakra-ui/react";
import { useTranslation } from "react-i18next";
import { MdInfoOutline, MdOpenInNew } from "react-icons/md";
import { useHelp } from "../../hooks/useHelp";

interface Props {
  titleKey: string;
  bodyKey: string;
  /**
   * Strona dokumentacji rozwijająca temat, np. `datasets/audyt.html#spatial-leakage`.
   * Popover ma pozostać krótki — pełna instrukcja mieszka w dokumentacji, nie w UI.
   */
  helpPage?: string;
}

export default function InfoPopover({ titleKey, bodyKey, helpPage }: Props) {
  const { t } = useTranslation();
  const openHelp = useHelp();
  const background = useColorModeValue("white", "navy.800");
  const borderColor = useColorModeValue("secondaryGray.200", "whiteAlpha.300");
  const textColor = useColorModeValue("navy.700", "whiteAlpha.900");

  return (
    <Popover placement="auto" isLazy>
      <Tooltip label={t("More information")} hasArrow openDelay={350}>
        <Box as="span" display="inline-flex" flexShrink={0}>
          <PopoverTrigger>
            <IconButton
              aria-label={t("More information")}
              icon={<MdInfoOutline size={17} />}
              size="xs"
              minW="22px"
              h="22px"
              variant="ghost"
              color="secondaryGray.600"
              borderRadius="full"
              onClick={(event) => event.stopPropagation()}
            />
          </PopoverTrigger>
        </Box>
      </Tooltip>
      <Portal>
        <PopoverContent
          maxW="360px"
          bg={background}
          borderColor={borderColor}
          color={textColor}
          boxShadow="xl"
          onClick={(event) => event.stopPropagation()}
        >
          <PopoverArrow bg={background} />
          <PopoverCloseButton />
          <PopoverHeader pr="36px" fontWeight="700">
            {t(titleKey)}
          </PopoverHeader>
          <PopoverBody fontSize="sm" lineHeight="1.55" pb={4}>
            {t(bodyKey)}
            {helpPage && (
              <Box mt={3}>
                <Button
                  size="xs"
                  variant="link"
                  colorScheme="brand"
                  rightIcon={<MdOpenInNew size={13} />}
                  onClick={() => void openHelp(helpPage)}
                >
                  {t("Learn more")}
                </Button>
              </Box>
            )}
          </PopoverBody>
        </PopoverContent>
      </Portal>
    </Popover>
  );
}
