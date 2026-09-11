import { IconButton, Tooltip, useToast } from "@chakra-ui/react";
import { MdContentCopy } from "react-icons/md";
import ApexCharts from "apexcharts";
import { useTranslation } from "react-i18next";

/**
 * Zamień data:URI (base64) na Blob BEZ `fetch`. W aplikacji desktop (Tauri/WebView) CSP
 * `connect-src` blokuje `fetch("data:...")` → "Failed to fetch"; dekodujemy base64 ręcznie.
 */
function dataUriToBlob(dataUri: string): Blob {
  const [meta, base64] = dataUri.split(",");
  const mime = /data:(.*?)(;base64)?$/.exec(meta)?.[1] || "image/png";
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

/**
 * Kopiuje wykres ApexCharts jako PNG do schowka. Wymaga, by wykres miał `chart.id`
 * równe `chartId` (ApexCharts.exec adresuje instancję po id).
 */
export default function CopyChartButton({ chartId }: { chartId: string }) {
  const { t } = useTranslation();
  const toast = useToast();

  const copy = async () => {
    try {
      // dataURI → dla wykresów rasterowych zwraca { imgURI } (base64 PNG).
      const result = (await ApexCharts.exec(chartId, "dataURI", { scale: 2 })) as {
        imgURI?: string;
        blob?: Blob;
      };
      let blob = result?.blob;
      if (!blob && result?.imgURI) {
        // NIE fetch(imgURI) — CSP desktopu blokuje data:URI. Dekoduj base64 lokalnie.
        blob = dataUriToBlob(result.imgURI);
      }
      if (!blob) throw new Error("no image produced");
      await navigator.clipboard.write([
        new ClipboardItem({ [blob.type || "image/png"]: blob }),
      ]);
      toast({ title: t("Chart copied to clipboard"), status: "success", duration: 1800 });
    } catch (error: any) {
      toast({
        title: t("Could not copy chart"),
        description: error?.message || String(error),
        status: "error",
        duration: 4000,
        isClosable: true,
      });
    }
  };

  return (
    <Tooltip label={t("Copy chart as PNG")} openDelay={400}>
      <IconButton
        aria-label={t("Copy chart as PNG")}
        size="xs"
        variant="ghost"
        icon={<MdContentCopy />}
        onClick={copy}
      />
    </Tooltip>
  );
}
