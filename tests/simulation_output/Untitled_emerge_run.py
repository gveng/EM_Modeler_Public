# Auto-generated EMERGE Python script from EM 3D Modeler
# This script is intended to be run with: python -u <script.py>

import os
import shutil
import traceback
import emerge as em

abspath = os.path.abspath(__file__)
currDir = os.path.dirname(abspath)
os.chdir(currDir)

m = 1.0
cm = 0.01
mm = 0.001
um = 0.000001

PROJECT_NAME = "Untitled"
MODEL_UNITS = "mm"
FMIN_GHZ = 0.0
FMAX_GHZ = 10.0
FSTEP_GHZ = 0.1

npoints = int(max(2, round((FMAX_GHZ - FMIN_GHZ) / max(FSTEP_GHZ, 1e-9)) + 1))
Sim_Path = os.path.join(currDir, 'simulation_output')
if os.path.exists(Sim_Path):
    shutil.rmtree(Sim_Path)
os.mkdir(Sim_Path)

simulationObj = em.Simulation(PROJECT_NAME, save_file=True, write_log=True)
simulationObj.mw.set_frequency_range(FMIN_GHZ * 1e9, FMAX_GHZ * 1e9, npoints)

materials = {}

materials["DIEL_AD320_PIM"] = em.Material(name="DIEL_AD320_PIM", er=3.2, ur=1.0, tand=0.003, cond=0.0)
materials["DIEL_AD320_PIM"].color = "#21912b"
materials["DIEL_AD320_PIM"].opacity = 0.3

materials["MET_NICKEL"] = em.Material(name="MET_NICKEL", er=1.0, ur=1.0, tand=0.0, cond=11400000.0)
materials["MET_NICKEL"].color = "#bfbfbf"
materials["MET_NICKEL"].opacity = 0.5

materials["PEC"] = em.lib.PEC
# STEP entries grouped by source file. Each STEP solid keeps its own material assignment.

_step_map_1 = {
    "STEP_Solid_28": {'material': "DIEL_AD320_PIM", 'prio': 5001},
    "STEP_Solid_29": {'material': "DIEL_AD320_PIM", 'prio': 5002},
    "STEP_Solid_30": {'material': "PEC", 'prio': 5003},
    "STEP_Solid_31": {'material': "PEC", 'prio': 5004},
    "STEP_Solid_32": {'material': "PEC", 'prio': 5005},
    "STEP_Solid_33": {'material': "PEC", 'prio': 5006},
}
stepObjectGroup = em.geo.step.STEPItems(name="STEP_Group_1", filename="D:\\Visual_Studio_Code\\EM_3D_Modeler\\tests\\GV_For_Sym\\TK440270A_IP_ASSY_Apr10_2026_GV_For_Sym.STEP", unit=mm)
for geoObj in stepObjectGroup.objects:
    _meta = _step_map_1.get(geoObj.name)
    if _meta is None:
        continue
    geoObj.prio_set(_meta['prio'])
    _mat = materials.get(_meta['material'])
    if _mat is not None:
        geoObj.set_material(_mat)

# Non-STEP objects currently exported as notes (no direct EMERGE geometry mapping yet):
# - Fuse_STEP_Solid_1 (MeshObject without STEP source metadata)
# - Fuse_STEP_Solid_4 (MeshObject without STEP source metadata)
# - Fuse_STEP_Solid_7 (MeshObject without STEP source metadata)
# - Fuse_STEP_Solid_10 (MeshObject without STEP source metadata)
# - Fuse_STEP_Solid_13 (MeshObject without STEP source metadata)
# - Fuse_STEP_Solid_16 (MeshObject without STEP source metadata)
# - Fuse_STEP_Solid_19 (MeshObject without STEP source metadata)
# - Fuse_STEP_Solid_22 (MeshObject without STEP source metadata)
# - Fuse_STEP_Solid_25 (MeshObject without STEP source metadata)
# - PORT1 (PlateObject)
# - PORT2 (PlateObject)

# Boundary settings from project tree
BOUNDARIES = {"Xmin": "PML", "Xmax": "PML", "Ymin": "PML", "Ymax": "PML", "Zmin": "PML", "Zmax": "PML"}

# Port settings from project tree (kept for reference/log)
PORTS = [{"name": "Port_PORT1", "type": "LumpedPort", "x": 0.0, "y": 0.0, "z": 0.0, "params": {"Resistance_Ohm": 50.0, "Voltage_V": 1.0}, "object": "PORT1"}, {"name": "Port_PORT2", "type": "LumpedPort", "x": 0.0, "y": 0.0, "z": 0.0, "params": {"Resistance_Ohm": 50.0, "Voltage_V": 1.0}, "object": "PORT2"}]

simulationObj.commit_geometry()
simulationObj.generate_mesh()

try:
    simulationResult = simulationObj.mw.run_sweep()
    print('Simulation completed successfully.')
except Exception:
    print('Simulation execution failed:')
    traceback.print_exc()
    raise

simulationObj.save()
print('Project saved.')