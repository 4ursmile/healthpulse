"""Tighten long pauses, normalise loudness and time-stretch narration so the whole video lands on TARGET seconds.
Writes audio/sNN.wav per scene and audio/timing.json (durations used by the recorder and the assembler)."""
import json
import re
import subprocess
import sys
from pathlib import Path

from script import SCENES

FF = open("/tmp/hpvid/ff").read().strip()
RAW = Path("../audio/raw")
OUT = Path("../audio")
TARGET = 175.0           # 2:55
GAP = 0.35               # silence between scenes (visual crossfade covers it)
LEAD, TAIL = 0.5, 2.6    # silence before first line, end-slate hold after the last
KEEP = 0.46              # longest pause kept inside a line


def silences(f):
    err = subprocess.run([FF, "-hide_banner", "-i", str(f), "-af", "silencedetect=n=-40dB:d=0.3", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    st = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    en = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    dur = float(re.search(r"Duration: (\d+):(\d+):([\d.]+)", err).groups()[2]) + 60 * int(re.search(r"Duration: (\d+):(\d+)", err).groups()[1])
    return list(zip(st, en + [dur] * (len(st) - len(en)))), dur


def keep_segments(f):
    sil, dur = silences(f)
    segs, cur = [], 0.0
    for s, e in sil:
        if s <= 0.05:                      # leading silence: drop
            cur = e
            continue
        if e >= dur - 0.05:                # trailing silence: drop
            segs.append((cur, s + 0.08))
            cur = None
            break
        if e - s > KEEP:
            mid = (s + e) / 2
            segs.append((cur, mid - KEEP / 2 + 0.0))
            cur = mid + KEEP / 2
    if cur is not None:
        segs.append((cur, dur))
    return segs, dur


def main():
    raws = sorted(RAW.glob("0*.wav"))
    assert len(raws) == len(SCENES), (len(raws), len(SCENES))
    tight = []
    for i, (sc, f) in enumerate(zip(SCENES, raws)):
        segs, dur = keep_segments(f)
        sel = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in segs)
        tmp = Path(f"/tmp/hpvid/tight_{sc['id']}.wav")
        subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-i", str(f), "-af",
                        f"aselect='{sel}',asetpts=N/SR/TB", "-ar", "48000", str(tmp)], check=True)
        d = sum(b - a for a, b in segs)
        tight.append((sc, tmp, d))
        print(f"{sc['id']}: {dur:.2f} -> {d:.2f}")
    speech = sum(d for _, _, d in tight)
    budget = TARGET - LEAD - TAIL - GAP * (len(SCENES) - 1)
    tempo = max(1.0, speech / budget)
    print(f"speech {speech:.2f}s, budget {budget:.2f}s -> atempo {tempo:.4f}")
    timing = []
    for sc, tmp, d in tight:
        out = OUT / f"{sc['id']}.wav"
        subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-i", str(tmp), "-af",
                        f"atempo={tempo:.5f},loudnorm=I=-16:TP=-1.5:LRA=9,aresample=48000", "-ar", "48000", "-ac", "2", str(out)], check=True)
        p = subprocess.run([FF, "-hide_banner", "-i", str(out), "-f", "null", "-"], capture_output=True, text=True).stderr
        h, m, s = re.findall(r"time=(\d+):(\d+):([\d.]+)", p)[-1]
        timing.append(dict(id=sc["id"], dur=round(int(h) * 3600 + int(m) * 60 + float(s), 3)))
    json.dump(dict(tempo=tempo, gap=GAP, lead=LEAD, tail=TAIL, scenes=timing), open(OUT / "timing.json", "w"), indent=1)
    tot = LEAD + TAIL + GAP * (len(timing) - 1) + sum(t["dur"] for t in timing)
    print(json.dumps(timing), f"\nTOTAL {tot:.2f}s")


if __name__ == "__main__":
    main()
