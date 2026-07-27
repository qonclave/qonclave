# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

import os

import cv2

from arduino.app_peripherals.camera import BaseCamera, CameraOpenError, CameraReadError


class FileCamera(BaseCamera):
  """Camera implementation that reads frames from a local video file instead
  of a live device, for testing/demoing detection without hardware. Follows
  the same BaseCamera contract (_open_camera/_close_camera/_read_frame) as
  V4LCamera/IPCamera, so it drops into VideoObjectDetection unchanged."""

  def __init__(self, path: str, loop: bool = True, **kwargs):
    super().__init__(**kwargs)
    self.path = path
    self.loop = loop
    self.name = f"file:{os.path.basename(path)}"
    self._cap = None

  def _open_camera(self) -> None:
    if not os.path.exists(self.path):
      raise CameraOpenError(f"Video file not found: {self.path}")

    self._cap = cv2.VideoCapture(self.path)
    if not self._cap.isOpened():
      self._cap = None
      raise CameraOpenError(f"Failed to open video file: {self.path}")

    self._set_status("connected", {"path": self.path})

  def _close_camera(self) -> None:
    if self._cap is not None:
      self._cap.release()
      self._cap = None
      self._set_status("disconnected", {"path": self.path})

  def _read_frame(self):
    if self._cap is None:
      return None

    try:
      ret, frame = self._cap.read()
      if not ret:
        if not self.loop:
          return None
        # End of file: rewind and keep the stream going, like a live source.
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ret, frame = self._cap.read()
        if not ret:
          raise CameraReadError(f"Failed to read from video file: {self.path}")
      return frame
    except Exception as e:
      self.logger.error(f"Failed to read frame from {self.name}: {e}")
      return None
