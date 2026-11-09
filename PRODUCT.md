# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Astro and TypeScript, accepted in the architecture discussion. Build a static site locally and publish its public snapshots to Cloudflare Pages. Python standard-library tools adapt existing producer contracts.

## Users

Readers following Zilnon's public US-equity watchlist and the owner checking the daily research produced by the private system.

## Product Purpose

Make existing Fundamental, News/Macro and Social reports easy to read, compare and revisit. Readers can follow citations and distinguish the evidence cutoff from publication time and review status.

## Operating Context

The private laptop generates research. A local exporter selects public fields and the publishing process uploads static assets. Visitors never contact the laptop, trigger inference or receive broker access. The initial watchlist is SNDK, MU, AMD, CRDO and BE.

## Capabilities and Constraints

- Latest briefing, watchlist, company pages, full report pages, searchable archive and methodology.
- Complete reports remain unreviewed; publication does not establish predictive accuracy or strategy acceptance.
- Failed exports leave the previous publication intact. Report cutoff, generation and publication timestamps stay distinct.
- Account details, personal portfolio values, source excerpts, private notes, credentials and raw source bodies are excluded.
- Source-only checkout uses explicitly labelled synthetic examples. Actual publication data is generated locally and excluded from Git.
- No live execution, public research trigger, new analyst workflow or private-system database access.

## Brand Commitments

Zilnon Open. The user requested a clean, clear website. The content is investment research rather than promotional performance claims.

## Evidence on Hand

The private system produces versioned FundamentalReportView, NewsReportView and SocialReportView contracts. These are input adapters, not automatic public-release approval for their full contents. No third-party product implementation is copied into this repository.

## Product Principles

- Let the research be the main content.
- Preserve uncertainty, source attribution and actual dates.
- Keep the public reading experience independent of laptop uptime.
- Keep financial and execution authority in the private producers.

## Accessibility & Inclusion

Responsive reading layouts, semantic headings and links, visible keyboard focus, sufficient contrast, and reduced-motion support.
