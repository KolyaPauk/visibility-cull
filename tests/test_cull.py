"""Headless checks for visibility culling. Run with:

blender --background --python tests/test_cull.py
"""

from __future__ import annotations

import sys
from math import radians
from pathlib import Path

import bpy
import bmesh
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import visibility_cull as vc  # noqa: E402

CUBE_VERTS = [
    (-1.0, -1.0, -1.0),
    (1.0, -1.0, -1.0),
    (1.0, 1.0, -1.0),
    (-1.0, 1.0, -1.0),
    (-1.0, -1.0, 1.0),
    (1.0, -1.0, 1.0),
    (1.0, 1.0, 1.0),
    (-1.0, 1.0, 1.0),
]
CUBE_FACES = [
    (0, 1, 2, 3),
    (4, 7, 6, 5),
    (0, 4, 5, 1),
    (2, 6, 7, 3),
    (0, 3, 7, 4),
    (1, 5, 6, 2),
]

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if condition:
        print(f"OK  {message}")
    else:
        FAILURES.append(message)
        print(f"FAIL {message}")


def new_mesh(name: str, verts, faces) -> bpy.types.Mesh:
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


def mesh_snapshot(mesh: bpy.types.Mesh) -> tuple[int, int, list[tuple[float, float, float]]]:
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    coords = [(v.co.x, v.co.y, v.co.z) for v in bm.verts]
    faces = len(bm.faces)
    bm.free()
    return faces, len(coords), coords


def make_level_mesh() -> bpy.types.Mesh:
    verts = list(CUBE_VERTS)
    faces = list(CUBE_FACES)
    # Interior triangle the outside viewpoint must not see.
    verts.extend([(0.0, -0.05, -0.05), (0.0, 0.05, -0.05), (0.0, 0.0, 0.05)])
    faces.append((8, 9, 10))
    # Triangle behind the cube.
    verts.extend([(-3.0, -0.2, -0.2), (-3.0, 0.2, -0.2), (-3.0, 0.0, 0.2)])
    faces.append((11, 12, 13))
    # Tiny triangle above the cube, in clear view.
    verts.extend([(0.0, 0.0, 3.0), (0.02, 0.0, 3.0), (0.0, 0.02, 3.0)])
    faces.append((14, 15, 16))
    # Isolated vertex.
    verts.append((50.0, 50.0, 50.0))
    return new_mesh("level", verts, faces)


def test_front_face_only() -> None:
    mesh = make_level_mesh()
    stats = vc.cull_mesh(
        mesh,
        Matrix.Identity(4),
        [Vector((5.0, 0.0, 0.0))],
        resolution=32,
        sample_centroids=True,
        sample_vertices=True,
    )
    faces, vert_count, coords = mesh_snapshot(mesh)
    front = [co for co in coords if abs(co[0] - 1.0) < 1.0e-5 and abs(co[2]) <= 1.0 + 1.0e-5]
    tiny = [co for co in coords if co[2] > 2.0]
    check(faces == 3, f"front + tiny triangle kept, got {faces} faces")
    check(vert_count == 7, f"front 4 verts + tiny 3, no loose verts, got {vert_count}")
    check(len(front) == 4, f"only the +X face of the cube remains, front verts {len(front)}")
    check(len(tiny) == 3, f"tiny visible triangle kept, verts {len(tiny)}")
    check(all(co[0] > 0.5 or co[2] > 2.0 for co in coords), "hidden and interior verts are gone")
    check(stats.vertices_deleted > 0, "isolated and unused vertices were deleted")


def test_low_resolution_misses_tiny_without_centroids() -> None:
    mesh = make_level_mesh()
    vc.cull_mesh(
        mesh,
        Matrix.Identity(4),
        [Vector((5.0, 0.0, 0.0))],
        resolution=2,
        sample_centroids=False,
        sample_vertices=False,
    )
    _faces, _verts, coords = mesh_snapshot(mesh)
    check(all(co[2] < 2.0 for co in coords), "coarse rays miss the tiny triangle when centroid rays are off")


def test_centroids_keep_tiny_triangle() -> None:
    mesh = make_level_mesh()
    vc.cull_mesh(
        mesh,
        Matrix.Identity(4),
        [Vector((5.0, 0.0, 0.0))],
        resolution=2,
        sample_centroids=True,
        sample_vertices=False,
    )
    _faces, _verts, coords = mesh_snapshot(mesh)
    check(any(co[2] > 2.0 for co in coords), "centroid rays keep the tiny triangle")
    check(all(abs(co[0] - 1.0) < 1.0e-4 or co[2] > 2.0 for co in coords), "centroid rays do not keep side faces")


def test_union_of_viewpoints() -> None:
    mesh = new_mesh("cube", CUBE_VERTS, CUBE_FACES)
    vc.cull_mesh(
        mesh,
        Matrix.Identity(4),
        [Vector((5.0, 0.0, 0.0)), Vector((-5.0, 0.0, 0.0))],
        resolution=24,
        sample_centroids=True,
        sample_vertices=False,
    )
    _faces, vert_count, coords = mesh_snapshot(mesh)
    xs = {round(co[0], 5) for co in coords}
    check(xs == {-1.0, 1.0}, f"both caps kept from two points, x positions {xs}")
    check(vert_count == 8, f"all eight cube corners still used, got {vert_count}")


def test_rotated_and_scaled_object() -> None:
    mesh = new_mesh("xform", CUBE_VERTS, CUBE_FACES)
    matrix = Matrix.Rotation(radians(90.0), 4, "Z") @ Matrix.Diagonal((2.0, 1.0, 1.0, 1.0))
    # Local +X becomes world +Y, then scale x does not change that axis after rotation...
    # Scale happens first (rightmost): local (1,0,0) -> (2,0,0) -> rot90z -> (0,2,0).
    # Viewpoint sits outside that face.
    vc.cull_mesh(
        mesh,
        matrix,
        [Vector((0.0, 6.0, 0.0))],
        resolution=24,
        sample_centroids=True,
        sample_vertices=False,
    )
    _faces, _verts, coords = mesh_snapshot(mesh)
    check(
        coords and all(abs(co[0] - 1.0) < 1.0e-4 for co in coords),
        f"transformed mesh keeps the local front face, xs {[round(co[0], 4) for co in coords]}",
    )


def configure_glass(material: bpy.types.Material) -> None:
    if material.node_tree is None:
        material.use_nodes = True
    for value in ("BLEND", "BLENDED", "HASHED", "DITHERED"):
        for attribute in ("blend_method", "surface_render_method"):
            if not hasattr(material, attribute):
                continue
            try:
                setattr(material, attribute, value)
            except (TypeError, ValueError):
                continue
    principled = next(
        (node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"),
        None,
    )
    if principled is not None:
        for socket in principled.inputs:
            if socket.name == "Alpha" or getattr(socket, "identifier", "") == "Alpha":
                socket.default_value = 0.2


def test_transparent_does_not_occlude() -> None:
    verts = [
        (2.0, -2.0, -2.0),
        (2.0, 2.0, -2.0),
        (2.0, 2.0, 2.0),
        (2.0, -2.0, 2.0),
        (0.0, -0.2, -0.2),
        (0.0, 0.2, -0.2),
        (0.0, 0.0, 0.2),
    ]
    faces = [(0, 1, 2, 3), (4, 5, 6)]
    blocked = new_mesh("blocked", verts, faces)
    passed = new_mesh("passed", verts, faces)
    for mesh in (blocked, passed):
        glass = bpy.data.materials.new(mesh.name + "_glass")
        opaque = bpy.data.materials.new(mesh.name + "_opaque")
        configure_glass(glass)
        if opaque.node_tree is None:
            opaque.use_nodes = True
        mesh.materials.append(glass)
        mesh.materials.append(opaque)
        mesh.polygons[0].material_index = 0
        mesh.polygons[1].material_index = 1
        mesh.update()

    check(vc.material_is_transparent(blocked.materials[0]), "glass material is detected")
    check(not vc.material_is_transparent(blocked.materials[1]), "opaque material stays opaque")

    origin = [Vector((5.0, 0.0, 0.0))]
    vc.cull_mesh(
        blocked,
        Matrix.Identity(4),
        origin,
        resolution=16,
        sample_centroids=True,
        ignore_transparent=False,
    )
    vc.cull_mesh(
        passed,
        Matrix.Identity(4),
        origin,
        resolution=16,
        sample_centroids=True,
        ignore_transparent=True,
    )
    _faces, _verts, blocked_coords = mesh_snapshot(blocked)
    _faces, _verts, passed_coords = mesh_snapshot(passed)
    check(
        blocked_coords and all(abs(co[0] - 2.0) < 1.0e-4 for co in blocked_coords),
        "opaque treatment removes the triangle behind the pane",
    )
    check(
        any(abs(co[0] - 2.0) < 1.0e-4 for co in passed_coords)
        and any(abs(co[0]) < 1.0e-4 for co in passed_coords),
        "transparent pane keeps both the glass and the triangle behind it",
    )


def clear_scene() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def test_operator_on_moved_object() -> None:
    clear_scene()
    vc.register()
    bpy.ops.mesh.primitive_cube_add(location=(10.0, 0.0, 0.0))
    cube = bpy.context.active_object
    bpy.ops.object.empty_add(type="PLAIN_AXES", location=(15.0, 0.0, 0.0))
    empty = bpy.context.active_object
    collection = bpy.data.collections.new("player_points")
    bpy.context.scene.collection.children.link(collection)
    collection.objects.link(empty)

    cube.select_set(True)
    empty.select_set(False)
    settings = bpy.context.scene.visibility_cull
    settings.viewpoints = collection
    settings.resolution = 24
    settings.sample_centroids = True
    settings.sample_vertices = False
    settings.modify_original = False
    settings.hide_source = True
    settings.use_evaluated = True
    settings.ignore_transparent = False

    result = bpy.ops.visibility_cull.run()
    check(result == {"FINISHED"}, f"operator finished, got {result}")
    visible = bpy.data.objects.get(cube.name + "_visible")
    if visible is None:
        visible = next((obj for obj in bpy.data.objects if obj.name.startswith(cube.name + "_visible")), None)
    check(visible is not None, "a visible copy was created")
    if visible is None:
        return
    _faces, vert_count, coords = mesh_snapshot(visible.data)
    check(vert_count == 4, f"moved cube keeps 4 front vertices, got {vert_count}")
    check(
        all(abs(co[0] - 1.0) < 1.0e-4 for co in coords),
        f"copy stays in object-local space, xs {[round(co[0], 4) for co in coords]}",
    )
    check(cube.hide_get(), "source object is hidden in the viewport")
    vc.unregister()


def test_operator_on_several_meshes() -> None:
    clear_scene()
    vc.register()
    bpy.ops.mesh.primitive_cube_add(location=(10.0, 0.0, 0.0))
    near = bpy.context.active_object
    near.name = "Near"
    bpy.ops.mesh.primitive_cube_add(location=(10.0, 6.0, 0.0))
    far = bpy.context.active_object
    far.name = "Far"
    bpy.ops.mesh.primitive_cube_add(location=(-20.0, 0.0, 0.0))
    skipped = bpy.context.active_object
    skipped.name = "Skipped"
    bpy.ops.object.empty_add(type="PLAIN_AXES", location=(15.0, 0.0, 0.0))
    empty = bpy.context.active_object
    collection = bpy.data.collections.new("player_points_multi")
    bpy.context.scene.collection.children.link(collection)
    collection.objects.link(empty)

    for obj in list(bpy.context.selected_objects):
        obj.select_set(False)
    near.select_set(True)
    far.select_set(True)

    settings = bpy.context.scene.visibility_cull
    settings.viewpoints = collection
    settings.resolution = 16
    settings.sample_centroids = True
    settings.sample_vertices = False
    settings.modify_original = False
    settings.hide_source = True
    settings.limit_to_bounds = True

    result = bpy.ops.visibility_cull.run()
    check(result == {"FINISHED"}, f"several meshes finished, got {result}")
    check(bpy.data.objects.get("Near_visible") is not None, "first selected mesh was processed")
    check(bpy.data.objects.get("Far_visible") is not None, "second selected mesh was processed")
    check(bpy.data.objects.get("Skipped_visible") is None, "an unselected mesh was left untouched")
    _faces, vert_count, _coords = mesh_snapshot(skipped.data)
    check(vert_count == 8, f"unselected cube keeps all 8 vertices, got {vert_count}")
    check(near.hide_get() and far.hide_get(), "both processed sources are hidden")
    check(not skipped.hide_get(), "the unselected mesh stays visible")
    vc.unregister()


def test_face_projection_roundtrip() -> None:
    samples = ((0.0, 0.0), (0.4, -0.2), (-0.7, 0.5), (0.95, 0.1))
    for face in range(6):
        for horizontal, vertical in samples:
            direction = vc.cube_face_direction(face, horizontal, vertical)
            projected = vc.cube_face_uv(face, direction.x, direction.y, direction.z)
            check(
                projected is not None
                and abs(projected[0] - horizontal) < 1.0e-6
                and abs(projected[1] - vertical) < 1.0e-6,
                f"face {face} uv {horizontal, vertical} projects back to {projected}",
            )


def test_bounds_limit_matches_full_sphere() -> None:
    mesh_full = new_mesh("bounds_full", CUBE_VERTS, CUBE_FACES)
    mesh_limited = new_mesh("bounds_limited", CUBE_VERTS, CUBE_FACES)
    origin = [Vector((5.0, 5.0, 5.0))]
    options = dict(resolution=16, sample_centroids=True, sample_vertices=False)
    full = vc.cull_mesh(mesh_full, Matrix.Identity(4), origin, limit_to_bounds=False, **options)
    limited = vc.cull_mesh(mesh_limited, Matrix.Identity(4), origin, limit_to_bounds=True, **options)
    _faces, _verts, full_coords = mesh_snapshot(mesh_full)
    _faces, _verts, limited_coords = mesh_snapshot(mesh_limited)
    check(full.rays_cast > limited.rays_cast, f"bounds culling casts fewer rays ({limited.rays_cast} vs {full.rays_cast})")
    check(
        sorted(tuple(round(c, 4) for c in co) for co in full_coords)
        == sorted(tuple(round(c, 4) for c in co) for co in limited_coords),
        "exterior viewpoint keeps the same vertices when rays are limited to the bounds",
    )


def test_bounds_limit_inside_uses_full_sphere() -> None:
    outside = [Vector((5.0, 0.0, 0.0))]
    inside = [Vector((0.0, 0.0, 0.0))]
    corners = [Vector(co) for co in CUBE_VERTS]
    resolution = 8
    full = resolution * resolution * 6
    check(
        vc.grid_rays_toward_bounds(inside, corners, resolution) == full,
        "a viewpoint inside the bounds still traces the full sphere",
    )
    check(
        vc.grid_rays_toward_bounds(outside, corners, resolution) < full,
        "a viewpoint outside the bounds skips rays that miss the box",
    )


def test_interface_language() -> None:
    view = bpy.context.preferences.view
    saved = (view.language, view.use_translate_interface, view.use_translate_tooltips)
    vc.register()
    try:
        view.language = "en_US"
        view.use_translate_interface = True
        english = vc.iface_("Delete unseen triangles")
        check(english == "Delete unseen triangles", f"English interface keeps the source string, got {english!r}")

        view.language = "ru_RU"
        view.use_translate_interface = True
        view.use_translate_tooltips = True
        russian = vc.iface_("Delete unseen triangles")
        check(russian == "Удалить невидимые треугольники", f"Russian interface translates the button, got {russian!r}")
        label = bpy.app.translations.pgettext("Selected meshes: {count}", "*")
        check(label == "Выбрано мешей: {count}", f"Russian interface translates the mesh count, got {label!r}")
        tip = bpy.app.translations.pgettext_tip("Ray length. 0 means no limit", "*")
        check(tip == "Дальность луча. 0 — без ограничения", f"Russian interface translates tooltips, got {tip!r}")
        summary = vc.iface_("Points: {count}. Grid rays per point: {rays}").format(count=2, rays=24)
        check(summary == "Точек: 2. Лучей сетки на точку: 24", f"Russian interface translates the status line, got {summary!r}")
    finally:
        view.language, view.use_translate_interface, view.use_translate_tooltips = saved
        vc.unregister()


def main() -> None:
    test_interface_language()
    test_face_projection_roundtrip()
    test_bounds_limit_matches_full_sphere()
    test_bounds_limit_inside_uses_full_sphere()
    test_front_face_only()
    test_low_resolution_misses_tiny_without_centroids()
    test_centroids_keep_tiny_triangle()
    test_union_of_viewpoints()
    test_rotated_and_scaled_object()
    test_transparent_does_not_occlude()
    test_operator_on_moved_object()
    test_operator_on_several_meshes()
    if FAILURES:
        print(f"\n{len(FAILURES)} failed")
        for message in FAILURES:
            print(f" - {message}")
        raise SystemExit(1)
    print("\nAll visibility cull checks passed")


if __name__ == "__main__":
    main()
