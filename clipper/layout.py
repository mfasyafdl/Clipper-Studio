"""Vertical layout for gaming streams: facecam big on top, gameplay below.

One STATIC crop per clip, like a human editor would do. The facecam is found
with YuNet (OpenCV's DNN face detector) on ~10 sampled frames, only inside
the corner where the streamer's camera lives, then the median position wins.
That kills the old problem of the camera jumping to faces inside the game.

Canvas 1080x1920 (numbers from the Windah preset of arkclennox/klip-gta6's
study of 319 Windah Shorts; ideas and numbers only, no code):
    0-210     black header with the hook title
    210-1000  facecam panel (1080x790)
    1000-1810 gameplay, 4:3 crop that cuts the cam corner away (1080x810)
    1810-1920 black
"""
from __future__ import annotations

import hashlib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import MODELS_DIR

YUNET_URL = ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")
YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"

CANVAS_W, CANVAS_H = 1080, 1920
HEADER_H = 210
CAM_Y, CAM_H = 210, 790
GAME_Y, GAME_H = 1000, 810

# fractions of the source frame
PRESETS = {
    # Windah: green-screen cut-out bottom-right, drifts during the stream
    "windah": {"cam_search": (0.55, 0.45, 0.45, 0.55), "cam_fallback": (0.78, 0.66, 0.22, 0.34),
               "game_x0": 0.052, "game_w": 0.75},
    # generic streamer with a facecam box bottom-right
    "facecam_br": {"cam_search": (0.6, 0.5, 0.4, 0.5), "cam_fallback": (0.75, 0.68, 0.25, 0.32),
                   "game_x0": 0.0, "game_w": 0.75},
    # facecam top-left
    "facecam_tl": {"cam_search": (0.0, 0.0, 0.4, 0.5), "cam_fallback": (0.0, 0.0, 0.25, 0.32),
                   "game_x0": 0.25, "game_w": 0.75},
}


@dataclass
class CamRect:
    x: int
    y: int
    w: int
    h: int
    found: bool  # False = preset fallback
    face_x: float = 0.5   # face centre inside the crop (0-1): the zoom anchor
    face_y: float = 0.42

    def filter(self) -> str:
        return f"crop={self.w}:{self.h}:{self.x}:{self.y}"


def ensure_yunet() -> Path:
    path = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == YUNET_SHA256:
        return path
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    print("[layout] mengunduh model wajah YuNet (230 KB)", flush=True)
    data = urllib.request.urlopen(YUNET_URL, timeout=60).read()
    if hashlib.sha256(data).hexdigest() != YUNET_SHA256:
        raise RuntimeError("model YuNet yang terunduh tidak cocok (checksum)")
    path.write_bytes(data)
    return path


def cam_rect_from_faces(faces: list[tuple[float, float, float, float]], src_w: int, src_h: int,
                        preset: dict) -> CamRect:
    """faces: (cx, cy, w, h) in source pixels from sampled frames. Median wins.

    Crop = 3.0x face width, aspect of the 1080x790 panel, face centre at 42%
    of the crop height (room for shoulders and hands, the reactions).
    """
    if len(faces) < 3:
        fx, fy, fw, fh = preset["cam_fallback"]
        return CamRect(int(fx * src_w), int(fy * src_h), int(fw * src_w), int(fh * src_h), False)
    med = lambda xs: sorted(xs)[len(xs) // 2]  # noqa: E731
    mcx, mcy, mfw = med([f[0] for f in faces]), med([f[1] for f in faces]), med([f[2] for f in faces])
    # drop stray detections (a face in the game, a poster) far from the webcam face
    faces = [f for f in faces if abs(f[0] - mcx) <= mfw and abs(f[1] - mcy) <= mfw and 0.6 <= f[2] / mfw <= 1.6]
    if len(faces) < 3:
        fx, fy, fw, fh = preset["cam_fallback"]
        return CamRect(int(fx * src_w), int(fy * src_h), int(fw * src_w), int(fh * src_h), False)
    cx, cy, fw = med([f[0] for f in faces]), med([f[1] for f in faces]), med([f[2] for f in faces])
    w = min(src_w, int(fw * 3.0))
    h = min(src_h, int(w / (CANVAS_W / CAM_H)))
    w = int(h * (CANVAS_W / CAM_H))
    x = int(min(max(0, cx - w / 2), src_w - w))
    y = int(min(max(0, cy - 0.42 * h), src_h - h))
    x, y, w, h = x - x % 2, y - y % 2, w - w % 2, h - h % 2
    # where the face really sits after clamping to the frame edge
    return CamRect(int(x), int(y), int(w), int(h), True, round(float(min(0.8, max(0.2, (cx - x) / w))), 3),
                   round(float(min(0.75, max(0.25, (cy - y) / h))), 3))


def detect_cam(source: Path, start: float, end: float, preset_name: str, samples: int = 16) -> CamRect:
    import cv2  # type: ignore

    preset = PRESETS.get(preset_name, PRESETS["windah"])
    cap = cv2.VideoCapture(str(source))
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    sx, sy, sw, sh = preset["cam_search"]
    rx, ry, rw, rh = int(sx * src_w), int(sy * src_h), int(sw * src_w), int(sh * src_h)
    faces: list[tuple[float, float, float, float]] = []
    try:
        detector = cv2.FaceDetectorYN.create(str(ensure_yunet()), "", (rw, rh), 0.7, 0.3, 50)
    except Exception as e:  # no model, no network: fall back to the preset rectangle
        print(f"[layout] YuNet tidak tersedia ({e}); pakai posisi preset", flush=True)
        cap.release()
        return cam_rect_from_faces([], src_w, src_h, preset)
    for i in range(samples):
        t = start + (end - start) * (i + 0.5) / samples
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        roi = frame[ry:ry + rh, rx:rx + rw]
        _, dets = detector.detect(roi)
        if dets is None or len(dets) == 0:
            continue
        x, y, w, h = max(dets, key=lambda d: d[2] * d[3])[:4]
        faces.append((rx + x + w / 2, ry + y + h / 2, float(w), float(h)))
    cap.release()
    rect = cam_rect_from_faces(faces, src_w, src_h, preset)
    print(f"[layout] facecam {'ketemu' if rect.found else 'tidak ketemu, pakai preset'} "
          f"({len(faces)}/{samples} frame): {rect.w}x{rect.h} di {rect.x},{rect.y}", flush=True)
    return rect


def game_crop(preset_name: str) -> str:
    p = PRESETS.get(preset_name, PRESETS["windah"])
    return f"crop=trunc(ih*4/3/2)*2:ih:trunc(iw*{p['game_x0']}/2)*2:0"
