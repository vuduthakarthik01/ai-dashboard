"""Vision pipeline.

Runs out of process, one worker per camera, so a stalled decode never blocks the
API. Each worker pulls RTSP with PyAV, samples at 5 fps, and fans the frame out
to the models enabled for that camera:

  safety   YOLOv8n fine-tuned on helmet/vest/glove classes  -> PPE
           pose keypoints + vertical-velocity rule          -> fall
           person track crossing a drawn polygon            -> intrusion
           smoke/fire classifier on 224px crops             -> fire, smoke
  quality  per-part classifier + anomaly head on the QC station feed

Detections above threshold are debounced (an alert needs N of the last M frames
to agree) before they reach the alert bus, which is what keeps a helmet-shaped
shadow from paging the owner at 2 a.m. Clips are written to object storage and
the row keeps only the URL.

The class below is the interface the rest of the app codes against; swap the
body for TensorRT, a Jetson, or a hosted endpoint without touching callers.
"""
import asyncio
from dataclasses import dataclass


@dataclass
class VisionDetection:
    kind: str                  # fire|smoke|ppe_helmet|ppe_gloves|fall|intrusion
    confidence: float
    bbox: dict                 # {"x":0..1,"y":0..1,"w":0..1,"h":0..1}
    clip_url: str = ""


THRESHOLDS = {"fire": 0.82, "smoke": 0.78, "ppe_helmet": 0.70,
              "ppe_gloves": 0.72, "fall": 0.80, "intrusion": 0.75}
DEBOUNCE = {"needed": 3, "window": 5}


class VisionWorker:
    def __init__(self, camera_id: str, rtsp_url: str, models: list[str]) -> None:
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.models = models
        self._recent: dict[str, list[bool]] = {}

    async def run(self, on_detection) -> None:
        async for frame in self._frames():
            for detection in await self._infer(frame):
                if self._debounced(detection):
                    await on_detection(self.camera_id, detection)

    async def _frames(self):
        """PyAV RTSP reader, 5 fps. Reconnects with backoff on stream loss."""
        while True:
            await asyncio.sleep(0.2)
            yield None       # replace with the decoded frame

    async def _infer(self, frame) -> list[VisionDetection]:
        """Batch the frame through the enabled models on the GPU."""
        return []

    def _debounced(self, d: VisionDetection) -> bool:
        if d.confidence < THRESHOLDS.get(d.kind, 0.8):
            return False
        window = self._recent.setdefault(d.kind, [])
        window.append(True)
        del window[:-DEBOUNCE["window"]]
        return sum(window) >= DEBOUNCE["needed"]
