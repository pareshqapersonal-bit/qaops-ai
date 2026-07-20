"""Reporting & export framework.

Implements the Phase 0 Exporter protocol. Exporters consume a
TestDesignResult and write an artifact; they never mutate the input and
never call an LLM. JSON is the canonical serialization from which the
other formats derive their shape (ADR-016).

JsonExporter and MarkdownExporter and CsvExporter have no third-party
dependencies. ExcelExporter needs the optional 'excel' extra
(pip install qaops-ai[excel]).
"""

from qaops.exporters.csv_exporter import CsvExporter
from qaops.exporters.excel_exporter import ExcelExporter
from qaops.exporters.json_exporter import JsonExporter
from qaops.exporters.markdown_exporter import MarkdownExporter

__all__ = [
    "CsvExporter",
    "ExcelExporter",
    "JsonExporter",
    "MarkdownExporter",
]
