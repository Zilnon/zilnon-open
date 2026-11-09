"""Validate the public boundary without importing any private product code."""

from datetime import date, datetime
import ipaddress
import re
from urllib.parse import parse_qsl, urlsplit

SCHEMA_VERSION = "1.0.0"
WATCHLIST = [
    {"symbol": "SNDK", "name": "Sandisk"},
    {"symbol": "MU", "name": "Micron Technology"},
    {"symbol": "AMD", "name": "Advanced Micro Devices"},
    {"symbol": "CRDO", "name": "Credo Technology"},
    {"symbol": "BE", "name": "Bloom Energy"},
]
KINDS = {"fundamental", "news", "social"}
PARAGRAPH_KINDS = {"source_fact", "source_claim", "interpretation", "uncertainty", "research_question", "summary"}


class PublicationError(ValueError):
    """Safe diagnostic; never attach producer payloads or private paths."""


def require(condition, message):
    if not condition:
        raise PublicationError(message)


def text(value, *, empty=False):
    require(isinstance(value, str) and (empty or bool(value.strip())), "Invalid public text")
    return value


def timestamp(value):
    text(value)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(parsed.tzinfo is not None and parsed.utcoffset() is not None, "Timestamp needs timezone")
        return parsed
    except ValueError:
        raise PublicationError("Invalid publication timestamp") from None


def public_url(value):
    text(value)
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower().rstrip(".")
        require(parts.scheme == "https" and host and not parts.username and not parts.password,
                "Source requires a public HTTPS URL")
        require(not re.search(r"[\s\\\x00-\x1f]", value) and parts.port in {None, 443}, "Unsafe source URL")
        require("." in host and not host.endswith((".local", ".internal", ".localhost", ".lan", ".home", ".test")),
                "Private source URL is not publishable")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        require(address is None or address.is_global, "Private source URL is not publishable")
        require(not any(re.search(r"token|secret|password|credential|signature|api.?key|authorization", key, re.I)
                        for key, _ in parse_qsl(parts.query)), "Credential-bearing source URL is not publishable")
    except ValueError:
        raise PublicationError("Unsafe source URL") from None
    return value


def fields(value, expected):
    require(isinstance(value, dict) and set(value) == set(expected.split()), "Unexpected public object fields")


def array(value):
    require(isinstance(value, list), "Expected publication array")
    return value


def references(value, source_ids):
    for identifier in array(value):
        text(identifier)
        require(identifier in source_ids, "Citation references an absent source")
    require(len(value) == len(set(value)), "Duplicate citation reference")
    return value


def validate_publication(value):
    """Reject extra keys at every level, including archived public reports."""
    fields(value, "schema_version published_at watchlist reports")
    require(value["schema_version"] == SCHEMA_VERSION, "Unsupported publication schema")
    timestamp(value["published_at"])
    symbols = set()
    for stock in array(value["watchlist"]):
        fields(stock, "symbol name")
        text(stock["name"])
        require(isinstance(stock["symbol"], str) and re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", stock["symbol"]), "Invalid symbol")
        require(stock["symbol"] not in symbols, "Duplicate watchlist symbol")
        symbols.add(stock["symbol"])
    report_ids = set()
    for report in array(value["reports"]):
        fields(report, "id kind title symbols as_of generated_at published_at status review_status demo summary sections metrics sources limitations")
        require(report["kind"] in KINDS, "Invalid report kind")
        require(isinstance(report["id"], str) and re.fullmatch(r"(?:fundamental|news|social)-[a-z0-9-]+", report["id"]), "Invalid report identity")
        require(report["id"].startswith(report["kind"] + "-"), "Report identity kind differs")
        require(report["id"] not in report_ids, "Duplicate report identity")
        report_ids.add(report["id"])
        require(report["status"] == "complete" and report["review_status"] == "unreviewed", "Unsupported report state")
        require(type(report["demo"]) is bool, "Invalid demo status")
        text(report["title"])
        text(report["summary"], empty=True)
        for key in ("as_of", "generated_at", "published_at"):
            timestamp(report[key])
        require(timestamp(report["as_of"]) <= timestamp(report["generated_at"]) <= timestamp(report["published_at"]), "Report timestamps are out of order")
        references(report["symbols"], symbols)
        source_ids = set()
        for source in array(report["sources"]):
            fields(source, "id title url publisher published_at published_date publication_precision")
            for key in ("id", "title", "publisher"):
                text(source[key])
            require(source["id"] not in source_ids, "Duplicate source identity")
            source_ids.add(source["id"])
            public_url(source["url"])
            if source["published_at"] is not None:
                timestamp(source["published_at"])
            if source["published_date"] is not None:
                value = source["published_date"]
                require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), "Invalid source publication date")
                try:
                    date.fromisoformat(value)
                except ValueError:
                    raise PublicationError("Invalid source publication date") from None
            precision = source["publication_precision"]
            require(precision in {None, "date", "timestamp"}, "Unknown source publication precision")
            if precision == "date":
                require(source["published_date"] is not None and source["published_at"] is None, "Date-only publication must not invent a timestamp")
            elif precision == "timestamp":
                require(source["published_at"] is not None, "Timestamp publication needs an instant")
            else:
                require(source["published_at"] is None and source["published_date"] is None, "Unknown precision requires unknown publication time")
        for section in array(report["sections"]):
            fields(section, "heading paragraphs")
            text(section["heading"])
            for paragraph in array(section["paragraphs"]):
                fields(paragraph, "text kind source_ids")
                text(paragraph["text"])
                require(paragraph["kind"] in PARAGRAPH_KINDS, "Unknown paragraph kind")
                references(paragraph["source_ids"], source_ids)
        for metric in array(report["metrics"]):
            fields(metric, "label value unit period source_ids")
            for key in ("label", "value", "unit", "period"):
                text(metric[key])
            references(metric["source_ids"], source_ids)
        for limitation in array(report["limitations"]):
            text(limitation)
    return value
