# Model registry

**Goal.** Keep a trained model together with the information about where it came from,
and designate the one the project uses.

## Registration

A model is registered from a finished training run. The checkpoint goes into the registry
together with its metrics and a full description of its provenance.

!!! info "Only a run that finished successfully can be registered"

    A run that was interrupted or ended with an error has no complete record of what
    happened. A model without such a record would be a file of unknown provenance -
    exactly what the registry exists to avoid.

## Promotion

**Promotion** designates the model the project uses for prediction. The previous one goes
into the history - it does not disappear.

!!! warning "The geometry has to match"

    A detection model cannot be promoted in a project with oriented boxes, or the other
    way round. Such a model would predict an entirely different kind of geometry, so the
    attempt is rejected rather than producing useless results.

The promotion history shows which model was in use during a given period - useful when
explaining where older predictions came from.

## Lineage

For a registered model the chain can be traced:

```text
model → training run → dataset version → tiles → annotations → authors
```

The lineage answers the question **"whose work went into this model"** - down to specific
annotations, not merely a list of the people on the project.

!!! info "What counts is use, not presence in the project"

    Only the authors of annotations that **actually went into the training** appear in the
    lineage. Work filtered out when the dataset was built, or lying in tiles unused in
    that version, is not counted.

What this is for in practice:

- establishing whose annotations shaped the behavior of a model when it confuses one
  particular class;
- accounting for contributions in team work;
- reconstructing the conditions a model was created under during an audit.

## When the lineage cannot be traced

The chain requires the dataset version and its tile catalog to still exist. If they have
been deleted, the application says so outright rather than showing an incomplete list
posing as a complete one.

!!! warning "This is why a version used for training cannot be deleted"

    The deletion lock described in
    [Publishing a version](../datasets/publikowanie.md) exists precisely for this. A model
    whose provenance cannot be reconstructed stops being accountable - and in GEOINT work
    that usually disqualifies it more thoroughly than weaker metrics.

## The model in the project

The promoted model is the one used by [prediction](../ai-assistance/predykcja.md) in that
project. Any `.pt` file from disk can also be selected - the registry does not restrict
prediction, it only keeps your own models in order.
