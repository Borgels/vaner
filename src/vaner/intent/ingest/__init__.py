# SPDX-License-Identifier: Apache-2.0
"""0.8.2 WS1 — intent-bearing artefact ingestion pipeline.

The package's seven-stage pipeline turns raw discovered artefacts into
persisted :class:`IntentArtefact` + :class:`IntentArtefactSnapshot` +
:class:`IntentArtefactItem` records:

1. Source discovery (connectors, implemented in :mod:`vaner.intent.connectors`)
2. Classifier gate (this package: :mod:`classifier`)
3. Extraction (this package: ``extract_markdown`` / ``extract_board`` /
   ``extract_github``)
4. Normalization (:mod:`normalize`)
5. Confidence scoring (the classifier itself returns the confidence; the
   pipeline combines it with recency + tier + structural richness)
6. Linking (pipeline: goal / artefact / file id resolution)
7. Persistence (pipeline + :class:`ArtefactStore`)

See the 0.8.2 release spec §7 for the full design.
"""
