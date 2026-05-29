# Evaluation Result Artifacts

This directory contains curated, compact result summaries that are intended to
be portable across machines.

The first public release does not publish raw local transcripts, raw benchmark
patches, or full evaluator logs by default. Those artifacts can contain
machine-specific paths, benchmark details that need provenance review, or model
outputs that are too noisy for the public evidence layer.

Publishable artifacts should favor:

- compact JSON summaries
- run manifests with private paths redacted where practical
- methodology notes
- aggregate metrics
- explicit caveats about contamination, benchmark validity, and confidence

Raw logs and patches should be reviewed and deliberately promoted before they
are committed.
