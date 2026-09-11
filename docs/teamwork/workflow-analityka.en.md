# Analyst workflow

**Goal.** Hand your work over to the manager and apply the corrections after a review.

**Project role.** Labeling.

An analyst works on the **full scenes** assigned to them. They do not create tiles or
datasets - that is the job of the collective project.

## Send the work

1. Open the project dashboard and choose **Export annotation package**.
2. Check the summary: the author, the number of scenes, annotations and classes, and the
   warnings about `NO GEO` scenes.
3. If the summary shows a **Prepare package** button, click it and wait for the progress
   bar. This is a one-off stage for a given scene - see below.
4. Save the `GeoTileAnnotations_<project>_<author>_<date>.zip` file and pass it to the
   manager.

**Checkpoint.** The summary shows the author address you use in the team, and the
expected number of scenes.

!!! warning "Check the author address before the first export"

    The address from the project profile decides whose work an import will replace. A
    typo creates a "second analyst" in the collective project, whose corrections will not
    replace the earlier objects but pile up next to them. See
    [Project roles](role-projektu.md).

## Preparing the package

A package carries annotations without images, so on the other side it has to point
unambiguously at the file they belong to. That is what the **exact fingerprint of the
source scene** is for: the full checksum of the raster, not merely the name or the size.
Only that tells two processings of the same acquisition apart - and those are exactly the
files that look alike and have identical headers.

Scene import computes a fast sampled fingerprint, which is not enough for this. That is
why, on the first export from a project, the summary offers **Prepare package**: the
application then reads the source files of the scenes and fills in the missing
fingerprints.

- **the cost** - a one-off read of the source files, shown before it starts: the number
  of scenes and the volume to be read. Progress runs in bytes, and the job can be
  interrupted;
- **the one-off nature** - the result is remembered as long as the file is unchanged, so
  the next package from this project reads nothing;
- **when it comes back** - after the source is replaced, relinked, or the working variant
  changes, because the fingerprint then stops matching the file.

The stage concerns only the scenes whose fingerprint is missing. When the button is not
there, everything is ready and you go straight to saving.

## What is in a package

Source annotations, classes, scene manifests, GeoParquet and checksums. **There are no
scene images, tiles or datasets** - which is why the package is light.

The package also declares its **scope**: for which author and which scenes it is a
complete set, when it was created and which earlier package it supersedes. The chain
builds itself - the next export of the same scenes marks the previous one as superseded.

!!! tip "Export whole scenes, not fragments of work"

    A package is **a complete set for a given scene**, not an increment. An import
    replaces the contents of a scene as a whole, so sending it mid-work is safe - the next
    package will replace the previous one.

## When a review comes back

1. Choose **Import review** and point to the file from the manager.
2. The verdicts appear in the **Review** column of the scene catalog.
3. Fix the scenes flagged for correction.
4. Export a new annotation package.

!!! info "Importing a review does not change a single annotation"

    A review package carries only verdicts and comments. There is no geometry in it, so
    nothing in your scenes will move or disappear. You make the corrections yourself - the
    review only points at where.

## Verdicts

| Verdict | What it means |
| --- | --- |
| **Accepted** | the scene is taken, nothing needs doing |
| **Needs fix** | go back to the scene, following the comment |
| **Rejected** | the work in this scene needs a fundamental change |

A verdict concerns **the whole scene**, not individual objects - the details are in the
comment.

## The rhythm of the work

```text
label → export a package → (review) → fix → export again
```

Do not hold the export back until the whole task is finished. Earlier packages let the
manager catch a divergence in class interpretation before you repeat it across a dozen
scenes.

## Related

- [Exchange package types](../reference/rodzaje-paczek.md) - how an annotation package
  differs from a backup
- [Review grid](../annotation/siatka-przegladu.md) - checking coverage before sending
