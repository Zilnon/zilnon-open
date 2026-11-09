"""Original synthetic contract fixtures; no private reports or upstream code."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from publisher.contracts import PublicationError, WATCHLIST, public_url, validate_publication
from publisher.export import export_publication, project_view, read_view

NOW = "2026-11-09T12:00:00Z"
LATER = "2026-11-10T12:00:00Z"


def claim(kind="source_claim"):
    return {"claim_id": "synthetic-claim", "kind": kind,
            "statement": "Synthetic issuer commentary requires independent verification.",
            "citations": [{"source_id": "synthetic-source", "excerpt": "DO_NOT_PUBLISH_LITERAL_EXCERPT"}],
            "metric_ids": [], "private_note": "DO_NOT_PUBLISH_PRIVATE_NOTE"}


def view(kind, symbol="SNDK", identifier="00000000-0000-4000-8000-000000000001"):
    source = {"source_id": "synthetic-source", "url": "https://example.com/synthetic-source", "title": "Synthetic evidence",
              "publisher": "Synthetic publisher", "author": "Synthetic author", "platform": "reddit", "published_at": NOW,
              "content": "DO_NOT_PUBLISH_RAW_CONTENT", "private_path": "/private/DO_NOT_PUBLISH_PATH"}
    if kind == "fundamental":
        sections = {name: [claim()] for name in ("executive_summary", "business_performance", "financial_health", "earnings_guidance", "risks_counter_evidence", "watchpoints")}
    elif kind == "news":
        sections = {name: [claim("inference")] for name in ("executive_summary", "macro", "industry", "scenarios", "watchpoints")}
        sections["stocks"] = [{"symbol": symbol, "status": "assessed", "findings": [claim()], "uncertainties": [claim("inference")], "watchpoints": [claim()]}]
    else:
        sections = {"executive_summary": [claim()], "others": [claim()], "stocks": [{"symbol": symbol, "findings": [claim()], "disagreements": [claim()], "research_questions": [claim("inference")]}]}
    report = {"report_id": identifier, "contract_version": "3.3.0" if kind == "social" else "1.0.0",
              "status": "complete", "review_status": "unreviewed", "symbol": symbol, "watchlist": [symbol],
              "as_of": NOW, "generated_at": NOW, "sources": [source], "sections": sections, "coverage": [],
              "limitations": ["Synthetic limitations for test evidence."], "model_requested": "DO_NOT_PUBLISH_MODEL",
              "prompt": "DO_NOT_PUBLISH_PROMPT", "account": "DO_NOT_PUBLISH_ACCOUNT",
              "review_notes": "DO_NOT_PUBLISH_REVIEW", "metrics": [{"name": "Synthetic revenue", "status": "available", "value_decimal": "123.4500", "unit": "USD", "period_type": "annual", "period_start": "2025-01-01", "period_end": "2025-12-31", "source_ids": ["synthetic-source"], "inputs": [{"source_path": "DO_NOT_PUBLISH_METRIC_PATH"}]}]}
    return {"contract": kind.title() + "ReportView", "version": "1.1.0" if kind == "social" else "1.0.0", "producer": "zilnon-intelligence", "status": "ready", "report": report, "latest_check": {"diagnostic": "DO_NOT_PUBLISH_DIAGNOSTIC"}}


def empty(kind):
    result = view(kind)
    result.update(status="empty", report=None)
    return result


class ExportTests(unittest.TestCase):
    def test_all_categories_preserve_sections_and_citations_without_private_fields(self):
        for kind, section_count in (("fundamental", 6), ("news", 8), ("social", 5)):
            with self.subTest(kind=kind):
                result = project_view(view(kind), kind, NOW)
                self.assertEqual(len(result["sections"]), section_count)
                self.assertEqual(result["sections"][0]["paragraphs"][0]["source_ids"], ["synthetic-source"])
                self.assertNotIn("DO_NOT_PUBLISH", json.dumps(result))
                self.assertEqual(result["review_status"], "unreviewed")
                self.assertFalse(result["demo"])
        result = project_view(view("fundamental"), "fundamental", NOW)
        self.assertEqual(result["metrics"][0]["value"], "123.4500")
        self.assertEqual(result["metrics"][0]["period"], "annual · 2025-01-01 to 2025-12-31")
        self.assertEqual(result["sections"][0]["paragraphs"][0]["kind"], "source_claim")
        result = project_view(view("news"), "news", NOW)
        self.assertEqual(result["sections"][0]["paragraphs"][0]["kind"], "interpretation")

    def test_source_dates_preserve_date_only_and_unknown_precision(self):
        item = view("news")
        source = item["report"]["sources"][0]
        source.update(published_at=None, published_date="2026-11-08", publication_precision="date")
        result = project_view(item, "news", NOW)["sources"][0]
        self.assertEqual((result["published_at"], result["published_date"], result["publication_precision"]),
                         (None, "2026-11-08", "date"))
        source.update(published_date=None, publication_precision=None)
        result = project_view(item, "news", NOW)["sources"][0]
        self.assertEqual((result["published_at"], result["published_date"], result["publication_precision"]), (None, None, None))
        source.update(published_at=NOW)
        result = project_view(item, "news", NOW)["sources"][0]
        self.assertEqual(result["publication_precision"], "timestamp")
        for invalid in ({"published_at": NOW, "published_date": "2026-11-08", "publication_precision": "date"},
                        {"published_at": None, "published_date": "2026-02-30", "publication_precision": "date"},
                        {"published_at": None, "published_date": None, "publication_precision": "timestamp"}):
            source.update(invalid)
            with self.assertRaises(PublicationError):
                project_view(item, "news", NOW)

    def test_coverage_diagnostics_become_public_gaps_without_private_prose(self):
        for kind, prefix in (("news", "Coverage"), ("social", "Best-effort")):
            item = view(kind)
            item["report"]["limitations"].extend([
                f"{prefix} reddit / MU: unavailable (DO_NOT_PUBLISH_ERROR /private/account).",
                "Captured discussion is a bounded sample.",
            ])
            item["report"]["coverage"] = [
                {"provider": "reddit", "subject": "MU", "status": "unavailable", "error_code": "DO_NOT_PUBLISH_ERROR"},
                {"provider": "DO_NOT_PUBLISH_PROVIDER", "subject": "DO_NOT_PUBLISH_SUBJECT", "status": "partial", "error_code": "private diagnostic"},
                {"provider": "reddit", "subject": "AMD", "status": "success_empty", "error_code": None},
            ]
            result = project_view(item, kind, NOW)
            self.assertNotIn("DO_NOT_PUBLISH", json.dumps(result))
            self.assertNotIn("private diagnostic", json.dumps(result))
            self.assertIn("Captured discussion is a bounded sample.", result["limitations"])
            self.assertTrue(any("MU from Reddit was unavailable" in value for value in result["limitations"]))
            self.assertTrue(any("configured sources from a configured provider was partial" in value for value in result["limitations"]))
            self.assertFalse(any("AMD" in value for value in result["limitations"]))
        item["report"]["coverage"] = []
        result = project_view(item, kind, NOW)
        self.assertTrue(any("Some configured source coverage was incomplete" in value for value in result["limitations"]))

    def test_unsafe_source_urls_rejected(self):
        for url in ("http://example.com/a", "https://user:pass@example.com/a", "https://localhost/a", "https://10.1.2.3/a", "https://127.0.0.1/a", "https://host.internal/a", "https://example.com/a?api_key=private", "https://example.com:8443/a"):
            with self.subTest(url=url), self.assertRaises(PublicationError):
                public_url(url)
        self.assertEqual(public_url("https://example.com/a?article=123"), "https://example.com/a?article=123")

    def test_unknown_versions_missing_sources_invalid_status_and_wrong_company_fail(self):
        mutations = [lambda v: v.update(version="9.0.0"),
                     lambda v: v["report"].update(status="partial"),
                     lambda v: v["report"].update(review_status="accepted"),
                     lambda v: v["report"].update(sources=[]),
                     lambda v: v["report"].update(symbol="MU"),
                     lambda v: v["report"]["sections"]["executive_summary"][0].update(kind="unknown"),
                     lambda v: v["report"].update(generated_at="invalid")]
        for mutation in mutations:
            item = view("fundamental")
            mutation(item)
            with self.assertRaises(PublicationError):
                project_view(item, "fundamental", NOW, symbol="SNDK")

    def test_archive_accumulates_and_repeat_preserves_bytes_and_publication_time(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "publication.json"
            reader = lambda exe, data, kind, symbol: view(kind) if kind == "news" else empty(kind)
            first = export_publication("unused", "unused", output, now=NOW, reader=reader)
            before = output.read_bytes()
            repeated = export_publication("unused", "unused", output, now=LATER, reader=reader)
            self.assertEqual(first, repeated)
            self.assertEqual(output.read_bytes(), before)
            newer = view("news", identifier="00000000-0000-4000-8000-000000000002")
            newer["report"].update(as_of=LATER, generated_at=LATER)
            second_reader = lambda exe, data, kind, symbol: newer if kind == "news" else empty(kind)
            second = export_publication("unused", "unused", output, now=LATER, reader=second_reader)
            self.assertEqual(len(second["reports"]), 2)
            self.assertEqual(second["reports"][1]["published_at"], NOW)
            self.assertEqual(second["published_at"], LATER)

    def test_failed_read_or_changed_identity_preserves_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "publication.json"
            reader = lambda exe, data, kind, symbol: view(kind) if kind == "news" else empty(kind)
            export_publication("unused", "unused", output, now=NOW, reader=reader)
            before = output.read_bytes()
            def broken(exe, data, kind, symbol):
                if kind == "social":
                    raise PublicationError("Producer read failed")
                return reader(exe, data, kind, symbol)
            with self.assertRaises(PublicationError):
                export_publication("unused", "unused", output, now=LATER, reader=broken)
            changed = view("news")
            changed["report"]["limitations"].append("Changed content")
            with self.assertRaises(PublicationError):
                export_publication("unused", "unused", output, now=LATER, reader=lambda e, d, k, s: changed if k == "news" else empty(k))
            self.assertEqual(output.read_bytes(), before)

    def test_archive_nested_extras_are_rejected(self):
        report = project_view(view("social"), "social", NOW)
        publication = {"schema_version": "1.0.0", "published_at": NOW, "watchlist": deepcopy(WATCHLIST), "reports": [report]}
        validate_publication(publication)
        report["sources"][0]["private_field"] = "secret"
        with self.assertRaises(PublicationError):
            validate_publication(publication)

    def test_fixed_read_command_and_bounded_process_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "producer"
            executable.write_text("#!/usr/bin/env python3\nimport json,sys\nassert sys.argv[1:] == ['news-report-view', '--data-dir', '/synthetic']\nprint(json.dumps({'status':'empty'}))\n")
            executable.chmod(0o700)
            self.assertEqual(read_view(executable, "/synthetic", "news"), {"status": "empty"})
            executable.write_text("#!/usr/bin/env python3\nprint('x'*1000)\n")
            with patch("publisher.export.MAX_VIEW_BYTES", 100), self.assertRaises(PublicationError):
                read_view(executable, "/synthetic", "news")
            executable.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(1)\n")
            with patch("publisher.export.READ_TIMEOUT", .02), self.assertRaises(PublicationError):
                read_view(executable, "/synthetic", "news")


if __name__ == "__main__":
    unittest.main()
