# Keyboard shortcuts

The complete list of shortcuts for the labeling view. Shortcuts do not work while the
cursor is in a text field.

## Map tools

A tool stays active until another one is chosen.

| Shortcut | Tool | What it does |
| --- | --- | --- |
| ++v++ | Pan / select | moving the map and selecting individual annotations |
| ++d++ | Draw annotation | turns drawing mode on and off |
| ++a++ | Multi-select | selecting a group of annotations with a rectangle |
| ++m++ | Measure | measuring distances on the scene |
| ++p++ | Class paint | assigning a class to one annotation after another without opening the list |

## Classes

| Shortcut | Action |
| --- | --- |
| ++1++ – ++9++ | select the class with that shortcut number |
| ++k++ | open the class search |
| ++n++ | toggle negative mode |

!!! note "The numbers work only for classes that have one"

    The ++1++–++9++ keys select the class with that shortcut number assigned. A class
    without a number is reachable only through the list or the search (++k++). Assigning
    the numbers is described in [Classes](../projects/klasy.md).

## Annotations

| Shortcut | Action |
| --- | --- |
| ++c++ + dragging the center of a box | create a copy of the annotation |
| ++delete++ or ++backspace++ | delete the selected annotation outside drawing mode |
| ++ctrl+z++ | undo the last annotation |
| ++esc++ | cancel the current operation |

## Review grid

| Shortcut | Action |
| --- | --- |
| ++s++ | show or hide the grid |
| ++r++ | mark the cell under the cursor as reviewed |
| ++shift+r++ | mark every visible cell as reviewed |
| ++e++ | exclude the cell under the cursor |
| ++shift+e++ | exclude every visible cell |

The meaning of the cell states is described in
[Review grid](../annotation/siatka-przegladu.md).

!!! tip "++s++ shows the grid that matters at that moment"

    Before the grid exists, ++s++ toggles the configuration preview. Once it exists - the
    actual review grid. The two are never visible at the same time.

## Layers and scene appearance

| Shortcut | Action |
| --- | --- |
| ++z++ | show or hide the source raster layer |
| ++bracket-left++ | decrease scene opacity by 10% |
| ++bracket-right++ | increase scene opacity by 10% |
| ++ctrl+bracket-left++ | decrease gamma |
| ++ctrl+bracket-right++ | increase gamma |

Hiding the raster (++z++) makes it possible to compare the annotations with the
reference map layer on georeferenced scenes.

!!! warning "++ctrl+z++ is undo, not a layer"

    ++z++ on its own toggles the raster layer, and ++ctrl+z++ undoes the last annotation.
    Two different actions on the same key.
