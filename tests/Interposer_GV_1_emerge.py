## EMerge simulation
#
#
# To be run with python.
# FreeCAD to OpenEMS plugin but this time it generates EMerge by Lubomir Jagos, 
# see https://github.com/LubomirJagos42/FreeCAD-OpenEMS-Export
#
# This file has been automatically generated. Manual changes may be overwritten.
#

### Import Libraries
import math
import numpy as np
import emerge as em
import os, tempfile, shutil

# Change current path to script file folder
#
abspath = os.path.abspath(__file__)
dname = os.path.dirname(abspath)
os.chdir(dname)
## constants
unit    = 0.001 # Model coordinates and lengths will be specified in mm.
fc_unit = 0.001 # STL files are exported in FreeCAD standard units (mm).


currDir = os.getcwd()
print(currDir)

## prepare simulation folder, if dir exits remove and create new one to be empty
Sim_Path = os.path.join(currDir, 'simulation_output')
if os.path.exists(Sim_Path):
	shutil.rmtree(Sim_Path)   # clear previous directory
	os.mkdir(Sim_Path)    # create empty simulation folder

# --- Unit definitions -----------------------------------------------------
m = 1.0
cm = 0.01
mm = 0.001  # meters per millimeter
um = 0.000001
nm = 0.000000001

pF = 1e-12  # picofarad in farads
fF = 1e-15  # femtofarad in farads
pH = 1e-12  # picohenry in henrys
nH = 1e-9  # nanohenry in henrys

simulationObj = em.Simulation("Interposer_GV_1", save_file=True, write_log=True)

#######################################################################################################################################
# EXCITATION 10MHz-20GHz-21pts
#######################################################################################################################################
fmin = 0.01*1000000000.0
fmax = 20.0*1000000000.0
resolution = 0.33
npoints = 21
simulationObj.mw.set_frequency_range(fmin, fmax, npoints)
simulationObj.mw.set_resolution(resolution)

#######################################################################################################################################
# MATERIALS AND GEOMETRY
#######################################################################################################################################
materialList = {}

## MATERIAL - Copper
materialList['Copper'] = em.Material(name='Copper', er=1.0, ur=1.0, tand=0.0, cond=58000000.0)
materialList['Copper'].color = '#727980'
materialList['Copper'].opacity = 1.0

stepObjectGroup = em.geo.step.STEPItems(name='PAD_Top', filename=os.path.join(currDir,'PAD_Top_gen_model.step'), unit=mm)
for geoObj in stepObjectGroup.objects:
	geoObj.prio_set(9400)
	geoObj.set_material(materialList['Copper'])
materialList['Copper'].color = '#727980'
materialList['Copper'].opacity = 1.0

stepObjectGroup = em.geo.step.STEPItems(name='Pad_Bottom', filename=os.path.join(currDir,'Pad_Bottom_gen_model.step'), unit=mm)
for geoObj in stepObjectGroup.objects:
	geoObj.prio_set(9500)
	geoObj.set_material(materialList['Copper'])
materialList['Copper'].color = '#727980'
materialList['Copper'].opacity = 1.0

stepObjectGroup = em.geo.step.STEPItems(name='GND1', filename=os.path.join(currDir,'GND1_gen_model.step'), unit=mm)
for geoObj in stepObjectGroup.objects:
	geoObj.prio_set(9600)
	geoObj.set_material(materialList['Copper'])
materialList['Copper'].color = '#727980'
materialList['Copper'].opacity = 1.0

stepObjectGroup = em.geo.step.STEPItems(name='GND2', filename=os.path.join(currDir,'GND2_gen_model.step'), unit=mm)
for geoObj in stepObjectGroup.objects:
	geoObj.prio_set(9700)
	geoObj.set_material(materialList['Copper'])

## MATERIAL - Nickel
materialList['Nickel'] = em.Material(name='Nickel', er=1, ur=1, cond=14300000)

## MATERIAL - PB
materialList['PB'] = em.Material(name='PB', er=1, ur=1, cond=1.3e+07)
materialList['PB'].color = '#727980'
materialList['PB'].opacity = 1.0

stepObjectGroup = em.geo.step.STEPItems(name='Chamfer011', filename=os.path.join(currDir,'Chamfer011_gen_model.step'), unit=mm)
for geoObj in stepObjectGroup.objects:
	geoObj.prio_set(8500)
	geoObj.set_material(materialList['PB'])
materialList['PB'].color = '#727980'
materialList['PB'].opacity = 1.0

stepObjectGroup = em.geo.step.STEPItems(name='Chamfer012', filename=os.path.join(currDir,'Chamfer012_gen_model.step'), unit=mm)
for geoObj in stepObjectGroup.objects:
	geoObj.prio_set(8600)
	geoObj.set_material(materialList['PB'])
materialList['PB'].color = '#727980'
materialList['PB'].opacity = 1.0

stepObjectGroup = em.geo.step.STEPItems(name='Chamfer013', filename=os.path.join(currDir,'Chamfer013_gen_model.step'), unit=mm)
for geoObj in stepObjectGroup.objects:
	geoObj.prio_set(8700)
	geoObj.set_material(materialList['PB'])
materialList['PB'].color = '#727980'
materialList['PB'].opacity = 1.0

stepObjectGroup = em.geo.step.STEPItems(name='Chamfer014', filename=os.path.join(currDir,'Chamfer014_gen_model.step'), unit=mm)
for geoObj in stepObjectGroup.objects:
	geoObj.prio_set(8800)
	geoObj.set_material(materialList['PB'])
materialList['PB'].color = '#727980'
materialList['PB'].opacity = 1.0

stepObjectGroup = em.geo.step.STEPItems(name='Chamfer015', filename=os.path.join(currDir,'Chamfer015_gen_model.step'), unit=mm)
for geoObj in stepObjectGroup.objects:
	geoObj.prio_set(8900)
	geoObj.set_material(materialList['PB'])
materialList['PB'].color = '#727980'
materialList['PB'].opacity = 1.0

stepObjectGroup = em.geo.step.STEPItems(name='Chamfer016', filename=os.path.join(currDir,'Chamfer016_gen_model.step'), unit=mm)
for geoObj in stepObjectGroup.objects:
	geoObj.prio_set(9000)
	geoObj.set_material(materialList['PB'])
materialList['PB'].color = '#727980'
materialList['PB'].opacity = 1.0

stepObjectGroup = em.geo.step.STEPItems(name='Chamfer017', filename=os.path.join(currDir,'Chamfer017_gen_model.step'), unit=mm)
for geoObj in stepObjectGroup.objects:
	geoObj.prio_set(9100)
	geoObj.set_material(materialList['PB'])
materialList['PB'].color = '#727980'
materialList['PB'].opacity = 1.0

stepObjectGroup = em.geo.step.STEPItems(name='Chamfer009', filename=os.path.join(currDir,'Chamfer009_gen_model.step'), unit=mm)
for geoObj in stepObjectGroup.objects:
	geoObj.prio_set(8300)
	geoObj.set_material(materialList['PB'])
materialList['PB'].color = '#727980'
materialList['PB'].opacity = 1.0

stepObjectGroup = em.geo.step.STEPItems(name='Chamfer010', filename=os.path.join(currDir,'Chamfer010_gen_model.step'), unit=mm)
for geoObj in stepObjectGroup.objects:
	geoObj.prio_set(8400)
	geoObj.set_material(materialList['PB'])

## MATERIAL - PEC
materialList['PEC'] = em.lib.PEC

## MATERIAL - Resin
materialList['Resin'] = em.Material(name='Resin', er=3.06, ur=1, cond=1)
materialList['Resin'].color = '#fcbc84'
materialList['Resin'].opacity = 1.0

stepObjectGroup = em.geo.step.STEPItems(name='270A_Housing(PEI).STEP002', filename=os.path.join(currDir,'270A_Housing(PEI).STEP002_gen_model.step'), unit=mm)
for geoObj in stepObjectGroup.objects:
	geoObj.prio_set(9200)
	geoObj.set_material(materialList['Resin'])
materialList['Resin'].color = '#fcbc84'
materialList['Resin'].opacity = 1.0

stepObjectGroup = em.geo.step.STEPItems(name='270A_Housing(PEI).STEP003', filename=os.path.join(currDir,'270A_Housing(PEI).STEP003_gen_model.step'), unit=mm)
for geoObj in stepObjectGroup.objects:
	geoObj.prio_set(9300)
	geoObj.set_material(materialList['Resin'])


# Imported objects used as boundary conditions
#

stepObjectGroup = em.geo.step.STEPItems(name='AirBox', filename=os.path.join(currDir,'AirBox_gen_model.step'), unit=mm)
for geoObj in stepObjectGroup.objects:
	geoObj.prio_set(9800)



#######################################################################################################################################
# PORTS
#######################################################################################################################################
port = {}
portNamesAndNumbersList = {}


## PORT - 1 - port1
portStart = [ -0.35, -0.038796, -0.1 ]
portStop  = [ -0.25, -0.038796, 0.1 ]
portStart = [k*0.001 for k in portStart]
portStop = [k*0.001 for k in portStop]


port[1] = {}
port[1]['portStart'] = portStart
port[1]['portStop'] = portStop
w = abs(portStart[0] - portStop[0])
h = abs(portStart[1] - portStop[1])
th = abs(portStart[2] - portStop[2])
port[1]['w'] = w
port[1]['h'] = h
port[1]['th'] = th
port[1]['portR'] = 50*1
port[1]['portDirection'] = em.ZAX
port[1]['portExcitationAmplitude'] = 1.0
port[1]['object'] = em.geo.Plate(name='port1', origin=portStart, u=[w,0,0], v=[0,0,th])
portNamesAndNumbersList["port1"] = 1

## PORT - 2 - port2
portStart = [ -0.35, 3.2368, -0.1 ]
portStop  = [ -0.25, 3.2368, 0.1 ]
portStart = [k*0.001 for k in portStart]
portStop = [k*0.001 for k in portStop]


port[2] = {}
port[2]['portStart'] = portStart
port[2]['portStop'] = portStop
w = abs(portStart[0] - portStop[0])
h = abs(portStart[1] - portStop[1])
th = abs(portStart[2] - portStop[2])
port[2]['w'] = w
port[2]['h'] = h
port[2]['th'] = th
port[2]['portR'] = 50*1
port[2]['portDirection'] = em.ZAX
port[2]['portExcitationAmplitude'] = 1.0
port[2]['object'] = em.geo.Plate(name='port2', origin=portStart, u=[w,0,0], v=[0,0,th])
portNamesAndNumbersList["port2"] = 2

#######################################################################################################################################
# COMPLETE GEOMETRY
#######################################################################################################################################

simulationObj.commit_geometry()

#######################################################################################################################################
# GRID LINES
#######################################################################################################################################

#	max element size for 'Pad_Bottom'
#
for geometryObj in simulationObj.state.manager.geometry_list[simulationObj.modelname].values():
		if geometryObj.name == 'Pad_Bottom' or geometryObj.name.startswith('Pad_Bottom_'):
			simulationObj.mesher.set_size(geometryObj, 0.1 * mm)



#
# First mesh must be created on existing geometry
#
simulationObj.generate_mesh()


#
# Now follows boundary condition definition
#
simulationObj.mw.bc.LumpedPort(port[1]['object'], 1, width=port[1]['w'], height=port[1]['th'], direction=port[1]['portDirection'], Z0=port[1]['portR'], power=port[1]['portExcitationAmplitude'])
simulationObj.mw.bc.LumpedPort(port[2]['object'], 2, width=port[2]['w'], height=port[2]['th'], direction=port[2]['portDirection'], Z0=port[2]['portR'], power=port[2]['portExcitationAmplitude'])

#######################################################################################################################################
# BOUNDARY CONDITIONS PART
#######################################################################################################################################

# BOUNDARY CONDITION NAME: Absorbing
# TYPE: Absorbing
boundary_selection = None
for geometryObj in simulationObj.state.manager.geometry_list[simulationObj.modelname].values():
	if geometryObj.name == 'AirBox' or geometryObj.name.startswith('AirBox'):
		boundary_selection = geometryObj.boundary()
simulationObj.mw.bc.AbsorbingBoundary(boundary_selection)


# BOUNDARY CONDITION NAME: PEC
# TYPE: PEC

# BOUNDARY CONDITION NAME: PEC1
# TYPE: PEC

#######################################################################################################################################
# EXPERIMENT EXPORT MESH WITH NAMED GROUP OF MESH
#######################################################################################################################################
import gmsh

def createGmshNamedGroup(geometryObjName: str, groupName: str, groupTag: int = -1, useBoundary: bool = False, useSuffixToRecognizeGeometryName: bool = True):
	objectTag1DList = []
	objectTag2DList = []
	objectTag3DList = []

	for geometryObj in simulationObj.state.manager.geometry_list[simulationObj.modelname].values():
		if geometryObj.name == geometryObjName or geometryObj.name.startswith(geometryObjName + ('_' if useSuffixToRecognizeGeometryName else '')):
			for tagTuple in (geometryObj.boundary().dimtags if useBoundary else geometryObj.dimtags):
				if tagTuple[0] == 1:
					objectTag1DList.append(tagTuple[1])
				if tagTuple[0] == 2:
					objectTag2DList.append(tagTuple[1])
				if tagTuple[0] == 3:
					objectTag3DList.append(tagTuple[1])

	if groupTag > -1:
		gmsh.model.addPhysicalGroup(1, objectTag1DList, name=groupName, tag=groupTag)
		gmsh.model.addPhysicalGroup(2, objectTag2DList, name=groupName, tag=groupTag + 1)
		gmsh.model.addPhysicalGroup(3, objectTag3DList, name=groupName, tag=groupTag + 2)
	else:
		gmsh.model.addPhysicalGroup(1, objectTag1DList, name=groupName)
		gmsh.model.addPhysicalGroup(2, objectTag2DList, name=groupName)
		gmsh.model.addPhysicalGroup(3, objectTag3DList, name=groupName)

createGmshNamedGroup('Pad_Bottom', 'Pad_Bottom')
createGmshNamedGroup('GND2', 'GND2')
createGmshNamedGroup('Chamfer013', 'Chamfer013')
createGmshNamedGroup('Chamfer014', 'Chamfer014')
createGmshNamedGroup('270A_Housing(PEI).STEP003', '270A_Housing(PEI).STEP003')
createGmshNamedGroup('270A_Housing(PEI).STEP002', '270A_Housing(PEI).STEP002')
createGmshNamedGroup('Chamfer017', 'Chamfer017')
createGmshNamedGroup('port2', 'port2')
createGmshNamedGroup('Chamfer016', 'Chamfer016')
createGmshNamedGroup('PAD_Top', 'PAD_Top')
createGmshNamedGroup('GND1', 'GND1')
createGmshNamedGroup('Chamfer009', 'Chamfer009')
createGmshNamedGroup('Chamfer015', 'Chamfer015')
createGmshNamedGroup('port1', 'port1')
createGmshNamedGroup('Chamfer012', 'Chamfer012')
createGmshNamedGroup('Chamfer010', 'Chamfer010')
createGmshNamedGroup('Chamfer011', 'Chamfer011')
createGmshNamedGroup('AirBox', 'AirBoxBoundary', useBoundary=True)

simulationObj.export('Interposer_GV_1.msh')

#######################################################################################################################################
# DISPLAY MODEL
#######################################################################################################################################
simulationObj.view()
simulationObj.view(plot_mesh=True, volume_mesh=False)

#######################################################################################################################################
# RUN and save results
#######################################################################################################################################
simulationResult = simulationObj.mw.run_sweep()
simulationObj.save()

