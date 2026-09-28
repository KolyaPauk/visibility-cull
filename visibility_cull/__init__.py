# SPDX-License-Identifier: MIT
"""Delete mesh triangles that no viewpoint can see.

From each viewpoint the addon casts rays in every direction (six faces of a
cube map) and, optionally, extra rays at each triangle. A triangle stays only
when it is the first surface hit by at least one ray. Every other triangle is
deleted, then vertices that no longer belong to a face are deleted too.

Hardware ray tracing is not used. Blender's Python API does not expose OptiX
or DXR queries. Primary visibility is the first hit along a direction, and a
dense cube-map grid samples those directions. BVHTree performs the same query
on the CPU.
"""

from __future__ import annotations

import math
import time

import bmesh
import bpy
from bpy.app.translations import pgettext_iface as iface_
from mathutils import Vector
from mathutils.bvhtree import BVHTree

bl_info = {
    "name": "Visibility Cull",
    "author": "kolya",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Visibility",
    "description": "Delete triangles never hit by rays cast in all directions from viewpoints",
    "category": "Mesh",
}

MAX_DISTANCE = 1.0e18
TRACE_STEPS = 16
PASS_EPSILON = 1.0e-4


def _input_socket(node, *names: str):
    for socket in node.inputs:
        identifier = getattr(socket, "identifier", "")
        if socket.name in names or identifier in names:
            return socket
    return None


def _socket_is_open(socket, threshold: float, *, below: bool) -> bool:
    """True when a shader socket lets light through.

    Blender 5 defaults new materials to dithered/hashed even when they are
    fully opaque, so the blend mode alone is not transparency. An open or
    lowered Alpha, or a raised Transmission, is.
    """
    if socket is None:
        return False
    if socket.is_linked:
        return True
    value = float(socket.default_value)
    return value < threshold if below else value > threshold


def material_is_transparent(material: bpy.types.Material | None) -> bool:
    """True when a material should not block visibility rays."""
    if material is None:
        return False

    tree = material.node_tree
    if tree is None:
        return getattr(material, "blend_method", "OPAQUE") in {"BLEND", "BLENDED"}

    for node in tree.nodes:
        if node.type in {"BSDF_TRANSPARENT", "BSDF_GLASS", "BSDF_REFRACTION"}:
            return True
        if node.type != "BSDF_PRINCIPLED":
            continue
        if _socket_is_open(_input_socket(node, "Alpha"), 0.999, below=True):
            return True
        if _socket_is_open(
            _input_socket(node, "Transmission Weight", "Transmission"),
            0.001,
            below=False,
        ):
            return True
    return False


def collect_transparent_faces(mesh: bpy.types.Mesh, bm: bmesh.types.BMesh) -> set[int]:
    flags = [material_is_transparent(material) for material in mesh.materials]
    if not any(flags):
        return set()
    transparent: set[int] = set()
    for face in bm.faces:
        index = face.material_index
        if 0 <= index < len(flags) and flags[index]:
            transparent.add(face.index)
    return transparent


def cubemap_directions(resolution: int) -> list[Vector]:
    """Unit directions through the pixel centers of a cube map.

    Six faces cover every direction. Pixel centers leave no overlap and no
    gaps larger than one sample cell.
    """
    if resolution < 1:
        raise ValueError("resolution must be >= 1")
    full = (0, resolution - 1, 0, resolution - 1)
    return directions_for_windows(resolution, (full,) * 6)


def cube_face_direction(face: int, horizontal: float, vertical: float) -> Vector:
    """Unnormalized direction for one cube-face sample. `face` is 0..5: +X -X +Y -Y +Z -Z."""
    if face == 0:
        return Vector((1.0, -vertical, -horizontal))
    if face == 1:
        return Vector((-1.0, -vertical, horizontal))
    if face == 2:
        return Vector((horizontal, 1.0, vertical))
    if face == 3:
        return Vector((horizontal, -1.0, -vertical))
    if face == 4:
        return Vector((horizontal, -vertical, 1.0))
    return Vector((-horizontal, -vertical, -1.0))


def cube_face_uv(face: int, dx: float, dy: float, dz: float) -> tuple[float, float] | None:
    """Project a vector onto a cube face. None when the vector looks away from that face."""
    if face == 0:
        if dx <= 1.0e-12:
            return None
        return (-dz / dx, -dy / dx)
    if face == 1:
        if dx >= -1.0e-12:
            return None
        return (dz / -dx, dy / dx)
    if face == 2:
        if dy <= 1.0e-12:
            return None
        return (dx / dy, dz / dy)
    if face == 3:
        if dy >= -1.0e-12:
            return None
        return (dx / -dy, dz / dy)
    if face == 4:
        if dz <= 1.0e-12:
            return None
        return (dx / dz, -dy / dz)
    if dz >= -1.0e-12:
        return None
    return (dx / dz, dy / dz)


def _pixel_span(low: float, high: float, resolution: int) -> tuple[int, int] | None:
    first = math.ceil(resolution * (low + 1.0) * 0.5 - 0.5 - 1.0e-9)
    last = math.floor(resolution * (high + 1.0) * 0.5 - 0.5 + 1.0e-9)
    first = max(0, first)
    last = min(resolution - 1, last)
    if first > last:
        return None
    return first, last


def world_aabb(points: list[Vector]) -> tuple[Vector, Vector]:
    lower = Vector(points[0])
    upper = Vector(points[0])
    for point in points[1:]:
        for axis in range(3):
            lower[axis] = min(lower[axis], point[axis])
            upper[axis] = max(upper[axis], point[axis])
    return lower, upper


def expand_aabb(lower: Vector, upper: Vector) -> tuple[Vector, Vector]:
    span = max(upper.x - lower.x, upper.y - lower.y, upper.z - lower.z)
    pad = max(1.0e-4, span * 1.0e-5)
    margin = Vector((pad, pad, pad))
    return lower - margin, upper + margin


def aabb_corners(lower: Vector, upper: Vector) -> list[Vector]:
    corners: list[Vector] = []
    for x in (lower.x, upper.x):
        for y in (lower.y, upper.y):
            for z in (lower.z, upper.z):
                corners.append(Vector((x, y, z)))
    return corners


def point_inside_aabb(point: Vector, lower: Vector, upper: Vector) -> bool:
    return (
        lower.x <= point.x <= upper.x
        and lower.y <= point.y <= upper.y
        and lower.z <= point.z <= upper.z
    )


def bounds_windows(
    origin: Vector,
    corners: list[Vector],
    resolution: int,
    lower: Vector,
    upper: Vector,
) -> tuple[tuple[int, int, int, int] | None, ...]:
    """Pixel windows on the six cube faces that can see the bounding box.

    A viewpoint inside the box gets the full sphere: every direction can hit
    the mesh. Outside, each face keeps the pixels that cover the box, padded
    by half a sample so a cell on the silhouette is not dropped.
    """
    full = (0, resolution - 1, 0, resolution - 1)
    if point_inside_aabb(origin, lower, upper):
        return (full,) * 6

    pad = 1.0 / resolution
    windows: list[tuple[int, int, int, int] | None] = []
    for face in range(6):
        horizontal_min = vertical_min = math.inf
        horizontal_max = vertical_max = -math.inf
        for corner in corners:
            offset = corner - origin
            projected = cube_face_uv(face, offset.x, offset.y, offset.z)
            if projected is None:
                continue
            horizontal, vertical = projected
            horizontal_min = min(horizontal_min, horizontal)
            horizontal_max = max(horizontal_max, horizontal)
            vertical_min = min(vertical_min, vertical)
            vertical_max = max(vertical_max, vertical)
        if horizontal_min > horizontal_max:
            windows.append(None)
            continue
        horizontal_span = _pixel_span(horizontal_min - pad, horizontal_max + pad, resolution)
        vertical_span = _pixel_span(vertical_min - pad, vertical_max + pad, resolution)
        if horizontal_span is None or vertical_span is None:
            windows.append(None)
            continue
        windows.append((horizontal_span[0], horizontal_span[1], vertical_span[0], vertical_span[1]))
    return tuple(windows)


def window_ray_count(windows: tuple[tuple[int, int, int, int] | None, ...]) -> int:
    total = 0
    for window in windows:
        if window is None:
            continue
        u0, u1, v0, v1 = window
        total += (u1 - u0 + 1) * (v1 - v0 + 1)
    return total


def directions_for_windows(
    resolution: int,
    windows: tuple[tuple[int, int, int, int] | None, ...],
) -> list[Vector]:
    coords = [(index + 0.5) / resolution * 2.0 - 1.0 for index in range(resolution)]
    directions: list[Vector] = []
    append = directions.append
    for face, window in enumerate(windows):
        if window is None:
            continue
        u0, u1, v0, v1 = window
        for vertical_index in range(v0, v1 + 1):
            vertical = coords[vertical_index]
            for horizontal_index in range(u0, u1 + 1):
                append(cube_face_direction(face, coords[horizontal_index], vertical).normalized())
    return directions


def grid_rays_toward_bounds(
    origins: list[Vector],
    corners: list[Vector],
    resolution: int,
) -> int:
    if not corners:
        return 0
    lower, upper = expand_aabb(*world_aabb(corners))
    box = aabb_corners(lower, upper)
    return sum(window_ray_count(bounds_windows(origin, box, resolution, lower, upper)) for origin in origins)


def trace_ray(
    ray_cast,
    origin: Vector,
    direction: Vector,
    max_distance: float,
    transparent: set[int] | None,
    bias: float,
) -> list[tuple[int, float]]:
    """Walk one ray and return (face index, distance from origin) pairs.

    The walk stops at the first opaque face. Transparent faces are recorded
    and the ray continues, so geometry behind glass stays visible.
    """
    hits: list[tuple[int, float]] = []
    traveled = 0.0
    start = origin
    if bias > 0.0:
        start = origin + direction * bias
        traveled = bias

    for _ in range(TRACE_STEPS):
        remaining = max_distance - traveled
        if remaining <= 1.0e-8:
            break
        _location, _normal, index, distance = ray_cast(start, direction, remaining)
        if index is None or index < 0 or distance is None:
            break
        traveled_hit = traveled + float(distance)
        hits.append((int(index), traveled_hit))
        if transparent is None or index not in transparent:
            break
        traveled = traveled_hit + PASS_EPSILON
        start = origin + direction * traveled
    return hits


def sample_reaches_face(
    hits: list[tuple[int, float]],
    face_index: int,
    length: float,
    transparent: set[int] | None,
    accept_same_depth: bool,
) -> bool:
    """True when this face is the surface seen at a sample point.

    A closer opaque hit hides the face. Transparent hits do not.
    Shared vertices lie on several faces at once, so a vertex sample counts
    only when the hit face is this one. A centroid belongs to one face, so a
    hit at that depth counts even if the BVH returns a coplanar neighbour.
    """
    tolerance = max(1.0e-4, length * 1.0e-5)
    for index, distance in hits:
        if index == face_index:
            return True
        if transparent is not None and index in transparent:
            continue
        if distance < length - tolerance:
            return False
        return accept_same_depth
    return False


def expand_visible(bm: bmesh.types.BMesh, visible: set[int], rings: int) -> set[int]:
    if rings <= 0 or not visible:
        return visible

    bm.faces.ensure_lookup_table()
    current = {bm.faces[index] for index in visible if index < len(bm.faces)}
    for _ in range(rings):
        expanded = set(current)
        for face in current:
            for edge in face.edges:
                expanded.update(edge.link_faces)
        current = expanded
    return {face.index for face in current}


def delete_unseen(
    bm: bmesh.types.BMesh,
    visible: set[int],
) -> tuple[int, int]:
    """Delete faces outside `visible`, then vertices that belong to no face.

    Returns (deleted faces, deleted vertices).
    """
    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    vertices_before = len(bm.verts)
    doomed = [face for face in bm.faces if face.index not in visible]
    deleted_faces = len(doomed)
    if doomed:
        bmesh.ops.delete(bm, geom=doomed, context="FACES")

    loose_edges = [edge for edge in bm.edges if not edge.link_faces]
    if loose_edges:
        bmesh.ops.delete(bm, geom=loose_edges, context="EDGES")

    loose_vertices = [vertex for vertex in bm.verts if not vertex.link_faces]
    if loose_vertices:
        bmesh.ops.delete(bm, geom=loose_vertices, context="VERTS")

    return deleted_faces, vertices_before - len(bm.verts)


def _world_vertex_positions(bm: bmesh.types.BMesh, matrix_world) -> list[Vector]:
    world = [Vector()] * len(bm.verts)
    for vertex in bm.verts:
        world[vertex.index] = matrix_world @ vertex.co
    return world


def _triangulated_polygons(bm: bmesh.types.BMesh) -> list[tuple[int, int, int]]:
    bmesh.ops.triangulate(
        bm,
        faces=bm.faces[:],
        quad_method="FIXED",
        ngon_method="EAR_CLIP",
    )
    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    polygons: list[tuple[int, int, int] | None] = [None] * len(bm.faces)
    for face in bm.faces:
        if len(face.verts) != 3:
            raise RuntimeError("Triangulation left a non-triangle face")
        polygons[face.index] = (
            face.verts[0].index,
            face.verts[1].index,
            face.verts[2].index,
        )
    if any(polygon is None for polygon in polygons):
        raise RuntimeError("Face indices after triangulation are not contiguous")
    return polygons  # type: ignore[return-value]


class CullStats:
    def __init__(self) -> None:
        self.triangles_before = 0
        self.triangles_kept = 0
        self.triangles_deleted = 0
        self.vertices_before = 0
        self.vertices_deleted = 0
        self.rays_cast = 0
        self.seconds = 0.0

    @property
    def vertices_after(self) -> int:
        return self.vertices_before - self.vertices_deleted


def cull_bmesh(
    bm: bmesh.types.BMesh,
    matrix_world,
    origins: list[Vector],
    *,
    resolution: int = 128,
    max_distance: float = 0.0,
    ray_bias: float = 0.0,
    sample_centroids: bool = True,
    sample_vertices: bool = False,
    adjacency_rings: int = 0,
    limit_to_bounds: bool = False,
    ignore_transparent: bool = False,
    mesh: bpy.types.Mesh | None = None,
    progress=None,
) -> CullStats:
    """Mark triangles hit from any origin and delete the rest.

    `origins` are world-space points. `matrix_world` transforms the bmesh,
    which stays in local space, into the same coordinates. When
    `ignore_transparent` is set, materials on `mesh` that are transparent do
    not stop rays. Prefer `cull_mesh`, which writes the result back.
    """
    if not origins:
        raise ValueError("Add at least one viewpoint")

    started = time.perf_counter()
    stats = CullStats()
    stats.vertices_before = len(bm.verts)

    polygons = _triangulated_polygons(bm)
    stats.triangles_before = len(polygons)
    if stats.triangles_before == 0:
        stats.seconds = time.perf_counter() - started
        return stats

    transparent = None
    if ignore_transparent and mesh is not None:
        transparent = collect_transparent_faces(mesh, bm) or None

    world_positions = _world_vertex_positions(bm, matrix_world)
    tree = BVHTree.FromPolygons(world_positions, polygons, all_triangles=True, epsilon=0.0)
    ray_cast = tree.ray_cast
    limit = MAX_DISTANCE if max_distance <= 0.0 else max_distance
    visible: set[int] = set()
    rays = 0

    def report(done: int, total: int) -> None:
        if progress is not None:
            progress(done, total)

    full_directions = cubemap_directions(resolution)
    windows_per_origin: list[tuple | None] = [None] * len(origins)
    if limit_to_bounds:
        lower, upper = expand_aabb(*world_aabb(world_positions))
        bound_corners = aabb_corners(lower, upper)
        for index, origin in enumerate(origins):
            if not point_inside_aabb(origin, lower, upper):
                windows_per_origin[index] = bounds_windows(origin, bound_corners, resolution, lower, upper)
    sphere_total = 0
    for windows in windows_per_origin:
        if windows is None:
            sphere_total += len(full_directions)
        else:
            sphere_total += window_ray_count(windows)
    for origin, windows in zip(origins, windows_per_origin):
        directions = full_directions if windows is None else directions_for_windows(resolution, windows)
        for direction in directions:
            for index, _distance in trace_ray(
                ray_cast, origin, direction, limit, transparent, ray_bias
            ):
                visible.add(index)
            rays += 1
            if rays % 8192 == 0:
                report(rays, sphere_total)
    report(sphere_total, sphere_total)

    if sample_centroids or sample_vertices:
        centers = [None] * len(bm.faces)
        corners: list[tuple[int, ...]] = [()] * len(bm.faces)
        for face in bm.faces:
            centers[face.index] = matrix_world @ face.calc_center_median()
            if sample_vertices:
                corners[face.index] = tuple(vertex.index for vertex in face.verts)

        face_total = len(bm.faces)
        for face_index in range(face_total):
            if face_index in visible:
                continue
            targets: list[Vector] = []
            if sample_centroids:
                targets.append(centers[face_index])
            if sample_vertices:
                # Inset from each vertex toward the centroid so the sample lies
                # on this face only. A ray aimed at a shared corner can hit the
                # neighbouring face and would otherwise keep occluded sides.
                center = centers[face_index]
                for index in corners[face_index]:
                    corner = world_positions[index]
                    targets.append(corner + (center - corner) * 0.05)

            centroid_count = 1 if sample_centroids else 0
            for sample_index, target in enumerate(targets):
                seen = False
                for origin in origins:
                    direction = target - origin
                    length = direction.length
                    if length <= max(ray_bias, 1.0e-8):
                        seen = True
                        break
                    if length > limit:
                        continue
                    direction.normalize()
                    hits = trace_ray(
                        ray_cast, origin, direction, limit, transparent, ray_bias
                    )
                    rays += 1
                    if sample_reaches_face(
                        hits,
                        face_index,
                        length,
                        transparent,
                        accept_same_depth=sample_index < centroid_count,
                    ):
                        seen = True
                        break
                if seen:
                    visible.add(face_index)
                    break
            if face_index % 1024 == 0:
                report(face_index, face_total)

    visible = expand_visible(bm, visible, adjacency_rings)
    deleted_faces, deleted_vertices = delete_unseen(bm, visible)
    stats.triangles_kept = stats.triangles_before - deleted_faces
    stats.triangles_deleted = deleted_faces
    stats.vertices_deleted = deleted_vertices
    stats.rays_cast = rays
    stats.seconds = time.perf_counter() - started
    return stats


def cull_mesh(
    mesh: bpy.types.Mesh,
    matrix_world,
    origins: list[Vector],
    *,
    ignore_transparent: bool = False,
    **kwargs,
) -> CullStats:
    """Triangulate `mesh`, cull it in place, and return statistics."""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    try:
        stats = cull_bmesh(
            bm,
            matrix_world,
            origins,
            ignore_transparent=ignore_transparent,
            mesh=mesh,
            **kwargs,
        )
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()
    return stats


def load_working_mesh(obj: bpy.types.Object, context, use_evaluated: bool) -> bpy.types.Mesh:
    mesh = bpy.data.meshes.new(obj.data.name + "_visible")
    bm = bmesh.new()
    try:
        if use_evaluated:
            bm.from_object(obj, context.evaluated_depsgraph_get())
        else:
            bm.from_mesh(obj.data)
        bm.to_mesh(mesh)
    except Exception:
        bm.free()
        bpy.data.meshes.remove(mesh)
        raise
    bm.free()
    mesh.update()
    for material in obj.data.materials:
        mesh.materials.append(material)
    return mesh


def selected_targets(context) -> list[bpy.types.Object]:
    """Selected meshes, in name order, so a multi-selection runs predictably."""
    return sorted(
        (obj for obj in context.selected_objects if obj.type == "MESH" and obj.data is not None),
        key=lambda obj: obj.name,
    )


def viewpoint_objects(context, targets: list[bpy.types.Object], collection) -> list[bpy.types.Object]:
    excluded = set(targets)
    if collection is not None:
        objects = list(collection.all_objects)
    else:
        objects = [obj for obj in context.selected_objects if obj.type != "MESH"]
    return [obj for obj in objects if obj not in excluded]


def viewpoint_origins(objects: list[bpy.types.Object], eye_height: float) -> list[Vector]:
    origins: list[Vector] = []
    for obj in objects:
        origin = obj.matrix_world.translation.copy()
        origin.z += eye_height
        origins.append(origin)
    return origins


class VisibilityCullSettings(bpy.types.PropertyGroup):
    viewpoints: bpy.props.PointerProperty(
        name="Viewpoints",
        description="Collection of empties at player positions. If empty, selected empties are used",
        type=bpy.types.Collection,
    )
    eye_height: bpy.props.FloatProperty(
        name="Eye height",
        description="World up offset when the empty sits at the character's feet",
        default=0.0,
        unit="LENGTH",
    )
    resolution: bpy.props.IntProperty(
        name="Resolution",
        description="Samples per side of one cube-map face. Rays per point: resolution × resolution × 6. The grid covers every direction, and a hit is the first triangle along a ray",
        default=128,
        min=1,
        max=1024,
    )
    limit_to_bounds: bpy.props.BoolProperty(
        name="Limit to bounds",
        description="Cast grid rays only toward the target bounding box. A viewpoint inside the box still traces a full sphere",
        default=True,
    )
    max_distance: bpy.props.FloatProperty(
        name="Max distance",
        description="Ray length. 0 means no limit",
        default=0.0,
        min=0.0,
        unit="LENGTH",
    )
    ray_bias: bpy.props.FloatProperty(
        name="Ray offset",
        description="Shift the ray start forward when the point lies on a surface",
        default=0.0,
        min=0.0,
        unit="LENGTH",
    )
    sample_centroids: bpy.props.BoolProperty(
        name="Rays to centers",
        description="Extra ray to each triangle center, so small faces between grid rays are kept",
        default=True,
    )
    sample_vertices: bpy.props.BoolProperty(
        name="Rays to vertices",
        description="Extra rays toward each triangle's vertices. Slower, and better for thin edges",
        default=False,
    )
    adjacency_rings: bpy.props.IntProperty(
        name="Neighbor margin",
        description="Rings of neighboring triangles to keep around visible ones. 0 deletes every triangle a ray did not hit",
        default=0,
        min=0,
        max=3,
    )
    use_evaluated: bpy.props.BoolProperty(
        name="Apply modifiers",
        description="Trace the mesh after modifiers. Modifiers are applied on the result",
        default=True,
    )
    ignore_transparent: bpy.props.BoolProperty(
        name="See through transparent",
        description="Transparent materials do not stop a ray. The glass itself stays when a ray hits it",
        default=False,
    )
    modify_original: bpy.props.BoolProperty(
        name="Edit original object",
        description="Write the result into the original mesh. Off creates a copy and hides the source in the viewport",
        default=False,
    )
    hide_source: bpy.props.BoolProperty(
        name="Hide source",
        description="Hide the original object in the viewport when the result is a copy",
        default=True,
    )


class VISIBILITYCULL_OT_run(bpy.types.Operator):
    bl_idname = "visibility_cull.run"
    bl_label = "Delete unseen triangles"
    bl_description = "On every selected mesh, cast rays in every direction from each point, delete triangles no ray saw, then delete vertices that belong to no face"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.visibility_cull
        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                self.report({"ERROR"}, iface_("Switch to Object Mode"))
                return {"CANCELLED"}

        targets = selected_targets(context)
        if not targets:
            self.report({"ERROR"}, iface_("Select one or more meshes"))
            return {"CANCELLED"}
        if settings.modify_original and any(target.data.library is not None for target in targets):
            self.report(
                {"ERROR"},
                iface_('The mesh is linked from a library. Turn off "Edit original object" or make the mesh local'),
            )
            return {"CANCELLED"}

        sources = viewpoint_objects(context, targets, settings.viewpoints)
        origins = viewpoint_origins(sources, settings.eye_height)
        if not origins:
            self.report(
                {"ERROR"},
                iface_("No viewpoints. Assign a collection of empties, or select them together with the meshes"),
            )
            return {"CANCELLED"}

        window_manager = context.window_manager
        window_manager.progress_begin(0, 1000)
        target_count = len(targets)

        def progress_for(target_index: int):
            def progress(done: int, total: int) -> None:
                if total <= 0:
                    fraction = 1.0
                else:
                    fraction = done / total
                overall = (target_index + fraction) / target_count
                window_manager.progress_update(min(1000, int(overall * 1000)))

            return progress

        kept = total_triangles = removed_vertices = rays = 0
        seconds = 0.0
        results: list[bpy.types.Object] = []
        failures: list[str] = []
        try:
            for index, target in enumerate(targets):
                mesh = None
                try:
                    mesh = load_working_mesh(target, context, settings.use_evaluated)
                    stats = cull_mesh(
                        mesh,
                        target.matrix_world.copy(),
                        origins,
                        resolution=settings.resolution,
                        max_distance=settings.max_distance,
                        limit_to_bounds=settings.limit_to_bounds,
                        ray_bias=settings.ray_bias,
                        sample_centroids=settings.sample_centroids,
                        sample_vertices=settings.sample_vertices,
                        adjacency_rings=settings.adjacency_rings,
                        ignore_transparent=settings.ignore_transparent,
                        progress=progress_for(index),
                    )
                except Exception as error:  # noqa: BLE001 - keep going with the remaining meshes
                    if mesh is not None and mesh.users == 0:
                        bpy.data.meshes.remove(mesh)
                    failures.append(target.name)
                    self.report({"WARNING"}, f"{target.name}: {iface_(str(error))}")
                    continue

                if settings.modify_original:
                    old_mesh = target.data
                    target.data = mesh
                    target.modifiers.clear()
                    if old_mesh.users == 0:
                        bpy.data.meshes.remove(old_mesh)
                    result = target
                else:
                    result = target.copy()
                    result.data = mesh
                    result.name = target.name + "_visible"
                    result.modifiers.clear()
                    collections = list(target.users_collection) or [context.collection]
                    for collection in collections:
                        collection.objects.link(result)
                    if settings.hide_source:
                        target.hide_set(True)
                results.append(result)
                kept += stats.triangles_kept
                total_triangles += stats.triangles_before
                removed_vertices += stats.vertices_deleted
                rays += stats.rays_cast
                seconds += stats.seconds
        finally:
            window_manager.progress_end()

        if not results:
            self.report({"ERROR"}, iface_("Select one or more meshes") if not failures else iface_("Could not process the selected meshes"))
            return {"CANCELLED"}

        for obj in context.selected_objects:
            obj.select_set(False)
        for result in results:
            result.select_set(True)
        context.view_layer.objects.active = results[-1]

        self.report(
            {"WARNING"} if failures or kept == 0 else {"INFO"},
            iface_(
                "Processed {done} of {objects} meshes. Kept {kept} of {total} triangles, "
                "removed unused vertices: {removed}. Rays: {rays}. {seconds:.1f} s."
            ).format(
                done=len(results),
                objects=target_count,
                kept=kept,
                total=total_triangles,
                removed=removed_vertices,
                rays=rays,
                seconds=seconds,
            ),
        )
        return {"FINISHED"}


class VISIBILITYCULL_PT_panel(bpy.types.Panel):
    bl_label = "Visibility Cull"
    bl_idname = "VISIBILITYCULL_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Visibility"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.visibility_cull
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.label(text=iface_("Rays from player points in every direction"))
        layout.label(text=iface_("Each selected mesh is processed on its own"))
        meshes = selected_targets(context)
        layout.label(text=iface_("Selected meshes: {count}").format(count=len(meshes)))
        layout.prop(settings, "viewpoints")
        layout.prop(settings, "eye_height")

        sources = viewpoint_objects(context, meshes, settings.viewpoints)
        count = len(sources)
        per_point = settings.resolution * settings.resolution * 6
        full = len(meshes) * count * per_point
        aimed = full
        if settings.limit_to_bounds and meshes and count:
            origins = viewpoint_origins(sources, settings.eye_height)
            aimed = 0
            for mesh_obj in meshes:
                source = mesh_obj
                if settings.use_evaluated:
                    source = mesh_obj.evaluated_get(context.evaluated_depsgraph_get())
                corners = [source.matrix_world @ Vector(corner) for corner in source.bound_box]
                aimed += grid_rays_toward_bounds(origins, corners, settings.resolution)
        if settings.limit_to_bounds:
            layout.label(
                text=iface_("Points: {count}. Grid rays: {rays} of {full}").format(
                    count=count, rays=aimed, full=full
                )
            )
        else:
            layout.label(
                text=iface_("Points: {count}. Grid rays per point: {rays}").format(count=count, rays=per_point)
            )

        layout.prop(settings, "resolution")
        layout.prop(settings, "limit_to_bounds")
        layout.prop(settings, "max_distance")
        layout.prop(settings, "ray_bias")
        layout.prop(settings, "sample_centroids")
        layout.prop(settings, "sample_vertices")
        layout.prop(settings, "adjacency_rings")
        layout.prop(settings, "use_evaluated")
        layout.prop(settings, "ignore_transparent")
        layout.prop(settings, "modify_original")
        if not settings.modify_original:
            layout.prop(settings, "hide_source")

        layout.separator()
        layout.operator("visibility_cull.run", icon="HIDE_OFF")


CLASSES = (
    VisibilityCullSettings,
    VISIBILITYCULL_OT_run,
    VISIBILITYCULL_PT_panel,
)


def register():
    from .translations import translation_dict

    bpy.app.translations.register(__name__, translation_dict())
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.visibility_cull = bpy.props.PointerProperty(type=VisibilityCullSettings)


def unregister():
    delete = getattr(bpy.types.Scene, "visibility_cull", None)
    if delete is not None:
        del bpy.types.Scene.visibility_cull
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    bpy.app.translations.unregister(__name__)
