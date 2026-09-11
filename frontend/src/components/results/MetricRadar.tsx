import Chart from "react-apexcharts";
import { Box, useColorModeValue } from "@chakra-ui/react";

export interface RadarSeries {
  name: string;
  data: number[];
}

interface Props {
  chartId: string;
  categories: string[];
  series: RadarSeries[];
  colors: string[];
}

/** Presentational 0..1 radar shared by the overall and per-class metric radars. */
export default function MetricRadar({ chartId, categories, series, colors }: Props) {
  const gridColor = useColorModeValue("#E2E8F0", "rgba(255,255,255,0.14)");
  const textColor = useColorModeValue("#4A5568", "#CBD5E0");
  // Solidne tło + foreColor + tryb motywu trafiają do eksportu PNG — inaczej eksport jest
  // przezroczysty, a w ciemnym motywie jasne linie/tekst giną (np. na białym tle podglądu).
  const chartBg = useColorModeValue("#FFFFFF", "#242428");
  const foreColor = useColorModeValue("#1B2559", "#D6D6D8");
  const chartMode = useColorModeValue("light", "dark") as "light" | "dark";
  return (
    <Box minW={0}>
      <Chart
        type="radar"
        height={320}
        series={series}
        options={{
          chart: { id: chartId, toolbar: { show: false }, background: chartBg, foreColor },
          theme: { mode: chartMode },
          colors,
          labels: categories,
          fill: { opacity: 0.1 },
          stroke: { width: 2 },
          markers: { size: 3 },
          yaxis: { min: 0, max: 1, tickAmount: 4, labels: { formatter: (value: number) => value.toFixed(2) } },
          legend: { position: "bottom", labels: { colors: textColor } },
          plotOptions: { radar: { polygons: { strokeColors: gridColor, connectorColors: gridColor } } },
          tooltip: { theme: chartMode, y: { formatter: (value: number) => value.toFixed(3) } },
        }}
      />
    </Box>
  );
}
