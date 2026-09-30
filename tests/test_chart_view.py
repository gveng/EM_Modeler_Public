import os
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import pyqtgraph as pg
import emerge.plot as emerge_plot
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog

from em3d_modeler.ui import chart_view
from em3d_modeler.ui.chart_view import PlotView, _AxisRangeDialog, _transform_values


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
    assert view.axis_settings_button is not None
    assert view.add_marker_button is not None
    assert view.fit_view_button is not None
    assert view.findChild(QCheckBox).text() == "S11"
    assert isinstance(view.plot_widget, pg.PlotWidget)
    assert len(view.plot_widget.listDataItems()) == 1
    plot_api.assert_not_called()
    assert app is not None


def test_plot_size_is_user_controlled_and_d_b_mode_forces_linear_y(monkeypatch):
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
    view = PlotView("plot_sp")
    view.resize(1600, 1100)
    view.show()
    app.processEvents()
    initial_size = view.canvas.size()

    view.resize(view.width() + 300, view.height() + 300)
    app.processEvents()

    assert view.canvas.size() == initial_size
    assert (initial_size.width(), initial_size.height()) == (1000, 600)

    view.plot_width_spin.setValue(1250)
    view.plot_height_spin.setValue(750)
    assert view.canvas.size().width() == 1250
    assert view.canvas.size().height() == 750
    assert stored_settings[f"{view._plot_size_setting_key}/width"] == 1250
    assert stored_settings[f"{view._plot_size_setting_key}/height"] == 750

    view.resize(600, 450)
    app.processEvents()
    compact_size = view.canvas.size()
    assert compact_size.width() < 1250
    assert compact_size.height() < 750
    assert compact_size.width() / compact_size.height() == pytest.approx(1250 / 750, rel=0.01)

    view.resize(1600, 1100)
    app.processEvents()
    assert view.canvas.size().width() == 1250
    assert view.canvas.size().height() == 750

    view.display_mode_combo.setCurrentText("Magnitude (linear)")
    assert view.y_scale_combo.isEnabled()
    view.y_scale_combo.setCurrentText("Log")
    assert view.y_scale_combo.currentText() == "Log"
    view.display_mode_combo.setCurrentText("Magnitude (dB)")
    assert view.y_scale_combo.currentText() == "Linear"
    assert not view.y_scale_combo.isEnabled()
    assert not view.axes.getAxis("left").logMode
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
    assert len(view.findChildren(QCheckBox)) == 1
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


def test_plot_view_normalizes_invalid_trace_color_before_styling_checkbox():
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
    assert view._trace_checkboxes[("default", "S11")].styleSheet().find("not-a-color") == -1
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
    assert view.axes.legend is None
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
    assert view.canvas.width() == view.plot_width_spin.value()
    assert view.canvas.height() == view.plot_height_spin.value()
    assert view.plot_width_spin.value() == view.plot_height_spin.value() == 700
    assert view.axis_settings_button is None
    assert view.add_marker_button is None
    assert view.display_mode_combo.isHidden()
    gamma_real, gamma_imag = view._curve_items[0].getData()
    assert gamma_real == pytest.approx([0.2, -0.3])
    assert gamma_imag == pytest.approx([0.1, 0.4])
    assert view.canvas.width() == view.plot_width_spin.value()
    assert view.canvas.height() == view.plot_height_spin.value()
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
    assert view.findChildren(QCheckBox) == []
    assert app is not None


def test_sparameter_plot_view_emits_touchstone_load_request():
    app = QApplication.instance() or QApplication([])
    view = PlotView("plot_sp")
    spy = QSignalSpy(view.touchstone_load_requested)

    assert view.load_touchstone_button is not None
    view.load_touchstone_button.click()

    assert spy.count() == 1
    assert app is not None


def test_pyqtgraph_log_axes_and_marker_readout_use_data_coordinates():
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
    assert view.axes.getAxis("bottom").logMode
    assert view.axes.getAxis("left").logMode

    view._add_marker(1.0)

    marker_text = view._marker_items[0][1].toPlainText()
    assert "Frequency (GHz): 10" in marker_text
    assert "S11: 10" in marker_text
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

    assert view.selected_parameters() == ["S11"]
    assert [box.text() for box in view.findChildren(QCheckBox)] == ["S11", "S21", "S11", "S22"]
    assert len(view.plot_widget.listDataItems()) == 2
    second_curve = view.plot_widget.listDataItems()[1]
    assert second_curve.getData()[0] == pytest.approx([10.0, 20.0])
    assert not any(button.text() == "Color" for button in view.findChildren(type(view.fit_view_button)))
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
        assert trace_labels == ["first.s2p - S11", "second.s2p - S11"]
        assert app is not None