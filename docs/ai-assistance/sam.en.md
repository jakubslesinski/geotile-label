# SAM - click-to-box and text

**Goal.** Turn a click (or a text prompt) into a finished outline of an object, so that
boxes do not have to be drawn from scratch. SAM suggests the **shape** - you assign the
class.

**When.** Single objects with a distinct edge (click), or marking every object of a
given type in the frame with one command (text), when you have no detection model.

**Requirements.** A SAM model selected (a `.pt` file). Text mode requires **SAM3**.
Everything runs offline on the CPU; a large DINO/SAM3 model benefits from a GPU.

## Models

The SAM model is chosen in the **Prediction** panel (or by right-clicking the SAM icon
in the AI panel on the map), with the option of pointing to your own folder of weights.
Supported families:

| Family | Example weights | Notes |
| --- | --- | --- |
| SAM1 | `mobile_sam`, `sam_b/l/h` | light, general purpose |
| SAM2 | `sam2.1_t/s/b/l` | newer generation |
| **SAM3** | `sam3.pt` | required for **text mode** |
| FastSAM | `FastSAM-s/x` | fast, less accurate |

!!! info "SAM3 needs the full runtime"

    Text mode works only with a SAM3 model and the matching environment (the text
    encoder). When it is missing, the text tool is **greyed out with a readable reason**
    - it does not hang and does not report a "network error".

## Click-to-box

1. Set the **active class**.
2. Turn on the **SAM** tool in the AI panel on the map (the wand icon).
3. **Click on the object** - a box proposal appears (or an oriented box, depending on
   the annotation mode).

<!-- Raw HTML is not rewritten by MkDocs, so the path must be written for this page's
     own depth: English pages sit one level deeper, under en/. -->
<video controls autoplay loop muted playsinline style="width:100%;height:auto" title="A click on a vehicle and the resulting SAM box">
  <source src="../../assets/images/sam-click-to-box.mp4" type="video/mp4">
</video>
*One click turns into the outline of a single object.*

!!! tip "SAM picks a single object, not a group"

    On dense satellite scenes a click used to catch a whole cluster of neighboring
    objects. The tool now selects the mask of a **single object** - by quality and size
    fit - instead of the largest area.

!!! tip "A selected annotation hints at the size"

    If you have **an annotation of the same class selected**, its size becomes a size
    hint for the clicks that follow. This helps when objects of different scale sit next
    to each other.

## Text mode (SAM3)

1. Set the **active class** and zoom in on the fragment you care about.
2. Click **SAM3 text** in the AI panel - the prompt window opens.
3. **Type the prompt** (the field is pre-filled with the class name, but you can change
   it).
4. Choose **Run on current view** or **Draw search area** and mark a rectangle.

<!-- Raw HTML is not rewritten by MkDocs, so the path must be written for this page's
     own depth: English pages sit one level deeper, under en/. -->
<video controls autoplay loop muted playsinline style="width:100%;height:auto" title="The SAM3 prompt window with the text field and the area selection">
  <source src="../../assets/images/sam3-text-prompt.mp4" type="video/mp4">
</video>
*You type the prompt yourself; the area is the current view or a rectangle you draw.*

!!! warning "Type a natural phrase, not a class code"

    SAM3 understands an **open vocabulary in English**. A class name is often an
    abbreviation or contains characters like `_` (for example `pojazd_transportowy`) -
    that makes a weak prompt. Type a natural description instead, such as
    `military vehicle`, `aircraft`, `ship`.

**Text mode settings:**

- **Confidence** (0.25 by default) - on EO/SAR imagery, quick-look products especially,
  confidences tend to be moderate; a lower threshold returns more candidates.
- **Area** - the current view or a rectangle you draw; it works at **full resolution**,
  so the area is bounded (with the view too wide, a request to zoom in appears).

## Reviewing proposals

The output of both modes is **proposals**, not annotations. You review them and accept
or remove them, in bulk as well by selecting several with ++ctrl++/++shift++. Oriented
boxes need the heading direction confirmed.

!!! danger "A proposal is not an annotation yet"

    Nothing enters a dataset until you accept it. An accepted proposal remembers where it
    came from (SAM-assisted), which is visible in the audit and in the lineage.

## Common problems

- **No model / SAM unavailable** - point to the weights in the Prediction panel.
- **Text mode greyed out** - a SAM3 model and its runtime are needed.
- **The click catches the background instead of the object** - the object has no distinct
  edge; try text mode or exemplar matching.

## Related

- [Find similar](znajdz-podobne.md) - template matching and DINO few-shot
- [YOLO prediction](predykcja.md) - auto-labeling a whole scene with a model
- [AI tools](index.md) - the work cycle and the limits
