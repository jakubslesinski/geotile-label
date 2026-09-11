# Teamwork

The team workflow separates **annotating full scenes** from **building the dataset**.
Analysts hand over light annotation packages, the manager merges them in a collective
project and runs the quality control.

There are no accounts and no logging in. Everything rests on the project role and on the
author address entered in its profile.

## Start here

<div class="grid cards" markdown>

-   :material-account-edit: **I am an analyst**

    ---

    Exporting a package, the correction cycle after a review.

    [Analyst workflow](workflow-analityka.md)

-   :material-account-tie: **I am a manager**

    ---

    The collective project, package import, verdicts, the dataset.

    [Manager quick start](szybki-start-managera.md)

-   :material-shield-account: **How it works**

    ---

    Roles, annotation ownership and the scope of a replacement.

    [Project roles](role-projektu.md)

</div>

## The loop in short

```text
analyst                         manager
   │                            │
   ├─ labels scenes             │
   ├─ exports a package ───────►│
   │                            ├─ imports, decides per scene
   │                            ├─ checks completeness
   │◄─────── exports a review ──┤  (verdicts, no geometry)
   ├─ fixes the flagged scenes  │
   └─ exports a new package ───►│
                                └─ builds the catalog and the dataset
```

## The three rules it stands on

**Replacement, not appending.** An import replaces the annotations of a given owner in a
given scene as a whole. That is what makes not only new objects propagate, but
**corrections and deletions** as well.

**A verdict at scene level.** The manager assesses the work, not every object
separately - with thousands of annotations nothing else is feasible.

**A review without geometry.** The package that goes back to the analyst carries only
verdicts and comments, so importing a review cannot break anything in their scenes.

## Before you hand the work out

1. **Agree on the classes** - the same `classes.json` for everybody. The class name is
   the matching key.
2. **Agree on the author addresses** - a typo creates a second analyst in the collective
   project.
3. **Agree on the project profile** - modality, georeferencing and annotation mode.
4. **Agree on how the scenes are prepared** - a divergence in working views surfaces only
   at import, that is, after the work has been done.

!!! danger "Ownership protects against a mistake, not against bad faith"

    The author address is text entered by hand, and packages are not signed. That is a
    deliberate choice for an offline tool in a team that trusts itself - but do not build
    formal accountability on it. Details in [Project roles](role-projektu.md).
