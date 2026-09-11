import { useEffect } from "react";
import type { LabelClass, MapTool } from "../types";

interface Options {
  classes: LabelClass[];
  onSelectClass: (classId: number) => void;
  onToggleNegative?: () => void;
  onUndo?: () => void;
  onEscape?: () => void;
  onToggleDrawing?: () => void;
  onOpenClassSelector?: () => void;
  onSelectMapTool?: (tool: MapTool) => void;
  onToggleGrid?: () => void;
  onToggleSceneRaster?: () => void;
  onDecreaseSceneOpacity?: () => void;
  onIncreaseSceneOpacity?: () => void;
  onDecreaseDisplayGamma?: () => void;
  onIncreaseDisplayGamma?: () => void;
  onDeleteSelected?: () => void;
  onReviewTile?: () => void;
  onReviewAllVisible?: () => void;
  onExcludeTile?: () => void;
  onExcludeAllVisible?: () => void;
}

export function useHotkeys({
  classes,
  onSelectClass,
  onToggleNegative,
  onUndo,
  onEscape,
  onToggleDrawing,
  onOpenClassSelector,
  onSelectMapTool,
  onToggleGrid,
  onToggleSceneRaster,
  onDecreaseSceneOpacity,
  onIncreaseSceneOpacity,
  onDecreaseDisplayGamma,
  onIncreaseDisplayGamma,
  onDeleteSelected,
  onReviewTile,
  onReviewAllVisible,
  onExcludeTile,
  onExcludeAllVisible,
}: Options) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Ignore when typing in inputs
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        (e.target instanceof HTMLElement && e.target.isContentEditable)
      )
        return;

      // Number keys 1-9 → select class
      const num = parseInt(e.key);
      if (num >= 1 && num <= 9) {
        const cls = classes.find((c) => c.hotkey === num);
        if (cls) {
          onSelectClass(cls.id);
          e.preventDefault();
        }
      }

      // N → toggle negative mode
      if (e.key === "n" || e.key === "N") {
        onToggleNegative?.();
        e.preventDefault();
      }

      // Ctrl+Z → undo
      if (e.key === "z" && (e.ctrlKey || e.metaKey)) {
        onUndo?.();
        e.preventDefault();
      }

      // Escape → cancel
      if (e.key === "Escape") {
        onEscape?.();
        e.preventDefault();
      }

      // D -> toggle drawing mode
      if (
        (e.key === "d" || e.key === "D") &&
        !e.ctrlKey &&
        !e.metaKey &&
        !e.altKey
      ) {
        onToggleDrawing?.();
        e.preventDefault();
      }

      // Map tool shortcuts
      if (!e.ctrlKey && !e.metaKey && !e.altKey) {
        if (e.key === "v" || e.key === "V") {
          onSelectMapTool?.("select");
          e.preventDefault();
        } else if (e.key === "a" || e.key === "A") {
          onSelectMapTool?.("multi_select");
          e.preventDefault();
        } else if (e.key === "m" || e.key === "M") {
          onSelectMapTool?.("measure");
          e.preventDefault();
        } else if (e.key === "p" || e.key === "P") {
          onSelectMapTool?.("class_paint");
          e.preventDefault();
        } else if (e.key === "k" || e.key === "K") {
          onOpenClassSelector?.();
          e.preventDefault();
        }
      }

      // S -> toggle grid visibility
      if (
        (e.key === "s" || e.key === "S") &&
        !e.ctrlKey &&
        !e.metaKey &&
        !e.altKey
      ) {
        onToggleGrid?.();
        e.preventDefault();
      }

      // Z -> toggle the source raster layer (Ctrl+Z remains undo)
      if (
        (e.key === "z" || e.key === "Z") &&
        !e.ctrlKey &&
        !e.metaKey &&
        !e.altKey
      ) {
        onToggleSceneRaster?.();
        e.preventDefault();
      }

      const isLeftBracket = e.key === "[" || e.code === "BracketLeft";
      const isRightBracket = e.key === "]" || e.code === "BracketRight";

      if (isLeftBracket && (e.ctrlKey || e.metaKey)) {
        onDecreaseDisplayGamma?.();
        e.preventDefault();
      } else if (isRightBracket && (e.ctrlKey || e.metaKey)) {
        onIncreaseDisplayGamma?.();
        e.preventDefault();
      } else if (isLeftBracket && !e.ctrlKey && !e.metaKey && !e.altKey) {
        onDecreaseSceneOpacity?.();
        e.preventDefault();
      } else if (isRightBracket && !e.ctrlKey && !e.metaKey && !e.altKey) {
        onIncreaseSceneOpacity?.();
        e.preventDefault();
      }

      // Delete / Backspace -> remove selected annotation
      if (
        (e.key === "Delete" || e.key === "Backspace") &&
        !e.ctrlKey &&
        !e.metaKey &&
        !e.altKey
      ) {
        if (onDeleteSelected) {
          onDeleteSelected();
          e.preventDefault();
        }
      }

      // R → toggle reviewed on hovered tile
      if (e.key === "r" && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
        if (onReviewTile) {
          onReviewTile();
          e.preventDefault();
        }
      }

      // Shift+R → mark all visible tiles as reviewed
      if (e.key === "R" && e.shiftKey && !e.ctrlKey && !e.metaKey) {
        if (onReviewAllVisible) {
          onReviewAllVisible();
          e.preventDefault();
        }
      }

      // E → toggle excluded on hovered tile
      if (e.key === "e" && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
        if (onExcludeTile) {
          onExcludeTile();
          e.preventDefault();
        }
      }

      // Shift+E → exclude all visible tiles
      if (e.key === "E" && e.shiftKey && !e.ctrlKey && !e.metaKey) {
        if (onExcludeAllVisible) {
          onExcludeAllVisible();
          e.preventDefault();
        }
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [
    classes,
    onSelectClass,
    onToggleNegative,
    onUndo,
    onEscape,
    onToggleDrawing,
    onOpenClassSelector,
    onSelectMapTool,
    onToggleGrid,
    onToggleSceneRaster,
    onDecreaseSceneOpacity,
    onIncreaseSceneOpacity,
    onDecreaseDisplayGamma,
    onIncreaseDisplayGamma,
    onDeleteSelected,
    onReviewTile,
    onReviewAllVisible,
    onExcludeTile,
    onExcludeAllVisible,
  ]);
}
