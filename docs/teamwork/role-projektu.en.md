# Project roles and annotation ownership

Teamwork rests on two notions: the **project role**, which decides what operations are
available, and the **annotation owner**, which decides what an import touches.

## The project role

The role is chosen **once, when the project is created**. There are no accounts and no
logging in.

| Role | Who | Can |
| --- | --- | --- |
| **Labeling** (the default) | the analyst | export annotation packages, import reviews |
| **Review** | the manager | import annotation packages, issue verdicts, export reviews |

A review project is **the end of the road**: its product is a dataset, not another
annotation package - which is why exporting an annotation package is disabled in it.

!!! info "Unavailable buttons are greyed out, not hidden"

    Always with the reason stated. A missing function is meant to be understandable, not
    to look like a fault.

Projects created before roles were introduced open as **Labeling** and work unchanged.
Working alone requires nothing to be configured.

## The annotation owner

Every annotation created locally is given the address from the project profile. That
field is the **owner** and decides what a package import will touch.

A separate field preserves the **original author**, even if ownership later changes. That
way handing work over does not erase who did it.

!!! danger "Ownership is cooperative, not enforced"

    The owner address is text entered by hand in the project settings, and packages are
    not signed. Nobody verifies that an analyst entered their own address.

    The mechanism protects **against a mistake, not against bad faith**. For an offline
    tool in a team that trusts itself, that is a deliberate choice - but do not treat it
    as a security measure and do not build formal accountability on it.

## Why the owner matters at import

A package import **replaces the annotations of a given owner in a given scene as a
whole**. Corrections come in, and objects the analyst deleted disappear.

Annotations of other owners - a second analyst, or the manager themselves - stay
untouched. That is why the scope of a package is the "scene × owner" pair, not the scene
alone.

!!! tip "Agree on the addresses before you start"

    An analyst who enters a different address than usual creates a "second author" in the
    collective project. Their corrections will not replace the earlier work, they will
    only pile up next to it. That is the most common cause of duplicated objects after an
    import.

## The work loop

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

Analysts **do not generate tiles or datasets** - that is the job of the review project.

## Related

- [Analyst workflow](workflow-analityka.md)
- [Manager quick start](szybki-start-managera.md)
- [Exchange package types](../reference/rodzaje-paczek.md)
