from __future__ import annotations

import json
import os
import tempfile
import traceback
import zipfile
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, QThread, Qt, Signal, Slot, QStandardPaths, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFontMetrics, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
)

from .. import APP_NAME, __version__
from ..core.catalog import load_catalog
from ..core.jar_analyzer import analyze_jar, build_preview_spec, read_asset_bytes
from ..core.legacy1710_engine import run_conversion, run_conversion_preflight
from ..core.modpack_analyzer import analyze_modpack
from ..core.version_targets import TARGETS
from ..core.workspace_store import WorkspaceStore


class FunctionWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    log_line = Signal(str)

    def __init__(self, fn):
        super().__init__(); self.fn = fn

    @Slot()
    def run(self):
        try:
            result = self.fn(self.log_line.emit)
            self.finished.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())


def _muted(text: str) -> QLabel:
    x = QLabel(text); x.setObjectName("muted"); x.setWordWrap(True); return x


def _title(text: str, subtitle: str) -> QVBoxLayout:
    box = QVBoxLayout()
    t = QLabel(text); t.setObjectName("pageTitle")
    box.addWidget(t); box.addWidget(_muted(subtitle)); return box


def _application_storage_root() -> Path:
    """Resolve the user-visible persistent storage root under Documents."""
    documents = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    base = Path(documents) if documents else (Path.home() / "Documents")
    return base / APP_NAME


def _preview_images(jar_path: str, paths: list[str]) -> dict[str, QImage]:
    """Load linked texture images once, preserving alpha and animation frame 0."""
    loaded: dict[str, QImage] = {}
    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            names = set(zf.namelist())
            for path in paths[:16]:
                try:
                    raw = zf.read(path)
                except Exception:
                    continue
                image = QImage.fromData(raw)
                if image.isNull():
                    continue
                image = image.convertToFormat(QImage.Format.Format_RGBA8888)
                # Animated Minecraft block textures are vertical strips with a
                # sidecar .mcmeta. Preview the first frame rather than crushing
                # the whole animation into one face.
                if path + ".mcmeta" in names and image.width() > 0 and image.height() >= image.width():
                    frame = image.copy(0, 0, image.width(), image.width())
                    if not frame.isNull():
                        image = frame
                loaded[path] = image
    except Exception:
        pass
    return loaded


def _project_raw(point) -> tuple[float, float]:
    x, y, z = [float(v) for v in point]
    return (x - z, (x + z) * 0.48 - y)


def _fit_iso_projection(vertices: list[tuple[float, float, float]], size: int):
    if not vertices:
        vertices = [(0, 0, 0), (16, 16, 16)]
    raw = [_project_raw(v) for v in vertices]
    min_x = min(p[0] for p in raw); max_x = max(p[0] for p in raw)
    min_y = min(p[1] for p in raw); max_y = max(p[1] for p in raw)
    span_x = max(max_x - min_x, 1.0); span_y = max(max_y - min_y, 1.0)
    margin_x = 24.0
    top_margin = 18.0
    bottom_reserved = 36.0
    scale = min((size - margin_x * 2) / span_x, (size - top_margin - bottom_reserved) / span_y)
    scale = max(scale, 0.1)
    offset_x = size * 0.5 - ((min_x + max_x) * 0.5) * scale
    offset_y = top_margin - min_y * scale
    return offset_x, offset_y, scale


def _project_iso(point, transform) -> QPointF:
    rx, ry = _project_raw(point)
    ox, oy, scale = transform
    return QPointF(ox + rx * scale, oy + ry * scale)


def _texture_path_for_role(roles: dict[str, str], role: str) -> str:
    if role in roles:
        return roles[role]
    if role in {"east", "south", "north", "west"} and "side" in roles:
        return roles["side"]
    if role == "up" and "top" in roles:
        return roles["top"]
    if role == "down" and "bottom" in roles:
        return roles["bottom"]
    return roles.get("all", "")


def _affine_coefficients(src, dst):
    """Return an affine map from three source points to three destination points.

    The result follows Qt's QTransform constructor order:
    (m11, m12, m21, m22, dx, dy). Keeping the math separate makes the
    texture projection deterministic and avoids the triangle/mosaic artefacts
    from the old sampled micro-mesh renderer.
    """
    (x0,y0),(x1,y1),(x2,y2)=src
    (X0,Y0),(X1,Y1),(X2,Y2)=dst
    det=x0*(y1-y2)+x1*(y2-y0)+x2*(y0-y1)
    if abs(det) < 1e-9:
        return None
    a=(X0*(y1-y2)+X1*(y2-y0)+X2*(y0-y1))/det
    b=(X0*(x2-x1)+X1*(x0-x2)+X2*(x1-x0))/det
    c=(X0*(x1*y2-x2*y1)+X1*(x2*y0-x0*y2)+X2*(x0*y1-x1*y0))/det
    d=(Y0*(y1-y2)+Y1*(y2-y0)+Y2*(y0-y1))/det
    e=(Y0*(x2-x1)+Y1*(x0-x2)+Y2*(x1-x0))/det
    f=(Y0*(x1*y2-x2*y1)+Y1*(x2*y0-x0*y2)+Y2*(x0*y1-x1*y0))/det
    return (a,d,b,e,c,f)


def _draw_affine_image_triangle(
    painter: QPainter,
    points: list[QPointF],
    source_points: list[tuple[float,float]],
    image: QImage,
    opacity: float = 1.0,
) -> bool:
    if len(points) != 3 or len(source_points) != 3 or image.isNull():
        return False
    dst=[(p.x(),p.y()) for p in points]
    coeffs=_affine_coefficients(source_points,dst)
    if coeffs is None:
        return False
    transform=QTransform(*coeffs)
    painter.save()
    painter.setOpacity(opacity)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
    painter.setTransform(transform, False)
    clip=QPainterPath()
    clip.addPolygon(QPolygonF([QPointF(x,y) for x,y in source_points]))
    painter.setClipPath(clip)
    painter.drawImage(QPointF(0,0), image)
    painter.restore()
    return True


def _draw_textured_quad(
    painter: QPainter,
    points: list[QPointF],
    image: QImage | None,
    shade: int = 0,
    opacity: float = 1.0,
) -> None:
    if len(points) < 3:
        return
    polygon = QPolygonF(points)
    painter.save()
    painter.setOpacity(opacity)
    painted=False
    if image is not None and not image.isNull() and len(points) >= 4:
        w=max(1,image.width()); h=max(1,image.height())
        # Geometry vertices are ordered bottom-left, bottom-right, top-right,
        # top-left. Two affine triangles map the full pixel-art texture onto the
        # projected face without painting into an axis-aligned bounding box.
        src=[(0.0,float(h)),(float(w),float(h)),(float(w),0.0),(0.0,0.0)]
        painted |= _draw_affine_image_triangle(painter,[points[0],points[1],points[2]],[src[0],src[1],src[2]],image,opacity)
        painted |= _draw_affine_image_triangle(painter,[points[0],points[2],points[3]],[src[0],src[2],src[3]],image,opacity)
    elif image is not None and not image.isNull() and len(points) == 3:
        w=max(1,image.width()); h=max(1,image.height())
        painted = _draw_affine_image_triangle(
            painter, points[:3], [(0.0,float(h)),(float(w),float(h)),(0.0,0.0)], image, opacity
        )
    if not painted:
        painter.setBrush(QColor("#68798a"))
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(polygon)
    painter.restore()

    if shade:
        painter.save()
        overlay=QPainterPath(); overlay.addPolygon(polygon)
        painter.fillPath(overlay,QColor(0,0,0,max(0,min(180,shade))))
        painter.restore()
    painter.setOpacity(1.0)
    painter.setPen(QPen(QColor(90, 110, 130, 150), 1))
    painter.setBrush(Qt.NoBrush)
    painter.drawPolygon(polygon)

def _box_visible_faces(bounds) -> list[tuple[str, list[tuple[float, float, float]], int]]:
    x0, y0, z0, x1, y1, z1 = [float(v) for v in bounds]
    return [
        ("south", [(x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1)], 42),
        ("east",  [(x1,y0,z0),(x1,y0,z1),(x1,y1,z1),(x1,y1,z0)], 22),
        ("up",    [(x0,y1,z0),(x1,y1,z0),(x1,y1,z1),(x0,y1,z1)], 0),
    ]


def _draw_box(painter, bounds, transform, images, roles, role_override: str = "") -> None:
    for face_role, verts, shade in _box_visible_faces(bounds):
        texture_role = role_override or face_role
        path = _texture_path_for_role(roles, texture_role)
        image = images.get(path)
        _draw_textured_quad(painter, [_project_iso(v, transform) for v in verts], image, shade=shade)


def _draw_uv_triangle(painter: QPainter, pts, uvs, image: QImage) -> None:
    """Map one OBJ UV triangle directly with an affine image transform.

    Orthographic/isometric projection keeps each planar triangle affine, so a
    direct transform is both faster and much more faithful than sampling the
    texture into dozens of flat-colour micro-triangles.
    """
    if image is None or image.isNull() or len(pts) != 3 or len(uvs) != 3:
        _draw_textured_quad(painter,list(pts),image,shade=18)
        return
    w=max(1,image.width()); h=max(1,image.height())
    src=[(max(0.0,min(1.0,float(u)))*w, (1.0-max(0.0,min(1.0,float(v))))*h) for u,v in uvs]
    if not _draw_affine_image_triangle(painter,list(pts),src,image,1.0):
        _draw_textured_quad(painter,list(pts),image,shade=18)

def _preview_scene_vertices(kind: str, elements, obj_vertices) -> list[tuple[float,float,float]]:
    if kind == "obj" and obj_vertices:
        return [tuple(map(float, v[:3])) for v in obj_vertices]
    if kind == "door":
        return [(0,0,7),(16,32,9)]
    if kind == "campfire":
        return [(0,0,0),(16,18,16)]
    if kind in {"sign", "hanging_sign"}:
        return [(0,0,6),(16,18,10)]
    if kind == "elements" and elements:
        verts=[]
        for e in elements:
            if isinstance(e,dict) and isinstance(e.get("from"),list) and isinstance(e.get("to"),list):
                lo=e["from"]; hi=e["to"]
                if len(lo)>=3 and len(hi)>=3:
                    verts.extend([(float(lo[0]),float(lo[1]),float(lo[2])),(float(hi[0]),float(hi[1]),float(hi[2]))])
        if verts:
            return verts
    return [(0,0,0),(16,16,16)]


def _render_static_preview(jar_path: str, candidate, size: int = 280, preview_mode: str = "auto") -> tuple[QPixmap, str]:
    spec = build_preview_spec(jar_path, candidate)
    canvas = QPixmap(size, size)
    canvas.fill(QColor("#0b0f14"))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)

    requested_mode = preview_mode if preview_mode in {"auto", "model", "2d"} else "auto"
    effective_mode = str(spec.get("auto_preview_mode") or "model") if requested_mode == "auto" else requested_mode
    preview_2d_path = str(spec.get("preview_2d_path") or "")
    if effective_mode == "2d" and not preview_2d_path:
        effective_mode = "model"

    texture_paths = list(spec.get("render_texture_paths") or spec.get("texture_paths") or [])
    load_paths = list(texture_paths)
    if preview_2d_path and preview_2d_path not in load_paths:
        load_paths.append(preview_2d_path)
    images = _preview_images(jar_path, load_paths)
    roles = dict(spec.get("texture_roles") or {})
    kind = str(spec.get("kind") or "asset")
    elements = list(spec.get("elements") or [])
    obj_vertices = list(spec.get("vertices") or [])

    # OBJ coordinates are often in [-0.5, 0.5] or arbitrary author units.
    # Normalize only for framing; UVs remain untouched.
    normalized_obj = obj_vertices
    if kind == "obj" and obj_vertices:
        xs=[float(v[0]) for v in obj_vertices]; ys=[float(v[1]) for v in obj_vertices]; zs=[float(v[2]) for v in obj_vertices]
        span=max(max(xs)-min(xs),max(ys)-min(ys),max(zs)-min(zs),1e-6)
        normalized_obj=[
            ((float(v[0])-min(xs))/span*16, (float(v[1])-min(ys))/span*16, (float(v[2])-min(zs))/span*16)
            for v in obj_vertices
        ]

    scene_vertices = _preview_scene_vertices(kind, elements, normalized_obj)
    transform = _fit_iso_projection(scene_vertices, size)

    if effective_mode == "2d" and preview_2d_path:
        image = images.get(preview_2d_path)
        if image is not None and not image.isNull():
            # Preserve pixel art and aspect ratio; never stretch a rectangular
            # atlas/icon into a square. This is intentionally a truthful 2D
            # asset view, not invented block geometry.
            max_w = size - 72
            max_h = size - 104
            scale = min(max_w / max(1, image.width()), max_h / max(1, image.height()))
            target_w = max(1.0, image.width() * scale)
            target_h = max(1.0, image.height() * scale)
            target = QRectF((size-target_w)/2.0, 28.0 + (max_h-target_h)/2.0, target_w, target_h)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            painter.drawImage(target, image, QRectF(image.rect()))
        else:
            painter.setPen(QColor("#9aa8b7"))
            painter.drawText(canvas.rect(), Qt.AlignCenter, "2D icon/texture\nunavailable")
    elif kind in {"asset","runtime_unresolved"} and not images and not spec.get("model_path"):
        painter.setPen(QColor("#9aa8b7"))
        message = "Runtime renderer\nnot statically reconstructable" if kind == "runtime_unresolved" else "No packaged static model\nor texture linked"
        painter.drawText(canvas.rect(), Qt.AlignCenter, message)
    elif kind == "texture_card":
        path=roles.get("all") or (texture_paths[0] if texture_paths else "")
        image=images.get(path)
        if image is not None and not image.isNull():
            target=QRectF(54,48,size-108,size-116)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            painter.drawImage(target,image,QRectF(image.rect()))
        else:
            painter.setPen(QColor("#9aa8b7"))
            painter.drawText(canvas.rect(),Qt.AlignCenter,"Runtime renderer\nstatic icon unavailable")
    elif kind == "obj" and normalized_obj and spec.get("faces"):
        faces = list(spec.get("faces") or [])
        texcoords = list(spec.get("texcoords") or [])
        default_path = roles.get("all") or (texture_paths[0] if texture_paths else "")
        texture = images.get(default_path)
        draw_faces=[]
        for face in faces:
            vis = face.get("vertices") if isinstance(face,dict) else face
            if not isinstance(vis,list) or len(vis)<3:
                continue
            pts3=[normalized_obj[i] for i in vis if isinstance(i,int) and 0 <= i < len(normalized_obj)]
            if len(pts3)<3:
                continue
            depth=sum(p[0]+p[2]+p[1]*0.96 for p in pts3)/len(pts3)
            draw_faces.append((depth,face,pts3))
        for _depth,face,pts3 in sorted(draw_faces,key=lambda x:x[0]):
            vis=face.get("vertices",[]); uis=face.get("uvs",[])
            for i in range(1,len(pts3)-1):
                tri3=[pts3[0],pts3[i],pts3[i+1]]
                tri2=[_project_iso(v,transform) for v in tri3]
                uv_ok = texture is not None and len(uis)==len(vis)
                tri_uv=[]
                if uv_ok:
                    for pos in (0,i,i+1):
                        ui=uis[pos]
                        if not isinstance(ui,int) or not (0 <= ui < len(texcoords)):
                            uv_ok=False; break
                        uv=texcoords[ui]
                        tri_uv.append((float(uv[0]),float(uv[1])))
                if uv_ok:
                    _draw_uv_triangle(painter,tri2,tri_uv,texture)
                else:
                    _draw_textured_quad(painter,tri2,texture,shade=18)
        painter.setPen(QPen(QColor(100,120,140,120),1))
    elif kind == "cross":
        path=_texture_path_for_role(roles,"all"); image=images.get(path)
        for plane in (
            [(0,0,0),(16,0,16),(16,16,16),(0,16,0)],
            [(16,0,0),(0,0,16),(0,16,16),(16,16,0)],
        ):
            _draw_textured_quad(painter,[_project_iso(v,transform) for v in plane],image,opacity=0.96)
    elif kind == "door":
        bottom_path=roles.get("door_bottom") or roles.get("bottom") or roles.get("all","")
        top_path=roles.get("door_top") or roles.get("top") or roles.get("all","")
        bottom_roles={"all":bottom_path}; top_roles={"all":top_path}
        _draw_box(painter,(0,0,7,16,16,9),transform,images,bottom_roles,"all")
        _draw_box(painter,(0,16,7,16,32,9),transform,images,top_roles,"all")
    elif kind == "trapdoor":
        _draw_box(painter,(0,0,0,16,3,16),transform,images,roles)
    elif kind == "carpet":
        _draw_box(painter,(0,0,0,16,1,16),transform,images,roles)
    elif kind == "campfire":
        registry_token = str(spec.get("registry") or "").lower()
        is_unlit = "_base" in registry_token or "unlit" in registry_token
        log_path=(roles.get("log") if is_unlit else roles.get("log_lit")) or roles.get("log") or roles.get("all","")
        fire_path="" if is_unlit else roles.get("fire","")
        log_roles={"all":log_path}
        for bounds in ((1,0,3,15,4,6),(1,0,10,15,4,13),(3,3,1,6,7,15),(10,3,1,13,7,15)):
            _draw_box(painter,bounds,transform,images,log_roles,"all")
        fire=images.get(fire_path)
        if fire is not None:
            for plane in (
                [(3,5,3),(13,5,13),(13,18,13),(3,18,3)],
                [(13,5,3),(3,5,13),(3,18,13),(13,18,3)],
            ):
                _draw_textured_quad(painter,[_project_iso(v,transform) for v in plane],fire,opacity=0.96)
    elif kind == "lantern":
        _draw_box(painter,(4,1,4,12,10,12),transform,images,roles)
        _draw_box(painter,(6,10,6,10,13,10),transform,images,roles)
        _draw_box(painter,(6,13,7,10,16,9),transform,images,roles)
    elif kind in {"sign","hanging_sign"}:
        _draw_box(painter,(2,7,7,14,16,9),transform,images,roles)
        if kind == "sign":
            _draw_box(painter,(7,0,7,9,7,9),transform,images,roles)
        else:
            _draw_box(painter,(4,16,7,6,18,9),transform,images,roles)
            _draw_box(painter,(10,16,7,12,18,9),transform,images,roles)
    elif kind == "elements" and elements:
        bindings=dict(spec.get("texture_bindings") or {})
        for element in sorted(elements,key=lambda e: float((e.get("from") or [0,0,0])[1]) if isinstance(e,dict) else 0):
            if not isinstance(element,dict):
                continue
            lo=element.get("from"); hi=element.get("to")
            if not (isinstance(lo,list) and isinstance(hi,list) and len(lo)>=3 and len(hi)>=3):
                continue
            bounds=(lo[0],lo[1],lo[2],hi[0],hi[1],hi[2])
            faces=element.get("faces") if isinstance(element.get("faces"),dict) else {}
            for face_role,verts,shade in _box_visible_faces(bounds):
                json_face=faces.get(face_role) if isinstance(faces,dict) else None
                texture_path=""
                if isinstance(json_face,dict):
                    ref=str(json_face.get("texture") or "")
                    if ref.startswith("#"):
                        texture_path=bindings.get(ref[1:],"")
                if not texture_path:
                    texture_path=_texture_path_for_role(roles,face_role)
                _draw_textured_quad(painter,[_project_iso(v,transform) for v in verts],images.get(texture_path),shade=shade)
    else:
        if kind == "slab": elements2=[(0,0,0,16,8,16)]
        elif kind == "stairs": elements2=[(0,0,0,16,8,16),(0,8,8,16,16,16)]
        elif kind == "fence": elements2=[(6,0,6,10,16,10),(0,5,7,16,8,9),(0,11,7,16,14,9)]
        elif kind == "pane" or kind == "thin": elements2=[(7,0,0,9,16,16)]
        elif kind == "wall": elements2=[(5,0,5,11,16,11),(0,0,6,16,12,10)]
        else: elements2=[(0,0,0,16,16,16)]
        for bounds in sorted(elements2,key=lambda b:b[1]):
            _draw_box(painter,bounds,transform,images,roles)

    painter.setOpacity(1.0)
    painter.setPen(QColor("#d8dee9"))
    label = str(spec.get("registry") or "")
    if label:
        painter.drawText(10, size - 12, label[:54])
    painter.end()
    fidelity = str(spec.get("preview_fidelity") or "unknown")
    detail = str(spec.get("note") or "Static asset preview")
    detail += f"\nPreview mode: {effective_mode} (requested {requested_mode}); fidelity: {fidelity}"
    if effective_mode == "2d" and preview_2d_path:
        detail += f"\n2D source: {spec.get('preview_2d_kind') or 'texture'} ({spec.get('preview_2d_confidence') or 'unknown'} confidence)"
        detail += f"\n{preview_2d_path}"
    elif spec.get("model_path"):
        detail += f"\n{spec['model_path']}"
    if texture_paths:
        detail += f"\n{len(texture_paths)} render texture(s)"
    warnings = [str(x) for x in (spec.get("preview_warnings") or []) if str(x)]
    if warnings:
        detail += "\nWarnings: " + "; ".join(warnings)
    return canvas, detail



class _AdaptiveHeaderTable(QTableWidget):
    """QTableWidget with readable headers and width-aware automatic fitting.

    The fitter runs when the table itself changes width. Manual column changes
    are remembered as the user's preferred widths and are not immediately
    overwritten merely because a scrollbar or selection state changes.
    """

    def resizeEvent(self, event):
        previous_width = getattr(self, "_wg_last_outer_width", None)
        new_width = event.size().width()
        super().resizeEvent(event)
        self._wg_last_outer_width = new_width
        fitter = getattr(self, "_wg_fit_header_columns", None)
        if fitter is not None and (previous_width is None or previous_width != new_width):
            fitter()


_HEADER_TEXT_ALLOWANCE = 42
_HEADER_COMFORT_MARGIN = 14
_HEADER_ABSOLUTE_FLOOR = 72


def _configure_resizable_columns(
    table: QTableWidget,
    labels: tuple[str, ...],
) -> None:
    """Keep analyzer headings compact, readable, responsive, and user-adjustable.

    Each heading receives the same text-relative readable floor and comfort
    margin. The initial/automatic layout fits those preferred widths into the
    viewport; when the window narrows, columns contract only as far as their
    readable floors. User-resized widths are remembered and restored when room
    becomes available again instead of being overwritten by incidental viewport
    changes.
    """
    table.setHorizontalHeaderLabels(list(labels))
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)

    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.Interactive)
    header.setStretchLastSection(False)
    header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    metrics = QFontMetrics(header.font())
    minimums = tuple(
        max(_HEADER_ABSOLUTE_FLOOR, metrics.horizontalAdvance(label) + _HEADER_TEXT_ALLOWANCE)
        for label in labels
    )
    preferreds = tuple(width + _HEADER_COMFORT_MARGIN for width in minimums)

    header.setMinimumSectionSize(min(minimums))
    table._wg_header_minimums = minimums
    table._wg_header_preferreds = preferreds
    table._wg_header_desireds = list(preferreds)
    clamp_guard = {"active": False}
    fit_guard = {"active": False}

    def keep_readable(index: int, _old_size: int, new_size: int) -> None:
        if fit_guard["active"] or clamp_guard["active"] or index >= len(minimums):
            return
        if new_size < minimums[index]:
            clamp_guard["active"] = True
            try:
                table._wg_header_desireds[index] = minimums[index]
                header.resizeSection(index, minimums[index])
            finally:
                clamp_guard["active"] = False
            return
        table._wg_header_desireds[index] = new_size

    def fit_columns_to_view() -> None:
        if fit_guard["active"]:
            return
        fit_guard["active"] = True
        try:
            available = max(0, table.viewport().width() - 2)
            desireds = tuple(max(minimum, desired) for minimum, desired in zip(minimums, table._wg_header_desireds))
            minimum_total = sum(minimums)
            desired_total = sum(desireds)

            if available <= minimum_total:
                widths = minimums
            elif available >= desired_total:
                widths = desireds
            else:
                # Shrink each column by the same fraction of its available
                # comfort/extra width, preserving relative user choices while
                # never crossing a heading's readable floor.
                shrinkable = max(1, desired_total - minimum_total)
                keep_fraction = (available - minimum_total) / shrinkable
                widths = tuple(
                    minimum + round((desired - minimum) * keep_fraction)
                    for minimum, desired in zip(minimums, desireds)
                )

            for index, width in enumerate(widths):
                header.resizeSection(index, width)
        finally:
            fit_guard["active"] = False

    header.sectionResized.connect(keep_readable)
    table._wg_fit_header_columns = fit_columns_to_view
    fit_columns_to_view()

def _path_row(parent, label: str, mode: str, target: QLineEdit, file_filter: str = "All files (*)"):
    wrap = QWidget(parent); lay = QHBoxLayout(wrap); lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(target, 1)
    btn = QPushButton("Browse…")
    def browse():
        if mode == "file":
            p, _ = QFileDialog.getOpenFileName(parent, label, target.text() or str(Path.home()), file_filter)
        elif mode == "save":
            p, _ = QFileDialog.getSaveFileName(parent, label, target.text() or str(Path.home()), file_filter)
        else:
            p = QFileDialog.getExistingDirectory(parent, label, target.text() or str(Path.home()))
        if p: target.setText(p)
    btn.clicked.connect(browse); lay.addWidget(btn)
    return wrap


class DashboardTab(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self); root.addLayout(_title(
            "Map Backporter Studio",
            "A reusable desktop workspace for inspecting mod blocks and backporting modern Java Edition maps into older modded worlds."
        ))
        cards = QGridLayout()
        items = [
            ("Map Backporter", "Convert modern Anvil regions into a validated older target format. The 1.7.10 Forge/HBM backend is available now."),
            ("Mod / JAR Analyzer", "Inspect a mod JAR's block textures, blockstate/model assets, metadata and likely registry names without launching Minecraft."),
            ("Modpack Analyzer", "Inspect local modpack instances or ZIP exports and build a reusable target-block catalog from the JARs actually present."),
            ("Catalog Workspace", "Combine target catalogs and control which mod namespaces are eligible for reviewed safe Backporter mapping rules."),
        ]
        for i, (name, desc) in enumerate(items):
            card = QFrame(); card.setObjectName("card"); l = QVBoxLayout(card)
            h = QLabel(name); h.setObjectName("sectionTitle"); l.addWidget(h); l.addWidget(_muted(desc)); l.addStretch()
            cards.addWidget(card, i // 2, i % 2)
        root.addLayout(cards)
        grp = QGroupBox("Target support")
        gl = QGridLayout(grp)
        gl.addWidget(QLabel("Version"), 0, 0); gl.addWidget(QLabel("Status"), 0, 1); gl.addWidget(QLabel("Notes"), 0, 2)
        for r, t in enumerate(TARGETS, 1):
            gl.addWidget(QLabel(t.version), r, 0); gl.addWidget(QLabel(t.status), r, 1); gl.addWidget(_muted(t.notes), r, 2)
        gl.setColumnStretch(2, 1); root.addWidget(grp); root.addStretch()


class AsyncTab(QWidget):
    def __init__(self):
        super().__init__(); self._thread = None; self._worker = None
        self._done_cb = None; self._error_cb = None; self._log_cb = None

    def launch(self, fn, on_done, on_error=None, log=None):
        if self._thread is not None:
            return
        self._done_cb, self._error_cb, self._log_cb = on_done, on_error, log
        self._thread = QThread(self); self._worker = FunctionWorker(fn); self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        # These receivers are methods on this QWidget, so Qt queues them back to the GUI thread.
        self._worker.log_line.connect(self._dispatch_log)
        self._worker.finished.connect(self._dispatch_done)
        self._worker.failed.connect(self._dispatch_failed)
        self._thread.start()

    @Slot(str)
    def _dispatch_log(self, line):
        if self._log_cb: self._log_cb(line)

    @Slot(object)
    def _dispatch_done(self, result):
        try:
            if self._done_cb: self._done_cb(result)
        finally:
            self._cleanup_thread()

    @Slot(str)
    def _dispatch_failed(self, tb):
        try:
            if self._error_cb: self._error_cb(tb)
            else: QMessageBox.critical(self, "Operation failed", tb)
        finally:
            self._cleanup_thread()

    def _cleanup_thread(self):
        if self._thread:
            self._thread.quit(); self._thread.wait(1500); self._thread.deleteLater()
        if self._worker: self._worker.deleteLater()
        self._thread = None; self._worker = None
        self._done_cb = None; self._error_cb = None; self._log_cb = None


class BackportTab(AsyncTab):
    def __init__(self, catalog_provider=None):
        super().__init__()
        self._catalog_provider = catalog_provider
        self._preflight_result = None
        self._preflight_token = None

        root = QVBoxLayout(self); root.addLayout(_title(
            "Map Backporter",
            "Preflight the modern source against the exact target/template world and active Catalog Workspace before creating an output world."
        ))

        scroll = QScrollArea(self)
        scroll.setObjectName("backportScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setObjectName("backportScrollViewport")
        scroll_body = QWidget(scroll)
        scroll_body.setObjectName("backportScrollBody")
        body = QVBoxLayout(scroll_body)
        body.setContentsMargins(0, 0, 0, 0)

        form_group = QGroupBox("Conversion job")
        form_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        form_group.setMinimumHeight(195)
        form = QFormLayout(form_group)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.source = QLineEdit()
        self.source.setPlaceholderText("Modern world folder, region folder, region ZIP, or .mca")
        src_wrap = QWidget(self)
        src_l = QHBoxLayout(src_wrap)
        src_l.setContentsMargins(0, 0, 0, 0)
        src_l.addWidget(self.source, 1)
        src_file = QPushButton("File / ZIP…")
        src_folder = QPushButton("Folder…")

        def choose_source_file():
            p, _ = QFileDialog.getOpenFileName(
                self,
                "Select modern map/region file",
                self.source.text() or str(Path.home()),
                "Minecraft map data (*.zip *.mca);;All files (*)",
            )
            if p:
                self.source.setText(p)

        def choose_source_folder():
            p = QFileDialog.getExistingDirectory(
                self,
                "Select modern world/region folder",
                self.source.text() or str(Path.home()),
            )
            if p:
                self.source.setText(p)

        src_file.clicked.connect(choose_source_file)
        src_folder.clicked.connect(choose_source_folder)
        src_l.addWidget(src_file)
        src_l.addWidget(src_folder)
        form.addRow("Source map", src_wrap)

        self.version = QComboBox()
        self.version.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.version.setMinimumWidth(220)
        self.version.setMaximumWidth(320)
        for t in TARGETS:
            self.version.addItem(f"{t.version} — {t.status}", t)
        form.addRow("Target version", self.version)

        self.target_status = _muted("")
        self.target_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        form.addRow("Backend", self.target_status)

        self.template = QLineEdit()
        self.template.setPlaceholderText("Saved target world opened once with the destination modpack")
        form.addRow("Template world", _path_row(self, "Select target/template world", "dir", self.template))

        self.output = QLineEdit()
        self.output.setPlaceholderText("New or empty output world folder")
        form.addRow("Output world", _path_row(self, "Select empty output folder", "dir", self.output))
        body.addWidget(form_group)

        opts = QGroupBox("Surface / compatibility options")
        opts.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        opts.setMinimumHeight(205)
        og = QGridLayout(opts)
        og.setColumnStretch(1, 1)
        og.setHorizontalSpacing(12)
        og.setVerticalSpacing(8)

        self.hbm = QCheckBox("Use enabled catalog/backport block replacements")
        self.hbm.setChecked(True)
        self.hbm.setToolTip(
            "Prefer exact registered blocks from enabled backport-provider catalogs, then use reviewed architectural/decorative rules "
            "from enabled mod namespaces. The target world's actual registry remains authoritative."
        )
        self.catalog_status = _muted("")
        self.yoff = QSpinBox()
        self.yoff.setRange(-192, 192)
        self.yoff.setSingleStep(16)
        self.yoff.setValue(0)
        self.yoff.setMaximumWidth(180)
        self.strip = QSpinBox()
        self.strip.setRange(0, 255)
        self.strip.setValue(0)
        self.strip.setMaximumWidth(180)
        self.recommended_btn = QPushButton("Use recommended")
        self.recommended_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.recommended_status = _muted("")

        og.addWidget(self.hbm, 0, 0, 1, 2)
        og.addWidget(self.catalog_status, 1, 0, 1, 2)
        og.addWidget(QLabel("Vertical offset"), 2, 0)
        og.addWidget(self.yoff, 2, 1)
        og.addWidget(QLabel("Strip/fill below target Y"), 3, 0)
        og.addWidget(self.strip, 3, 1)
        recommended_row = QHBoxLayout()
        recommended_row.addWidget(self.recommended_btn)
        recommended_row.addWidget(self.recommended_status, 1)
        og.addLayout(recommended_row, 4, 0, 1, 2)
        og.addWidget(
            _muted(
                "For 1.7.10, source blocks below Y=0 or above Y=255 cannot be represented. "
                "The recommended 0 / 0 profile preserves normal RTG and sea-level alignment."
            ),
            5, 0, 1, 2,
        )
        body.addWidget(opts)

        status_group = QGroupBox("Conversion preflight")
        status_layout = QVBoxLayout(status_group)
        self.preflight_status = _muted(
            "Required before Convert map. Preflight is read-only and validates the source, target registry, active catalogs and mapping settings."
        )
        status_layout.addWidget(self.preflight_status)
        body.addWidget(status_group)

        buttons = QHBoxLayout()
        self.scan_btn = QPushButton("Preflight conversion")
        self.convert_btn = QPushButton("Convert map")
        self.convert_btn.setObjectName("primary")
        buttons.addWidget(self.scan_btn)
        buttons.addStretch()
        buttons.addWidget(self.convert_btn)
        body.addLayout(buttons)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        body.addWidget(self.progress)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(150)
        body.addWidget(self.log, 1)

        scroll_body.setMinimumHeight(700)
        scroll.setWidget(scroll_body)
        root.addWidget(scroll, 1)

        self.scan_btn.clicked.connect(self.scan_source)
        self.convert_btn.clicked.connect(self.convert)
        self.recommended_btn.clicked.connect(self._apply_recommended)
        self.version.currentIndexChanged.connect(self._target_changed)
        self.source.textChanged.connect(self._invalidate_preflight)
        self.template.textChanged.connect(self._invalidate_preflight)
        self.yoff.valueChanged.connect(self._invalidate_preflight)
        self.strip.valueChanged.connect(self._invalidate_preflight)
        self.hbm.stateChanged.connect(self._invalidate_preflight)

        self._target_changed()
        self._refresh_catalog_status()
        self._update_action_state()

    def _catalog_snapshot(self) -> dict:
        if self._catalog_provider is None:
            return {
                "enabled_catalogs": [],
                "enabled_mod_ids": [],
                "registry_hints": [],
                "candidate_count": 0,
                "block_entity_count": 0,
                "backport_providers": [],
            }
        try:
            snapshot = self._catalog_provider() or {}
        except Exception:
            snapshot = {}
        providers = [item for item in (snapshot.get("backport_providers") or []) if isinstance(item, dict)]
        return {
            "enabled_catalogs": list(snapshot.get("enabled_catalogs") or []),
            "enabled_mod_ids": sorted({str(x).lower() for x in (snapshot.get("enabled_mod_ids") or []) if str(x).strip()}),
            "registry_hints": sorted({str(x).lower() for x in (snapshot.get("registry_hints") or []) if str(x).strip()}),
            "candidate_count": int(snapshot.get("candidate_count") or 0),
            "block_entity_count": int(snapshot.get("block_entity_count") or 0),
            "backport_providers": providers,
        }

    def _current_input_token(self) -> str:
        payload = {
            "source": self.source.text().strip(),
            "template": self.template.text().strip(),
            "target": getattr(self.version.currentData(), "version", ""),
            "allow_safe_mod_replacements": bool(self.hbm.isChecked()),
            "vertical_offset": int(self.yoff.value()),
            "strip_below_y": int(self.strip.value()),
            "catalog_snapshot": self._catalog_snapshot(),
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    def _preflight_valid(self) -> bool:
        return bool(
            isinstance(self._preflight_result, dict)
            and self._preflight_result.get("ready")
            and self._preflight_token == self._current_input_token()
        )

    def _invalidate_preflight(self, *_args):
        self._preflight_result = None
        self._preflight_token = None
        if hasattr(self, "preflight_status"):
            self.preflight_status.setText(
                "Preflight required — source, template, mapping settings or enabled catalogs changed."
            )
        self._update_action_state()

    def catalog_workspace_changed(self):
        self._refresh_catalog_status()
        self._invalidate_preflight()

    def _refresh_catalog_status(self):
        snapshot = self._catalog_snapshot()
        labels = snapshot["enabled_catalogs"]
        mods = snapshot["enabled_mod_ids"]
        if not labels:
            text = "Catalog Workspace: 0 enabled catalogs • safe mod rules will fall back to vanilla targets."
        else:
            provider_count = len(snapshot.get("backport_providers") or [])
            text = (
                f"Catalog Workspace: {len(labels):,} enabled catalog(s) • "
                f"{snapshot['candidate_count']:,} active blocks • {snapshot.get('block_entity_count', 0):,} block entities • "
                f"{provider_count:,} backport provider(s) • namespaces: {', '.join(mods) or 'none'}"
            )
        if hasattr(self, "catalog_status"):
            self.catalog_status.setText(text)

    def _target_changed(self, *_args):
        t = self.version.currentData()
        self.target_status.setText(f"{t.status}: {t.notes}")
        self.hbm.setEnabled(t.version == "1.7.10")
        if t.recommended_y_offset is not None and t.recommended_strip_below_y is not None:
            self.recommended_btn.setEnabled(True)
            self.recommended_status.setText(
                f"{t.version} recommended surface profile: vertical offset {t.recommended_y_offset}, "
                f"strip/fill below Y {t.recommended_strip_below_y}."
            )
        else:
            self.recommended_btn.setEnabled(False)
            self.recommended_status.setText("No automatic recommendation is defined for this planned target yet.")
        self._invalidate_preflight()

    def _apply_recommended(self):
        t = self.version.currentData()
        if t.recommended_y_offset is None or t.recommended_strip_below_y is None:
            return
        self.yoff.setValue(int(t.recommended_y_offset))
        self.strip.setValue(int(t.recommended_strip_below_y))
        self._invalidate_preflight()

    def _update_action_state(self):
        busy = self._thread is not None
        backend_ready = self.version.currentData().backend is not None
        self.scan_btn.setEnabled((not busy) and backend_ready)
        self.convert_btn.setEnabled((not busy) and backend_ready and self._preflight_valid())

    def _set_busy(self, busy: bool):
        self.scan_btn.setEnabled(not busy)
        self.convert_btn.setEnabled((not busy) and self.version.currentData().backend is not None and self._preflight_valid())
        self.progress.setRange(0, 0 if busy else 1)
        if not busy:
            self.progress.setValue(1)

    def _log(self, s):
        self.log.appendPlainText(str(s))

    def scan_source(self):
        t = self.version.currentData()
        if t.backend != "legacy1710":
            QMessageBox.information(self, "Backend not implemented", f"{t.version} is scaffolded but deliberately not enabled yet.")
            return

        source = self.source.text().strip()
        template = self.template.text().strip()
        if not source or not template:
            QMessageBox.warning(
                self,
                "Missing preflight paths",
                "Select both the modern source map and the target/template world before running conversion preflight.",
            )
            return
        if self.yoff.value() % 16:
            QMessageBox.warning(self, "Invalid offset", "The 1.7.10 vertical offset must be a multiple of 16.")
            return

        request_token = self._current_input_token()
        snapshot = self._catalog_snapshot()
        allow_safe = self.hbm.isChecked()
        yoff = self.yoff.value()
        strip = self.strip.value()

        self.log.clear()
        self._preflight_result = None
        self._preflight_token = None
        self.preflight_status.setText("Preflight running… no output world will be created.")
        self._set_busy(True)

        def work(log):
            return run_conversion_preflight(
                source,
                template,
                allow_safe,
                yoff,
                strip,
                catalog_snapshot=snapshot,
                log=log,
            )

        def done(rep):
            if request_token != self._current_input_token():
                self._preflight_result = None
                self._preflight_token = None
                self._set_busy(False)
                self.preflight_status.setText(
                    "Preflight finished, but the inputs changed while it was running. Run it again before conversion."
                )
                QMessageBox.warning(
                    self,
                    "Preflight became stale",
                    "The source, template, settings or Catalog Workspace changed while preflight was running. "
                    "The result was discarded and no output world was created.",
                )
                return

            self._preflight_result = rep
            self._preflight_token = request_token
            p = rep.get("preflight") or {}
            profile = rep.get("mapping_profile") or {}
            content = p.get("content_audit") or {}
            be_count = int(content.get("block_entities_total", 0) or 0)
            entity_total = content.get("entities_total")
            entity_text = "entity audit unavailable" if entity_total is None else f"{int(entity_total):,} entities"
            warning = " • content-loss manifest" if be_count or (entity_total not in (None, 0)) or content.get("entity_scan_status") == "unavailable" else ""
            target_registry = rep.get("target_registry") or {}
            provider_catalog_targets = int(target_registry.get("backport_provider_targets_catalog", profile.get("backport_provider_target_count", 0)) or 0)
            provider_registered_targets = int(target_registry.get("backport_provider_targets_registered", 0) or 0)
            provider_text = (
                f"{provider_registered_targets:,}/{provider_catalog_targets:,} provider targets registered"
                if provider_catalog_targets else "0 provider targets"
            )
            impact = p.get("mapping_quality_percent") or {}
            exact_pct = float(impact.get("exact", 0.0) or 0.0) + float(impact.get("backport_exact", 0.0) or 0.0)
            self.preflight_status.setText(
                f"READY • {rep.get('regions', 0):,} regions • {p.get('chunks', 0):,} chunks • "
                f"{p.get('unique_palette_states', 0):,} unique in-range palette states • "
                f"{exact_pct:.1f}% exact by placed blocks • {provider_text} • "
                f"{be_count:,} block entities • {entity_text} • "
                f"{len(profile.get('enabled_catalogs') or []):,} enabled catalog(s){warning}"
            )
            self._set_busy(False)
            summary = {
                "ready": rep.get("ready"),
                "regions": rep.get("regions"),
                "target_registry": rep.get("target_registry"),
                "mapping_profile": rep.get("mapping_profile"),
                "preflight": rep.get("preflight"),
            }
            self._log("\nPreflight summary:\n" + json.dumps(summary, indent=2, ensure_ascii=False)[:16000])
            content = p.get("content_audit") or {}
            be_count = int(content.get("block_entities_total", 0) or 0)
            entity_total = content.get("entities_total")
            if entity_total is None:
                content_line = (
                    f"{be_count:,} block entity record(s) were found. Entity-region data could not be audited from this input form. "
                    "The current backend reports these records but does not translate entities/block entities yet."
                )
            else:
                content_line = (
                    f"{be_count:,} block entity record(s) and {int(entity_total):,} entity record(s) were found. "
                    "The current backend reports them in the loss manifest but does not translate them yet."
                )
            unavailable = p.get("unavailable_backport_candidates") or []
            if unavailable:
                unavailable_line = (
                    f" {len(unavailable):,} high-impact source block type(s) have catalog backport candidates that are not "
                    "registered in the selected target/template world; the log identifies those provider/config gaps. "
                )
            else:
                unavailable_line = ""
            QMessageBox.information(
                self,
                "Conversion preflight ready",
                f"Validated {p.get('chunks', 0):,} source chunks against the target registry and active mapping profile. "
                f"Mapping impact is {exact_pct:.2f}% exact/backport-exact across placed in-range non-air blocks; "
                f"{provider_registered_targets:,}/{provider_catalog_targets:,} catalog backport target(s) are actually registered in the selected template. "
                f"The log lists the highest-impact non-exact mappings.{unavailable_line}{content_line} "
                "Output chunks will request a target-side relight. No output world was created. Convert map is now enabled.",
            )

        def err(tb):
            self._preflight_result = None
            self._preflight_token = None
            self._set_busy(False)
            self.preflight_status.setText("Preflight FAILED — no output world was created.")
            self._log(tb)
            QMessageBox.critical(self, "Conversion preflight failed", tb)

        self.launch(work, done, err, self._log)

    def convert(self):
        t = self.version.currentData()
        if t.backend != "legacy1710":
            QMessageBox.information(self, "Backend not implemented", f"{t.version} is scaffolded but deliberately not enabled yet.")
            return

        source = self.source.text().strip()
        template = self.template.text().strip()
        output = self.output.text().strip()
        if not source or not template or not output:
            QMessageBox.warning(self, "Missing paths", "Select the source map, target/template world and output folder.")
            return
        if not self._preflight_valid():
            QMessageBox.warning(
                self,
                "Preflight required",
                "Run Preflight conversion successfully after the latest source, template, settings and Catalog Workspace changes before converting.",
            )
            return
        if self.yoff.value() % 16:
            QMessageBox.warning(self, "Invalid offset", "The 1.7.10 vertical offset must be a multiple of 16.")
            return
        if Path(output).exists() and any(Path(output).iterdir()):
            QMessageBox.warning(
                self,
                "Output is not empty",
                "Choose a new or empty output folder. The converter intentionally refuses to overwrite an existing world.",
            )
            return

        snapshot = self._catalog_snapshot()
        verified = dict(self._preflight_result)
        allow_safe = self.hbm.isChecked()
        yoff = self.yoff.value()
        strip = self.strip.value()

        self.log.clear()
        self._set_busy(True)

        def work(log):
            return run_conversion(
                source,
                template,
                output,
                allow_safe,
                yoff,
                strip,
                log,
                catalog_snapshot=snapshot,
                verified_preflight=verified,
            )

        def done(rep):
            self._set_busy(False)
            self._log(
                "\nFinished.\n" + json.dumps(
                    {
                        k: rep.get(k)
                        for k in (
                            "output_promoted",
                            "regions_converted",
                            "regions_verified",
                            "chunks_converted",
                            "chunks_verified",
                            "chunks_failed",
                            "chunks_cropped_above_255",
                            "chunks_cropped_below_0",
                            "block_entities_omitted",
                            "entities_omitted",
                            "preflight_reused",
                        )
                    },
                    indent=2,
                )
            )
            failed = int(rep.get("chunks_failed", 0) or 0)
            promoted = bool(rep.get("output_promoted"))
            if failed or not promoted:
                failure_report = rep.get("failure_report_txt") or "the external failure report"
                QMessageBox.warning(
                    self,
                    "Backport not promoted",
                    f"The staged conversion was not promoted to the requested output world. "
                    f"{failed:,} chunk/verification failure(s) were recorded. "
                    f"The requested output remains absent; review {failure_report}.",
                )
            else:
                be_count = int(rep.get("block_entities_omitted", 0) or 0)
                entity_total = rep.get("entities_omitted")
                if entity_total is None:
                    loss_text = f"{be_count:,} block entity record(s) were omitted; source entities could not be quantified from the selected input form."
                else:
                    loss_text = f"{be_count:,} block entity record(s) and {int(entity_total):,} entity record(s) were omitted and listed in the report."
                QMessageBox.information(
                    self,
                    "Backport complete and verified",
                    "Conversion finished with zero chunk failures, every written region passed round-trip structural verification, "
                    "and the staged world was promoted to the requested output. "
                    f"{loss_text} The legacy chunks request target-side relighting. "
                    "Review WG_BACKPORT_REPORT.txt before opening the world in Minecraft.",
                )

        def err(tb):
            self._set_busy(False)
            self._log(tb)
            QMessageBox.critical(self, "Conversion failed", tb)

        self.launch(work, done, err, self._log)


class JarAnalyzerTab(AsyncTab):
    addCatalogRequested = Signal(object)

    def __init__(self, store: WorkspaceStore | None = None):
        super().__init__()
        self.catalog = None
        self._store = store
        root = QVBoxLayout(self)
        root.addLayout(_title(
            "Mod / JAR Analyzer",
            "Statically discover blocks and block/tile entities across legacy and modern mod JAR layouts, then preview packaged geometry without executing the mod."
        ))
        top = QHBoxLayout()
        self.jar = QLineEdit()
        self.jar.setPlaceholderText("Select a mod .jar")
        top.addWidget(self.jar, 1)
        browse = QPushButton("Browse…")
        analyze = QPushButton("Analyze JAR")
        analyze.setObjectName("primary")
        top.addWidget(browse)
        top.addWidget(analyze)
        root.addLayout(top)
        self.summary = _muted("No JAR analyzed yet.")
        root.addWidget(self.summary)

        splitter = QSplitter(Qt.Horizontal)
        self.table = _AdaptiveHeaderTable(0, 7)
        jar_labels = ("Kind", "Registry / class", "Display name", "Confidence", "Evidence", "Textures", "Models")
        _configure_resizable_columns(self.table, jar_labels)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSortingEnabled(True)
        splitter.addWidget(self.table)

        side = QWidget()
        sl = QVBoxLayout(side)
        preview_controls = QHBoxLayout()
        preview_controls.addWidget(QLabel("Preview:"))
        self.preview_mode = QComboBox()
        self.preview_mode.addItem("Auto (reliable)", "auto")
        self.preview_mode.addItem("3D model (experimental)", "model")
        self.preview_mode.addItem("2D icon / texture", "2d")
        self.preview_mode.setToolTip(
            "Auto prefers faithful static geometry and falls back to a 2D icon/texture when a legacy model has ambiguous materials. "
            "This affects only the analyzer preview, never conversion mapping."
        )
        preview_controls.addWidget(self.preview_mode, 1)
        sl.addLayout(preview_controls)
        self.preview = QLabel("Select a block or block entity to preview packaged static geometry.")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(250, 250)
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet("background:#0b0f14;border:1px solid #303b48;border-radius:8px;")
        sl.addWidget(self.preview, 1)
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(170)
        sl.addWidget(self.notes)
        splitter.addWidget(side)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([820, 340])
        root.addWidget(splitter, 1)

        bottom = QHBoxLayout()
        self.add_to_workspace = QPushButton("Add to Catalog Workspace")
        self.add_to_workspace.setEnabled(False)
        self.export = QPushButton("Export catalog JSON…")
        self.export.setEnabled(False)
        bottom.addStretch()
        bottom.addWidget(self.add_to_workspace)
        bottom.addWidget(self.export)
        root.addLayout(bottom)

        browse.clicked.connect(self._browse)
        analyze.clicked.connect(self._analyze)
        self.add_to_workspace.clicked.connect(self._add_to_workspace)
        self.export.clicked.connect(self._export)
        self.table.itemSelectionChanged.connect(self._preview_selected)
        self.preview_mode.currentIndexChanged.connect(self._preview_selected)

    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Select mod JAR", self.jar.text() or str(Path.home()), "Java archives (*.jar);;All files (*)"
        )
        if p:
            self.jar.setText(p)

    def _analyze(self):
        p = self.jar.text().strip()
        if not p:
            QMessageBox.warning(self, "Missing JAR", "Select a mod JAR first.")
            return
        self.summary.setText("Analyzing…")
        self.table.setRowCount(0)
        self.preview.setPixmap(QPixmap())
        self.preview.setText("Analyzing…")

        def work(log):
            return analyze_jar(p, log=log)

        def done(cat):
            self.catalog = cat
            self._show_catalog(cat)

        def err(tb):
            self.summary.setText("Analysis failed.")
            QMessageBox.critical(self, "JAR analysis failed", tb)

        self.launch(work, done, err)

    def _show_catalog(self, cat):
        stats = cat.analysis_stats or {}
        model_count = int(stats.get("packaged_model_assets", 0) or 0)
        provider = str(cat.provider_role or "general").replace("_", " ").title()
        self.summary.setText(
            f"{cat.mod_name or Path(cat.source).name} • {cat.loader_hint} • {cat.mod_version or 'version unknown'} • "
            f"{len(cat.blocks):,} blocks • {len(cat.block_entities):,} block entities • {model_count:,} packaged models • {provider}"
        )

        rows: list[tuple[str, int, object]] = []
        rows.extend(("Block", i, value) for i, value in enumerate(cat.blocks))
        rows.extend(("Block entity", i, value) for i, value in enumerate(cat.block_entities))
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for r, (kind, source_index, asset) in enumerate(rows):
            identity = asset.registry_hint or getattr(asset, "class_name", "")
            values = [
                kind,
                identity,
                asset.display_name,
                asset.confidence,
                asset.evidence,
                str(len(asset.texture_paths)),
                str(len(asset.model_paths)),
            ]
            for c, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if c == 0:
                    item.setData(Qt.UserRole, ("block" if kind == "Block" else "block_entity", source_index))
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)

        notes = list(cat.notes)
        notes.insert(0, f"Provider role: {provider}. {cat.provider_reason or 'No special mapping authority assigned.'}")
        self.notes.setPlainText("\n".join(notes))
        self.add_to_workspace.setEnabled(True)
        self.export.setEnabled(True)
        self.preview.setPixmap(QPixmap())
        self.preview.setText("Select a block or block entity to preview packaged static geometry.")

    def _preview_selected(self):
        if not self.catalog:
            return
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        anchor = self.table.item(rows[0].row(), 0)
        token = anchor.data(Qt.UserRole) if anchor is not None else None
        if not isinstance(token, tuple) or len(token) != 2:
            return
        kind, source_index = token
        if not isinstance(source_index, int):
            return
        if kind == "block":
            if not (0 <= source_index < len(self.catalog.blocks)):
                return
            candidate = self.catalog.blocks[source_index]
        elif kind == "block_entity":
            if not (0 <= source_index < len(self.catalog.block_entities)):
                return
            candidate = self.catalog.block_entities[source_index]
        else:
            return

        try:
            mode = str(self.preview_mode.currentData() or "auto")
            pixmap, detail = _render_static_preview(self.jar.text().strip(), candidate, preview_mode=mode)
            self.preview.setText("")
            self.preview.setPixmap(pixmap)
            self.preview.setToolTip(detail)
        except Exception as exc:
            self.preview.setPixmap(QPixmap())
            self.preview.setText(f"Static preview unavailable:\n{exc}")

    def _add_to_workspace(self):
        if self.catalog:
            self.addCatalogRequested.emit(self.catalog.to_dict())

    def _export(self):
        if not self.catalog:
            return
        suggested = (self.catalog.mod_ids[0] if self.catalog.mod_ids else "mod") + "-block-catalog.json"
        start = Path(suggested)
        if self._store is not None:
            self._store.ensure_layout()
            start = self._store.catalogs_dir / suggested
        p, _ = QFileDialog.getSaveFileName(self, "Export block catalog", str(start), "JSON (*.json)")
        if p:
            self.catalog.save(p)


class ModpackAnalyzerTab(AsyncTab):
    addAnalysisRequested = Signal(object)

    def __init__(self, store: WorkspaceStore | None = None):
        super().__init__(); self.analysis = None; self._store = store
        root = QVBoxLayout(self); root.addLayout(_title(
            "Modpack Analyzer",
            "Inspect the mods physically present in an instance or export. CurseForge manifests are recognized; missing JARs remain explicitly unresolved."
        ))
        top = QHBoxLayout(); self.path = QLineEdit(); self.path.setPlaceholderText("Modpack instance folder or ZIP export")
        top.addWidget(self.path, 1); browse_folder = QPushButton("Folder…"); browse_zip = QPushButton("ZIP…"); run = QPushButton("Analyze modpack"); run.setObjectName("primary")
        top.addWidget(browse_folder); top.addWidget(browse_zip); top.addWidget(run); root.addLayout(top)
        self.summary = _muted("No modpack analyzed yet."); root.addWidget(self.summary)
        self.table = _AdaptiveHeaderTable(0, 8)
        modpack_labels = ("Mod", "Mod IDs", "Version", "Loader", "Blocks", "Block entities", "Role", "Source")
        _configure_resizable_columns(self.table, modpack_labels)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers); self.table.setSortingEnabled(True); root.addWidget(self.table, 1)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(170); root.addWidget(self.log)
        bottom = QHBoxLayout()
        self.add_to_workspace = QPushButton("Add catalogs to Workspace")
        self.add_to_workspace.setEnabled(False)
        self.export = QPushButton("Export combined analysis…")
        self.export.setEnabled(False)
        bottom.addStretch(); bottom.addWidget(self.add_to_workspace); bottom.addWidget(self.export); root.addLayout(bottom)
        browse_folder.clicked.connect(self._folder); browse_zip.clicked.connect(self._zip); run.clicked.connect(self._run)
        self.add_to_workspace.clicked.connect(self._add_to_workspace)
        self.export.clicked.connect(self._export)

    def _folder(self):
        p = QFileDialog.getExistingDirectory(self, "Select modpack instance", self.path.text() or str(Path.home()))
        if p: self.path.setText(p)
    def _zip(self):
        p, _ = QFileDialog.getOpenFileName(self, "Select modpack ZIP", self.path.text() or str(Path.home()), "ZIP archives (*.zip);;All files (*)")
        if p: self.path.setText(p)
    def _run(self):
        p = self.path.text().strip()
        if not p: QMessageBox.warning(self, "Missing modpack", "Select an instance folder or ZIP first."); return
        self.log.clear(); self.summary.setText("Analyzing…")
        def work(log): return analyze_modpack(p, log=log)
        def done(rep):
            self.analysis = rep; self.summary.setText(f"{rep.pack_name or Path(rep.source).name} • Minecraft {rep.minecraft_version or 'unknown'} • {rep.local_jars} local JARs • {rep.manifested_files} manifest entries")
            self.table.setSortingEnabled(False); self.table.setRowCount(len(rep.mods))
            for r, m in enumerate(rep.mods):
                vals = [m.mod_name or Path(m.source).name, ", ".join(m.mod_ids), m.version, m.loader_hint, str(m.block_candidates), str(m.block_entities), m.provider_role.replace("_", " "), m.source]
                for c, v in enumerate(vals): self.table.setItem(r, c, QTableWidgetItem(v))
            self.table.setSortingEnabled(True)
            if rep.notes: self.log.appendPlainText("\n".join(rep.notes))
            self.add_to_workspace.setEnabled(bool(rep.block_catalogs))
            self.export.setEnabled(True)
        def err(tb): self.summary.setText("Analysis failed."); self.log.appendPlainText(tb); QMessageBox.critical(self, "Modpack analysis failed", tb)
        self.launch(work, done, err, self.log.appendPlainText)
    def _add_to_workspace(self):
        if not self.analysis:
            return
        self.addAnalysisRequested.emit(self.analysis.to_dict())

    def _export(self):
        if not self.analysis: return
        start = Path("modpack-block-analysis.json")
        if self._store is not None:
            self._store.ensure_layout()
            start = self._store.catalogs_dir / start
        p, _ = QFileDialog.getSaveFileName(self, "Export modpack analysis", str(start), "JSON (*.json)")
        if p: self.analysis.save(p)


class CatalogTab(QWidget):
    workspaceChanged = Signal()
    storageMessage = Signal(str)

    def __init__(self, store: WorkspaceStore):
        super().__init__()
        self._store = store
        self._store.ensure_layout()
        self.sources: list[dict] = []
        self._source_serial = 0
        self._restoring_workspace = False

        root = QVBoxLayout(self); root.addLayout(_title(
            "Catalog Workspace",
            "Combine multiple catalogs, toggle target mods on/off, and define the active target pool used by Map Backporter's reviewed safe mapping rules."
        ))

        toolbar = QHBoxLayout()
        add = QPushButton("Add catalog(s)…")
        remove = QPushButton("Remove selected")
        clear = QPushButton("Clear all")
        save = QPushButton("Save workspace copy…")
        storage = QPushButton("Storage folder")
        self.search = QLineEdit(); self.search.setPlaceholderText("Search registry, display name, mod, candidate kind or evidence…")
        toolbar.addWidget(add); toolbar.addWidget(remove); toolbar.addWidget(clear); toolbar.addWidget(save); toolbar.addWidget(storage)
        toolbar.addSpacing(10); toolbar.addWidget(self.search, 1)
        root.addLayout(toolbar)

        self.summary = _muted("No catalogs loaded yet.")
        root.addWidget(self.summary)
        self.autosave_info = _muted(
            f"Automatically remembered in {self._store.default_workspace_path}"
        )
        root.addWidget(self.autosave_info)

        splitter = QSplitter(Qt.Horizontal)
        source_panel = QGroupBox("Loaded catalogs")
        source_layout = QVBoxLayout(source_panel)
        source_layout.addWidget(_muted(
            "Checked catalogs contribute blocks to the active workspace and enable their mod namespaces for reviewed Backporter mapping rules."
        ))
        self.source_list = QListWidget()
        self.source_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        source_layout.addWidget(self.source_list, 1)
        source_panel.setMinimumWidth(260)
        splitter.addWidget(source_panel)

        self.table = _AdaptiveHeaderTable(0, 7)
        catalog_labels = ("Kind", "Registry / class", "Display", "Mod", "Confidence", "Evidence", "Assets")
        _configure_resizable_columns(self.table, catalog_labels)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSortingEnabled(True)
        splitter.addWidget(self.table)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([300, 900])
        root.addWidget(splitter, 1)

        add.clicked.connect(self._load)
        remove.clicked.connect(self._remove_selected)
        clear.clicked.connect(self._clear)
        save.clicked.connect(self._save_workspace)
        storage.clicked.connect(self._open_storage_folder)
        self.search.textChanged.connect(self._refresh)
        self.source_list.itemChanged.connect(self._source_toggled)

        self._restore_default_workspace()

    def _catalog_label(self, catalog: dict, fallback: str = "Catalog") -> str:
        name = str(catalog.get("mod_name") or "").strip()
        mod_ids = catalog.get("mod_ids") or []
        mod_id = str(mod_ids[0]) if isinstance(mod_ids, list) and mod_ids else ""
        version = str(catalog.get("mod_version") or "").strip()
        base = name or mod_id or fallback
        if version:
            return f"{base} • {version}"
        return base

    def _catalog_identity(self, catalog: dict) -> str:
        source = str(catalog.get("source") or "")
        mod_ids = catalog.get("mod_ids") or []
        mod_id = str(mod_ids[0]) if isinstance(mod_ids, list) and mod_ids else ""
        version = str(catalog.get("mod_version") or "")
        return f"{source}\n{mod_id}\n{version}"

    def _workspace_payload(self) -> dict:
        return {
            "schema": 1,
            "kind": "catalog_workspace",
            "sources": [
                {
                    "enabled": bool(source["enabled"]),
                    "label": source["label"],
                    "catalog": source["catalog"],
                }
                for source in self.sources
            ],
        }

    def _autosave_workspace(self) -> None:
        try:
            path = self._store.save_default_workspace(self._workspace_payload())
            self.autosave_info.setText(f"Automatically remembered in {path}")
        except Exception as exc:
            self.autosave_info.setText(f"Workspace autosave failed: {exc}")
            self.storageMessage.emit(f"Catalog Workspace autosave failed: {exc}")

    def _after_workspace_mutation(self, message: str = "") -> None:
        self._refresh()
        if not self._restoring_workspace:
            self._autosave_workspace()
            self.workspaceChanged.emit()
            if message:
                self.storageMessage.emit(message)

    def _append_catalog(self, catalog: dict, enabled: bool = True, fallback: str = "Catalog") -> bool:
        if not isinstance(catalog, dict) or catalog.get("kind") != "mod_block_catalog":
            return False
        identity = self._catalog_identity(catalog)
        for source in self.sources:
            if source["identity"] == identity:
                source["enabled"] = True
                item = source.get("item")
                if item is not None:
                    item.setCheckState(Qt.Checked)
                return False

        self._source_serial += 1
        source_id = self._source_serial
        entry = {
            "id": source_id,
            "identity": identity,
            "label": self._catalog_label(catalog, fallback),
            "catalog": catalog,
            "enabled": bool(enabled),
        }
        item = QListWidgetItem(entry["label"])
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
        item.setData(Qt.UserRole, source_id)
        source_path = str(catalog.get("source") or "")
        stats = catalog.get("analysis_stats") or {}
        details = [source_path] if source_path else []
        details.append(f"{len(catalog.get('blocks', []) or []):,} block candidates")
        details.append(f"{len(catalog.get('block_entities', []) or []):,} block/tile entity candidates")
        role = str(catalog.get("provider_role") or "general").replace("_", " ")
        details.append(f"Role: {role}")
        if stats.get("packaged_model_assets"):
            details.append(f"{int(stats['packaged_model_assets']):,} packaged models")
        item.setToolTip("\n".join(details))
        entry["item"] = item
        self.sources.append(entry)
        self.source_list.addItem(item)

        if not self._restoring_workspace:
            try:
                self._store.save_catalog_snapshot(catalog)
            except Exception as exc:
                self.storageMessage.emit(f"Catalog snapshot could not be stored automatically: {exc}")
        return True

    def _ingest_document(self, data: dict, fallback: str) -> int:
        if not isinstance(data, dict):
            raise ValueError("Catalog data must be a JSON object.")
        kind = data.get("kind")
        added = 0
        if kind == "mod_block_catalog":
            added += int(self._append_catalog(data, True, fallback))
        elif kind == "modpack_block_analysis":
            for cat in data.get("block_catalogs", []) or []:
                added += int(self._append_catalog(cat, True, fallback))
        elif kind == "catalog_workspace":
            for source in data.get("sources", []) or []:
                if not isinstance(source, dict):
                    continue
                cat = source.get("catalog")
                if isinstance(cat, dict):
                    added += int(self._append_catalog(cat, bool(source.get("enabled", True)), fallback))
        else:
            raise ValueError("This JSON is not a WG block catalog, modpack analysis, or catalog workspace.")
        return added

    @Slot(object)
    def add_catalog_document(self, data: object) -> None:
        """Add analyzer output directly without requiring an export/import round trip."""
        if not isinstance(data, dict):
            QMessageBox.warning(self, "Catalog could not be added", "Analyzer output was not valid catalog data.")
            return
        try:
            fallback = str(data.get("mod_name") or data.get("pack_name") or "Analyzer catalog")
            added = self._ingest_document(data, fallback)
            self._after_workspace_mutation(
                f"Catalog Workspace updated: {added:,} new catalog source(s) added and autosaved."
                if added else
                "Catalog Workspace already contained this source; it was re-enabled and autosaved."
            )
        except Exception as exc:
            QMessageBox.critical(self, "Catalog could not be added", str(exc))

    def _load(self):
        self._store.ensure_layout()
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add block catalogs or analyses",
            str(self._store.catalogs_dir),
            "JSON (*.json);;All files (*)",
        )
        if not paths:
            return
        added = 0
        errors = []
        for path in paths:
            try:
                data = load_catalog(path)
                added += self._ingest_document(data, Path(path).stem)
            except Exception as exc:
                errors.append(f"{Path(path).name}: {exc}")

        self._after_workspace_mutation(
            f"Catalog Workspace updated: {added:,} new catalog source(s) loaded and autosaved."
        )
        if errors:
            QMessageBox.warning(self, "Some catalogs could not be loaded", "\n".join(errors))
        elif not added:
            QMessageBox.information(
                self,
                "Catalogs already loaded",
                "The selected catalog data was already present in this workspace; the existing source was re-enabled.",
            )

    def _source_toggled(self, item: QListWidgetItem):
        source_id = item.data(Qt.UserRole)
        for source in self.sources:
            if source["id"] == source_id:
                source["enabled"] = item.checkState() == Qt.Checked
                break
        if not self._restoring_workspace:
            self._after_workspace_mutation("Catalog Workspace selection changed and was autosaved.")

    def _remove_selected(self):
        selected_ids = {item.data(Qt.UserRole) for item in self.source_list.selectedItems()}
        if not selected_ids:
            return
        self.sources = [source for source in self.sources if source["id"] not in selected_ids]
        for row in range(self.source_list.count() - 1, -1, -1):
            if self.source_list.item(row).data(Qt.UserRole) in selected_ids:
                self.source_list.takeItem(row)
        self._after_workspace_mutation("Selected catalog source(s) removed and workspace autosaved.")

    def _clear(self):
        if not self.sources:
            return
        self.sources.clear()
        self.source_list.clear()
        self._after_workspace_mutation("Catalog Workspace cleared and autosaved.")

    def _restore_default_workspace(self):
        try:
            payload = self._store.load_default_workspace()
        except Exception as exc:
            self.autosave_info.setText(
                f"Could not restore {self._store.default_workspace_path}: {exc}"
            )
            return
        if not payload:
            self._refresh()
            return

        self._restoring_workspace = True
        self.source_list.blockSignals(True)
        try:
            self._ingest_document(payload, "Restored catalog")
        finally:
            self.source_list.blockSignals(False)
            self._restoring_workspace = False
        self._refresh()
        self.storageMessage.emit(
            f"Restored {len(self.sources):,} catalog source(s) from the default workspace."
        )

    def _open_storage_folder(self):
        self._store.ensure_layout()
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._store.root))):
            QMessageBox.information(
                self,
                "Storage folder",
                f"WG Map Backporter Studio stores persistent user data in:\n\n{self._store.root}",
            )

    def _active_rows(self) -> list[dict]:
        """Rows shown in the workspace inspector; mapping authority still uses blocks only."""
        rows: list[dict] = []
        for source in self.sources:
            if not source["enabled"]:
                continue
            catalog = source["catalog"]
            for block in catalog.get("blocks", []) or []:
                if isinstance(block, dict):
                    row = dict(block)
                    row["_workspace_kind"] = "Block"
                    rows.append(row)
            for block_entity in catalog.get("block_entities", []) or []:
                if isinstance(block_entity, dict):
                    row = dict(block_entity)
                    row["_workspace_kind"] = "Block entity"
                    rows.append(row)
        return rows

    def active_catalog_snapshot(self) -> dict:
        """Return a pure-data snapshot suitable for a conversion mapping profile.

        Backport-provider catalogs are carried separately so exact modern vanilla
        names can be preferred without granting automatic mapping authority to
        ordinary content mods or HBM-style architectural fallback catalogs.
        """
        labels: list[str] = []
        mod_ids: set[str] = set()
        registry_hints: set[str] = set()
        candidate_count = 0
        block_entity_count = 0
        backport_providers: list[dict] = []
        for source in self.sources:
            if not source["enabled"]:
                continue
            labels.append(str(source["label"]))
            catalog = source["catalog"]
            for mod_id in catalog.get("mod_ids", []) or []:
                if str(mod_id).strip():
                    mod_ids.add(str(mod_id).strip().lower())
            blocks = [block for block in (catalog.get("blocks", []) or []) if isinstance(block, dict)]
            block_entities = [row for row in (catalog.get("block_entities", []) or []) if isinstance(row, dict)]
            candidate_count += len(blocks)
            block_entity_count += len(block_entities)
            for block in blocks:
                hint = str(block.get("registry_hint") or "").strip().lower()
                if hint:
                    registry_hints.add(hint)
            if str(catalog.get("provider_role") or "").strip().lower() == "backport_provider":
                backport_providers.append({
                    "label": str(source["label"]),
                    "mod_ids": [str(x).strip().lower() for x in (catalog.get("mod_ids") or []) if str(x).strip()],
                    "blocks": blocks,
                })
        return {
            "enabled_catalogs": labels,
            "enabled_mod_ids": sorted(mod_ids),
            "registry_hints": sorted(registry_hints),
            "candidate_count": candidate_count,
            "block_entity_count": block_entity_count,
            "backport_providers": backport_providers,
        }

    def _refresh(self):
        active_rows = self._active_rows()
        q = self.search.text().strip().lower()
        rows = []
        for block in active_rows:
            hay = " ".join(str(block.get(key, "")) for key in (
                "_workspace_kind", "registry_hint", "class_name", "display_name", "source_mod", "confidence",
                "evidence", "candidate_kind", "localization_locale"
            )).lower()
            if not q or q in hay:
                rows.append(block)

        enabled = sum(1 for source in self.sources if source["enabled"])
        provider_count = sum(
            1 for source in self.sources
            if source["enabled"] and str(source["catalog"].get("provider_role") or "").lower() == "backport_provider"
        )
        active_be_count = sum(
            len(source["catalog"].get("block_entities", []) or [])
            for source in self.sources if source["enabled"]
        )
        active_block_count = sum(
            len(source["catalog"].get("blocks", []) or [])
            for source in self.sources if source["enabled"]
        )
        self.summary.setText(
            f"{len(self.sources):,} catalog source(s) loaded • {enabled:,} enabled • "
            f"{provider_count:,} backport provider(s) • {active_block_count:,} active blocks • "
            f"{active_be_count:,} block entities • {len(rows):,} asset row(s) shown"
            if self.sources else "No catalogs loaded yet."
        )

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row_index, block in enumerate(rows):
            identity = block.get("registry_hint", "") or block.get("class_name", "")
            asset_count = len(block.get("texture_paths", []) or []) + len(block.get("model_paths", []) or [])
            values = [
                block.get("_workspace_kind", "Block"),
                identity,
                block.get("display_name", ""),
                block.get("source_mod", ""),
                block.get("confidence", ""),
                block.get("evidence", ""),
                str(asset_count),
            ]
            for column, value in enumerate(values):
                self.table.setItem(row_index, column, QTableWidgetItem(str(value)))
        self.table.setSortingEnabled(True)

    def _save_workspace(self):
        if not self.sources:
            QMessageBox.information(self, "Nothing to save", "Add at least one catalog before saving a workspace.")
            return
        self._store.ensure_layout()
        suggested = self._store.workspaces_dir / "wg-catalog-workspace.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save catalog workspace copy", str(suggested), "JSON (*.json)"
        )
        if not path:
            return
        try:
            saved = self._store.save_workspace_copy(path, self._workspace_payload())
            self.storageMessage.emit(f"Saved catalog workspace copy to {saved}")
        except Exception as exc:
            QMessageBox.critical(self, "Workspace save failed", str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle(f"{APP_NAME} {__version__}"); self.resize(1260, 820); self.setMinimumSize(980, 740)
        root = QWidget(); root.setObjectName("rootWindow"); layout = QVBoxLayout(root); layout.setContentsMargins(18, 16, 18, 12)
        header = QHBoxLayout(); brand = QLabel(APP_NAME); brand.setObjectName("sectionTitle"); header.addWidget(brand); header.addStretch(); header.addWidget(_muted(f"v{__version__}")); layout.addLayout(header)

        self._store = WorkspaceStore(_application_storage_root())
        self._store.ensure_layout()

        tabs = QTabWidget(); tabs.setDocumentMode(True)
        catalog_tab = CatalogTab(self._store)
        backport_tab = BackportTab(catalog_provider=catalog_tab.active_catalog_snapshot)
        jar_tab = JarAnalyzerTab(self._store)
        modpack_tab = ModpackAnalyzerTab(self._store)

        catalog_tab.workspaceChanged.connect(backport_tab.catalog_workspace_changed)
        catalog_tab.storageMessage.connect(lambda message: self.statusBar().showMessage(message, 8000))

        def add_to_workspace(data):
            catalog_tab.add_catalog_document(data)
            tabs.setCurrentWidget(catalog_tab)

        jar_tab.addCatalogRequested.connect(add_to_workspace)
        modpack_tab.addAnalysisRequested.connect(add_to_workspace)

        tabs.addTab(DashboardTab(), "Overview")
        tabs.addTab(backport_tab, "Map Backporter")
        tabs.addTab(jar_tab, "Mod / JAR Analyzer")
        tabs.addTab(modpack_tab, "Modpack Analyzer")
        tabs.addTab(catalog_tab, "Catalog Workspace")
        layout.addWidget(tabs, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage(
            f"Ready — persistent catalogs/workspaces: {self._store.root}"
        )
