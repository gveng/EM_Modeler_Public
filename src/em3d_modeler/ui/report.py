"""PDF project report generation using Qt's built-in PDF support."""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSizeF, QUrl
from PySide6.QtGui import QImage, QPageSize, QPainter, QPdfWriter, QTextDocument


def enabled_simulation_names(settings: dict[str, Any]) -> set[str]:
    simulations = settings.get("simulations", []) if isinstance(settings, dict) else []
    return {
        str(item.get("name", "")).strip()
        for item in simulations
        if isinstance(item, dict)
        and bool(item.get("enabled", True))
        and str(item.get("name", "")).strip()
    } if isinstance(simulations, list) else set()


def filter_enabled_simulation_plots(
    settings: dict[str, Any],
    plots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Exclude plots owned by disabled simulations or disabled outputs."""
    enabled_simulations = enabled_simulation_names(settings)
    outputs = settings.get("outputs", []) if isinstance(settings, dict) else []
    enabled_outputs = {
        f"{str(item.get('simulation', '')).strip()}::{str(item.get('name', 'Output')).strip() or 'Output'}"
        for item in outputs
        if isinstance(item, dict) and bool(item.get("enabled", True))
    } if isinstance(outputs, list) else set()
    return [
        plot for plot in plots
        if str(plot.get("simulation", "")).strip() in enabled_simulations
        and str(plot.get("key", "")).strip() in enabled_outputs
    ]


def touchstone_sources(
    series: Any,
    available_paths: list[str | Path] | None = None,
) -> list[dict[str, str]]:
    """Return sources for visible traces, resolving names against discovered files."""
    import re

    candidates = [Path(path) for path in (available_paths or [])]
    by_name = {path.name.casefold(): path for path in candidates}
    found: dict[tuple[str, str], dict[str, str]] = {}
    for item in series if isinstance(series, (list, tuple)) else ():
        if not isinstance(item, dict):
            continue
        # Chart data may retain every parameter from a source file while only
        # drawing the configured/visible traces. Hidden traces did not
        # contribute to the report image and must not create file references.
        if not bool(item.get("visible", True)):
            continue
        source = str(item.get("source_path", "")).strip()
        if not source:
            file_id = str(item.get("file_id", "")).strip()
            source = ""
            for part in reversed(file_id.split("::")):
                cleaned = re.split(r"::(?:append-run-|append-)", part, maxsplit=1)[0]
                if re.search(r"\.s\d+p$", Path(cleaned).name, re.I):
                    source = cleaned
                    break
        file_name = str(item.get("file_name", "")).strip()
        candidate = Path(source) if source else None
        if candidate is not None and re.search(r"\.s\d+p$", candidate.name, re.I):
            # A basename alone is not provenance. Resolve it only against the
            # discovered source list; explicit paths remain valid source ids.
            if not candidate.is_absolute() and candidate.parent == Path(".") and not candidate.exists():
                candidate = by_name.get(candidate.name.casefold())
            elif candidates and not candidate.exists():
                candidate = by_name.get(candidate.name.casefold()) or candidate
        else:
            named_path = Path(file_name) if file_name else None
            if (
                named_path is not None
                and re.search(r"\.s\d+p$", named_path.name, re.I)
                and (named_path.is_absolute() or named_path.parent != Path(".") or named_path.exists())
            ):
                candidate = named_path
            else:
                candidate = by_name.get(named_path.name.casefold()) if named_path is not None else None
        if candidate is None or not re.search(r"\.s\d+p$", candidate.name, re.I):
            continue
        path = str(candidate.resolve()) if candidate.exists() else str(candidate)
        trace = str(item.get("legend_label", item.get("label", "Trace")))
        found[(trace, path)] = {"trace": trace, "path": path, "name": candidate.name}
    return sorted(found.values(), key=lambda entry: (entry["trace"].casefold(), entry["path"].casefold()))


def touchstone_file_names(series: Any, available_paths: list[str | Path] | None = None) -> list[str]:
    return sorted(
        {entry["name"] for entry in touchstone_sources(series, available_paths)},
        key=str.casefold,
    )


def _display_value(value: Any) -> str:
    """Format nested project settings in a readable, deterministic form."""
    if isinstance(value, dict):
        return "; ".join(
            f"{key}: {_display_value(item)}"
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]).casefold())
        )
    if isinstance(value, (list, tuple)):
        if not value:
            return "(none)"
        return "; ".join(_display_value(item) for item in value)
    if value is None:
        return "(none)"
    if isinstance(value, bool):
        return "Enabled" if value else "Disabled"
    return str(value)


def _settings_table(title: str, settings: dict[str, Any]) -> str:
    if not settings:
        return f"<h2>{html.escape(title)}</h2><p>None</p>"
    rows = "".join(
        "<tr><th>{}</th><td>{}</td></tr>".format(
            html.escape(str(key)),
            html.escape(_display_value(value)),
        )
        for key, value in sorted(settings.items(), key=lambda pair: str(pair[0]).casefold())
    )
    return f"<h2>{html.escape(title)}</h2><table>{rows}</table>"


def _settings_html(settings: dict[str, Any]) -> str:
    """Show project settings except the verbose, separately reported port/output data."""
    settings = settings if isinstance(settings, dict) else {}
    sections = (
        ("Boundaries", ("boundaries", "open_region", "object_boundaries")),
        ("Simulation", ("simulation", "simulations")),
    )
    # Port details are shown next to each port image below. Output records can
    # be large and are represented by their enabled plots in the report.
    omitted = {"ports", "post", "post_processing", "outputs"}
    consumed: set[str] = set(omitted)
    content = ["<h1>Project Settings</h1>"]
    for title, keys in sections:
        section = {key: settings[key] for key in keys if key in settings}
        consumed.update(section)
        content.append(_settings_table(title, section))
    remaining = {key: value for key, value in settings.items() if key not in consumed}
    if remaining:
        content.append(_settings_table("Other Settings", remaining))
    return "".join(content)


def _port_report_settings(port: dict[str, Any]) -> dict[str, Any]:
    """Select concise, user-facing settings for one port."""
    result: dict[str, Any] = {}
    for key in ("number", "name", "type", "object"):
        if key in port and port[key] is not None and str(port[key]).strip():
            result[key.title() if key != "type" else "Type"] = port[key]

    coordinates = [port.get(axis) for axis in ("x", "y", "z")]
    if any(value is not None for value in coordinates):
        result["Position (x, y, z)"] = tuple(
            value if value is not None else 0 for value in coordinates
        )

    # These fields occur in alternate/legacy project formats. Keep them when
    # present, while omitting unrelated implementation metadata.
    for key in ("direction", "width", "height", "mode", "mode_type", "face_name"):
        if key in port and port[key] is not None:
            result[key.replace("_", " ").title()] = port[key]

    params = port.get("params")
    if isinstance(params, dict):
        result.update(params)
    return result


def _port_snapshots_html(
    port_snapshots: list[dict[str, Any]] | None,
    resources: list[tuple[str, QImage]],
) -> str:
    """Build project-grouped port sections with side-by-side settings and images."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for snapshot in port_snapshots or []:
        if isinstance(snapshot, dict):
            project = str(snapshot.get("project", "Untitled"))
            grouped.setdefault(project, []).append(snapshot)
    if not grouped:
        return ""

    body = ["<h1>Port Snapshots</h1>"]
    for project, snapshots in grouped.items():
        body.append(f"<h2>{html.escape(project)}</h2>")
        for index, snapshot in enumerate(snapshots, start=1):
            port = snapshot.get("port", {})
            port = port if isinstance(port, dict) else {}
            number = str(port.get("number", index))
            name = str(port.get("name", f"Port {number}"))
            body.append(f"<h3>Port {html.escape(number)} — {html.escape(name)}</h3>")
            image = snapshot.get("image")
            image_markup = "<p>No port snapshot is available.</p>"
            if isinstance(image, QImage) and not image.isNull():
                resource_name = f"report://port/{len(resources)}"
                resources.append((resource_name, image))
                image_markup = f'<img src="{resource_name}" width="300">'
            note = str(snapshot.get("note", "")).strip()
            if note:
                image_markup += f"<p>{html.escape(note)}</p>"
            settings = _port_report_settings(port)
            setting_rows = "".join(
                "<tr><th>{}</th><td>{}</td></tr>".format(
                    html.escape(str(key)),
                    html.escape(_display_value(value)),
                )
                for key, value in sorted(
                    settings.items(), key=lambda pair: str(pair[0]).casefold()
                )
            )
            settings_markup = (
                f"<table>{setting_rows}</table>" if setting_rows
                else "<p>No port settings are configured.</p>"
            )
            body.append(
                "<table class='port-snapshot'><tr>"
                f"<td width='45%'>{image_markup}</td>"
                f"<td width='55%'>{settings_markup}</td>"
                "</tr></table>"
            )
    return "".join(body)


def write_project_report(
    path: str | Path,
    *,
    project_name: str,
    settings: dict[str, Any],
    materials: list[str],
    model_image: QImage | None,
    plots: list[dict[str, Any]],
    port_snapshots: list[dict[str, Any]] | None = None,
) -> None:
    """Write a paginated PDF without introducing a third-party PDF dependency.

    Each plot mapping accepts ``simulation``, ``name``, ``image`` and
    ``touchstone_sources``. Port snapshot mappings accept ``project``, ``port``
    and ``image``. Images are embedded as Qt document resources, so the PDF is
    self-contained.
    """
    writer = QPdfWriter(str(Path(path)))
    writer.setTitle(f"{project_name} — Project Report")
    writer.setCreator("EM 3D Modeler")
    writer.setResolution(96)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))

    resources: list[tuple[str, QImage]] = []
    body = [
        "<h1>Project Report</h1>",
        f"<h2>{html.escape(project_name or 'Untitled')}</h2>",
    ]
    if model_image is not None and not model_image.isNull():
        resources.append(("report://model", model_image))
        body.extend(("<h2>Axonometric Model</h2>", '<img src="report://model" width="650">'))

    body.append("<h1>Materials</h1>")
    if materials:
        body.append("<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in materials) + "</ul>")
    else:
        body.append("<p>No materials are assigned to project objects.</p>")

    body.append(_settings_html(settings))
    body.append(_port_snapshots_html(port_snapshots, resources))

    grouped: dict[str, list[dict[str, Any]]] = {}
    for plot in plots:
        grouped.setdefault(str(plot.get("simulation", "Unassigned")), []).append(plot)
    body.append("<h1>Plots</h1>")
    if not grouped:
        body.append("<p>No plots are currently available.</p>")
    for simulation, entries in grouped.items():
        body.append(f"<h2>{html.escape(simulation)}</h2>")
        for index, plot in enumerate(entries):
            name = str(plot.get("name", f"Plot {index + 1}"))
            body.append(f"<h3>{html.escape(name)}</h3>")
            image = plot.get("image")
            if isinstance(image, QImage) and not image.isNull():
                resource_name = f"report://plot/{len(resources)}"
                resources.append((resource_name, image))
                body.append(f'<img src="{resource_name}" width="650">')
            trace_sources = plot.get("touchstone_sources", [])
            names = {
                str(item.get("name", Path(str(item.get("path", ""))).name))
                for item in trace_sources
                if isinstance(item, dict)
            }
            if names:
                body.append(
                    "<p><b>Touchstone files:</b> "
                    + html.escape(", ".join(sorted(names, key=str.casefold)))
                    + "</p>"
                )
            if trace_sources:
                body.append(
                    "<ul class='sources'>"
                    + "".join(
                        "<li>{}: {}</li>".format(
                            html.escape(str(item.get("trace", "Trace"))),
                            html.escape(str(item.get("path", item.get("name", "")))),
                        )
                        for item in trace_sources
                    )
                    + "</ul>"
                )
            elif plot.get("source"):
                body.append(f"<p><b>Source:</b> {html.escape(str(plot['source']))}</p>")

    document = QTextDocument()
    document.setDefaultStyleSheet(
        "body { font-family: sans-serif; font-size: 9pt; color: #20242a; }"
        "h1 { color: #174b70; font-size: 19pt; margin-top: 18pt; }"
        "h2 { color: #276b91; font-size: 14pt; margin-top: 14pt; }"
        "h3 { color: #333; font-size: 11pt; margin-top: 10pt; }"
        "table { border-collapse: collapse; width: 100%; }"
        "th, td { border: 1px solid #b8c2ca; padding: 4px; text-align: left; }"
        "th { background-color: #eaf0f4; width: 28%; }"
        "img { max-width: 100%; }"
    )
    document.setHtml(
        "<html><body style='margin: 42px'>" + "".join(body) + "</body></html>"
    )
    for name, image in resources:
        document.addResource(
            QTextDocument.ResourceType.ImageResource, QUrl(name), image
        )

    page_size = QSizeF(float(writer.width()), float(writer.height()))
    document.setPageSize(page_size)
    page_height = page_size.height()
    page_count = max(1, int((document.size().height() + page_height - 1) // page_height))
    painter = QPainter(writer)
    try:
        for page in range(page_count):
            if page:
                writer.newPage()
            painter.save()
            painter.translate(0, -page * page_height)
            painter.setClipRect(0, page * page_height, page_size.width(), page_height)
            document.drawContents(painter)
            painter.restore()
    finally:
        painter.end()
