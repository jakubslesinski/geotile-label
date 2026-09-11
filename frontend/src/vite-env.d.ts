/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "lite" ukrywa zaawansowane zakładki (Analiza datasetu, Trening, Wyniki). */
  readonly VITE_GEOTILE_EDITION?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
