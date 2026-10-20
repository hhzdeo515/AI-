"""Create the assistant's original eyewear asset with Blender 4.5 LTS.

blender --background --python assets/glasses/build_glasses.py
All dimensions below are millimetres; the saved model and GLB use metres.
The GLB contains only the product. The .blend also includes a photo studio.
"""
from pathlib import Path
import math
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "langgraph-app/lg_assistant/web/static/models"
SOURCE = ROOT / "assets/glasses"
ASSETS.mkdir(parents=True, exist_ok=True)
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.unit_settings.system = "METRIC"
scene.unit_settings.length_unit = "MILLIMETERS"
product = bpy.data.collections.new("LENS AI / Product")
scene.collection.children.link(product)


def mat(name, color, metallic=0, rough=.3, transmission=0, coat=0):
    m = bpy.data.materials.new(name)
    m.diffuse_color = (*color, 1)
    m.use_nodes = True
    bs = m.node_tree.nodes.get("Principled BSDF")
    bs.inputs["Base Color"].default_value = (*color, 1)
    bs.inputs["Metallic"].default_value = metallic
    bs.inputs["Roughness"].default_value = rough
    bs.inputs["Transmission Weight"].default_value = transmission
    bs.inputs["IOR"].default_value = 1.46
    bs.inputs["Coat Weight"].default_value = coat
    bs.inputs["Coat Roughness"].default_value = .16
    return m


acetate = mat("Graphite / polished acetate", (.006, .009, .012), .08, .3, coat=.28)
rubber = mat("Soft-touch ear tips", (.012, .016, .018), .02, .53)
titanium = mat("Brushed titanium", (.27, .31, .33), .88, .27)
dark_metal = mat("Camera anodised aluminium", (.035, .045, .052), .8, .24)
glass = mat("Optical glass / pale blue AR coating", (.81, .94, .98), 0, .08, .96, .35)
camera_glass = mat("Camera sapphire", (.018, .07, .10), .4, .1, .35, .7)
silicone = mat("Translucent silicone nose pads", (.65, .70, .70), 0, .35, .65)
ink = mat("Laser engraved silver", (.47, .53, .55), .55, .4)
black = mat("Microphone and speaker apertures", (.002, .003, .004), .05, .65)
led = mat("Status LED", (.16, .65, .42), .1, .25)
bs = led.node_tree.nodes.get("Principled BSDF")
bs.inputs["Emission Color"].default_value = (.13, .6, .3, 1)
bs.inputs["Emission Strength"].default_value = .5


def finish(obj, name, material, bevel=0):
    obj.name = name
    for collection in list(obj.users_collection):
        collection.objects.unlink(obj)
    product.objects.link(obj)
    obj.data.materials.append(material)
    if bevel:
        mod = obj.modifiers.new("Machined edge radius", "BEVEL")
        mod.width = bevel / 1000
        mod.segments = 3
    if obj.type == "MESH":
        for p in obj.data.polygons:
            p.use_smooth = True
        mod = obj.modifiers.new("Weighted surface normals", "WEIGHTED_NORMAL")
        mod.keep_sharp = True
        mod.weight = 40
    return obj


def mesh(name, vertices, faces, material, bevel=0):
    data = bpy.data.meshes.new(name)
    data.from_pydata([tuple(v / 1000 for v in p) for p in vertices], [], faces)
    data.update()
    obj = bpy.data.objects.new(name, data)
    product.objects.link(obj)
    return finish(obj, name, material, bevel)


def cube(name, pos, size, material, bevel=.5):
    bpy.ops.mesh.primitive_cube_add(size=1, location=Vector(pos)/1000)
    ob = bpy.context.object
    ob.dimensions = Vector(size)/1000
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return finish(ob, name, material, bevel)


def sphere(name, pos, size, material):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, location=Vector(pos)/1000)
    ob = bpy.context.object
    ob.scale = Vector(size)/1000
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return finish(ob, name, material)


def tube(name, points, radius, material):
    data = bpy.data.curves.new(name, "CURVE")
    data.dimensions = "3D"
    data.resolution_u = 16
    data.bevel_depth = radius/1000
    data.bevel_resolution = 4
    spline = data.splines.new("BEZIER")
    spline.bezier_points.add(len(points)-1)
    for b, p in zip(spline.bezier_points, points):
        b.co = Vector(p)/1000
        b.handle_left_type = "AUTO"
        b.handle_right_type = "AUTO"
    obj = bpy.data.objects.new(name, data)
    product.objects.link(obj)
    return finish(obj, name, material)


def outline(t, w, h):
    # A wearable soft rectangular silhouette, slightly narrower at the bottom.
    c, s = math.cos(t), math.sin(t)
    x = math.copysign(abs(c)**(2/3.4), c)*w/2
    z = math.copysign(abs(s)**(2/3.4), s)*h/2
    x *= .97 + .03*(z/(h/2))
    return x, z


def bow(x, z):
    return 1.5*(x/72)**2 + .45*(z/22)**2


def rim(side):
    n = 128
    cx = side*34
    vertices = []
    for w, h, depth in [(58,42,0), (52,36,0), (58,42,4.4), (52,36,4.4)]:
        for i in range(n):
            x, z = outline(i*2*math.pi/n, w, h)
            x += cx
            vertices.append((x,bow(x,z)+depth,z))
    faces = []
    for i in range(n):
        j=(i+1)%n
        faces += [(i,j,n+j,n+i), (2*n+i,3*n+i,3*n+j,2*n+j),
                  (i,2*n+i,2*n+j,j), (n+i,n+j,3*n+j,3*n+i)]
    mesh(f"Frame {'L' if side<0 else 'R'}", vertices, faces, acetate, .65)
    # A restrained bright bevel catches the studio lighting.
    pts=[]
    for i in range(65):
        x,z=outline(i*2*math.pi/64,57,41)
        x+=cx
        pts.append((x,bow(x,z)-.12,z))
    tube(f"Titanium rim inlay {side}",pts,.18,titanium)


def lens(side):
    n, rings = 128, 12
    cx=side*34
    vertices=[]
    for back in [False,True]:
        vertices.append((cx,1.6+(1.05 if back else 0),0))
        for k in range(1,rings+1):
            r=k/rings
            for i in range(n):
                x,z=outline(i*2*math.pi/n,52.2*r,36.2*r)
                x+=cx
                vertices.append((x,1.6+bow(x,z)+1.1*r*r+(1.05 if back else 0),z))
    faces=[]
    stride=1+n*rings
    for b in range(2):
        base=b*stride
        for i in range(n):
            face=(base,base+1+i,base+1+(i+1)%n)
            faces.append(face if not b else face[::-1])
        for k in range(rings-1):
            for i in range(n):
                a=base+1+k*n+i
                c=base+1+k*n+(i+1)%n
                face=(a,a+n,c+n,c)
                faces.append(face if not b else face[::-1])
    start=1+(rings-1)*n
    for i in range(n):
        a=start+i;c=start+(i+1)%n
        faces.append((a,c,c+stride,a+stride))
    mesh(f"Optical lens {side}",vertices,faces,glass)


def cylinder(name, pos, radius, depth, material, front=False):
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=radius/1000, depth=depth/1000, location=Vector(pos)/1000)
    ob=bpy.context.object
    if front:
        ob.rotation_euler[0]=math.pi/2
    return finish(ob,name,material,.12)


for side in [-1,1]:
    rim(side)
    lens(side)
    cube(f"End piece {side}",(side*65.8,3.8,13.1),(13,7.4,9),acetate,2.1)
    cube(f"Hinge block {side}",(side*69,9.7,13),(4.8,7,7),titanium,.7)
    cylinder(f"Hinge screw {side}",(side*69,9.7,16.7),1.15,.45,titanium)
    cube(f"Screw slot {side}",(side*69,9.7,16.96),(1.4,.22,.06),black,.03)
    # Shaped electronics housing, with a real rounded rectangular cross section.
    pts=[(side*69,11,13),(side*70.1,25,12.6),(side*69.6,55,11.8),
         (side*67.7,83,10.5),(side*64.6,105,8.2)]
    verts=[]
    cross=24
    for k,(x,y,z) in enumerate(pts):
        width=[5.4,5.9,5.3,4.4,3.6][k]
        height=[8,8.1,7,5.6,4.6][k]
        for i in range(cross):
            t=i*2*math.pi/cross
            dx=math.copysign(abs(math.cos(t))**.45,math.cos(t))*width/2
            dz=math.copysign(abs(math.sin(t))**.45,math.sin(t))*height/2
            verts.append((x+dx,y,z+dz))
    faces=[tuple(range(cross-1,-1,-1)),tuple((len(pts)-1)*cross+i for i in range(cross))]
    for k in range(len(pts)-1):
        for i in range(cross):
            j=(i+1)%cross;a=k*cross
            faces.append((a+i,a+j,a+cross+j,a+cross+i))
    mesh(f"Smart temple {side}",verts,faces,acetate,.6)
    tube(f"Comfort ear hook {side}",[(side*64.8,102,8.3),(side*63.3,113,6.2),
          (side*61.5,124,-1),(side*60.5,132,-10)],2.35,rubber)
    # Genuine separate hinge, speaker, touch surface and charging contacts.
    cube(f"Touch inset {side}",(side*72.94,38,12.5),(.3,22,4.1),dark_metal,.9)
    for j in range(5):
        cube(f"Speaker slot {side}/{j}",(side*67.25,59+j*1.65,11.6),(.22,.65,2.1),black,.22)
    tube(f"Nose support {side}",[(side*8.5,3,1),(side*9.8,7,-2),(side*10.5,10,-4)],.6,titanium)
    ob=sphere(f"Silicone nose pad {side}",(side*10.5,10,-4.5),(2,2.4,5.5),silicone)
    ob.rotation_euler[1]=side*.24
    for j in range(2):
        sphere(f"Charging contact {side}/{j}",(side*66,85+j*3.2,8),(.6,.8,.5),titanium)
    cylinder(f"Front rivet {side}",(side*62.8,-.2,13.2),.8,.5,titanium,True)

tube("Sculpted nose bridge",[(-7,1.5,10.2),(-3,-.6,12.4),(3,-.6,12.4),(7,1.5,10.2)],2.2,acetate)
tube("Bridge highlight",[(-7,-.2,11.4),(0,-2.5,13.4),(7,-.2,11.4)],.25,titanium)
cylinder("Camera bezel",(-68.2,-.45,13.2),3.05,1.55,dark_metal,True)
cylinder("Camera metal ring",(-68.2,-1.28,13.2),2.47,.35,titanium,True)
cylinder("Camera black aperture",(-68.2,-1.5,13.2),2.06,.25,black,True)
cylinder("Camera optical element",(-68.2,-1.67,13.2),1.6,.16,camera_glass,True)
sphere("Camera catchlight",(-68.7,-1.80,13.8),(.31,.06,.25),glass)
cylinder("Recording indicator",(68,-.6,13.2),1,.5,led,True)
cylinder("Front microphone",(65.5,-.6,9.8),.45,.4,black,True)
cube("Waveguide projector",(-57,5,14),(5,6,6),dark_metal,.75)

# A subtle laser marking on the outer temple.
font=bpy.data.curves.new("Temple engraving","FONT")
font.body="L E N S  /  A I"
font.size=.0024
font.extrude=.00002
ob=bpy.data.objects.new("Temple engraving",font)
product.objects.link(ob)
ob.location=(.0731,.025,.0115)
ob.rotation_euler=(math.pi/2,0,math.pi/2)
ob.data.materials.append(ink)

# Convert curves, apply modifiers, recalculate closed-mesh normals for portable GLB.
bpy.ops.object.select_all(action="DESELECT")
for ob in list(product.objects):
    ob.select_set(True)
bpy.context.view_layer.objects.active=list(product.objects)[0]
bpy.ops.object.convert(target="MESH")
for ob in list(product.objects):
    bpy.context.view_layer.objects.active=ob
    for mod in list(ob.modifiers):
        bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")
for ob in product.objects:
    ob.select_set(True)
bpy.ops.export_scene.gltf(filepath=str(ASSETS/"smart-glasses.glb"),export_format="GLB",
                          use_selection=True,export_apply=True,export_yup=True)

# Product studio; it is deliberately excluded from the GLB above.
studio=bpy.data.collections.new("Studio / not exported")
scene.collection.children.link(studio)


def studio_object(ob):
    for c in list(ob.users_collection):
        c.objects.unlink(ob)
    studio.objects.link(ob)


def aim(ob, point):
    ob.rotation_euler=(Vector(point)-ob.location).to_track_quat("-Z","Y").to_euler()


def area(name,loc,energy,size,color,target=(0,.04,0)):
    data=bpy.data.lights.new(name,"AREA")
    data.energy=energy
    data.shape="DISK"
    data.size=size
    data.color=color
    ob=bpy.data.objects.new(name,data)
    studio.objects.link(ob)
    ob.location=loc
    aim(ob,target)


floor=mat("Studio warm grey",(.22,.24,.24),.05,.52)
bpy.ops.mesh.primitive_plane_add(size=200,location=(0,0,-.023))
ob=bpy.context.object
ob.name="Studio ground"
ob.data.materials.append(floor)
studio_object(ob)
area("Large softbox",(-.10,-.12,.22),4,.18,(.86,.93,1))
area("Long rim softbox",(.15,.10,.16),6,.14,(1,.91,.79))
area("Front fill",(.08,-.22,.06),1.5,.10,(.80,.92,1))
scene.world.use_nodes=True
scene.world.node_tree.nodes["Background"].inputs[0].default_value=(.28,.31,.33,1)
scene.world.node_tree.nodes["Background"].inputs[1].default_value=.3
bpy.ops.object.camera_add(location=(.19,-.27,.17))
camera=bpy.context.object
camera.name="Product camera"
studio_object(camera)
aim(camera,(0,.044,0))
camera.data.type="ORTHO"
camera.data.ortho_scale=.25
camera.data.lens=55
camera.data.clip_start=.001
scene.camera=camera
scene.render.engine="CYCLES"
scene.cycles.samples=48
scene.cycles.use_denoising=True
scene.render.resolution_x=1600
scene.render.resolution_y=1100
scene.render.resolution_percentage=100
scene.render.image_settings.file_format="PNG"
scene.view_settings.view_transform="AgX"
scene.render.filepath=str(SOURCE/"smart-glasses-preview.png")
# Open the .blend on the product, with a useful studio camera.
for screen in bpy.data.screens:
    for ar in screen.areas:
        if ar.type=="VIEW_3D":
            ar.spaces.active.region_3d.view_perspective="CAMERA"
bpy.ops.wm.save_as_mainfile(filepath=str(SOURCE/"smart-glasses.blend"))
bpy.ops.render.render(write_still=True)
print("GLASSES_ASSET_COMPLETE")
