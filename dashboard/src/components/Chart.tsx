import { BarChart, CustomChart, HeatmapChart, LineChart, ScatterChart } from "echarts/charts";
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import { useTheme } from "@/lib/theme";

echarts.use([
  BarChart,
  CustomChart,
  HeatmapChart,
  LineChart,
  ScatterChart,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

export type ChartOption = echarts.EChartsCoreOption;
export type ChartEvents = Record<string, (params: unknown) => void>;

/** Axis, label and grid colors that follow the theme (charts are drawn on canvas). */
export function chartColors(dark: boolean) {
  return dark
    ? { text: "#8494ad", line: "#22314d", fg: "#e2e8f0", tooltip: "#111a2e" }
    : { text: "#64748b", line: "#dbe2ea", fg: "#0f172a", tooltip: "#ffffff" };
}

export function Chart({
  option,
  height = 220,
  events,
  label,
}: {
  option: ChartOption;
  height?: number;
  events?: ChartEvents;
  label: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const { dark } = useTheme();

  useEffect(() => {
    if (!ref.current) return;
    const c = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chart.current = c;
    const observer = new ResizeObserver(() => c.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      c.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    const colors = chartColors(dark);
    chart.current?.setOption(
      {
        backgroundColor: "transparent",
        textStyle: { color: colors.text, fontFamily: "inherit" },
        tooltip: {
          backgroundColor: colors.tooltip,
          borderColor: colors.line,
          textStyle: { color: colors.fg, fontSize: 12 },
        },
        ...option,
      },
      { notMerge: true },
    );
  }, [option, dark]);

  useEffect(() => {
    const c = chart.current;
    if (!c || !events) return;
    for (const [name, handler] of Object.entries(events)) c.on(name, handler);
    return () => {
      for (const [name, handler] of Object.entries(events)) c.off(name, handler);
    };
  }, [events]);

  return <div ref={ref} role="img" aria-label={label} style={{ height, width: "100%" }} />;
}
