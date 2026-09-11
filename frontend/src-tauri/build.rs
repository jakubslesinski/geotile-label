fn main() {
    println!("cargo:rerun-if-changed=tauri.conf.json");
    println!("cargo:rerun-if-changed=icons/icon.ico");
    println!("cargo:rerun-if-changed=icons/icon.png");
    println!("cargo:rerun-if-env-changed=GEOTILE_BUILD_VARIANT");
    let variant = std::env::var("GEOTILE_BUILD_VARIANT").unwrap_or_else(|_| "yolo".to_string());
    println!("cargo:rustc-env=GEOTILE_BUILD_VARIANT={variant}");
    tauri_build::build();
}
