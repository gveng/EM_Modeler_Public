import ast
from types import SimpleNamespace

from em3d_modeler.emerge.python_script_exporter import export_emerge_python_script


def _export_material_script(material_record):
    return export_emerge_python_script(
        project_name="FrequencyDependentMaterialExport",
        settings={
            "simulations": [{
                "name": "Sweep",
                "type": "Sweep",
                "Fmin_GHz": 0.5,
                "Fmax_GHz": 8.0,
                "Fstep_GHz": 2.5,
            }],
        },
        step_entries=[{
            "object_name": "MaterialBlock",
            "object_type": "BoxObject",
            "step_file": "MaterialBlock.step",
            "material": "Tabulated",
        }],
        materials_catalog={"Tabulated": material_record},
        run_sweep=False,
    )


def _generated_material(script, name):
    module = ast.parse(script)
    statements = []
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_make_frequency_dependent_scalar":
            statements.append(node)
        elif isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name)
                and target.id == "materials"
                and isinstance(node.value, ast.Dict)
                for target in node.targets
            ):
                statements.append(node)
            elif any(
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "materials"
                and ast.literal_eval(target.slice) == name
                for target in node.targets
            ):
                statements.append(node)

    class FakeMaterial:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_em = SimpleNamespace(
        Material=FakeMaterial,
        FreqDependent=lambda scalar: scalar,
    )
    namespace = {"em": fake_em}
    exec(
        compile(ast.Module(body=statements, type_ignores=[]), "<generated materials>", "exec"),
        namespace,
    )
    return namespace["materials"][name]


def test_frequency_dependent_er_and_ur_interpolate_and_clamp():
    script = _export_material_script({
        "er": 2.0,
        "ur": 1.0,
        "er_frequency_data": [
            {"frequency_hz": 1e9, "real": 2.0, "imag": 0.1},
            {"frequency_hz": 3e9, "real": 4.0, "imag": 0.3},
            {"frequency_hz": 5e9, "real": 6.0, "imag": 0.5},
        ],
        "ur_frequency_data": [
            {"frequency_hz": 2e9, "real": 1.0, "imag": 0.2},
            {"frequency_hz": 4e9, "real": 3.0, "imag": 0.6},
        ],
    })

    ast.parse(script)
    assert "er=em.FreqDependent(scalar=_make_frequency_dependent_scalar(" in script
    assert "ur=em.FreqDependent(scalar=_make_frequency_dependent_scalar(" in script
    assert "FMIN_GHZ = 0.5" in script
    assert "FMAX_GHZ = 8.0" in script
    assert "NPOINTS = 4" in script

    material = _generated_material(script, "Tabulated")
    er = material.kwargs["er"]
    ur = material.kwargs["ur"]

    assert er(0.5e9) == complex(2.0, 0.1)
    assert er(1e9) == complex(2.0, 0.1)
    assert er(2e9) == complex(3.0, 0.2)
    assert er(5e9) == complex(6.0, 0.5)
    assert er(8e9) == complex(6.0, 0.5)

    assert ur(1e9) == complex(1.0, 0.2)
    assert ur(2e9) == complex(1.0, 0.2)
    assert ur(3e9) == complex(2.0, 0.4)
    assert ur(4e9) == complex(3.0, 0.6)
    assert ur(8e9) == complex(3.0, 0.6)


def test_missing_or_empty_frequency_tables_preserve_scalar_material_values():
    script = _export_material_script({
        "er": 3.25,
        "ur": 1.5,
        "er_frequency_data": [],
    })

    ast.parse(script)
    assert "_make_frequency_dependent_scalar" not in script
    assert (
        'materials["Tabulated"] = em.Material(name="Tabulated", '
        "er=3.25, ur=1.5, tand=0.0, cond=0.0)"
    ) in script
