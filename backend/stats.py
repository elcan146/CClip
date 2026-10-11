#!/usr/bin/env python3
"""System stats collection for CClip."""

import os
import subprocess

import psutil

from config import GPU_LIB, STATS_Q, CANCEL


def _stats_worker():
    while not CANCEL.is_set():
        cpu = psutil.cpu_percent(interval=1.0)
        ram = psutil.virtual_memory().percent

        current_drive = os.path.abspath('.')
        try:
            dsk = psutil.disk_usage(current_drive).percent
        except:
            dsk = 0.0

        gp, gt, gm = 0.0, 0.0, 0.0
        if GPU_LIB:
            try:
                import GPUtil
                gpus = GPUtil.getGPUs()
                if gpus:
                    gp = gpus[0].load * 100
                    gt = gpus[0].temperature
                    gm = gpus[0].memoryUtil * 100
            except:
                pass
        else:
            try:
                cmd = [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits"
                ]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=2, creationflags=subprocess.CREATE_NO_WINDOW)
                if r.returncode == 0:
                    parts = r.stdout.strip().split(",")
                    gp = float(parts[0])
                    gt = float(parts[1])
                    gm = float(parts[2]) / max(float(parts[3]), 1) * 100
            except:
                pass

        STATS_Q.put((cpu, ram, gp, gt, gm, dsk))
