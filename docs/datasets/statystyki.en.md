# Dataset statistics

The tab shows what actually ended up in a dataset version. It serves to catch problems
the audit does not report as an error, but which spoil training.

## What you will find

- the number of source annotations and of annotations after propagation into tiles;
- the class distribution and the distribution across the split;
- tiles with annotations, used reviewed empty cells, technical exclusions, and cells
  omitted or unchecked;
- the usage of individual scenes;
- unused classes and scenes without annotations.

!!! tip "Long names are shortened on the axes"

    The full name of a class or a scene appears when you point at the element with the
    cursor.

![Annotations per class, class distribution per split, tile usage and class imbalance](../assets/images/dataset-statistics-dashboard.png)
*The statistics of a dataset version.*

## Source annotations versus tile annotations

Two different numbers, and **it is normal for them to differ**.

A source annotation lies on the full scene. When it is cut, it can land in several tiles
- especially when the tiles overlap - so the number after propagation tends to be higher.

!!! warning "A large difference the other way is a warning sign"

    If there are **noticeably fewer** tile annotations than source ones, some of the
    objects fell out of the dataset. The usual causes: source filters narrowed more than
    intended, or objects lying in cells that are unchecked or excluded.

## Class distribution

Class imbalance is normal in real data, but an extreme one can make a model ignore the
rare classes.

When one class dominates, consider a spatial split with class balancing, or a deliberate
decision that the rare classes are not ready for training yet.

!!! info "A class with a handful of examples is usually too few"

    It is better to filter it out and train it in later than to teach a model a class it
    has practically not seen - otherwise it will confuse it with similar ones.

## Scene usage

Shows how many tiles come from which scene. An extreme dominance of one scene means the
model is mostly learning its conditions - the time of day, the viewing angle, the
characteristics of the sensor.

That matters especially with SAR, where the acquisition geometry changes the appearance
of the same objects considerably.

## Unused classes and scenes without annotations

A class that is defined but unused usually means one of two things: nobody has found such
an object yet, or the team is using a different name than the agreed one.

A scene without annotations may genuinely be empty - or simply untouched. The
[review grid](../annotation/siatka-przegladu.md) settles it: a scene that was reviewed
and is empty is valuable material, a scene nobody looked at is a backlog.

## Related

- [Dataset audit](audyt.md) - checking readiness for training
- [Build a dataset](zbuduj-dataset.md) - the parameters that shape the numbers above
