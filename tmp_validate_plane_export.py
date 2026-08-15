import sys
sys.path.insert(0, "src")

from em3d_modeler.scene.em_objects import CylinderObject

c = CylinderObject(cx=1, cy=2, cz=3, radius=4, height=10, axis='Z')
c.set_creation_plane('XZ', (0, 0, 0), (1, 0, 0))
print(c.creation_plane_reference_block())
print(c.to_emerge_script())
