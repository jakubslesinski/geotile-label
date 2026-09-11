import { Flex, HStack, Stat, StatLabel, StatNumber, useColorModeValue } from "@chakra-ui/react";
import Card from "./Card";
import InfoPopover from "./InfoPopover";

interface Props {
  label: string;
  value: string | number;
  icon?: React.ReactNode;
  infoTitleKey?: string;
  infoBodyKey?: string;
}

export default function MiniStatistics({ label, value, icon, infoTitleKey, infoBodyKey }: Props) {
  const textColor = useColorModeValue("secondaryGray.900", "white");
  const labelColor = useColorModeValue("secondaryGray.600", "secondaryGray.600");

  return (
    <Card>
      <Flex align="center" gap={4}>
        {icon && icon}
        <Stat>
          <StatLabel fontSize="xs" color={labelColor}>
            <HStack spacing={1}>
              <span>{label}</span>
              {infoTitleKey && infoBodyKey && (
                <InfoPopover titleKey={infoTitleKey} bodyKey={infoBodyKey} />
              )}
            </HStack>
          </StatLabel>
          <StatNumber fontSize="2xl" color={textColor}>
            {value}
          </StatNumber>
        </Stat>
      </Flex>
    </Card>
  );
}
