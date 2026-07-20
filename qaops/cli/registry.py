"""Format-name to exporter mapping for the CLI.

A plain dict, not a plugin registry - the same "dict until a concrete
need justifies more" judgment as ADR-005. The CLI resolves the
configured/requested format names against this map.
"""

from qaops.exporters import CsvExporter, ExcelExporter, JsonExporter, MarkdownExporter

# Concrete exporter classes keyed by format name. mypy will not accept
# type[ConcreteExporter] where type[Exporter] (a Protocol) is expected, because
# a class object is not a structural subtype of the protocol type - only its
# instances are. An explicit union of the concrete classes keeps full type
# safety without weakening the annotation to Any.
_ExporterClass = (
    type[JsonExporter] | type[MarkdownExporter] | type[CsvExporter] | type[ExcelExporter]
)

EXPORTERS: dict[str, _ExporterClass] = {
    "json": JsonExporter,
    "markdown": MarkdownExporter,
    "csv": CsvExporter,
    "xlsx": ExcelExporter,
}


ExporterInstance = JsonExporter | MarkdownExporter | CsvExporter | ExcelExporter


def resolve_exporters(format_names: list[str]) -> list[ExporterInstance]:
    """Instantiate exporters for the given format names, preserving order.

    The return type is the concrete union rather than the Exporter protocol:
    each concrete `export` narrows its parameter to TestDesignResult, which is
    intentional (exporters only ever serialize that type) but means the classes
    are not structural subtypes of the looser protocol. All four nonetheless
    provide the protocol's members, and the CLI only calls those members.

    Raises:
        KeyError: if a name is not a known format. The CLI catches this
            and turns it into a friendly message.
    """
    unknown = [name for name in format_names if name not in EXPORTERS]
    if unknown:
        known = ", ".join(sorted(EXPORTERS))
        msg = f"Unknown export format(s): {unknown}. Known formats: {known}."
        raise KeyError(msg)
    return [EXPORTERS[name]() for name in format_names]
