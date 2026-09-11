# Package import fails

Importing an annotation package rejects some of the scenes, or the whole package. Every
cause has a different fix.

!!! info "A rejected scene does not spoil the rest of the import"

    A problem with one scene blocks only that scene. The others are applied normally.

## Missing scene

The package contains a scene that is not present in the target project.

**Fix.** Import the missing scene into the collective project, then repeat the package
import.

## A different working view

The same source scene, but prepared in a different working grid than the one in the
target project.

**Fix.** Prepare the scene in the same grid before importing.

!!! warning "This is not an ambiguity to be resolved"

    Annotations are stored in the pixels of one specific working view. Forcing them onto
    a different grid would shift the objects with respect to the ground. That is why the
    application reports it instead of guessing. See
    [Derived products](../input-data/produkty-pochodne.md).

## A match on the vendor identifier alone

Scenes are matched by a stable identifier, by the checksum of the file, and as a last
resort by a controlled set of metadata. A match based on the vendor identifier alone
**requires manual confirmation** and is not applied automatically.

## Missing class

The package contains a class that the target project does not know.

**Fix.** Agree to create it during the import, or add it beforehand.

!!! danger "Without that consent the scene is skipped entirely"

    This is a safeguard. An import replaces the annotations of an owner in a scene as a
    whole - if some of the objects dropped out because of a missing class, the
    replacement would **delete the rest** and leave an incomplete set. Better to skip the
    scene and report the problem than to quietly trim the data.

The lasting fix is agreeing on a single class file across the team. See
[Classes](../projects/klasy.md).

## Two packages from the same author for the same scene

Without a supersedes chain the import is **rejected with an error**.

**Fix.** Choose the newest package.

!!! info "Why the application does not choose by itself"

    The date of a file does not say which package holds the newer work - it could have
    been copied or restored from a backup. Guessing would risk undoing corrections, so
    the application prefers to ask.

## Changed annotations do not come through

The same object with different content is not updated, and the differences are reported
as not applied.

**Cause.** The package comes from before scopes were introduced. Such packages can only
**add** - they will not update or delete existing annotations.

**Fix.** Ask for a fresh export from the current version of the application.

## Where to look for details

The import report and a snapshot of the data from before the import stay in the project
folder, under `import_reports\`. The import is transactional - a failed one leaves no
intermediate state.
