# Analysis workflow

The working process separates representation, exploratory grouping and explicit
questions. Each step keeps enough provenance to revisit the choice that produced
the result.

1. **Preserve the source.** Import into a local archive without modifying the
   original export. Keep links from derived passages back to their source records.
2. **Check time and order.** Distinguish a timestamp actually present in a source
   from an inferred ordering. Missing dates stay missing; they are not silently
   assigned to a timeline.
3. **Choose the passage representation.** Use single-speaker content when
   attribution matters. Use mixed conversation context only as an explicit choice.
4. **Explore similarity.** Build reusable embeddings, then vary cluster resolution
   without rebuilding the vectors. Inspect passages behind any apparent theme.
5. **Ask defined questions.** Score explicit hypotheses about the target text.
   Save definitions and model/backend versions with the results.
6. **Look across time.** Compare timeline bins, aggregate hours, shared windows and
   possible following patterns with their denominators and missing-data counts.
7. **Review and revise.** Treat a pattern as a reason to inspect evidence, not as
   proof of a psychological state or a causal relationship.

## Keep experiments comparable

Record passage construction, speaker selection, context policy, question revision,
model/backend, date coverage, timezone and any sampling. A change in one of these
can change the result even when the visualization looks the same.

For a dated experiment, use the sequence **question → setup → observation → limits
→ next step**. The [journal](../journal/index.md) preserves those decisions over time.
