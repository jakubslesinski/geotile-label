# AI tools

Three tools that speed labeling up. All of them produce **proposals**, not annotations.

!!! danger "A proposal becomes an annotation only once it is accepted"

    This rule holds throughout the chapter. The output of an AI tool is temporary: it
    will not enter a dataset, will not go into an annotation package and will not be
    counted in the statistics until a person accepts it.

## Which tool for what

| Tool | When | What it needs |
| --- | --- | --- |
| **YOLO prediction** | you have a model for similar material | a `.pt` file |
| **[SAM click-to-box](sam.md)** | single objects, no model available | a SAM model |
| **[SAM text](sam.md#text-mode-sam3)** | mark every object of a type with one prompt | a SAM3 model |
| **[Find similar](znajdz-podobne.md)** | many similar objects on one scene | a selected exemplar annotation |

All of them run on the CPU and are part of the base installation. The heavier engines
(SAM3 text, DINO few-shot) benefit from a GPU. The GPU pack is required only for
**training** your own models.

## YOLO prediction

Analyzes the whole scene and reports objects of the classes the model knows. It is most
effective when you have a model trained on comparable material.

Details: [YOLO prediction](predykcja.md).

## SAM - click and text

Turns a click into the outline of an object (**click-to-box**), or finds every object
described by a phrase (**text mode**, requires SAM3). It does not know the classes of
the project - it suggests a **shape**, and you assign the class. The SAM model is chosen
in the Prediction panel; a folder of your own weights can be selected.

Details: [SAM - click-to-box and text](sam.md).

## Find similar

Looks for objects similar to a selected annotation - you mark one and search for the
rest. Two engines: **Template (NCC)** (classic, offline) and **DINO (few-shot)**
(semantic, EO and SAR, GPU recommended). The engine is set in the Prediction panel or
on the fly in the tool menu on the map. The search scope is the local area, the current
view or the whole scene.

Details: [Find similar](znajdz-podobne.md).

!!! tip "Narrow the scope when the scene is large"

    Searching a whole large image takes time and returns more false hits. Restricting it
    to the current view usually gives a better result sooner.

## The work cycle

1. **Proposal** - the tool reports candidates.
2. **Review** - you look at the result.
3. **Correction** - you fix the geometry or the class where needed.
4. **Decision** - you accept or remove.

Only the fourth step creates a canonical annotation.

!!! info "What remains after acceptance"

    An accepted proposal records where it came from - that it originated from an AI tool
    rather than being drawn by hand. That is what makes the share of material coming from
    assistance visible in the audit and in the lineage of a model.

## Quality limits

AI tools work best on material close to what they were built on.

- a model from electro-optical imagery **will not work** on SAR, and the other way round;
- a ground sample distance different from the one used in training lowers the hit rate;
- SAR can be harder for SAM than an optical image, because object edges are less
  distinct.

!!! warning "An AI tool does not replace reviewing the scene"

    The absence of a proposal somewhere does not mean there is nothing there. Track
    coverage with the [review grid](../annotation/siatka-przegladu.md), not with the
    number of proposals.
