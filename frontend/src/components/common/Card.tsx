import { Box, useColorModeValue } from "@chakra-ui/react";
import type { BoxProps } from "@chakra-ui/react";

export default function Card(props: BoxProps) {
  const bg = useColorModeValue("white", "#202023");
  const borderColor = useColorModeValue("rgba(20, 20, 24, 0.10)", "rgba(255, 255, 255, 0.07)");
  const boxShadow = useColorModeValue(
    "0 12px 32px rgba(20, 20, 24, 0.06)",
    "0 14px 36px rgba(0, 0, 0, 0.20)",
  );
  return (
    <Box
      bg={bg}
      borderRadius="20px"
      borderWidth="1px"
      borderColor={borderColor}
      boxShadow={boxShadow}
      p={5}
      {...props}
    />
  );
}
