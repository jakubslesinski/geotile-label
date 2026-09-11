# Exchange package types

The application creates four different ZIP packages. They look alike but do entirely
different things - confusing them is the most common source of misunderstanding in team
work.

| Package | Direction | What it is for | Carries geometry |
| --- | --- | --- | --- |
| **Project backup** | - | restoring a whole project | yes |
| **Annotation package** | analyst → manager | handing over labels without tiles | yes |
| **Review package** | manager → analyst | verdicts and comments | **no** |
| **Dataset ZIP** | - | training, audit, archiving | yes (the labels) |

## A backup versus an annotation package

!!! warning "They are not interchangeable"

    **A backup restores a whole project as a new one.** An annotation package **attaches
    work to an existing** project. Using a backup where an annotation package was needed
    creates a second, parallel project instead of merging the work.

A backup takes the configuration, the classes, the sources, the annotations and the
derived products. An annotation package carries only what the analyst drew, plus the data
that lets it be matched to the scenes on the manager's side.

## A review package contains no annotations

A review travels the other way and **carries no geometry**. It contains scene-level
verdicts together with the comment, and who issued them and when.

The practical consequence: importing a review **does not change a single annotation** on
the analyst's side. It only tells them which scenes are coming back for correction and
why. The analyst makes the corrections themselves and then sends an ordinary annotation
package back.

!!! info "A verdict concerns a scene, not an individual object"

    With thousands of annotations, assessing each one separately is unrealistic, so a
    verdict covers the whole scene. The details are conveyed in the comment.

## Scope and the supersedes chain

An annotation package carries a **scope** - the "scene × owner" pair. An import replaces
the annotations within that scope **wholesale**, which is what makes not only new objects
propagate, but corrections and deletions as well.

The next package from the same analyst for the same scene should point at the previous
one as superseded. Without that chain the application **rejects** the import with an
error, instead of guessing which version is newer.

!!! warning "Older packages can only add"

    Packages created before scopes were introduced can only add annotations - they will
    not update or delete existing ones. The differences are then reported as not applied.
    If corrections are not reaching the manager, check the package version first.

## Review rounds

A verdict refers to one specific round. Once the analyst sends corrected work back, the
verdict from the previous round becomes **stale** and is counted separately, instead of
posing as an assessment of the current version.

The full cycle is described in [Project roles](../teamwork/role-projektu.md).

## Dataset ZIP

The end product: tile images, labels in the chosen format, manifests, checksums and
provenance. It is not for exchanging work within a team - it is the input to training and
archival material.

The contents depend on the export format chosen; the overview is in the
[Export](../export/index.md) chapter.
