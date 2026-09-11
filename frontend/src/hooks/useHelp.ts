import { useCallback, useEffect } from "react";
import { useToast } from "@chakra-ui/react";
import { useTranslation } from "react-i18next";
import { openHelp } from "../desktop/help";

/**
 * Zwraca funkcję otwierającą dokumentację. Błąd — np. uszkodzone zasoby w instalacji —
 * jest pokazywany użytkownikowi, a nie połykany po cichu.
 */
export function useHelp() {
  const toast = useToast();
  const { t } = useTranslation();

  return useCallback(
    async (page?: string) => {
      try {
        await openHelp(page);
      } catch (error) {
        toast({
          title: t("Cannot open documentation"),
          description: error instanceof Error ? error.message : String(error),
          status: "error",
          duration: 9000,
          isClosable: true,
        });
      }
    },
    [toast, t]
  );
}

/**
 * Rejestruje F1 jako globalny skrót otwierający dokumentację. Skrót jest ignorowany
 * podczas pisania w polu tekstowym, żeby nie przerywać wpisywania nazw i wartości.
 */
export function useHelpHotkey() {
  const openHelpPage = useHelp();

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "F1" || event.ctrlKey || event.altKey || event.metaKey) {
        return;
      }

      const target = event.target as HTMLElement | null;
      if (target?.isContentEditable) return;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

      event.preventDefault();
      void openHelpPage();
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [openHelpPage]);
}
