# Find similar

**Goal.** Mark one object and find the rest of the same type on the scene - without
drawing each of them separately.

**When.** When the same object repeats across a scene (a vehicle park, a row of
buildings, a fleet of vessels). You mark one and search for the similar ones.

**Requirements.** At least one selected exemplar annotation. The **DINO** engine
additionally needs DINO weights and preferably a GPU; the **Template** engine works
offline on the CPU.

## Two engines

| Engine | How it works | When |
| --- | --- | --- |
| **Template (NCC)** | correlation of pixels/edges | EO, fast, offline |
| **DINO (few-shot)** | similarity of DINO network features | semantic, EO **and SAR**, robust to scale/rotation/illumination |

The engine is chosen in the **Prediction** panel (the **Find-similar engine** section) -
that is the **project default** - or ad hoc in the tool menu on the map. Both places
write the same value, so they cannot drift apart.

!!! info "DINO is few-shot without training"

    DINO turns the exemplar into a feature vector and looks for objects with similar
    features - **without training a model**. It is stronger in this domain than the
    template (on satellite material especially), but it computes more slowly; a GPU is
    assumed for interactive work.

## Steps

1. Select the annotation that is to be the **exemplar**. You can select **several**
   (++ctrl++ - one by one, ++shift++ - a range) - the **DINO** engine averages them into
   a single few-shot prototype.
2. In the AI panel on the map click **Find similar** (the menu next to the button holds
   the engine and scope selection).
3. Review the proposals and accept or remove them, in bulk as well with multi-selection.

<!-- Raw HTML is not rewritten by MkDocs, so the path must be written for this page's
     own depth: English pages sit one level deeper, under en/. -->
<video controls autoplay loop muted playsinline style="width:100%;height:auto" title="The exemplar and the similar objects found as proposals">
  <source src="../../assets/images/find-similar.mp4" type="video/mp4">
</video>
*One selected object, the rest found automatically.*

## Search scope

Shared by both engines:

- **Local area (2048 px)** - the neighborhood of the exemplar; the fastest.
- **Current map view** - what is visible on the map.
- **Whole scene** - the complete image (the longest; for DINO on a CPU it can be very
  slow).

!!! tip "Narrow the scope on large scenes"

    Searching a whole scene takes time and returns more false hits. The current view
    usually gives a better result sooner.

## Template engine settings

- **Minimum similarity** (the threshold) - higher = fewer, more certain hits.
- **Scale tolerance** and **Rotation tolerance** - how far the objects may differ from
  the exemplar.
- **Include edges** - matching on edges instead of raw pixels; it helps when the
  illumination varies.

## DINO engine settings

- **DINO weights folder** - in the Prediction panel you point to the directory and the
  variant. **DINOv3-SAT** (the satellite variant) is preferred by default - stronger on
  EO/SAR; you can choose the lighter DINOv2 (faster on a CPU) or a file of your own.
- **Similarity threshold** - as above, the cosine of the features (slider range
  0.2–1.0).
- **Several exemplars (few-shot)** - select a few examples of the same class before
  running. A prototype averaged from several objects is more robust to variance
  (different aspects, shadows, orientations) and usually gives fewer false hits than a
  single one.

!!! tip "How to choose exemplars for better results"

    Pick **varied** examples (different orientation, background, brightness), not several
    identical ones. Two to four well-chosen exemplars are usually enough; an atypical or
    partly occluded exemplar tends to blur the prototype rather than help.

!!! warning "The quality depends on the variant and on the size of the objects"

    On very small objects the light DINOv2 discriminates poorly. For satellite material
    use the **SAT** variant and tune the threshold; the output is **boxes** (the outline
    is carried over from the exemplar).

## Reviewing proposals

The output is **proposals**, not annotations - you accept or remove them. An accepted one
remembers where it came from (template- or DINO-assisted), which is visible in the audit
and in the lineage.

!!! danger "A proposal is not an annotation yet"

    Nothing reaches a dataset without acceptance. Use **Accept all** after looking at the
    result, not instead of looking at it.

## Related

- [SAM - click and text](sam.md) - the outline of a single object and text mode
- [YOLO prediction](predykcja.md) - auto-labeling with a model
- [Dataset analysis](../datasets/analiza.md) - the same DINO features in label review
