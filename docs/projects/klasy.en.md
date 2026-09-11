# Classes

**Goal.** Define the list of object classes used in the project.

**When.** Right after creating the project, before labeling starts.

## Import from a file

1. Open the project.
2. In the **Classes** section choose **Import from file**.
3. Point to `classes.json`.

## File format

An array of objects. Every class has an identifier, a name, a color and an optional
shortcut number:

```json
[
  { "id": 0, "name": "transport_aircraft", "color": "#FF0000", "hotkey": 1 },
  { "id": 1, "name": "helicopter",         "color": "#00FF00", "hotkey": 2 },
  { "id": 2, "name": "aircraft",           "color": "#0000FF", "hotkey": 3 }
]
```

| Field | Meaning |
| --- | --- |
| `id` | the class identifier, counted from zero |
| `name` | the name used in the interface and in exports |
| `color` | the color of the boxes on the map |
| `hotkey` | the number of the ++1++–++9++ key that selects the class |

!!! tip "Give the shortcut numbers to the classes you use most"

    The keys work only for classes with a number assigned - not by their order on the
    list. Classes without a number are picked from the list or through the search
    (++k++). There are nine shortcuts, so with a longer list it is worth reserving them
    for the objects that dominate the material.

## Adding by hand

Classes can also be added one at a time with the **Add class** button. With longer lists
it is more convenient to prepare a file and import it - the same file can then be sent
round the team.

A new class takes the next color from the default palette. The swatch to the left of the
name opens the choice: the palette, the system color wheel, or a HEX value typed in. The
same swatch on an existing class changes its color.

!!! info "The color is saved only once you confirm it"

    The choice has to be confirmed with **Apply**. Changing a color writes to the project
    class file, so clicking around the palette on its own does not make anything stick.
    The color is presentation only: it changes neither the annotations nor the exports.

## Agreeing on it in the team

!!! warning "Every analyst must have the same class file"

    The class name is what a package import matches annotations by. A divergence in the
    naming - even a small one, such as a plural or a different way of writing a
    character - means the manager sees a **missing class** on import instead of a
    correctly matched object.

When a package contains a class the target project does not know, it can be created
during the import. Without that consent **the scene is skipped entirely**, so that
replacing the annotations does not remove existing objects because of a problem with
classes.

## Changes after the work has started

Adding a new class during a project is safe. Renaming an existing one is not:
annotations point at a class, and exports and packages carry its name.

!!! tip "Unused classes show up in the audit"

    The dataset audit shows classes that are defined but unused, and ones present only in
    the source. That is a good moment to tidy the list up - before training, not after.
    See [Dataset audit](../datasets/audyt.md).
