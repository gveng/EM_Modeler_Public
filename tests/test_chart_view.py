import os
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import pyqtgraph as pg
import emerge.plot as emerge_plot
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QLabel, QWidget

from em3d_modeler.ui import chart_view
from em3d_modeler.ui.chart_view import PlotView, _AxisRangeDialog, _transform_values


def _drag_widget(widget, start, end):
    hover_point = widget.rect().center()
    if hover_point == start:
        hover_point = widget.rect().topLeft()
    QTest.mouseMove(widget, hover_point, delay=10)
    QApplication.processEvents()
    QTest.mouseMove(widget, start, delay=10)
    QApplication.processEvents()
    global_start = widget.mapToGlobal(start)
    global_end = global_start + end - start

    def send(event_type, global_position, button, buttons):
        local_position = widget.mapFromGlobal(global_position)
        event = QMouseEvent(
            event_type,
            QPointF(local_position),
            QPointF(global_position),
            button,
            buttons,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(widget, event)

    send(
        QEvent.Type.MouseMove,
        global_start,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
    )
    QApplication.processEvents()
    send(
        QEvent.Type.MouseButtonPress,
        global_start,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
    )
    send(
        QEvent.Type.MouseMove,
        global_end,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
    )
    send(
        QEvent.Type.MouseButtonRelease,
        global_end,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
    )
    QApplication.processEvents()


def _host_chart_in_outer_window(view):
    from em3d_modeler.ui.main_window import _PlotMdiArea, _PlotSubWindow

    outer_workspace = _PlotMdiArea()
    outer_workspace.resize(1200, 900)
    outer_chart_window = _PlotSubWindow(outer_workspace)
    outer_chart_window.setMinimumSize(480, 360)
    outer_chart_window.setWidget(view)
    outer_workspace.addSubWindow(outer_chart_window)
    outer_chart_window.setGeometry(60, 60, 1000, 740)
    outer_workspace.show()
    outer_chart_window.show()
    QApplication.processEvents()
    return outer_workspace, outer_chart_window


def _global_rect(widget):
    return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())


def _drag_overlay(widget, delta):
    target = getattr(widget, "handle", None) or widget
    start = target.rect().center()
    _drag_widget(target, start, start + delta)


def _legend_labels(view):
    if view._use_pyqtgraph:
        if view._legend_item is None:
            return []
        return [label.text for _sample, label in view._legend_item.items]
    return [
        view._external_legend.item(index).text()
        for index in range(view._external_legend.count())
    ]


def _marker_readout_text(readout):
    return readout.textItem.toPlainText()


@pytest.fixture(autouse=True)
def isolated_chart_data_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(
        chart_view, "_chart_data_cache_root", lambda: tmp_path / "chart-data"
    )


@pytest.fixture
def memory_qsettings(monkeypatch, tmp_path):
    values = {}

    class MemorySettings:
        def value(self, key, default=""):
            return values.get(key, default)

        def setValue(self, key, value):
            values[key] = value

        def sync(self):
            pass

    monkeypatch.setattr(chart_view, "QSettings", MemorySettings)
    return values


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("Magnitude (linear)", [1.0, 2.0]),
        ("Magnitude (dB)", [0.0, 20.0 * np.log10(2.0)]),
        ("Phase (degrees)", [0.0, 90.0]),
        ("Real", [1.0, 0.0]),
        ("Imaginary", [0.0, 2.0]),
        ("VSWR", [3.0, 2e12]),
    ],
)
def test_transform_values(mode, expected):
    result = _transform_values([1 + 0j, 0 + 2j] if mode != "VSWR" else [0.5, 1.0], mode)

    assert result == pytest.approx(expected)


def test_plot_view_exposes_controls_and_uses_pyqtgraph(monkeypatch):
    app = QApplication.instance() or QApplication([])
    plot_api = Mock(wraps=emerge_plot.plot)
    monkeypatch.setattr(emerge_plot, "plot", plot_api)
    view = PlotView("plot_sp")
    view.set_plot_data(
        [1.0, 2.0],
        [{"label": "S11", "values": [0.1 + 0j, 0.2 + 0.1j]}],
        title="S parameters",
        xlabel="Frequency (Hz)",
        ylabel="Magnitude",
    )

    assert view.canvas in view.findChildren(type(view.canvas))
    assert view.display_mode_combo in view.findChildren(QComboBox)
    assert view.display_mode_combo.currentText() == "Magnitude (dB)"
    assert view.x_scale_combo.findText("Log") >= 0
    assert view.y_scale_combo.findText("Log") >= 0
    assert not view.y_scale_combo.isEnabled()
    assert [button.accessibleName() for button in view._action_buttons] == [
        "Settings", "Fit", "Add Marker", "Remove Markers", "Append to Chart"
    ]
    assert all(button.text() == "" and not button.icon().isNull() for button in view._action_buttons)
    assert view._settings_dialog.axis_ranges_button is not None
    assert view._trace_items[("default", "S11")].text() == "S11 - Results"
    assert isinstance(view.plot_widget, pg.PlotWidget)
    assert len(view.plot_widget.listDataItems()) == 1
    plot_api.assert_not_called()
    assert app is not None


def test_plot_logo_is_shown_by_default_and_checkbox_removes_and_restores_it():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    checkbox = view._settings_dialog.remove_logo_checkbox

    assert isinstance(checkbox, QCheckBox)
    assert checkbox.text() == "Remove EMERGE Logo"
    assert not checkbox.isChecked()
    assert view._plot_logo_item is not None
    assert view._plot_logo_item.isVisible()

    checkbox.setChecked(True)
    assert not view._plot_logo_item.isVisible()
    checkbox.setChecked(False)
    assert view._plot_logo_item.isVisible()
    view.close()
    assert app is not None


def test_equation_tab_creates_persistent_dynamic_equation_dataset(memory_qsettings):
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp", settings_key="equation-chart")
    source = {
        "file_id": "run-1",
        "file_name": "Run 1",
        "label": "S11",
        "values": np.asarray([0.1 + 0j, 0.2 + 0j, 0.3 + 0j]),
    }
    view.set_plot_data(
        [1.0, 2.0, 3.0],
        [source],
        title="S parameters",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )

    dialog = view._settings_dialog
    assert dialog.tabs.tabText(dialog.tabs.count() - 1) == "Equation"
    assert dialog.equation_parameter_combo.count() == 1
    dialog.equation_parameter_combo.setCurrentIndex(0)
    dialog.equation_variable_name_edit.setText("s11")
    view._add_equation_variable()
    dialog.equation_constant_name_edit.setText("gain")
    dialog.equation_constant_value_edit.setText("2")
    view._add_equation_constant()
    dialog.equation_name_edit.setText("Power")
    dialog.equation_formula_edit.setText("abs(s11) ** 2 * gain")
    view._save_equation_from_editor()

    equations = [
        entry for entry in view._series_data
        if entry["file_id"] == chart_view._EQUATION_FILE_ID
    ]
    assert len(equations) == 1
    assert equations[0]["file_name"] == "Equations"
    assert equations[0]["label"] == "Power"
    assert equations[0]["x_values"] == pytest.approx([1.0, 2.0, 3.0])
    assert equations[0]["values"] == pytest.approx([0.02, 0.08, 0.18])

    dialog.new_equation_button.click()
    dialog.equation_variable_name_edit.setText("s11")
    view._add_equation_variable()
    dialog.equation_name_edit.setText("Magnitude")
    dialog.equation_formula_edit.setText("abs(s11)")
    view._save_equation_from_editor()
    assert len([
        entry for entry in view._series_data
        if entry["file_id"] == chart_view._EQUATION_FILE_ID
    ]) == 2
    persisted = memory_qsettings[f"{view._chart_settings_prefix}/equations"]
    assert '"expression": "abs(s11) ** 2 * gain"' in persisted
    assert '"name": "Magnitude"' in persisted

    view.set_plot_data(
        [1.0, 2.0, 3.0],
        [dict(source, values=np.asarray([0.5 + 0j, 0.4 + 0j, 0.3 + 0j]))],
        title="Updated S parameters",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )
    recalculated = next(
        entry for entry in view._series_data
        if entry["file_id"] == chart_view._EQUATION_FILE_ID
        and entry["label"] == "Power"
    )
    assert recalculated["values"] == pytest.approx([0.5, 0.32, 0.18])

    restored = PlotView("plot_sp", settings_key="equation-chart")
    restored.set_plot_data(
        [1.0, 2.0, 3.0],
        [source],
        title="Restored S parameters",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )
    restored_equation = next(
        entry for entry in restored._series_data
        if entry["file_id"] == chart_view._EQUATION_FILE_ID
        and entry["label"] == "Power"
    )
    assert restored_equation["values"] == pytest.approx([0.02, 0.08, 0.18])
    view.close()
    restored.close()
    assert app is not None


def test_equation_evaluator_is_safe_and_supports_elementwise_complex_functions():
    result = chart_view._evaluate_equation_expression(
        "abs(z) ** 2 + real(z)",
        {"z": np.asarray([1 + 2j, 3 + 4j])},
    )

    assert result == pytest.approx([6.0, 28.0])
    with pytest.raises(ValueError, match="not allowed|Only the listed"):
        chart_view._evaluate_equation_expression(
            "__import__('os').system('echo unsafe')",
            {},
        )
    with pytest.raises(ValueError, match="Unknown formula"):
        chart_view._evaluate_equation_expression("missing + 1", {})


def test_equation_x_mismatch_is_reported_in_chart_settings():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.set_plot_data(
        [1.0, 2.0],
        [
            {
                "file_id": "run-a",
                "file_name": "Run A",
                "label": "A",
                "values": [1.0, 2.0],
                "x_values": [1.0, 2.0],
            },
            {
                "file_id": "run-b",
                "file_name": "Run B",
                "label": "B",
                "values": [3.0, 4.0],
                "x_values": [1.5, 2.5],
            },
        ],
        title="S parameters",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )
    dialog = view._settings_dialog
    dialog.equation_parameter_combo.setCurrentIndex(0)
    dialog.equation_variable_name_edit.setText("a")
    view._add_equation_variable()
    dialog.equation_parameter_combo.setCurrentIndex(1)
    dialog.equation_variable_name_edit.setText("b")
    view._add_equation_variable()
    dialog.equation_name_edit.setText("Difference")
    dialog.equation_formula_edit.setText("a - b")
    view._save_equation_from_editor()

    assert not any(
        entry["file_id"] == chart_view._EQUATION_FILE_ID
        for entry in view._series_data
    )
    assert "same X values" in dialog.equation_error_label.text()
    view.close()
    assert app is not None


def test_equation_uses_file_id_and_label_for_duplicate_trace_names_and_own_x_values():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.set_plot_data(
        [1.0, 2.0],
        [
            {
                "file_id": "overlay-a",
                "file_name": "Overlay A",
                "label": "S21",
                "values": [0.1 + 0j, 0.2 + 0j],
                "x_values": [1.0, 2.0],
            },
            {
                "file_id": "overlay-b",
                "file_name": "Overlay B",
                "label": "S21",
                "values": [0.0 + 2j, 2.0 + 0j],
                "x_values": [4.0, 9.0],
            },
        ],
        title="Overlays",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )

    dialog = view._settings_dialog
    dialog.equation_parameter_combo.setCurrentIndex(1)
    dialog.equation_variable_name_edit.setText("selected")
    view._add_equation_variable()
    dialog.equation_name_edit.setText("SelectedOverlay")
    dialog.equation_formula_edit.setText("selected * 2")
    view._save_equation_from_editor()

    equation = next(
        entry for entry in view._series_data
        if entry["file_id"] == chart_view._EQUATION_FILE_ID
    )
    assert equation["x_values"] == pytest.approx([4.0, 9.0])
    assert equation["values"] == pytest.approx([4j, 4 + 0j])
    rendered_x, rendered_y = view._curve_items[-1].getData()
    assert rendered_x == pytest.approx([4.0, 9.0])
    assert rendered_y == pytest.approx([20 * np.log10(4), 20 * np.log10(4)])
    view.close()
    assert app is not None


def test_plot_logo_is_scene_native_in_lower_right_of_plot_area():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.resize(900, 680)
    view.show()
    app.processEvents()

    logo = view._plot_logo_item
    plot_bounds = view.plot_widget.getPlotItem().getViewBox().sceneBoundingRect()
    logo_bounds = logo.sceneBoundingRect()

    assert logo.scene() is view.plot_widget.scene()
    assert logo.parentItem() is None
    assert 16 <= logo.pixmap().width() <= 40
    assert 16 <= logo.pixmap().height() <= 40
    assert 0 <= plot_bounds.right() - logo_bounds.right() <= 9
    assert 0 <= plot_bounds.bottom() - logo_bounds.bottom() <= 9

    initial_position = logo.scenePos()
    view.plot_widget.getPlotItem().getViewBox().setRange(xRange=(10, 20), yRange=(-5, 5))
    app.processEvents()
    assert logo.scenePos() == initial_position
    view.close()
    assert app is not None


def test_plot_logo_scales_with_plot_viewport_resize():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.resize(600, 460)
    view.show()
    app.processEvents()
    compact_size = view._plot_logo_item.pixmap().width()

    view.resize(1500, 1000)
    app.processEvents()
    expanded_size = view._plot_logo_item.pixmap().width()

    assert 16 <= compact_size < expanded_size <= 40
    view.close()
    assert app is not None


def test_matplotlib_canvas_remains_installed_after_redraw(monkeypatch):
    app = QApplication.instance() or QApplication([])

    def fake_plot_ff(frequencies, values, *, labels, **_kwargs):
        import matplotlib.pyplot as plt

        _figure, axes = plt.subplots()
        for series_values, label in zip(values, labels):
            axes.plot(frequencies, series_values, label=label)

    monkeypatch.setattr(emerge_plot, "plot_ff", fake_plot_ff)
    view = PlotView("plot_ff")
    view.set_plot_data(
        [0.0, 90.0],
        [{"label": "Gain", "values": [1.0, 2.0]}],
        title="Far field",
        xlabel="Angle",
        ylabel="Gain",
    )

    assert view._canvas_scroll.widget() is view.canvas
    assert view.canvas in view.findChildren(type(view.canvas))
    assert view._external_legend.count() == 1
    assert view._external_legend.item(0).text() == "Gain - Results"
    assert app is not None


def test_plot_canvas_tracks_window_size_and_d_b_mode_forces_linear_y():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.resize(1600, 1100)
    view.show()
    app.processEvents()
    initial_size = view.canvas.size()

    view.resize(1900, 1400)
    app.processEvents()
    expanded_size = view.canvas.size()

    assert expanded_size.width() > initial_size.width()
    assert expanded_size.height() > initial_size.height()

    view.resize(700, 550)
    app.processEvents()
    compact_size = view.canvas.size()
    assert compact_size.width() < expanded_size.width()
    assert compact_size.height() < expanded_size.height()
    assert not hasattr(view, "plot_width_spin")
    assert not hasattr(view, "plot_height_spin")

    view.display_mode_combo.setCurrentText("Magnitude (linear)")
    assert view.y_scale_combo.isEnabled()
    view.y_scale_combo.setCurrentText("Log")
    assert view.y_scale_combo.currentText() == "Log"
    view.display_mode_combo.setCurrentText("Magnitude (dB)")
    assert view.y_scale_combo.currentText() == "Linear"
    assert not view.y_scale_combo.isEnabled()
    assert not view.axes.getAxis("left").logMode
    assert app is not None


def test_single_chart_workspace_resizes_plot_frame_from_its_boundary():
    app = QApplication.instance() or QApplication([])
    from em3d_modeler.ui.main_window import _PlotMdiArea, _PlotSubWindow

    workspace = _PlotMdiArea()
    workspace.setObjectName("singleChartWorkspace")
    workspace.resize(900, 700)
    plot_frame = _PlotSubWindow(workspace)
    plot_frame.setObjectName("plotFrame")
    plot_frame.setMinimumSize(360, 280)
    plot_frame.setWidget(PlotView("plot_sp"))
    workspace.addSubWindow(plot_frame)
    plot_frame.setGeometry(40, 40, 640, 480)
    workspace.show()
    plot_frame.show()
    app.processEvents()

    initial_width = plot_frame.width()
    edge_y = plot_frame.height() // 2
    _drag_widget(
        plot_frame,
        QPoint(plot_frame.width() - 2, edge_y),
        QPoint(plot_frame.width() - 42, edge_y),
    )

    assert plot_frame.width() <= initial_width - 20
    workspace.close()
    assert app is not None


def test_inner_chart_surface_cannot_resize_chart_subwindow():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.set_plot_data(
        [1.0, 2.0],
        [{"label": "S11", "values": [0.1 + 0j, 0.2 + 0j]}],
        title="Single chart",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )
    outer_workspace, outer_chart_window = _host_chart_in_outer_window(view)

    chart_frame = view._chart_subwindow
    assert view._chart_subwindow.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert view._mdi_area.background().color() == view._chart_background
    assert view.plot_widget.backgroundBrush().color() == view._chart_background
    outer_geometry_before_drag = _global_rect(outer_chart_window)
    chart_geometry_before_drag = chart_frame.geometry()
    chart_surface = view._chart_surface
    edge_y = chart_surface.height() // 2
    edge_point = QPoint(chart_surface.width() - 2, edge_y)
    QTest.mouseMove(chart_surface, edge_point, delay=10)
    app.processEvents()
    assert chart_surface.cursor().shape() != Qt.CursorShape.SizeHorCursor
    _drag_widget(
        chart_surface,
        edge_point,
        QPoint(chart_surface.width() - 42, edge_y),
    )

    assert chart_frame.geometry() == chart_geometry_before_drag
    assert _global_rect(outer_chart_window) == outer_geometry_before_drag
    assert not chart_surface.toolTip()
    outer_workspace.close()
    assert app is not None


def test_chart_subwindow_stays_close_to_mdi_viewport_when_outer_plot_resizes():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    outer_workspace, outer_chart_window = _host_chart_in_outer_window(view)
    previous_viewport_size = None

    for width, height in ((600, 460), (850, 650), (1100, 820)):
        outer_workspace.resize(width + 160, height + 140)
        outer_chart_window.setGeometry(40, 40, width, height)
        app.processEvents()

        viewport = view._mdi_area.viewport().rect()
        chart_geometry = view._chart_subwindow.geometry()
        margins = (
            chart_geometry.x(),
            chart_geometry.y(),
            viewport.width() - chart_geometry.x() - chart_geometry.width(),
            viewport.height() - chart_geometry.y() - chart_geometry.height(),
        )

        if previous_viewport_size is not None:
            assert viewport.width() > previous_viewport_size.width()
            assert viewport.height() > previous_viewport_size.height()
        assert all(0 <= margin <= 20 for margin in margins)
        previous_viewport_size = viewport.size()

    outer_workspace.close()
    assert app is not None


def test_marker_readout_stays_native_and_usable_after_outer_plot_resize():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.set_plot_data(
        [1.0, 2.0],
        [
            {
                "label": f"Trace_{index:02d}_reference_measurement_with_long_label",
                "values": [0.1, 0.2],
            }
            for index in range(4)
        ],
        title="Responsive markers",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )
    view._set_marker(("default", "Trace_00_reference_measurement_with_long_label"), "o")
    view._add_marker(1.0)
    outer_workspace, outer_chart_window = _host_chart_in_outer_window(view)
    viewport_widths = []
    marker_pen_widths = []
    trace_symbol_sizes = []

    for width, height in ((480, 360), (760, 620), (1100, 820)):
        outer_workspace.resize(width + 160, height + 140)
        outer_chart_window.setGeometry(40, 40, width, height)
        app.processEvents()
        marker_line, marker_readout = view._marker_items[0]
        viewport_widths.append(view.plot_widget.viewport().width())
        assert marker_readout.parentItem() is not marker_line
        assert marker_readout.scene() is view.plot_widget.scene()
        assert marker_readout.movable
        assert view._legend_item.parentItem() is view.plot_widget.getViewBox()
        assert view._legend_item.scene() is view.plot_widget.scene()
        marker_pen_widths.append(marker_line.pen.widthF())
        trace_symbol_sizes.append(view._curve_items[0].opts["symbolSize"])

    assert viewport_widths[0] < viewport_widths[1] < viewport_widths[2]
    assert marker_pen_widths[0] < marker_pen_widths[1] < marker_pen_widths[2]
    assert trace_symbol_sizes[0] < trace_symbol_sizes[1] < trace_symbol_sizes[2]

    outer_workspace.close()
    assert app is not None


def test_plot_grid_has_thin_border_without_chart_header_or_black_bar():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.set_plot_data(
        [1.0, 2.0],
        [{"label": "S11", "values": [0.1 + 0j, 0.2 + 0j]}],
        title="Single chart",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )
    outer_workspace, _outer_chart_window = _host_chart_in_outer_window(view)

    border = view.plot_widget.getViewBox().border

    assert border.widthF() == pytest.approx(1.0)
    assert border.color().name() == "#7a858f"
    assert not view._chart_surface.findChildren(QLabel, "chartMoveHandle")
    assert view._chart_surface.frameShape() == chart_view.QFrame.Shape.NoFrame
    assert view._chart_subwindow.windowTitle() == ""
    assert view._chart_subwindow.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert view._mdi_area.background().color() == view._chart_background
    assert view._chart_background.name() == "#ffffff"
    assert view.plot_widget.backgroundBrush().color().name() == "#ffffff"
    outer_workspace.close()
    assert app is not None


def test_pyqtgraph_legend_is_a_native_graphics_item():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.set_plot_data(
        [1.0, 2.0],
        [{"label": "S11", "values": [0.1 + 0j, 0.2 + 0j]}],
        title="Single chart",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )
    legend = view._legend_item

    assert isinstance(legend, pg.LegendItem)
    assert legend.parentItem() is view.plot_widget.getViewBox()
    assert legend.scene() is view.plot_widget.scene()
    assert view._legend_panel is None
    assert view._external_legend is None
    assert _legend_labels(view) == ["S11 - Results"]
    view.close()
    assert app is not None


def test_chart_options_slider_scales_native_legend_immediately_and_on_recreation():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.resize(900, 640)
    view.set_plot_data(
        [1.0, 2.0],
        [{"label": "S11", "values": [0.1 + 0j, 0.2 + 0j]}],
        title="Single chart",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )
    view.show()
    app.processEvents()

    slider = view._settings_dialog.legend_scale_slider
    legend = view._legend_item
    initial_footprint = legend.sceneBoundingRect()
    assert view._settings_dialog.tabs.widget(1).isAncestorOf(slider)
    assert slider.value() == 100

    slider.setValue(60)
    app.processEvents()
    scaled_footprint = legend.sceneBoundingRect()
    assert legend.scale() == pytest.approx(0.6)
    assert scaled_footprint.width() < initial_footprint.width()
    assert scaled_footprint.height() < initial_footprint.height()
    assert view._settings_dialog.legend_scale_label.text() == "60%"

    view._redraw()
    assert view._legend_item.scale() == pytest.approx(0.6)
    view.close()
    assert app is not None


def test_alt_drag_does_not_move_inner_chart_or_outer_window():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.set_plot_data(
        [1.0, 2.0],
        [{"label": "S11", "values": [0.1 + 0j, 0.2 + 0j]}],
        title="Single chart",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )
    outer_workspace, outer_chart_window = _host_chart_in_outer_window(view)

    chart_frame = view._chart_subwindow
    chart_geometry_before_drag = chart_frame.geometry()
    outer_geometry_before_drag = _global_rect(outer_chart_window)
    viewport = view.plot_widget.viewport()
    start = viewport.rect().center()
    global_start = viewport.mapToGlobal(start)
    global_end = global_start + QPoint(70, 45)
    for event_type, global_position, button, buttons in (
        (QEvent.Type.MouseButtonPress, global_start, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseMove, global_end, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseButtonRelease, global_end, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton),
    ):
        local_position = viewport.mapFromGlobal(global_position)
        event = QMouseEvent(
            event_type,
            QPointF(local_position),
            QPointF(global_position),
            button,
            buttons,
            Qt.KeyboardModifier.AltModifier,
        )
        QApplication.sendEvent(viewport, event)
    app.processEvents()

    assert chart_frame.geometry() == chart_geometry_before_drag
    assert _global_rect(outer_chart_window) == outer_geometry_before_drag
    outer_workspace.close()
    assert app is not None


def test_native_legend_can_be_dragged_within_the_canvas_viewport():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.resize(1000, 740)
    view.set_plot_data(
        [1.0, 2.0],
        [{"label": "S11", "values": [0.1 + 0j, 0.2 + 0j]}],
        title="Single chart",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )
    view.show()
    app.processEvents()

    legend = view._legend_item
    viewport = view.plot_widget.viewport()
    initial_position = QPoint(round(legend.pos().x()), round(legend.pos().y()))
    item_center = view.plot_widget.mapFromScene(
        legend.mapToScene(legend.boundingRect().center())
    )
    _drag_widget(viewport, item_center, item_center + QPoint(-60, 50))

    assert legend.pos() != initial_position
    assert legend.scene() is view.plot_widget.scene()
    assert _legend_labels(view) == ["S11 - Results"]
    view.close()
    assert app is not None


def test_native_marker_readout_tracks_marker_and_updates_with_data():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.resize(1000, 740)
    view.set_plot_data(
        [1.0, 2.0],
        [{"label": "S11", "values": [0.1 + 0j, 0.3 + 0j]}],
        title="Single chart",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )
    view.display_mode_combo.setCurrentText("Magnitude (linear)")
    view.show()
    view._add_marker(1.0)
    app.processEvents()

    marker_line = view._marker_items[0][0]
    marker_readout = view._marker_items[0][1]
    assert isinstance(marker_readout, pg.TextItem)
    assert marker_readout.objectName() == "markerReadout1"
    assert marker_readout.parentItem() is not marker_line
    assert marker_readout.scene() is view.plot_widget.scene()
    assert marker_readout.movable
    readout_scene_x_before_move = marker_readout.sceneBoundingRect().center().x()

    marker_line.setValue(1.5)
    app.processEvents()
    readout_scene_x_after_move = marker_readout.sceneBoundingRect().center().x()
    assert "Frequency (GHz): 1.5" in _marker_readout_text(marker_readout)
    assert "S11: 0.2" in _marker_readout_text(marker_readout)
    assert readout_scene_x_after_move != readout_scene_x_before_move

    viewport = view.plot_widget.viewport()
    center_before_drag = marker_readout.sceneBoundingRect().center()
    readout_center = view.plot_widget.mapFromScene(
        center_before_drag
    )
    _drag_widget(viewport, readout_center, readout_center + QPoint(75, -45))
    dragged_center = marker_readout.sceneBoundingRect().center()
    assert dragged_center.x() - center_before_drag.x() > 40
    assert center_before_drag.y() - dragged_center.y() > 20
    position_after_drag = marker_readout.pos()

    marker_line.setValue(1.75)
    app.processEvents()
    assert marker_readout.pos() == position_after_drag
    assert "Frequency (GHz): 1.75" in _marker_readout_text(marker_readout)
    assert "S11: 0.25" in _marker_readout_text(marker_readout)
    view.close()
    assert app is not None


def test_progressive_update_preserves_series_style():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.set_plot_data(
        [1.0],
        [{"label": "S11", "values": [0.1 + 0j]}],
        title="Sweep",
        xlabel="Frequency (Hz)",
        ylabel="Magnitude",
    )
    view._series_data[0]["color"] = "#123456"
    view._series_data[0]["marker"] = "s"
    view._series_data[0]["visible"] = False

    view.set_progressive_data([1.0, 2.0], [{"label": "S11", "values": [0.1, 0.2]}], title="Sweep live")

    assert view._series_data[0]["color"] == "#123456"
    assert view._series_data[0]["marker"] == "s"
    assert view._series_data[0]["visible"] is False
    assert len(view._trace_items) == 1
    assert view._available_trace_list.count() == 1
    assert view._selected_trace_list.count() == 0
    assert app is not None


def test_progressive_plot_keeps_configured_band_visible():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")

    view.set_progressive_data(
        [0.1, 0.595, 1.09],
        [{"label": "S11", "values": [1.0 + 0j, 0.99 + 0j, 0.98 + 0j]}],
        title="Sweep (3/21 samples)",
        xlabel="Frequency (GHz)",
        ylabel="S-parameter",
        x_range=(0.1, 10.0),
    )

    assert view.plot_widget.getViewBox().viewRange()[0] == pytest.approx([0.1, 10.0])
    assert view._title == "Sweep (3/21 samples)"
    assert app is not None


def test_plot_view_normalizes_invalid_trace_color_before_styling_list_item():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    series = [{"label": "S11", "values": [0.1 + 0j]}]
    view.set_plot_data(
        [1.0], series, title="Sweep", xlabel="Frequency", ylabel="Magnitude"
    )
    view._series_data[0]["color"] = "not-a-color"

    view.set_plot_data(
        [2.0], series, title="Sweep", xlabel="Frequency", ylabel="Magnitude"
    )

    assert chart_view.QColor(view._series_data[0]["color"]).isValid()
    assert view._trace_items[("default", "S11")].foreground().color().isValid()
    assert app is not None


def test_trace_color_persists_across_runs_and_plot_recreation(monkeypatch):
    app = QApplication.instance() or QApplication([])
    stored_settings = {}

    class MemorySettings:
        def value(self, key, default=""):
            return stored_settings.get(key, default)

        def setValue(self, key, value):
            stored_settings[key] = value

        def sync(self):
            pass

    monkeypatch.setattr(chart_view, "QSettings", MemorySettings)
    monkeypatch.setattr(
        chart_view.QColorDialog,
        "getColor",
        lambda *_args, **_kwargs: chart_view.QColor("#123456"),
    )
    plot_data = [{
        "label": "S11",
        "file_id": "Simulation_1",
        "file_name": "Simulation_1",
        "values": [0.1 + 0j, 0.2 + 0j],
    }]
    view = PlotView("plot_sp", settings_key="Simulation_1::Output")
    view.set_plot_data(
        [1.0, 2.0], plot_data, title="S parameters", xlabel="Frequency", ylabel="Magnitude"
    )
    view._choose_color(("Simulation_1", "S11"))
    view.clear_data()
    view.set_plot_data(
        [1.0, 2.0], plot_data, title="S parameters", xlabel="Frequency", ylabel="Magnitude"
    )

    assert view._series_data[0]["color"] == "#123456"

    recreated_view = PlotView("plot_sp", settings_key="Simulation_1::Output")
    recreated_view.set_plot_data(
        [1.0, 2.0], plot_data, title="S parameters", xlabel="Frequency", ylabel="Magnitude"
    )

    assert recreated_view._series_data[0]["color"] == "#123456"
    assert app is not None


def test_chart_and_trace_settings_restore_across_view_recreation(monkeypatch, memory_qsettings):
    app = QApplication.instance() or QApplication([])
    key = "Simulation_1::Output"
    plot_data = [
        {
            "label": "S11",
            "file_id": "run-1",
            "file_name": "run-1",
            "values": [1.0, 10.0, 100.0],
        },
        {
            "label": "S22",
            "file_id": "run-1",
            "file_name": "run-1",
            "values": [2.0, 20.0, 80.0],
        },
    ]
    view = PlotView("plot_sp", settings_key=key)
    view.display_mode_combo.setCurrentText("Magnitude (linear)")
    view.x_scale_combo.setCurrentText("Log")
    view.y_scale_combo.setCurrentText("Log")

    class AcceptedAxisRanges:
        def __init__(self, *_args):
            self.x_range = (10.0, 100.0)
            self.y_range = (2.0, 80.0)

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(chart_view, "_AxisRangeDialog", AcceptedAxisRanges)
    monkeypatch.setattr(
        chart_view.QColorDialog,
        "getColor",
        lambda *_args, **_kwargs: chart_view.QColor("#123456"),
    )
    view.set_plot_data(
        [1.0, 10.0, 100.0], plot_data,
        title="S parameters", xlabel="Frequency", ylabel="Magnitude",
    )
    view._open_axis_settings()
    view._set_legend_scale(65)
    view._settings_dialog.remove_logo_checkbox.setChecked(True)
    view._set_marker(("run-1", "S11"), "^")
    view._choose_color(("run-1", "S11"))
    view._set_visible(("run-1", "S22"), False)

    restored = PlotView("plot_sp", settings_key=key)
    restored.set_plot_data(
        [1.0, 10.0, 100.0], plot_data,
        title="S parameters", xlabel="Frequency", ylabel="Magnitude",
    )

    assert restored.display_mode_combo.currentText() == "Magnitude (linear)"
    assert restored.x_scale_combo.currentText() == "Log"
    assert restored.y_scale_combo.currentText() == "Log"
    assert restored._x_auto_range is False
    assert restored._y_auto_range is False
    assert restored._x_range == (10.0, 100.0)
    assert restored._y_range == (2.0, 80.0)
    assert restored._settings_dialog.legend_scale_slider.value() == 65
    assert restored._settings_dialog.remove_logo_checkbox.isChecked()
    if restored._plot_logo_item is not None:
        assert not restored._plot_logo_item.isVisible()

    x_range, y_range = restored.plot_widget.getPlotItem().getViewBox().viewRange()
    assert x_range == pytest.approx([1.0, 2.0])
    assert y_range == pytest.approx(np.log10([2.0, 80.0]))
    assert [entry["visible"] for entry in restored._series_data] == [True, False]
    restored_s11 = restored._series_data[0]
    assert restored_s11["color"] == "#123456"
    assert restored_s11["marker"] == "^"
    assert restored._curve_items[0].opts["symbol"] == "t"
    assert restored._curve_items[0].opts["pen"].color().name() == "#123456"
    assert restored._legend_item.scale() == pytest.approx(0.65)

    restored._fit_view()
    fitted = PlotView("plot_sp", settings_key=key)
    assert fitted._x_auto_range and fitted._y_auto_range
    assert fitted._x_range is None and fitted._y_range is None
    assert app is not None


def test_markers_legend_and_appended_series_restore_without_refresh_duplicates(memory_qsettings):
    app = QApplication.instance() or QApplication([])
    key = "persistent-overlay-chart"
    base_series = [{
        "label": "S11",
        "file_id": "base-run",
        "file_name": "base-run",
        "values": [0.1 + 0.2j, 0.3 + 0.4j],
    }]
    source = PlotView("plot_sp", settings_key=key)
    source.resize(1000, 740)
    source.show()
    app.processEvents()
    source.set_plot_data(
        [1.0, 2.0], base_series,
        title="Overlay", xlabel="Frequency (GHz)", ylabel="Magnitude",
    )
    source.display_mode_combo.setCurrentText("Magnitude (linear)")
    source._set_legend_scale(73)
    source._set_marker_scale(145)
    source.append_plot_data(
        [10.0, 20.0],
        [
            {
                "label": "S21", "file_id": "run-2", "file_name": "run-2.s2p",
                "values": [3.0 + 4.0j, 0.0 + 2.0j],
            },
            {
                "label": "S22", "file_id": "run-2", "file_name": "run-2.s2p",
                "values": [1.0 + 1.0j, 2.0 + 0.0j],
            },
        ],
        title="Overlay", xlabel="Frequency (GHz)", ylabel="Magnitude",
    )
    source.append_plot_data(
        [100.0, 200.0],
        [{
            "label": "S12", "file_id": "run-3", "file_name": "run-3.s2p",
            "values": [0.5 + 0.25j, 0.75 + 0.5j],
        }],
        title="Overlay", xlabel="Frequency (GHz)", ylabel="Magnitude",
    )
    first_overlay_id = next(
        item["file_id"] for item in source._series_data if item["label"] == "S21"
    )
    source.set_progressive_data(
        [1.0, 2.0],
        [
            base_series[0],
            {
                "label": "S21", "file_id": first_overlay_id,
                "file_name": "run-2.s2p", "x_values": [10.0, 20.0],
                "values": [4.0 + 3.0j, 1.0 + 2.0j],
            },
        ],
        title="Overlay live", xlabel="Frequency (GHz)", ylabel="Magnitude",
    )
    source._set_marker((first_overlay_id, "S21"), "s")
    source._set_visible((first_overlay_id, "S22"), False)
    source._add_marker(12.0)
    source._add_marker(18.0)
    source._marker_items[0][1].setPos(14.0, 0.75)
    source._marker_items[1][1].setPos(17.0, -0.5)
    app.processEvents()

    legend = source._legend_item
    drag_event = Mock()
    drag_event.button.return_value = Qt.MouseButton.LeftButton
    drag_event.pos.return_value = QPointF(-55.0, 38.0)
    drag_event.lastPos.return_value = QPointF(0.0, 0.0)
    legend.mouseDragEvent(drag_event)
    legend_bounds = legend.parentItem().boundingRect()
    saved_legend_position = (
        legend.pos().x() / legend_bounds.width(),
        legend.pos().y() / legend_bounds.height(),
    )
    assert f"{source._chart_settings_prefix}/legend_position" in memory_qsettings

    overlay_series = [
        entry for entry in source._series_data
        if entry["file_id"] in source._overlay_file_ids
    ]
    expected_overlay_keys = [(entry["file_id"], entry["label"]) for entry in overlay_series]
    source.close()

    restored = PlotView("plot_sp", settings_key=key)
    restored.resize(1000, 740)
    restored.show()
    app.processEvents()
    restored.set_plot_data(
        [1.0, 2.0], base_series,
        title="Overlay", xlabel="Frequency (GHz)", ylabel="Magnitude",
    )
    app.processEvents()

    assert restored.display_mode_combo.currentText() == "Magnitude (linear)"
    assert restored._legend_scale_percent == 73
    assert restored._marker_scale_percent == 145
    restored_overlays = [
        entry for entry in restored._series_data
        if entry["file_id"] in restored._overlay_file_ids
    ]
    assert [(entry["file_id"], entry["label"]) for entry in restored_overlays] == expected_overlay_keys
    assert restored_overlays[0]["values"].tolist() == [4.0 + 3.0j, 1.0 + 2.0j]
    assert restored_overlays[0]["x_values"].tolist() == [10.0, 20.0]
    assert restored_overlays[0]["marker"] == "s"
    assert restored_overlays[1]["visible"] is False
    assert restored_overlays[2]["values"].tolist() == [0.5 + 0.25j, 0.75 + 0.5j]
    assert len(restored._marker_items) == 2
    assert [line.value() for line, _readout in restored._marker_items] == pytest.approx([12.0, 18.0])
    assert [
        (readout.pos().x(), readout.pos().y())
        for _line, readout in restored._marker_items
    ] == pytest.approx([(14.0, 0.75), (17.0, -0.5)])
    assert "Marker 1" in _marker_readout_text(restored._marker_items[0][1])
    assert "Frequency (GHz): 12" in _marker_readout_text(restored._marker_items[0][1])
    assert "S21:" in _marker_readout_text(restored._marker_items[0][1])
    restored_legend_bounds = restored._legend_item.parentItem().boundingRect()
    assert restored._legend_item.pos().x() / restored_legend_bounds.width() == pytest.approx(
        saved_legend_position[0]
    )
    assert restored._legend_item.pos().y() / restored_legend_bounds.height() == pytest.approx(
        saved_legend_position[1]
    )

    restored.set_plot_data(
        [1.0, 2.0], base_series,
        title="Overlay refresh", xlabel="Frequency (GHz)", ylabel="Magnitude",
    )
    refreshed_overlay_keys = [
        (entry["file_id"], entry["label"])
        for entry in restored._series_data
        if entry["file_id"] in restored._overlay_file_ids
    ]
    assert refreshed_overlay_keys == expected_overlay_keys
    assert len(restored._series_data) == 4

    restored.set_progressive_data(
        [1.0, 2.0],
        [
            base_series[0],
            {
                "label": "S21", "file_id": first_overlay_id,
                "file_name": "run-2.s2p", "x_values": [10.0, 20.0],
                "values": [5.0 + 1.0j, 2.0 + 3.0j],
            },
        ],
        title="Overlay progressive refresh",
        xlabel="Frequency (GHz)", ylabel="Magnitude",
    )
    progressive_overlays = [
        entry for entry in restored._series_data
        if entry["file_id"] in restored._overlay_file_ids
    ]
    assert [(entry["file_id"], entry["label"]) for entry in progressive_overlays] == expected_overlay_keys
    assert progressive_overlays[0]["values"].tolist() == [5.0 + 1.0j, 2.0 + 3.0j]
    assert progressive_overlays[0]["marker"] == "s"
    assert progressive_overlays[1]["visible"] is False
    assert len(restored._series_data) == 4
    assert app is not None


def test_appended_curve_data_is_compressed_in_sidecar_not_qsettings(memory_qsettings):
    app = QApplication.instance() or QApplication([])
    x_values = np.linspace(1.0, 20.0, 1200)
    values = np.exp(1j * x_values)
    source = PlotView("plot_sp", settings_key="large-append-cache")
    source.append_plot_data(
        x_values,
        [{"label": "S21", "file_id": "sweep-b", "values": values}],
        title="Large append",
        xlabel="Frequency",
        ylabel="Magnitude",
    )

    cache_path = source._appended_series_path()
    assert cache_path.is_file()
    assert cache_path.stat().st_size < 1200 * 100
    assert f"{source._chart_settings_prefix}/appended_series" not in memory_qsettings

    restored = PlotView("plot_sp", settings_key="large-append-cache")
    restored.set_plot_data(
        [], [], title="Large append", xlabel="Frequency", ylabel="Magnitude"
    )
    restored_series = next(
        item for item in restored._series_data if item["label"] == "S21"
    )
    assert restored_series["x_values"].tolist() == pytest.approx(x_values.tolist())
    assert restored_series["values"].tolist() == pytest.approx(values.tolist())
    previous_id = restored_series["file_id"]
    restored.append_plot_data(
        [30.0],
        [{"label": "S21", "file_id": "sweep-b", "values": [0.5 + 0.5j]}],
        title="Large append",
        xlabel="Frequency",
        ylabel="Magnitude",
    )
    restored_ids = [item["file_id"] for item in restored._series_data]
    assert len(restored_ids) == 2
    assert previous_id in restored_ids
    assert len(set(restored_ids)) == 2
    source.close()
    restored.close()
    assert app is not None


def test_loaded_dataset_and_chart_settings_restore_together(memory_qsettings):
    app = QApplication.instance() or QApplication([])
    key = "loaded-overlay-settings"
    base_series = [{
        "label": "S11",
        "file_id": "simulation-run",
        "file_name": "simulation-run",
        "values": [0.1 + 0.2j, 0.3 + 0.4j],
    }]
    source = PlotView("plot_sp", settings_key=key)
    source.set_plot_data(
        [1.0, 2.0], base_series,
        title="Loaded overlay", xlabel="Frequency (GHz)", ylabel="Magnitude",
    )
    source.add_file_data(
        [1.0, 2.0],
        [{
            "label": "S21",
            "file_id": "touchstone-file",
            "file_name": "loaded.s2p",
            "values": [0.5 + 0.25j, 0.75 + 0.5j],
        }],
    )
    source.display_mode_combo.setCurrentText("Magnitude (linear)")
    source._set_legend_scale(82)
    source._set_marker_scale(130)
    source._set_marker(("touchstone-file", "S21"), "^")
    source._set_visible(("touchstone-file", "S21"), False)
    source.close()

    restored = PlotView("plot_sp", settings_key=key)
    restored.set_plot_data(
        [1.0, 2.0], base_series,
        title="Loaded overlay", xlabel="Frequency (GHz)", ylabel="Magnitude",
    )

    overlay = next(
        entry for entry in restored._series_data
        if entry["file_id"] == "touchstone-file"
    )
    assert overlay["values"].tolist() == [0.5 + 0.25j, 0.75 + 0.5j]
    assert overlay["visible"] is False
    assert overlay["marker"] == "^"
    assert restored.display_mode_combo.currentText() == "Magnitude (linear)"
    assert restored._legend_scale_percent == 82
    assert restored._marker_scale_percent == 130
    assert "touchstone-file" in restored._overlay_file_ids
    assert app is not None


def test_append_mode_does_not_cache_the_original_chart_series(memory_qsettings):
    app = QApplication.instance() or QApplication([])
    base_series = [{
        "label": "S11",
        "file_id": "base-run",
        "file_name": "base-run",
        "values": [0.1 + 0.2j, 0.3 + 0.4j],
    }]
    view = PlotView("plot_sp", settings_key="append-cache-excludes-base")
    view.set_plot_data(
        [1.0, 2.0], base_series,
        title="Append", xlabel="Frequency", ylabel="Magnitude",
    )
    view._overlay_file_ids.add("base-run")
    view.append_plot_data(
        [3.0, 4.0],
        [{
            "label": "S21",
            "file_id": "next-run",
            "file_name": "next-run.s2p",
            "values": [0.5 + 0.25j, 0.75 + 0.5j],
        }],
        title="Append", xlabel="Frequency", ylabel="Magnitude",
    )

    view.set_plot_data(
        [1.0, 2.0],
        [dict(base_series[0], values=[0.2 + 0.1j, 0.4 + 0.3j])],
        title="Append", xlabel="Frequency", ylabel="Magnitude",
    )

    assert [(item["file_id"], item["label"]) for item in view._load_appended_series()] == [
        ("next-run::append-1", "S21")
    ]
    base = next(item for item in view._series_data if item["file_id"] == "base-run")
    assert base["values"].tolist() == [0.2 + 0.1j, 0.4 + 0.3j]
    assert len(view._series_data) == 2
    assert app is not None


def test_progressive_partial_overlay_snapshot_preserves_hidden_sibling(memory_qsettings):
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp", settings_key="progressive-overlay-visibility")
    view.set_plot_data(
        [1.0, 2.0],
        [
            {"label": "S21", "file_id": "overlay-1", "file_name": "run.s2p",
             "values": [0.2, 0.3]},
            {"label": "S22", "file_id": "overlay-1", "file_name": "run.s2p",
             "values": [0.4, 0.5]},
        ],
        title="Overlay",
        xlabel="Frequency",
        ylabel="Magnitude",
    )
    view._overlay_file_ids.add("overlay-1")
    view._set_visible(("overlay-1", "S22"), False)

    view.set_progressive_data(
        [1.0, 2.0, 3.0],
        [{"label": "S21", "file_id": "overlay-1", "file_name": "run.s2p",
          "values": [0.25, 0.35, 0.45]}],
        title="Overlay live",
    )

    by_label = {entry["label"]: entry for entry in view._series_data}
    assert by_label["S21"]["values"].tolist() == [0.25, 0.35, 0.45]
    assert by_label["S22"]["values"].tolist() == [0.4, 0.5]
    assert by_label["S22"]["visible"] is False
    assert len(view._series_data) == 2
    assert app is not None


def test_malformed_persisted_chart_payloads_fall_back_to_base_data(memory_qsettings):
    app = QApplication.instance() or QApplication([])
    key = "stale-persisted-chart"
    identity = "\x1f".join(("plot_sp", key))
    digest = chart_view.hashlib.sha256(identity.encode("utf-8")).hexdigest()
    prefix = f"plots/chart_options/{digest}"
    memory_qsettings.update({
        f"{prefix}/appended_series": "z1:bm90LWEtemxpYi1zdHJlYW0=",
        f"{prefix}/marker_positions": "[{bad json]",
        f"{prefix}/legend_position": ["NaN", 3.0],
    })

    view = PlotView("plot_sp", settings_key=key)
    view.set_plot_data(
        [1.0, 2.0], [{"label": "S11", "values": [0.1, 0.2]}],
        title="Base only", xlabel="Frequency", ylabel="Magnitude",
    )

    assert [(entry["file_id"], entry["label"]) for entry in view._series_data] == [
        ("default", "S11")
    ]
    assert view._marker_items == []
    assert view._legend_position is None
    assert app is not None


def test_chart_settings_are_isolated_by_settings_key(memory_qsettings):
    app = QApplication.instance() or QApplication([])
    first = PlotView("plot_sp", settings_key="chart-one")
    first.display_mode_combo.setCurrentText("Magnitude (linear)")
    first._set_legend_scale(72)

    second = PlotView("plot_sp", settings_key="chart-two")
    assert second.display_mode_combo.currentText() == "Magnitude (dB)"
    assert second._settings_dialog.legend_scale_slider.value() == 100
    second.display_mode_combo.setCurrentText("Real")
    second._set_legend_scale(88)

    restored_first = PlotView("plot_sp", settings_key="chart-one")
    restored_second = PlotView("plot_sp", settings_key="chart-two")
    assert restored_first.display_mode_combo.currentText() == "Magnitude (linear)"
    assert restored_first._settings_dialog.legend_scale_slider.value() == 72
    assert restored_second.display_mode_combo.currentText() == "Real"
    assert restored_second._settings_dialog.legend_scale_slider.value() == 88
    assert app is not None


def test_chart_settings_are_isolated_by_plot_type(memory_qsettings):
    app = QApplication.instance() or QApplication([])
    s_parameter_chart = PlotView("plot_sp", settings_key="shared-output")
    s_parameter_chart.display_mode_combo.setCurrentText("Magnitude (linear)")
    s_parameter_chart._set_legend_scale(68)

    vswr_chart = PlotView("plot_vswr", settings_key="shared-output")
    assert vswr_chart.display_mode_combo.currentText() == "VSWR"
    assert vswr_chart._settings_dialog.legend_scale_slider.value() == 100

    restored_s_parameter_chart = PlotView("plot_sp", settings_key="shared-output")
    assert restored_s_parameter_chart.display_mode_combo.currentText() == "Magnitude (linear)"
    assert restored_s_parameter_chart._settings_dialog.legend_scale_slider.value() == 68
    assert app is not None


def test_invalid_chart_settings_fall_back_and_d_b_never_restores_y_log(memory_qsettings):
    app = QApplication.instance() or QApplication([])
    key = "stale-chart"
    identity = "\x1f".join(("plot_sp", key))
    digest = chart_view.hashlib.sha256(identity.encode("utf-8")).hexdigest()
    prefix = f"plots/chart_options/{digest}"
    memory_qsettings.update({
        f"{prefix}/display_mode": "Removed mode",
        f"{prefix}/x_scale": "Sideways",
        f"{prefix}/y_scale": "Log",
        f"{prefix}/x_auto_range": False,
        f"{prefix}/y_auto_range": False,
        f"{prefix}/x_range": [10.0, 1.0],
        f"{prefix}/y_range": [10.0, 1.0],
        f"{prefix}/legend_scale_percent": 150,
        f"{prefix}/remove_logo": "not a boolean",
    })

    view = PlotView("plot_sp", settings_key=key)

    assert view.display_mode_combo.currentText() == "Magnitude (dB)"
    assert view.x_scale_combo.currentText() == "Linear"
    assert view.y_scale_combo.currentText() == "Linear"
    assert not view.y_scale_combo.isEnabled()
    assert view._x_auto_range and view._y_auto_range
    assert view._x_range is None and view._y_range is None
    assert view._settings_dialog.legend_scale_slider.value() == 100
    assert not view._settings_dialog.remove_logo_checkbox.isChecked()
    assert app is not None


def test_matplotlib_chart_x_scale_persists_across_recreation(memory_qsettings):
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_ff", settings_key="farfield-output")
    view.x_scale_combo.setCurrentText("Log")

    restored = PlotView("plot_ff", settings_key="farfield-output")

    assert restored.x_scale_combo.currentText() == "Log"
    assert app is not None


def test_empty_plot_view_accepts_its_first_progressive_data_chunk():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.set_plot_data(
        [], [], title="Waiting for results", xlabel="Frequency (GHz)", ylabel="S-parameter"
    )

    assert view._series_data == []
    assert view.plot_widget.listDataItems() == []

    view.set_progressive_data(
        [1.0, 2.0],
        [{"label": "S11", "values": [0.1 + 0j, 0.2 + 0j]}],
        title="First results",
    )

    assert len(view.plot_widget.listDataItems()) == 1
    assert view._series_data[0]["values"].tolist() == [0.1 + 0j, 0.2 + 0j]
    assert app is not None


def test_smith_view_draws_smith_grid_with_pyqtgraph_without_emerge_or_matplotlib(monkeypatch):
    app = QApplication.instance() or QApplication([])
    original_import_module = chart_view.importlib.import_module

    def reject_plot_module(module_name, *args, **kwargs):
        if module_name == "emerge.plot":
            raise AssertionError("Smith charts must not import EMERGE plotting")
        return original_import_module(module_name, *args, **kwargs)

    monkeypatch.setattr(chart_view.importlib, "import_module", reject_plot_module)
    view = PlotView("smith")
    view.set_plot_data(
        [1.0, 2.0],
        [{"label": "S11", "values": [0.2 + 0.1j, -0.3 + 0.4j]}],
        title="Smith",
        xlabel="Frequency (Hz)",
        ylabel="Reflection",
    )

    assert view.figure is None
    assert isinstance(view.plot_widget, pg.PlotWidget)
    assert view.axes.getViewBox().state["aspectLocked"] == 1.0
    assert view.axes.titleLabel.text == "Smith"
    assert isinstance(view._legend_item, pg.LegendItem)
    assert len(view._smith_grid_items) > 10
    smith_grid_curves = [
        item for item in view._smith_grid_items if isinstance(item, pg.PlotDataItem)
    ]
    assert len(smith_grid_curves) >= 40
    assert all(
        item.opts["pen"].style() == Qt.PenStyle.SolidLine
        for item in smith_grid_curves
    )
    assert sum(len(item.getData()[0]) >= 721 for item in smith_grid_curves) == 40
    assert len(view._curve_items) == 1
    assert not view.add_marker_button.isEnabled()
    assert not view.remove_markers_button.isEnabled()
    assert not view.display_mode_combo.isEnabled()
    assert not view._settings_dialog.axis_ranges_button.isEnabled()
    gamma_real, gamma_imag = view._curve_items[0].getData()
    assert gamma_real == pytest.approx([0.2, -0.3])
    assert gamma_imag == pytest.approx([0.1, 0.4])
    assert not view.y_scale_combo.isEnabled()
    assert app is not None


def test_smith_double_click_does_not_add_a_frequency_marker():
    app = QApplication.instance() or QApplication([])
    view = PlotView("smith")

    class DoubleClick:
        @staticmethod
        def double():
            return True

    view._on_plot_scene_clicked(DoubleClick())

    assert view._marker_items == []
    assert app is not None


def test_clear_data_removes_series_before_next_progressive_run():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.set_plot_data(
        [1.0, 2.0],
        [{"label": "S11", "values": [0.1 + 0j, 0.2 + 0j]}],
        title="Sweep",
        xlabel="Frequency (GHz)",
        ylabel="S-parameter",
    )

    view.clear_data()

    assert view._x_values.size == 0
    assert view._series_data == []
    assert view.plot_widget.listDataItems() == []
    assert view._available_trace_list.count() == 0
    assert view._selected_trace_list.count() == 0
    assert app is not None


def test_sparameter_plot_view_emits_touchstone_load_request():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    spy = QSignalSpy(view.touchstone_load_requested)
    view.set_plot_data(
        [1.0], [{"label": "S11", "values": [0.1 + 0j]}],
        title="Sweep", xlabel="Frequency", ylabel="Magnitude",
    )
    view.show()
    view.settings_button.click()
    app.processEvents()

    assert view._settings_dialog.isVisible()
    assert [view._settings_dialog.tabs.tabText(index) for index in range(2)] == [
        "Setup", "Option"
    ]
    assert view._selected_trace_list.item(0).text() == "S11 - Results"
    assert view._settings_dialog.isAncestorOf(view.display_mode_combo)
    assert view.load_touchstone_button is not None
    view.load_touchstone_button.click()

    assert spy.count() == 1
    view._settings_dialog.close()
    assert app is not None


def test_pyqtgraph_log_axes_and_marker_readout_use_data_coordinates():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.set_plot_data(
        [1.0, 10.0, 100.0],
        [
            {"label": "S11", "values": [1.0, 10.0, 100.0]},
            {"label": "S22", "values": [10.0, 100.0], "x_values": [10.0, 100.0]},
        ],
        title="Log chart",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )

    view.display_mode_combo.setCurrentText("Magnitude (linear)")
    view.x_scale_combo.setCurrentText("Log")
    view.y_scale_combo.setCurrentText("Log")
    assert view.axes.getAxis("bottom").logMode
    assert view.axes.getAxis("left").logMode

    view._add_marker(1.0)

    marker_text = _marker_readout_text(view._marker_items[0][1])
    assert "Frequency (GHz): 10" in marker_text
    assert "S11: 10" in marker_text
    assert _legend_labels(view) == ["S11 - Results", "S22 - Results"]
    assert view._marker_items[0][1].isVisible()

    view._marker_items[0][0].setValue(2.0)
    moved_marker_text = _marker_readout_text(view._marker_items[0][1])
    assert "Frequency (GHz): 100" in moved_marker_text
    assert "S11: 100" in moved_marker_text

    view._marker_items[0][0].setValue(1.5)
    assert "Frequency (GHz): 31.6228" in _marker_readout_text(view._marker_items[0][1])
    assert "S11: 31.62" in _marker_readout_text(view._marker_items[0][1])
    view.x_scale_combo.setCurrentText("Linear")
    assert view._marker_items[0][0].value() == pytest.approx(10 ** 1.5)
    assert "Frequency (GHz): 31.6228" in _marker_readout_text(view._marker_items[0][1])
    view.x_scale_combo.setCurrentText("Log")
    assert view._marker_items[0][0].value() == pytest.approx(1.5)
    view._marker_items[0][0].setValue(0.5)
    assert "S22: n/a" in _marker_readout_text(view._marker_items[0][1])
    assert app is not None


def test_marker_readouts_renumber_after_removal_without_reusing_widget_names():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.set_plot_data(
        [1.0, 2.0],
        [{"label": "S11", "values": [1.0, 2.0]}],
        title="Chart",
        xlabel="Frequency",
        ylabel="Magnitude",
    )
    view._add_marker(1.0)
    view._add_marker(2.0)
    first_marker, second_marker = view._marker_items
    assert "Marker 1" in _marker_readout_text(first_marker[1])
    assert "Marker 2" in _marker_readout_text(second_marker[1])

    event = Mock()
    event.button.return_value = Qt.MouseButton.RightButton
    view._on_marker_clicked(first_marker, event)
    assert first_marker[1].scene() is None
    assert "Marker 1" in _marker_readout_text(second_marker[1])

    view._add_marker(1.5)
    assert "Marker 1" in _marker_readout_text(view._marker_items[0][1])
    assert "Marker 2" in _marker_readout_text(view._marker_items[1][1])
    assert view._marker_items[1][1].objectName() != second_marker[1].objectName()
    remaining_readouts = [label for _line, label in view._marker_items]
    view._clear_markers()
    assert all(readout.scene() is None for readout in remaining_readouts)
    assert app is not None


def test_manual_ranges_are_applied_in_log_axis_coordinates():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.set_plot_data(
        [1.0, 10.0, 100.0],
        [{"label": "S11", "values": [1.0, 10.0, 100.0]}],
        title="Log chart",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )
    view.display_mode_combo.setCurrentText("Magnitude (linear)")
    view.x_scale_combo.setCurrentText("Log")
    view.y_scale_combo.setCurrentText("Log")
    view._x_auto_range = False
    view._y_auto_range = False
    view._x_range = (10.0, 100.0)
    view._y_range = (10.0, 100.0)
    view._redraw(reset_range=True)

    x_range, y_range = view.axes.getViewBox().viewRange()
    assert x_range == pytest.approx([1.0, 2.0])
    assert y_range == pytest.approx([1.0, 2.0])
    assert app is not None


def test_axis_range_dialog_accepts_manual_limits():
    app = QApplication.instance() or QApplication([])
    dialog = _AxisRangeDialog(None, None, False, False)
    dialog.x_auto.setCurrentText("Manual")
    dialog.x_min.setText("1")
    dialog.x_max.setText("10")
    dialog._accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.x_range == (1.0, 10.0)
    assert dialog.y_range is None
    assert app is not None


def test_plot_view_groups_parameters_by_file_and_keeps_independent_frequency_axes():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.set_plot_data(
        [1.0, 2.0],
        [
            {"label": "S11", "values": [0.1, 0.2], "visible": True,
             "configured": True, "file_name": "first.s2p", "file_id": "first"},
            {"label": "S21", "values": [0.3, 0.4], "visible": False,
             "configured": False, "file_name": "first.s2p", "file_id": "first"},
        ],
        title="Overlay",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )

    view.add_file_data(
        [10.0, 20.0],
        [
            {"label": "S11", "values": [0.5, 0.6], "visible": True,
             "configured": True, "file_name": "second.s2p", "file_id": "second"},
            {"label": "S22", "values": [0.7, 0.8], "visible": False,
             "configured": False, "file_name": "second.s2p", "file_id": "second"},
        ],
    )

    assert _legend_labels(view) == ["S11 - first.s2p", "S11 - second.s2p"]
    assert view.selected_parameters() == ["S11"]
    assert view._settings_dialog.dataset_combo.count() == 2
    assert view._settings_dialog.dataset_combo.currentData() == "second"
    assert [
        view._selected_trace_list.item(index).text()
        for index in range(view._selected_trace_list.count())
    ] == ["S11 - first.s2p", "S11 - second.s2p"]
    assert [
        view._available_trace_list.item(index).text()
        for index in range(view._available_trace_list.count())
    ] == ["S22"]
    view._settings_dialog.dataset_combo.setCurrentIndex(0)
    assert [
        view._available_trace_list.item(index).text()
        for index in range(view._available_trace_list.count())
    ] == ["S21"]
    assert len(view.plot_widget.listDataItems()) == 2
    second_curve = view.plot_widget.listDataItems()[1]
    assert second_curve.getData()[0] == pytest.approx([10.0, 20.0])
    assert not any(button.text() == "Color" for button in view.findChildren(type(view.fit_view_button)))
    assert app is not None


def test_trace_lists_filter_and_move_parameters_in_both_directions():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.set_plot_data(
        [1.0],
        [
            {"label": "S11", "values": [0.1], "file_name": "first.s2p", "file_id": "first",
             "visible": False},
            {"label": "S22", "values": [0.2], "file_name": "second.s2p", "file_id": "second",
             "visible": False},
        ],
        title="Overlay",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )

    view._settings_dialog.trace_search.setText("S22")
    assert view._settings_dialog.dataset_combo.count() == 2
    view._settings_dialog.dataset_combo.setCurrentIndex(1)
    assert view._available_trace_list.count() == 1
    assert not view._available_trace_list.item(0).isHidden()

    view._available_trace_list.item(0).setSelected(True)
    view._settings_dialog.add_trace_button.click()
    assert view._series_data[1]["visible"]
    assert view._selected_trace_list.count() == 1
    assert view._available_trace_list.count() == 0
    assert view._settings_dialog.configure_trace_button.isEnabled()

    selected_s22 = next(
        index for index in range(view._selected_trace_list.count())
        if view._selected_trace_list.item(index).data(Qt.ItemDataRole.UserRole)
        == ("second", "S22")
    )
    view._selected_trace_list.item(selected_s22).setSelected(True)
    view._settings_dialog.remove_trace_button.click()
    assert not view._series_data[1]["visible"]
    assert view._available_trace_list.count() == 1
    assert view._selected_trace_list.count() == 0
    assert app is not None


def test_selected_trace_order_buttons_reorder_plot_and_preserve_order_on_updates():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    series = [
        {"label": label, "values": [value], "file_name": f"run-{index}.s2p",
         "file_id": f"run-{index}", "visible": True}
        for index, (label, value) in enumerate(
            (("S11", 0.1), ("S21", 0.2), ("S22", 0.3))
        )
    ]
    view.set_plot_data(
        [1.0], series, title="Sweep", xlabel="Frequency (GHz)", ylabel="Magnitude"
    )

    up_button = view._settings_dialog.move_trace_up_button
    down_button = view._settings_dialog.move_trace_down_button
    assert not up_button.isEnabled()
    assert not down_button.isEnabled()
    view._selected_trace_list.setCurrentRow(1)
    assert up_button.isEnabled() and down_button.isEnabled()
    up_button.click()

    selected_labels = [
        view._selected_trace_list.item(index).text()
        for index in range(view._selected_trace_list.count())
    ]
    assert selected_labels == ["S21 - run-1.s2p", "S11 - run-0.s2p", "S22 - run-2.s2p"]
    assert [entry["label"] for entry in view._series_data] == ["S21", "S11", "S22"]
    assert [curve.name() for curve in view._curve_items] == [
        "S21 - run-1.s2p", "S11 - run-0.s2p", "S22 - run-2.s2p"
    ]

    view.set_progressive_data(
        [1.0, 2.0],
        [
            {**entry, "values": [entry["values"][0], entry["values"][0] * 2]}
            for entry in series
        ],
        title="Sweep update",
    )
    assert [entry["label"] for entry in view._series_data] == ["S21", "S11", "S22"]

    view._selected_trace_list.setCurrentRow(0)
    down_button.click()
    assert [entry["label"] for entry in view._series_data] == ["S11", "S21", "S22"]
    assert app is not None


def test_configure_selected_trace_applies_its_color_and_marker(monkeypatch):
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.set_plot_data(
        [1.0],
        [
            {"label": "S11", "values": [0.1], "file_name": "first.s2p", "file_id": "first"},
            {"label": "S22", "values": [0.2], "file_name": "second.s2p", "file_id": "second"},
        ],
        title="Overlay",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )

    class AcceptedTraceSettings:
        def __init__(self, _color, _marker, _parent):
            self.color = "#123456"
            self.marker = "s"

        def setWindowTitle(self, _title):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(chart_view, "_TraceSettingsDialog", AcceptedTraceSettings)
    first_item = view._selected_trace_list.item(0)
    first_item.setSelected(True)
    view._selected_trace_list.setCurrentItem(first_item)
    view._settings_dialog.configure_trace_button.click()

    first = next(entry for entry in view._series_data if entry["file_id"] == "first")
    second = next(entry for entry in view._series_data if entry["file_id"] == "second")
    assert (first["color"], first["marker"]) == ("#123456", "s")
    assert second["color"] != "#123456"
    assert not view._settings_dialog.styleSheet()
    assert app is not None


def test_added_file_overlay_survives_progressive_sweep_updates():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.set_plot_data(
        [1.0, 2.0],
        [{"label": "S11", "values": [0.1, 0.2], "file_name": "live", "file_id": "live"}],
        title="Live sweep",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )
    view.add_file_data(
        [10.0, 20.0],
        [{"label": "S11", "values": [0.3, 0.4], "file_name": "reference.s2p",
          "file_id": "reference", "x_values": [10.0, 20.0]}],
    )

    view.set_progressive_data(
        [1.0, 2.0, 3.0],
        [{"label": "S11", "values": [0.1, 0.2, 0.3], "file_name": "live", "file_id": "live"}],
        title="Live sweep update",
    )

    assert {(item["file_id"], item["label"]) for item in view._series_data} == {
        ("live", "S11"), ("reference", "S11")
    }
    reference = next(item for item in view._series_data if item["file_id"] == "reference")
    assert reference["x_values"].tolist() == [10.0, 20.0]
    assert app is not None


def test_append_plot_data_keeps_prior_datasets_and_distinguishes_repeated_runs():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    view.set_plot_data(
        [1.0, 2.0],
        [{"label": "S11", "values": [0.1, 0.2], "file_name": "first", "file_id": "first"}],
        title="First run",
        xlabel="Frequency (GHz)",
        ylabel="Magnitude",
    )
    next_run = [{
        "label": "S11", "values": [0.3, 0.4], "file_name": "second", "file_id": "second"
    }]

    for title in ("Second run", "Third run"):
        view.append_plot_data(
            [1.0, 2.0], next_run, title=title, xlabel="Frequency (GHz)", ylabel="Magnitude"
        )

    assert len(view._series_data) == 3
    assert [item["file_name"] for item in view._series_data] == ["first", "second", "second"]
    assert len({item["file_id"] for item in view._series_data}) == 3
    assert view._settings_dialog.dataset_combo.currentText() == "second"
    assert view.append_button.isCheckable()
    assert app is not None


def test_smith_overlay_accepts_independent_frequency_grids():
        app = QApplication.instance() or QApplication([])
        view = PlotView("smith")
        view.set_plot_data(
                [1.0, 2.0],
                [{"label": "S11", "values": [0.2 + 0.1j, -0.3 + 0.4j],
                    "file_name": "first.s2p", "file_id": "first", "x_values": [1.0, 2.0]}],
                title="Smith overlay",
                xlabel="Frequency (GHz)",
                ylabel="Reflection",
        )

        view.add_file_data(
                [10.0, 20.0, 30.0],
                [{"label": "S11", "values": [0.1j, 0.2j, 0.3j],
                    "file_name": "second.s2p", "file_id": "second", "x_values": [10.0, 20.0, 30.0]}],
        )

        trace_labels = [item.name() for item in view._curve_items]
        assert trace_labels == ["S11 - first.s2p", "S11 - second.s2p"]
        assert app is not None


def test_remove_loaded_dataset_removes_all_traces_and_preserves_equation_definition(
    memory_qsettings,
):
    app = QApplication.instance() or QApplication([])
    settings_key = "remove-loaded-dataset-equation"
    base = {
        "file_id": "base-run",
        "file_name": "Base run",
        "label": "S11",
        "values": [0.1 + 0j, 0.2 + 0j],
    }
    view = PlotView("plot_sp", settings_key=settings_key)
    view.set_plot_data(
        [1.0, 2.0], [base], title="Dataset removal", xlabel="Frequency", ylabel="Magnitude"
    )
    view.add_file_data(
        [1.0, 2.0],
        [
            {"file_id": "loaded-run", "file_name": "Loaded run", "label": "S21",
             "values": [0.3 + 0j, 0.4 + 0j]},
            {"file_id": "loaded-run", "file_name": "Loaded run", "label": "S22",
             "values": [0.5 + 0j, 0.6 + 0j], "visible": False},
        ],
    )

    dialog = view._settings_dialog
    dialog.equation_parameter_combo.setCurrentIndex(1)
    dialog.equation_variable_name_edit.setText("s21")
    view._add_equation_variable()
    dialog.equation_name_edit.setText("Twice S21")
    dialog.equation_formula_edit.setText("s21 * 2")
    view._save_equation_from_editor()
    saved_equation = [dict(view._equations[0], variables=[dict(item) for item in view._equations[0]["variables"]])]
    assert any(entry["label"] == "Twice S21" for entry in view._series_data)

    dataset_combo = dialog.dataset_combo
    remove_button = dialog.remove_dataset_button
    base_index = dataset_combo.findData("base-run")
    dataset_combo.setCurrentIndex(base_index)
    assert not remove_button.isEnabled()
    loaded_index = dataset_combo.findData("loaded-run")
    dataset_combo.setCurrentIndex(loaded_index)
    assert remove_button.isEnabled()
    remove_button.click()

    assert [(entry["file_id"], entry["label"]) for entry in view._series_data] == [
        ("base-run", "S11")
    ]
    assert "loaded-run" not in view._overlay_file_ids
    assert "loaded-run" not in view._appended_file_ids
    assert not remove_button.isEnabled()
    assert view._equations == saved_equation
    assert view._equation_errors and "not loaded" in view._equation_errors[0]
    assert "Twice S21" in dialog.equation_error_label.text()

    restored = PlotView("plot_sp", settings_key=settings_key)
    restored.set_plot_data(
        [1.0, 2.0], [base], title="Dataset removal", xlabel="Frequency", ylabel="Magnitude"
    )
    assert restored._equations == saved_equation
    assert restored._equation_errors and "not loaded" in restored._equation_errors[0]
    view.close()
    restored.close()
    assert app is not None


def test_remove_appended_dataset_clears_cache_and_all_trace_list_entries(memory_qsettings):
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp", settings_key="remove-appended-dataset")
    view.set_plot_data(
        [1.0, 2.0],
        [{"file_id": "base-run", "file_name": "Base run", "label": "S11",
          "values": [0.1, 0.2]}],
        title="Append removal",
        xlabel="Frequency",
        ylabel="Magnitude",
    )
    view.append_plot_data(
        [1.0, 2.0],
        [
            {"file_id": "appended-run", "file_name": "Appended run", "label": "S21",
             "values": [0.3, 0.4]},
            {"file_id": "appended-run", "file_name": "Appended run", "label": "S22",
             "values": [0.5, 0.6], "visible": False},
        ],
        title="Append removal",
        xlabel="Frequency",
        ylabel="Magnitude",
    )
    appended_id = next(
        entry["file_id"] for entry in view._series_data if entry["label"] == "S21"
    )
    assert view._appended_series_path().is_file()
    assert any(
        view._available_trace_list.item(index).text() == "S22"
        for index in range(view._available_trace_list.count())
    )
    assert any(
        view._selected_trace_list.item(index).text().startswith("S21")
        for index in range(view._selected_trace_list.count())
    )

    view._settings_dialog.remove_dataset_button.click()

    assert [(entry["file_id"], entry["label"]) for entry in view._series_data] == [
        ("base-run", "S11")
    ]
    assert view._load_appended_series() == []
    assert not view._appended_series_path().exists()
    assert appended_id not in view._overlay_file_ids
    assert appended_id not in view._appended_file_ids
    assert view._available_trace_list.count() == 0
    assert [view._selected_trace_list.item(index).text()
            for index in range(view._selected_trace_list.count())] == ["S11 - Base run"]
    assert not view._settings_dialog.remove_dataset_button.isEnabled()
    assert app is not None