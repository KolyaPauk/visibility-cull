"""English source strings with a Russian catalog.

Blender shows the English text when the interface language is English.
The Russian catalog is used when Preferences → Interface → Translation
is set to Russian and Interface translation is enabled.
"""

MESSAGES_RU = {
    "Visibility Cull": "Очистка по видимости",
    "Delete triangles never hit by rays cast in all directions from viewpoints": (
        "Удаляет треугольники, в которые не попал ни один луч из точек обзора"
    ),
    "View3D > Sidebar > Visibility": "3D-вид > Боковая панель > Видимость",
    "Visibility": "Видимость",
    "Viewpoints": "Точки обзора",
    "Collection of empties at player positions. If empty, selected empties are used": (
        "Коллекция пустышек в местах игрока. Если пусто, берутся выделенные пустышки"
    ),
    "Each selected mesh is processed on its own": "Каждый выделенный меш обрабатывается отдельно",
    "Selected meshes: {count}": "Выбрано мешей: {count}",
    "Select one or more meshes": "Выделите один или несколько мешей",
    "Could not process the selected meshes": "Не удалось обработать выделенные меши",
    "Eye height": "Высота глаз",
    "World up offset when the empty sits at the character's feet": (
        "Смещение вверх по миру, если пустышка стоит в ногах персонажа"
    ),
    "Limit to bounds": "Только в габариты",
    "Cast grid rays only toward the target bounding box. A viewpoint inside the box still traces a full sphere": (
        "Пускать лучи сетки только в сторону габаритного бокса цели. Точка внутри бокса по-прежнему трассирует полную сферу"
    ),
    "Points: {count}. Grid rays: {rays} of {full}": "Точек: {count}. Лучей сетки: {rays} из {full}",
    "Resolution": "Разрешение",
    "Samples per side of one cube-map face. Rays per point: resolution × resolution × 6. The grid covers every direction, and a hit is the first triangle along a ray": (
        "Число лучей по стороне одной грани кубической карты. Лучей на точку: разрешение × разрешение × 6. Сетка покрывает все направления, попадание — первый треугольник вдоль луча"
    ),
    "Max distance": "Макс. дистанция",
    "Ray length. 0 means no limit": "Дальность луча. 0 — без ограничения",
    "Ray offset": "Отступ луча",
    "Shift the ray start forward when the point lies on a surface": (
        "Сдвиг старта луча вперёд, если точка лежит на поверхности"
    ),
    "Rays to centers": "Лучи в центры",
    "Extra ray to each triangle center, so small faces between grid rays are kept": (
        "Дополнительный луч в центр каждого треугольника, чтобы не удалить мелкие грани между лучами сетки"
    ),
    "Rays to vertices": "Лучи в вершины",
    "Extra rays toward each triangle's vertices. Slower, and better for thin edges": (
        "Дополнительные лучи к вершинам треугольника. Медленнее, лучше ловит тонкие края"
    ),
    "Neighbor margin": "Запас соседей",
    "Rings of neighboring triangles to keep around visible ones. 0 deletes every triangle a ray did not hit": (
        "Сколько колец соседних треугольников оставить вокруг видимых. 0 удаляет всё, во что луч не попал"
    ),
    "Apply modifiers": "Учитывать модификаторы",
    "Trace the mesh after modifiers. Modifiers are applied on the result": (
        "Строить лучи по мешу после модификаторов. На результате модификаторы уже применены"
    ),
    "See through transparent": "Смотреть сквозь прозрачные",
    "Transparent materials do not stop a ray. The glass itself stays when a ray hits it": (
        "Материалы с прозрачностью не останавливают луч. Само стекло остаётся, если луч в него попал"
    ),
    "Edit original object": "Менять исходный объект",
    "Write the result into the original mesh. Off creates a copy and hides the source in the viewport": (
        "Писать результат в исходный меш. Выключено — создаётся копия, исходник скрывается во вьюпорте"
    ),
    "Hide source": "Скрыть исходник",
    "Hide the original object in the viewport when the result is a copy": (
        "Скрыть исходный объект во вьюпорте, когда результат записан в копию"
    ),
    "Delete unseen triangles": "Удалить невидимые треугольники",
    "On every selected mesh, cast rays in every direction from each point, delete triangles no ray saw, then delete vertices that belong to no face": (
        "На каждом выделенном меше пускает лучи во всех направлениях, удаляет треугольники, которые ни один луч не увидел, затем удаляет вершины без граней"
    ),
    "Rays from player points in every direction": "Лучи из точек игрока во всех направлениях",
    "Points: {count}. Grid rays per point: {rays}": "Точек: {count}. Лучей сетки на точку: {rays}",
    'The mesh is linked from a library. Turn off "Edit original object" or make the mesh local': (
        "Меш связан из библиотеки. Снимите «Менять исходный объект» или сделайте меш локальным"
    ),
    "Switch to Object Mode": "Перейдите в объектный режим",
    "No viewpoints. Assign a collection of empties, or select them together with the meshes": (
        "Нет точек обзора. Назначьте коллекцию пустышек или выделите их вместе с мешами"
    ),
    "Processed {done} of {objects} meshes. Kept {kept} of {total} triangles, removed unused vertices: {removed}. Rays: {rays}. {seconds:.1f} s.": (
        "Обработано {done} из {objects} мешей. Осталось {kept} из {total} треугольников, удалено вершин без граней: {removed}. Лучей: {rays}. {seconds:.1f} с."
    ),
    "Add at least one viewpoint": "Нужна хотя бы одна точка обзора",
    "Triangulation left a non-triangle face": "После триангуляции осталась не треугольная грань",
    "Face indices after triangulation are not contiguous": (
        "Индексы граней после триангуляции не непрерывны"
    ),
}


def translation_dict() -> dict:
    """Register each string for RNA labels, operator labels, and interface text."""
    russian = {}
    for source, translated in MESSAGES_RU.items():
        for context in ("*", "Operator", None):
            russian[(context, source)] = translated
    return {"ru_RU": russian}
