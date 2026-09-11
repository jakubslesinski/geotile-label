import React from "react";
import ReactDOM from "react-dom/client";
import { ChakraProvider, ColorModeScript } from "@chakra-ui/react";
import { BrowserRouter } from "react-router-dom";
import theme from "./theme/theme";
import App from "./App";
import StartupView from "./views/StartupView";
import DesktopCloseGuard from "./components/common/DesktopCloseGuard";
import "./i18n";

const root = ReactDOM.createRoot(document.getElementById("root")!);

function renderApp() {
  root.render(
    <React.StrictMode>
      <ColorModeScript initialColorMode={theme.config.initialColorMode} />
      <ChakraProvider theme={theme}>
        <DesktopCloseGuard>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </DesktopCloseGuard>
      </ChakraProvider>
    </React.StrictMode>
  );
}

function renderStartup() {
  root.render(
    <React.StrictMode>
      <ColorModeScript initialColorMode={theme.config.initialColorMode} />
      <ChakraProvider theme={theme}>
        <DesktopCloseGuard>
          <StartupView onReady={renderApp} />
        </DesktopCloseGuard>
      </ChakraProvider>
    </React.StrictMode>
  );
}

renderStartup();
