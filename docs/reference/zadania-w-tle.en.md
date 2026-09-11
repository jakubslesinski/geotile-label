# Background jobs

Long-running operations are executed as durable jobs. You can leave the current view
and continue to monitor them in the **Background jobs** drawer.

Jobs cover scene import and preparation, overview and full-resolution JP2 COG
generation, dataset builds, embedding analysis, whole-scene inference, export,
backup, and artifact cleanup.

## Open the drawer

The **Background jobs** button is in the **left application sidebar, immediately above
Help**. It displays the number of active operations, and the drawer opens from the
left. Each entry provides:

- the operation name and type;
- status and current stage;
- progress when the number of items is known;
- time and the latest event message;
- available actions: **Cancel**, **Retry**, or **Download**.

## Statuses

| Status | Meaning |
| --- | --- |
| **queued** | waiting for a resource or worker |
| **running** | the operation is in progress |
| **completed** | the result or artifact is ready |
| **cancelled** | the user stopped the job at a safe boundary |
| **failed** | an error was recorded and the job may be retried |

Cancellation is not always immediate. A raster operation, inference batch, or archive
write may need to reach a safe boundary to avoid publishing a partial result.
Turning automatic COG preparation off under **Scene sources** prevents new automatic
jobs but does not cancel one that is already running; use **Cancel** here.

## Retry and resume

**Retry** resumes from a safe checkpoint or creates another attempt with the same
configuration, depending on job type. Atomically published stages can be reused. Do
not edit `job.json` or `state.json` manually.

Scene import and dataset analysis persist checkpoints. Dataset builds and exports
publish their result only after completion, so a temporary directory is not a usable
dataset version.

## Downloadable artifacts

Project backup and dataset export finish with a ZIP artifact. Select **Download** on
the completed job and then choose the destination. Closing the drawer does not delete
the artifact.

## After an application restart

Job state is persisted on disk. If a worker process no longer exists, the operation
does not remain “running” forever: the application reconciles it according to that job
type's resume policy. Diagnostic files are described under [Data locations](lokalizacje-danych.md).
