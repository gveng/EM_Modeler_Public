"""Pure-data helpers for S-parameter charts."""

import math
import re
from numbers import Number, Real


_SPARAMETER_PLOT_TYPES = {"plot_sp", "plot_vswr", "smith", "plot"}
_SPARAMETER_PATTERN = re.compile(r"S(\d+)[,:/_-]?(\d+)", re.IGNORECASE)


def _parameter_selection(output):
    params = output.get("params")
    params = params if isinstance(params, dict) else {}

    raw_parameters = params.get("s_parameters", output.get("s_parameters", []))
    if not isinstance(raw_parameters, list) or not raw_parameters:
        raw_parameters = [
            params.get("s_parameter", output.get("s_parameter", "S11"))
        ]

    selected = []
    for raw_parameter in raw_parameters:
        parameter = str(raw_parameter).strip().upper()
        if not parameter:
            continue
        match = _SPARAMETER_PATTERN.fullmatch(parameter)
        if match:
            output_port, input_port = int(match.group(1)), int(match.group(2))
        else:
            try:
                output_port = int(params.get("port_i", output.get("port_i", 1)))
                input_port = int(params.get("port_j", output.get("port_j", 1)))
            except (TypeError, ValueError) as exc:
                raise ValueError("legacy S-parameter port indices must be integers") from exc
            parameter = f"S{output_port}{input_port}"

        if output_port < 1 or input_port < 1:
            raise ValueError(f"{parameter} port indices must be positive")
        item = (output_port, input_port, f"S{output_port}{input_port}")
        if item not in selected:
            selected.append(item)

    if not selected:
        raise ValueError("at least one S-parameter must be selected")
    return selected


def _as_sequence(value, description):
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{description} must be a sequence")
    try:
        return list(value)
    except TypeError as exc:
        raise ValueError(f"{description} must be a sequence") from exc


def _complex_value(value):
    if isinstance(value, Number):
        parsed = complex(value)
    else:
        pair = _as_sequence(value, "S-matrix entry")
        if len(pair) != 2 or any(not isinstance(component, Real) for component in pair):
            raise ValueError("S-matrix entries must be complex values or real/imaginary pairs")
        parsed = complex(pair[0], pair[1])
    if not math.isfinite(parsed.real) or not math.isfinite(parsed.imag):
        raise ValueError("S-matrix entries must be finite")
    return parsed


def make_sparameter_plot_data(
    output: dict,
    frequencies,
    s_matrices,
    *,
    include_all_parameters: bool = False,
    file_name: str = "",
    file_id: str = "",
    selected_parameters=None,
) -> dict:
    """Build chart-ready series while preserving Sij output/input orientation."""
    frequency_values = _as_sequence(frequencies, "frequencies")
    matrices = _as_sequence(s_matrices, "S-matrices")
    if not frequency_values or len(frequency_values) != len(matrices):
        raise ValueError("frequency and S-matrix counts must match and be non-empty")

    x_values = []
    for frequency in frequency_values:
        if not isinstance(frequency, Real) or not math.isfinite(float(frequency)):
            raise ValueError("frequencies must be finite real values")
        x_values.append(float(frequency) / 1e9)

    parsed_matrices = []
    matrix_size = None
    for raw_matrix in matrices:
        rows = _as_sequence(raw_matrix, "S-matrix")
        if not rows:
            raise ValueError("S-matrices must be non-empty and square")
        matrix = []
        for raw_row in rows:
            row = _as_sequence(raw_row, "S-matrix row")
            if len(row) != len(rows):
                raise ValueError("S-matrices must be square")
            matrix.append([_complex_value(value) for value in row])
        if matrix_size is None:
            matrix_size = len(matrix)
        elif len(matrix) != matrix_size:
            raise ValueError("S-matrix dimensions must remain constant across frequencies")
        parsed_matrices.append(matrix)

    configured = _parameter_selection(output)
    requested = (
        {str(parameter).strip().upper() for parameter in selected_parameters}
        if selected_parameters is not None
        else {label for _, _, label in configured}
    )
    available = [
        (output_port, input_port, f"S{output_port}{input_port}")
        for output_port in range(1, matrix_size + 1)
        for input_port in range(1, matrix_size + 1)
    ]
    invalid = [
        label for _, _, label in configured
        if label not in {item[2] for item in available}
    ]
    if invalid and not include_all_parameters:
        raise ValueError(
            f"{invalid[0]} is outside the available {matrix_size}-port S-matrix"
        )
    selected = available if include_all_parameters else [
        item for item in configured if item[2] in requested
    ]
    configured_labels = requested.intersection(item[2] for item in available)
    if include_all_parameters:
        selected = available

    plot_type = str(output.get("plot_type", "plot_sp")).strip().lower() or "plot_sp"
    name = str(output.get("name", "S-parameter Plot")).strip() or "S-parameter Plot"
    series = [
        {
            "label": label,
            "values": [matrix[output_port - 1][input_port - 1] for matrix in parsed_matrices],
        }
        for output_port, input_port, label in selected
    ]
    if include_all_parameters:
        for item in series:
            item["visible"] = item["label"] in configured_labels
            item["configured"] = item["visible"]
            item["file_name"] = file_name or name
            item["file_id"] = file_id or file_name or name

    return {
        "title": name,
        "plot_type": plot_type,
        "xlabel": "Frequency (GHz)",
        "ylabel": "S-parameter",
        "x_values": x_values,
        "series": series,
    }


def make_parametric_sparameter_plot_data(
    output: dict,
    scalar_data,
    parameter_name: str,
    parameter_values,
    *,
    expected_frequency_count: int,
    simulation_name: str,
) -> dict:
    """Build one chart series set per parameter value from ungridded EMERGE data."""
    variables = _as_sequence(getattr(scalar_data, "_variables", []), "simulation variables")
    entries = _as_sequence(getattr(scalar_data, "_data_entries", []), "simulation entries")
    values = [str(value).strip() for value in parameter_values if str(value).strip()]
    if not values:
        values = ["default"]
    parameter_name = str(parameter_name).strip()
    expected_frequency_count = int(expected_frequency_count)
    if expected_frequency_count < 1 or len(variables) != len(entries):
        raise ValueError("parametric S-parameter data is incomplete")

    groups = []
    if parameter_name and any(
        isinstance(variable, dict) and parameter_name in variable
        for variable in variables
    ):
        for value in values:
            try:
                numeric_value = float(value)
                matches = [
                    (variable, entry)
                    for variable, entry in zip(variables, entries)
                    if isinstance(variable, dict)
                    and parameter_name in variable
                    and math.isclose(
                        float(variable[parameter_name]),
                        numeric_value,
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    )
                ]
            except (TypeError, ValueError):
                matches = [
                    (variable, entry)
                    for variable, entry in zip(variables, entries)
                    if isinstance(variable, dict)
                    and str(variable.get(parameter_name, "")).strip() == value
                ]
            if matches:
                groups.append((value, matches))

    if not groups:
        required_count = len(values) * expected_frequency_count
        if len(entries) != required_count:
            raise ValueError(
                f"expected {required_count} parametric S-parameter samples, found {len(entries)}"
            )
        groups = [
            (
                value,
                list(zip(
                    variables[index * expected_frequency_count:(index + 1) * expected_frequency_count],
                    entries[index * expected_frequency_count:(index + 1) * expected_frequency_count],
                )),
            )
            for index, value in enumerate(values)
        ]

    if len(groups) != len(values):
        raise ValueError("parametric S-parameter data is missing one or more parameter values")

    all_series = []
    chart_data = None
    output_name = str(output.get("name", "S-parameter Plot"))
    for index, (value, samples) in enumerate(groups, start=1):
        if len(samples) != expected_frequency_count:
            raise ValueError(
                f"parameter value {value} has {len(samples)} samples; expected {expected_frequency_count}"
            )
        samples.sort(key=lambda sample: float(sample[1].freq))
        frequencies = [float(entry.freq) for _, entry in samples]
        if any(right <= left for left, right in zip(frequencies, frequencies[1:])):
            raise ValueError(f"parameter value {value} has duplicate or unordered frequencies")
        matrices = [entry.Sp for _, entry in samples]
        value_data = make_sparameter_plot_data(
            output,
            frequencies,
            matrices,
            include_all_parameters=True,
            file_name=f"{parameter_name}={value}" if parameter_name else value,
            file_id=f"{simulation_name}::{output_name}::{index}",
        )
        if chart_data is None:
            chart_data = value_data
        all_series.extend(value_data["series"])

    chart_data["title"] = output_name
    chart_data["series"] = all_series
    return chart_data


def supports_live_plot(output: dict, simulation: dict) -> bool:
    """Return whether an output can consume progressive sweep samples."""
    plot_mode = str(output.get("plot_mode", "final")).strip().lower()
    simulation_type = str(simulation.get("type", "")).strip().lower()
    plot_type = str(output.get("plot_type", "plot_sp")).strip().lower()
    return (
        bool(output.get("enabled", True))
        and plot_mode == "live"
        and simulation_type in {"sweep", "parametric"}
        and bool(simulation.get("progressive_sparams_enabled", False))
        and plot_type in _SPARAMETER_PLOT_TYPES
    )