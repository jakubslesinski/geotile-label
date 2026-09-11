//! Conservative multi-instance process ownership and stale-session cleanup.
//!
//! A PID alone is not an identity: Windows can reuse it immediately after a process
//! exits.  Every destructive decision therefore uses `(pid, creation marker)`, and a
//! process is preserved whenever its path, identity or ancestry is incomplete.

use serde::{Deserialize, Serialize};
use std::{
    collections::{HashMap, HashSet},
    fs::{self, File},
    io::Write,
    path::{Component, Path, PathBuf},
    sync::Mutex,
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use sysinfo::{ProcessRefreshKind, RefreshKind, System, UpdateKind};
use uuid::Uuid;

#[cfg(target_os = "windows")]
use windows_sys::Win32::{
    Foundation::{CloseHandle, FILETIME, HANDLE},
    System::Threading::{
        GetProcessTimes, OpenProcess, TerminateProcess, PROCESS_QUERY_LIMITED_INFORMATION,
        PROCESS_TERMINATE,
    },
};

const SESSION_SCHEMA_NAME: &str = "geotile_process_session";
const SESSION_SCHEMA_VERSION: u32 = 1;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub struct ProcessIdentity {
    pub pid: u32,
    /// Exact Windows `FILETIME` creation tick. On other platforms this is the
    /// start-time marker exposed by sysinfo.
    pub start_time_marker: u64,
}

#[derive(Clone, Debug)]
struct ProcessRecord {
    pid: u32,
    identity: Option<ProcessIdentity>,
    parent_pid: Option<u32>,
    executable: Option<PathBuf>,
    name: String,
}

#[derive(Clone, Debug, Default)]
struct ProcessSnapshot {
    processes: HashMap<u32, ProcessRecord>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum IdentityStatus {
    Live,
    Absent,
    Recycled,
    Unknown,
}

impl ProcessSnapshot {
    fn capture() -> Self {
        let system = System::new_with_specifics(
            RefreshKind::nothing()
                .with_processes(ProcessRefreshKind::nothing().with_exe(UpdateKind::Always)),
        );
        let processes = system
            .processes()
            .iter()
            .map(|(pid, process)| {
                let pid = pid.as_u32();
                let identity =
                    process_start_marker(pid, process.start_time()).map(|marker| ProcessIdentity {
                        pid,
                        start_time_marker: marker,
                    });
                (
                    pid,
                    ProcessRecord {
                        pid,
                        identity,
                        parent_pid: process.parent().map(|value| value.as_u32()),
                        executable: process.exe().map(Path::to_path_buf),
                        name: process.name().to_string_lossy().into_owned(),
                    },
                )
            })
            .collect();
        Self { processes }
    }

    fn identity_is_live(&self, identity: &ProcessIdentity) -> bool {
        self.identity_status(identity) == IdentityStatus::Live
    }

    fn identity_status(&self, identity: &ProcessIdentity) -> IdentityStatus {
        match self.processes.get(&identity.pid) {
            None => IdentityStatus::Absent,
            Some(process) => match process.identity.as_ref() {
                Some(current) if current == identity => IdentityStatus::Live,
                Some(_) => IdentityStatus::Recycled,
                None => IdentityStatus::Unknown,
            },
        }
    }

    fn descends_from(&self, candidate_pid: u32, owner: &ProcessIdentity) -> bool {
        let mut current = Some(candidate_pid);
        let mut visited = HashSet::new();
        while let Some(pid) = current {
            if !visited.insert(pid) {
                return false;
            }
            let Some(process) = self.processes.get(&pid) else {
                return false;
            };
            if process.identity.as_ref() == Some(owner) {
                return true;
            }
            current = process.parent_pid;
        }
        false
    }

    fn descendants(&self, root: &ProcessIdentity) -> Vec<(usize, ProcessIdentity)> {
        let mut result = Vec::new();
        for process in self.processes.values() {
            let Some(identity) = process.identity.clone() else {
                continue;
            };
            if let Some(depth) = self.depth_from(process.pid, root) {
                result.push((depth, identity));
            }
        }
        result.sort_by_key(|item| std::cmp::Reverse(item.0));
        result
    }

    fn depth_from(&self, candidate_pid: u32, root: &ProcessIdentity) -> Option<usize> {
        let mut current = Some(candidate_pid);
        let mut visited = HashSet::new();
        let mut depth = 0usize;
        while let Some(pid) = current {
            if !visited.insert(pid) {
                return None;
            }
            let process = self.processes.get(&pid)?;
            if process.identity.as_ref() == Some(root) {
                return Some(depth);
            }
            current = process.parent_pid;
            depth += 1;
        }
        None
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct ProcessSessionManifest {
    schema_name: String,
    schema_version: u32,
    session_id: String,
    created_at_unix_s: u64,
    owner: ProcessIdentity,
    owner_executable: Option<PathBuf>,
    backend: Option<ProcessIdentity>,
}

pub struct ProcessSession {
    path: PathBuf,
    manifest: Mutex<ProcessSessionManifest>,
}

impl ProcessSession {
    pub fn start(session_dir: &Path) -> Result<Self, String> {
        fs::create_dir_all(session_dir).map_err(|err| {
            format!(
                "Cannot create process-session directory {}: {err}",
                session_dir.display()
            )
        })?;
        let snapshot = ProcessSnapshot::capture();
        let pid = std::process::id();
        let owner = snapshot
            .processes
            .get(&pid)
            .and_then(|process| process.identity.clone())
            .ok_or_else(|| format!("Cannot establish exact identity of desktop process {pid}"))?;
        let owner_executable = std::env::current_exe()
            .ok()
            .and_then(|path| path.canonicalize().ok().or(Some(path)));
        let session_id = Uuid::new_v4().to_string();
        let path = session_dir.join(format!("{session_id}.json"));
        let manifest = ProcessSessionManifest {
            schema_name: SESSION_SCHEMA_NAME.to_string(),
            schema_version: SESSION_SCHEMA_VERSION,
            session_id,
            created_at_unix_s: unix_timestamp(),
            owner,
            owner_executable,
            backend: None,
        };
        write_manifest(&path, &manifest)?;
        Ok(Self {
            path,
            manifest: Mutex::new(manifest),
        })
    }

    pub fn set_backend(&self, pid: u32) -> Result<ProcessIdentity, String> {
        let identity = (0..20)
            .find_map(|_| {
                let snapshot = ProcessSnapshot::capture();
                let identity = snapshot
                    .processes
                    .get(&pid)
                    .and_then(|process| process.identity.clone());
                if identity.is_none() {
                    thread::sleep(Duration::from_millis(10));
                }
                identity
            })
            .ok_or_else(|| format!("Cannot establish exact identity of backend process {pid}"))?;
        let mut manifest = self
            .manifest
            .lock()
            .map_err(|_| "Process-session manifest lock is poisoned".to_string())?;
        manifest.backend = Some(identity.clone());
        write_manifest(&self.path, &manifest)?;
        Ok(identity)
    }

    pub fn clear_backend(&self) -> Result<(), String> {
        let mut manifest = self
            .manifest
            .lock()
            .map_err(|_| "Process-session manifest lock is poisoned".to_string())?;
        manifest.backend = None;
        write_manifest(&self.path, &manifest)
    }

    pub fn backend_identity(&self) -> Option<ProcessIdentity> {
        self.manifest
            .lock()
            .ok()
            .and_then(|manifest| manifest.backend.clone())
    }

    pub fn finish(&self) {
        let _ = fs::remove_file(&self.path);
        if let Some(parent) = self.path.parent() {
            remove_dir_if_empty(parent);
        }
    }
}

impl Drop for ProcessSession {
    fn drop(&mut self) {
        self.finish();
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
enum CandidateDecision {
    PreserveLiveSession {
        session_id: String,
    },
    TerminateStaleBackend {
        session_id: String,
        root: ProcessIdentity,
    },
    CoveredByStaleBackend {
        session_id: String,
        root: ProcessIdentity,
    },
    PreserveRecycledPid {
        session_id: String,
    },
    PreserveUnknownOwner {
        session_id: String,
    },
    PreserveAmbiguous,
}

#[derive(Default)]
pub struct SweepReport {
    pub logs: Vec<String>,
    pub terminated: usize,
    pub preserved: usize,
    pub removed_manifests: usize,
}

pub fn sweep_orphaned_backends(runtime_dir: &Path, session_dir: &Path) -> SweepReport {
    let snapshot = ProcessSnapshot::capture();
    let (manifests, mut logs) = load_manifests(session_dir);
    let mut report = SweepReport::default();
    report.logs.append(&mut logs);

    let mut roots_to_terminate: HashMap<ProcessIdentity, (String, ProcessIdentity)> =
        HashMap::new();
    let mut candidates: Vec<&ProcessRecord> = snapshot
        .processes
        .values()
        .filter(|process| {
            process
                .executable
                .as_deref()
                .is_some_and(|path| path_scope(path, runtime_dir) != PathScope::Outside)
        })
        .collect();
    candidates.sort_by_key(|process| process.pid);

    for candidate in candidates {
        let scope = candidate
            .executable
            .as_deref()
            .map(|path| path_scope(path, runtime_dir))
            .unwrap_or(PathScope::Ambiguous);
        let path = candidate
            .executable
            .as_deref()
            .map(|value| value.display().to_string())
            .unwrap_or_else(|| "<unknown>".to_string());
        let decision = if scope == PathScope::Within {
            classify_candidate(candidate, &snapshot, &manifests)
        } else {
            CandidateDecision::PreserveAmbiguous
        };
        match decision {
            CandidateDecision::PreserveLiveSession { session_id } => {
                report.preserved += 1;
                report.logs.push(format!(
                    "process_sweep keep pid={} name={} path={} reason=descendant_of_live_session session={}",
                    candidate.pid, candidate.name, path, session_id
                ));
            }
            CandidateDecision::TerminateStaleBackend { session_id, root } => {
                report.logs.push(format!(
                    "process_sweep candidate pid={} name={} path={} reason=exact_stale_backend session={}",
                    candidate.pid, candidate.name, path, session_id
                ));
                let owner = manifests
                    .iter()
                    .find(|(_, manifest)| manifest.session_id == session_id)
                    .map(|(_, manifest)| manifest.owner.clone());
                if let Some(owner) = owner {
                    roots_to_terminate
                        .entry(root)
                        .or_insert((session_id, owner));
                }
            }
            CandidateDecision::CoveredByStaleBackend { session_id, root } => {
                report.logs.push(format!(
                    "process_sweep covered pid={} name={} path={} reason=descendant_of_stale_backend root_pid={} session={}",
                    candidate.pid, candidate.name, path, root.pid, session_id
                ));
            }
            CandidateDecision::PreserveRecycledPid { session_id } => {
                report.preserved += 1;
                report.logs.push(format!(
                    "process_sweep keep pid={} name={} path={} reason=pid_reused_identity_mismatch session={}",
                    candidate.pid, candidate.name, path, session_id
                ));
            }
            CandidateDecision::PreserveUnknownOwner { session_id } => {
                report.preserved += 1;
                report.logs.push(format!(
                    "process_sweep keep pid={} name={} path={} reason=owner_identity_unknown session={}",
                    candidate.pid, candidate.name, path, session_id
                ));
            }
            CandidateDecision::PreserveAmbiguous => {
                report.preserved += 1;
                report.logs.push(format!(
                    "process_sweep keep pid={} name={} path={} reason=ambiguous_or_unowned",
                    candidate.pid, candidate.name, path
                ));
            }
        }
    }

    let owner_verification = ProcessSnapshot::capture();
    for (root, (session_id, owner)) in roots_to_terminate {
        if matches!(
            owner_verification.identity_status(&owner),
            IdentityStatus::Live | IdentityStatus::Unknown
        ) {
            report.preserved += 1;
            report.logs.push(format!(
                "process_sweep keep pid={} reason=owner_not_confirmed_stale session={}",
                root.pid, session_id
            ));
            continue;
        }
        let termination = terminate_process_tree_if_same(&root);
        report.terminated += termination.terminated;
        for message in termination.logs {
            report.logs.push(format!("{message} session={session_id}"));
        }
    }

    let refreshed = ProcessSnapshot::capture();
    for (path, manifest) in manifests {
        match refreshed.identity_status(&manifest.owner) {
            IdentityStatus::Live => continue,
            IdentityStatus::Unknown => {
                report.logs.push(format!(
                    "process_sweep keep_manifest session={} reason=owner_identity_unknown",
                    manifest.session_id
                ));
                continue;
            }
            IdentityStatus::Absent | IdentityStatus::Recycled => {}
        }
        if let Some(backend) = manifest.backend.as_ref() {
            match refreshed.identity_status(backend) {
                IdentityStatus::Live => {
                    report.logs.push(format!(
                        "process_sweep keep_manifest session={} reason=stale_backend_still_alive",
                        manifest.session_id
                    ));
                    continue;
                }
                IdentityStatus::Unknown => {
                    report.logs.push(format!(
                        "process_sweep keep_manifest session={} reason=backend_identity_unknown",
                        manifest.session_id
                    ));
                    continue;
                }
                IdentityStatus::Absent | IdentityStatus::Recycled => {}
            }
        }
        match fs::remove_file(&path) {
            Ok(()) => {
                report.removed_manifests += 1;
                report.logs.push(format!(
                    "process_sweep remove_manifest session={} reason=owner_and_backend_not_alive",
                    manifest.session_id
                ));
            }
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {}
            Err(err) => report.logs.push(format!(
                "process_sweep keep_manifest session={} reason=remove_failed error={err}",
                manifest.session_id
            )),
        }
    }
    remove_dir_if_empty(session_dir);
    report
}

pub struct RuntimeBlocker {
    pub pid: u32,
    pub executable: String,
    pub reason: &'static str,
}

pub fn runtime_blockers(dir: &Path) -> Vec<RuntimeBlocker> {
    let snapshot = ProcessSnapshot::capture();
    let mut blockers: Vec<_> = snapshot
        .processes
        .values()
        .filter_map(|process| {
            let executable = process.executable.as_deref()?;
            let scope = path_scope(executable, dir);
            if scope == PathScope::Outside {
                return None;
            }
            Some(RuntimeBlocker {
                pid: process.pid,
                executable: executable.display().to_string(),
                reason: if scope == PathScope::Within {
                    "runtime_in_use"
                } else {
                    "runtime_path_ambiguous"
                },
            })
        })
        .collect();
    blockers.sort_by_key(|blocker| blocker.pid);
    blockers
}

#[derive(Default)]
pub struct TerminationReport {
    pub logs: Vec<String>,
    pub terminated: usize,
    pub preserved: usize,
}

pub fn terminate_process_tree_if_same(root: &ProcessIdentity) -> TerminationReport {
    let snapshot = ProcessSnapshot::capture();
    let mut report = TerminationReport::default();
    if !snapshot.identity_is_live(root) {
        report.preserved += 1;
        report.logs.push(format!(
            "process_terminate keep pid={} reason=identity_not_confirmed_live",
            root.pid
        ));
        return report;
    }

    for (_depth, identity) in snapshot.descendants(root) {
        match terminate_exact_identity(&identity) {
            ExactTermination::Terminated => {
                report.terminated += 1;
                report.logs.push(format!(
                    "process_terminate terminated pid={} reason=exact_identity_match",
                    identity.pid
                ));
            }
            ExactTermination::IdentityChanged => {
                report.preserved += 1;
                report.logs.push(format!(
                    "process_terminate keep pid={} reason=identity_changed_before_termination",
                    identity.pid
                ));
            }
            ExactTermination::Failed => {
                report.preserved += 1;
                report.logs.push(format!(
                    "process_terminate keep pid={} reason=termination_failed",
                    identity.pid
                ));
            }
        }
    }
    report
}

fn classify_candidate(
    candidate: &ProcessRecord,
    snapshot: &ProcessSnapshot,
    manifests: &[(PathBuf, ProcessSessionManifest)],
) -> CandidateDecision {
    for (_path, manifest) in manifests {
        if snapshot.identity_is_live(&manifest.owner)
            && snapshot.descends_from(candidate.pid, &manifest.owner)
        {
            return CandidateDecision::PreserveLiveSession {
                session_id: manifest.session_id.clone(),
            };
        }
    }

    for (_path, manifest) in manifests {
        match snapshot.identity_status(&manifest.owner) {
            IdentityStatus::Live => continue,
            IdentityStatus::Unknown => {
                if manifest.backend.as_ref().is_some_and(|backend| {
                    candidate.pid == backend.pid
                        || (snapshot.identity_is_live(backend)
                            && snapshot.descends_from(candidate.pid, backend))
                }) {
                    return CandidateDecision::PreserveUnknownOwner {
                        session_id: manifest.session_id.clone(),
                    };
                }
                continue;
            }
            IdentityStatus::Absent | IdentityStatus::Recycled => {}
        }
        let Some(backend) = manifest.backend.as_ref() else {
            continue;
        };
        if candidate.pid == backend.pid {
            if candidate.identity.as_ref() == Some(backend) {
                return CandidateDecision::TerminateStaleBackend {
                    session_id: manifest.session_id.clone(),
                    root: backend.clone(),
                };
            }
            return CandidateDecision::PreserveRecycledPid {
                session_id: manifest.session_id.clone(),
            };
        }
        if snapshot.identity_is_live(backend) && snapshot.descends_from(candidate.pid, backend) {
            return CandidateDecision::CoveredByStaleBackend {
                session_id: manifest.session_id.clone(),
                root: backend.clone(),
            };
        }
    }
    CandidateDecision::PreserveAmbiguous
}

fn load_manifests(session_dir: &Path) -> (Vec<(PathBuf, ProcessSessionManifest)>, Vec<String>) {
    let mut manifests = Vec::new();
    let mut logs = Vec::new();
    let Ok(entries) = fs::read_dir(session_dir) else {
        return (manifests, logs);
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|value| value.to_str()) != Some("json") {
            continue;
        }
        let parsed = fs::read(&path)
            .map_err(|err| err.to_string())
            .and_then(|bytes| {
                serde_json::from_slice::<ProcessSessionManifest>(&bytes)
                    .map_err(|err| err.to_string())
            });
        match parsed {
            Ok(manifest)
                if manifest.schema_name == SESSION_SCHEMA_NAME
                    && manifest.schema_version == SESSION_SCHEMA_VERSION =>
            {
                manifests.push((path, manifest));
            }
            Ok(manifest) => {
                logs.push(format!(
                "process_sweep keep_manifest path={} reason=unsupported_schema name={} version={}",
                path.display(), manifest.schema_name, manifest.schema_version
            ))
            }
            Err(err) => logs.push(format!(
                "process_sweep keep_manifest path={} reason=parse_failed error={err}",
                path.display()
            )),
        }
    }
    (manifests, logs)
}

fn write_manifest(path: &Path, manifest: &ProcessSessionManifest) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("Session manifest has no parent: {}", path.display()))?;
    fs::create_dir_all(parent)
        .map_err(|err| format!("Cannot create {}: {err}", parent.display()))?;
    let temporary = parent.join(format!(".{}.tmp", Uuid::new_v4()));
    let payload = serde_json::to_vec_pretty(manifest)
        .map_err(|err| format!("Cannot serialize process session: {err}"))?;
    let result = (|| {
        let mut file = File::create(&temporary)
            .map_err(|err| format!("Cannot create {}: {err}", temporary.display()))?;
        file.write_all(&payload)
            .map_err(|err| format!("Cannot write {}: {err}", temporary.display()))?;
        file.sync_all()
            .map_err(|err| format!("Cannot flush {}: {err}", temporary.display()))?;
        if path.exists() {
            fs::remove_file(path)
                .map_err(|err| format!("Cannot replace {}: {err}", path.display()))?;
        }
        fs::rename(&temporary, path)
            .map_err(|err| format!("Cannot publish {}: {err}", path.display()))
    })();
    let _ = fs::remove_file(temporary);
    result
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum PathScope {
    Within,
    Outside,
    Ambiguous,
}

fn path_scope(candidate: &Path, root: &Path) -> PathScope {
    match (candidate.canonicalize(), root.canonicalize()) {
        (Ok(candidate), Ok(root)) => {
            if component_path_is_within(&candidate, &root) {
                PathScope::Within
            } else {
                PathScope::Outside
            }
        }
        _ if component_path_is_within(candidate, root) => PathScope::Ambiguous,
        _ => PathScope::Outside,
    }
}

fn component_path_is_within(candidate: &Path, root: &Path) -> bool {
    let Some(candidate) = normalized_components(candidate) else {
        return false;
    };
    let Some(root) = normalized_components(root) else {
        return false;
    };
    candidate.len() >= root.len() && candidate[..root.len()] == root[..]
}

fn normalized_components(path: &Path) -> Option<Vec<String>> {
    path.components()
        .map(|component| match component {
            Component::CurDir => Some(".".to_string()),
            Component::ParentDir => Some("..".to_string()),
            Component::RootDir => Some(std::path::MAIN_SEPARATOR.to_string()),
            Component::Prefix(prefix) => prefix
                .as_os_str()
                .to_str()
                .map(|value| value.to_lowercase()),
            Component::Normal(value) => value.to_str().map(|value| value.to_lowercase()),
        })
        .collect()
}

fn remove_dir_if_empty(path: &Path) {
    if fs::read_dir(path)
        .ok()
        .is_some_and(|mut entries| entries.next().is_none())
    {
        let _ = fs::remove_dir(path);
    }
}

fn unix_timestamp() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

#[cfg(target_os = "windows")]
fn process_start_marker(pid: u32, _fallback: u64) -> Option<u64> {
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            return None;
        }
        let marker = process_start_marker_from_handle(handle);
        CloseHandle(handle);
        marker
    }
}

#[cfg(target_os = "windows")]
unsafe fn process_start_marker_from_handle(handle: HANDLE) -> Option<u64> {
    let mut creation: FILETIME = std::mem::zeroed();
    let mut exit: FILETIME = std::mem::zeroed();
    let mut kernel: FILETIME = std::mem::zeroed();
    let mut user: FILETIME = std::mem::zeroed();
    if GetProcessTimes(handle, &mut creation, &mut exit, &mut kernel, &mut user) == 0 {
        return None;
    }
    Some(((creation.dwHighDateTime as u64) << 32) | creation.dwLowDateTime as u64)
}

#[cfg(not(target_os = "windows"))]
fn process_start_marker(_pid: u32, fallback: u64) -> Option<u64> {
    Some(fallback)
}

enum ExactTermination {
    Terminated,
    IdentityChanged,
    Failed,
}

#[cfg(target_os = "windows")]
fn terminate_exact_identity(identity: &ProcessIdentity) -> ExactTermination {
    unsafe {
        let handle = OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE,
            0,
            identity.pid,
        );
        if handle.is_null() {
            return ExactTermination::IdentityChanged;
        }
        let marker = process_start_marker_from_handle(handle);
        if marker != Some(identity.start_time_marker) {
            CloseHandle(handle);
            return ExactTermination::IdentityChanged;
        }
        let terminated = TerminateProcess(handle, 1) != 0;
        CloseHandle(handle);
        if terminated {
            ExactTermination::Terminated
        } else {
            ExactTermination::Failed
        }
    }
}

#[cfg(not(target_os = "windows"))]
fn terminate_exact_identity(identity: &ProcessIdentity) -> ExactTermination {
    let snapshot = ProcessSnapshot::capture();
    if !snapshot.identity_is_live(identity) {
        return ExactTermination::IdentityChanged;
    }
    let system = System::new_all();
    match system.process(sysinfo::Pid::from_u32(identity.pid)) {
        Some(process) if process.kill() => ExactTermination::Terminated,
        Some(_) => ExactTermination::Failed,
        None => ExactTermination::IdentityChanged,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn identity(pid: u32, marker: u64) -> ProcessIdentity {
        ProcessIdentity {
            pid,
            start_time_marker: marker,
        }
    }

    fn record(pid: u32, marker: u64, parent_pid: Option<u32>, executable: &str) -> ProcessRecord {
        ProcessRecord {
            pid,
            identity: Some(identity(pid, marker)),
            parent_pid,
            executable: Some(PathBuf::from(executable)),
            name: Path::new(executable)
                .file_stem()
                .unwrap_or_default()
                .to_string_lossy()
                .into_owned(),
        }
    }

    fn manifest(
        session_id: &str,
        owner: ProcessIdentity,
        backend: Option<ProcessIdentity>,
    ) -> (PathBuf, ProcessSessionManifest) {
        (
            PathBuf::from(format!("{session_id}.json")),
            ProcessSessionManifest {
                schema_name: SESSION_SCHEMA_NAME.to_string(),
                schema_version: SESSION_SCHEMA_VERSION,
                session_id: session_id.to_string(),
                created_at_unix_s: 1,
                owner,
                owner_executable: None,
                backend,
            },
        )
    }

    #[test]
    fn active_worker_of_second_instance_is_preserved_through_full_ancestry() {
        let owner = identity(10, 100);
        let backend = identity(20, 200);
        let mut snapshot = ProcessSnapshot::default();
        snapshot
            .processes
            .insert(10, record(10, 100, None, "desktop.exe"));
        snapshot
            .processes
            .insert(20, record(20, 200, Some(10), "runtime/python.exe"));
        let worker = record(30, 300, Some(20), "runtime/python.exe");
        snapshot.processes.insert(30, worker.clone());
        let manifests = vec![manifest("other", owner, Some(backend))];

        assert_eq!(
            classify_candidate(&worker, &snapshot, &manifests),
            CandidateDecision::PreserveLiveSession {
                session_id: "other".to_string()
            }
        );
    }

    #[test]
    fn exact_backend_of_dead_owner_is_terminated() {
        let backend = identity(20, 200);
        let mut snapshot = ProcessSnapshot::default();
        let candidate = record(20, 200, Some(10), "runtime/python.exe");
        snapshot.processes.insert(20, candidate.clone());
        let manifests = vec![manifest("stale", identity(10, 100), Some(backend.clone()))];

        assert_eq!(
            classify_candidate(&candidate, &snapshot, &manifests),
            CandidateDecision::TerminateStaleBackend {
                session_id: "stale".to_string(),
                root: backend,
            }
        );
    }

    #[test]
    fn recycled_backend_pid_is_never_terminated() {
        let mut snapshot = ProcessSnapshot::default();
        let recycled = record(20, 999, None, "runtime/python.exe");
        snapshot.processes.insert(20, recycled.clone());
        let manifests = vec![manifest(
            "stale",
            identity(10, 100),
            Some(identity(20, 200)),
        )];

        assert_eq!(
            classify_candidate(&recycled, &snapshot, &manifests),
            CandidateDecision::PreserveRecycledPid {
                session_id: "stale".to_string()
            }
        );
    }

    #[test]
    fn unreadable_live_owner_identity_is_ambiguous_and_preserved() {
        let backend = identity(20, 200);
        let mut snapshot = ProcessSnapshot::default();
        let mut owner = record(10, 100, None, "desktop.exe");
        owner.identity = None;
        snapshot.processes.insert(10, owner);
        let candidate = record(20, 200, Some(10), "runtime/python.exe");
        snapshot.processes.insert(20, candidate.clone());
        let manifests = vec![manifest("unknown-owner", identity(10, 100), Some(backend))];

        assert_eq!(
            classify_candidate(&candidate, &snapshot, &manifests),
            CandidateDecision::PreserveUnknownOwner {
                session_id: "unknown-owner".to_string()
            }
        );
    }

    #[test]
    fn missing_or_cyclic_ancestry_is_ambiguous_and_preserved() {
        let owner = identity(10, 100);
        let mut snapshot = ProcessSnapshot::default();
        snapshot
            .processes
            .insert(10, record(10, 100, None, "desktop.exe"));
        let candidate = record(30, 300, Some(31), "runtime/python.exe");
        snapshot.processes.insert(30, candidate.clone());
        snapshot
            .processes
            .insert(31, record(31, 310, Some(30), "runtime/python.exe"));
        let manifests = vec![manifest("live", owner, None)];

        assert_eq!(
            classify_candidate(&candidate, &snapshot, &manifests),
            CandidateDecision::PreserveAmbiguous
        );
    }

    #[test]
    fn path_matching_uses_components_not_text_prefixes() {
        let root = PathBuf::from("runtime").join("backend-env");
        assert!(component_path_is_within(&root.join("python.exe"), &root));
        assert!(!component_path_is_within(
            &PathBuf::from("runtime")
                .join("backend-env-old")
                .join("python.exe"),
            &root
        ));
        assert_eq!(
            path_scope(&root.join("missing-python.exe"), &root),
            PathScope::Ambiguous
        );
    }

    #[test]
    fn session_manifest_records_pid_creation_marker_and_cleans_up() {
        let root = std::env::temp_dir().join(format!("geotile-session-test-{}", Uuid::new_v4()));
        let session = ProcessSession::start(&root).expect("session starts");
        let manifest: ProcessSessionManifest =
            serde_json::from_slice(&fs::read(&session.path).expect("manifest exists"))
                .expect("manifest parses");
        assert_eq!(manifest.owner.pid, std::process::id());
        assert!(manifest.owner.start_time_marker > 0);
        session.finish();
        assert!(!session.path.exists());
        let _ = fs::remove_dir_all(root);
    }
}
