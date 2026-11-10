# Questions and patterns

Embedding clusters describe similarity. Explicit questions provide another view:
which passages express evidence for a defined category?

## Three answer types

- **Independent yes/no:** several categories may be present together. Scores for
  anxiety, hope and programming do not have to sum to one.
- **Choice:** use a distribution over competing alternatives, ideally with an
  “other / unclear” option.
- **Ordered scale:** describe levels from lowest to highest. Inspect the full
  distribution as well as its expected level.

Questions classify a target speaker's text. Preceding context can help interpret
it, but another speaker's statement is not evidence that the target said the same
thing. This remains a model instruction, so attribution needs example review.

## Views over a long archive

| View | Useful question | Important limit |
|---|---|---|
| Daily or weekly timeline | When does a category become more common? | Empty bins mean no observations, not absence. |
| Hour of day, all dates combined | Does a category tend to appear at a particular hour? | Timezone, daylight saving and uneven activity matter. |
| Shared-window co-occurrence | Which categories appear in the same conversational windows? | Co-occurrence is not causation. |
| Following patterns | Does one category often precede another nearby window? | Window size, conversation boundaries and sampling affect order. |
| Cluster keyword signatures | Which words distinguish a cluster? | Frequent phrases can describe writing style rather than topic. |

Keep counts and denominators visible. Compare co-occurrence with marginal
frequencies, inspect low-support results, and keep undated material available
outside the timeline.

## Interpretation

These scores are model estimates about text. They are not calibrated measurements
of emotion, clinical assessments, or evidence about subjective experience.
Fiction, quotations, sarcasm, short replies and language differences can all
change classification. An interesting heatmap cell starts an inspection; it does
not finish an explanation.
