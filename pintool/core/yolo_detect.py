# -*- coding: utf-8 -*-
"""
YOLO 芯片检测核心模块
=====================
封装 ultralytics YOLO 推理。模型为项目内的 PCB 芯片检测模型（检测类别 O/T/F）。

对外提供：
  YoloRunner                    —— 延迟加载模型，run(img) 检测
  import_model(path)            —— 校验模型能否加载，返回 (ok, message, names)
"""
import os
import cv2
import numpy as np

_model = None
_model_path = None


class YoloRunner:
    """YOLO 模型封装：懒加载，支持单次推理。"""

    def __init__(self):
        self.model = None
        self.path = None
        self.names = {}

    def load(self, path):
        """加载 .pt 模型。成功返回 names 字典；失败抛异常。"""
        if not os.path.exists(path):
            raise FileNotFoundError("模型文件不存在: " + path)
        try:
            from ultralytics import YOLO
        except Exception as e:
            raise ImportError("未安装 ultralytics，无法加载 YOLO 模型：%r" % e)
        self.model = YOLO(path)
        self.path = path
        try:
            self.names = dict(self.model.names)
        except Exception:
            self.names = {}
        return self.names

    @property
    def loaded(self):
        return self.model is not None

    def run(self, img, conf=0.25, iou=0.45):
        """对 BGR 图推理。返回 boxes: [{'label','conf','box':[x0,y0,x1,y1]}]"""
        if self.model is None:
            raise RuntimeError("模型未加载")
        res = self.model.predict(img, conf=conf, iou=iou, verbose=False)
        out = []
        if not res:
            return out
        r = res[0]
        if r.boxes is None:
            return out
        xyxy = r.boxes.xyxy.cpu().numpy()
        cls = r.boxes.cls.cpu().numpy().astype(int)
        confs = r.boxes.conf.cpu().numpy()
        for (x0, y0, x1, y1), c, cf in zip(xyxy, cls, confs):
            label = self.names.get(int(c), str(int(c)))
            out.append(dict(label=label, conf=float(cf),
                            box=[float(x0), float(y0), float(x1), float(y1)]))
        return out


def import_model(path):
    """校验导入：返回 (ok:bool, message:str, names:dict)。"""
    try:
        y = YoloRunner()
        names = y.load(path)
        return True, "导入成功：%s\n类别：%s" % (os.path.basename(path),
                                                ", ".join("%s" % v for v in names.values())), names
    except Exception as e:
        return False, "导入失败：%r" % e, {}


SUPPORTED_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")


def list_images(folder):
    """列出文件夹内支持的图片（统一走 core.common，避免各模块实现不一致）。"""
    from .common import list_images as _li
    return _li(folder)
