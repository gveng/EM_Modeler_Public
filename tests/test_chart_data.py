from types import SimpleNamespace

import pytest

from em3d_modeler.ui.chart_data import (
    make_parametric_sparameter_plot_data,
    make_sparameter_plot_data,
    supports_live_plot,
)


def test_make_sparameter_plot_data_preserves_output_input_orientation():
    output = {
        "name": "Insertion and return loss",
        "plot_type": "plot_sp",
        "params": {"s_parameters": ["S21", "S12"]},
    }
    matrices = [
        [[[1, 0], [2, 3]], [[4, 5], [6, 0]]],
        [[[7, 0], [8, 9]], [[10, 11], [12, 0]]],
    ]

    result = make_sparameter_plot_data(output, [1e9, 2e9], matrices)

    assert result == {
        "title": "Insertion and return loss",
        "plot_type": "plot_sp",
        "xlabel": "Frequency (GHz)",
        "ylabel": "S-parameter",
        "x_values": [1.0, 2.0],
        "series": [
            {"label": "S21", "values": [complex(4, 5), complex(10, 11)]},
            {"label": "S12", "values": [complex(2, 3), complex(8, 9)]},
        ],
    }


def test_make_sparameter_plot_data_includes_unselected_parameters_for_controls():
    result = make_sparameter_plot_data(
        {"name": "Configured output", "params": {"s_parameters": ["S21"]}},
        [1e9],
        [[[[1, 0], [2, 0]], [[3, 0], [4, 0]]]],
        include_all_parameters=True,
        file_name="run.s2p",
        file_id="run-id",
    )

    assert [item["label"] for item in result["series"]] == ["S11", "S12", "S21", "S22"]
    assert [item["visible"] for item in result["series"]] == [False, False, True, False]
    assert {item["file_name"] for item in result["series"]} == {"run.s2p"}
    assert {item["file_id"] for item in result["series"]} == {"run-id"}


def test_make_parametric_sparameter_plot_data_groups_raw_entries_by_parameter_value():
    frequencies = [1e9, 2e9, 1e9, 2e9]
    values = [0.2, 0.2, 0.4, 0.4]
    scalar_data = SimpleNamespace(
        _variables=[
            {"freq": frequency, "width": value}
            for frequency, value in zip(frequencies, values)
        ],
        _data_entries=[
            SimpleNamespace(
                freq=frequency,
                Sp=[[complex(index, 0.5), complex(index + 1, 0.5)],
                    [complex(index + 2, 0.5), complex(index + 3, 0.5)]],
            )
            for index, frequency in enumerate(frequencies, start=1)
        ],
    )

    result = make_parametric_sparameter_plot_data(
        {
            "name": "Parametric S-parameters",
            "plot_type": "plot_sp",
            "params": {"s_parameters": ["S21"]},
        },
        scalar_data,
        "width",
        ["0.2", "0.4"],
        expected_frequency_count=2,
        simulation_name="Simulation_2",
    )

    assert result["x_values"] == [1.0, 2.0]
    assert [item["file_name"] for item in result["series"]] == [
        "width=0.2", "width=0.2", "width=0.2", "width=0.2",
        "width=0.4", "width=0.4", "width=0.4", "width=0.4",
    ]
    assert [item["values"] for item in result["series"] if item["label"] == "S21"] == [
        [3 + 0.5j, 4 + 0.5j], [5 + 0.5j, 6 + 0.5j],
    ]
    assert [item["visible"] for item in result["series"] if item["label"] == "S21"] == [
        True, True,
    ]


def test_make_sparameter_plot_data_intersects_requested_parameters_with_ports():
    result = make_sparameter_plot_data(
        {"params": {"s_parameters": ["S11", "S21"]}},
        [1e9],
        [[[[1, 0]]]],
        include_all_parameters=True,
    )

    assert [item["label"] for item in result["series"]] == ["S11"]
    assert result["series"][0]["visible"]


def test_make_sparameter_plot_data_supports_legacy_parameter_and_ports():
    result = make_sparameter_plot_data(
        {"params": {"s_parameter": "legacy", "port_i": 2, "port_j": 1}},
        [1e9],
        [[[[1, 0], [2, 0]], [[3, 4], [5, 0]]]],
    )

    assert result["series"] == [{"label": "S21", "values": [complex(3, 4)]}]


@pytest.mark.parametrize(
    ("frequencies", "matrices", "params", "message"),
    [
        ([1e9, 2e9], [[[[1, 0]]]], {"s_parameter": "S11"}, "counts must match"),
        ([1e9], [[]], {"s_parameter": "S11"}, "non-empty and square"),
        ([1e9], [[[[1, 0], [2, 0]]]], {"s_parameter": "S11"}, "square"),
        ([1e9], [[[[1, 0]], [[2, 0], [3, 0]]]], {"s_parameter": "S11"}, "square"),
        (
            [1e9, 2e9],
            [[[[1, 0]]], [[[1, 0], [0, 0]], [[0, 0], [1, 0]]]],
            {"s_parameter": "S11"},
            "dimensions must remain constant",
        ),
        ([1e9], [[[[1, 0]]]], {"s_parameter": "S21"}, "outside the available 1-port"),
        ([1e9], [[[[1]]]], {"s_parameter": "S11"}, "real/imaginary pairs"),
    ],
)
def test_make_sparameter_plot_data_rejects_malformed_data(
    frequencies, matrices, params, message
):
    with pytest.raises(ValueError, match=message):
        make_sparameter_plot_data({"params": params}, frequencies, matrices)


@pytest.mark.parametrize("plot_type", ["plot_sp", "plot_vswr", "smith", "plot"])
@pytest.mark.parametrize("simulation_type", ["Sweep", "Parametric"])
def test_supports_live_plot_requires_all_live_simulation_conditions(plot_type, simulation_type):
    output = {"enabled": True, "plot_mode": "live", "plot_type": plot_type}
    simulation = {"type": simulation_type, "progressive_sparams_enabled": True}

    assert supports_live_plot(output, simulation)


@pytest.mark.parametrize(
    ("output_changes", "simulation_changes"),
    [
        ({"enabled": False}, {}),
        ({"plot_mode": "final"}, {}),
        ({"plot_mode": None}, {}),
        ({}, {"type": "Eigenmode"}),
        ({}, {"progressive_sparams_enabled": False}),
        ({"plot_type": "plot_ff"}, {}),
    ],
)
def test_supports_live_plot_rejects_each_restriction(output_changes, simulation_changes):
    output = {"enabled": True, "plot_mode": "live", "plot_type": "plot_sp"}
    simulation = {"type": "Sweep", "progressive_sparams_enabled": True}
    output.update(output_changes)
    simulation.update(simulation_changes)

    assert not supports_live_plot(output, simulation)


def test_supports_live_plot_defaults_to_final_mode():
    assert not supports_live_plot(
        {"enabled": True, "plot_type": "smith"},
        {"type": "Sweep", "progressive_sparams_enabled": True},
    )