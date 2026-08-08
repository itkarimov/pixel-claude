# -*- coding: utf-8 -*-
"""
Забрать модель whisper в формате ONNX для движка sherpa-onnx.

faster-whisper качает модель сам при первом запуске. sherpa-onnx так не умеет,
и это к лучшему: двести мегабайт по чужому каналу без спроса — плохая манера.
Поэтому качаем отдельной командой и только с явного согласия.

    python tools/get_onnx_model.py                 # покажет, что и откуда
    python tools/get_onnx_model.py --yes           # base, 197 МБ
    python tools/get_onnx_model.py --model tiny --yes

Кладётся в models/whisper-<модель>/. Папка models/ в гит не попадает.
"""
import argparse
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
            "asr-models/sherpa-onnx-whisper-{name}.tar.bz2")
SIZES = {"tiny": 110, "base": 197, "small": 609}      # МБ, померено запросом HEAD


def progress(done, total):
    if not total:
        return
    share = done / total
    bar = "█" * int(share * 30)
    sys.stdout.write(f"\r  [{bar:<30}] {share * 100:5.1f}%  "
                     f"{done / 1024 / 1024:.0f}/{total / 1024 / 1024:.0f} МБ")
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="base", choices=sorted(SIZES))
    ap.add_argument("--yes", action="store_true",
                    help="согласие на скачивание — без него ничего не качается")
    args = ap.parse_args()

    url = BASE_URL.format(name=args.model)
    target = os.path.join(ROOT, "models", f"whisper-{args.model}")

    print(f"модель:  whisper {args.model} (ONNX, для sherpa-onnx)")
    print(f"размер:  ~{SIZES[args.model]} МБ")
    print(f"откуда:  {url}")
    print(f"куда:    {target}")

    if os.path.isdir(target) and os.listdir(target):
        print("\nуже на месте — перекачивать не буду. Удали папку, если нужна заново.")
        return 0

    if not args.yes:
        print("\nЗапусти с --yes, если согласен качать.")
        return 0

    os.makedirs(target, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        archive = os.path.join(tmp, "model.tar.bz2")
        print("\nкачаю…")
        try:
            urllib.request.urlretrieve(
                url, archive,
                reporthook=lambda n, size, total: progress(n * size, total))
        except Exception as exc:
            print(f"\nне скачалось: {exc}")
            return 1
        print("\nраспаковываю…")
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(tmp)

        # архив разворачивается в свою папку — вытаскиваем файлы наверх, чтобы
        # core/stt.py искал их по простому шаблону, а не угадывал вложенность
        moved = 0
        for folder, _, files in os.walk(tmp):
            for name in files:
                if name.endswith((".onnx", ".txt")) and not name.endswith(".tar.bz2"):
                    shutil.move(os.path.join(folder, name),
                                os.path.join(target, name))
                    moved += 1

    print(f"готово: {moved} файлов в {target}")
    print('\nтеперь поставь в config.json:  "stt_backend": "onnx"')
    if args.model != "base":
        print(f'и                              "onnx_model_dir": "{target}"'
              .replace("\\", "/"))
    print("\nсравнить движки: python tools/bench_stt.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
