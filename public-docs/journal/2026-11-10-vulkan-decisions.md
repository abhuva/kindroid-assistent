---
date: 2026-11-10
description: A small synthetic GPU comparison and the engineering needed for managed local scoring.
---

# Testing Vulkan for question scoring

*10 November 2026 · Local inference and performance*

## The question

Could the existing GTX 1050 Ti accelerate local question scoring enough to make
larger experiments more practical?

The first managed decision backend used jevos v5 with OpenVINO INT8 on the CPU.
A prototype tested the public Q8 GGUF weights with a pinned llama.cpp Vulkan
runtime before adding a GPU option to the archive tool.

## What was compared

The test used seven synthetic passages. It covered independent yes/no questions,
a three-way choice, a three-level scale, three 558-character targets with eleven
questions each, and a longer 1,390-character target. GPU and CPU model lifetimes
were sequential. CPU inference used four threads.

The machine had a Ryzen 5 3550H, about 5.9 GiB usable system RAM and a GTX 1050 Ti
with 4 GB VRAM. The GPU path used llama.cpp **b12255** and **jevos v5 Q8**. It read
the binary answer-token distribution and reproduced option/scale aggregation;
it did not ask a chat model to invent percentages.

| Measurement | Vulkan Q8 | OpenVINO CPU |
|---|---:|---:|
| Median of three 558-character, eleven-question passages | 1.40 s | 3.86 s |
| Longer target, eleven questions | 1.59 s | 5.10 s |
| Total scoring across all seven passages, excluding startup | 6.91 s | 19.74 s |

The representative median was about **2.8 times faster**. All **49 compared
labels** agreed, using a 0.5 threshold for yes/no and the largest probability for
choice/scale. The largest probability difference was **7.96 percentage points**.
Agreement is not the same as independently measured accuracy or calibration.

## The surprises

The laptop's default Vulkan enumeration exposed the integrated AMD GPU. Selecting
the installed NVIDIA Vulkan driver for the owned child process resolved startup
without changing global driver settings.

Prompt construction also mattered: adding a start token twice was caught during
the prototype and corrected. A backend comparison is only useful when the input
format and scoring semantics are comparable.

## From prototype to an option in the tool

The managed GPU integration added a backend selector, separate CPU/GPU cache
identities, saved runtime/model provenance and checks for actual full layer
offload. Existing runs keep their original backend on resume. It also preserves
job progress, cancellation and completed passage answers.

A subsequent synthetic worker check scored 39 logical answers from 48 binary
evaluations across three passages. It paused, closed the runtime, resumed and
completed, then reused all three passages on a repeated run.

## Limits and next questions

First-use timing varied: an earlier GPU trial took 6.15 seconds for its first
eleven-question target before later targets became faster. The desktop was live,
the sample was small and the text was synthetic. These numbers are not an ETA for
an entire archive.

The next useful measurements are longer varied runs, system memory pressure and
reviewed examples close to classification thresholds. CPU and GPU probabilities
remain separate experimental results.

## Public references

- [jevos v5 release](https://github.com/feder-cr/jev/releases/tag/jevos-v5)
- [llama.cpp b12255 release](https://github.com/ggml-org/llama.cpp/releases/tag/b12255)
- [llama.cpp server API](https://github.com/ggml-org/llama.cpp/blob/b12255/tools/server/README.md)
