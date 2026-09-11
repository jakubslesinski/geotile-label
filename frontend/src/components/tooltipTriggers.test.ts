import { describe, it, expect } from "vitest";

/**
 * `Tooltip` z Chakry wiesza nasłuch zamykający na węźle, do którego trafił JEGO ref:
 *
 *   useEventListener(() => ref.current, "pointerleave", closeWithDelay)   // use-tooltip
 *
 * Komponenty formularzowe Chakry przekazują ref do UKRYTEGO `<input>` (1×1 px, `clip`),
 * a nie do tego, co widać. Kursor nigdy nad nim nie jest, więc `pointerleave` nie pada
 * i dymek zostaje na ekranie po zjechaniu myszką. Drugą drogę wyjścia — `onBlur` —
 * `useCheckbox` dodatkowo wycina z propsów roota (`omit(rest, [..., "onBlur", ...])`).
 *
 * Objaw z produkcji: sześć dymków „Oznacz scenę jako ukończoną" naraz, jeden pod drugim
 * przy lewej krawędzi ekranu — popper kotwiczył się do ukrytego inputa.
 *
 * Lekarstwo: opakować kontrolkę elementem, który trzyma własny ref na widocznym obszarze:
 *
 *   <Tooltip label="..."><Box as="span" display="inline-flex"><Checkbox /></Box></Tooltip>
 */
const HIDDEN_REF_COMPONENTS = ["Checkbox", "Switch", "Radio"];

// Pliki wciąga bundler, więc test nie sięga po API Node — działa też w środowisku
// przeglądarkowym vitest i nie wymaga `@types/node` w tsconfigu aplikacji.
const SOURCES = import.meta.glob("../**/*.tsx", {
  eager: true,
  query: "?raw",
  import: "default",
}) as Record<string, string>;

/** Pierwszy znacznik komponentu otwarty bezpośrednio pod `<Tooltip ...>`. */
export function directTooltipChildren(source: string): Array<{ line: number; child: string }> {
  const lines = source.split(/\r?\n/);
  const found: Array<{ line: number; child: string }> = [];
  lines.forEach((line, index) => {
    if (!line.includes("<Tooltip")) return;
    for (let cursor = index; cursor < Math.min(index + 12, lines.length); cursor += 1) {
      const match = /^\s*<([A-Za-z][A-Za-z0-9]*)/.exec(lines[cursor]);
      if (match && match[1] !== "Tooltip") {
        found.push({ line: cursor + 1, child: match[1] });
        return;
      }
    }
  });
  return found;
}

describe("Tooltip triggers", () => {
  it("widzi źródła aplikacji", () => {
    // Bez tego pusty zbiór plików dawałby zielony test, który niczego nie sprawdza.
    expect(Object.keys(SOURCES).length).toBeGreaterThan(20);
  });

  it("nie opakowuje bezpośrednio kontrolek z ukrytym inputem", () => {
    const offenders: string[] = [];
    for (const [path, source] of Object.entries(SOURCES)) {
      for (const { line, child } of directTooltipChildren(source)) {
        if (HIDDEN_REF_COMPONENTS.includes(child)) {
          offenders.push(`${path}:${line} → <${child}>`);
        }
      }
    }
    expect(offenders, offenders.join("\n")).toEqual([]);
  });

  it("wykrywa wzorzec, gdy się pojawi", () => {
    const sample = [
      '<Tooltip label="x">',
      "  <Checkbox isChecked={false} />",
      "</Tooltip>",
    ].join("\n");
    expect(directTooltipChildren(sample)).toEqual([{ line: 2, child: "Checkbox" }]);
  });
});
