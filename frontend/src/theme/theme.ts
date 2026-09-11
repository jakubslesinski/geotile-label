import { extendTheme, type ThemeConfig } from "@chakra-ui/react";

const config: ThemeConfig = {
  initialColorMode: "dark",
  useSystemColorMode: false,
};

const theme = extendTheme({
  config,
  colors: {
    brand: {
      50: "#F6F2FF",
      100: "#EFE9FF",
      200: "#9B6CFF",
      300: "#AD86FF",
      400: "#8150E8",
      500: "#7C4FE0",
      600: "#6D3FD1",
      700: "#5E32B8",
      800: "#512BA2",
      900: "#3D2179",
    },
    secondaryGray: {
      50: "#FAFAFB",
      100: "#ECECEF",
      200: "#E2E2E5",
      300: "#F0F0F2",
      400: "#D6D6D8",
      500: "#8B8B8F",
      600: "#9A9A9D",
      700: "#7D7D80",
      800: "#5C5C60",
      900: "#29292E",
    },
    navy: {
      50: "#F5F5F6",
      100: "#E5E5E7",
      200: "#C3C3C6",
      300: "#A7A7AD",
      400: "#85858C",
      500: "#5D5D63",
      600: "#35353A",
      700: "#29292E",
      800: "#202023",
      900: "#111113",
    },
  },
  semanticTokens: {
    colors: {
      "app.bg": { default: "#F5F5F6", _dark: "#111113" },
      "app.main": { default: "#F0F0F2", _dark: "#1C1C1E" },
      "app.sidebar": { default: "white", _dark: "#19191B" },
      "app.surface": { default: "white", _dark: "#202023" },
      "app.surfaceRaised": { default: "white", _dark: "#242428" },
      "app.surfaceHover": { default: "gray.50", _dark: "#29292E" },
      "app.input": { default: "#FAFAFB", _dark: "#18181A" },
      "app.mapEmpty": { default: "#F5F5F6", _dark: "#131316" },
      "app.text": { default: "#202024", _dark: "#F5F5F6" },
      "app.textMuted": { default: "#6F6F76", _dark: "#9A9A9D" },
      "app.accent": { default: "#7C4FE0", _dark: "#9B6CFF" },
      "app.accentHover": { default: "#6D3FD1", _dark: "#AD86FF" },
      "app.border": {
        default: "rgba(20, 20, 24, 0.10)",
        _dark: "rgba(255, 255, 255, 0.07)",
      },
      "app.borderHover": {
        default: "rgba(20, 20, 24, 0.18)",
        _dark: "rgba(255, 255, 255, 0.13)",
      },
    },
  },
  fonts: {
    heading: `'Inter', 'Segoe UI', Arial, sans-serif`,
    body: `'Inter', 'Segoe UI', Arial, sans-serif`,
  },
  styles: {
    global: (props: any) => ({
      "html, body, #root": {
        minHeight: "100%",
      },
      body: {
        bg: props.colorMode === "dark" ? "#111113" : "#F5F5F6",
        color: props.colorMode === "dark" ? "#D6D6D8" : "#3D3D42",
        fontFamily: "'Inter', 'Segoe UI', Arial, sans-serif",
        letterSpacing: "-0.2px",
        WebkitFontSmoothing: "antialiased",
        MozOsxFontSmoothing: "grayscale",
      },
      "*::selection": {
        bg: props.colorMode === "dark" ? "rgba(155, 108, 255, 0.32)" : "brand.100",
      },
      "*": {
        scrollbarColor: props.colorMode === "dark" ? "#4A4A50 transparent" : undefined,
      },
      "::-webkit-scrollbar": {
        width: "10px",
        height: "10px",
      },
      "::-webkit-scrollbar-track": {
        bg: "transparent",
      },
      "::-webkit-scrollbar-thumb": {
        bg: props.colorMode === "dark" ? "#4A4A50" : "#C3C3C6",
        borderRadius: "999px",
        border: "2px solid transparent",
        backgroundClip: "padding-box",
      },
      "::-webkit-scrollbar-thumb:hover": {
        bg: props.colorMode === "dark" ? "#606067" : "#9A9A9D",
        border: "2px solid transparent",
        backgroundClip: "padding-box",
      },
    }),
  },
  components: {
    Card: {
      baseStyle: (props: any) => ({
        container: {
          bg: props.colorMode === "dark" ? "#202023" : "white",
          borderWidth: "1px",
          borderColor: props.colorMode === "dark"
            ? "rgba(255, 255, 255, 0.07)"
            : "rgba(20, 20, 24, 0.10)",
          borderRadius: "20px",
          boxShadow: props.colorMode === "dark"
            ? "0 14px 36px rgba(0, 0, 0, 0.20)"
            : "0 12px 32px rgba(20, 20, 24, 0.06)",
        },
      }),
    },
    Button: {
      baseStyle: (props: any) => ({
        borderRadius: "10px",
        fontWeight: 600,
        transition: "background-color 0.15s ease, border-color 0.15s ease, color 0.15s ease",
        _focusVisible: {
          boxShadow: props.colorMode === "dark"
            ? "0 0 0 3px rgba(174, 134, 255, 0.42)"
            : "0 0 0 3px rgba(124, 79, 224, 0.30)",
        },
      }),
    },
    Input: {
      variants: {
        outline: (props: any) => ({
          field: {
            bg: props.colorMode === "dark" ? "#18181A" : "#FAFAFB",
            borderColor: props.colorMode === "dark"
              ? "rgba(255, 255, 255, 0.09)"
              : "inherit",
            _hover: {
              borderColor: props.colorMode === "dark"
                ? "rgba(255, 255, 255, 0.16)"
                : "gray.300",
            },
            _focusVisible: {
              borderColor: props.colorMode === "dark" ? "brand.400" : "#7C4FE0",
              boxShadow: props.colorMode === "dark"
                ? "0 0 0 1px var(--chakra-colors-brand-400)"
                : "0 0 0 1px #7C4FE0",
            },
            _placeholder: {
              color: props.colorMode === "dark" ? "#6F6F76" : "gray.400",
            },
          },
        }),
      },
    },
    Select: {
      variants: {
        outline: (props: any) => ({
          field: {
            bg: props.colorMode === "dark" ? "#18181A" : "#FAFAFB",
            borderColor: props.colorMode === "dark"
              ? "rgba(255, 255, 255, 0.09)"
              : "inherit",
            _hover: {
              borderColor: props.colorMode === "dark"
                ? "rgba(255, 255, 255, 0.16)"
                : "gray.300",
            },
            _focusVisible: {
              borderColor: props.colorMode === "dark" ? "brand.400" : "#7C4FE0",
              boxShadow: props.colorMode === "dark"
                ? "0 0 0 1px var(--chakra-colors-brand-400)"
                : "0 0 0 1px #7C4FE0",
            },
          },
        }),
      },
    },
    Textarea: {
      variants: {
        outline: (props: any) => ({
          bg: props.colorMode === "dark" ? "#18181A" : "#FAFAFB",
          borderColor: props.colorMode === "dark"
            ? "rgba(255, 255, 255, 0.09)"
            : "inherit",
          _hover: {
            borderColor: props.colorMode === "dark"
              ? "rgba(255, 255, 255, 0.16)"
              : "gray.300",
          },
          _focusVisible: {
            borderColor: props.colorMode === "dark" ? "brand.400" : "#7C4FE0",
            boxShadow: props.colorMode === "dark"
              ? "0 0 0 1px var(--chakra-colors-brand-400)"
              : "0 0 0 1px #7C4FE0",
          },
        }),
      },
    },
    Modal: {
      baseStyle: (props: any) => ({
        dialog: {
          bg: props.colorMode === "dark" ? "#242428" : "white",
          borderWidth: props.colorMode === "dark" ? "1px" : 0,
          borderColor: "rgba(255, 255, 255, 0.08)",
        },
        overlay: {
          bg: props.colorMode === "dark" ? "rgba(0, 0, 0, 0.72)" : "blackAlpha.600",
        },
      }),
    },
    Popover: {
      baseStyle: (props: any) => ({
        content: {
          bg: props.colorMode === "dark" ? "#242428" : "white",
          borderColor: props.colorMode === "dark"
            ? "rgba(255, 255, 255, 0.10)"
            : "gray.200",
        },
      }),
    },
    Menu: {
      baseStyle: (props: any) => ({
        list: {
          bg: props.colorMode === "dark" ? "#242428" : "white",
          borderColor: props.colorMode === "dark"
            ? "rgba(255, 255, 255, 0.10)"
            : "gray.200",
        },
        item: {
          bg: "transparent",
          _hover: { bg: props.colorMode === "dark" ? "#29292E" : "gray.50" },
          _focus: { bg: props.colorMode === "dark" ? "#29292E" : "gray.50" },
        },
      }),
    },
    Tooltip: {
      baseStyle: (props: any) => ({
        bg: props.colorMode === "dark" ? "#29292E" : "gray.700",
        color: props.colorMode === "dark" ? "#F5F5F6" : "white",
        borderWidth: props.colorMode === "dark" ? "1px" : 0,
        borderColor: "rgba(255, 255, 255, 0.08)",
      }),
    },
  },
});

export default theme;
