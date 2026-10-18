import vtk
import pytest

from em3d_modeler.emerge import occ_export_builder, simulation_validator
from em3d_modeler.emerge.simulation_validator import validate_simulation
from em3d_modeler.scene.em_objects import (
    BoxObject,
    MeshObject,
    PlateObject,
    SweptSurfaceObject,
)


def _settings(port_object="Port"):
    return {
        "simulations": [{
            "name": "Sweep",
            "type": "Sweep",
            "enabled": True,
            "Fmin_GHz": 1.0,
            "Fmax_GHz": 2.0,
            "NumberOfPoints": 2,
        }],
        "ports": [{
            "name": "P1",
            "type": "LumpedPort",
            "object": port_object,
            "params": {
                "Direction_X": 0.0,
                "Direction_Y": 1.0,
                "Direction_Z": 0.0,
                "Resistance_Ohm": 50.0,
            },
        }],
        "runtime": {"solver": "PARDISO"},
    }


def test_sparta_solver_is_accepted_by_preflight():
    settings = _settings()
    settings["runtime"]["solver"] = "SPARTA"

    findings = validate_simulation([], settings)

    assert any(
        item.severity == "OK"
        and item.category == "Solver"
        and item.message == "Solver: SPARTA."
        for item in findings
    )


def test_open_sheets_require_a_three_dimensional_host_for_volume_meshing():
    sheet = SweptSurfaceObject(
        "Sheet",
        profile_pts=[(0, 0, 0), (1, 0, 0)],
        displacement=(0, 0, 1),
        closed=False,
    )

    findings = validate_simulation([sheet], {"simulations": []})

    assert any(
        item.severity == "ERROR"
        and item.category == "Geometry"
        and "require at least one closed, non-degenerate 3D host volume" in item.message
        for item in findings
    )


def test_open_surface_mesh_is_not_accepted_as_a_sheet_host_volume():
    plane = vtk.vtkPlaneSource()
    plane.SetOrigin(-2, 0, -2)
    plane.SetPoint1(2, 0, -2)
    plane.SetPoint2(-2, 0, 2)
    plane.Update()
    open_surface = MeshObject("OpenSurfaceHost", plane.GetOutput(), "AIR")
    sheet = SweptSurfaceObject(
        "Sheet",
        profile_pts=[(0, 0, 0), (1, 0, 0)],
        displacement=(0, 0, 1),
        closed=False,
    )

    findings = validate_simulation([open_surface, sheet], {"simulations": []})

    assert any(
        item.severity == "ERROR"
        and item.category == "Geometry"
        and "closed, non-degenerate 3D host volume" in item.message
        for item in findings
    )


@pytest.mark.parametrize(
    ("import_status", "version", "supported"),
    [
        (None, None, False),
        ("Import failed: ModuleNotFoundError", "3.2.0", False),
        ("Imported successfully", "unknown", False),
        ("Imported successfully", "2.9.9", False),
        ("Imported successfully", "3.0.0a23", True),
        ("Imported successfully", "3.1.0", True),
    ],
)
def test_swept_surface_preflight_fails_closed_on_unsupported_emerge_api(
    import_status, version, supported
):
    host = BoxObject("Host", -1, -1, -1, 2, 2, 2)
    sheet = SweptSurfaceObject(
        "Sheet",
        profile_pts=[(0, 0, 0), (1, 0, 0)],
        displacement=(0, 0, 1),
        closed=False,
    )

    findings = validate_simulation(
        [host, sheet],
        {"simulations": []},
        emerge_import_status=import_status,
        emerge_version=version,
    )

    unsupported = [
        finding
        for finding in findings
        if finding.category == "Runtime"
        and "requires an importable EMERGE 3.0.0 or later" in finding.message
    ]
    assert bool(unsupported) is (not supported)


def test_intentional_open_sheet_does_not_report_as_broken_volume_mesh():
    host = BoxObject("Host", -1, -1, -1, 2, 2, 2)
    sheet = SweptSurfaceObject(
        "Sheet",
        profile_pts=[(0, 0, 0), (1, 0, 0)],
        displacement=(0, 0, 1),
        closed=False,
    )

    findings = validate_simulation([host, sheet], {"simulations": []})

    assert not any(
        item.severity == "WARNING"
        and item.category == "Mesh"
        and "Sheet:" in item.message
        and "open or non-manifold" in item.message
        for item in findings
    )


@pytest.mark.parametrize(
    ("boundary_type", "expected_text"),
    [
        ("PML", "object PML boundaries require volumetric PML geometry"),
        ("Periodic", "Periodic object boundary is not supported"),
        ("Unsupported", "unsupported object boundary"),
    ],
)
def test_sheet_boundary_that_would_be_omitted_fails_preflight(
    boundary_type, expected_text
):
    host = BoxObject("Host", -1, -1, -1, 2, 2, 2, "AIR")
    sheet = SweptSurfaceObject(
        "Sheet",
        profile_pts=[(0, 0, 0), (1, 0, 0)],
        displacement=(0, 0, 1),
        closed=False,
    )

    findings = validate_simulation(
        [host, sheet],
        {
            "simulations": [],
            "object_boundaries": [{"object": "Sheet", "type": boundary_type}],
        },
    )

    assert any(
        item.severity == "ERROR"
        and item.category == "Boundaries"
        and expected_text in item.message
        for item in findings
    )


def test_sheet_boundary_target_must_resolve_to_an_exported_model_object():
    sheet = SweptSurfaceObject(
        "Sheet",
        profile_pts=[(0, 0, 0), (1, 0, 0)],
        displacement=(0, 0, 1),
        closed=False,
    )

    findings = validate_simulation(
        [BoxObject("Host", -1, -1, -1, 2, 2, 2, "AIR"), sheet],
        {
            "simulations": [],
            "object_boundaries": [{"object": "MissingSheet", "type": "PEC"}],
        },
    )

    assert any(
        item.severity == "ERROR"
        and item.category == "Boundaries"
        and "does not resolve to an exported MODEL object" in item.message
        for item in findings
    )


def test_explicit_pec_on_lumped_port_sheet_is_a_supported_port_owned_boundary():
    sheet = SweptSurfaceObject(
        "Sheet",
        profile_pts=[(0, 0, 0), (1, 0, 0)],
        displacement=(0, 0, 1),
        closed=False,
    )

    findings = validate_simulation(
        [BoxObject("Host", -1, -1, -1, 2, 2, 2, "AIR"), sheet],
        {
            "simulations": [],
            "object_boundaries": [{"object": "Sheet", "type": "PEC"}],
        },
        exported_ports=[{"plate_name": "Sheet", "type": "LumpedPort"}],
    )

    assert not any(
        item.severity == "ERROR"
        and item.category == "Boundaries"
        for item in findings
    )


def test_generated_surface_lumped_port_uses_stored_dimensions_and_auto_direction():
    host = BoxObject("Host", -1, -1, -1, 6, 6, 6, "AIR")
    sheet = SweptSurfaceObject(
        "Sheet",
        profile_pts=[(0, 0, 0), (3, 0, 0), (3, 2, 0), (0, 2, 0)],
        extrusion_length=4,
        direction=(0, 0, -1),
        closed=True,
    )
    settings = _settings("Sheet")
    settings["ports"][0]["params"].update({
        "Direction_X": 0.0,
        "Direction_Y": 0.0,
        "Direction_Z": 0.0,
    })

    findings = validate_simulation([host, sheet], settings)

    assert any(
        item.severity == "OK"
        and item.category == "Ports"
        and "W=10" in item.message
        and "H=4" in item.message
        for item in findings
    )
    assert not any(
        item.severity == "ERROR"
        and item.category == "Ports"
        and "not a Plate" in item.message
        for item in findings
    )


def test_generated_surface_rejects_waveguide_port_assignment():
    host = BoxObject("Host", -1, -1, -1, 6, 6, 6, "AIR")
    sheet = SweptSurfaceObject(
        "Sheet",
        profile_pts=[(0, 0, 0), (3, 0, 0)],
        extrusion_length=4,
        direction=(0, 0, 1),
        closed=False,
    )
    settings = _settings("Sheet")
    settings["ports"][0]["type"] = "WaveguidePort"

    findings = validate_simulation([host, sheet], settings)

    assert any(
        item.severity == "ERROR"
        and item.category == "Ports"
        and "support LumpedPort only" in item.message
        for item in findings
    )


def test_open_sheet_outside_all_volume_bounds_blocks_preflight():
    host = BoxObject("Host", 0, 0, 0, 1, 1, 1)
    sheet = SweptSurfaceObject(
        "Sheet",
        profile_pts=[(2, 2, 2), (3, 2, 2)],
        displacement=(0, 0, 1),
        closed=False,
    )

    findings = validate_simulation([host, sheet], {"simulations": []})

    assert any(
        item.severity == "ERROR"
        and item.category == "Geometry"
        and "sheet bounds must be contained within a closed 3D volume" in item.message
        for item in findings
    )


def test_lumped_port_connected_to_signal_and_reference_passes():
    signal = BoxObject("Signal", 0, 0, 0, 1, 1, 1, "COPPER")
    ground = BoxObject("Ground", 0, 2, 0, 1, 3, 1, "PEC")
    port = PlateObject("Port", 0.9, 0.9, 0.4, 1.1, 2.1, 0.6, "PEC")

    findings = validate_simulation([signal, ground, port], _settings())

    assert any(item.severity == "OK" and "connected to Signal, Ground" in item.message for item in findings)
    assert any(
        item.severity == "OK"
        and "Finite BREP contact area" in item.message
        and "Signal" in item.message
        for item in findings
    )
    assert not any(item.severity == "ERROR" and item.category == "Ports" for item in findings)


def test_lumped_port_connected_to_conductive_reference_plate_passes():
    signal = BoxObject("Signal", 0, 0, 0, 1, 1, 1, "COPPER")
    ground = PlateObject("Ground", 0, 2, 0, 1, 2, 1, "COPPER")
    port = PlateObject("Port", 0.9, 0.9, 0.4, 1.1, 2.1, 0.6, "PEC")

    findings = validate_simulation([signal, ground, port], _settings())

    assert any(item.severity == "OK" and "connected to Signal, Ground" in item.message for item in findings)
    assert any(
        item.severity == "WARNING"
        and "no finite-area common geometry for Ground" in item.message
        for item in findings
    )
    assert not any(item.severity == "ERROR" and item.category == "Ports" for item in findings)


def test_lumped_port_touching_one_conductor_is_error():
    signal = BoxObject("Signal", 0, 0, 0, 1, 1, 1, "COPPER")
    port = PlateObject("Port", 0.9, 0.9, 0.4, 1.1, 2.1, 0.6, "PEC")

    findings = validate_simulation([signal, port], _settings())

    assert any(item.severity == "ERROR" and "only one conductor" in item.message for item in findings)


def test_missing_model_material_priority_adds_warning():
    signal = BoxObject("Signal", 0, 0, 0, 1, 1, 1, "Copper Alloy")

    findings = validate_simulation([signal], _settings())

    assert any(
        item.severity == "WARNING"
        and item.category == "Materials"
        and "Copper Alloy" in item.message
        for item in findings
    )


def test_explicit_zero_material_priority_counts_as_set():
    signal = BoxObject("Signal", 0, 0, 0, 1, 1, 1, "Copper Alloy")
    settings = _settings()
    settings["material_priorities"] = {"Copper Alloy": 0}

    findings = validate_simulation([signal], settings)

    assert any(
        item.severity == "OK"
        and item.category == "Materials"
        and "priorities are set" in item.message
        for item in findings
    )
    assert not any(
        item.severity == "WARNING"
        and item.category == "Materials"
        and "priority" in item.message.lower()
        for item in findings
    )


def test_group_object_priority_satisfies_material_priority_validation():
    signal = BoxObject("Signal", 0, 0, 0, 1, 1, 1, "Copper Alloy")

    findings = validate_simulation(
        [signal],
        _settings(),
        object_priorities={"Signal": 2},
    )

    assert not any(
        item.severity == "WARNING"
        and item.category == "Materials"
        and "Copper Alloy" in item.message
        for item in findings
    )


def test_port_outside_air_region_is_error():
    signal = BoxObject("Signal", 0, 0, 0, 1, 1, 1, "COPPER")
    ground = BoxObject("Ground", 0, 2, 0, 1, 3, 1, "PEC")
    port = PlateObject("Port", 0.9, 0.9, 0.4, 1.1, 2.1, 0.6, "PEC")
    air = BoxObject("Air", -1, -1, -1, 1.05, 4, 2, "AIR")
    settings = _settings()
    settings["open_region"] = {"enabled": True, "object": "Air"}
    settings["boundaries"] = {"Xmin": "Radiation"}

    findings = validate_simulation([signal, ground, port, air], settings)

    assert any(item.severity == "ERROR" and "outside the AIR" in item.message for item in findings)


def test_valid_global_pml_is_supported():
    air = BoxObject("Air", -10, -10, -10, 10, 10, 10, "AIR")
    settings = {
        "simulations": [{
            "name": "Eigenmode",
            "type": "Eigenmode",
            "enabled": True,
            "Fmin_GHz": 1.0,
            "Fmax_GHz": 2.0,
            "NumberOfPoints": 2,
        }],
        "runtime": {"solver": "PARDISO"},
        "open_region": {"enabled": True, "object": "Air"},
        "boundaries": {"Xmin": "PML", "Xmax": "PML"},
        "pml": {
            "enabled": True,
            "air_object": "Air",
            "outer_object": "PML",
            "thickness_mm": 5.0,
            "layers": 1,
            "mesh_layers": 5,
            "exponent": 1.5,
            "deltamax": 8.0,
        },
    }

    findings = validate_simulation([air], settings)

    assert any(item.severity == "OK" and item.message.startswith("PML:") for item in findings)
    assert not any(item.severity == "ERROR" and "PML" in item.message for item in findings)


@pytest.mark.parametrize("search_frequency", [0.0, -1.0, float("nan"), float("inf")])
def test_eigenmode_search_frequency_must_be_positive_and_finite(search_frequency):
    settings = _settings()
    settings["simulations"] = [{
        "name": "Eigenmode",
        "type": "Eigenmode",
        "enabled": True,
        "Fmin_GHz": 1.0,
        "Fmax_GHz": 2.0,
        "NumberOfPoints": 2,
        "EigenmodeSearchFrequency_GHz": search_frequency,
    }]

    findings = validate_simulation([], settings)

    assert any(
        item.severity == "ERROR"
        and "eigenmode search frequency" in item.message
        for item in findings
    )


def test_adaptive_mesh_refinement_frequency_is_reported_as_additional_runtime():
    settings = _settings()
    settings["simulations"][0].update({
        "adaptive_mesh_refinement_enabled": True,
        "adaptive_mesh_refinement_frequency_GHz": 1.5,
    })

    findings = validate_simulation([], settings)

    assert any(
        item.severity == "WARNING"
        and item.category == "Runtime"
        and "adaptive mesh refinement at 1.5 GHz" in item.message
        for item in findings
    )
    assert not any(
        item.severity == "ERROR" and "adaptive mesh refinement" in item.message
        for item in findings
    )


@pytest.mark.parametrize(
    ("sim_type", "frequency_ghz"),
    [
        ("Sweep", 2.5),
        ("Parametric", 1.5),
        ("Sweep", 0.0),
    ],
)
def test_adaptive_mesh_refinement_requires_supported_type_and_in_band_frequency(
    sim_type, frequency_ghz
):
    settings = _settings()
    settings["simulations"][0].update({
        "type": sim_type,
        "adaptive_mesh_refinement_enabled": True,
        "adaptive_mesh_refinement_frequency_GHz": frequency_ghz,
    })

    findings = validate_simulation([], settings)

    assert any(
        item.severity == "ERROR"
        and "adaptive mesh refinement" in item.message
        for item in findings
    )


def test_pml_faces_without_enabled_shell_are_rejected():
    air = BoxObject("Air", -10, -10, -10, 10, 10, 10, "AIR")
    settings = {
        "simulations": [{
            "name": "Eigenmode",
            "type": "Eigenmode",
            "enabled": True,
            "Fmin_GHz": 1.0,
            "Fmax_GHz": 2.0,
            "NumberOfPoints": 2,
        }],
        "runtime": {"solver": "PARDISO"},
        "boundaries": {"Xmin": "PML"},
        "pml": {"enabled": False},
    }

    findings = validate_simulation([air], settings)

    errors = [item for item in findings if item.severity == "ERROR" and "PML" in item.message]
    assert len(errors) == 1
    assert "Internal waveguides should use PEC walls" in errors[0].message


def test_legacy_periodic_object_boundary_is_rejected_before_export():
    settings = _settings()
    settings["object_boundaries"] = [{
        "name": "LegacyPeriodic",
        "object": "Part",
        "type": "Periodic",
    }]

    findings = validate_simulation([], settings)

    assert any(
        item.severity == "ERROR"
        and item.category == "Boundaries"
        and "LegacyPeriodic" in item.message
        and "will not be exported" in item.message
        for item in findings
    )


def test_high_sweep_and_parametric_workload_warns_about_runtime():
    settings = _settings()
    settings["simulations"] = [
        {
            "name": "Dense Sweep", "type": "Sweep", "enabled": True,
            "Fmin_GHz": 1.0, "Fmax_GHz": 2.0, "NumberOfPoints": 600,
            "sparam_fitting": {"enabled": True, "points": 8000},
        },
        {
            "name": "Parametric", "type": "Parametric", "enabled": True,
            "Fmin_GHz": 1.0, "Fmax_GHz": 2.0, "NumberOfPoints": 100,
            "ParamValues": ",".join(str(value) for value in range(25)),
        },
    ]

    findings = validate_simulation([], settings)
    runtime_messages = [item.message for item in findings if item.category == "Runtime"]

    assert any("600 frequency points" in message for message in runtime_messages)
    assert any("8,000 points" in message for message in runtime_messages)
    assert any("2,500 frequency solves" in message for message in runtime_messages)
    assert any("3,100 frequency solves in total" in message for message in runtime_messages)


def test_fine_mesh_and_tiny_local_refinement_warn_about_mesh_cost():
    signal = BoxObject("Signal", 0, 0, 0, 1, 1, 1, "COPPER")
    settings = _settings()
    settings["simulations"][0]["Fmax_GHz"] = 10.0
    settings["mesh"] = {
        "default_fraction": 0.05,
        "local_refinements": [{
            "object": "Signal", "enabled": True, "mode": "face", "faces": ["+z"],
            "size_mm": 0.01, "growth_rate": 3.0,
        }],
    }

    findings = validate_simulation([signal], settings)
    mesh_warnings = [item.message for item in findings if item.severity == "WARNING" and item.category == "Mesh"]

    assert any("below 0.1 λ" in message for message in mesh_warnings)
    assert any("very fine size" in message for message in mesh_warnings)


def test_open_volume_surface_warns_about_meshing_failure():
    plane = vtk.vtkPlaneSource()
    plane.SetXResolution(1)
    plane.SetYResolution(1)
    plane.Update()
    surface = MeshObject("OpenSurface", plane.GetOutput(), "PEC")

    findings = validate_simulation([surface], _settings())

    assert any(
        item.severity == "WARNING" and item.category == "Mesh" and "open or non-manifold" in item.message
        for item in findings
    )


def test_volume_outside_air_domain_is_error():
    signal = BoxObject("Signal", 0, 0, 0, 2, 1, 1, "COPPER")
    air = BoxObject("Air", -1, -1, -1, 1, 2, 2, "AIR")
    settings = _settings()
    settings["open_region"] = {"enabled": True, "object": "Air"}
    settings["boundaries"] = {"Xmin": "Radiation"}

    findings = validate_simulation([signal, air], settings)

    assert any(
        item.severity == "ERROR" and item.category == "Boundaries" and "Signal" in item.message and "outside" in item.message
        for item in findings
    )


def test_non_finite_point_and_pml_layer_values_are_reported_without_crashing():
    air = BoxObject("Air", -10, -10, -10, 10, 10, 10, "AIR")
    settings = {
        "simulations": [{
            "name": "Sweep", "type": "Sweep", "enabled": True,
            "Fmin_GHz": 1.0, "Fmax_GHz": 2.0, "NumberOfPoints": float("inf"),
        }],
        "runtime": {"solver": "PARDISO"},
        "boundaries": {"Xmin": "PML"},
        "pml": {
            "enabled": True, "thickness_mm": 5.0, "layers": float("inf"),
            "mesh_layers": 5, "exponent": 1.5, "deltamax": 8.0,
        },
    }

    findings = validate_simulation([air], settings)

    assert any(item.severity == "ERROR" and item.category == "Simulation" for item in findings)
    assert any(item.severity == "ERROR" and item.category == "Boundaries" and "PML parameters" in item.message for item in findings)


def test_invalid_emerge_scale_factor_is_reported_before_export():
    settings = _settings()
    settings["mesh"] = {"emerge_scale_factor": float("inf")}

    findings = validate_simulation([], settings)

    assert any(
        item.severity == "ERROR" and item.category == "Mesh" and "EMERGE scale factor" in item.message
        for item in findings
    )


def test_missing_lumped_port_dimensions_are_reported_before_export():
    findings = validate_simulation(
        [],
        _settings(),
        exported_ports=[{"name": "P1", "type": "LumpedPort", "width": 0.01}],
    )

    assert any(
        item.severity == "ERROR" and item.category == "Ports" and "width and height" in item.message
        for item in findings
    )


def test_closed_waveguide_uses_unique_air_fill_without_open_region_boundaries():
    air = BoxObject("AIR", 1.0, 0.0, 1.0, 23.86, 50.0, 11.16, "AIR")
    wall = BoxObject("Cut_Box_1", 0.0, 0.0, 0.0, 24.86, 50.0, 12.16, "PEC")
    port_ymax = PlateObject("Plate_1", 1.0, 50.0, 1.0, 23.86, 50.0, 11.16, "PEC")
    port_ymin = PlateObject("Plate_2", 1.0, 0.0, 1.0, 23.86, 0.0, 11.16, "PEC")
    settings = _settings()
    settings["open_region"] = {"enabled": False, "object": ""}
    settings["ports"] = [
        {"name": "Port_Plate_1", "type": "WaveguidePort", "object": "Plate_1"},
        {"name": "Port_Plate_2", "type": "WaveguidePort", "object": "Plate_2"},
    ]

    findings = validate_simulation([air, wall, port_ymax, port_ymin], settings)

    assert not any(item.severity == "ERROR" and item.category == "Ports" for item in findings)
    assert sum(item.severity == "OK" and "WaveguidePort lies on the AIR outer boundary" in item.message for item in findings) == 2
    assert not any(item.severity == "WARNING" and item.category == "Mesh" and "open or non-manifold" in item.message for item in findings)


def test_lumped_port_inside_conductor_without_surface_contact_is_not_connected():
    enclosing_conductor = BoxObject("EnclosingGround", 0, 0, 0, 1, 1, 1, "COPPER")
    plate = PlateObject("Port", 0.4, 0.4, 0.4, 0.6, 0.6, 0.6, "PEC")
    settings = _settings()

    findings = validate_simulation([enclosing_conductor, plate], settings)

    assert any(item.severity == "ERROR" and "not connected to conductive geometry" in item.message for item in findings)


def test_exact_brep_gap_overrides_tessellated_mesh_contact(monkeypatch):
    signal = BoxObject("Signal", 0, 0, 0, 1, 1, 1, "COPPER")
    port = PlateObject("Port", 0.9, 1.0, 0.4, 1.1, 1.0, 0.6, "PEC")
    monkeypatch.setattr(
        simulation_validator,
        "_exact_brep_contact_measure",
        lambda *_args: (0.00400828, None),
    )

    findings = validate_simulation([signal, port], _settings())

    assert any(
        item.severity == "ERROR"
        and "not connected to conductive geometry" in item.message
        and "0.00400828 model units" in item.message
        for item in findings
    )


def test_sub_confusion_brep_residual_is_not_reported_as_a_gap(monkeypatch):
    signal = BoxObject("Signal", 0, 0, 0, 1, 1, 1, "COPPER")
    ground = PlateObject("Ground", 0, 2, 0, 1, 2, 1, "COPPER")
    port = PlateObject("Port", 0.9, 0.9, 0.4, 1.1, 2.1, 0.6, "PEC")

    def measure(_port, conductor, _cache, _distance_tolerance):
        if conductor is signal:
            return 1.77998e-9, 0.0
        return 0.0, 0.0

    monkeypatch.setattr(simulation_validator, "_exact_brep_contact_measure", measure)

    findings = validate_simulation([signal, ground, port], _settings())

    assert simulation_validator._exact_contact_epsilon((signal, port)) > 1.77998e-9
    assert any(
        item.severity == "OK"
        and "connected to Signal, Ground" in item.message
        for item in findings
    )
    assert any(
        item.severity == "WARNING"
        and "within the 100 nm connection tolerance" in item.message
        for item in findings
    )
    assert not any(item.severity == "ERROR" and item.category == "Ports" for item in findings)


@pytest.mark.parametrize(
    ("gap", "mm_per_unit", "connected"),
    [
        (9.9e-5, 1.0, True),
        (1.0e-4, 1.0, True),
        (1.01e-4, 1.0, False),
        (0.099, 0.001, True),
        (0.101, 0.001, False),
    ],
)
def test_lumped_port_uses_100_nm_brep_connection_tolerance(
    monkeypatch,
    gap,
    mm_per_unit,
    connected,
):
    signal = BoxObject("Signal", 0, 0, 0, 1, 1, 1, "COPPER")
    ground = BoxObject("Ground", 0, 2, 0, 1, 3, 1, "PEC")
    port = PlateObject("Port", 0.9, 0.9, 0.4, 1.1, 2.1, 0.6, "PEC")

    def measure(_port, conductor, _cache, _distance_tolerance):
        return (gap, 0.0) if conductor is signal else (0.0, 0.0)

    monkeypatch.setattr(simulation_validator, "_exact_brep_contact_measure", measure)
    findings = validate_simulation(
        [signal, ground, port],
        _settings(),
        mm_per_unit=mm_per_unit,
    )

    if connected:
        assert any(
            item.severity == "OK"
            and "connected to Signal, Ground" in item.message
            for item in findings
        )
        assert not any(item.severity == "ERROR" and item.category == "Ports" for item in findings)
    else:
        assert any(
            item.severity == "ERROR"
            and "only one conductor" in item.message
            and f"{gap:.6g} model units" in item.message
            for item in findings
        )


def test_lumped_port_accepts_sub_100_nm_gap_when_common_area_is_unavailable(monkeypatch):
    signal = BoxObject("Signal", 0, 0, 0, 1, 1, 1, "COPPER")
    ground = BoxObject("Ground", 0, 2, 0, 1, 3, 1, "PEC")
    port = PlateObject("Port", 0.9, 0.9, 0.4, 1.1, 2.1, 0.6, "PEC")

    def measure(_port, conductor, _cache, _distance_tolerance):
        return (5e-5, None) if conductor is signal else (0.0, 0.0)

    monkeypatch.setattr(simulation_validator, "_exact_brep_contact_measure", measure)
    findings = validate_simulation([signal, ground, port], _settings())

    assert any(
        item.severity == "OK"
        and "connected to Signal, Ground" in item.message
        for item in findings
    )
    assert any(
        item.severity == "WARNING"
        and "common area could not be measured for Signal" in item.message
        for item in findings
    )


def test_unavailable_step_brep_does_not_accept_tessellated_contact(monkeypatch):
    source = vtk.vtkCubeSource()
    source.SetBounds(0, 1, 0, 1, 0, 1)
    source.Update()
    signal = MeshObject(
        "Signal",
        source.GetOutput(),
        "COPPER",
        step_source_path="missing.step",
    )
    port = PlateObject("Port", 0.9, 1.0, 0.4, 1.1, 1.0, 0.6, "PEC")
    monkeypatch.setattr(simulation_validator, "_exact_brep_contact_measure", lambda *_args: None)

    findings = validate_simulation([signal, port], _settings())

    assert any(
        item.severity == "ERROR"
        and "not connected to conductive geometry" in item.message
        and "Exact STEP BREP contact could not be verified for Signal" in item.message
        for item in findings
    )


def test_exact_brep_distance_uses_export_shape_builder(monkeypatch):
    signal = object()
    port = object()
    signal_shape = object()
    port_shape = object()
    distance_shapes = []

    def build_shape(obj, _cache):
        return signal_shape if obj is signal else port_shape

    def shape_distance(shape_a, shape_b):
        distance_shapes.extend((shape_a, shape_b))
        return 0.00400828

    monkeypatch.setattr(occ_export_builder, "build_occ_shape_for_export", build_shape)
    monkeypatch.setattr(simulation_validator, "_occ_shape_distance", shape_distance)

    assert simulation_validator._exact_brep_contact_measure(signal, port, {}) == (
        pytest.approx(0.00400828),
        None,
    )
    assert distance_shapes == [signal_shape, port_shape]
