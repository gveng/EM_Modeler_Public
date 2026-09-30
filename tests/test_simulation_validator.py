import vtk

from em3d_modeler.emerge.simulation_validator import validate_simulation
from em3d_modeler.scene.em_objects import BoxObject, MeshObject, PlateObject


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


def test_lumped_port_connected_to_signal_and_reference_passes():
    signal = BoxObject("Signal", 0, 0, 0, 1, 1, 1, "COPPER")
    ground = BoxObject("Ground", 0, 2, 0, 1, 3, 1, "PEC")
    port = PlateObject("Port", 0.9, 0.9, 0.4, 1.1, 2.1, 0.6, "PEC")

    findings = validate_simulation([signal, ground, port], _settings())

    assert any(item.severity == "OK" and "connected to Signal, Ground" in item.message for item in findings)
    assert not any(item.severity == "ERROR" and item.category == "Ports" for item in findings)


def test_lumped_port_connected_to_conductive_reference_plate_passes():
    signal = BoxObject("Signal", 0, 0, 0, 1, 1, 1, "COPPER")
    ground = PlateObject("Ground", 0, 2, 0, 1, 2, 1, "COPPER")
    port = PlateObject("Port", 0.9, 0.9, 0.4, 1.1, 2.1, 0.6, "PEC")

    findings = validate_simulation([signal, ground, port], _settings())

    assert any(item.severity == "OK" and "connected to Signal, Ground" in item.message for item in findings)
    assert not any(item.severity == "ERROR" and item.category == "Ports" for item in findings)


def test_lumped_port_touching_one_conductor_is_error():
    signal = BoxObject("Signal", 0, 0, 0, 1, 1, 1, "COPPER")
    port = PlateObject("Port", 0.9, 0.9, 0.4, 1.1, 2.1, 0.6, "PEC")

    findings = validate_simulation([signal, port], _settings())

    assert any(item.severity == "ERROR" and "only one conductor" in item.message for item in findings)


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
