import { useCallback } from "react";
import { useDropzone } from "react-dropzone";
import {
  Box,
  Text,
  Icon,
  VStack,
  useColorModeValue,
} from "@chakra-ui/react";
import { MdCloudUpload } from "react-icons/md";
import { useTranslation } from "react-i18next";

interface Props {
  onUpload: (file: File) => void;
  isLoading?: boolean;
}

export default function UploadPanel({ onUpload, isLoading }: Props) {
  const { t } = useTranslation();
  const borderColor = useColorModeValue("secondaryGray.400", "whiteAlpha.300");
  const hoverBorder = useColorModeValue("brand.500", "brand.400");

  const onDrop = useCallback(
    (accepted: File[]) => {
      if (accepted.length > 0) onUpload(accepted[0]);
    },
    [onUpload]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      "image/png": [".png"],
      "image/jpeg": [".jpg", ".jpeg"],
      "image/tiff": [".tif", ".tiff"],
    },
    maxFiles: 1,
    disabled: isLoading,
  });

  return (
    <Box
      {...getRootProps()}
      border="2px dashed"
      borderColor={isDragActive ? hoverBorder : borderColor}
      borderRadius="20px"
      p={10}
      textAlign="center"
      cursor="pointer"
      transition="all 0.2s"
      _hover={{ borderColor: hoverBorder }}
      opacity={isLoading ? 0.5 : 1}
    >
      <input {...getInputProps()} />
      <VStack spacing={3}>
        <Icon as={MdCloudUpload} boxSize={12} color="secondaryGray.600" />
        <Text fontWeight="500" color="secondaryGray.600">
          {isDragActive
            ? t("Drop scene file here...")
            : t("Drag & drop a scene (PNG, JPEG, GeoTIFF)")}
        </Text>
        <Text fontSize="sm" color="secondaryGray.500">
          {t("or click to browse")}
        </Text>
      </VStack>
    </Box>
  );
}
