import { type ReactNode, useEffect, useRef, useState } from "react";
import {
  AlertDialog,
  AlertDialogBody,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogOverlay,
  Button,
  HStack,
  Image,
  Text,
  VStack,
} from "@chakra-ui/react";
import { useTranslation } from "react-i18next";
import * as api from "../../api/client";
import logoUrl from "../../assets/geotile-label.png";

interface DesktopCloseGuardProps {
  children: ReactNode;
}

export default function DesktopCloseGuard({ children }: DesktopCloseGuardProps) {
  const { t } = useTranslation();
  const cancelRef = useRef<HTMLButtonElement>(null);
  const quittingRef = useRef(false);
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    if (!api.isTauriRuntime()) return;

    let disposed = false;
    let unlisten: (() => void) | undefined;

    void import("@tauri-apps/api/window").then(({ getCurrentWindow }) => (
      getCurrentWindow().onCloseRequested((event) => {
        if (quittingRef.current) return;
        event.preventDefault();
        setIsOpen(true);
      })
    )).then((stopListening) => {
      if (disposed) stopListening();
      else unlisten = stopListening;
    });

    return () => {
      disposed = true;
      unlisten?.();
    };
  }, []);

  const closeApplication = async () => {
    quittingRef.current = true;
    setIsOpen(false);
    try {
      await api.quitApplication();
    } catch {
      quittingRef.current = false;
      setIsOpen(true);
    }
  };

  return (
    <>
      {children}
      <AlertDialog
        isOpen={isOpen}
        leastDestructiveRef={cancelRef}
        onClose={() => setIsOpen(false)}
        isCentered
      >
        <AlertDialogOverlay>
          <AlertDialogContent mx={4}>
            <AlertDialogHeader>
              <HStack spacing={3}>
                <Image src={logoUrl} alt="GeoTile Label" boxSize="44px" objectFit="contain" />
                <VStack align="start" spacing={0}>
                  <Text fontSize="lg">GeoTile Label</Text>
                  <Text fontSize="sm" fontWeight="normal" color="secondaryGray.500">
                    {t("Closing application")}
                  </Text>
                </VStack>
              </HStack>
            </AlertDialogHeader>
            <AlertDialogBody>{t("Are you sure you want to close the application?")}</AlertDialogBody>
            <AlertDialogFooter>
              <Button ref={cancelRef} onClick={() => setIsOpen(false)}>
                {t("No, stay")}
              </Button>
              <Button colorScheme="red" ml={3} onClick={closeApplication}>
                {t("Yes, close")}
              </Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialogOverlay>
      </AlertDialog>
    </>
  );
}
