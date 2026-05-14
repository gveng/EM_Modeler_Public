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
# One STEP file per object, exported in this bundle directory.

stepObjectGroup = em.geo.step.STEPItems(name="STEP_Solid_28", filename=os.path.join(currDir, "STEP_Solid_28.step"), unit=mm)
for geoObj in stepObjectGroup.objects:
    geoObj.prio_set(5001)
    _mat = materials.get("DIEL_AD320_PIM")
    if _mat is not None:
        geoObj.set_material(_mat)

stepObjectGroup = em.geo.step.STEPItems(name="STEP_Solid_29", filename=os.path.join(currDir, "STEP_Solid_29.step"), unit=mm)
for geoObj in stepObjectGroup.objects:
    geoObj.prio_set(5002)
    _mat = materials.get("DIEL_AD320_PIM")
    if _mat is not None:
        geoObj.set_material(_mat)

stepObjectGroup = em.geo.step.STEPItems(name="STEP_Solid_30", filename=os.path.join(currDir, "STEP_Solid_30.step"), unit=mm)
for geoObj in stepObjectGroup.objects:
    geoObj.prio_set(5003)
    _mat = materials.get("PEC")
    if _mat is not None:
        geoObj.set_material(_mat)

stepObjectGroup = em.geo.step.STEPItems(name="STEP_Solid_31", filename=os.path.join(currDir, "STEP_Solid_31.step"), unit=mm)
for geoObj in stepObjectGroup.objects:
    geoObj.prio_set(5004)
    _mat = materials.get("PEC")
    if _mat is not None:
        geoObj.set_material(_mat)

stepObjectGroup = em.geo.step.STEPItems(name="STEP_Solid_32", filename=os.path.join(currDir, "STEP_Solid_32.step"), unit=mm)
for geoObj in stepObjectGroup.objects:
    geoObj.prio_set(5005)
    _mat = materials.get("PEC")
    if _mat is not None:
        geoObj.set_material(_mat)

stepObjectGroup = em.geo.step.STEPItems(name="STEP_Solid_33", filename=os.path.join(currDir, "STEP_Solid_33.step"), unit=mm)
for geoObj in stepObjectGroup.objects:
    geoObj.prio_set(5006)
    _mat = materials.get("PEC")
    if _mat is not None:
        geoObj.set_material(_mat)

stepObjectGroup = em.geo.step.STEPItems(name="Fuse_STEP_Solid_1", filename=os.path.join(currDir, "Fuse_STEP_Solid_1.step"), unit=mm)
for geoObj in stepObjectGroup.objects:
    geoObj.prio_set(5007)
    _mat = materials.get("MET_NICKEL")
    if _mat is not None:
        geoObj.set_material(_mat)

stepObjectGroup = em.geo.step.STEPItems(name="Fuse_STEP_Solid_4", filename=os.path.join(currDir, "Fuse_STEP_Solid_4.step"), unit=mm)
for geoObj in stepObjectGroup.objects:
    geoObj.prio_set(5008)
    _mat = materials.get("MET_NICKEL")
    if _mat is not None:
        geoObj.set_material(_mat)

stepObjectGroup = em.geo.step.STEPItems(name="Fuse_STEP_Solid_7", filename=os.path.join(currDir, "Fuse_STEP_Solid_7.step"), unit=mm)
for geoObj in stepObjectGroup.objects:
    geoObj.prio_set(5009)
    _mat = materials.get("MET_NICKEL")
    if _mat is not None:
        geoObj.set_material(_mat)

stepObjectGroup = em.geo.step.STEPItems(name="Fuse_STEP_Solid_10", filename=os.path.join(currDir, "Fuse_STEP_Solid_10.step"), unit=mm)
for geoObj in stepObjectGroup.objects:
    geoObj.prio_set(5010)
    _mat = materials.get("MET_NICKEL")
    if _mat is not None:
        geoObj.set_material(_mat)

stepObjectGroup = em.geo.step.STEPItems(name="Fuse_STEP_Solid_13", filename=os.path.join(currDir, "Fuse_STEP_Solid_13.step"), unit=mm)
for geoObj in stepObjectGroup.objects:
    geoObj.prio_set(5011)
    _mat = materials.get("MET_NICKEL")
    if _mat is not None:
        geoObj.set_material(_mat)

stepObjectGroup = em.geo.step.STEPItems(name="Fuse_STEP_Solid_16", filename=os.path.join(currDir, "Fuse_STEP_Solid_16.step"), unit=mm)
for geoObj in stepObjectGroup.objects:
    geoObj.prio_set(5012)
    _mat = materials.get("MET_NICKEL")
    if _mat is not None:
        geoObj.set_material(_mat)

stepObjectGroup = em.geo.step.STEPItems(name="Fuse_STEP_Solid_19", filename=os.path.join(currDir, "Fuse_STEP_Solid_19.step"), unit=mm)
for geoObj in stepObjectGroup.objects:
    geoObj.prio_set(5013)
    _mat = materials.get("MET_NICKEL")
    if _mat is not None:
        geoObj.set_material(_mat)

stepObjectGroup = em.geo.step.STEPItems(name="Fuse_STEP_Solid_22", filename=os.path.join(currDir, "Fuse_STEP_Solid_22.step"), unit=mm)
for geoObj in stepObjectGroup.objects:
    geoObj.prio_set(5014)
    _mat = materials.get("MET_NICKEL")
    if _mat is not None:
        geoObj.set_material(_mat)

stepObjectGroup = em.geo.step.STEPItems(name="Fuse_STEP_Solid_25", filename=os.path.join(currDir, "Fuse_STEP_Solid_25.step"), unit=mm)
for geoObj in stepObjectGroup.objects:
    geoObj.prio_set(5015)
    _mat = materials.get("MET_NICKEL")
    if _mat is not None:
        geoObj.set_material(_mat)

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