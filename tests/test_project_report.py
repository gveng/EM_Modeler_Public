import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from em3d_modeler.ui.report import (
    _settings_html,
    _port_snapshots_html,
    filter_enabled_simulation_plots,
    touchstone_file_names,
    touchstone_sources,
    write_project_report,
)
from em3d_modeler.ui.chart_view import PlotView
from em3d_modeler.scene.em_objects import BoxObject, PlateObject
from em3d_modeler.scene import em_objects as scene_objects
from em3d_modeler.ui.viewport_widget import (
    _camera_orientation,
    _port_actor_parallel_scale,
    _port_plane_normal,
    _port_plane_orientation,
    _port_snapshot_actors,
    _report_actor_copy,
    _render_axonometric_actors,
    Viewport3DWidget,
)
from em3d_modeler.ui.main_window import MainWindow


def test_report_settings_omit_verbose_ports_and_outputs_but_retain_project_settings():
    rendered = _settings_html({
        "boundaries": {"Xmin": "PEC"},
        "ports": [{"name": "Port_1", "private_port_payload": "must not appear"}],
        "post": {"very_verbose": "must not appear"},
        "outputs": [{"name": "S11", "private_output_payload": "must not appear"}],
        "simulation": {"Fmin_GHz": 1.0},
        "mesh": {"default_fraction": 0.3},
        "custom_option": "preserved",
    })

    for title in ("Boundaries", "Simulation", "Other Settings"):
        assert f"<h2>{title}</h2>" in rendered
    for omitted in (
        "Ports</h2>", "Post-processing</h2>", "Port_1", "S11",
        "private_port_payload", "private_output_payload", "very_verbose",
    ):
        assert omitted not in rendered
    for setting in ("Xmin", "Fmin_GHz", "default_fraction", "custom_option", "preserved"):
        assert setting in rendered


def test_report_filters_disabled_simulations_and_outputs():
    settings = {
        "simulations": [
            {"name": "SweepA", "enabled": True},
            {"name": "SweepB", "enabled": False},
        ],
        "outputs": [
            {"name": "S11", "simulation": "SweepA", "enabled": True},
            {"name": "Disabled plot", "simulation": "SweepA", "enabled": False},
        ],
    }
    plots = [
        {"simulation": "SweepA", "key": "SweepA::S11", "name": "S11"},
        {"simulation": "SweepA", "key": "SweepA::Disabled plot", "name": "Disabled plot"},
        {"simulation": "SweepB", "key": "SweepB::S11", "name": "S11"},
    ]

    assert filter_enabled_simulation_plots(settings, plots) == [plots[0]]
    assert filter_enabled_simulation_plots(
        {"simulations": [{"name": "AllOff", "enabled": False}], "outputs": []},
        [{"simulation": "AllOff", "key": "AllOff::S11"}],
    ) == []


def test_report_lists_touchstone_files_used_by_plot_series():
    paths = [r"C:\results\run_a.s2p", "run_b.S4P", "unplotted.s6p"]
    series = [
        {"label": "S21", "file_name": "run_a.s2p", "file_id": paths[0]},
        {"label": "S11", "file_name": "run_b.S4P", "file_id": paths[1]},
        {"label": "S22", "file_name": "unplotted.s6p", "file_id": paths[2], "visible": False},
        {"file_name": "simdata.emerge"},
        {"file_name": "parameter=0.2"},
    ]
    assert touchstone_file_names(series, paths) == ["run_a.s2p", "run_b.S4P"]
    sources = touchstone_sources(series, paths)
    assert {(item["trace"], item["name"]) for item in sources} == {
        ("S11", "run_b.S4P"), ("S21", "run_a.s2p"),
    }
    assert touchstone_sources(
        [{"label": "Hidden only", "file_name": "unplotted.s6p", "visible": False}],
        paths,
    ) == []
    assert touchstone_sources(
        [
            {"label": "Unknown filename", "file_name": "unresolved.s2p"},
            {
                "label": "Unknown source",
                "file_name": "unresolved.s2p",
                "source_path": "unresolved.s2p",
            },
            {
                "label": "Unknown id",
                "file_id": "unresolved.s2p",
                "file_name": "unresolved.s2p",
            },
        ],
    ) == []


def test_report_groups_port_snapshots_and_places_settings_beside_images():
    image = QImage(80, 60, QImage.Format.Format_RGB32)
    image.fill(QColor("#ffffff"))
    resources = []

    markup = _port_snapshots_html(
        [
            {
                "project": "Demo & Project",
                "port": {
                    "number": 1, "name": "Input", "type": "WaveguidePort",
                    "object": "Plate_1", "x": 1, "y": 2, "z": 3,
                    "params": {"Mode": "TE10", "Impedance_Ohm": 50},
                    "private_cache": "not a user setting",
                },
                "image": image,
            },
            {
                "project": "Demo & Project",
                "port": {"number": 2, "name": "Output", "type": "LumpedPort", "params": {"Resistance_Ohm": 50}},
                "image": image,
            },
            {
                "project": "Demo & Project",
                "port": {"number": 3, "name": "Plane wave", "type": "PlaneWave", "params": {"Theta_Deg": 45}},
                "image": image,
                "note": "No mapped geometry; showing model context.",
            },
        ],
        resources,
    )

    assert markup.count("<h2>Demo &amp; Project</h2>") == 1
    assert markup.count("class='port-snapshot'") == 3
    assert "Port 1 — Input" in markup
    assert "Port 2 — Output" in markup
    assert "WaveguidePort" in markup and "Impedance_Ohm" in markup
    assert "Position (x, y, z)" in markup
    assert "private_cache" not in markup
    assert "No mapped geometry; showing model context." in markup
    port_rows = [
        row.split("</tr></table>", 1)[0]
        for row in markup.split("<table class='port-snapshot'><tr>")[1:]
    ]
    assert len(port_rows) == 3
    assert 'report://port/0' in port_rows[0] and "Plate_1" in port_rows[0]
    assert 'width=\'45%\'' in port_rows[0] and 'width=\'55%\'' in port_rows[0]
    assert 'report://port/1' in port_rows[1] and "Resistance_Ohm" in port_rows[1]
    assert 'report://port/2' in port_rows[2] and "Theta_Deg" in port_rows[2]
    assert len(resources) == 3


def test_report_touchstone_builder_is_pure_and_retains_absolute_source_paths(tmp_path):
    touchstone = tmp_path / "sweep-output.s2p"
    touchstone.write_text(
        "# GHz S RI R 50\n1 1 0 0.5 0 0.2 0 1 0\n",
        encoding="ascii",
    )
    unrelated_touchstone = tmp_path / "unselected.s1p"
    unrelated_touchstone.write_text(
        "# GHz S RI R 50\n1 0.1 0.0\n",
        encoding="ascii",
    )
    output = {
        "simulation": "SweepA",
        "name": "S-parameters",
        "plot_type": "plot_sp",
        "params": {"s_parameters": ["S21"]},
    }

    data, source, source_path = MainWindow._report_output_plot_data(
        None, output, {"type": "Sweep"}, [touchstone, unrelated_touchstone]
    )

    assert source == "Touchstone results"
    assert source_path == ""
    assert data["series"][0]["source_path"] == str(touchstone.resolve())
    assert data["series"][0]["file_name"] == touchstone.name
    assert any(item["visible"] for item in data["series"])
    assert any(not item["visible"] for item in data["series"])
    assert touchstone_file_names(data["series"], [touchstone, unrelated_touchstone]) == [
        touchstone.name
    ]


def test_collect_report_plots_uses_only_visible_chart_traces_for_provenance(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    visible_path = tmp_path / "plotted.s2p"
    hidden_path = tmp_path / "hidden.s2p"
    visible_path.write_text("", encoding="ascii")
    hidden_path.write_text("", encoding="ascii")
    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(QColor("#ffffff"))

    class Chart:
        _title = "S-parameters"
        _series_data = [
            {
                "label": "S21",
                "legend_label": "S21 - plotted.s2p",
                "file_name": visible_path.name,
                "file_id": str(visible_path.resolve()),
                "source_path": str(visible_path.resolve()),
                "source_kind": "touchstone",
                "visible": True,
            },
            {
                "label": "S11",
                "legend_label": "S11 - hidden.s2p",
                "file_name": hidden_path.name,
                "file_id": str(hidden_path.resolve()),
                "source_path": str(hidden_path.resolve()),
                "source_kind": "touchstone",
                "visible": False,
            },
        ]

        @staticmethod
        def capture_plot_image():
            return image

    class Collector:
        _project_name = "Demo"
        _plot_views = {"SweepA::S-parameters": Chart()}

        @staticmethod
        def _simulation_bundle_dir():
            return tmp_path

    monkeypatch.setattr(
        "em3d_modeler.ui.touchstone.find_touchstones",
        lambda *_args: [visible_path, hidden_path],
    )
    settings = {
        "simulations": [{"name": "SweepA", "enabled": True}],
        "outputs": [{"name": "S-parameters", "simulation": "SweepA", "enabled": True}],
    }

    plots = MainWindow._collect_report_plots(Collector(), settings)
    app.processEvents()

    assert len(plots) == 1
    assert plots[0]["touchstone_files"] == [visible_path.name]
    assert [item["name"] for item in plots[0]["touchstone_sources"]] == [
        visible_path.name
    ]
    assert plots[0]["touchstone_sources"][0]["path"] == str(visible_path.resolve())


def test_write_project_report_creates_pdf_with_embedded_model_and_plot(tmp_path):
    app = QApplication.instance() or QApplication([])
    model = QImage(100, 80, QImage.Format.Format_RGB32)
    model.fill(QColor("#6b8eaa"))
    plot = QImage(200, 120, QImage.Format.Format_RGB32)
    plot.fill(QColor("#ffffff"))
    port_image = QImage(140, 100, QImage.Format.Format_RGB32)
    port_image.fill(QColor("#b6cfe2"))
    target = tmp_path / "project-report.pdf"

    write_project_report(
        target,
        project_name="Demo Project",
        settings={"boundaries": {"Xmin": "PEC"}, "simulation": {"Fmin_GHz": 1}},
        materials=["PEC", "Dielectric"],
        model_image=model,
        plots=[{
            "simulation": "SweepA",
            "name": "S-parameters",
            "image": plot,
            "touchstone_sources": [{
                "trace": "S21",
                "name": "demo_run.s2p",
                "path": str(tmp_path / "demo_run.s2p"),
            }],
        }],
        port_snapshots=[{
            "project": "Demo Project",
            "port": {"number": 1, "name": "Feed", "type": "LumpedPort", "object": "Plate_1"},
            "image": port_image,
        }],
    )
    app.processEvents()

    content = target.read_bytes()
    assert content.startswith(b"%PDF-")
    assert b"/Type /Page" in content
    assert len(content) > 1000


def test_report_farfield_surface_uses_noninteractive_matplotlib_renderer():
    app = QApplication.instance() or QApplication([])
    result = MainWindow._render_farfield_surface(
        "Far field",
        [0.0, 1.0, 2.0],
        [0.0, 1.5, 3.0, 4.5],
        [[1.0, 0.7, 0.2]] * 4,
    )
    app.processEvents()

    assert result["plot_type"] == "plot_ff_3d"
    assert not result["image"].isNull()


def test_temporary_report_plot_does_not_load_or_persist_chart_settings(monkeypatch):
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp", settings_key="report-test-temporary")
    load_appended = []
    save_appended = []
    save_settings = []
    monkeypatch.setattr(view, "_load_appended_series", lambda: load_appended.append(True) or [])
    monkeypatch.setattr(view, "_save_appended_series", lambda: save_appended.append(True))
    monkeypatch.setattr(view, "_save_chart_settings", lambda: save_settings.append(True))

    view.set_plot_data(
        [1.0, 2.0],
        [{"label": "S11", "values": [0.5 + 0j, 0.25 + 0j]}],
        title="S11",
        xlabel="Frequency (GHz)",
        ylabel="S-parameter",
        persist=False,
    )
    view.resize(720, 480)
    app.processEvents()
    chart_image = view.grab().toImage()

    assert not load_appended
    assert not save_appended
    assert not save_settings
    assert not chart_image.isNull()
    plot_image = view.capture_plot_image()
    assert not plot_image.isNull()
    assert plot_image.size() == view.canvas.size()
    assert plot_image.size() != view.size()
    view.close()


def test_axonometric_actor_render_is_offscreen_and_does_not_change_source_actor():
    app = QApplication.instance() or QApplication([])
    model = BoxObject("Report model", x2=12, y2=8, z2=4)
    original_bounds = model.actor.GetBounds()

    image = _render_axonometric_actors(
        [model.actor],
        background=(1.0, 1.0, 1.0),
        background2=(0.9, 0.9, 0.95),
        gradient_background=True,
        width=320,
        height=240,
    )
    app.processEvents()

    assert not image.isNull()
    assert image.size().width() == 320
    assert model.actor.GetBounds() == original_bounds


def test_port_snapshot_includes_global_assembly_and_renders_it_offscreen():
    app = QApplication.instance() or QApplication([])
    target = BoxObject("Port body", x2=10, y2=8, z2=6)
    unrelated = BoxObject("Unrelated body", x1=100, x2=120, y2=20, z2=10)
    target.set_visible(False)
    actors, has_target = _port_snapshot_actors(
        [target, unrelated],
        {"number": 1, "name": "Input", "object": "Port body"},
    )
    original_bounds = target.actor.GetBounds()
    image = _render_axonometric_actors(
        actors,
        background=(1.0, 1.0, 1.0),
        background2=(0.9, 0.9, 0.95),
        gradient_background=True,
        width=320,
        height=240,
    )
    app.processEvents()

    assert not image.isNull()
    assert has_target
    assert actors == [target.actor, unrelated.actor]
    assert _port_actor_parallel_scale(actors) > (
        ((10 ** 2 + 8 ** 2 + 6 ** 2) ** 0.5) * 0.62
    )
    assert not target.is_visible()
    assert target.actor.GetBounds() == original_bounds


def test_port_image_includes_global_assembly_and_uses_port_plane_normal(monkeypatch):
    target = PlateObject("Port plate", x1=-5, x2=5, y1=-4, y2=4, z1=2, z2=2.1)
    connected = BoxObject(
        "Connected component", x1=10, x2=15, y1=-2, y2=2, z1=0, z2=4
    )
    hidden = BoxObject(
        "Hidden model component", x1=20, x2=22, y1=0, y2=2, z1=0, z2=2
    )
    hidden.set_visible(False)
    port = {
        "number": 1,
        "name": "Input",
        "type": "LumpedPort",
        "object": "Port plate",
        "params": {"Direction_X": 0, "Direction_Y": 0, "Direction_Z": -1},
    }
    plate_geometry = {
        "plate_name": "Port plate",
        "u": [1, 0, 0],
        "v": [0, 1, 0],
    }
    captured = {}
    sentinel = QImage(10, 10, QImage.Format.Format_RGB32)
    monkeypatch.setattr(
        "em3d_modeler.ui.viewport_widget._render_axonometric_actors",
        lambda actors, **kwargs: captured.update(actors=actors, kwargs=kwargs) or sentinel,
    )
    fake_renderer = type(
        "Renderer",
        (),
        {
            "GetBackground": lambda self: (1, 1, 1),
            "GetBackground2": lambda self: (1, 1, 1),
            "GetGradientBackground": lambda self: 0,
        },
    )()
    viewport = type(
        "Viewport",
        (),
        {
            "scene": type("Scene", (), {"objects": [target, connected, hidden]})(),
            "_renderer": fake_renderer,
        },
    )()

    result = Viewport3DWidget.render_port_image(
        viewport, port, plate_geometry=plate_geometry
    )

    assert result is sentinel
    assert captured["actors"] == [target.actor, connected.actor, hidden.actor]
    kwargs = captured["kwargs"]
    normal = kwargs["camera_normal"]
    assert normal == pytest.approx((0, 0, -1))
    assert kwargs["view_up"] == pytest.approx((0, 1, 0))
    scene_bounds = [actor.GetBounds() for actor in captured["actors"]]
    assert kwargs["focus_point"] == pytest.approx(
        (
            (min(item[0] for item in scene_bounds) + max(item[1] for item in scene_bounds)) / 2,
            (min(item[2] for item in scene_bounds) + max(item[3] for item in scene_bounds)) / 2,
            (min(item[4] for item in scene_bounds) + max(item[5] for item in scene_bounds)) / 2,
        )
    )
    assert kwargs["parallel_scale"] == pytest.approx(
        _port_actor_parallel_scale(
            captured["actors"],
            camera_normal=normal,
            view_up=kwargs["view_up"],
            width=900,
            height=640,
        )
    )
    assert kwargs["parallel_scale"] > _port_actor_parallel_scale(
        [target.actor],
        camera_normal=normal,
        view_up=kwargs["view_up"],
        width=900,
        height=640,
    )
    wide_scale = _port_actor_parallel_scale(
        captured["actors"],
        camera_normal=normal,
        view_up=kwargs["view_up"],
        width=1800,
        height=640,
    )
    tall_scale = _port_actor_parallel_scale(
        captured["actors"],
        camera_normal=normal,
        view_up=kwargs["view_up"],
        width=640,
        height=900,
    )
    assert wide_scale < kwargs["parallel_scale"] < tall_scale
    assert kwargs["highlight_actor"] is target.actor
    direction, up = _camera_orientation(normal, kwargs["view_up"])
    assert direction == pytest.approx(normal)
    assert up == pytest.approx((0, 1, 0))
    assert sum(direction[index] * up[index] for index in range(3)) == pytest.approx(0.0)
    fallback_normal = _port_plane_normal([target, connected], port)
    assert fallback_normal is not None
    assert abs(abs(fallback_normal[2]) - 1.0) < 1e-6
    nonparallel_normal, _ = _port_plane_orientation(
        [target],
        {"object": "Port plate", "params": {"Direction_X": 1}},
        plate_geometry,
    )
    assert nonparallel_normal == pytest.approx((0, 0, 1))
    waveguide_normal, _ = _port_plane_orientation(
        [target],
        {"type": "WaveguidePort", "object": "Port plate",
         "params": {"Direction_Z": -1}},
        plate_geometry,
    )
    assert waveguide_normal == pytest.approx((0, 0, 1))
    orthogonal_direction, orthogonal_up = _camera_orientation(
        (0, 0, 1), (2, 0, 3)
    )
    assert orthogonal_direction == pytest.approx((0, 0, 1))
    assert orthogonal_up == pytest.approx((1, 0, 0))
    assert sum(
        orthogonal_direction[index] * orthogonal_up[index]
        for index in range(3)
    ) == pytest.approx(0)
    # The supplied in-plane v axis is the camera's up vector, so the port
    # direction appears vertically while the camera remains normal to plane.
    rolled_normal = (1, 1, 1)
    port_axis = (1, -1, 0)
    rolled_direction, rolled_up = _camera_orientation(rolled_normal, port_axis)
    assert sum(
        rolled_direction[index] * rolled_up[index] for index in range(3)
    ) == pytest.approx(0)
    assert rolled_up == pytest.approx(
        tuple(value / (2 ** 0.5) for value in port_axis)
    )


def test_report_actor_copy_uses_viewport_selection_highlight_without_mutating_source():
    model = BoxObject("Port component")
    original_property = model.actor.GetProperty()
    original_color = original_property.GetColor()
    original_edge_color = original_property.GetEdgeColor()
    original_line_width = original_property.GetLineWidth()

    highlighted = _report_actor_copy(model.actor, selected=True)
    prop = highlighted.GetProperty()

    assert highlighted is not model.actor
    assert prop is not original_property
    assert prop.GetColor() == pytest.approx(scene_objects.SELECTION_COLOR)
    assert prop.GetLineWidth() == pytest.approx(2.5)
    assert prop.GetEdgeColor() == pytest.approx(
        tuple(max(value * 0.65, 0.0) for value in scene_objects.SELECTION_COLOR)
    )
    assert original_property.GetColor() == pytest.approx(original_color)
    assert original_property.GetEdgeColor() == pytest.approx(original_edge_color)
    assert original_property.GetLineWidth() == pytest.approx(original_line_width)


def test_project_report_passes_collected_world_plate_axes_to_port_renderer(
    monkeypatch, tmp_path
):
    port = {"number": 1, "name": "Feed", "type": "LumpedPort", "object": "Plate"}
    plate_geometry = {
        "plate_name": "Plate",
        "u": [0, 1, 0],
        "v": [0, 0, 1],
    }
    settings = {"ports": [port]}
    image = QImage(20, 20, QImage.Format.Format_RGB32)
    viewport = SimpleNamespace(
        scene=SimpleNamespace(objects=[]),
        render_port_image=Mock(return_value=image),
        render_axonometric_image=Mock(return_value=image),
    )
    window = SimpleNamespace(
        _project_tree=SimpleNamespace(get_settings=lambda: settings),
        _viewport=viewport,
        _material_store=SimpleNamespace(project_materials_to_json=lambda: []),
        _project_name="Demo",
        _collect_plate_lumped_ports=lambda: [plate_geometry],
        _collect_report_plots=lambda _settings: [],
    )
    monkeypatch.setattr(
        "em3d_modeler.ui.report.write_project_report",
        lambda *_args, **_kwargs: None,
    )

    MainWindow._write_project_report(window, tmp_path / "report.pdf")

    viewport.render_port_image.assert_called_once_with(
        port, plate_geometry=plate_geometry
    )
