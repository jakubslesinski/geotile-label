# Installation

**Goal.** Install GeoTile Label and confirm that the application starts.

**When.** Before first use, and when moving to a newer version.

## Requirements

- Windows 10 or 11, 64-bit;
- the installer `GeoTile Label_<version>_x64-setup.exe`, downloaded from
  [Releases](https://github.com/jakubslesinski/geotile-label/releases);
- access to the scenes, the metadata files and the class file;
- a folder set aside for projects.

!!! info "You are not installing Python or anything else"

    The installer carries the application together with the backend environment, the
    GEO libraries and prediction running on the CPU. Docker, Python, Node.js and Rust
    are not needed.

## Steps

1. Run the `.exe` file.
2. If Windows SmartScreen warns about an unknown publisher, choose **More info → Run
   anyway** - provided the file came from a trusted source inside your organization.
3. Finish the installation and start GeoTile Label from the Start menu.

![Installer window with the SmartScreen warning and the More info button](../assets/images/smartscreen-warning.png)
*The SmartScreen warning shown for an unsigned installer.*

## Checkpoint

The application opens on the list of projects. **The first launch takes longer** - the
backend environment is being unpacked. Later starts are fast.

If the window stays empty or a message about the backend appears, go to
[Application does not start](../troubleshooting/aplikacja-nie-startuje.md).

## What was installed

The base installation covers **labeling, the AI tools and prediction** - all of it runs
on the CPU and needs no graphics card.

!!! warning "Model training requires a separate pack"

    The **Training** tab will be visible, but the architectures stay unavailable until
    you install the [GPU training pack](pakiet-gpu.md). It is a separate file, not part
    of the installer.

Application data goes to `%APPDATA%\GeoTileLabel`; the full list is in
[Data locations](../reference/lokalizacje-danych.md).

## Updating to a newer version

Install the new version over the current one - projects and settings stay untouched,
because they live outside the program directory.

!!! warning "The GPU pack must match the application version"

    The training runtime is released together with the application. After an update,
    install the pack in the same version as the application.

## Next step

[Analyst quick start](szybki-start-analityka.md) takes you from an empty application to
your first saved annotation.
