---
date: 2026-11-10
description: Why attribution must be preserved before embedding or classifying a passage.
---

# Why speaker boundaries come first

*10 November 2026 · Embeddings and attribution*

## The question

Can an embedding map reveal differences between speakers when each embedded
passage contains contributions from several of them?

That was a problem in the initial passage construction. Filtering an already
embedded mixed passage by speaker changes which passages are visible. It does not
remove the other speaker's words from the vector that was computed earlier.

## The change

A separate embedding pass now constructs passages from a single speaker's text.
An index can include several speakers while retaining separate passages, or can
be built for one selected speaker. Mixed-context indexes remain available for
questions where conversational context is useful.

This moves attribution into the input representation, before vector construction.
The embedding model is only one part of the method; passage construction changes
what its output represents.

## The trade-off

Consider this invented exchange:

> Speaker A: Would a walk help you relax?  
> Speaker B: Yes.

The isolated reply carries little topical information. The mixed exchange gives
it meaning, but also includes an activity suggested by another speaker. These
representations answer different questions. Keeping both makes that choice
explicit instead of hiding it in a filter.

## What this supports

Speaker-separated indexes make it possible to compare thematic patterns while
knowing whose words entered each vector. They do not establish that similar
vectors reflect the same intention, feeling or personal trait.

For explicit question scoring, the next step is to distinguish the **target
speaker** from any **preceding context**, then inspect examples where those two
point in different directions. See [questions and patterns](../methods/questions-and-patterns.md).
