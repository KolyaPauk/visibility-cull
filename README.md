# Visibility Cull

A Blender addon that deletes triangles of a merged mesh the player can never see from a set of points. From each point it casts rays in every direction. A triangle is kept when it is the first hit of at least one ray. Every other triangle is deleted, then vertices that no longer belong to a face are deleted too.

The panel language follows Blender's interface language. English and Russian are included. Russian appears when **Preferences → Interface → Translation** is set to Russian and **Interface** is enabled.

## Install

Blender 4.2 or newer is required. `visibility_cull.zip` is attached to [release v1.0.0](https://github.com/KolyaPauk/visibility-cull/releases/tag/v1.0.0).

1. Download `visibility_cull.zip` from the release. Do not unzip it.
2. In Blender open **Edit → Preferences → Get Extensions → Install from Disk** and choose the zip.
3. Enable **Visibility Cull**.

The panel is in the 3D Viewport, `N` key, **Visibility** tab.

## Usage

1. Import the mesh.
2. Place an Empty at every point the player looks from. If the empty sits at the character's feet, set **Eye height** (about 1.6).
3. Put the empties in a collection.
4. Select one or more meshes. The button processes each selected mesh on its own. Unselected objects are left unchanged.
5. In the panel assign that collection to **Viewpoints** and click **Delete unseen triangles**.

The result is a copy named with the suffix `_visible`. The source is hidden in the viewport. Show it again with `Alt+H`. The output mesh is triangulated. A triangle is kept if any viewpoint can see it.

Rays use the object's location, not a camera's facing direction. A camera in the viewpoint collection is fine: the trace covers 360°.

## Parameters

**Viewpoints.** Collection of objects whose locations emit rays. If the collection is empty, selected empties are used.

**Eye height.** World-space offset upward. `0` starts the ray at the object location.

**Resolution.** Grid density, from 1 to 1024. Rays per point are `resolution × resolution × 6`. At 128 that is 98,304 rays. At 1024 it is 6,291,456. A higher value takes longer and is less likely to miss a thin detail.

**Limit to bounds.** Grid rays are cast only toward the mesh bounding box. Directions that cannot hit the box are skipped. A point inside the box still traces a full sphere, so walls around the player are not removed. The panel reports the reduced count as `Grid rays: N of M`.

**Max distance.** Ray length. `0` means no limit.

**Ray offset.** Moves the start of each ray forward. Use it when a point happens to lie on a surface.

**Rays to centers.** An extra ray to the center of every triangle. This keeps a small face that falls between grid rays when its center is visible. On by default.

**Rays to vertices.** Extra rays toward each triangle's vertices. Slower, and better at keeping thin edges.

**Neighbor margin.** How many rings of neighboring triangles to keep around visible ones. `0` deletes every triangle a ray did not hit. `1` or `2` leaves a margin when gaps appear along edges.

**Apply modifiers.** Rays trace the mesh after modifiers. Modifiers are applied on the copy.

**See through transparent.** A material does not stop a ray when the Principled BSDF **Alpha** input is linked, Alpha is below 1, Transmission is enabled, or the node tree contains a Transparent, Glass, or Refraction BSDF. The hit triangle is kept, and so is whatever is visible through it.

For an Unreal Engine opacity mask, connect the texture to **Alpha**. If opacity is stored in the R channel and R, G, and B are identical, connect the Image Texture **Color** output to **Alpha** and set **Color Space** to **Non-Color**. The addon does not read individual pixels: a linked Alpha makes the whole material transparent, including texels that are opaque in the mask.

**Edit original object.** Write the result into the original mesh. Off by default, which creates a copy.

**Hide source.** Hide the original object in the viewport when the result is a copy.

## Limits

- Place points along the whole path the player can walk. A sparse set will delete a wall between two points.
- Deleted geometry no longer casts shadows or takes part in lighting.
- Only the mesh being processed stops rays. Merge occluders into that mesh first.
- The addon does not change collision.
