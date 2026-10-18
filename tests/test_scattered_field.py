import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
)

from em3d_modeler.emerge.python_script_exporter import (
    build_clean_emerge_python_script,
    export_emerge_python_script,
)
from em3d_modeler.emerge.simulation_validator import validate_simulation
from em3d_modeler.scene.em_objects import BoxObject
from em3d_modeler.ui.main_window import MainWindow
from em3d_modeler.ui.project_tree_widget import ProjectTreeWidget


def _scattered_settings():
    return {
        "simulations": [{
            "name": "PlaneWave",
            "type": "ScatteredField",
            "enabled": True,
            "Fmin_GHz": 1.0,
            "Fmax_GHz": 2.0,
            "NumberOfPoints": 3,
            "ScatteredDefinition": "EA",
            "ScatteredThetaDeg": 90.0,
            "ScatteredPhiDeg": 0.0,
            "ScatteredPsiDeg": 0.0,
            "ScatteredPowerDensity_W_m2": 1.0,
            "ScatteredPhaseOrigin": "GCS",
        }],
        "ports": [],
        "boundaries": {
            "Xmin": "Radiation",
            "Xmax": "Radiation",
            "Ymin": "Radiation",
            "Ymax": "Radiation",
            "Zmin": "Radiation",
            "Zmax": "Radiation",
        },
        "open_region": {"enabled": True, "object": "Air"},
        "outputs": [{
            "name": "Scattered far field",
            "simulation": "PlaneWave",
            "plot_type": "plot_ff",
            "enabled": True,
            "params": {"integration_object": "Target"},
        }],
        "runtime": {"solver": "PARDISO"},
    }


def test_scattered_field_simulation_dialog_persists_ea_sph_settings(monkeypatch):
    app = QApplication.instance() or QApplication([])
    tree = ProjectTreeWidget()

    def accept_scattered(dialog):
        definition = dialog.findChild(
            QComboBox, "scatteredFieldDefinition"
        )
        assert definition.currentData() == "EA"
        theta = dialog.findChild(QDoubleSpinBox, "scatteredFieldTheta")
        assert theta.value() == pytest.approx(90.0)
        definition.setCurrentIndex(definition.findData("SPH"))
        theta.setValue(45.0)
        dialog.findChild(
            QDoubleSpinBox, "scatteredFieldPowerDensity"
        ).setValue(2.5)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec_", accept_scattered)
    result = tree._simulation_dialog_data(
        {"name": "PlaneWave", "type": "ScatteredField"},
        title="Edit Simulation",
    )

    assert result["type"] == "ScatteredField"
    assert result["ScatteredDefinition"] == "SPH"
    assert result["ScatteredThetaDeg"] == pytest.approx(45.0)
    assert result["ScatteredPhiDeg"] == pytest.approx(0.0)
    assert result["ScatteredPsiDeg"] == pytest.approx(0.0)
    assert result["ScatteredPowerDensity_W_m2"] == pytest.approx(2.5)
    assert result["ScatteredPhaseOrigin"] == "GCS"
    assert app is not None
    tree.close()


def test_scattered_field_farfield_output_requires_and_saves_surface(monkeypatch):
    app = QApplication.instance() or QApplication([])
    tree = ProjectTreeWidget()
    tree.set_scene_object_names(["Air", "Target"])
    tree.load_settings({"simulations": [{
        "name": "PlaneWave",
        "type": "ScatteredField",
    }]})

    def accept_farfield(dialog):
        plot_type = next(
            combo for combo in dialog.findChildren(QComboBox)
            if combo.findText("plot_ff_3d") >= 0
        )
        plot_type.setCurrentText("plot_ff_3d")
        integration_object = dialog.findChild(
            QComboBox, "farfieldIntegrationObject"
        )
        form = dialog.findChild(QFormLayout)
        integration_row = form.getWidgetPosition(integration_object)[0]
        assert form.isRowVisible(integration_row)
        colormap = dialog.findChild(QComboBox, "eigenFieldColormap")
        colormap_row = form.getWidgetPosition(colormap)[0]
        assert not form.isRowVisible(colormap_row)
        integration_object.setCurrentIndex(
            integration_object.findData("Target")
        )
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec_", accept_farfield)
    result = tree._output_dialog_data(
        {
            "name": "Far field",
            "simulation": "PlaneWave",
            "plot_type": "plot_ff",
        },
        title="Add Output Plot",
    )

    assert result["plot_type"] == "plot_ff_3d"
    assert result["params"]["integration_object"] == "Target"
    assert app is not None
    tree.close()


def test_scattered_field_3d_output_has_frequency_without_farfield_controls(monkeypatch):
    app = QApplication.instance() or QApplication([])
    tree = ProjectTreeWidget()
    tree.load_settings({"simulations": [
        {"name": "PlaneWave", "type": "ScatteredField"},
        {"name": "Sweep", "type": "Sweep"},
    ]})

    def accept_scattered_field_3d(dialog):
        plot_type = next(
            combo for combo in dialog.findChildren(QComboBox)
            if combo.findText("plot_sf_3d") >= 0
        )
        assert plot_type.findText("plot_sf_3d") >= 0
        plot_type.setCurrentText("plot_sf_3d")
        form = dialog.findChild(QFormLayout)
        frequency = dialog.findChild(QDoubleSpinBox, "outputFrequency")
        frequency_row = form.getWidgetPosition(frequency)[0]
        integration = dialog.findChild(
            QComboBox, "farfieldIntegrationObject"
        )
        integration_row = form.getWidgetPosition(integration)[0]
        plane = dialog.findChild(QComboBox, "outputPlane")
        plane_row = form.getWidgetPosition(plane)[0]
        polar = dialog.findChild(QComboBox, "outputPolarView")
        polar_row = form.getWidgetPosition(polar)[0]
        assert form.isRowVisible(frequency_row)
        assert not form.isRowVisible(integration_row)
        assert not form.isRowVisible(plane_row)
        assert not form.isRowVisible(polar_row)
        assert dialog.findChild(QLabel, "outputFrequencyLabel").text() == "Frequency"
        frequency.setValue(1.75)

        simulation = next(
            combo for combo in dialog.findChildren(QComboBox)
            if combo.findText("PlaneWave") >= 0
            and combo.findText("Sweep") >= 0
            and combo is not plot_type
        )
        simulation.setCurrentText("Sweep")
        assert plot_type.findText("plot_sf_3d") == -1
        assert plot_type.currentText() == "plot_sp"
        simulation.setCurrentText("PlaneWave")
        assert plot_type.findText("plot_sf_3d") >= 0
        plot_type.setCurrentText("plot_sf_3d")
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec_", accept_scattered_field_3d)
    result = tree._output_dialog_data(
        {
            "name": "3D scattered field",
            "simulation": "PlaneWave",
            "plot_type": "plot_ff",
        },
        title="Add Output Plot",
    )

    assert result["plot_type"] == "plot_sf_3d"
    assert result["params"]["frequency_GHz"] == pytest.approx(1.75)
    assert "integration_object" not in result["params"]
    assert app is not None
    tree.close()


def test_scattered_field_3d_output_needs_no_integration_object():
    air = BoxObject("Air", 0, 0, 0, 10, 10, 10, "AIR")
    settings = _scattered_settings()
    settings["outputs"][0] = {
        "name": "3D scattered field",
        "simulation": "PlaneWave",
        "plot_type": "plot_sf_3d",
        "enabled": True,
        "params": {"frequency_GHz": 1.5},
    }

    findings = validate_simulation([air], settings)

    output_findings = [
        item for item in findings
        if item.category == "Outputs" and "3D scattered-field" in item.message
    ]
    assert len(output_findings) == 1
    assert output_findings[0].severity == "OK"
    assert not any(
        item.severity == "ERROR"
        and item.category == "Outputs"
        and "integration" in item.message.lower()
        for item in findings
    )


def test_scattered_field_3d_output_is_rejected_for_non_scattered_job():
    settings = _scattered_settings()
    settings["simulations"][0]["type"] = "Sweep"
    settings["outputs"][0].update({
        "plot_type": "plot_sf_3d",
        "params": {"frequency_GHz": 1.5},
    })

    findings = validate_simulation([], settings)

    assert any(
        item.severity == "ERROR"
        and item.category == "Outputs"
        and "plot_sf_3d is available only" in item.message
        for item in findings
    )


def test_scattered_field_validator_accepts_enclosing_air_and_closed_surface():
    air = BoxObject("Air", 0, 0, 0, 10, 10, 10, "AIR")
    target = BoxObject("Target", 4, 4, 4, 6, 6, 6, "PEC")

    findings = validate_simulation([air, target], _scattered_settings())

    assert not [item for item in findings if item.severity == "ERROR"]
    assert any(
        item.severity == "OK"
        and "closed far-field integration surface 'Target'" in item.message
        for item in findings
    )


def test_scattered_field_validator_rejects_pml_boundary_and_pml_shell():
    air = BoxObject("Air", 0, 0, 0, 10, 10, 10, "AIR")
    target = BoxObject("Target", 4, 4, 4, 6, 6, 6, "PEC")
    settings = _scattered_settings()
    settings["boundaries"]["Zmax"] = "PML"
    settings["pml"] = {"enabled": True}

    findings = validate_simulation([air, target], settings)

    assert any(
        item.severity == "ERROR"
        and "PML" in item.message
        and item.category == "Boundaries"
        for item in findings
    )


def test_scattered_field_validator_requires_selected_closed_integration_object():
    air = BoxObject("Air", 0, 0, 0, 10, 10, 10, "AIR")
    target = BoxObject("Target", 4, 4, 4, 6, 6, 6, "PEC")
    settings = _scattered_settings()
    settings["outputs"][0]["params"].pop("integration_object")

    findings = validate_simulation([air, target], settings)

    assert any(
        item.severity == "ERROR"
        and "select an existing closed model object" in item.message
        for item in findings
    )


def test_scattered_field_allows_legacy_planewave_record_but_reports_it():
    air = BoxObject("Air", 0, 0, 0, 10, 10, 10, "AIR")
    target = BoxObject("Target", 4, 4, 4, 6, 6, 6, "PEC")
    settings = _scattered_settings()
    settings["ports"] = [{
        "name": "Old plane wave",
        "type": "PlaneWave",
        "params": {"Theta_Deg": 45.0},
    }]

    findings = validate_simulation([air, target], settings)

    assert not [item for item in findings if item.severity == "ERROR"]
    assert any(
        item.severity == "WARNING"
        and "legacy PlaneWave port is preserved" in item.message
        for item in findings
    )


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("ScatteredDefinition", "OTHER", "EA or SPH"),
        ("ScatteredPowerDensity_W_m2", -1.0, "power density"),
        ("ScatteredThetaDeg", float("inf"), "angles"),
    ],
)
def test_scattered_field_validator_rejects_invalid_excitation(key, value, message):
    air = BoxObject("Air", 0, 0, 0, 10, 10, 10, "AIR")
    target = BoxObject("Target", 4, 4, 4, 6, 6, 6, "PEC")
    settings = _scattered_settings()
    settings["simulations"][0][key] = value

    findings = validate_simulation([air, target], settings)

    assert any(
        item.severity == "ERROR" and message in item.message
        for item in findings
    )


def test_scattered_field_export_uses_run_scattered_and_skips_radiation_bc():
    settings = _scattered_settings()
    script = export_emerge_python_script(
        project_name="ScatteredFieldTest",
        settings=settings,
        step_entries=[
            {"object_name": "Air", "material": "AIR", "step_file": "Air.step"},
            {"object_name": "Target", "material": "PEC", "step_file": "Target.step"},
        ],
    )
    clean_script = build_clean_emerge_python_script(script, "scatteredfield")

    ast.parse(script)
    ast.parse(clean_script)
    for generated in (script, clean_script):
        assert "simulationObj.mw.bc.ScatteredField(" in generated
        assert "definition=" in generated and "EA" in generated
        assert "thetas=90.0" in generated
        assert "power_density=1.0" in generated
        assert "simulationObj.mw.run_scattered()" in generated
        assert "simulationObj.mw.bc.AbsorbingBoundary(_combined_open_region_faces)" not in generated
    assert "_postprocess_sparams" not in clean_script
    assert "ScatteredField completed" in script


def test_scattered_field_export_rejects_ports():
    settings = _scattered_settings()
    settings["ports"] = [{"name": "P1", "type": "LumpedPort"}]

    with pytest.raises(ValueError, match="cannot be exported with"):
        export_emerge_python_script(
            project_name="ScatteredFieldPortConflict",
            settings=settings,
            step_entries=[],
        )


def test_scattered_field_export_ignores_legacy_planewave_port_record():
    settings = _scattered_settings()
    settings["ports"] = [{
        "name": "Legacy PlaneWave",
        "type": "PlaneWave",
    }]

    script = export_emerge_python_script(
        project_name="ScatteredFieldLegacyPort",
        settings=settings,
        step_entries=[
            {"object_name": "Air", "material": "AIR", "step_file": "Air.step"},
            {"object_name": "Target", "material": "PEC", "step_file": "Target.step"},
        ],
    )

    assert "simulationObj.mw.run_scattered()" in script
    assert "legacy PlaneWave port" not in script


def test_scattered_field_export_supports_sph_and_rejects_conflicting_boundaries():
    settings = _scattered_settings()
    settings["simulations"][0].update({
        "ScatteredDefinition": "SPH",
        "ScatteredThetaDeg": 45.0,
        "ScatteredPhiDeg": 90.0,
        "ScatteredPsiDeg": 10.0,
    })
    script = export_emerge_python_script(
        project_name="ScatteredFieldSphTest",
        settings=settings,
        step_entries=[
            {"object_name": "Air", "material": "AIR", "step_file": "Air.step"},
        ],
    )

    assert "definition=\"SPH\"" in script
    assert "thetas=45.0" in script
    assert "phis=90.0" in script
    assert "polarizations=10.0" in script

    settings["boundaries"]["Xmin"] = "PEC"
    with pytest.raises(ValueError, match="global PML, PEC, or PMC"):
        export_emerge_python_script(
            project_name="ScatteredFieldBoundaryConflict",
            settings=settings,
            step_entries=[
                {"object_name": "Air", "material": "AIR", "step_file": "Air.step"},
            ],
        )


def test_farfield_integration_helper_uses_only_selected_geometry():
    class FaceSet:
        def __init__(self, names):
            self.names = tuple(names)

        def __add__(self, other):
            return FaceSet(self.names + other.names)

    class Geometry:
        def __init__(self, name):
            self.name = name

        def boundary(self):
            return FaceSet([self.name])

    class LoadedSimulation:
        def all_geos(self):
            return [Geometry("Air"), Geometry("Target")]

    selected = MainWindow._farfield_integration_faces(
        LoadedSimulation(), {"integration_object": "Target"}
    )

    assert selected.names == ("Target",)


def test_farfield_integration_helper_matches_step_import_names():
    class FaceSet:
        def __init__(self, names):
            self.names = tuple(names)

        def __add__(self, other):
            return FaceSet(self.names + other.names)

    class Geometry:
        def __init__(self, name):
            self.name = name

        def boundary(self):
            return FaceSet([self.name])

    class LoadedSimulation:
        def all_geos(self):
            return [Geometry("Sphere_1_OCC 3"), Geometry("Sphere_2_OCC 4")]

    selected = MainWindow._farfield_integration_faces(
        LoadedSimulation(), {"integration_object": "Sphere_1"}
    )

    assert selected.names == ("Sphere_1_OCC 3",)


def test_scattered_field_msgpack_save_load_restores_excitation_and_fields(
    monkeypatch, tmp_path
):
    emerge_file = pytest.importorskip("emsutil.file")
    emerge_module = pytest.importorskip("emerge")
    mw_data_module = pytest.importorskip(
        "emerge._emerge.physics.microwave.microwave_data"
    )
    background_module = pytest.importorskip(
        "emerge._emerge.physics.microwave.bcs.background_field"
    )
    save_object = emerge_file.save_object
    field = mw_data_module.MWField()
    background = background_module.BackgroundField(
        2.0, 0.0, 0.0, 0.0, (0.0, 0.0, 0.0)
    )
    solver_background = background_module.BackgroundField(
        2.0, 0.01, 0.0, 0.0, (0.0, 0.0, 0.0)
    )
    field.add_field_properties(background)
    field._fields = {background: np.asarray([1.0 + 2.0j, 3.0 + 4.0j])}
    field.set_field_vector()
    original_excitation = field.excitation

    coordinates = np.asarray([0.0, 0.25])
    mask = np.asarray([True, True])
    expected_mode = field._field.copy()
    expected_back_e = field.backE(coordinates, coordinates, coordinates, mask)
    expected_back_h = field.backH(coordinates, coordinates, coordinates, mask)
    field._fields = {
        solver_background: np.asarray([1.0 + 2.0j, 3.0 + 4.0j])
    }
    field.excitation[solver_background] = 0.0
    original_fields = field._fields
    original_excitation = field.excitation

    failing_field = mw_data_module.MWField()
    failing_field.add_field_properties(background)
    failing_field.excitation = {background: 1.0}
    with pytest.raises(TypeError, match="BackgroundField"):
        save_object(
            str(tmp_path / "raw-simdata.emerge"),
            {"field": [failing_field]},
        )

    monkeypatch.setenv("EMERGE_BASE_PATH", str(tmp_path))
    simulation_name = "ScatteredFieldPersistence"
    simulation = emerge_module.Simulation(
        simulation_name,
        save_file=True,
        store_system="msgpack",
    )
    simulation.data.mw.field._data_entries.append(field)
    state_entry = simulation.data.sim.new()
    state_entry["mesh"] = simulation.mesh
    state_entry["geos"] = []
    simulation.state.set_modified()

    script = export_emerge_python_script(
        project_name="ScatteredFieldPersistence",
        settings={"simulations": [{"name": "Scattered", "type": "Sweep"}]},
        step_entries=[],
        run_sweep=False,
    )
    tree = ast.parse(script)
    save_helper = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_save_simulation_with_serializable_background_field_maps"
    )
    namespace = {}
    exec(
        compile(        ast.Module(body=[save_helper], type_ignores=[]), "<save-helper>", "exec"),
        namespace,
    )
    simdata_path = simulation.modelpath / "simdata.emerge"
    namespace["simulationObj"] = simulation
    namespace["_save_simulation_with_serializable_background_field_maps"]()

    assert simdata_path.is_file()
    assert field.excitation is original_excitation
    assert field._fields is original_fields

    loader = SimpleNamespace(
        _resolve_emerge_simulation_ctor=lambda: (
            lambda model_name, **kwargs: emerge_module.Simulation(
                model_name, store_system="msgpack", **kwargs
            )
        ),
        _simdata_file_in_dir=lambda directory: (
            Path(directory) / "simdata.emerge"
            if (Path(directory) / "simdata.emerge").is_file()
            else None
        ),
        _simulation_bundle_dir=lambda: tmp_path,
    )
    loaded_simulation, loaded_grid, loaded_path = (
        MainWindow._load_sim_grid_from_results(
            loader, "Scattered", simulation.modelpath, allow_field_only=True
        )
    )
    loaded_field = loaded_simulation.data.mw.field._data_entries[0]

    assert loaded_grid is None
    assert loaded_path == simdata_path
    assert len(loaded_field.background_fields) == 1
    loaded_background = loaded_field.background_fields[0]
    assert loaded_field.excitation == {loaded_background: 1.0}
    assert list(loaded_field._fields) == [loaded_background]
    np.testing.assert_allclose(loaded_field._field, expected_mode)
    np.testing.assert_allclose(
        loaded_field.backE(coordinates, coordinates, coordinates, mask),
        expected_back_e,
    )
    np.testing.assert_allclose(
        loaded_field.backH(coordinates, coordinates, coordinates, mask),
        expected_back_h,
    )
