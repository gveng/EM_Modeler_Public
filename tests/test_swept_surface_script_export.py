import ast
from types import SimpleNamespace

from em3d_modeler.emerge.python_script_exporter import export_emerge_python_script


def test_every_swept_surface_is_imported_curved_and_receives_direct_bc():
    script = export_emerge_python_script(
        "SheetProject",
        {
            "simulations": [{
                "name": "Sweep",
                "type": "Sweep",
                "Fmin_GHz": 1.0,
                "Fmax_GHz": 2.0,
                "NumberOfPoints": 2,
            }],
            "object_boundaries": [{
                "object": "CylinderSheet",
                "type": "PEC",
            }, {
                "object": "SecondSheet",
                "type": "PMC",
            }],
        },
        [{
            "object_name": "AirRegion",
            "object_type": "BoxObject",
            "step_file": "AirRegion.step",
            "material": "AIR",
            "priority": 4000,
            "bounds_mm": (-10, 10, -10, 10, -10, 10),
        }, {
            "object_name": "CylinderSheet",
            "object_type": "SweptSurfaceObject",
            "step_file": "CylinderSheet.step",
            "material": "PEC",
            "priority": 5001,
            "bounds_mm": (-1, 1, -1, 1, -1, 1),
        }, {
            "object_name": "SecondSheet",
            "object_type": "SweptSurfaceObject",
            "step_file": "SecondSheet.step",
            "material": "PEC",
            "priority": 5002,
            "bounds_mm": (-2, 2, -2, 2, -2, 2),
        }],
        run_sweep=False,
    )

    ast.parse(script)
    assert 'filename=os.path.join(SCRIPT_DIR, "CylinderSheet.step")' in script
    assert 'filename=os.path.join(SCRIPT_DIR, "SecondSheet.step")' in script
    assert script.count("em.geo.STEPItems(") >= 2
    assert ".surfaces" in script
    assert "em.geo.Plate(" not in script
    assert "as_surface()" not in script
    assert "gmsh.model.mesh.embed(2, _swept_surface_tags, 3, _swept_host_volume_tag)" in script
    assert "_swept_surface_host_volume_tag(('AirRegion',)" in script
    assert "simulationObj.mw.bc.PEC(" in script
    assert "simulationObj.mw.bc.PMC(" in script


def test_generated_embedding_host_check_uses_gmsh_xyz_bounds_order():
    script = export_emerge_python_script(
        "EmbeddingProject",
        {"simulations": [{
            "name": "Sweep",
            "type": "Sweep",
            "Fmin_GHz": 1.0,
            "Fmax_GHz": 2.0,
            "NumberOfPoints": 2,
        }]},
        [{
            "object_name": "AirRegion",
            "object_type": "BoxObject",
            "step_file": "AirRegion.step",
            "material": "AIR",
            "bounds_mm": (-30, 30, -16, 48, -30, 30),
        }, {
            "object_name": "SweptSurface",
            "object_type": "SweptSurfaceObject",
            "step_file": "SweptSurface.step",
            "material": "PEC",
            "bounds_mm": (-8.3, -7.7, 32.3, 32.7, -8.3, -7.7),
        }],
        run_sweep=False,
    )
    module = ast.parse(script)
    helper = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_swept_surface_host_volume_tag"
    )
    bounds = {
        (2, 11): (-8.3, 32.3, -8.3, -7.7, 32.7, -7.7),
        (3, 5): (-30.0, -16.0, -30.0, 30.0, 48.0, 30.0),
    }
    namespace = {
        "geometry_groups": {
            "AirRegion": SimpleNamespace(
                volumes=[SimpleNamespace(tags=[5])]
            ),
        },
        "gmsh": SimpleNamespace(
            model=SimpleNamespace(
                getBoundingBox=lambda dimension, tag: bounds[(dimension, tag)]
            )
        ),
    }
    exec(compile(ast.Module(body=[helper], type_ignores=[]), "<generated>", "exec"), namespace)

    host_tag = namespace["_swept_surface_host_volume_tag"](
        ("AirRegion",),
        "SweptSurface",
        [SimpleNamespace(tags=[11])],
    )

    assert host_tag == 5
