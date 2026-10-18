import ast
from types import SimpleNamespace

import pytest

from em3d_modeler.emerge.python_script_exporter import export_emerge_python_script
from em3d_modeler.scene.em_objects import SweptSurfaceObject
from em3d_modeler.ui.main_window import MainWindow


def _window_for_port(params):
    sheet = SweptSurfaceObject(
        "ExtrudedProfile",
        profile_pts=[(0, 0, 0), (3, 0, 0), (3, 2, 0), (0, 2, 0)],
        extrusion_length=5,
        direction=(0, 0, -1),
        closed=True,
    )
    port = {
        "name": "SheetPort",
        "number": 1,
        "type": "LumpedPort",
        "object": sheet.name,
        "params": {
            "Resistance_Ohm": 50.0,
            "Voltage_V": 1.0,
            "Direction_X": 0.0,
            "Direction_Y": 0.0,
            "Direction_Z": 0.0,
            **params,
        },
    }
    return SimpleNamespace(
        _viewport=SimpleNamespace(scene=SimpleNamespace(objects=[sheet])),
        _project_tree=SimpleNamespace(get_settings=lambda: {"ports": [port]}),
    )


def test_generated_surface_lumped_port_uses_profile_dimensions_and_signed_direction():
    port = MainWindow._collect_plate_lumped_ports(_window_for_port({}))[0]

    assert port["width"] == pytest.approx(10)
    assert port["height"] == pytest.approx(5)
    assert port["direction"] == pytest.approx([0, 0, -1])
    assert port["is_swept_surface"] is True
    assert sum(value * value for value in port["u"]) ** 0.5 == pytest.approx(10)
    assert sum(value * value for value in port["v"]) ** 0.5 == pytest.approx(5)
    assert sum(port["u"][index] * port["v"][index] for index in range(3)) == pytest.approx(0)
    assert port["v"] == pytest.approx([0, 0, -5])


def test_generated_surface_lumped_port_honors_normalized_direction_override():
    port = MainWindow._collect_plate_lumped_ports(
        _window_for_port({
            "Direction_X": 2.0,
            "Direction_Y": 0.0,
            "Direction_Z": 2.0,
        })
    )[0]

    assert port["direction"] == pytest.approx([2 ** -0.5, 0, 2 ** -0.5])


def test_curved_sheet_is_not_collected_as_a_planar_plate():
    window = _window_for_port({})
    window._project_tree = SimpleNamespace(get_settings=lambda: {"ports": []})
    window._is_plate_role_object = lambda obj: MainWindow._is_plate_role_object(obj)

    assert MainWindow._collect_emerge_plates(window) == []


def test_exported_sheet_port_uses_curved_step_surface():
    script = export_emerge_python_script(
        "SheetPortProject",
        {
            "simulations": [{
                "name": "Sweep",
                "type": "Sweep",
                "Fmin_GHz": 1.0,
                "Fmax_GHz": 2.0,
                "NumberOfPoints": 2,
            }],
            "object_boundaries": [{
                "object": "ExtrudedProfile",
                "type": "PEC",
            }],
        },
        [{
            "object_name": "ExtrudedProfile",
            "object_type": "SweptSurfaceObject",
            "step_file": "ExtrudedProfile.step",
            "material": "PEC",
            "priority": 5001,
        }],
        lumped_ports=[{
            "index": 1,
            "name": "SheetPort",
            "type": "LumpedPort",
            "plate_name": "ExtrudedProfile",
            "is_swept_surface": True,
            "origin": [0, 5, 0],
            "u": [0, -10, 0],
            "v": [0, 0, -5],
            "width": 10.0,
            "height": 5.0,
            "direction": [0.0, 0.0, -1.0],
            "z0": 50.0,
            "power": 1.0,
        }],
        run_sweep=False,
    )

    ast.parse(script)
    assert 'filename=os.path.join(SCRIPT_DIR, "ExtrudedProfile.step")' in script
    assert "em.geo.STEPItems(" in script
    assert "em.geo.unite(" in script
    assert "as_surface()" not in script
    assert "em.geo.Plate(" not in script
    assert 'port[1][\'object\'] = port_surfaces["ExtrudedProfile"]' in script
    assert "port[1]['w'] = 0.01" in script
    assert "port[1]['h'] = 0.005" in script
    assert "width=port[1]['w']" in script
    assert "height=port[1]['h']" in script
    assert "port[1]['portDirection'] = (0.0, 0.0, -1.0)" in script
    assert "port_surfaces[\"ExtrudedProfile\"].boundary()" not in script
    assert "PEC(port_surfaces[\"ExtrudedProfile\"])" not in script
    assert "Explicit PEC omitted for port surface ExtrudedProfile" in script
