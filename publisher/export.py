"""Export versioned report views through fixed read-only producer CLI commands.

No private database, private package import, model call, or upstream code copy is
needed. The public archive is replaced only after all producer reads validate.
"""

import argparse
from copy import deepcopy
from datetime import datetime, timezone, date
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import time
from uuid import UUID

from .contracts import (PublicationError, SCHEMA_VERSION, WATCHLIST, array, public_url,
                        references, require, text, timestamp, validate_publication)

MAX_VIEW_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
READ_TIMEOUT = 60
VIEWS = {"fundamental": ("FundamentalReportView", {"1.0.0"}),
         "news": ("NewsReportView", {"1.0.0"}),
         "social": ("SocialReportView", {"1.1.0"})}
REPORT_VERSIONS = {"fundamental": {"1.0.0"}, "news": {"1.0.0"},
                   "social": {"2.0.0", "3.0.0", "3.1.0", "3.2.0", "3.3.0"}}
SECTION_FIELDS = {
    "fundamental": ("executive_summary", "business_performance", "financial_health", "earnings_guidance", "risks_counter_evidence", "watchpoints"),
    "news": ("executive_summary", "macro", "industry", "stocks", "scenarios", "watchpoints"),
    "social": ("executive_summary", "stocks", "others"),
}


def read_view(executable, data_dir, kind, symbol=None):
    """Bound stdout in memory and time; discard private stderr diagnostics."""
    require(kind in VIEWS, "Unsupported producer command")
    args = [str(executable), f"{kind}-report-view", "--data-dir", str(data_dir)]
    if symbol is not None:
        args += ["--symbol", symbol]
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        chunks, size = [], 0
        deadline = time.monotonic() + READ_TIMEOUT
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    require(remaining > 0, "Producer read timed out")
                    for key, _ in selector.select(remaining):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            break
                        size += len(chunk)
                        require(size <= MAX_VIEW_BYTES, "Producer view exceeds size limit")
                        chunks.append(chunk)
            require(process.wait(timeout=max(0.01, deadline - time.monotonic())) == 0, "Producer read failed")
        except BaseException:
            process.kill()
            process.wait()
            raise
    try:
        return json.loads(b"".join(chunks))
    except (ValueError, UnicodeError):
        raise PublicationError("Producer returned invalid JSON") from None


def _paragraph(claim, source_ids):
    require(isinstance(claim, dict), "Invalid producer claim")
    role = claim["kind"]
    require(role in {"reported_fact", "source_claim", "inference"}, "Unsupported producer claim kind")
    statement = text(claim["statement"])
    ids = list(dict.fromkeys(text(item["source_id"]) for item in array(claim["citations"])))
    require(bool(ids), "Producer claim has no citations")
    references(ids, source_ids)
    return {"text": statement, "kind": {"inference": "interpretation", "reported_fact": "source_fact", "source_claim": "source_claim"}[role], "source_ids": ids}


def _sections(report, kind, source_ids, symbols):
    sections = report["sections"]
    require(isinstance(sections, dict) and set(sections) == set(SECTION_FIELDS[kind]), "Unsupported producer sections")
    result = []
    for key in SECTION_FIELDS[kind]:
        if key == "stocks":
            stocks = array(sections[key])
            require([item["symbol"] for item in stocks] == symbols, "Producer stock sections differ from watchlist")
            for stock in stocks:
                names = ("findings", "uncertainties", "watchpoints") if kind == "news" else ("findings", "disagreements", "research_questions")
                for name in names:
                    result.append({"heading": stock["symbol"] + " · " + name.replace("_", " ").title(),
                                   "paragraphs": [_paragraph(item, source_ids) for item in array(stock[name])]})
        else:
            result.append({"heading": key.replace("_", " ").title(),
                           "paragraphs": [_paragraph(item, source_ids) for item in array(sections[key])]})
    return result


def _public_limitations(report):
    """Project coverage states, never the producer's embedded diagnostic prose."""
    limitations = []
    omitted_diagnostic = False
    for item in array(report["limitations"]):
        value = text(item)
        if value.startswith(("Coverage ", "Best-effort ")):
            omitted_diagnostic = True
        else:
            limitations.append(value)
    providers = {"sec_submissions": "SEC filings", "sec_companyfacts": "SEC financial facts",
                 "issuer_ir": "issuer announcements", "yahoo_news": "news",
                 "reddit": "Reddit", "stocktwits": "Stocktwits", "hacker_news": "Hacker News",
                 "bluesky": "Bluesky", "twitterapi_io": "X"}
    symbols = {item["symbol"] for item in WATCHLIST}
    coverage_notes = []
    for row in array(report["coverage"]):
        state = row["status"]
        require(state in {"success", "success_empty", "partial", "unavailable", "rejected"}, "Unsupported producer coverage state")
        if state in {"success", "success_empty"} and not row.get("error_code"):
            continue
        subject = row["subject"] if row["subject"] in symbols else "configured sources"
        provider = providers.get(row["provider"], "a configured provider")
        status = {"partial": "partial", "unavailable": "unavailable", "rejected": "not admitted"}.get(state, "incomplete")
        coverage_notes.append(f"Coverage for {subject} from {provider} was {status}. Missing evidence does not establish no developments or neutral discussion.")
    if omitted_diagnostic and not coverage_notes:
        coverage_notes.append("Some configured source coverage was incomplete. Missing evidence does not establish no developments or neutral discussion.")
    return list(dict.fromkeys([*limitations, *coverage_notes]))


def project_view(view, kind, published_at, *, symbol=None):
    """Allowlist every emitted field; citation excerpts are deliberately omitted."""
    try:
        require(isinstance(view, dict), "Invalid producer view")
        contract, versions = VIEWS[kind]
        require(view["contract"] == contract and view["version"] in versions and view["producer"] == "zilnon-intelligence", "Unsupported producer view contract")
        if view["status"] == "empty":
            require(view["report"] is None, "Invalid empty producer view")
            return None
        require(view["status"] == "ready", "Producer report is unavailable")
        report = view["report"]
        require(report["status"] == "complete" and report["review_status"] == "unreviewed", "Producer report is not complete and unreviewed")
        require(report["contract_version"] in REPORT_VERSIONS[kind], "Unsupported producer report contract")
        identifier = str(UUID(report["report_id"]))
        symbols = [report["symbol"]] if kind == "fundamental" else array(report["watchlist"])
        allowed_symbols = {item["symbol"] for item in WATCHLIST}
        references(symbols, allowed_symbols)
        require(symbol is None or symbols == [symbol], "Producer report belongs to a different company")
        sources = []
        for source in array(report["sources"]):
            publisher = text(source["author"]) if kind == "social" else text(source["publisher"])
            title = f"{text(source['platform']).replace('_', ' ').title()} · {publisher}" if kind == "social" else text(source["title"])
            published_date = source.get("published_date")
            precision = source.get("publication_precision")
            if precision is None:
                precision = "timestamp" if source["published_at"] is not None else "date" if published_date is not None else None
            sources.append({"id": text(source["source_id"]), "title": title, "url": public_url(source["url"]),
                            "publisher": publisher, "published_at": source["published_at"],
                            "published_date": published_date, "publication_precision": precision})
        source_ids = {item["id"] for item in sources}
        sections = _sections(report, kind, source_ids, symbols)
        metrics = []
        for metric in array(report["metrics"] if kind == "fundamental" else []):
            require(metric["status"] in {"available", "unavailable"}, "Unknown metric state")
            value = "Unavailable"
            if metric["status"] == "available":
                value = text(metric["value_decimal"])
                require(Decimal(value).is_finite(), "Invalid metric number")
            else:
                require(metric["value_decimal"] is None, "Unavailable metric has a value")
            period = text(metric["period_type"])
            for key in ("period_start", "period_end"):
                if metric[key] is not None:
                    date.fromisoformat(metric[key])
            period += " · " + " to ".join(item for item in (metric["period_start"], metric["period_end"]) if item) if metric["period_start"] or metric["period_end"] else ""
            metrics.append({"label": text(metric["name"]), "value": value, "unit": text(metric["unit"]),
                            "period": period, "source_ids": list(references(metric["source_ids"], source_ids))})
        title = {"fundamental": "Fundamental & earnings", "news": "News & macro", "social": "Social intelligence"}[kind]
        if kind == "fundamental":
            title = symbols[0] + " · " + title
        result = {"id": kind + "-" + identifier, "kind": kind, "title": title, "symbols": list(symbols),
                  "as_of": report["as_of"], "generated_at": report["generated_at"], "published_at": published_at,
                  "status": "complete", "review_status": "unreviewed", "demo": False,
                  "summary": "\n\n".join(item["text"] for item in sections[0]["paragraphs"]),
                  "sections": sections, "metrics": metrics, "sources": sources,
                  "limitations": _public_limitations(report)}
        validate_publication({"schema_version": SCHEMA_VERSION, "published_at": published_at, "watchlist": WATCHLIST, "reports": [result]})
        return result
    except PublicationError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation):
        raise PublicationError("Malformed producer report view") from None


def export_publication(executable, data_dir, output, *, now=None, reader=read_view):
    """Accumulate immutable reports; failed reads leave the prior file unchanged."""
    output = Path(output)
    published_at = now or datetime.now(timezone.utc).isoformat()
    timestamp(published_at)
    existing = None
    if output.exists():
        require(output.stat().st_size <= MAX_ARCHIVE_BYTES, "Public archive exceeds size limit")
        try:
            existing = validate_publication(json.loads(output.read_bytes()))
        except (ValueError, KeyError, TypeError):
            raise PublicationError("Existing public archive is invalid") from None
        require(existing["watchlist"] == WATCHLIST, "Archive watchlist differs from publication scope")
    reports = {item["id"]: deepcopy(item) for item in existing["reports"]} if existing else {}
    for kind, symbol in [("fundamental", item["symbol"]) for item in WATCHLIST] + [("news", None), ("social", None)]:
        report = project_view(reader(executable, data_dir, kind, symbol), kind, published_at, symbol=symbol)
        if report is None:
            continue
        old = reports.get(report["id"])
        if old is not None:
            report["published_at"] = old["published_at"]
            require(report == old, "Published report identity changed content")
        else:
            reports[report["id"]] = report
    result = {"schema_version": SCHEMA_VERSION, "published_at": published_at, "watchlist": deepcopy(WATCHLIST),
              "reports": sorted(reports.values(), key=lambda item: (timestamp(item["generated_at"]), item["id"]), reverse=True)}
    if existing and existing["reports"] == result["reports"]:
        return existing
    validate_publication(result)
    payload = (json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    require(len(payload) <= MAX_ARCHIVE_BYTES, "Public archive exceeds size limit")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".publication-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intelligence-cli", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        require(all(path.is_absolute() for path in (args.intelligence_cli, args.data_dir, args.output)), "CLI, data directory and output paths must be absolute")
        result = export_publication(args.intelligence_cli, args.data_dir, args.output)
    except (PublicationError, OSError, subprocess.SubprocessError):
        print("Export failed; prior public archive preserved. Check producer availability and publication contract locally.", file=sys.stderr)
        return 1
    print(f"Public archive ready: {len(result['reports'])} reports")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
