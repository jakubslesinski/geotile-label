# GPU training pack

**Goal.** Enable model training on an NVIDIA card.

**When.** Only if you intend to **train models**. Labeling, the AI tools and prediction
work without this pack.

## Why it is a separate file

The CUDA libraries weigh several gigabytes and do not fit inside the installer.
Separating them means an analyst installs under a gigabyte instead of several, and only
the people who actually train download the pack.

The pack is selected **by hand**. The application downloads nothing from the network.

## Requirements

- an NVIDIA card with a driver that supports CUDA 12.6;
- about 6.5 GB of free disk space;
- a pack in **the same version** as the installed application.

## Where to get the pack

The pack is **not published as a release asset** - the CUDA runtime alone weighs about
3 GB, which is more than a release attachment usefully carries. You build it yourself
from this repository, on a machine with network access:

```powershell
# 1. Base YOLO weights, if you do not have them yet - the repository does not carry them.
powershell -ExecutionPolicy Bypass -File .\scripts\fetch-base-models.ps1

# 2. The pack itself.
powershell -ExecutionPolicy Bypass -File .\scripts\build-cuda-pack.ps1
```

Step 1 fetches the weights into `data/models/base`. Without it the build warns and
produces a pack **without the weights archive**, and the Training tab then lists every
architecture as unavailable with the reason "Weights are missing locally". Skip it
deliberately only with `-SkipBaseModels`, when the target machine already has them.

The result lands in `release/cuda-pack-<version>/` together with the checksums and a
README for whoever installs it. The script downloads the PyTorch wheels for CUDA 12.6, so
the first run takes a while. `-PartSizeMB` splits the archive when the medium or the
transfer channel limits file size, and `-SkipBaseModels` leaves the weights out when the
target machine already has them.

Build it from **the same revision** as the installer you are using - the pack and the
application are versioned together, and the application refuses a pack from another
version.

## What you get

The pack folder holds two archives and a checksum file:

| File | Contents |
| --- | --- |
| `geotile-cuda-runtime-<version>.tar.gz` | the environment with PyTorch for CUDA |
| `geotile-base-models-<version>.tar.gz` | the base weights of the architectures |
| `SHA256SUMS.txt` | checksums for verification |

The large runtime file is sometimes split into parts (`.001`, `.002`, …). That is
normal - the parts are joined during installation.

## Steps

1. Copy **all** the files of the pack to the computer with the NVIDIA card.
2. Open **Settings → GPU training pack**.
3. Choose **Select CUDA pack**.
4. In the file dialog select **all the files at once**, including the weights archive
   and any parts.
5. Wait for the installation to finish.
6. **Restart the application.**

![Settings with the GPU training pack section and the Select CUDA pack button](../assets/images/gpu-training-pack-in-settings.png)
*The GPU training pack section in Settings.*

!!! warning "Select all the files, not just the runtime"

    The weights archive is separate from the runtime. Selecting the runtime alone
    installs the environment, but the **Training** tab will show the architectures as
    unavailable with the reason "Weights are missing locally". The files can also be
    selected separately, in two passes.

## Checkpoint

After the restart, **Settings** reports **The GPU runtime is active.** next to the pack.

The restart is required because the environment is chosen when the application starts.
Until then, the message asking you to restart stays visible.

## When the pack does not work

After unpacking, the application checks the pack with a probe. If the probe does not
pass, the pack is **removed** and the application carries on with the CPU environment.

!!! info "A failed installation does not break a working application"

    This is deliberate: an incompatible pack must not be able to block labeling. The
    most common reason for rejection is a driver older than CUDA 12.6 requires, or an
    incomplete set of archive parts.

Verifying the downloaded files in PowerShell:

```powershell
Get-FileHash .\geotile-cuda-runtime-<version>.tar.gz -Algorithm SHA256
```

Compare the result with `SHA256SUMS.txt`.

## What next

The architectures available after installation are described in
[Base architectures](../reference/architektury.md), and the first training run in
[Start training](../training/uruchom-trening.md).
