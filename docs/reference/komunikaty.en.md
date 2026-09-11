# Message index

Messages appear as the **body of a notification** (a toast) after an action fails. Most
of them come from the local application service. Below is what each one means and one
specific corrective action. The list is representative, not exhaustive; for longer
scenarios see [troubleshooting](../troubleshooting/index.md).

## Project and scenes

| Message | Meaning | What to do |
| --- | --- | --- |
| `Project not found` | no project with that identifier exists | refresh the project list; it may have been deleted or moved |
| `Selected folder is not a GeoTile Label project folder` | the folder given is not an application project | point to a directory with the project files (see [data locations](lokalizacje-danych.md)) |
| `A project cannot combine SAR and EO scene sources` | an attempt to add a source of a different modality than the project | create a separate project for the other modality |
| `Scene is not georeferenced` | the action needs a `GEO` scene and this one is not | use a georeferenced scene; see [scene statuses](statusy-scen.md) |
| `Only scenes imported from a managed source can be removed here` | the scene does not come from a catalogued source | remove the whole source instead of the single scene |

## Importing scenes and packages

| Message | Meaning | What to do |
| --- | --- | --- |
| `No image files found in folder` / `No .ntf/.nitf files found…` | the folder holds no recognized scene files | point to the right folder; check [vendor products](produkty-dostawcow.md) |
| `Scene import preview is invalid` / `Invalid preview_id` | the import preview expired after the project changed | generate the import preview again |
| `Three RGB band indexes are required for this product` | a multi-band product needs bands to be selected | give three RGB band indexes (for example `3,2,1`) |
| `Selected asset does not belong to this scene package` | the file given does not belong to this package | choose an asset from the right package |

More: [Package import fails](../troubleshooting/import-paczek.md).

## Dataset

| Message | Meaning | What to do |
| --- | --- | --- |
| `No tile catalog found. Build the tile catalog first.` | there is no tile catalog for this version | build the [tile catalog](../datasets/katalog-kafelkow.md) |
| `No tile annotations. Generate dataset first.` | an export or operation before the dataset was generated | [build the dataset](../datasets/zbuduj-dataset.md) first |
| `Dataset not found. Generate it first.` | the files of the version have not been generated | generate the dataset version |
| `No project classes match the dataset class filter` | the class filter excluded every class | widen the class filter in the dataset configuration |
| `Buffer must be smaller than tile size` | invalid tiling geometry | set the overlap smaller than the tile size |

## Training and evaluation

| Message | Meaning | What to do |
| --- | --- | --- |
| `Dataset run must be published before training` | training starts only from a published version | publish the dataset version; see [publishing](../datasets/publikowanie.md) |
| `Dataset run is not complete` | the version chosen has not finished generating | wait for it to finish, or generate it again |
| `This run did not finish, so it cannot be evaluated.` | a test evaluation for an unfinished run | finish the training before evaluating; see [results](../training/wyniki.md) |
| `No labels_obb/ files in the dataset run…` | a `rotated_bbox` project and a run without OBB labels | generate the dataset again - the OBB labels are created automatically |

## Prediction and models

| Message | Meaning | What to do |
| --- | --- | --- |
| `Model not configured or file not found` / `Model file not found` | the path to the YOLO model is empty or stale | point to the current `.pt` file; see [prediction is unavailable](../troubleshooting/predykcja-niedostepna.md) |
| `SAM models directory not found` / `DINO models directory not found` | the SAM/DINO weights folder does not exist | use **Clear path**, then **Change folder** and point to the new directory |
| `SAM checkpoint not found` / `DINO checkpoint not found` | the weights file given does not exist | choose an existing checkpoint from the current folder |
| `No SAM checkpoint configured or bundled` | there is no SAM model for the click-to-box tool | point to the SAM models folder |
| `Text mode requires a SAM3 checkpoint (e.g. sam3.pt)` | text mode works only with a SAM3 model | point to a `sam3.pt` checkpoint |
| `Path outside configured roots` | the path lies outside the permitted directories | choose a file from a directory within the configured locations |

## Reviews and team packages

| Message | Meaning | What to do |
| --- | --- | --- |
| `Exporting a review package requires a 'review' project; this project is '…'` | a review can be exported only from a project with the *review* role | do it in the manager's collective project; see [project roles](../teamwork/role-projektu.md) |
| `No reviewed scenes to export` | no scene has a verdict yet | assess the scenes before exporting a review |
| `output_path must be an absolute path` | a relative path was given | give the full, absolute path to save to |

## Related

- [Troubleshooting](../troubleshooting/index.md) - longer corrective scenarios
- [Scene statuses](statusy-scen.md) - the states in the scene catalog
- [Data locations](lokalizacje-danych.md) - where the application keeps its files
