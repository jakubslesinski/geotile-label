mod process_lifecycle;

use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tauri::{
    path::BaseDirectory, Manager, State, WebviewUrl, WebviewWindowBuilder, WindowEvent,
};
use uuid::Uuid;
use zip::{write::FileOptions, CompressionMethod, ZipWriter};

use process_lifecycle::{
    runtime_blockers, sweep_orphaned_backends, terminate_process_tree_if_same, ProcessSession,
};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;

// --- Job Object: gwarancja, ze backend nie przezyje aplikacji (DESIGN_DECISIONS.md, performance-audit E3) ---
//
// `shutdown()` obsluguje czysta sciezke przez sprawdzone PID + czas utworzenia procesu.
// Gdy proces aplikacji ginie twardo — crash, "Zakoncz zadanie" w Menedzerze, kill z konsoli —
// zaden nasz kod sie nie wykona, a backend zostaje: audyt 2026-08-27 znalazl sierote trzymajaca
// 656 MB RAM i port, ktora przezyla nawet czyste zamkniecie pozniejszej sesji.
//
// Job Object rozwiazuje to na poziomie JADRA: dziecko przypisane do zadania z flaga
// KILL_ON_JOB_CLOSE ginie, gdy zamknie sie ostatni uchwyt zadania — a uchwyt trzymamy my,
// wiec smierc naszego procesu (z dowolnej przyczyny) zamyka go automatycznie. To jedyny
// mechanizm dzialajacy BEZ wspolpracy procesu nadrzednego.
#[cfg(target_os = "windows")]
mod backend_job {
    use std::os::windows::io::AsRawHandle;
    use std::process::Child;
    use std::sync::OnceLock;
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    /// Uchwyt zadania zyje tak dlugo jak proces aplikacji. Celowo NIE implementujemy `Drop`
    /// zamykajacego uchwyt: zamkniecie go ubiloby backend, a chcemy dokladnie odwrotnej
    /// kolejnosci — uchwyt ma zniknac dopiero razem z procesem, i wtedy zabrac backend.
    pub struct JobObject(HANDLE);

    // HANDLE to surowy wskaznik, wiec Rust nie uzna go za Send/Sync sam z siebie. Uchwyt
    // zadania jest jednak bezpieczny do wspoldzielenia: uzywamy go wylacznie do
    // `AssignProcessToJobObject`, ktore jest thread-safe.
    unsafe impl Send for JobObject {}
    unsafe impl Sync for JobObject {}

    static JOB: OnceLock<Option<JobObject>> = OnceLock::new();

    fn create() -> Option<JobObject> {
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() {
                return None;
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let ok = SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                std::ptr::addr_of!(info).cast(),
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            if ok == 0 {
                CloseHandle(handle);
                return None;
            }
            Some(JobObject(handle))
        }
    }

    /// Przypisz backend do zadania. Zwraca `false`, gdy sie nie udalo — wolajacy loguje
    /// powod i idzie dalej: brak Job Objectu pogarsza sprzatanie, ale nie moze zablokowac
    /// startu aplikacji (inwariant "bez cichej degradacji" — powod trafia do logu).
    pub fn assign(child: &Child) -> bool {
        let Some(job) = JOB.get_or_init(create) else {
            return false;
        };
        unsafe { AssignProcessToJobObject(job.0, child.as_raw_handle() as HANDLE) != 0 }
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use std::{
            os::windows::process::CommandExt,
            process::{Command, Stdio},
            thread,
            time::{Duration, Instant},
        };

        const CREATE_NO_WINDOW: u32 = 0x08000000;

        #[test]
        fn closing_job_handle_terminates_assigned_process() {
            let job = create().expect("Windows Job Object should be available");
            let mut child = Command::new("powershell")
                .args([
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "Start-Sleep -Seconds 30",
                ])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .creation_flags(CREATE_NO_WINDOW)
                .spawn()
                .expect("test process should start");

            let assigned = unsafe {
                AssignProcessToJobObject(job.0, child.as_raw_handle() as HANDLE) != 0
            };
            if !assigned {
                let _ = child.kill();
                let _ = child.wait();
                panic!("test process could not be assigned to Job Object");
            }

            unsafe { CloseHandle(job.0) };
            let deadline = Instant::now() + Duration::from_secs(5);
            let exited = loop {
                match child.try_wait() {
                    Ok(Some(_)) => break true,
                    Ok(None) if Instant::now() < deadline => {
                        thread::sleep(Duration::from_millis(25));
                    }
                    _ => break false,
                }
            };
            if !exited {
                let _ = child.kill();
                let _ = child.wait();
            }
            assert!(exited, "closing a KILL_ON_JOB_CLOSE job must kill its process");
        }
    }
}

#[derive(Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
#[serde(default)]
struct BackendCapabilities {
    desktop: bool,
    build_variant: String,
    scene_package_graph_v2: bool,
    feature_flags: BTreeMap<String, bool>,
    yolo: bool,
    rasterio: bool,
    torch: Option<String>,
    ultralytics: Option<String>,
    cuda_available: bool,
    device: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct BackendInfo {
    base_url: String,
    token: String,
    capabilities: BackendCapabilities,
}

#[derive(Clone)]
struct BackendPaths {
    app_data_dir: PathBuf,
    data_dir: PathBuf,
    logs_dir: PathBuf,
    runtime_dir: PathBuf,
    process_sessions_dir: PathBuf,
    /// Base training weights and prediction models. Passed explicitly as MODELS_ROOT,
    /// because the backend would otherwise fall back to a path next to its own sources
    /// — inside the installation directory, which is read-only for a standard user and
    /// wiped on upgrade.
    models_dir: PathBuf,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct DiagnosticsInfo {
    app_version: String,
    os: String,
    arch: String,
    app_data_dir: String,
    data_dir: String,
    logs_dir: String,
    runtime_dir: String,
    backend_running: bool,
    backend_base_url: Option<String>,
    capabilities: BackendCapabilities,
    last_backend_error: Option<String>,
}

struct BackendState {
    info: Mutex<Result<BackendInfo, String>>,
    child: Mutex<Option<Child>>,
    paths: BackendPaths,
    process_session: ProcessSession,
    last_error: Mutex<Option<String>>,
}

impl BackendState {
    fn shutdown(&self) {
        if let Ok(mut child_guard) = self.child.lock() {
            if let Some(mut child) = child_guard.take() {
                // `kill()` konczy tylko bezposredni proces. Backend (python/uvicorn, a przy
                // pakiecie CUDA takze torch/workery) moze miec procesy potomne, ktore dalej
                // trzymaja pliki env — wiec na Windows ubijamy cale drzewo, potem czekamy.
                if let Some(identity) = self.process_session.backend_identity() {
                    log_process_messages(
                        &self.paths,
                        &terminate_process_tree_if_same(&identity).logs,
                    );
                }
                let _ = child.kill();
                let _ = child.wait();
            }
        }
        if let Err(err) = self.process_session.clear_backend() {
            let _ = write_app_log(
                &self.paths,
                &format!("process_session clear_backend_failed error={err}"),
            );
        }
    }

    fn finish(&self) {
        self.shutdown();
        self.process_session.finish();
    }
}

fn log_process_messages(paths: &BackendPaths, messages: &[String]) {
    for message in messages {
        let _ = write_app_log(paths, message);
    }
}

fn sweep_stale_processes(paths: &BackendPaths) {
    let report = sweep_orphaned_backends(&paths.runtime_dir, &paths.process_sessions_dir);
    log_process_messages(paths, &report.logs);
    let _ = write_app_log(
        paths,
        &format!(
            "process_sweep summary terminated={} preserved={} removed_manifests={}",
            report.terminated, report.preserved, report.removed_manifests
        ),
    );
}

/// Destructive runtime operations are allowed only when no process uses the target.
/// Unknown or non-canonical paths are blockers as well: ambiguity must never become a kill.
fn ensure_runtime_not_in_use(dir: &Path, paths: &BackendPaths) -> Result<(), String> {
    sweep_stale_processes(paths);
    // TerminateProcess i zwalnianie mapowanych DLL-i są asynchroniczne. Krótki polling
    // zapobiega fałszywej blokadzie tuż po poprawnym shutdown własnego backendu.
    let mut blockers = runtime_blockers(dir);
    for _ in 0..20 {
        if blockers.is_empty() {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(50));
        blockers = runtime_blockers(dir);
    }
    if blockers.is_empty() {
        return Ok(());
    }
    for blocker in &blockers {
        let _ = write_app_log(
            paths,
            &format!(
                "runtime_mutation blocked pid={} path={} reason={}",
                blocker.pid, blocker.executable, blocker.reason
            ),
        );
    }
    Err(format!(
        "Runtime {} is used by another GeoTile Label instance or its ownership is ambiguous \
         (PID: {}). Close the other instance and retry; no process was terminated.",
        dir.display(),
        blockers
            .iter()
            .map(|blocker| blocker.pid.to_string())
            .collect::<Vec<_>>()
            .join(", ")
    ))
}

/// Usuwa katalog, ponawiajac przy chwilowej blokadzie. Po zamknieciu procesu Windows
/// zwalnia zmapowane sekcje obrazu (DLL/EXE) asynchronicznie, wiec `remove_dir_all`
/// tuz po `shutdown()` potrafi zwrocic "Odmowa dostepu (os error 5)" mimo ze proces
/// juz nie zyje. Teardown kontekstu CUDA + duze DLL-e potrafia zwalniac sie wolniej,
/// wiec ponawiamy z backoffem do ~10 s, az blokady znikna.
// Uwaga do przyszlych zmian: NIE dokladac tu zdejmowania atrybutu read-only. Sprawdzone
// empirycznie na toolchainie tego projektu (rustc 1.96): `fs::remove_dir_all` sam radzi
// sobie z plikami tylko-do-odczytu — drzewo z takim plikiem usuwa sie bez bledu. Skoro
// read-only odpada, "Odmowa dostepu (os error 5)" oznacza tu OTWARTY UCHWYT, czyli zywy
// proces trzymajacy plik — i tym zajmuje sie `replace_runtime_env_dir`.
fn remove_dir_all_retry(path: &Path) -> std::io::Result<()> {
    let mut last_err = None;
    for attempt in 0..40 {
        if !path.exists() {
            return Ok(());
        }
        match fs::remove_dir_all(path) {
            Ok(()) => return Ok(()),
            Err(err) => {
                last_err = Some(err);
                // Za pierwszym razem nie spimy — czesto handle sa juz zwolnione.
                if attempt < 39 {
                    thread::sleep(Duration::from_millis(250));
                }
            }
        }
    }
    if !path.exists() {
        return Ok(());
    }
    Err(last_err.unwrap_or_else(|| std::io::Error::other("remove_dir_all_retry: unknown error")))
}

#[tauri::command]
fn get_backend_info(
    app: tauri::AppHandle,
    state: State<BackendState>,
) -> Result<BackendInfo, String> {
    ensure_backend(&app, &state)
}

#[tauri::command]
fn restart_backend(
    app: tauri::AppHandle,
    state: State<BackendState>,
) -> Result<BackendInfo, String> {
    restart_backend_inner(&app, &state)
}

#[tauri::command]
fn get_app_diagnostics(
    app: tauri::AppHandle,
    state: State<BackendState>,
) -> DiagnosticsInfo {
    build_diagnostics(&app, &state)
}

#[tauri::command]
fn open_logs_dir(state: State<BackendState>) -> Result<(), String> {
    fs::create_dir_all(&state.paths.logs_dir)
        .map_err(|err| format!("Cannot create logs dir: {err}"))?;
    Command::new("explorer")
        .arg(&state.paths.logs_dir)
        .spawn()
        .map_err(|err| format!("Cannot open logs folder: {err}"))?;
    Ok(())
}

#[tauri::command]
fn open_path_in_file_manager(path: String) -> Result<(), String> {
    let requested_path = PathBuf::from(path);
    let resolved_path = requested_path
        .canonicalize()
        .map_err(|err| format!("Cannot resolve path {}: {err}", requested_path.display()))?;
    if !resolved_path.exists() {
        return Err(format!("Path does not exist: {}", resolved_path.display()));
    }
    Command::new("explorer")
        .arg(&resolved_path)
        .spawn()
        .map_err(|err| format!("Cannot open path {}: {err}", resolved_path.display()))?;
    Ok(())
}

#[tauri::command]
fn export_diagnostics_zip(
    app: tauri::AppHandle,
    state: State<BackendState>,
) -> Result<String, String> {
    let path = export_diagnostics(&app, &state)?;
    Ok(path.display().to_string())
}

#[tauri::command]
fn clear_runtime_cache(
    app: tauri::AppHandle,
    state: State<BackendState>,
) -> Result<(), String> {
    write_app_log(&state.paths, "Clearing runtime and tile cache")?;
    state.shutdown();
    if let Err(err) = ensure_runtime_not_in_use(&state.paths.runtime_dir, &state.paths) {
        let _ = restart_backend_inner(&app, &state);
        return Err(err);
    }
    if state.paths.runtime_dir.exists() {
        remove_dir_all_retry(&state.paths.runtime_dir)
            .map_err(|err| format!("Cannot remove runtime cache: {err}"))?;
    }
    remove_named_dirs(&state.paths.data_dir, "geo_tile_cache")?;
    restart_backend_inner(&app, &state).map(|_| ())
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct CudaPackStatus {
    installed: bool,
    usable: bool,
    /// Wynik sondy („12.6 True”) albo powód, dla którego pakiet jest nieużywalny.
    detail: String,
    path: String,
}

#[tauri::command]
fn get_cuda_pack_status(state: State<BackendState>) -> CudaPackStatus {
    let env_dir = cuda_runtime_dir(&state.paths);
    let path = env_dir.display().to_string();
    if !env_dir.exists() {
        return CudaPackStatus {
            installed: false,
            usable: false,
            detail: String::new(),
            path,
        };
    }
    match cuda_runtime_probe(&env_dir) {
        Ok(info) => CudaPackStatus {
            installed: true,
            usable: true,
            detail: info,
            path,
        },
        Err(reason) => CudaPackStatus {
            installed: true,
            usable: false,
            detail: reason,
            path,
        },
    }
}

/// Instaluje ręcznie wskazany pakiet CUDA.
///
/// Pakiet jest rozpakowywany obok runtime bazowego, a nie zamiast niego, i zostaje
/// przyjęty dopiero po przejściu sondy. Nieudana instalacja sprząta po sobie i
/// zostawia aplikację na działającym runtime CPU.
#[tauri::command]
fn install_cuda_pack(
    app: tauri::AppHandle,
    state: State<BackendState>,
    archive_paths: Vec<String>,
) -> Result<CudaPackStatus, String> {
    if archive_paths.is_empty() {
        return Err("Nie wskazano pliku pakietu".to_string());
    }
    let selected: Vec<PathBuf> = archive_paths.into_iter().map(PathBuf::from).collect();
    for part in &selected {
        if !part.is_file() {
            return Err(format!("Plik nie istnieje: {}", part.display()));
        }
    }

    // The pack folder holds two independent things: the CUDA runtime (possibly split
    // into parts) and the base training weights. They are separate archives so that
    // producing the weights does not mean repacking a ~3 GB runtime — routing by name
    // lets the user select everything at once and install it in one action.
    let (weight_archives, mut parts): (Vec<PathBuf>, Vec<PathBuf>) =
        selected.into_iter().partition(|path| {
            path.file_name()
                .and_then(|name| name.to_str())
                .map(|name| name.to_ascii_lowercase().contains(BASE_MODELS_ARCHIVE_MARKER))
                .unwrap_or(false)
        });
    parts.sort();

    let mut installed_weights = 0usize;
    for archive in &weight_archives {
        installed_weights += install_base_models_archive(&state.paths, archive)?;
    }

    if parts.is_empty() {
        // Weights only — a legitimate selection, and no reason to disturb the runtime.
        let env_dir = cuda_runtime_dir(&state.paths);
        let _ = write_app_log(
            &state.paths,
            &format!("Base weights installed ({installed_weights} file(s)), runtime untouched"),
        );
        return Ok(match cuda_runtime_probe(&env_dir) {
            Ok(info) => CudaPackStatus {
                installed: true,
                usable: true,
                detail: info,
                path: env_dir.display().to_string(),
            },
            Err(reason) => CudaPackStatus {
                installed: env_dir.exists(),
                usable: false,
                detail: format!("Zainstalowano wagi bazowe ({installed_weights}). {reason}"),
                path: env_dir.display().to_string(),
            },
        });
    }

    let env_dir = cuda_runtime_dir(&state.paths);
    write_app_log(
        &state.paths,
        &format!("Installing CUDA runtime pack from {} part(s)", parts.len()),
    )?;

    // Backend dziala z tego env (Windows blokuje zaladowany python.exe/DLL), wiec przed
    // podmiana trzeba go zatrzymac — inaczej remove_dir_all zwraca "Odmowa dostepu (os 5)".
    if env_dir.exists() {
        state.shutdown();
        if let Err(err) = ensure_runtime_not_in_use(&env_dir, &state.paths) {
            let _ = restart_backend_inner(&app, &state);
            return Err(err);
        }
        remove_dir_all_retry(&env_dir)
            .map_err(|err| format!("Nie można usunąć poprzedniego pakietu: {err}"))?;
    }
    fs::create_dir_all(&env_dir)
        .map_err(|err| format!("Nie można utworzyć katalogu pakietu: {err}"))?;

    if let Err(err) = extract_tar_gz_parts(&parts, &env_dir) {
        let _ = fs::remove_dir_all(&env_dir);
        let _ = restart_backend_inner(&app, &state); // wznow backend (CPU) po nieudanej podmianie
        return Err(format!("Nie można rozpakować pakietu: {err}"));
    }

    // conda-unpack tylko relokuje sciezki prefiksu — NIE jest testem uzywalnosci pakietu.
    // O akceptacji decyduje sonda (torch+CUDA importuja sie i dzialaja). Gdy conda-unpack
    // padnie, ale sonda przejdzie, pakiet jest sprawny (krytyczne paczki nie zaleza od
    // przepisanego prefiksu, a sciezki geo ustawia configure_backend_geospatial_env).
    // Logujemy ostrzezenie zamiast falszywie odrzucac dzialajacy pakiet.
    if let Err(err) = run_conda_unpack_if_needed(&env_dir, &state.paths.logs_dir) {
        let _ = write_app_log(
            &state.paths,
            &format!("conda-unpack warning (kontynuuje, o akceptacji decyduje sonda): {err}"),
        );
    }

    match cuda_runtime_probe(&env_dir) {
        Ok(info) => {
            let _ = write_app_log(&state.paths, &format!("CUDA runtime pack installed ({info})"));
            // Backend zostal zatrzymany przed podmiana — wznow go na nowym pakiecie.
            let _ = restart_backend_inner(&app, &state);
            Ok(CudaPackStatus {
                installed: true,
                usable: true,
                detail: info,
                path: env_dir.display().to_string(),
            })
        }
        Err(reason) => {
            let _ = fs::remove_dir_all(&env_dir);
            let _ = write_app_log(
                &state.paths,
                &format!("CUDA runtime pack rejected: {reason}"),
            );
            // Backend zostal zatrzymany przed podmiana — wznow na CPU, by komunikat byl prawdziwy.
            let _ = restart_backend_inner(&app, &state);
            Err(format!(
                "Pakiet został odrzucony i usunięty: {reason}. Aplikacja nadal działa na CPU."
            ))
        }
    }
}

/// Marks an archive in the pack folder as base weights rather than a runtime part.
const BASE_MODELS_ARCHIVE_MARKER: &str = "base-models";

/// Unpack base training weights into MODELS_ROOT/base and report how many landed.
///
/// Extraction goes to a staging directory first: a half-extracted archive must not be
/// mistaken for a prepared weights directory, because the backend decides an
/// architecture is available purely by the file being present.
fn install_base_models_archive(paths: &BackendPaths, archive: &Path) -> Result<usize, String> {
    let target = paths.models_dir.join("base");
    let staging = paths.models_dir.join(".base-incoming");

    if staging.exists() {
        let _ = fs::remove_dir_all(&staging);
    }
    fs::create_dir_all(&staging)
        .map_err(|err| format!("Nie można utworzyć katalogu tymczasowego wag: {err}"))?;

    if let Err(err) = extract_tar_gz_parts(&[archive.to_path_buf()], &staging) {
        let _ = fs::remove_dir_all(&staging);
        return Err(format!("Nie można rozpakować wag bazowych: {err}"));
    }

    fs::create_dir_all(&target)
        .map_err(|err| format!("Nie można utworzyć katalogu wag: {err}"))?;

    // The archive may carry the files at its root or inside a base/ folder; accept both.
    let mut source = staging.clone();
    if staging.join("base").is_dir() {
        source = staging.join("base");
    }

    let mut moved = 0usize;
    let entries = fs::read_dir(&source)
        .map_err(|err| format!("Nie można odczytać rozpakowanych wag: {err}"))?;
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let name = match path.file_name() {
            Some(value) => value.to_owned(),
            None => continue,
        };
        let destination = target.join(&name);
        // Same volume in practice, but a rename across devices fails — fall back to copy.
        if fs::rename(&path, &destination).is_err() {
            fs::copy(&path, &destination)
                .map_err(|err| format!("Nie można zapisać {}: {err}", name.to_string_lossy()))?;
        }
        moved += 1;
    }

    let _ = fs::remove_dir_all(&staging);

    if moved == 0 {
        return Err(format!(
            "Archiwum {} nie zawiera plików wag",
            archive.display()
        ));
    }
    Ok(moved)
}

#[tauri::command]
fn remove_cuda_pack(app: tauri::AppHandle, state: State<BackendState>) -> Result<(), String> {
    let env_dir = cuda_runtime_dir(&state.paths);
    if env_dir.exists() {
        // Backend dziala z tego env — najpierw go zatrzymaj (Windows blokuje zaladowane
        // pliki: "Odmowa dostepu / os error 5"), usun, a potem wznow na runtime CPU.
        state.shutdown();
        if let Err(err) = ensure_runtime_not_in_use(&env_dir, &state.paths) {
            let _ = restart_backend_inner(&app, &state);
            return Err(err);
        }
        remove_dir_all_retry(&env_dir)
            .map_err(|err| format!("Nie można usunąć pakietu CUDA: {err}"))?;
        write_app_log(&state.paths, "CUDA runtime pack removed")?;
        restart_backend_inner(&app, &state).map(|_| ())?;
    }
    Ok(())
}

const HELP_WINDOW_LABEL: &str = "help";
const HELP_WIDTH: f64 = 1180.0;
const HELP_HEIGHT: f64 = 900.0;
const HELP_MIN_WIDTH: f64 = 720.0;
const HELP_MIN_HEIGHT: f64 = 520.0;

/// Keeps the caller inside the bundled documentation. The value reaches us from the
/// renderer, so a traversal or an absolute URL must not become a window pointing
/// anywhere else.
fn normalize_help_path(requested: Option<&str>) -> Result<String, String> {
    let raw = requested
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or("index.html");

    let rejected = raw.contains("..")
        || raw.starts_with('/')
        || raw.starts_with('\\')
        || raw.contains("://")
        || raw.contains(':');

    if rejected {
        return Err(format!("Nieprawidłowy adres strony pomocy: {raw}"));
    }

    Ok(format!("help/{raw}"))
}

/// In development the pages come from the Vite dev server, so there is nothing to
/// verify up front. In a build the documentation is embedded, and a missing entry
/// point means a damaged installation — better a readable message than a blank window.
fn ensure_help_assets(app: &tauri::AppHandle) -> Result<(), String> {
    if tauri::is_dev() {
        return Ok(());
    }

    let resolver = app.asset_resolver();
    let found = resolver.get("/help/index.html".to_string()).is_some()
        || resolver.get("help/index.html".to_string()).is_some();

    if found {
        Ok(())
    } else {
        Err(
            "Nie znaleziono dokumentacji offline w tej instalacji. Zainstaluj aplikację \
             ponownie albo otwórz dokumentację z paczki dostarczonej przez zespół."
                .to_string(),
        )
    }
}

/// Komenda MUSI byc asynchroniczna. Tworzenie okna z komendy synchronicznej blokuje
/// na Windows watek IPC, ktory czeka na glowna petle zdarzen, a ta czeka na zakonczenie
/// komendy — okno powstaje, ale nigdy sie nie maluje i przestaje reagowac razem z
/// cala aplikacja. Nie zamieniaj tego z powrotem na `fn`.
#[tauri::command]
async fn open_help_window(app: tauri::AppHandle, path: Option<String>) -> Result<(), String> {
    let target = normalize_help_path(path.as_deref())?;

    // Second click focuses the window that is already open instead of stacking copies.
    if let Some(existing) = app.get_webview_window(HELP_WINDOW_LABEL) {
        let location = serde_json::to_string(&format!("/{target}"))
            .map_err(|err| format!("Nie można przygotować adresu pomocy: {err}"))?;
        let _ = existing.eval(format!("window.location.replace({location})"));
        let _ = existing.unminimize();
        let _ = existing.show();
        existing
            .set_focus()
            .map_err(|err| format!("Nie można aktywować okna pomocy: {err}"))?;
        return Ok(());
    }

    ensure_help_assets(&app)?;

    WebviewWindowBuilder::new(&app, HELP_WINDOW_LABEL, WebviewUrl::App(target.into()))
        .title("GeoTile Label - Pomoc")
        .inner_size(HELP_WIDTH, HELP_HEIGHT)
        .min_inner_size(HELP_MIN_WIDTH, HELP_MIN_HEIGHT)
        .resizable(true)
        // Zmaksymalizowane, a nie fullscreen: fullscreen chowa pasek tytulu razem
        // z krzyzykiem, a okno pomocy ma sie zamykac tak samo jak kazde inne.
        // Rozmiar z `inner_size` zostaje jako ten, do ktorego wraca przywrocenie.
        .maximized(true)
        .build()
        .map_err(|err| format!("Nie można otworzyć okna pomocy: {err}"))?;

    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            get_backend_info,
            get_app_diagnostics,
            open_logs_dir,
            open_path_in_file_manager,
            export_diagnostics_zip,
            restart_backend,
            clear_runtime_cache,
            get_cuda_pack_status,
            install_cuda_pack,
            remove_cuda_pack,
            open_help_window,
            quit_app
        ])
        .setup(|app| {
            let paths = resolve_backend_paths(app.handle())?;
            fs::create_dir_all(&paths.data_dir)
                .map_err(|err| format!("Cannot create data dir: {err}"))?;
            fs::create_dir_all(&paths.logs_dir)
                .map_err(|err| format!("Cannot create logs dir: {err}"))?;
            write_app_log(&paths, "GeoTile Label desktop window initialized")?;
            // Rejestrujemy bieżącego właściciela PRZED zamiataniem. Dzięki temu druga
            // instancja widzi pełne drzewo tej sesji jako żywe i nie może go usunąć.
            let process_session = ProcessSession::start(&paths.process_sessions_dir)?;
            // Przed podniesieniem wlasnego backendu sprzatamy sieroty z poprzednich sesji.
            // Musi byc TUTAJ, przed pierwszym `ensure_backend`, zeby stary proces nie
            // trzymal juz pamieci ani portu w chwili startu nowego.
            sweep_stale_processes(&paths);
            app.manage(BackendState {
                info: Mutex::new(Err("Backend not started yet".to_string())),
                child: Mutex::new(None),
                paths,
                process_session,
                last_error: Mutex::new(None),
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            // The documentation lives in a second window. Closing it must not look
            // like quitting the application, so the backend shutdown stays bound to
            // the main window only.
            // Handler leci na glownym watku petli zdarzen. Nie wolno tu wolac API okna
            // (inner_size, scale_factor, get_webview_window) — kazde z nich czeka na te
            // sama petle i zakleszcza aplikacje. Okno pomocy zamyka sie samo, domyslnie.
            if window.label() == HELP_WINDOW_LABEL {
                return;
            }

            if matches!(event, WindowEvent::Destroyed) {
                // Nie wolno tu odpytywac menedzera okien: zdarzenie leci w trakcie
                // rozbierania okna, a siegniecie po inne okno potrafi zakleszczyc
                // proces. Okno pomocy zamyka `quit_app` przed `app.exit`.
                if let Some(state) = window.try_state::<BackendState>() {
                    state.finish();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running GeoTile Label Desktop");
}

#[tauri::command]
fn quit_app(app: tauri::AppHandle, state: State<'_, BackendState>) {
    // Okno pomocy zamykamy jawnie i przed `app.exit`, zeby zawieszony webview
    // dokumentacji nie mogl zablokowac zamkniecia calej aplikacji. Uzytkownik
    // musialby wtedy ubic proces menedzerem zadan.
    if let Some(help) = app.get_webview_window(HELP_WINDOW_LABEL) {
        let _ = help.destroy();
    }
    state.finish();
    app.exit(0);
}

fn ensure_backend(
    app: &tauri::AppHandle,
    state: &BackendState,
) -> Result<BackendInfo, String> {
    if let Some(info) = current_running_backend(state) {
        return Ok(info);
    }
    restart_backend_inner(app, state)
}

fn current_running_backend(state: &BackendState) -> Option<BackendInfo> {
    let info = state.info.lock().ok()?.clone().ok()?;
    let mut child_guard = state.child.lock().ok()?;
    let child = child_guard.as_mut()?;
    match child.try_wait() {
        Ok(None) => Some(info),
        Ok(Some(_)) | Err(_) => {
            *child_guard = None;
            let _ = state.process_session.clear_backend();
            None
        }
    }
}

fn restart_backend_inner(
    app: &tauri::AppHandle,
    state: &BackendState,
) -> Result<BackendInfo, String> {
    state.shutdown();
    rotate_logs(&state.paths).map_err(|err| format!("Cannot rotate logs: {err}"))?;
    match launch_backend(app, &state.paths, &state.process_session) {
        Ok((info, child)) => {
            if let Ok(mut guard) = state.child.lock() {
                *guard = Some(child);
            }
            if let Ok(mut guard) = state.info.lock() {
                *guard = Ok(info.clone());
            }
            if let Ok(mut guard) = state.last_error.lock() {
                *guard = None;
            }
            write_app_log(&state.paths, &format!("Backend started at {}", info.base_url))?;
            Ok(info)
        }
        Err(error) => {
            if let Ok(mut guard) = state.info.lock() {
                *guard = Err(error.clone());
            }
            if let Ok(mut guard) = state.last_error.lock() {
                *guard = Some(error.clone());
            }
            let _ = write_app_log(&state.paths, &format!("Backend failed: {error}"));
            Err(error)
        }
    }
}

fn launch_backend(
    app: &tauri::AppHandle,
    paths: &BackendPaths,
    process_session: &ProcessSession,
) -> Result<(BackendInfo, Child), String> {
    fs::create_dir_all(&paths.data_dir).map_err(|err| format!("Cannot create data dir: {err}"))?;
    fs::create_dir_all(&paths.logs_dir).map_err(|err| format!("Cannot create logs dir: {err}"))?;
    fs::create_dir_all(&paths.models_dir)
        .map_err(|err| format!("Cannot create models dir: {err}"))?;

    let backend_dir = resolve_backend_dir(app)?;
    let python_path = resolve_python_path(app, paths)?;
    let port = find_free_port()?;
    let token = Uuid::new_v4().to_string();
    let base_url = format!("http://127.0.0.1:{port}");
    let build_variant = desktop_build_variant();
    let yolo_enabled = build_variant == "yolo";
    let scene_package_graph_v2 = scene_package_graph_v2_enabled();
    let json_index_v2 = release_feature_flag(
        "GEOTILE_JSON_INDEX_V2",
        option_env!("GEOTILE_JSON_INDEX_V2"),
        false,
    );
    let json_index_v2_delta = release_feature_flag(
        "GEOTILE_JSON_INDEX_V2_DELTA",
        option_env!("GEOTILE_JSON_INDEX_V2_DELTA"),
        true,
    );
    let training_dataset_cache = release_feature_flag(
        "GEOTILE_TRAINING_DATASET_CACHE",
        option_env!("GEOTILE_TRAINING_DATASET_CACHE"),
        false,
    );
    let catalog_snapshot = release_feature_flag(
        "GEOTILE_CATALOG_SNAPSHOT",
        option_env!("GEOTILE_CATALOG_SNAPSHOT"),
        true,
    );
    let scene_import_common_jobs = release_feature_flag(
        "GEOTILE_SCENE_IMPORT_COMMON_JOBS",
        option_env!("GEOTILE_SCENE_IMPORT_COMMON_JOBS"),
        true,
    );

    let stdout_log = open_log_file(&paths.logs_dir.join("backend.stdout.log"))?;
    let stderr_log = open_log_file(&paths.logs_dir.join("backend.stderr.log"))?;

    let mut command = Command::new(&python_path);
    hide_console_window(&mut command);
    command
        .arg("-m")
        .arg("uvicorn")
        .arg("main:app")
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(port.to_string())
        .current_dir(&backend_dir)
        .env("DATA_DIR", &paths.data_dir)
        .env("MODELS_ROOT", &paths.models_dir)
        .env("GEOTILE_DESKTOP", "1")
        .env("GEOTILE_BUILD_VARIANT", &build_variant)
        .env(
            "GEOTILE_SCENE_PACKAGE_GRAPH_V2",
            feature_flag_value(scene_package_graph_v2),
        )
        .env("GEOTILE_JSON_INDEX_V2", feature_flag_value(json_index_v2))
        .env(
            "GEOTILE_JSON_INDEX_V2_DELTA",
            feature_flag_value(json_index_v2_delta),
        )
        .env(
            "GEOTILE_TRAINING_DATASET_CACHE",
            feature_flag_value(training_dataset_cache),
        )
        .env(
            "GEOTILE_CATALOG_SNAPSHOT",
            feature_flag_value(catalog_snapshot),
        )
        .env(
            "GEOTILE_SCENE_IMPORT_COMMON_JOBS",
            feature_flag_value(scene_import_common_jobs),
        )
        .env("GEOTILE_APP_VERSION", app.package_info().version.to_string())
        .env("GEOTILE_ENABLE_YOLO", if yolo_enabled { "1" } else { "0" })
        .env("GEOTILE_AUTH_TOKEN", &token)
        .env("OMP_NUM_THREADS", "1")
        .env("MKL_NUM_THREADS", "1")
        .env("OPENBLAS_NUM_THREADS", "1")
        .env("NUMEXPR_NUM_THREADS", "1")
        .env("KMP_DUPLICATE_LIB_OK", "TRUE")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("PYTHONUNBUFFERED", "1")
        .stdout(Stdio::from(stdout_log))
        .stderr(Stdio::from(stderr_log));

    configure_backend_geospatial_env(&mut command, &python_path, paths)?;

    let mut child = command.spawn().map_err(|err| {
        format!(
            "Cannot start backend. python={}, backend={}, error={err}",
            python_path.display(),
            backend_dir.display()
        )
    })?;

    // Natychmiast po starcie, PRZED czekaniem na zdrowie: gdyby aplikacja zginela w trakcie
    // tych 30 sekund, backend bez przypisania zostalby sierota.
    #[cfg(target_os = "windows")]
    if !backend_job::assign(&child) {
        // Bez cichej degradacji: aplikacja dziala dalej (sprzatanie oprze sie na
        // `shutdown()` + zamiataniu przy starcie), ale powod zostaje w logu.
        let _ = write_app_log(
            paths,
            "WARNING: could not assign the backend to a Job Object; \
             an orphaned backend may survive a hard crash of the app",
        );
    }

    // Manifest musi wskazywać dokładną tożsamość procesu, zanim zacznie on tworzyć
    // workery. Nie kontynuujemy bez tego zabezpieczenia przed recyklingiem PID.
    if let Err(err) = process_session.set_backend(child.id()) {
        if let Some(identity) = process_session.backend_identity() {
            log_process_messages(paths, &terminate_process_tree_if_same(&identity).logs);
        }
        let _ = child.kill();
        let _ = child.wait();
        let _ = process_session.clear_backend();
        return Err(format!("Cannot register backend process identity: {err}"));
    }

    if !wait_for_backend(port, Duration::from_secs(30)) {
        let _ = child.kill();
        let _ = child.wait();
        let _ = process_session.clear_backend();
        return Err(format!(
            "Backend did not become healthy. Check logs in {}",
            paths.logs_dir.display()
        ));
    }
    let capabilities = fetch_backend_capabilities(port, &token)
        .unwrap_or_else(|| fallback_capabilities(&build_variant));

    Ok((
        BackendInfo {
            base_url,
            token,
            capabilities,
        },
        child,
    ))
}

fn configure_backend_geospatial_env(
    command: &mut Command,
    python_path: &Path,
    paths: &BackendPaths,
) -> Result<(), String> {
    let Some(env_root) = python_path.parent() else {
        return Ok(());
    };

    let proj_dir = env_root.join("Library").join("share").join("proj");
    if proj_dir.join("proj.db").exists() {
        command.env("PROJ_DATA", &proj_dir);
        command.env("PROJ_LIB", &proj_dir);
        let _ = write_app_log(
            paths,
            &format!("Using bundled PROJ data: {}", proj_dir.display()),
        );
    }

    let gdal_dir = env_root.join("Library").join("share").join("gdal");
    if gdal_dir.exists() {
        command.env("GDAL_DATA", &gdal_dir);
        let _ = write_app_log(
            paths,
            &format!("Using bundled GDAL data: {}", gdal_dir.display()),
        );
    }

    let gdal_plugin_dir = env_root
        .join("Library")
        .join("lib")
        .join("gdalplugins");
    if gdal_plugin_dir.exists() {
        command.env("GDAL_DRIVER_PATH", &gdal_plugin_dir);
        let _ = write_app_log(
            paths,
            &format!(
                "Using bundled GDAL plugins: {}",
                gdal_plugin_dir.display()
            ),
        );
    }

    let mut path_entries = Vec::new();
    for candidate in [
        env_root.join("Library").join("bin"),
        env_root.join("Scripts"),
        env_root.to_path_buf(),
    ] {
        if candidate.exists() {
            path_entries.push(candidate);
        }
    }
    if let Some(current_path) = std::env::var_os("PATH") {
        path_entries.extend(std::env::split_paths(&current_path));
    }
    if !path_entries.is_empty() {
        let joined_path = std::env::join_paths(path_entries)
            .map_err(|err| format!("Cannot build backend PATH: {err}"))?;
        command.env("PATH", joined_path);
    }

    Ok(())
}

fn desktop_build_variant() -> String {
    // Unified build: the YOLO stack is always bundled. The env override is
    // kept only for local diagnostics of the packaging process.
    std::env::var("GEOTILE_BUILD_VARIANT")
        .ok()
        .or_else(|| option_env!("GEOTILE_BUILD_VARIANT").map(str::to_string))
        .unwrap_or_else(|| "yolo".to_string())
        .to_ascii_lowercase()
}

fn scene_package_graph_v2_enabled() -> bool {
    // The release configuration must be explicit and reproducible. A runtime override is
    // useful for diagnostics; an environment value present while compiling the installer
    // becomes its default. Unset/unknown values deliberately keep the compatibility graph.
    release_feature_flag(
        "GEOTILE_SCENE_PACKAGE_GRAPH_V2",
        option_env!("GEOTILE_SCENE_PACKAGE_GRAPH_V2"),
        false,
    )
}

fn release_feature_flag(name: &str, build_value: Option<&str>, default: bool) -> bool {
    std::env::var(name)
        .ok()
        .or_else(|| build_value.map(str::to_string))
        .map(|value| {
            matches!(
                value.trim().to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            )
        })
        .unwrap_or(default)
}

fn feature_flag_value(enabled: bool) -> &'static str {
    if enabled {
        "1"
    } else {
        "0"
    }
}

fn fallback_capabilities(build_variant: &str) -> BackendCapabilities {
    BackendCapabilities {
        desktop: true,
        build_variant: build_variant.to_string(),
        scene_package_graph_v2: scene_package_graph_v2_enabled(),
        feature_flags: BTreeMap::from([
            (
                "GEOTILE_SCENE_PACKAGE_GRAPH_V2".to_string(),
                scene_package_graph_v2_enabled(),
            ),
            (
                "GEOTILE_JSON_INDEX_V2".to_string(),
                release_feature_flag(
                    "GEOTILE_JSON_INDEX_V2",
                    option_env!("GEOTILE_JSON_INDEX_V2"),
                    false,
                ),
            ),
            (
                "GEOTILE_JSON_INDEX_V2_DELTA".to_string(),
                release_feature_flag(
                    "GEOTILE_JSON_INDEX_V2_DELTA",
                    option_env!("GEOTILE_JSON_INDEX_V2_DELTA"),
                    true,
                ),
            ),
            (
                "GEOTILE_TRAINING_DATASET_CACHE".to_string(),
                release_feature_flag(
                    "GEOTILE_TRAINING_DATASET_CACHE",
                    option_env!("GEOTILE_TRAINING_DATASET_CACHE"),
                    false,
                ),
            ),
            (
                "GEOTILE_CATALOG_SNAPSHOT".to_string(),
                release_feature_flag(
                    "GEOTILE_CATALOG_SNAPSHOT",
                    option_env!("GEOTILE_CATALOG_SNAPSHOT"),
                    true,
                ),
            ),
            (
                "GEOTILE_SCENE_IMPORT_COMMON_JOBS".to_string(),
                release_feature_flag(
                    "GEOTILE_SCENE_IMPORT_COMMON_JOBS",
                    option_env!("GEOTILE_SCENE_IMPORT_COMMON_JOBS"),
                    true,
                ),
            ),
        ]),
        yolo: false,
        rasterio: false,
        torch: None,
        ultralytics: None,
        cuda_available: false,
        device: "cpu".to_string(),
    }
}

fn resolve_backend_paths(app: &tauri::AppHandle) -> Result<BackendPaths, String> {
    let app_data_dir = if let Ok(app_data) = std::env::var("APPDATA") {
        PathBuf::from(app_data).join("GeoTileLabel")
    } else {
        app.path()
            .app_data_dir()
            .map_err(|err| format!("Cannot resolve app data dir: {err}"))?
            .join("GeoTileLabel")
    };
    Ok(BackendPaths {
        data_dir: app_data_dir.join("data"),
        logs_dir: app_data_dir.join("logs"),
        runtime_dir: app_data_dir.join("runtime"),
        process_sessions_dir: app_data_dir.join("process-sessions"),
        models_dir: app_data_dir.join("data").join("models"),
        app_data_dir,
    })
}

fn resolve_backend_dir(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    if cfg!(debug_assertions) {
        let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let repo_backend = manifest_dir.join("..").join("..").join("backend");
        if repo_backend.join("main.py").exists() {
            return Ok(repo_backend.canonicalize().unwrap_or(repo_backend));
        }
    }

    if let Some(resource_backend) =
        resolve_resource_dir(app, &["backend", "resources/backend"], "main.py")
    {
        return Ok(resource_backend);
    }

    Err("Backend resource not found. Run desktop backend preparation first.".to_string())
}

/// Katalog opcjonalnego runtime CUDA, instalowanego z ręcznie wskazanego pakietu.
fn cuda_runtime_dir(paths: &BackendPaths) -> PathBuf {
    paths.runtime_dir.join("backend-env-cuda")
}

/// Czy zainstalowany pakiet CUDA nadaje się do użycia.
///
/// Sprawdzamy **działaniem**, nie metadanymi: sam fakt obecności `python.exe` nic nie
/// mówi o tym, czy torch widzi CUDA. Sonda jest tania (jeden import) i uruchamiana
/// tylko wtedy, gdy katalog w ogóle istnieje.
fn cuda_runtime_probe(env_dir: &Path) -> Result<String, String> {
    let python = env_dir.join("python.exe");
    if !python.exists() {
        return Err("brak python.exe w pakiecie CUDA".to_string());
    }
    let mut command = Command::new(&python);
    hide_console_window(&mut command);
    let output = command
        .arg("-c")
        .arg("import torch; print(torch.version.cuda or '', torch.cuda.is_available())")
        .output()
        .map_err(|err| format!("nie udało się uruchomić sondy: {err}"))?;
    if !output.status.success() {
        return Err(format!(
            "sonda zakończona błędem: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if text.is_empty() || text.starts_with(' ') {
        return Err("torch w pakiecie nie ma wsparcia CUDA".to_string());
    }
    Ok(text)
}

fn resolve_python_path(app: &tauri::AppHandle, paths: &BackendPaths) -> Result<PathBuf, String> {
    if let Ok(path) = std::env::var("GEOTILE_BACKEND_PYTHON") {
        let candidate = PathBuf::from(path);
        if candidate.exists() {
            return Ok(candidate);
        }
    }

    // Opcjonalny pakiet CUDA ma pierwszeństwo przed runtime bazowym, ale tylko gdy
    // faktycznie działa — wadliwy pakiet nie może pozbawić użytkownika aplikacji.
    let cuda_env = cuda_runtime_dir(paths);
    if cuda_env.exists() {
        match cuda_runtime_probe(&cuda_env) {
            Ok(info) => {
                let _ = write_app_log(paths, &format!("Using CUDA runtime pack ({info})"));
                return Ok(cuda_env.join("python.exe"));
            }
            Err(reason) => {
                let _ = write_app_log(
                    paths,
                    &format!("CUDA runtime pack unusable ({reason}); falling back to CPU runtime"),
                );
            }
        }
    }

    if cfg!(debug_assertions) {
        let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let repo_python = manifest_dir
            .join("..")
            .join("..")
            .join("backend")
            .join(".venv")
            .join("Scripts")
            .join("python.exe");
        if repo_python.exists() {
            return Ok(repo_python);
        }
    }

    if let Some(archive_parts) = resolve_runtime_archive_parts(app) {
        let runtime_env = prepare_runtime_env_from_archive(app, &archive_parts, paths)?;
        let python = runtime_env.join("python.exe");
        if python.exists() {
            return Ok(python);
        }
    }

    if let Some(resource_env) =
        resolve_resource_dir(app, &["backend-env", "resources/backend-env"], "python.exe")
    {
        let runtime_env = prepare_runtime_env_from_dir(app, &resource_env, paths)?;
        let python = runtime_env.join("python.exe");
        if python.exists() {
            return Ok(python);
        }
    }

    if cfg!(debug_assertions) {
        return Ok(PathBuf::from("python"));
    }

    Err("Backend Python runtime not found.".to_string())
}

fn resolve_resource_dir(
    app: &tauri::AppHandle,
    relative_paths: &[&str],
    marker_file: &str,
) -> Option<PathBuf> {
    for relative_path in relative_paths {
        if let Ok(candidate) = app.path().resolve(relative_path, BaseDirectory::Resource) {
            if candidate.join(marker_file).exists() {
                return Some(candidate);
            }
        }
    }

    if let Ok(resource_dir) = app.path().resource_dir() {
        for relative_path in relative_paths {
            let candidate = resource_dir.join(relative_path);
            if candidate.join(marker_file).exists() {
                return Some(candidate);
            }
        }
    }

    None
}

fn resolve_resource_file(app: &tauri::AppHandle, relative_paths: &[&str]) -> Option<PathBuf> {
    for relative_path in relative_paths {
        if let Ok(candidate) = app.path().resolve(relative_path, BaseDirectory::Resource) {
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }

    if let Ok(resource_dir) = app.path().resource_dir() {
        for relative_path in relative_paths {
            let candidate = resource_dir.join(relative_path);
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }

    None
}

/// Usuwa stare srodowisko runtime przed podmiana na nowe (aktualizacja wersji aplikacji).
///
/// Incydent 2026-08-27 (diagnostyka z maszyny analityka, aktualizacja do 1.2.6): kasowanie
/// przerwalo sie z "Odmowa dostepu (os error 5)", bo pliki env trzymal OSIEROCONY backend
/// z poprzedniej wersji. Efekt byl trwaly, nie przejsciowy: kasowanie zdazylo usunac
/// `site-packages` (env skurczyl sie do 41 MB) i znacznik `.geotile-version`, ale zostawilo
/// `python.exe`. Przez to warunek `needs_copy` byl odtad ZAWSZE prawdziwy, a kazda kolejna
/// proba trafiala na te sama blokade — aplikacja nie mogla sie sama naprawic.
///
/// P0.4 celowo nie zabija już wszystkich procesów znalezionych pod ścieżką. Po bezpiecznym
/// zamiataniu dokładnie zidentyfikowanych sierot każda żywa lub niejednoznaczna blokada
/// zatrzymuje podmianę runtime, zamiast ryzykować przerwanie pracy innej instancji.
fn replace_runtime_env_dir(runtime_env: &Path, paths: &BackendPaths) -> Result<(), String> {
    if !runtime_env.exists() {
        return Ok(());
    }
    ensure_runtime_not_in_use(runtime_env, paths)?;
    remove_dir_all_retry(runtime_env).map_err(|err| {
        let _ = write_app_log(
            paths,
            &format!("Cannot remove old runtime env at {}: {err}", runtime_env.display()),
        );
        // Komunikat trafia na ekran startowy, wiec musi mowic uzytkownikowi CO zrobic —
        // poprzednia wersja podawala sam kod bledu i nie dalo sie z niej wyjsc.
        format!(
            "Cannot remove old runtime env: {err}. Close GeoTile Label, make sure no python.exe \
             from {} is running, delete that folder manually and start the app again \
             (your projects in the data folder are not affected).",
            runtime_env.display()
        )
    })
}

fn prepare_runtime_env_from_dir(
    app: &tauri::AppHandle,
    resource_env: &Path,
    paths: &BackendPaths,
) -> Result<PathBuf, String> {
    let runtime_env = paths.runtime_dir.join("backend-env");
    let version_marker = runtime_env.join(".geotile-version");
    let package_version = format!("{}-{}", app.package_info().version, desktop_build_variant());

    let needs_copy = !runtime_env.join("python.exe").exists()
        || fs::read_to_string(&version_marker).unwrap_or_default() != package_version;

    if needs_copy {
        replace_runtime_env_dir(&runtime_env, paths)?;
        fs::create_dir_all(&paths.runtime_dir)
            .map_err(|err| format!("Cannot create runtime dir: {err}"))?;
        copy_dir_all(resource_env, &runtime_env)
            .map_err(|err| format!("Cannot copy backend env: {err}"))?;
        fs::write(&version_marker, &package_version)
            .map_err(|err| format!("Cannot write env version marker: {err}"))?;
    }

    // conda-unpack tylko relokuje prefiks — NIE jest warunkiem startu backendu (backend
    // odpala python.exe wprost, a ścieżki geo ustawia configure_backend_geospatial_env, jak
    // w torze CUDA na górze pliku). Porażka — np. plik torcha o ścieżce > MAX_PATH na stacji
    // z długą nazwą użytkownika — nie może blokować uruchomienia; logujemy ostrzeżenie zamiast
    // fatalnie przerywać. Znacznik .geotile-unpacked nie powstaje, więc próba ponowi się przy
    // kolejnym starcie (a build prune'uje głębokie licencje, żeby do tego nie dochodziło).
    if let Err(err) = run_conda_unpack_if_needed(&runtime_env, &paths.logs_dir) {
        let _ = write_app_log(
            paths,
            &format!("conda-unpack warning (kontynuuje mimo błędu): {err}"),
        );
    }
    Ok(runtime_env)
}

fn prepare_runtime_env_from_archive(
    app: &tauri::AppHandle,
    archive_parts: &[PathBuf],
    paths: &BackendPaths,
) -> Result<PathBuf, String> {
    let runtime_env = paths.runtime_dir.join("backend-env");
    let version_marker = runtime_env.join(".geotile-version");
    let package_version = format!("{}-{}", app.package_info().version, desktop_build_variant());

    let needs_extract = !runtime_env.join("python.exe").exists()
        || fs::read_to_string(&version_marker).unwrap_or_default() != package_version;

    if needs_extract {
        replace_runtime_env_dir(&runtime_env, paths)?;
        fs::create_dir_all(&runtime_env)
            .map_err(|err| format!("Cannot create runtime env dir: {err}"))?;
        let _ = write_app_log(
            paths,
            &format!(
                "Extracting backend env archive ({} part(s)): {}",
                archive_parts.len(),
                archive_parts
                    .iter()
                    .map(|part| part
                        .file_name()
                        .map(|name| name.to_string_lossy().into_owned())
                        .unwrap_or_default())
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
        );
        extract_tar_gz_parts(archive_parts, &runtime_env)
            .map_err(|err| format!("Cannot extract backend env archive: {err}"))?;
        fs::write(&version_marker, &package_version)
            .map_err(|err| format!("Cannot write env version marker: {err}"))?;
        let _ = write_app_log(paths, "Backend env archive extracted");
    }

    // conda-unpack tylko relokuje prefiks — NIE jest warunkiem startu backendu (backend
    // odpala python.exe wprost, a ścieżki geo ustawia configure_backend_geospatial_env, jak
    // w torze CUDA na górze pliku). Porażka — np. plik torcha o ścieżce > MAX_PATH na stacji
    // z długą nazwą użytkownika — nie może blokować uruchomienia; logujemy ostrzeżenie zamiast
    // fatalnie przerywać. Znacznik .geotile-unpacked nie powstaje, więc próba ponowi się przy
    // kolejnym starcie (a build prune'uje głębokie licencje, żeby do tego nie dochodziło).
    if let Err(err) = run_conda_unpack_if_needed(&runtime_env, &paths.logs_dir) {
        let _ = write_app_log(
            paths,
            &format!("conda-unpack warning (kontynuuje mimo błędu): {err}"),
        );
    }
    Ok(runtime_env)
}

/// Maksymalna liczba części archiwum runtime, jakiej szukamy w zasobach.
const RUNTIME_ARCHIVE_MAX_PARTS: usize = 64;

/// Znajduje archiwum runtime: albo pojedynczy plik, albo części `.001`, `.002`, …
///
/// Runtime CUDA ma ponad 3 GB, a ani NSIS (`failed creating mmap`), ani WiX
/// (`light.exe`) nie potrafią osadzić pojedynczego zasobu tej wielkości. Build tnie
/// więc archiwum na części; pojedynczy plik pozostaje obsługiwany, bo tak wyglądają
/// starsze i deweloperskie zestawy zasobów.
fn resolve_runtime_archive_parts(app: &tauri::AppHandle) -> Option<Vec<PathBuf>> {
    if let Some(single) =
        resolve_resource_file(app, &["backend-env.tar.gz", "resources/backend-env.tar.gz"])
    {
        return Some(vec![single]);
    }

    let mut parts = Vec::new();
    for index in 1..=RUNTIME_ARCHIVE_MAX_PARTS {
        let name = format!("backend-env.tar.gz.{index:03}");
        let nested = format!("resources/{name}");
        match resolve_resource_file(app, &[name.as_str(), nested.as_str()]) {
            Some(path) => parts.push(path),
            None => break,
        }
    }

    if parts.is_empty() {
        None
    } else {
        Some(parts)
    }
}

/// Rozpakowuje archiwum złożone z jednej lub wielu części.
///
/// Części są łączone **strumieniowo** — nie powstaje sklejony plik pośredni, więc
/// instalacja nie wymaga dodatkowych kilku GB wolnego miejsca ani kroku kopiowania.
fn extract_tar_gz_parts(parts: &[PathBuf], destination: &Path) -> std::io::Result<()> {
    use std::io::Read;

    let (first, rest) = parts
        .split_first()
        .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::NotFound, "no archive parts"))?;

    let mut reader: Box<dyn Read> = Box::new(File::open(first)?);
    for part in rest {
        reader = Box::new(reader.chain(File::open(part)?));
    }

    let decoder = flate2::read::GzDecoder::new(reader);
    let mut archive = tar::Archive::new(decoder);
    archive.unpack(destination)
}

fn run_conda_unpack_if_needed(env_dir: &Path, logs_dir: &Path) -> Result<(), String> {
    let marker = env_dir.join(".geotile-unpacked");
    let unpack = env_dir.join("Scripts").join("conda-unpack.exe");
    if marker.exists() || !unpack.exists() {
        return Ok(());
    }

    let stdout_log = open_log_file(&logs_dir.join("conda-unpack.stdout.log"))?;
    let stderr_log = open_log_file(&logs_dir.join("conda-unpack.stderr.log"))?;
    let mut command = Command::new(&unpack);
    hide_console_window(&mut command);
    let status = command
        .current_dir(env_dir)
        .stdout(Stdio::from(stdout_log))
        .stderr(Stdio::from(stderr_log))
        .status()
        .map_err(|err| format!("Cannot run conda-unpack: {err}"))?;
    if !status.success() {
        return Err("conda-unpack failed. Check logs in app data directory.".to_string());
    }
    fs::write(marker, "ok").map_err(|err| format!("Cannot write unpack marker: {err}"))?;
    Ok(())
}

fn hide_console_window(command: &mut Command) {
    #[cfg(target_os = "windows")]
    command.creation_flags(CREATE_NO_WINDOW);
}

fn build_diagnostics(app: &tauri::AppHandle, state: &BackendState) -> DiagnosticsInfo {
    let info = state.info.lock().ok().and_then(|guard| guard.clone().ok());
    let backend_running = state
        .child
        .lock()
        .ok()
        .and_then(|guard| guard.as_ref().map(|_| true))
        .unwrap_or(false);
    let last_backend_error = state
        .last_error
        .lock()
        .ok()
        .and_then(|guard| guard.clone())
        .or_else(|| state.info.lock().ok().and_then(|guard| guard.clone().err()));

    DiagnosticsInfo {
        app_version: app.package_info().version.to_string(),
        os: std::env::consts::OS.to_string(),
        arch: std::env::consts::ARCH.to_string(),
        app_data_dir: state.paths.app_data_dir.display().to_string(),
        data_dir: state.paths.data_dir.display().to_string(),
        logs_dir: state.paths.logs_dir.display().to_string(),
        runtime_dir: state.paths.runtime_dir.display().to_string(),
        backend_running,
        backend_base_url: info.as_ref().map(|i| i.base_url.clone()),
        capabilities: info
            .map(|i| i.capabilities)
            .unwrap_or_else(|| fallback_capabilities(&desktop_build_variant())),
        last_backend_error,
    }
}

fn export_diagnostics(
    app: &tauri::AppHandle,
    state: &BackendState,
) -> Result<PathBuf, String> {
    fs::create_dir_all(&state.paths.logs_dir)
        .map_err(|err| format!("Cannot create logs dir: {err}"))?;
    let output_path = state
        .paths
        .logs_dir
        .join(format!("GeoTileLabel-diagnostics-{}.zip", timestamp_secs()));
    let file = File::create(&output_path)
        .map_err(|err| format!("Cannot create diagnostics ZIP: {err}"))?;
    let mut zip = ZipWriter::new(file);
    let options = FileOptions::default().compression_method(CompressionMethod::Deflated);

    let diagnostics = build_diagnostics(app, state);
    let diagnostics_json = serde_json::to_vec_pretty(&diagnostics)
        .map_err(|err| format!("Cannot serialize diagnostics: {err}"))?;
    zip.start_file("diagnostics.json", options)
        .map_err(|err| format!("Cannot write diagnostics metadata: {err}"))?;
    zip.write_all(&diagnostics_json)
        .map_err(|err| format!("Cannot write diagnostics metadata: {err}"))?;

    let listing = build_directory_listing(&state.paths.app_data_dir, 3)
        .unwrap_or_else(|err| format!("Cannot build directory listing: {err}"));
    zip.start_file("appdata-listing.txt", options)
        .map_err(|err| format!("Cannot write diagnostics listing: {err}"))?;
    zip.write_all(listing.as_bytes())
        .map_err(|err| format!("Cannot write diagnostics listing: {err}"))?;

    for log_name in [
        "app.log",
        "backend.stdout.log",
        "backend.stderr.log",
        "conda-unpack.stdout.log",
        "conda-unpack.stderr.log",
    ] {
        let path = state.paths.logs_dir.join(log_name);
        if path.exists() {
            add_file_to_zip(&mut zip, &path, &format!("logs/{log_name}"), options)?;
        }
    }

    let project_diagnostics = state
        .info
        .lock()
        .ok()
        .and_then(|guard| guard.clone().ok())
        .and_then(|info| fetch_backend_json(&info, "/api/diagnostics/project-summary"))
        .unwrap_or_else(|| serde_json::json!({
            "schema_name": "geotile_support_project_diagnostics",
            "schema_version": 1,
            "status": "unavailable",
            "reason": "Backend was not available while exporting diagnostics"
        }));
    let project_diagnostics_json = serde_json::to_vec_pretty(&project_diagnostics)
        .map_err(|err| format!("Cannot serialize project diagnostics: {err}"))?;
    zip.start_file("project-diagnostics.json", options)
        .map_err(|err| format!("Cannot write project diagnostics: {err}"))?;
    zip.write_all(&project_diagnostics_json)
        .map_err(|err| format!("Cannot write project diagnostics: {err}"))?;

    zip.finish()
        .map_err(|err| format!("Cannot finish diagnostics ZIP: {err}"))?;
    Ok(output_path)
}

fn add_file_to_zip(
    zip: &mut ZipWriter<File>,
    path: &Path,
    name: &str,
    options: FileOptions,
) -> Result<(), String> {
    let mut file = File::open(path)
        .map_err(|err| format!("Cannot open diagnostics file {}: {err}", path.display()))?;
    zip.start_file(name, options)
        .map_err(|err| format!("Cannot add diagnostics file {name}: {err}"))?;
    std::io::copy(&mut file, zip)
        .map_err(|err| format!("Cannot copy diagnostics file {name}: {err}"))?;
    Ok(())
}

fn build_directory_listing(root: &Path, max_depth: usize) -> std::io::Result<String> {
    let mut out = String::new();
    list_dir_recursive(root, root, 0, max_depth, &mut out)?;
    Ok(out)
}

fn list_dir_recursive(
    root: &Path,
    current: &Path,
    depth: usize,
    max_depth: usize,
    out: &mut String,
) -> std::io::Result<()> {
    if depth > max_depth || !current.exists() {
        return Ok(());
    }
    for entry in fs::read_dir(current)? {
        let entry = entry?;
        let path = entry.path();
        let rel = path.strip_prefix(root).unwrap_or(&path);
        let metadata = entry.metadata()?;
        out.push_str(&format!(
            "{}\t{}\t{}\n",
            if metadata.is_dir() { "DIR" } else { "FILE" },
            metadata.len(),
            rel.display()
        ));
        if metadata.is_dir() {
            list_dir_recursive(root, &path, depth + 1, max_depth, out)?;
        }
    }
    Ok(())
}

fn rotate_logs(paths: &BackendPaths) -> std::io::Result<()> {
    fs::create_dir_all(&paths.logs_dir)?;
    let log_files = [
        "backend.stdout.log",
        "backend.stderr.log",
        "conda-unpack.stdout.log",
        "conda-unpack.stderr.log",
    ];
    let has_logs = log_files.iter().any(|name| paths.logs_dir.join(name).exists());
    if has_logs {
        let archive_dir = paths
            .logs_dir
            .join("archive")
            .join(format!("run-{}", timestamp_secs()));
        fs::create_dir_all(&archive_dir)?;
        for name in log_files {
            let src = paths.logs_dir.join(name);
            if src.exists() {
                let _ = fs::rename(&src, archive_dir.join(name));
            }
        }
    }
    trim_log_archives(&paths.logs_dir.join("archive"), 10)?;
    Ok(())
}

fn trim_log_archives(archive_root: &Path, keep: usize) -> std::io::Result<()> {
    if !archive_root.exists() {
        return Ok(());
    }
    let mut dirs = fs::read_dir(archive_root)?
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().map(|t| t.is_dir()).unwrap_or(false))
        .collect::<Vec<_>>();
    dirs.sort_by_key(|entry| entry.file_name());
    while dirs.len() > keep {
        if let Some(entry) = dirs.first() {
            let _ = fs::remove_dir_all(entry.path());
        }
        dirs.remove(0);
    }
    Ok(())
}

fn remove_named_dirs(root: &Path, dir_name: &str) -> Result<(), String> {
    if !root.exists() {
        return Ok(());
    }
    for entry in fs::read_dir(root).map_err(|err| format!("Cannot read cache root: {err}"))? {
        let entry = entry.map_err(|err| format!("Cannot read cache entry: {err}"))?;
        let path = entry.path();
        if entry
            .file_type()
            .map_err(|err| format!("Cannot read cache entry type: {err}"))?
            .is_dir()
        {
            if entry.file_name().to_string_lossy() == dir_name {
                fs::remove_dir_all(&path)
                    .map_err(|err| format!("Cannot remove cache {}: {err}", path.display()))?;
            } else {
                remove_named_dirs(&path, dir_name)?;
            }
        }
    }
    Ok(())
}

fn write_app_log(paths: &BackendPaths, message: &str) -> Result<(), String> {
    fs::create_dir_all(&paths.logs_dir).map_err(|err| format!("Cannot create logs dir: {err}"))?;
    let mut file = open_log_file(&paths.logs_dir.join("app.log"))?;
    writeln!(file, "[{}] {}", timestamp_secs(), message)
        .map_err(|err| format!("Cannot write app log: {err}"))?;
    Ok(())
}

fn copy_dir_all(source: &Path, destination: &Path) -> std::io::Result<()> {
    fs::create_dir_all(destination)?;
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let file_type = entry.file_type()?;
        let target = destination.join(entry.file_name());
        if file_type.is_dir() {
            copy_dir_all(&entry.path(), &target)?;
        } else {
            fs::copy(entry.path(), target)?;
        }
    }
    Ok(())
}

fn open_log_file(path: &Path) -> Result<File, String> {
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|err| format!("Cannot open log file {}: {err}", path.display()))
}

fn find_free_port() -> Result<u16, String> {
    let listener = TcpListener::bind("127.0.0.1:0")
        .map_err(|err| format!("Cannot bind local port: {err}"))?;
    let port = listener
        .local_addr()
        .map_err(|err| format!("Cannot read local port: {err}"))?
        .port();
    drop(listener);
    Ok(port)
}

fn wait_for_backend(port: u16, timeout: Duration) -> bool {
    let started = Instant::now();
    while started.elapsed() < timeout {
        if health_check(port) {
            return true;
        }
        thread::sleep(Duration::from_millis(250));
    }
    false
}

fn health_check(port: u16) -> bool {
    let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(1)));
    let request = "GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = String::new();
    stream.read_to_string(&mut response).is_ok() && response.contains("200 OK")
}

/// Ile razy próbujemy pobrać capabilities i ile czekamy na odpowiedź.
///
/// Zmierzone: `import torch` + `cuda.is_available()` to 4–5 s (zarówno CPU, jak i
/// CUDA), więc 15 s daje trzykrotny zapas. Trzy próby wystarczają na przejściowy
/// problem, a jednocześnie ograniczają najgorszy przypadek (backend przyjmuje
/// połączenie, ale nie odpowiada) do ~35 s zamiast minut.
const CAPABILITIES_FETCH_ATTEMPTS: usize = 3;
const CAPABILITIES_FETCH_TIMEOUT_SECS: u64 = 15;

/// Pobiera capabilities backendu, z ponowieniami.
///
/// `/api/health` odpowiada od razu, ale **pierwsze** `/api/capabilities` importuje
/// torcha i inicjalizuje kontekst CUDA — przy runtime GPU (biblioteki cuDNN/cuBLAS
/// rzędu gigabajtów) potrafi to trwać kilkanaście sekund. Wcześniejsza pojedyncza
/// próba z 2-sekundowym timeoutem cicho degradowała się do `fallback_capabilities`
/// z `device: "cpu"`, a ta wartość była zamrażana na całą sesję: diagnostyka
/// pokazywała CPU mimo działającego runtime CUDA.
fn fetch_backend_capabilities(port: u16, token: &str) -> Option<BackendCapabilities> {
    for attempt in 0..CAPABILITIES_FETCH_ATTEMPTS {
        if attempt > 0 {
            std::thread::sleep(Duration::from_secs(2));
        }
        if let Some(capabilities) = try_fetch_backend_capabilities(port, token) {
            return Some(capabilities);
        }
    }
    None
}

fn try_fetch_backend_capabilities(port: u16, token: &str) -> Option<BackendCapabilities> {
    let mut stream = TcpStream::connect(("127.0.0.1", port)).ok()?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(
        CAPABILITIES_FETCH_TIMEOUT_SECS,
    )));
    let request = format!(
        "GET /api/capabilities HTTP/1.1\r\nHost: 127.0.0.1\r\nX-GeoTile-Token: {token}\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(request.as_bytes()).ok()?;
    let mut response = String::new();
    stream.read_to_string(&mut response).ok()?;
    if !response.contains("200 OK") {
        return None;
    }
    let body = response.split("\r\n\r\n").nth(1)?;
    serde_json::from_str::<BackendCapabilities>(body.trim()).ok()
}

fn fetch_backend_json(info: &BackendInfo, path: &str) -> Option<serde_json::Value> {
    let port = info
        .base_url
        .rsplit(':')
        .next()?
        .parse::<u16>()
        .ok()?;
    let mut stream = TcpStream::connect(("127.0.0.1", port)).ok()?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nX-GeoTile-Token: {}\r\nConnection: close\r\n\r\n",
        info.token
    );
    stream.write_all(request.as_bytes()).ok()?;
    let mut response = String::new();
    stream.read_to_string(&mut response).ok()?;
    if !response.contains("200 OK") {
        return None;
    }
    let body = response.split("\r\n\r\n").nth(1)?;
    serde_json::from_str(body.trim()).ok()
}

fn timestamp_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or_default()
}
