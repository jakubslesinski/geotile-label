# Manager quick start

**Goal.** From a collective project to a validated dataset version.

**Project role.** Review.

## 1. Set up the collective project

Create a project with **all the scenes** of the task and set the **Review** role. The
profile - modality, georeferencing, annotation mode - must be the same as the analysts'.

!!! warning "Agree on the classes before handing the work out"

    The class name is the matching key at import. Give the team the same `classes.json`
    file that you will use in the collective project. See
    [Classes](../projects/klasy.md).

## 2. Import the packages

1. Choose **Import annotations into project**.
2. Point to the packages - **several at once is fine**. The scopes of different analysts
   are disjoint and apply without conflict.
3. Review the preview with a decision **per scene**.
4. Confirm.

For every scene the preview shows the number of annotations before and after, and the
balance of the changes:

```text
lot_A.tif · analyst@example.com     312 → 318   +8  ~4  −2   [x] accept
lot_B.tif · analyst@example.com      87 →  87    0   0   0    -
lot_C.tif · analyst@example.com     154 →   0    0   0 −154  [ ] accept  ⚠
```

!!! danger "Accepting a scene replaces it as a whole"

    For a given owner: corrections come in, and the objects they deleted **disappear**.
    Annotations of other owners stay untouched.

    That is why a row with a large negative balance - like `lot_C` above - deserves
    attention. It may be correct (the analyst rightly deleted mistaken work) or it may
    mean an error.

## Three safeguards

They work automatically:

1. **A scene losing most of an owner's work** is unchecked by default and requires
   explicit confirmation.
2. **A package that cannot be resolved** - with a missing class, for example - **deletes
   nothing**. The whole scene is skipped with the reason stated, so that a configuration
   problem cannot erase data.
3. **An annotation you corrected earlier** does not come back as a duplicate from an
   older analyst package.

The import is transactional. The report and a snapshot of the data from before the
import stay in the project folder.

## 3. Check completeness

The **Annotation Summary** section shows the completeness of the scenes, the number of
objects by class and by author, annotations without an author, and the history of
imports.

This is the moment to catch a divergence in class interpretation - before it reaches the
dataset.

## 4. Send it back for correction

1. In the **Review** column click the verdict of a scene.
2. Choose **Accepted**, **Needs fix** or **Rejected** and add a comment.
3. Export the review in one of two modes:
    - **per analyst** - give the analyst's address; the package will cover only the
      scenes where they have annotations (a separate package for each analyst);
    - **All reviews** - tick this box to send the verdicts for every reviewed scene in one
      package, without giving an email address.

A verdict is **at scene level** - you assess the work, not every object separately. A
review package contains only verdicts and comments.

!!! info "How All reviews works"

    At import the verdicts land on scenes matched **by scene identifier**, so one
    collective package can be given to every analyst - only their own scenes are updated,
    and the rest are skipped with a harmless warning. Such a package does, however,
    contain the scene names and the comments of every analyst - for strict separation, use
    the per-analyst mode.

## 5. Build the dataset

After the checks, build **one tile catalog**, and from it any number of dataset versions.

1. [Tile catalog](../datasets/katalog-kafelkow.md)
2. [Build a dataset](../datasets/zbuduj-dataset.md)
3. [Audit](../datasets/audyt.md)
4. [Publish a version](../datasets/publikowanie.md)

**Checkpoint.** A dataset version with a `ready` audit, or with warnings judged
deliberately, published and ready for training.

## What next

[Start training](../training/uruchom-trening.md) on the published version.
